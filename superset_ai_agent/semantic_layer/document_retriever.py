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

"""Uploaded-document chunk retrieval (RAG over ``raw/`` documents, plan §3.4).

Reuses the existing per-row mutable vector store (:class:`LanceVectorCache`) — the
same primitive backing ``sql_pairs`` and ``instructions`` — pointed at a dedicated
``document_chunks`` collection, instead of inventing new infrastructure.

Governance — **degrade closed** (mirrors ``schema_retriever`` / ``vector_cache``):
when no LanceDB-backed cache is configured/available, retrieval falls back to
keyword overlap over the candidate chunks, so document RAG is never a hard
dependency. Embedding is "index once at write time"; only the *query* is embedded
per recall.

Scope: this Phase-0 data-layer index covers the **persistent LanceDB path** and the
**keyword fallback**. A non-LanceDB *in-memory embedding* index for documents is a
deferred gap (memory mode degrades to keyword); see the plan's risk notes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.llm.embeddings import Embedder
from superset_ai_agent.semantic_layer.document_chunks import (
    DocumentChunk,
    DocumentChunkMatch,
    keyword_rank_chunks,
)
from superset_ai_agent.semantic_layer.pgvector import PgVectorCache
from superset_ai_agent.semantic_layer.store import scope_hash
from superset_ai_agent.semantic_layer.vector_cache import LanceVectorCache

logger = logging.getLogger(__name__)

#: Collection name for document-chunk vectors (namespaced separately from the
#: ``sql_pairs`` / ``instructions`` collections in the same LanceDB store).
DOCUMENT_CHUNK_COLLECTION = "document_chunks"


def document_scope_key(
    project_id: str | None, scope: ConversationScope | None = None
) -> str:
    """Vector-cache partition key for a document's chunks.

    Project-scoped when the document belongs to a project, else the scope hash.
    Indexing and retrieval must derive this identically or recall misses. ``scope``
    is only required for project-less documents (it is ignored when ``project_id``
    is present, so project-scoped callers may omit it).
    """

    if project_id:
        return f"doc:{project_id}"
    if scope is None:
        raise ValueError("document_scope_key requires a scope when project_id is None.")
    return f"doc:{scope_hash(scope)}"


class VectorCache(Protocol):
    """The subset of :class:`LanceVectorCache` this index depends on.

    Declared so tests can inject a deterministic fake (no LanceDB) and exercise the
    embedding-ranked recall path.
    """

    def is_available(self) -> bool: ...

    def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool: ...

    def remove(self, *, scope_key: str, row_id: str) -> bool: ...

    def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None: ...


class DocumentChunkIndex:
    """Index + retrieve document chunks, degrading closed to keyword overlap.

    The vector cache is an accelerator, never the source of truth — the chunk rows
    in the store remain authoritative. A stale or unavailable cache simply routes
    recall through the keyword fallback.
    """

    #: Reciprocal-rank-fusion constant (standard k=60) for the hybrid path.
    _RRF_K = 60

    def __init__(self, cache: VectorCache | None, *, hybrid: bool = False) -> None:
        self._cache = cache
        #: When on (B1), an embedding-served result is FUSED with the keyword
        #: ranking by RRF instead of replacing it — exact identifiers are
        #: lexical-match territory, colloquial phrasings are embedding
        #: territory, and the two fail complementarily.
        self._hybrid = hybrid

    @property
    def is_embedding_backed(self) -> bool:
        """Whether an available vector cache will serve embedding-ranked recall."""

        return self._cache is not None and self._cache.is_available()

    def index(self, chunks: list[DocumentChunk], *, scope_key: str) -> list[str]:
        """Embed-and-store each chunk; returns the ids actually persisted.

        Idempotent per chunk id (deterministic per ``document_id``/index), so a
        reindex replaces vectors in place. A no-op (returns ``[]``) when no cache is
        available — recall then falls back to keyword.
        """

        if self._cache is None or not self._cache.is_available():
            return []
        embedded: list[str] = []
        for chunk in chunks:
            if self._cache.upsert(
                scope_key=scope_key, row_id=chunk.id, text=chunk.text
            ):
                embedded.append(chunk.id)
        return embedded

    def remove(self, chunk_ids: list[str], *, scope_key: str) -> None:
        """Evict chunk vectors (delete cascade / reindex). Best-effort, never raises."""

        if self._cache is None:
            return
        for chunk_id in chunk_ids:
            self._cache.remove(scope_key=scope_key, row_id=chunk_id)

    def retrieve(
        self,
        query: str,
        chunks: list[DocumentChunk],
        *,
        scope_key: str,
        k: int,
    ) -> list[DocumentChunk]:
        """Return up to ``k`` of ``chunks`` most relevant to ``query``.

        Uses the vector cache when it can serve a result, otherwise keyword overlap.
        ``chunks`` is the candidate set (e.g. a document's persisted chunks); vector
        hits are mapped back to it, so ids missing from the candidate set are skipped
        and an empty mapping falls back to keyword.
        """

        if k <= 0:
            return []
        if self._cache is not None:
            pool = k * 2 if self._hybrid else k
            ids = self._cache.search(scope_key=scope_key, query=query, k=pool)
            if ids is not None:
                by_id = {chunk.id: chunk for chunk in chunks}
                ordered = [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]
                if ordered and self._hybrid:
                    return self._fuse(
                        ordered, keyword_rank_chunks(query, chunks, pool), k
                    )
                if ordered:
                    return ordered[:k]
        return keyword_rank_chunks(query, chunks, k)

    def _fuse(
        self,
        embedded: list[DocumentChunk],
        lexical: list[DocumentChunk],
        k: int,
    ) -> list[DocumentChunk]:
        """Reciprocal-rank fusion of the two chunk rankings (hybrid, B1)."""

        if not lexical:
            return embedded[:k]
        scores: dict[str, float] = {}
        by_id: dict[str, DocumentChunk] = {}
        for ranking in (embedded, lexical):
            for rank, chunk in enumerate(ranking):
                scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (
                    self._RRF_K + rank + 1
                )
                by_id.setdefault(chunk.id, chunk)
        ordered = sorted(scores.items(), key=lambda pair: -pair[1])
        return [by_id[chunk_id] for chunk_id, _ in ordered[:k]]


class DocumentContextStore(Protocol):
    """The subset of ``SemanticLayerStore`` the SQL-agent doc channel reads.

    Declared locally (like :class:`VectorCache`) so the channel depends on two
    read methods, not the full store protocol — tests inject a small fake.
    """

    def list_project_documents(
        self, project_id: str, *, owner_id: str = ...
    ) -> list[Any]: ...

    def list_project_chunks(
        self, project_id: str, *, owner_id: str = ...
    ) -> list[DocumentChunk]: ...


#: A second-stage reranking strategy (B3): given (question, candidate texts,
#: top_k), return candidate indices best-first, or ``None`` to keep the
#: first-stage order. The callable owns validating its output.
Reranker = Callable[[str, list[str], int], "list[int] | None"]


def retrieve_document_context(
    *,
    question: str,
    project_id: str | None,
    owner_id: str,
    store: DocumentContextStore | None,
    index: DocumentChunkIndex | None,
    k: int,
    max_chars: int,
    reranker: Reranker | None = None,
) -> dict[str, Any] | None:
    """Rank the project's uploaded-document chunks for a question (plan A1).

    The SQL agent's *advisory* grounding channel: eval v4 measured the raw BI
    document worth ~+12/30 over the bare semantic layer (`wren_bi_context` 22.0
    vs `wren_bi` ~9), so relevant document passages are retrieved into the
    prompt — budgeted, never dumped whole (doc-context oversupply measurably
    degrades SQL accuracy).

    Returns ``None`` (channel inert) when there is no project/store/index, the
    budget or ``k`` is non-positive, the project has no chunks, or on any error
    — degrade closed, the SQL path is never disrupted. Otherwise a JSON-safe
    dict: ``passages`` (document filename + text, budget-trimmed to
    ``max_chars`` total), ``document_ids`` (provenance for the explain UI),
    ``retriever`` (embedding vs keyword), and ``truncated``.
    """

    if project_id is None or store is None or index is None or k <= 0 or max_chars <= 0:
        return None
    try:
        chunks = store.list_project_chunks(project_id, owner_id=owner_id)
        if not chunks:
            return None
        # With a reranker, over-fetch so the second stage has a real pool.
        pool = k * 2 if reranker is not None else k
        ranked = index.retrieve(
            question,
            chunks,
            scope_key=document_scope_key(project_id),
            k=pool,
        )
        if not ranked:
            return None
        reranked = False
        if reranker is not None and len(ranked) > 1:
            order = reranker(question, [chunk.text for chunk in ranked], k)
            if order:
                ranked = [ranked[i] for i in order]
                reranked = True
        ranked = ranked[:k]
        filenames = {
            document.id: document.filename
            for document in store.list_project_documents(project_id, owner_id=owner_id)
        }
        passages, document_ids, truncated = _budget_trim(ranked, filenames, max_chars)
        if not passages:
            return None
        return {
            "passages": passages,
            "document_ids": document_ids,
            "retriever": "embedding" if index.is_embedding_backed else "keyword",
            "truncated": truncated,
            "reranked": reranked,
        }
    except Exception as ex:  # pylint: disable=broad-except - channel is best-effort
        logger.warning("Document context retrieval failed (non-fatal): %s", ex)
        return None


def _budget_trim(
    ranked: list[DocumentChunk],
    filenames: dict[str, Any],
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Fit ranked chunks into the char budget; flag any drop/cut loudly.

    A partial first passage beats none; everything past the budget is dropped
    with ``truncated=True`` (no silent cap).
    """

    passages: list[dict[str, Any]] = []
    document_ids: list[str] = []
    used = 0
    truncated = False
    for chunk in ranked:
        text = chunk.text
        remaining = max_chars - used
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            if passages:
                truncated = True
                break
            text = text[:remaining]
            truncated = True
        passages.append(
            {
                "document_id": chunk.document_id,
                "filename": filenames.get(chunk.document_id),
                "chunk_index": chunk.chunk_index,
                "text": text,
            }
        )
        used += len(text)
        if chunk.document_id not in document_ids:
            document_ids.append(chunk.document_id)
    return passages, document_ids, truncated


def find_exact_duplicate_matches(
    chunks: list[DocumentChunk],
) -> list[DocumentChunkMatch]:
    """Pair chunks with identical content (checksum equality) — the cheap dedup pass.

    Zero-dependency and always available; the first occurrence of a checksum is the
    canonical chunk, each later occurrence pairs back to it. Cosine near-duplicate
    detection (embedding-based) is a deferred follow-on (plan R5).
    """

    canonical: dict[str, DocumentChunk] = {}
    matches: list[DocumentChunkMatch] = []
    for chunk in chunks:
        prior = canonical.get(chunk.checksum)
        if prior is None:
            canonical[chunk.checksum] = chunk
            continue
        matches.append(
            DocumentChunkMatch(
                chunk_id=prior.id,
                other_chunk_id=chunk.id,
                document_id=prior.document_id,
                other_document_id=chunk.document_id,
                score=1.0,
                exact=True,
            )
        )
    return matches


def _document_lancedb_path(config: AgentConfig) -> str:
    """Dedicated LanceDB directory for document chunks.

    Deliberately distinct from the MDL/sql_pairs/instructions store
    (``wren_lancedb_path``) so document vectors live in their own database and can
    never affect — or be affected by — the existing MDL retrieval index.
    """

    if config.wren_document_lancedb_path:
        return config.wren_document_lancedb_path
    return str(Path(config.agent_storage_dir) / "lancedb_documents")


def create_document_index(
    config: AgentConfig, embedder: Embedder
) -> DocumentChunkIndex:
    """Build the document chunk index from config.

    Honors ``wren_document_vector_index`` (independent of the MDL ``wren_vector_index``
    knob): ``lancedb`` + an available embedder yields a persistent, embedding-ranked
    index in the documents-only LanceDB directory; otherwise the keyword-fallback
    index. Degrades closed when LanceDB or the embedder is unavailable.

    No vector backend is constructed when document indexing is disabled, so a
    deployment with the feature off never opens a LanceDB connection.
    """

    hybrid = config.wren_document_hybrid_retrieval
    if config.wren_document_indexing_enabled:
        if config.wren_document_vector_index == "lancedb":
            return DocumentChunkIndex(
                LanceVectorCache(
                    embedder, _document_lancedb_path(config), DOCUMENT_CHUNK_COLLECTION
                ),
                hybrid=hybrid,
            )
        if config.wren_document_vector_index == "postgres":
            return DocumentChunkIndex(
                PgVectorCache(
                    embedder,
                    config.effective_vector_database_url,
                    DOCUMENT_CHUNK_COLLECTION,
                ),
                hybrid=hybrid,
            )
    return DocumentChunkIndex(None)
