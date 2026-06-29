# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Retriever seam — rank MDL schema items for a question (Wren parity).

`KeywordRetriever` (default, zero-dependency) ranks by token overlap;
`EmbeddingRetriever` ranks by cosine similarity over an `Embedder`. Both operate
on `SchemaItem` chunks derived from a `CompiledManifest`, mirroring Wren's
`schema_items` collection. The embedding path degrades to keyword when the
embedder is unavailable (governance: degrade closed).
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.embeddings import Embedder, NullEmbedder
from superset_ai_agent.semantic_layer.mdl_compile import (
    compile_manifest,
    CompiledManifest,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFile, MdlFileStore

logger = logging.getLogger(__name__)


class SchemaItem(BaseModel):
    """One retrievable MDL chunk (a model, column, or relationship)."""

    kind: str
    name: str
    model: str | None = None
    text: str
    #: For ``kind == "relationship"``: the model names this relationship joins
    #: (its endpoints). Empty for models/columns. Carried structurally (not just in
    #: ``text``) so context selection can pull in a join partner whose own items
    #: ranked out — the cross-schema join-closure (see ``runtime`` / the plan).
    related_models: list[str] = []
    #: Query-time relevance (set by the ranker on the returned top-k, not at index
    #: time): normalized token overlap for keyword, cosine similarity for embedding.
    #: ``None`` when no question tokens matched or the cold ANN path omits it.
    score: float | None = None


class Retriever(Protocol):
    """Index-then-search retriever (Wren `schema_items` parity, wren_full.md R1).

    The manifest's chunks are indexed **once** per ``(scope_key, checksum)``;
    ``retrieve`` searches that index for a question. For the embedding binding this
    means item vectors are computed at index time and only the *question* is
    embedded per query — independent of schema width.
    """

    name: str

    def has_index(self, scope_key: str, checksum: str) -> bool:
        """Whether this exact manifest version is already indexed."""

    def index(self, items: list[SchemaItem], *, scope_key: str, checksum: str) -> None:
        """Build/refresh the index for a manifest version (idempotent per checksum)."""

    def retrieve(
        self, question: str, *, scope_key: str, checksum: str, k: int
    ) -> list[SchemaItem]:
        """Return the top-k indexed items most relevant to the question."""

    def effective_name(self, scope_key: str) -> str:
        """The retriever actually used for ``scope_key`` (``keyword`` on fallback)."""


def _semantic_terms(entity: dict[str, Any]) -> str:
    """Join an entity's human-facing semantics for the retrieval chunk (CR9).

    Pulls ``description`` and the ``properties`` keys Wren bakes into its
    ``db_schema`` DDL chunk (``displayName``/``alias``), plus any free-text
    ``synonyms``. These are the terms a colloquial question ("patty", "griddle")
    must match — without them the chunk is names+types only and enriched semantics
    never influence retrieval.
    """

    props = entity.get("properties") or {}
    parts: list[str] = []
    for value in (
        entity.get("description"),
        props.get("displayName") if isinstance(props, dict) else None,
        props.get("alias") if isinstance(props, dict) else None,
        props.get("synonyms") if isinstance(props, dict) else None,
        props.get("description") if isinstance(props, dict) else None,
    ):
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item).strip() for item in value if str(item).strip())
    return " ".join(parts)


