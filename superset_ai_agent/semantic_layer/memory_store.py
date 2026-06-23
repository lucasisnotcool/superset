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
    """One confirmed natural-language to SQL example."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    question: str
    semantic_sql: str
    native_sql: str
    result_meta: dict[str, Any] = Field(default_factory=dict)


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


class Memory(Protocol):
    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
        """Return up to k confirmed examples relevant to the question."""

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        scope_hash: str,
        owner_id: str,
        project_id: str | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        """Persist a confirmed NL->SQL pair for future recall."""


class NullMemory:
    """No-op memory used when the learning loop is disabled."""

    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
        return []

    def store_confirmed(self, **kwargs: Any) -> None:
        return None


class InMemoryMemory:
    """Process-local memory store (tests/dev)."""

    def __init__(
        self, max_examples: int = 0, *, embedder: Embedder | None = None
    ) -> None:
        # keyed by (owner_id, scope_hash)
        self._pairs: dict[tuple[str, str], list[NlSqlPair]] = {}
        self.max_examples = max_examples
        self.embedder = embedder

    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
        pairs = self._pairs.get((owner_id, scope_hash), [])
        return _recall_rank(question, pairs, k, self.embedder)

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        scope_hash: str,
        owner_id: str,
        project_id: str | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        pair = NlSqlPair(
            question=question,
            semantic_sql=semantic_sql,
            native_sql=native_sql,
            result_meta=result_meta or {},
        )
        bucket = self._pairs.setdefault((owner_id, scope_hash), [])
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
    """Durable, cross-worker memory store."""

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

    def load_candidates(
        self, *, scope_hash: str, owner_id: str
    ) -> list[NlSqlPair]:
        """The bounded recall window for an owner+scope, unranked (no embed).

        Exposed so a vector-cache wrapper can rank by ANN id lookup instead of
        re-embedding this set every query (C0.1).
        """

        with self.session_factory() as session:
            rows = session.scalars(
                select(AiAgentNlSqlExample)
                .where(
                    AiAgentNlSqlExample.owner_id == owner_id,
                    AiAgentNlSqlExample.scope_hash == scope_hash,
                )
                .order_by(AiAgentNlSqlExample.created_at.desc())
                .limit(200)
            ).all()
        return [
            NlSqlPair(
                id=row.id,
                question=row.question,
                semantic_sql=row.semantic_sql,
                native_sql=row.native_sql,
                result_meta=row.result_meta or {},
            )
            for row in rows
        ]

    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
        pairs = self.load_candidates(scope_hash=scope_hash, owner_id=owner_id)
        return _recall_rank(question, pairs, k, self.embedder)

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        scope_hash: str,
        owner_id: str,
        project_id: str | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        key = _dedup_key(question, native_sql)
        with self.session_factory() as session:
            # Dedup against recent examples for this owner+scope (RV4a). The scan
            # is bounded; the store is single-worker-scale (see R-C).
            recent = session.scalars(
                select(AiAgentNlSqlExample)
                .where(
                    AiAgentNlSqlExample.owner_id == owner_id,
                    AiAgentNlSqlExample.scope_hash == scope_hash,
                )
                .order_by(AiAgentNlSqlExample.created_at.desc())
                .limit(500)
            ).all()
            for row in recent:
                if _dedup_key(row.question, row.native_sql) == key:
                    # Refresh recency + latest result metadata in place.
                    row.created_at = datetime.now(timezone.utc)
                    row.semantic_sql = semantic_sql
                    row.result_meta = result_meta or {}
                    session.commit()
                    return
            session.add(
                AiAgentNlSqlExample(
                    id=uuid.uuid4().hex,
                    owner_id=owner_id,
                    project_id=project_id,
                    scope_hash=scope_hash,
                    question=question,
                    semantic_sql=semantic_sql,
                    native_sql=native_sql,
                    result_meta=result_meta or {},
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
            self._evict_old(session, owner_id, scope_hash)

    def _evict_old(self, session: Session, owner_id: str, scope_hash: str) -> None:
        """Decay: delete examples for this owner+scope past ``max_examples``."""

        if self.max_examples <= 0:
            return
        stale = session.scalars(
            select(AiAgentNlSqlExample)
            .where(
                AiAgentNlSqlExample.owner_id == owner_id,
                AiAgentNlSqlExample.scope_hash == scope_hash,
            )
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
    def _scope_key(owner_id: str, scope_hash: str) -> str:
        return f"{owner_id}:{scope_hash}"

    def store_confirmed(
        self,
        *,
        question: str,
        semantic_sql: str,
        native_sql: str,
        scope_hash: str,
        owner_id: str,
        project_id: str | None = None,
        result_meta: dict[str, Any] | None = None,
    ) -> None:
        self.inner.store_confirmed(
            question=question,
            semantic_sql=semantic_sql,
            native_sql=native_sql,
            scope_hash=scope_hash,
            owner_id=owner_id,
            project_id=project_id,
            result_meta=result_meta,
        )
        self.cache.upsert(
            scope_key=self._scope_key(owner_id, scope_hash),
            row_id=_cache_id(question, native_sql),
            text=question,
        )

    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
        candidates = self.inner.load_candidates(
            scope_hash=scope_hash, owner_id=owner_id
        )
        ids = self.cache.search(
            scope_key=self._scope_key(owner_id, scope_hash), query=question, k=k
        )
        if ids is None:
            # Cache unavailable/cold → existing ranked recall (degrade closed).
            return _recall_rank(question, candidates, k, self.inner.embedder)
        by_cache_id = {
            _cache_id(pair.question, pair.native_sql): pair for pair in candidates
        }
        ordered = [by_cache_id[i] for i in ids if i in by_cache_id]
        # Fill from candidates not surfaced by the cache (e.g. not yet embedded) so
        # we never return fewer than the SQL window would, preserving recall.
        chosen = {id(pair) for pair in ordered}
        for pair in candidates:
            if len(ordered) >= k:
                break
            if id(pair) not in chosen:
                ordered.append(pair)
        return ordered[:k]


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
