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

"""SemanticPipeline facade composing the seams (wren_full.md 4.1)."""

from __future__ import annotations

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.schemas import AgentQueryRequest
from superset_ai_agent.semantic_layer.memory_store import InMemoryMemory
from superset_ai_agent.semantic_layer.pipeline import SemanticPipeline
from tests.unit_tests.superset_ai_agent.test_graph_semantic_engine import (
    _FakeRewriteEngine,
    _RecordingSupersetClient,
)

_SEMANTIC_SQL = "SELECT name, SUM(num) AS total FROM birth_names GROUP BY name"


def _context() -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="examples", backend="postgresql"),
        datasets=[
            DatasetMetadata(
                id=16,
                table_name="birth_names",
                database_id=1,
                columns=[ColumnSummary(name="num", type="BIGINT")],
                metrics=[],
            )
        ],
    )


def _request(execute: bool = True) -> AgentQueryRequest:
    return AgentQueryRequest(
        question="top names",
        database_id=1,
        schema_name="public",
        dataset_ids=[16],
        execute=execute,
    )


def test_pipeline_rewrites_validates_executes_and_stores() -> None:
    superset = _RecordingSupersetClient()
    memory = InMemoryMemory()
    pipeline = SemanticPipeline(
        config=AgentConfig(),
        superset_client=superset,
        semantic_engine=_FakeRewriteEngine(),
        memory=memory,
    )

    result = pipeline.plan_and_execute(
        semantic_sql=_SEMANTIC_SQL,
        context=_context(),
        request=_request(),
        owner_id="owner-1",
    )

    # Engine rewrote the logical table to the physical one before execution.
    assert result.rewritten is True
    assert "public.birth_names" in result.native_sql
    assert superset.executed_sql
    assert "public.birth_names" in superset.executed_sql[0]
    assert result.validation.is_valid
    assert result.execution_result is not None
    # A confirmed example was stored and is recallable from this database's pool.
    assert result.stored_example is True
    recalled = memory.recall_examples("top names", database_id=1, k=3)
    assert any(pair.question == "top names" for pair in recalled)


def test_pipeline_plan_only_does_not_execute_or_store() -> None:
    superset = _RecordingSupersetClient()
    memory = InMemoryMemory()
    pipeline = SemanticPipeline(
        config=AgentConfig(),
        superset_client=superset,
        semantic_engine=_FakeRewriteEngine(),
        memory=memory,
    )

    result = pipeline.plan_and_execute(
        semantic_sql=_SEMANTIC_SQL,
        context=_context(),
        request=_request(execute=False),
        owner_id="owner-1",
        execute=False,
    )

    assert result.rewritten is True
    assert result.execution_result is None
    assert result.stored_example is False
    assert superset.executed_sql == []


def test_pipeline_classify_intent_defaults_without_model() -> None:
    pipeline = SemanticPipeline(
        config=AgentConfig(),
        superset_client=_RecordingSupersetClient(),
        semantic_engine=_FakeRewriteEngine(),
    )
    assert pipeline.classify_intent("anything").intent == "text_to_sql"