def manifest_to_schema_items(manifest: CompiledManifest) -> list[SchemaItem]:
    """Chunk a compiled manifest into retrievable schema items.

    Each chunk's ``text`` carries the entity's **semantics** (descriptions, display
    names, aliases/synonyms) and a relationship's join **condition**, mirroring Wren's
    annotated ``db_schema`` DDL chunk — so enriched business terms become retrievable
    (CR9), not just physical names+types.
    """

    items: list[SchemaItem] = []
    for model in manifest.models:
        model_name = str(model.get("name") or "")
        if not model_name:
            continue
        columns = model.get("columns") or []
        col_names = ", ".join(
            str(col.get("name")) for col in columns if col.get("name")
        )
        model_text = f"model {model_name} columns: {col_names}"
        model_terms = _semantic_terms(model)
        if model_terms:
            model_text = f"{model_text} — {model_terms}"
        items.append(
            SchemaItem(
                kind="model",
                name=model_name,
                model=model_name,
                text=model_text,
            )
        )
        for col in columns:
            col_name = str(col.get("name") or "")
            if not col_name:
                continue
            col_text = f"{model_name}.{col_name} {col.get('type') or ''}".strip()
            col_terms = _semantic_terms(col)
            if col_terms:
                col_text = f"{col_text} — {col_terms}"
            items.append(
                SchemaItem(
                    kind="column",
                    name=col_name,
                    model=model_name,
                    text=col_text,
                )
            )
    for rel in manifest.relationships:
        rel_name = str(rel.get("name") or "")
        if not rel_name:
            continue
        models = ", ".join(str(m) for m in (rel.get("models") or []))
        rel_text = (
            f"relationship {rel_name} joins {models} ({rel.get('joinType') or ''})"
        )
        condition = rel.get("condition")
        if isinstance(condition, str) and condition.strip():
            rel_text = f"{rel_text} on {condition.strip()}"
        items.append(
            SchemaItem(
                kind="relationship",
                name=rel_name,
                text=rel_text,
                related_models=[
                    str(m) for m in (rel.get("models") or []) if str(m or "")
                ],
            )
        )
    return items


