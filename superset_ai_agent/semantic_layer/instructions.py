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

"""Instructions seam — user-authored prompt guidance (Wren `instructions`).

Instructions are scoped by ``owner_id`` + ``scope_hash`` (governance: they are
*context*, never permission sources). ``is_global`` instructions always apply for
the scope; non-global ones are retrieved by similarity to the question (embedding
cosine, degrade-closed to keyword overlap). Injected into the SQL prompt at draft
time so an operator can steer generation without code changes.
"""

from __future__ import annotations

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
from superset_ai_agent.persistence.models import AiAgentInstruction
from superset_ai_agent.semantic_layer.vector_cache import LanceVectorCache

logger = logging.getLogger(__name__)


class Instruction(BaseModel):
    """One user-authored instruction."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    instruction: str
    is_global: bool = False
    project_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def _tokens(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {token for token in normalized.split() if token}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _rank_non_global(
    question: str,
    pairs: list[Instruction],
    k: int,
    embedder: Embedder | None,
) -> list[Instruction]:
    """Rank non-global instructions by relevance to the question.

    Embedding cosine when an embedder is available; otherwise keyword token
    overlap. Any embedding failure degrades closed to keyword.
    """

    if k <= 0 or not pairs:
        return []
    if embedder is not None and embedder.is_available() and question.strip():
        try:
            vectors = embedder.embed([item.instruction for item in pairs])
            query_vector = embedder.embed([question])[0]
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Instruction embedding failed; keyword fallback: %s", ex)
        else:
            scored = sorted(
                zip(pairs, vectors, strict=True),
                key=lambda pair: _cosine(query_vector, pair[1]),
                reverse=True,
            )
            return [item for item, _ in scored[:k]]
    q_tokens = _tokens(question)
    if not q_tokens:
        return pairs[:k]
    return sorted(
        pairs,
        key=lambda item: len(q_tokens & _tokens(item.instruction)),
        reverse=True,
    )[:k]


def _recall(
    question: str,
    instructions: list[Instruction],
    k: int,
    embedder: Embedder | None,
) -> list[Instruction]:
    """Global instructions (always) + the top-k relevant non-global ones."""

    globals_ = [item for item in instructions if item.is_global]
    non_global = [item for item in instructions if not item.is_global]
    return [*globals_, *_rank_non_global(question, non_global, k, embedder)]


class InstructionStore(Protocol):
    def add(
        self,
        *,
        instruction: str,
        scope_hash: str,
        owner_id: str,
        is_global: bool = False,
        project_id: str | None = None,
    ) -> Instruction:
        """Persist a new instruction."""

    def list_instructions(
        self, *, scope_hash: str, owner_id: str
    ) -> list[Instruction]:
        """List instructions for an owner+scope (newest first)."""

    def delete(self, instruction_id: str, *, owner_id: str) -> bool:
        """Delete one instruction; returns whether it existed."""

    def recall(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[Instruction]:
        """Global + top-k relevant non-global instructions for a question."""


class NullInstructionStore:
    """No-op store used when instructions are disabled / unconfigured."""

    def add(self, **kwargs: Any) -> Instruction:
        return Instruction(instruction=kwargs.get("instruction", ""))

    def list_instructions(self, **kwargs: Any) -> list[Instruction]:
        return []

    def delete(self, instruction_id: str, **kwargs: Any) -> bool:
        return False

    def recall(self, question: str, **kwargs: Any) -> list[Instruction]:
        return []


class InMemoryInstructionStore:
    """Process-local instruction store (tests/dev)."""

    def __init__(self, *, embedder: Embedder | None = None) -> None:
        self._items: dict[tuple[str, str], list[Instruction]] = {}
        self.embedder = embedder

    def add(
        self,
        *,
        instruction: str,
        scope_hash: str,
        owner_id: str,
        is_global: bool = False,
        project_id: str | None = None,
    ) -> Instruction:
        item = Instruction(
            instruction=instruction, is_global=is_global, project_id=project_id
        )
        self._items.setdefault((owner_id, scope_hash), []).append(item)
        return item

    def list_instructions(self, *, scope_hash: str, owner_id: str) -> list[Instruction]:
        items = self._items.get((owner_id, scope_hash), [])
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def delete(self, instruction_id: str, *, owner_id: str) -> bool:
        for key, items in self._items.items():
            if key[0] != owner_id:
                continue
            for index, item in enumerate(items):
                if item.id == instruction_id:
                    del items[index]
                    return True
        return False

    def recall(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[Instruction]:
        items = self._items.get((owner_id, scope_hash), [])
        return _recall(question, items, k, self.embedder)


class SqlAlchemyInstructionStore:
    """Durable, cross-worker instruction store."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.embedder = embedder

    def add(
        self,
        *,
        instruction: str,
        scope_hash: str,
        owner_id: str,
        is_global: bool = False,
        project_id: str | None = None,
    ) -> Instruction:
        item = Instruction(
            instruction=instruction, is_global=is_global, project_id=project_id
        )
        with self.session_factory() as session:
            session.add(
                AiAgentInstruction(
                    id=item.id,
                    owner_id=owner_id,
                    project_id=project_id,
                    scope_hash=scope_hash,
                    instruction=instruction,
                    is_global=is_global,
                    created_at=item.created_at,
                )
            )
            session.commit()
        return item

    def list_instructions(self, *, scope_hash: str, owner_id: str) -> list[Instruction]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AiAgentInstruction)
                .where(
                    AiAgentInstruction.owner_id == owner_id,
                    AiAgentInstruction.scope_hash == scope_hash,
                )
                .order_by(AiAgentInstruction.created_at.desc())
                .limit(500)
            ).all()
        return [_from_model(row) for row in rows]

    def delete(self, instruction_id: str, *, owner_id: str) -> bool:
        with self.session_factory() as session:
            row = session.get(AiAgentInstruction, instruction_id)
            if row is None or row.owner_id != owner_id:
                return False
            session.delete(row)
            session.commit()
            return True

    def recall(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[Instruction]:
        items = self.list_instructions(scope_hash=scope_hash, owner_id=owner_id)
        return _recall(question, items, k, self.embedder)


def _from_model(row: AiAgentInstruction) -> Instruction:
    return Instruction(
        id=row.id,
        instruction=row.instruction,
        is_global=row.is_global,
        project_id=row.project_id,
        created_at=row.created_at,
    )


class LanceDbInstructionStore:
    """SqlAlchemy instruction store + a persistent `instructions` cache (C0.2).

    The inner SQL store stays the source of truth; the cache embeds each
    *non-global* instruction **once at write time** so recall ranks them by an ANN
    id lookup rather than re-embedding the candidate set per query. Globals always
    apply, so they are never embedded. Degrades closed: a cold/unavailable cache
    (``search`` → ``None``) falls back to the inner store's ``_recall``.

    Delete does not know the scope, so it cannot target the per-scope cache table;
    a deleted instruction's cache row is left inert (it maps to nothing on recall
    once gone from SQL), matching the memory cache's stale-row behavior.
    """

    def __init__(
        self, inner: SqlAlchemyInstructionStore, cache: LanceVectorCache
    ) -> None:
        self.inner = inner
        self.cache = cache

    @staticmethod
    def _scope_key(owner_id: str, scope_hash: str) -> str:
        return f"{owner_id}:{scope_hash}"

    def add(
        self,
        *,
        instruction: str,
        scope_hash: str,
        owner_id: str,
        is_global: bool = False,
        project_id: str | None = None,
    ) -> Instruction:
        item = self.inner.add(
            instruction=instruction,
            scope_hash=scope_hash,
            owner_id=owner_id,
            is_global=is_global,
            project_id=project_id,
        )
        if not is_global:
            self.cache.upsert(
                scope_key=self._scope_key(owner_id, scope_hash),
                row_id=item.id,
                text=instruction,
            )
        return item

    def list_instructions(self, *, scope_hash: str, owner_id: str) -> list[Instruction]:
        return self.inner.list_instructions(scope_hash=scope_hash, owner_id=owner_id)

    def delete(self, instruction_id: str, *, owner_id: str) -> bool:
        return self.inner.delete(instruction_id, owner_id=owner_id)

    def recall(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[Instruction]:
        items = self.inner.list_instructions(scope_hash=scope_hash, owner_id=owner_id)
        ids = self.cache.search(
            scope_key=self._scope_key(owner_id, scope_hash), query=question, k=k
        )
        if ids is None:
            return _recall(question, items, k, self.inner.embedder)
        globals_ = [item for item in items if item.is_global]
        non_global = [item for item in items if not item.is_global]
        by_id = {item.id: item for item in non_global}
        ranked = [by_id[i] for i in ids if i in by_id]
        chosen = {item.id for item in ranked}
        for item in non_global:
            if len(ranked) >= k:
                break
            if item.id not in chosen:
                ranked.append(item)
        return [*globals_, *ranked[:k]]


def _lancedb_path(config: AgentConfig) -> str:
    if config.wren_lancedb_path:
        return config.wren_lancedb_path
    return str(Path(config.agent_storage_dir) / "lancedb")


def create_instruction_store(
    config: AgentConfig,
    *,
    session_factory: "sessionmaker[Session] | None" = None,
    embedder: Embedder | None = None,
) -> InstructionStore:
    """Build the configured instruction store.

    Durable when the memory store is sqlalchemy-backed (it shares the agent DB);
    otherwise process-local. ``NullInstructionStore`` is never returned here so
    instructions work even without the learning loop — they are independent. With
    ``wren_memory_store="lancedb"`` and an available embedder the durable store is
    wrapped in a persistent `instructions` vector cache (plan C0.2).
    """

    if config.wren_memory_store in {"sqlalchemy", "lancedb"}:
        if session_factory is None:
            raise ValueError("Durable instruction store requires a database.")
        inner = SqlAlchemyInstructionStore(session_factory, embedder=embedder)
        if (
            config.wren_memory_store == "lancedb"
            and embedder is not None
            and embedder.is_available()
        ):
            cache = LanceVectorCache(embedder, _lancedb_path(config), "instructions")
            if cache.is_available():
                return LanceDbInstructionStore(inner, cache)
        return inner
    return InMemoryInstructionStore(embedder=embedder)
