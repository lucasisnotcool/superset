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

import json  # noqa: TID251 - tests cover the standalone agent JSON contract

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import AgentQueryRequest, ExecutionResult


class FakeModelClient:
    def __init__(self, sql: str):
        self.sql = sql
        self.messages: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict | None = None,
    ) -> ModelResult:
        self.messages.append(messages)
        return ModelResult(
            content=json.dumps(
                {
                    "sql": self.sql,
                    "explanation": "Generated from test metadata.",
                }
            )
        )


class FakeContextProvider:
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(
                id=request.database_id,
                name="examples",
                backend="sqlite",
            ),
            datasets=[
                DatasetMetadata(
                    id=16,
                    table_name="birth_names",
                    database_id=request.database_id,
                    columns=[
                        ColumnSummary(name="name", type="VARCHAR"),
                        ColumnSummary(name="num", type="BIGINT"),
                    ],
                    metrics=[
                        MetricSummary(name="count", expression="COUNT(*)"),
                    ],
                )
            ],
        )


class FakeSupersetClient:
    def get_database_dialect(self, database_id: int) -> str:
        return "sqlite"

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        schema_name: str | None = None,
        limit: int = 1000,
    ) -> ExecutionResult:
        return ExecutionResult(
            columns=["name", "total_births"],
            rows=[{"name": "Michael", "total_births": 2467129}],
            row_count=1,
        )


def test_graph_generates_valid_sql_without_execution() -> None:
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
    )

    response = graph.run(
        AgentQueryRequest(
            question="top names",
            database_id=1,
            dataset_ids=[16],
            execute=False,
        )
    )

    assert response.status == "needs_review"
    assert response.execution_result is None
    assert response.validation.is_valid is True
    assert response.sql is not None
    assert response.sql.endswith("LIMIT 1000")


def test_graph_executes_valid_sql_when_requested() -> None:
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births "
            "FROM birth_names GROUP BY name LIMIT 10"
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
    )

    response = graph.run(
        AgentQueryRequest(
            question="top names",
            database_id=1,
            dataset_ids=[16],
            execute=True,
        )
    )

    assert response.status == "ok"
    assert response.execution_result is not None
    assert response.execution_result.row_count == 1
    assert [event.step for event in response.trace] == [
        "load_context",
        "draft_sql",
        "validate_sql",
        "execute_sql",
    ]