def _tokens(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {token for token in normalized.split() if token}


def _keyword_rank(question: str, items: list[SchemaItem], k: int) -> list[SchemaItem]:
    q_tokens = _tokens(question)
    if not q_tokens:
        return items[:k]
    overlap = {
        id(item): len(q_tokens & _tokens(f"{item.name} {item.text}")) for item in items
    }
    scored = sorted(items, key=lambda item: overlap[id(item)], reverse=True)
    # Normalize overlap to 0-1 by the query token count so the surfaced score is
    # comparable across questions (A3).
    denom = len(q_tokens)
    return [
        item.model_copy(update={"score": round(overlap[id(item)] / denom, 4)})
        for item in scored[:k]
    ]


def _embedding_rank(
    query_vector: list[float],
    items: list[SchemaItem],
    vectors: list[list[float]],
    k: int,
) -> list[SchemaItem]:
    scored = sorted(
        zip(items, vectors, strict=True),
        key=lambda pair: _cosine(query_vector, pair[1]),
        reverse=True,
    )
    return [
        item.model_copy(update={"score": round(_cosine(query_vector, vector), 4)})
        for item, vector in scored[:k]
    ]


@dataclass
class _IndexEntry:
    """One indexed manifest version for a scope (latest checksum wins)."""

    checksum: str
    items: list[SchemaItem]
    #: Item vectors, parallel to ``items``; ``None`` when keyword-ranked or the
    #: embedder was unavailable at index time (so retrieval falls back to keyword).
    vectors: list[list[float]] | None = None


def _index_checksum(checksum: str, embedder: Embedder) -> str:
    """Fold the embedder identity into the index key (R3/R-RET4).

    A model or dimension change shifts the signature, so the same MDL content
    re-indexes rather than mixing vectors from different models.
    """

    return f"{checksum}#{embedder.signature()}"


class _LruIndex:
    """Per-scope index entries bounded to the N most-recently-used scopes (C4).

    Stops the in-process retriever index from growing unbounded across many
    projects/owners in a long-lived worker. ``max_scopes <= 0`` is unlimited.
    """

    def __init__(self, max_scopes: int = 0) -> None:
        self._data: OrderedDict[str, _IndexEntry] = OrderedDict()
        self.max_scopes = max_scopes

    def get(self, scope_key: str) -> _IndexEntry | None:
        entry = self._data.get(scope_key)
        if entry is not None:
            try:
                self._data.move_to_end(scope_key)  # mark recently used
            except KeyError:
                # Concurrently evicted between get and move_to_end (the index is
                # shared across request threads under C4); benign, return what we
                # read. GIL keeps each op atomic; this guards the two-op gap.
                pass
        return entry

    def set(self, scope_key: str, entry: _IndexEntry) -> None:
        self._data[scope_key] = entry
        self._data.move_to_end(scope_key)
        while self.max_scopes > 0 and len(self._data) > self.max_scopes:
            self._data.popitem(last=False)  # evict least-recently-used

    def __len__(self) -> int:
        return len(self._data)


class KeywordRetriever:
    """Default retriever: rank by question/item token overlap, no embeddings."""

    name = "keyword"

    def __init__(self, max_scopes: int = 0) -> None:
        self._index = _LruIndex(max_scopes)

    def has_index(self, scope_key: str, checksum: str) -> bool:
        entry = self._index.get(scope_key)
        return entry is not None and entry.checksum == checksum

    def index(self, items: list[SchemaItem], *, scope_key: str, checksum: str) -> None:
        self._index.set(scope_key, _IndexEntry(checksum=checksum, items=items))

    def retrieve(
        self, question: str, *, scope_key: str, checksum: str, k: int
    ) -> list[SchemaItem]:
        entry = self._index.get(scope_key)
        if entry is None or entry.checksum != checksum:
            return []
        return _keyword_rank(question, entry.items, k)

    def effective_name(self, scope_key: str) -> str:
        return "keyword"


class EmbeddingRetriever:
    """Embedding retriever: item vectors built once per checksum (wren_full.md R1).

    Closes the per-request re-embedding gap (G1): ``index`` embeds the items once
    and ``retrieve`` embeds only the question. Falls back to keyword for a scope
    when the embedder is unavailable or embedding raises — and reports that via
    ``effective_name`` so the UI badge cannot misreport (G8a).
    """

    name = "embedding"

    def __init__(self, embedder: Embedder, max_scopes: int = 0) -> None:
        self.embedder = embedder
        self._index = _LruIndex(max_scopes)

    def effective_checksum(self, checksum: str) -> str:
        return _index_checksum(checksum, self.embedder)

    def has_index(self, scope_key: str, checksum: str) -> bool:
        entry = self._index.get(scope_key)
        return entry is not None and entry.checksum == self.effective_checksum(checksum)

    def index(self, items: list[SchemaItem], *, scope_key: str, checksum: str) -> None:
        eff = self.effective_checksum(checksum)
        existing = self._index.get(scope_key)
        if existing is not None and existing.checksum == eff:
            return  # this manifest version + embedder is already indexed
        vectors: list[list[float]] | None = None
        if items and self.embedder.is_available():
            try:
                vectors = self.embedder.embed([item.text for item in items])
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Embedding index build failed; keyword fallback: %s", ex)
                vectors = None
        self._index.set(
            scope_key, _IndexEntry(checksum=eff, items=items, vectors=vectors)
        )

    def retrieve(
        self, question: str, *, scope_key: str, checksum: str, k: int
    ) -> list[SchemaItem]:
        entry = self._index.get(scope_key)
        if entry is None or entry.checksum != self.effective_checksum(checksum):
            return []
        if entry.vectors is None:
            return _keyword_rank(question, entry.items, k)
        try:
            query_vector = self.embedder.embed([question])[0]
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Question embedding failed; keyword fallback: %s", ex)
            return _keyword_rank(question, entry.items, k)
        return _embedding_rank(query_vector, entry.items, entry.vectors, k)

    def effective_name(self, scope_key: str) -> str:
        entry = self._index.get(scope_key)
        return (
            "embedding"
            if entry is not None and entry.vectors is not None
            else ("keyword")
        )

    def prime(
        self,
        scope_key: str,
        checksum: str,
        items: list[SchemaItem],
        vectors: list[list[float]] | None,
    ) -> None:
        """Inject a prebuilt index entry (used to rehydrate from a durable store)."""

        self._index.set(
            scope_key, _IndexEntry(checksum=checksum, items=items, vectors=vectors)
        )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _table_name(scope_key: str, checksum: str) -> str:
    # Hash both parts to a safe identifier — LanceDB table names allow only
    # alphanumerics/underscore/hyphen/period, and the effective checksum carries
    # the embedder signature (e.g. "...#openai:model:1536") with ':' and '#'.
    scope = hashlib.sha1(scope_key.encode("utf-8")).hexdigest()[:16]  # noqa: S324
    cksum = hashlib.sha1(checksum.encode("utf-8")).hexdigest()[:16]  # noqa: S324
    return f"mdl_{scope}_{cksum}"


class LanceDbRetriever:
    """Persistent embedding retriever backed by LanceDB (wren_full.md R2).

    Survives restarts/workers by persisting item vectors per ``(scope_key,
    checksum)`` to a LanceDB table. All ranking reuses the in-process
    `EmbeddingRetriever` (so logic is shared + tested); LanceDB is purely
    persistence + cold-start rehydration. **Every** LanceDB call is wrapped — on
    an import error, a connect error, or any API mismatch it degrades to the
    in-process embedding index, so it can never crash the request path.
    """

    name = "embedding"

    def __init__(self, embedder: Embedder, path: str, max_scopes: int = 0) -> None:
        self.embedder = embedder
        self.path = path
        self._mem = EmbeddingRetriever(embedder, max_scopes)
        self._db = self._connect()

    def _connect(self) -> Any | None:
        try:
            import lancedb  # type: ignore  # lazy, optional dependency

            return lancedb.connect(self.path)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("LanceDB unavailable (%s); using in-process index.", ex)
            return None

    def is_persistent(self) -> bool:
        """Whether the durable LanceDB backend connected (vs. in-process fallback)."""

        return self._db is not None

    def has_index(self, scope_key: str, checksum: str) -> bool:
        if self._mem.has_index(scope_key, checksum):
            return True
        return self._table(scope_key, checksum) is not None

    def index(self, items: list[SchemaItem], *, scope_key: str, checksum: str) -> None:
        if self.has_index(scope_key, checksum):
            return
        self._mem.index(items, scope_key=scope_key, checksum=checksum)
        if self._db is None:
            return
        # Only persist when embedding actually produced vectors; LanceDB is
        # vector-only, so a keyword fallback stays purely in-process.
        if self._mem.effective_name(scope_key) != "embedding":
            return
        entry = self._mem._index.get(scope_key)  # pylint: disable=protected-access
        if entry is None or entry.vectors is None:
            return
        try:
            rows = [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "model": item.model or "",
                    "text": item.text,
                    "vector": vector,
                }
                for item, vector in zip(entry.items, entry.vectors, strict=True)
            ]
            self._db.create_table(
                _table_name(scope_key, self._mem.effective_checksum(checksum)),
                data=rows,
                mode="overwrite",
            )
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("LanceDB index persist failed (%s); in-process only.", ex)

    def retrieve(
        self, question: str, *, scope_key: str, checksum: str, k: int
    ) -> list[SchemaItem]:
        # Warm (same process as index()) → in-process rank over cached vectors.
        if self._mem.has_index(scope_key, checksum):
            return self._mem.retrieve(
                question, scope_key=scope_key, checksum=checksum, k=k
            )
        # Cold (restart/new worker) → native ANN search so we never load the
        # whole corpus into memory (C2 / closes R-RET-B). Degrades to rehydrate +
        # in-process rank when the embedder is unavailable or search raises.
        table = self._table(scope_key, checksum)
        if table is not None and self.embedder.is_available():
            try:
                query_vector = self.embedder.embed([question])[0]
                # Use cosine to match the in-process path (lancedb defaults to L2,
                # which would rank differently for non-normalized vectors).
                rows = (
                    table.search(query_vector)
                    .metric("cosine")
                    .limit(k)
                    .to_arrow()
                    .to_pylist()
                )
                return [
                    SchemaItem(
                        kind=row["kind"],
                        name=row["name"],
                        model=row["model"] or None,
                        text=row["text"],
                        # cosine distance -> similarity, matching the warm path's
                        # score scale (A3); absent if the column is not returned.
                        score=(
                            round(1 - row["_distance"], 4)
                            if row.get("_distance") is not None
                            else None
                        ),
                    )
                    for row in rows
                ]
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("LanceDB ANN search failed (%s); rehydrating.", ex)
        self._rehydrate(scope_key, checksum)
        return self._mem.retrieve(question, scope_key=scope_key, checksum=checksum, k=k)

    def effective_name(self, scope_key: str) -> str:
        entry = self._mem._index.get(scope_key)  # pylint: disable=protected-access
        if entry is not None:
            return self._mem.effective_name(scope_key)
        # Cold ANN path doesn't populate the in-process index; a connected LanceDB
        # means vectors were persisted, so the effective mode is embedding.
        return "embedding" if self._db is not None else "keyword"

    def _table(self, scope_key: str, checksum: str) -> Any | None:
        if self._db is None:
            return None
        # open_table raises on a missing table; that is the expected "no index
        # yet" signal, so a failure here simply means "not indexed".
        try:
            name = _table_name(scope_key, self._mem.effective_checksum(checksum))
            return self._db.open_table(name)
        except Exception:  # pylint: disable=broad-except
            return None

    def _rehydrate(self, scope_key: str, checksum: str) -> None:
        table = self._table(scope_key, checksum)
        if table is None:
            return
        try:
            # to_arrow() needs only pyarrow (a lancedb dep); to_pandas() would
            # additionally require the separate `pylance` package.
            rows = table.to_arrow().to_pylist()
            items = [
                SchemaItem(
                    kind=row["kind"],
                    name=row["name"],
                    model=row["model"] or None,
                    text=row["text"],
                )
                for row in rows
            ]
            vectors = [list(row["vector"]) for row in rows]
            self._mem.prime(
                scope_key, self._mem.effective_checksum(checksum), items, vectors
            )
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("LanceDB rehydrate failed (%s); in-process only.", ex)


