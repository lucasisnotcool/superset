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

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.superset_metadata import SupersetMetadataContextProvider
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.schemas import AgentQueryRequest, ExecutionResult


class FakeSupersetClient:
    def __init__(self) -> None:
        self.list_limits: list[int] = []

    def get_agent_context(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
    ) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(id=database_id, name="warehouse"),
            datasets=[],
        )

    def list_datasets(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ) -> list[DatasetMetadata]:
        self.list_limits.append(limit)
        return [
            DatasetMetadata(
                id=1,
                table_name="customers",
                schema_name=schema_name,
                database_id=database_id,
                columns=[],
                metrics=[],
            ),
            DatasetMetadata(
                id=2,
                table_name="pipeline_moves",
                schema_name=schema_name,
                database_id=database_id,
                columns=[],
                metrics=[],
            ),
        ]

    def list_databases(self) -> list[DatabaseSummary]:
        return []

    def get_database_dialect(self, database_id: int) -> str | None:
        return None

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        limit: int = 1000,
    ) -> ExecutionResult:
        return ExecutionResult(columns=[], rows=[], row_count=0)


def test_context_provider_scans_and_ranks_schema_metadata() -> None:
    client = FakeSupersetClient()
    provider = SupersetMetadataContextProvider(
        client,
        config=AgentConfig(
            max_context_datasets=3,
            wren_schema_table_scan_limit=50,
            wren_schema_table_candidate_limit=1,
        ),
    )

    context = provider.get_context(
        AgentQueryRequest(
            question="Show gross moves",
            database_id=1,
            catalog_name="prod",
            schema_name="sales",
        )
    )

    assert client.list_limits == [50]
    assert [dataset.table_name for dataset in context.datasets] == ["pipeline_moves"]
    assert provider.last_retrieval is not None
    assert provider.last_retrieval.retrieval.scanned_table_count == 2
    assert provider.last_retrieval.retrieval.omitted_table_count == 1


def test_context_provider_keeps_explicit_dataset_request() -> None:
    client = FakeSupersetClient()
    provider = SupersetMetadataContextProvider(client)

    provider.get_context(
        AgentQueryRequest(
            question="Show gross moves",
            database_id=1,
            schema_name="sales",
            dataset_ids=[2],
        )
    )

    assert client.list_limits == []
    assert provider.last_retrieval is None
