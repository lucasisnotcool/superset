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

"""Phase 2.3 — Memory learning loop (NL->SQL examples)."""

from __future__ import annotations

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.semantic_layer.memory_store import (
    create_memory,
    InMemoryMemory,
    NullMemory,
    SqlAlchemyMemory,
)


def test_in_memory_store_and_recall() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="top names by births",
        semantic_sql="SELECT 1",
        native_sql="SELECT 1",
        scope_hash="s1",
        owner_id="u1",
    )
    recalled = memory.recall_examples(
        "show top names", scope_hash="s1", owner_id="u1", k=3
    )
    assert len(recalled) == 1
    assert recalled[0].question == "top names by births"


def test_recall_is_owner_and_scope_isolated() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="q", semantic_sql="x", native_sql="x", scope_hash="s1", owner_id="u1"
    )
    # Different owner sees nothing.
    assert memory.recall_examples("q", scope_hash="s1", owner_id="u2", k=3) == []
    # Different scope sees nothing.
    assert memory.recall_examples("q", scope_hash="s2", owner_id="u1", k=3) == []


def test_null_memory_is_inert() -> None:
    memory = NullMemory()
    memory.store_confirmed(
        question="q", semantic_sql="x", native_sql="x", scope_hash="s", owner_id="u"
    )
    assert memory.recall_examples("q", scope_hash="s", owner_id="u", k=3) == []


def test_sqlalchemy_memory_persists_across_instances(tmp_path) -> None:
    config = AgentConfig(
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'agent.db'}",
    )
    run_migrations(config)
    engine = create_engine_from_config(config)

    writer = SqlAlchemyMemory(create_session_factory(engine))
    writer.store_confirmed(
        question="quarterly revenue by region",
        semantic_sql="SELECT region, revenue FROM sales",
        native_sql="SELECT region, revenue FROM public.sales",
        scope_hash="scope-1",
        owner_id="owner-1",
        result_meta={"rows": 4},
    )

    # New store on the same DB (simulating another worker) recalls the pair.
    reader = SqlAlchemyMemory(create_session_factory(create_engine_from_config(config)))
    recalled = reader.recall_examples(
        "revenue by region", scope_hash="scope-1", owner_id="owner-1", k=3
    )
    assert len(recalled) == 1
    assert recalled[0].native_sql == "SELECT region, revenue FROM public.sales"
    assert recalled[0].result_meta == {"rows": 4}


def test_create_memory_none_is_null() -> None:
    assert isinstance(create_memory(AgentConfig()), NullMemory)  # store=none default
    assert isinstance(
        create_memory(AgentConfig(wren_memory_learning_enabled=False)), NullMemory
    )


def test_create_memory_sqlalchemy_requires_db() -> None:
    with pytest.raises(ValueError, match="requires a database"):
        create_memory(AgentConfig(wren_memory_store="sqlalchemy"))
