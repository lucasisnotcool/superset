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

"""Memory seam — the confirmed NL->SQL learning loop (Wren `query_history`).

Confirmed (successfully executed) question/SQL pairs are stored per owner+scope
and recalled as few-shot examples, so the agent improves over time. Examples are
**context, not permission sources**, and are isolated by ``owner_id`` +
``scope_hash`` (governance).
"""

from __future__ import annotations

import hashlib
import logging
import math
import uuid
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.embeddings import Embedder
from superset_ai_agent.persistence.models import AiAgentNlSqlExample
from superset_ai_agent.semantic_layer.vector_cache import LanceVectorCache

logger = logging.getLogger(__name__)


class NlSqlPair(BaseModel):
    """One confirmed natural-language to SQL example.

    ``referenced_tables`` are the physical tables the pair touches, as
    lowercased ``"schema.table"`` strings (bare ``"table"`` when the source SQL
    left the schema implicit); ``referenced_schemas`` is the set of schemas. These
    drive the access-aware recall filter (F2) — a pair is surfaced only when the
    requesting user can reach *every* referenced table.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    question: str
    semantic_sql: str
    native_sql: str
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_schemas: list[str] = Field(default_factory=list)
    result_meta: dict[str, Any] = Field(default_factory=dict)


def qualify_table_refs(
    extracted: list[tuple[str | None, str]],
) -> tuple[list[str], list[str]]:
    """Normalize ``(schema, table)`` pairs into the stored recall-filter shape.

    Returns ``(referenced_tables, referenced_schemas)`` — tables as lowercased
    ``"schema.table"`` (bare ``"table"`` when schema is ``None``), schemas as the
    sorted lowercased set (``None`` schemas excluded). Use at store time to fill
    ``NlSqlPair.referenced_tables`` / ``referenced_schemas`` from
    ``extract_qualified_tables(native_sql)``.
    """

    tables: set[str] = set()
    schemas: set[str] = set()
    for schema, table in extracted:
        if not table:
            continue
        name = table.lower()
        if schema:
            schema_l = schema.lower()
            tables.add(f"{schema_l}.{name}")
            schemas.add(schema_l)
        else:
            tables.add(name)
    return sorted(tables), sorted(schemas)


def refs_from_sql(
    native_sql: str, *, dialect: str | None = None
) -> tuple[list[str], list[str]]:
    """Store-time helper: ``native_sql`` -> ``(referenced_tables, referenced_schemas)``.

    Wraps the schema-aware extractor; on a parse failure both lists are empty, which
    makes the recall access filter (F2) **fail closed** for that pair (it is never
    surfaced). Local import avoids an import cycle with the engine package.
    """

    from superset_ai_agent.semantic_layer.engine.base import extract_qualified_tables

    return qualify_table_refs(extract_qualified_tables(native_sql, dialect=dialect))


@dataclass(frozen=True)
class RecallAccess:
    """Per-request access context for the recall filter (F2).

    All sets are lowercased. ``accessible_tables`` / ``onboarded_tables`` hold
    qualified ``"schema.table"`` keys. Built by the draft node from the
    access-proven dataset context + the active project manifest (see graph wiring).
    When ``None`` is passed to recall, the access filter is skipped (test / no-context
    paths only — production call sites always supply it).
    """

    accessible_tables: frozenset[str] = dataclass_field(default_factory=frozenset)
    project_schemas: frozenset[str] = dataclass_field(default_factory=frozenset)
    onboarded_tables: frozenset[str] = dataclass_field(default_factory=frozenset)

    @property
    def accessible_names(self) -> frozenset[str]:
        """Bare table names of the accessible set (for unqualified-ref leniency)."""

        return frozenset(key.rsplit(".", 1)[-1] for key in self.accessible_tables)

    @property
    def onboarded_names(self) -> frozenset[str]:
        return frozenset(key.rsplit(".", 1)[-1] for key in self.onboarded_tables)


def build_recall_access(datasets: Any) -> RecallAccess:
    """Construct the recall access context (F2) from access-proven datasets.

    ``datasets`` is the request scope's ``AgentContext.datasets`` — the tables the
    user is proven to reach (a schema they cannot access contributes nothing, see
    ``SemanticAccessService``). Per DP-3 (v1), this request scope is both the
    ``accessible`` and ``onboarded`` set; a pair referencing anything outside it is
    dropped by Stage A. Each dataset exposes ``schema_name`` / ``table_name``.
    """

    tables: set[str] = set()
    schemas: set[str] = set()
    for dataset in datasets or []:
        table = (getattr(dataset, "table_name", None) or "").lower()
        if not table:
            continue
        schema = getattr(dataset, "schema_name", None)
        if schema:
            schema_l = schema.lower()
            tables.add(f"{schema_l}.{table}")
            schemas.add(schema_l)
        else:
            tables.add(table)
    frozen = frozenset(tables)
    return RecallAccess(
        accessible_tables=frozen,
        project_schemas=frozenset(schemas),
        onboarded_tables=frozen,
    )


def load_recall_access(
    superset_client: Any,
    *,
    database_id: int,
    catalog_name: str | None,
    schema_names: list[str],
    limit: int,
) -> RecallAccess:
    """Build the F2 recall access set from the user's reachable tables (R1 fix).

    The recall access set must be the tables the requesting user can *access*
    across the project's full schema set — NOT the relevance-ranked, single-schema
    grounding subset (``context.datasets``) the recall filter previously reused.
    Lists datasets per schema under the requester's Superset auth (so the result is
    per-user access-filtered: a schema/table they cannot reach never appears, and
    Stage A stays fail-closed for it), then unions into one accessible set spanning
    every schema. ``superset_client`` is duck-typed to avoid an import cycle; it
    must expose ``list_datasets(database_id, catalog_name, schema_name, limit)``.

    On any listing error, returns an empty ``RecallAccess`` (degrade closed — the
    caller falls back to the prior single-schema behaviour, no worse than today).
    """

    datasets: list[Any] = []
    for schema in schema_names:
        if not schema:
            continue
        try:
            datasets.extend(
                superset_client.list_datasets(
                    database_id=database_id,
                    catalog_name=catalog_name,
                    schema_name=schema,
                    limit=limit,
                )
            )
        except Exception as ex:  # pylint: disable=broad-except - best-effort
            logger.warning(
                "Recall access load failed for schema %s: %s", schema, ex
            )
            return RecallAccess()
    return build_recall_access(datasets)


def _ref_is_accessible(ref: str, access: RecallAccess) -> bool:
    if ref in access.accessible_tables:
        return True
    # Unqualified ref (no schema in the source SQL) -> lenient name match, so
    # single-schema deployments that emit bare table names still recall.
    return "." not in ref and ref in access.accessible_names


def _pair_is_accessible(pair: NlSqlPair, access: RecallAccess) -> bool:
    """Stage A — fail closed: keep only pairs whose every table is reachable."""

    refs = pair.referenced_tables or []
    if not refs:
        return False  # unknown references -> never surface (legacy / unparseable)
    return all(_ref_is_accessible(ref, access) for ref in refs)


def _pair_has_foreign_schema(pair: NlSqlPair, access: RecallAccess) -> bool:
    if not access.project_schemas:
        return False
    return any(
        schema not in access.project_schemas
        for schema in (pair.referenced_schemas or [])
    )


def _pair_is_fully_onboarded(pair: NlSqlPair, access: RecallAccess) -> bool:
    if not access.onboarded_tables:
        return False
    for ref in pair.referenced_tables or []:
        if ref in access.onboarded_tables:
            continue
        if "." not in ref and ref in access.onboarded_names:
            continue
        return False
    return True


def _tokens(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {token for token in normalized.split() if token}


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _dedup_key(question: str, native_sql: str) -> tuple[str, str]:
    """Identity of a confirmed example for write-back dedup (RV4a).

    Two stores of the same normalized question + native SQL are the same example
    (e.g. the user re-ran an auto-execute turn), so the later one refreshes the
    earlier rather than accumulating a duplicate.
    """

    return (_normalize(question), _normalize(native_sql))


def _cache_id(question: str, native_sql: str) -> str:
    """Stable vector-cache row id for a pair: the dedup identity, hashed (C0.1).

    Keying the cache on the dedup identity (not the SQL row id) means a refresh of
    the same normalized question+SQL overwrites its vector in place, and the cache
    id is computable from a recalled pair without a round-trip to the store.
    """

    norm_q, norm_sql = _dedup_key(question, native_sql)
    return hashlib.sha1(  # noqa: S324 - cache key, not security
        f"{norm_q}\x00{norm_sql}".encode()
    ).hexdigest()


def _rank(question: str, pairs: list[NlSqlPair], k: int) -> list[NlSqlPair]:
    q_tokens = _tokens(question)
    if not q_tokens:
        return pairs[:k]
    return sorted(
        pairs,
        key=lambda pair: len(q_tokens & _tokens(pair.question)),
        reverse=True,
    )[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _semantic_rank(
    question: str,
    pairs: list[NlSqlPair],
    k: int,
    embedder: Embedder,
) -> list[NlSqlPair]:
    """Rank examples by embedding cosine similarity to the question (R3/R6).

    Vectors are computed on demand over the bounded candidate set; a future
    LanceDB-backed `sql_pairs` collection can cache them. Any embedding failure
    (or an unavailable embedder) degrades closed to keyword ranking.
    """

    if not pairs or not question.strip() or not embedder.is_available():
        return _rank(question, pairs, k)
    try:
        vectors = embedder.embed([pair.question for pair in pairs])
        query_vector = embedder.embed([question])[0]
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Example embedding failed; keyword recall fallback: %s", ex)
        return _rank(question, pairs, k)
    scored = sorted(
        zip(pairs, vectors, strict=True),
        key=lambda pair: _cosine(query_vector, pair[1]),
        reverse=True,
    )
    return [pair for pair, _ in scored[:k]]


def _recall_rank(
    question: str,
    pairs: list[NlSqlPair],
    k: int,
    embedder: Embedder | None,
) -> list[NlSqlPair]:
    if embedder is not None and embedder.is_available():
        return _semantic_rank(question, pairs, k, embedder)
    return _rank(question, pairs, k)


def _access_filter_and_rank(
    question: str,
    pairs: list[NlSqlPair],
    k: int,
    embedder: Embedder | None,
    access: RecallAccess | None,
) -> list[NlSqlPair]:
    """Access-aware recall (F2): hard-filter, relevance down-rank, then presentation.

    Stage A (fail closed): drop any pair referencing a table the user cannot reach
    (or whose references are unknown). Stage B: stable down-rank — foreign-schema
    pairs sink below non-onboarded, which sink below fully in-scope, preserving
    similarity order within each tier. Stage C: a surviving pair whose tables are
    not all onboarded in the active project keeps its ``native_sql`` but drops its
    project-local ``semantic_sql`` (foreign model names would dangle).
    """

    if access is None:
        return _recall_rank(question, pairs, k, embedder)
    survivors = [pair for pair in pairs if _pair_is_accessible(pair, access)]
    if not survivors:
        return []
    # Rank the full survivor set by similarity, then tier + present.
    ranked = _recall_rank(question, survivors, len(survivors), embedder)
    return _tier_and_present(ranked, k, access)


def _tier_and_present(
    ranked: list[NlSqlPair], k: int, access: RecallAccess
) -> list[NlSqlPair]:
    """Stage B (stable down-rank into relevance tiers) + Stage C (presentation).

    ``ranked`` must already be access-filtered (Stage A) and similarity-ordered.
    Python's stable sort preserves similarity order within each tier.
    """

    def _tier(pair: NlSqlPair) -> int:
        if _pair_has_foreign_schema(pair, access):
            return 2
        if not _pair_is_fully_onboarded(pair, access):
            return 1
        return 0

    chosen = sorted(ranked, key=_tier)[:k]
    presented: list[NlSqlPair] = []
    for pair in chosen:
        if _pair_is_fully_onboarded(pair, access):
            presented.append(pair)
        else:
            # Strip project-local semantic SQL; keep the DB-valid native SQL.
            # Breadcrumb the provenance (out_of_scope) so the explain UI can mark
            # this as learned from outside the project's onboarded tables (F2/2C).
            presented.append(
                pair.model_copy(
                    update={
                        "semantic_sql": "",
                        "result_meta": {
                            **(pair.result_meta or {}),
                            "out_of_scope": True,
                        },
                    }
                )
            )
    return presented


class Memory(Protocol):
    def recall_examples(
        self,
        question: str,
        *,
        database_id: int,
        k: int,
        access: RecallAccess | None = None,
    ) -> list[NlSqlPair]:
        """Return up to k confirmed examples relevant to the question.

        Pairs are pooled per **database** (shared across users and projects). When
        ``access`` is supplied the access filter (F2) is applied; production call
        sites always supply it.
        """

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        database_id: int,
        project_id: str | None = None,
        created_by: str | None = None,
        referenced_tables: list[str] | None = None,
        referenced_schemas: list[str] | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        """Persist a confirmed NL->SQL pair for future recall."""


class NullMemory:
    """No-op memory used when the learning loop is disabled."""

    def recall_examples(
        self,
        question: str,
        *,
        database_id: int,
        k: int,
        access: RecallAccess | None = None,
    ) -> list[NlSqlPair]:
        return []

    def store_confirmed(self, **kwargs: Any) -> None:
        return None


class InMemoryMemory:
    """Process-local memory store (tests/dev). Keyed by ``database_id``."""

    def __init__(
        self, max_examples: int = 0, *, embedder: Embedder | None = None
    ) -> None:
        # keyed by database_id (the shared, database-level pool)
        self._pairs: dict[int, list[NlSqlPair]] = {}
        self.max_examples = max_examples
        self.embedder = embedder

    def recall_examples(
        self,
        question: str,
        *,
        database_id: int,
        k: int,
        access: RecallAccess | None = None,
    ) -> list[NlSqlPair]:
        pairs = self._pairs.get(database_id, [])
        return _access_filter_and_rank(question, pairs, k, self.embedder, access)

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        database_id: int,
        project_id: str | None = None,
        created_by: str | None = None,
        referenced_tables: list[str] | None = None,
        referenced_schemas: list[str] | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        pair = NlSqlPair(
            question=question,
            semantic_sql=semantic_sql,
            native_sql=native_sql,
            referenced_tables=referenced_tables or [],
            referenced_schemas=referenced_schemas or [],
            result_meta=result_meta or {},
        )
        bucket = self._pairs.setdefault(database_id, [])
        key = _dedup_key(question, native_sql)
        for index, existing in enumerate(bucket):
            if _dedup_key(existing.question, existing.native_sql) == key:
                bucket[index] = pair  # refresh the existing example in place
                return
        bucket.append(pair)
        # Decay: keep only the most recent ``max_examples`` (oldest evicted).
        if self.max_examples > 0 and len(bucket) > self.max_examples:
            del bucket[: len(bucket) - self.max_examples]


class SqlAlchemyMemory:
    """Durable, cross-worker memory store. Keyed by ``database_id`` (shared pool)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        max_examples: int = 0,
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.max_examples = max_examples
        self.embedder = embedder

    def load_candidates(self, *, database_id: int) -> list[NlSqlPair]:
        """The bounded recall window for a database pool, unranked (no embed).

        Exposed so a vector-cache wrapper can rank by ANN id lookup instead of
        re-embedding this set every query (C0.1).
        """

        with self.session_factory() as session:
            rows = session.scalars(
                select(AiAgentNlSqlExample)
                .where(AiAgentNlSqlExample.database_id == database_id)
                .order_by(AiAgentNlSqlExample.created_at.desc())
                .limit(200)
            ).all()
        return [
            NlSqlPair(
                id=row.id,
                question=row.question,
                semantic_sql=row.semantic_sql,
                native_sql=row.native_sql,
                referenced_tables=list(row.referenced_tables or []),
                referenced_schemas=list(row.referenced_schemas or []),
                result_meta=row.result_meta or {},
            )
            for row in rows
        ]

    def recall_examples(
        self,
        question: str,
        *,
        database_id: int,
        k: int,
        access: RecallAccess | None = None,
    ) -> list[NlSqlPair]:
        pairs = self.load_candidates(database_id=database_id)
        return _access_filter_and_rank(question, pairs, k, self.embedder, access)

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        database_id: int,
        project_id: str | None = None,
        created_by: str | None = None,
        referenced_tables: list[str] | None = None,
        referenced_schemas: list[str] | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        key = _dedup_key(question, native_sql)
        with self.session_factory() as session:
            # Dedup against recent examples for this database pool (RV4a). The scan
            # is bounded; the store is single-worker-scale (see R-C).
            recent = session.scalars(
                select(AiAgentNlSqlExample)
                .where(AiAgentNlSqlExample.database_id == database_id)
                .order_by(AiAgentNlSqlExample.created_at.desc())
                .limit(500)
            ).all()
            for row in recent:
                if _dedup_key(row.question, row.native_sql) == key:
                    # Refresh recency + latest result metadata in place.
                    row.created_at = datetime.now(timezone.utc)
                    row.semantic_sql = semantic_sql
                    row.referenced_tables = referenced_tables or []
                    row.referenced_schemas = referenced_schemas or []
                    row.result_meta = result_meta or {}
                    session.commit()
                    return
            session.add(
                AiAgentNlSqlExample(
                    id=uuid.uuid4().hex,
                    # owner_id is authorship metadata only (no longer a key).
                    owner_id=created_by or "",
                    project_id=project_id,
                    database_id=database_id,
                    # scope_hash retained NOT NULL for back-compat; unused as a key.
                    scope_hash="",
                    question=question,
                    semantic_sql=semantic_sql,
                    native_sql=native_sql,
                    referenced_tables=referenced_tables or [],
                    referenced_schemas=referenced_schemas or [],
                    result_meta=result_meta or {},
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
            self._evict_old(session, database_id)

    def _evict_old(self, session: Session, database_id: int) -> None:
        """Decay: delete examples for this database pool past ``max_examples``."""

        if self.max_examples <= 0:
            return
        stale = session.scalars(
            select(AiAgentNlSqlExample)
            .where(AiAgentNlSqlExample.database_id == database_id)
            .order_by(AiAgentNlSqlExample.created_at.desc())
            .offset(self.max_examples)
        ).all()
        if not stale:
            return
        for row in stale:
            session.delete(row)
        session.commit()


class LanceDbMemory:
    """SqlAlchemy memory + a persistent `sql_pairs` vector cache (plan C0.1).

    The inner SQL store stays the source of truth (durability, dedup, eviction);
    the cache embeds each confirmed question **once at store time**, so recall is
    an ANN id lookup over the cache instead of re-embedding the candidate set every
    query. Degrades closed: when the cache is unavailable/cold (``search`` →
    ``None``) recall falls back to the inner store's ranking (itself keyword
    without an embedder). Stale cache rows (evicted from SQL) map to nothing and
    are inert.
    """

    def __init__(self, inner: SqlAlchemyMemory, cache: LanceVectorCache) -> None:
        self.inner = inner
        self.cache = cache

    @staticmethod
    def _scope_key(database_id: int) -> str:
        return f"db:{database_id}"

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        database_id: int,
        project_id: str | None = None,
        created_by: str | None = None,
        referenced_tables: list[str] | None = None,
        referenced_schemas: list[str] | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        self.inner.store_confirmed(
            question=question,
            semantic_sql=semantic_sql,
            native_sql=native_sql,
            database_id=database_id,
            project_id=project_id,
            created_by=created_by,
            referenced_tables=referenced_tables,
            referenced_schemas=referenced_schemas,
            result_meta=result_meta,
        )
        self.cache.upsert(
            scope_key=self._scope_key(database_id),
            row_id=_cache_id(question, native_sql),
            text=question,
        )

    def recall_examples(
        self,
        question: str,
        *,
        database_id: int,
        k: int,
        access: RecallAccess | None = None,
    ) -> list[NlSqlPair]:
        candidates = self.inner.load_candidates(database_id=database_id)
        # Stage A (access filter) is applied regardless of the cache path, so a pair
        # the user cannot reach is never surfaced — fail closed.
        if access is not None:
            candidates = [c for c in candidates if _pair_is_accessible(c, access)]
        ids = self.cache.search(
            scope_key=self._scope_key(database_id),
            query=question,
            k=len(candidates) or k,
        )
        if ids is None:
            # Cache unavailable/cold → similarity ranking (degrade closed).
            ordered = _recall_rank(
                question, candidates, len(candidates) or k, self.inner.embedder
            )
        else:
            by_cache_id = {
                _cache_id(pair.question, pair.native_sql): pair for pair in candidates
            }
            ordered = [by_cache_id[i] for i in ids if i in by_cache_id]
            # Fill from candidates the cache did not surface (e.g. not yet embedded)
            # so we never return fewer than the SQL window would.
            chosen = {id(pair) for pair in ordered}
            ordered.extend(pair for pair in candidates if id(pair) not in chosen)
        if access is None:
            return ordered[:k]
        return _tier_and_present(ordered, k, access)


def _lancedb_path(config: AgentConfig) -> str:
    if config.wren_lancedb_path:
        return config.wren_lancedb_path
    return str(Path(config.agent_storage_dir) / "lancedb")


def create_memory(
    config: AgentConfig,
    *,
    session_factory: "sessionmaker[Session] | None" = None,
    embedder: Embedder | None = None,
) -> Memory:
    """Build the configured memory store; ``NullMemory`` when learning is off.

    When an ``embedder`` is available, recall ranks examples by **semantic**
    similarity (R3/R6); otherwise it degrades closed to keyword token overlap. With
    ``wren_memory_store="lancedb"`` and an available embedder the durable store is
    wrapped in a persistent `sql_pairs` vector cache (plan C0.1) so recall is an ANN
    lookup rather than a per-query re-embed.
    """

    if not config.wren_memory_learning_enabled or config.wren_memory_store == "none":
        return NullMemory()
    if config.wren_memory_store in {"sqlalchemy", "lancedb"}:
        if session_factory is None:
            raise ValueError("Durable memory store requires a database.")
        inner = SqlAlchemyMemory(
            session_factory,
            max_examples=config.wren_memory_max_examples,
            embedder=embedder,
        )
        if (
            config.wren_memory_store == "lancedb"
            and embedder is not None
            and embedder.is_available()
        ):
            cache = LanceVectorCache(embedder, _lancedb_path(config), "sql_pairs")
            if cache.is_available():
                return LanceDbMemory(inner, cache)
        return inner
    return NullMemory()
