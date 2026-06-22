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

import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.models import AiAgentNlSqlExample


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


def _rank(question: str, pairs: list[NlSqlPair], k: int) -> list[NlSqlPair]:
    q_tokens = _tokens(question)
    if not q_tokens:
        return pairs[:k]
    return sorted(
        pairs,
        key=lambda pair: len(q_tokens & _tokens(pair.question)),
        reverse=True,
    )[:k]


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

    def __init__(self) -> None:
        # keyed by (owner_id, scope_hash)
        self._pairs: dict[tuple[str, str], list[NlSqlPair]] = {}

    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
        pairs = self._pairs.get((owner_id, scope_hash), [])
        return _rank(question, pairs, k)

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
        self._pairs.setdefault((owner_id, scope_hash), []).append(pair)


class SqlAlchemyMemory:
    """Durable, cross-worker memory store."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def recall_examples(
        self, question: str, *, scope_hash: str, owner_id: str, k: int
    ) -> list[NlSqlPair]:
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
        pairs = [
            NlSqlPair(
                id=row.id,
                question=row.question,
                semantic_sql=row.semantic_sql,
                native_sql=row.native_sql,
                result_meta=row.result_meta or {},
            )
            for row in rows
        ]
        return _rank(question, pairs, k)

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
        with self.session_factory() as session:
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


def create_memory(
    config: AgentConfig,
    *,
    session_factory: "sessionmaker[Session] | None" = None,
) -> Memory:
    """Build the configured memory store; ``NullMemory`` when learning is off."""

    if not config.wren_memory_learning_enabled or config.wren_memory_store == "none":
        return NullMemory()
    if config.wren_memory_store in {"sqlalchemy", "lancedb"}:
        # LanceDB-backed semantic recall is an optional optimization; until it
        # lands, durable recall uses the SQLAlchemy store (RV1).
        if session_factory is None:
            raise ValueError("Durable memory store requires a database.")
        return SqlAlchemyMemory(session_factory)
    return NullMemory()
