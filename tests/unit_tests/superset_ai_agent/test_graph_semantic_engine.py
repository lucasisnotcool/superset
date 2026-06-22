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

"""Phase 1.2 — SemanticEngine wired into the live query path (graph.py)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.llm.base import ModelResult
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    ExecutionResult,
    SqlExecutionSource,
)
from superset_ai_agent.semantic_layer.engine import PlannedSql
from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
from superset_ai_agent.semantic_layer.schemas import MdlValidationResult

_SEMANTIC_SQL = "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"


class _FakeModelClient:
    def __init__(self, sql: str) -> None:
        self.sql = sql

    def chat(self, messages, *, model=None, format_schema=None) -> ModelResult:
        return ModelResult(
            content=json.dumps({"sql": self.sql, "explanation": "test"})
        )


class _FakeContextProvider:
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(
                id=request.database_id, name="examples", backend="postgresql"
            ),
            datasets=[
                DatasetMetadata(
                    id=16,
                    table_name="birth_names",
                    database_id=request.database_id,
                    columns=[ColumnSummary(name="num", type="BIGINT")],
                    metrics=[],
                )
            ],
        )


class _RecordingSupersetClient:
    def __init__(self) -> None:
        self.executed_sql: list[str] = []

    def get_database_dialect(self, database_id: int) -> str:
        return "postgresql"

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name=None,
        schema_name=None,
        limit: int = 1000,
        source: SqlExecutionSource | None = None,
    ) -> ExecutionResult:
        self.executed_sql.append(sql)
        return ExecutionResult(
            columns=["name", "total_births"],
            rows=[{"name": "Michael", "total_births": 42}],
            row_count=1,
        )


class _FakeRewriteEngine:
    """Stand-in SemanticEngine that rewrites a model name to a physical table."""

    name = "fake"

    def is_available(self) -> bool:
        return True

    def compile(self, mdl_files):
        return compile_manifest(mdl_files)

    def validate(self, manifest, *, deep=False, schema_index=None):
        return MdlValidationResult(valid=True)

    def plan_sql(self, semantic_sql, manifest, *, dialect=None) -> PlannedSql:
        native = semantic_sql.replace("birth_names", "public.birth_names")
        return PlannedSql(
            native_sql=native,
            engine=self.name,
            rewritten=True,
            referenced_tables=["birth_names"],
        )


def _request() -> AgentQueryRequest:
    return AgentQueryRequest(
        question="top names",
        database_id=1,
        schema_name="public",
        dataset_ids=[16],
        execute=True,
    )


def test_engine_rewrite_reaches_execution_and_audit() -> None:
    superset = _RecordingSupersetClient()
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=_FakeModelClient(_SEMANTIC_SQL),
        context_provider=_FakeContextProvider(),
        superset_client=superset,
        semantic_engine=_FakeRewriteEngine(),
    )

    response = graph.run(_request())

    assert response.status == "ok"
    # The engine-rewritten (native) SQL is what Superset executed.
    assert superset.executed_sql, "expected an execution"
    assert "public.birth_names" in superset.executed_sql[0]
    # Audit carries both SQLs + the engine name.
    assert response.audit is not None
    assert response.audit.engine == "fake"
    assert response.audit.semantic_sql == _SEMANTIC_SQL
    assert "public.birth_names" in (response.audit.native_sql or "")
    # The plan step appears in the trace.
    assert any(event.step == "plan_semantic_sql" for event in response.trace)


def test_passthrough_engine_is_a_no_op_but_stamps_audit() -> None:
    superset = _RecordingSupersetClient()
    graph = TextToSqlGraph(
        config=AgentConfig(),  # default wren_engine=passthrough
        model_client=_FakeModelClient(_SEMANTIC_SQL),
        context_provider=_FakeContextProvider(),
        superset_client=superset,
    )

    response = graph.run(_request())

    assert response.status == "ok"
    # No rewrite: the original table name is executed unchanged.
    assert "public.birth_names" not in superset.executed_sql[0]
    assert "birth_names" in superset.executed_sql[0]
    assert response.audit is not None
    assert response.audit.engine == "passthrough"
    assert response.audit.semantic_sql == response.audit.native_sql
    # Passthrough adds no plan_semantic_sql trace event (zero behavior change).
    assert not any(event.step == "plan_semantic_sql" for event in response.trace)


class _CapturingModelClient(_FakeModelClient):
    def __init__(self, sql: str) -> None:
        super().__init__(sql)
        self.payloads: list[str] = []

    def chat(self, messages, *, model=None, format_schema=None):
        self.payloads.append(messages[-1].content)
        return super().chat(messages, model=model, format_schema=format_schema)


def test_semantic_sql_mode_injects_authoring_guidance() -> None:
    model = _CapturingModelClient(_SEMANTIC_SQL)
    graph = TextToSqlGraph(
        config=AgentConfig(wren_engine="wren_core", wren_semantic_sql_enabled=True),
        model_client=model,
        context_provider=_FakeContextProvider(),
        superset_client=_RecordingSupersetClient(),
        semantic_engine=_FakeRewriteEngine(),
    )
    graph.run(_request())
    assert any("Semantic-SQL mode is ON" in payload for payload in model.payloads)


def test_semantic_sql_mode_off_by_default() -> None:
    model = _CapturingModelClient(_SEMANTIC_SQL)
    graph = TextToSqlGraph(
        config=AgentConfig(),  # passthrough + flag off
        model_client=model,
        context_provider=_FakeContextProvider(),
        superset_client=_RecordingSupersetClient(),
    )
    graph.run(_request())
    assert not any("Semantic-SQL mode is ON" in payload for payload in model.payloads)


def test_memory_writeback_and_recall_round_trip() -> None:
    from superset_ai_agent.semantic_layer.memory_store import InMemoryMemory

    memory = InMemoryMemory()
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=_FakeModelClient(_SEMANTIC_SQL),
        context_provider=_FakeContextProvider(),
        superset_client=_RecordingSupersetClient(),
        memory=memory,
    )
    # First run executes and stores the confirmed pair.
    graph.run(_request())

    capturing = _CapturingModelClient(_SEMANTIC_SQL)
    graph2 = TextToSqlGraph(
        config=AgentConfig(),
        model_client=capturing,
        context_provider=_FakeContextProvider(),
        superset_client=_RecordingSupersetClient(),
        memory=memory,
    )
    graph2.run(_request())
    # The second run recalls the stored example into the prompt payload.
    assert any("top names" in payload for payload in capturing.payloads)
    assert any("recalled_examples" in payload for payload in capturing.payloads)