def create_retriever(
    config: AgentConfig, embedder: Embedder | None = None
) -> Retriever:
    """Build the configured retriever; degrade to keyword when embedding is off."""

    max_scopes = config.wren_retriever_cache_scopes
    if config.wren_retriever == "embedding":
        active = embedder or NullEmbedder()
        if active.is_available():
            if config.wren_vector_index == "lancedb":
                return LanceDbRetriever(active, _lancedb_path(config), max_scopes)
            return EmbeddingRetriever(active, max_scopes)
    return KeywordRetriever(max_scopes)


def _lancedb_path(config: AgentConfig) -> str:
    if config.wren_lancedb_path:
        return config.wren_lancedb_path
    return str(Path(config.agent_storage_dir) / "lancedb")


def effective_vector_index(config: AgentConfig, retriever: Retriever) -> str:
    """Report the index actually in use (C1 loud fallback).

    ``memory_fallback`` means LanceDB was requested but did not connect (e.g. the
    wheel is absent), so the index silently runs in-process — surfaced so an
    operator can see the misconfig instead of it degrading invisibly.
    """

    if config.wren_vector_index != "lancedb":
        return "memory"
    if isinstance(retriever, LanceDbRetriever) and retriever.is_persistent():
        return "lancedb"
    return "memory_fallback"


