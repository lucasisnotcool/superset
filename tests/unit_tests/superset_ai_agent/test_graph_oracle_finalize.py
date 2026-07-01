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

"""End-to-end: Oracle semantic query is finalized before it reaches execution."""

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

_SEMANTIC_SQL = "SELECT id FROM orders LIMIT 10"


class _FakeModelClient:
    def chat(self, messages, *, model=None, format_schema=None) -> ModelResult:
        return ModelResult(
            content=json.dumps({"sql": _SEMANTIC_SQL, "explanation": "t"})
        )


class _OracleContextProvider:
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(
                id=request.database_id, name="ora", backend="oracle"
            ),
            datasets=[
                DatasetMetadata(
                    id=16,
                    table_name="orders",
                    database_id=request.database_id,
                    columns=[ColumnSummary(name="id", type="NUMBER")],
                    metrics=[],
                )
            ],
        )


class _RecordingSupersetClient:
    def __init__(self) -> None:
        self.executed_sql: list[str] = []

    def get_database_dialect(self, database_id: int) -> str:
        return "oracle"

    def execute_sql(self, *, database_id, sql, catalog_name=None, schema_name=None,
                    limit=1000, source: SqlExecutionSource | None = None):
        self.executed_sql.append(sql)
        return ExecutionResult(columns=["ID"], rows=[{"ID": 1}], row_count=1)


class _LowercaseLimitEngine:
    """Emits wren-core-style output: lowercase-quoted identifiers + LIMIT."""

    name = "wren_core"

    def is_available(self) -> bool:
        return True

    def compile(self, mdl_files):
        return compile_manifest(mdl_files)

    def validate(self, manifest, *, deep=False, schema_index=None):
        return MdlValidationResult(valid=True)

    def plan_sql(self, semantic_sql, manifest, *, dialect=None) -> PlannedSql:
        return PlannedSql(
            native_sql='SELECT o."id" FROM "orders" AS o LIMIT 10',
            engine=self.name,
            rewritten=True,
            referenced_tables=["orders"],
        )


def _request() -> AgentQueryRequest:
    return AgentQueryRequest(
        question="orders",
        database_id=1,
        schema_name="APP",
        dataset_ids=[16],
        execute=True,
    )


def test_oracle_query_is_finalized_before_execution() -> None:
    superset = _RecordingSupersetClient()
    graph = TextToSqlGraph(
        config=AgentConfig(wren_semantic_sql_enabled=True),
        model_client=_FakeModelClient(),
        context_provider=_OracleContextProvider(),
        superset_client=superset,
        semantic_engine=_LowercaseLimitEngine(),
    )

    response = graph.run(_request())

    assert response.status == "ok", response.error
    assert superset.executed_sql, "expected an execution"
    executed = superset.executed_sql[0]
    # LIMIT rewritten to Oracle row-limiting; identifiers uppercased to match
    # Oracle's stored case (the ORA-00904 fix); no bare LIMIT survives.
    assert "FETCH FIRST 10 ROWS ONLY" in executed
    assert '"ID"' in executed
    assert '"id"' not in executed
    assert "LIMIT" not in executed.upper()
    # Provenance: the executed native SQL is stamped on the audit.
    assert response.audit is not None
    assert response.audit.engine == "wren_core"


def test_oracle_finalization_kill_switch_leaves_limit() -> None:
    superset = _RecordingSupersetClient()
    graph = TextToSqlGraph(
        config=AgentConfig(
            wren_semantic_sql_enabled=True,
            wren_dialect_finalize_enabled=False,
        ),
        model_client=_FakeModelClient(),
        context_provider=_OracleContextProvider(),
        superset_client=superset,
        semantic_engine=_LowercaseLimitEngine(),
    )

    response = graph.run(_request())

    assert response.status == "ok", response.error
    # With finalization off, the un-transpiled LIMIT reaches execution unchanged.
    assert "LIMIT 10" in superset.executed_sql[0]
