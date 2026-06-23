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

from __future__ import annotations

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.semantic_layer.instructions import (
    create_instruction_store,
    InMemoryInstructionStore,
    LanceDbInstructionStore,
    SqlAlchemyInstructionStore,
)

_REVENUE = {"revenue", "sales", "total"}


class _TopicEmbedder:
    """Deterministic 2-D embedder: revenue topic -> [1,0], else [0,1]."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.embedded: list[list[str]] = []

    def is_available(self) -> bool:
        return self.available

    def dimensions(self) -> int:
        return 2

    def signature(self) -> str:
        return "topic:2"

    def embed(self, texts):
        self.embedded.append(list(texts))
        return [
            [1.0, 0.0] if _REVENUE & {w.lower() for w in t.split()} else [0.0, 1.0]
            for t in texts
        ]


def test_add_list_delete_roundtrip() -> None:
    store = InMemoryInstructionStore()
    created = store.add(
        instruction="Always filter to active rows",
        scope_hash="s1",
        owner_id="u1",
    )

    listed = store.list_instructions(scope_hash="s1", owner_id="u1")
    assert [item.id for item in listed] == [created.id]

    assert store.delete(created.id, owner_id="u1") is True
    assert store.list_instructions(scope_hash="s1", owner_id="u1") == []
    # Deleting again (or a stranger's id) is a no-op.
    assert store.delete(created.id, owner_id="u1") is False


def test_delete_is_owner_scoped() -> None:
    store = InMemoryInstructionStore()
    created = store.add(instruction="x", scope_hash="s1", owner_id="u1")
    assert store.delete(created.id, owner_id="other") is False
    assert store.list_instructions(scope_hash="s1", owner_id="u1")  # still present


def test_recall_returns_globals_plus_relevant_non_global() -> None:
    store = InMemoryInstructionStore(embedder=_TopicEmbedder())
    store.add(
        instruction="GLOBAL: never expose PII",
        scope_hash="s1",
        owner_id="u1",
        is_global=True,
    )
    store.add(
        instruction="quarterly revenue rounds to 2 decimals",
        scope_hash="s1",
        owner_id="u1",
    )
    store.add(
        instruction="employee names are case-insensitive",
        scope_hash="s1",
        owner_id="u1",
    )

    recalled = store.recall(
        "total sales report", scope_hash="s1", owner_id="u1", k=1
    )
    texts = [item.instruction for item in recalled]
    # Global always present; the revenue-topic non-global wins the single slot.
    assert "GLOBAL: never expose PII" in texts
    assert "quarterly revenue rounds to 2 decimals" in texts
    assert "employee names are case-insensitive" not in texts


def test_recall_is_scope_isolated() -> None:
    store = InMemoryInstructionStore()
    store.add(instruction="scope-1 rule", scope_hash="s1", owner_id="u1")
    assert store.recall("q", scope_hash="s2", owner_id="u1", k=3) == []


def test_create_instruction_store_durable_roundtrip(tmp_path) -> None:
    config = AgentConfig(
        wren_memory_store="sqlalchemy",
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'inst.db'}",
    )
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))

    store = create_instruction_store(config, session_factory=session_factory)
    assert isinstance(store, SqlAlchemyInstructionStore)
    created = store.add(
        instruction="durable rule", scope_hash="s1", owner_id="u1", is_global=True
    )

    # A fresh store instance on the same DB recalls it (cross-worker durability).
    store2 = create_instruction_store(config, session_factory=session_factory)
    recalled = store2.recall("anything", scope_hash="s1", owner_id="u1", k=3)
    assert [item.instruction for item in recalled] == ["durable rule"]
    assert store2.delete(created.id, owner_id="u1") is True


def test_create_instruction_store_in_memory_without_db() -> None:
    store = create_instruction_store(AgentConfig())  # wren_memory_store="none"
    assert isinstance(store, InMemoryInstructionStore)


# --- C0.2: persistent `instructions` vector cache -----------------------------


def _lancedb_config(tmp_path):
    return AgentConfig(
        wren_memory_store="lancedb",
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'inst.db'}",
        wren_lancedb_path=str(tmp_path / "lancedb"),
    )


def test_create_instruction_store_lancedb_wraps_with_cache(tmp_path) -> None:
    pytest.importorskip("lancedb")
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    store = create_instruction_store(
        config, session_factory=session_factory, embedder=_TopicEmbedder()
    )
    assert isinstance(store, LanceDbInstructionStore)


def test_create_instruction_store_lancedb_falls_back_without_embedder(tmp_path) -> None:
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    store = create_instruction_store(
        config,
        session_factory=session_factory,
        embedder=_TopicEmbedder(available=False),
    )
    assert isinstance(store, SqlAlchemyInstructionStore)


def test_lancedb_instruction_recall_globals_plus_cache_ranked(tmp_path) -> None:
    pytest.importorskip("lancedb")
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    embedder = _TopicEmbedder()
    store = create_instruction_store(
        config, session_factory=session_factory, embedder=embedder
    )
    assert isinstance(store, LanceDbInstructionStore)
    store.add(
        instruction="GLOBAL never expose PII",
        scope_hash="s1",
        owner_id="u1",
        is_global=True,
    )
    store.add(
        instruction="quarterly revenue rounds to 2 decimals",
        scope_hash="s1",
        owner_id="u1",
    )
    store.add(
        instruction="employee names are case-insensitive",
        scope_hash="s1",
        owner_id="u1",
    )

    embedder.embedded = []  # watch only the recall, not the write-time embeds
    recalled = store.recall("total sales report", scope_hash="s1", owner_id="u1", k=1)
    texts = [item.instruction for item in recalled]
    assert "GLOBAL never expose PII" in texts  # global always applies
    assert "quarterly revenue rounds to 2 decimals" in texts  # ANN-ranked non-global
    assert "employee names are case-insensitive" not in texts
    # Recall embedded only the query — non-globals were embedded once at write time.
    assert embedder.embedded == [["total sales report"]]