def _content_checksum(files: list[MdlFile]) -> str:
    """Stable checksum of a project's active MDL content (drives reindex)."""

    digest = hashlib.sha256()
    for file in files:
        digest.update(file.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def ensure_project_indexed(
    *,
    retriever: Retriever,
    project_id: str | None,
    owner_id: str,
    mdl_file_store: MdlFileStore | None,
) -> tuple[str, str] | None:
    """Build/refresh the retriever index for a project's active MDL (idempotent).

    Returns ``(scope_key, checksum)`` when an index exists or was built, else
    ``None`` (no project, no store, or no active MDL). Shared by
    `retrieve_mdl_context` (lazy, per query) and the activation route (eager
    re-index on MDL deploy, E6) so both use one indexing path. Raises on a
    compile/index error; callers wrap as appropriate (retrieval is best-effort,
    activation re-index is best-effort).
    """

    if project_id is None or mdl_file_store is None:
        return None
    active_files = [
        file
        for file in mdl_file_store.list(project_id, owner_id=owner_id)
        if file.status == "active" and file.deleted_at is None
    ]
    if not active_files:
        return None
    checksum = _content_checksum(active_files)
    scope_key = f"{owner_id}:{project_id}"
    # Recompile + (re)index only when the MDL content changed (G2/G3).
    if not retriever.has_index(scope_key, checksum):
        items = manifest_to_schema_items(compile_manifest(active_files))
        if not items:
            return None
        retriever.index(items, scope_key=scope_key, checksum=checksum)
    return scope_key, checksum


def reindex_project_mdl(
    *,
    retriever: Retriever,
    project_id: str | None,
    owner_id: str,
    mdl_file_store: MdlFileStore | None,
) -> bool:
    """Best-effort eager re-index of a project's active MDL (E6 deploy→reindex).

    Returns whether an index is now present. Never raises — activation must not
    fail because retrieval indexing hiccupped.
    """

    try:
        return (
            ensure_project_indexed(
                retriever=retriever,
                project_id=project_id,
                owner_id=owner_id,
                mdl_file_store=mdl_file_store,
            )
            is not None
        )
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Eager MDL re-index failed (non-fatal): %s", ex)
        return False


def retrieve_mdl_context(
    *,
    config: AgentConfig,
    retriever: Retriever,
    question: str,
    project_id: str | None,
    owner_id: str,
    mdl_file_store: MdlFileStore | None,
) -> list[dict[str, Any]]:
    """Rank a project's active MDL into prompt context-item dicts for a question.

    Distinct from [`retrieval.retrieve_schema_context`](retrieval.py), which ranks
    physical Superset *datasets*; this ranks compiled *MDL* schema items (models/
    columns/relationships) via the configured `Retriever` seam.

    Compiles the project's active MDL files, chunks them into ``SchemaItem``s
    (Wren ``schema_items`` parity), ranks them with the configured retriever, and
    returns the top-k as ``context_items`` dicts the SQL prompt already consumes.

    Degrades closed: returns ``[]`` when there is no project, no MDL file store,
    no active MDL, or on any error — so the existing keyword context path is
    never disrupted when the retriever has nothing to add.
    """

    if project_id is None or mdl_file_store is None:
        return []
    try:
        # Ensure the index is current for this MDL version (lazy build on a warm
        # checksum embeds just the question, G1); shared with the eager activation
        # re-index (E6) so both paths index identically.
        indexed = ensure_project_indexed(
            retriever=retriever,
            project_id=project_id,
            owner_id=owner_id,
            mdl_file_store=mdl_file_store,
        )
        if indexed is None:
            return []
        scope_key, checksum = indexed
        top = retriever.retrieve(
            question,
            scope_key=scope_key,
            checksum=checksum,
            k=config.wren_context_limit,
        )
        if not top:
            return []
        # Stamp the *effective* retriever (keyword on a silent fallback) so the UI
        # badge reflects what actually ran (G8a).
        mode = retriever.effective_name(scope_key)
        return [_item_to_dict(item, source="retriever", retriever=mode) for item in top]
    except Exception as ex:  # pylint: disable=broad-except - retrieval is best-effort
        logger.warning("Schema retrieval failed; using keyword context only: %s", ex)
        return []


def _item_to_dict(
    item: SchemaItem, *, source: str, retriever: str | None = None
) -> dict[str, Any]:
    """Serialize a ``SchemaItem`` to the context-item dict shape the prompt + the
    selection/closure steps consume. ``related_models`` is carried so closure can
    resolve a relationship's join endpoints structurally."""

    payload: dict[str, Any] = {
        "source": source,
        "kind": item.kind,
        "name": item.name,
        "model": item.model,
        "text": item.text,
        "score": item.score,
        "related_models": item.related_models,
    }
    if retriever is not None:
        payload["retriever"] = retriever
    return payload


def project_schema_items(
    *,
    project_id: str | None,
    owner_id: str,
    mdl_file_store: MdlFileStore | None,
) -> list[dict[str, Any]]:
    """The project's **entire** active MDL chunked into context-item dicts.

    Distinct from :func:`retrieve_mdl_context` (which returns the question-ranked
    top-k): this is the unranked, complete set used as the **join-closure source**
    — a join partner that ranked out of the top-k must still be injectable, so
    closure needs every model/column/relationship, not just the retrieved subset.
    Degrades closed to ``[]`` (no project/store/active MDL, or on any error) so the
    SQL path is never disrupted. Tagged ``source="manifest"``.
    """

    if project_id is None or mdl_file_store is None:
        return []
    try:
        active_files = [
            file
            for file in mdl_file_store.list(project_id, owner_id=owner_id)
            if file.status == "active" and file.deleted_at is None
        ]
        if not active_files:
            return []
        items = manifest_to_schema_items(compile_manifest(active_files))
        return [_item_to_dict(item, source="manifest") for item in items]
    except Exception as ex:  # pylint: disable=broad-except - best-effort closure source
        logger.warning("Manifest closure-source build failed (non-fatal): %s", ex)
        return []
