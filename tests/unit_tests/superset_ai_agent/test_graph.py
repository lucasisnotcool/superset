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
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.wren.llm_client import LlmWrenClient
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    ExecutionResult,
    SqlExecutionSource,
    WrenContextArtifact,
)
from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.projects import InMemorySemanticProjectStore
from superset_ai_agent.semantic_layer.schemas import (
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
    SemanticLayerVersion,
    SemanticProjectResolveRequest,
)
from superset_ai_agent.semantic_layer.store import scope_hash


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
    def __init__(self) -> None:
        self.execution_sources: list[SqlExecutionSource | None] = []

    def get_database_dialect(self, database_id: int) -> str:
        return "sqlite"

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        limit: int = 1000,
        source: SqlExecutionSource | None = None,
    ) -> ExecutionResult:
        self.execution_sources.append(source)
        return ExecutionResult(
            columns=["name", "total_births"],
            rows=[{"name": "Michael", "total_births": 2467129}],
            row_count=1,
        )


class FakeWrenClient:
    def __init__(self) -> None:
        self.mdl_paths: list[str | None] = []

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl_path: str | None = None,
    ) -> WrenContextArtifact:
        self.mdl_paths.append(mdl_path)
        return WrenContextArtifact(
            enabled=True,
            available=True,
            matched_models=["birth_names"],
            example_ids=["example-1"],
        )

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
        mdl_path: str | None = None,
    ) -> dict:
        return {
            "available": True,
            "planning_only": True,
            "matched_models": ["birth_names"],
            "sql_hash": "test",
        }

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["birth_names"]

    def recall_examples(self, *, question: str, limit: int) -> list[dict]:
        return [{"id": "example-1"}]


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
            schema_name="main",
            dataset_ids=[16],
            execute=False,
        )
    )

    assert response.status == "needs_review"
    assert response.execution_result is None
    assert response.validation.is_valid is True
    assert response.sql is not None
    assert response.sql.endswith("LIMIT 1000")


def test_graph_records_wren_context_and_dry_plan() -> None:
    wren_client = FakeWrenClient()
    graph = TextToSqlGraph(
        config=AgentConfig(wren_dry_plan_enabled=True),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=wren_client,
    )

    response = graph.run(
        AgentQueryRequest(
            question="top names",
            database_id=1,
            schema_name="main",
            dataset_ids=[16],
            execute=False,
        )
    )

    assert response.wren_context is not None
    assert response.wren_context.available is True
    assert response.wren_context.dry_plan == {
        "available": True,
        "planning_only": True,
        "matched_models": ["birth_names"],
        "sql_hash": "test",
    }
    assert wren_client.mdl_paths == [None]
    assert [event.step for event in response.trace] == [
        "load_context",
        "load_wren_context",
        "draft_sql",
        "dry_plan_with_wren",
        "validate_sql",
    ]


def test_graph_skips_wren_context_without_schema() -> None:
    wren_client = FakeWrenClient()
    graph = TextToSqlGraph(
        config=AgentConfig(wren_dry_plan_enabled=True),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=wren_client,
    )

    response = graph.run(
        AgentQueryRequest(
            question="top names",
            database_id=1,
            dataset_ids=[16],
            execute=False,
        )
    )

    assert response.wren_context is not None
    assert response.wren_context.available is False
    assert response.wren_context.dry_plan is None
    assert wren_client.mdl_paths == []
    assert response.trace[1].summary == "Wren context requires a selected schema."


def test_graph_materializes_schema_project_for_wren_context(tmp_path) -> None:
    project_store = InMemorySemanticProjectStore()
    mdl_store = InMemoryMdlFileStore()
    project = project_store.resolve(
        SemanticProjectResolveRequest(
            database_id=1,
            database_label="Examples",
            schema_name="main",
        ),
        owner_id="analyst",
    )
    file = mdl_store.create(
        project.id,
        MdlFileCreateRequest(
            path="models/birth_names.yaml",
            content="models:\n  - name: birth_names\n",
        ),
        owner_id="analyst",
    )
    mdl_store.update(
        file.id,
        MdlFileUpdateRequest(status="active"),
        owner_id="analyst",
    )
    wren_client = FakeWrenClient()
    graph = TextToSqlGraph(
        config=AgentConfig(agent_storage_dir=str(tmp_path)),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=wren_client,
        semantic_project_store=project_store,
        mdl_file_store=mdl_store,
    )

    response = graph.run(
        AgentQueryRequest(
            question="Show total births",
            database_id=1,
            schema_name="main",
        ),
        owner_id="analyst",
    )

    assert response.wren_context is not None
    assert response.wren_context.project_id == project.id
    assert response.wren_context.materialized_file_count == 1
    assert response.wren_context.mdl_path is not None
    assert response.wren_context.mdl_path.endswith("mdl.json")
    assert wren_client.mdl_paths == [response.wren_context.mdl_path]


def test_graph_merges_indexed_semantic_context() -> None:
    semantic_layer_store = InMemorySemanticLayerStore()
    scope = ConversationScope(database_id=1, schema_name="main")
    semantic_layer_store.save_version(
        SemanticLayerVersion(
            scope=scope,
            scope_hash=scope_hash(scope),
            version="v1",
            status="idle",
            wren_context=WrenContextArtifact(
                enabled=True,
                available=True,
                document_ids=["doc-1"],
                semantic_layer_version="v1",
                indexing_status="indexed",
                context_items=[{"kind": "document", "name": "terms"}],
                warnings=["Indexed semantic context loaded."],
            ),
        ),
        owner_id="analyst",
    )
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=FakeWrenClient(),
        semantic_layer_store=semantic_layer_store,
    )

    response = graph.run(
        AgentQueryRequest(
            question="Show total births",
            database_id=1,
            schema_name="main",
        ),
        owner_id="analyst",
    )

    assert response.wren_context is not None
    assert response.wren_context.semantic_layer_version == "v1"
    assert response.wren_context.indexing_status == "indexed"
    assert response.wren_context.document_ids == ["doc-1"]
    assert response.wren_context.context_items == [
        {"kind": "document", "name": "terms"}
    ]


def test_graph_injects_materialized_mdl_into_sql_prompt(tmp_path) -> None:
    """Touchpoint 3: active MDL semantics reach the SQL prompt at query time."""

    project_store = InMemorySemanticProjectStore()
    mdl_store = InMemoryMdlFileStore()
    project = project_store.resolve(
        SemanticProjectResolveRequest(
            database_id=1,
            database_label="Examples",
            schema_name="main",
        ),
        owner_id="analyst",
    )
    file = mdl_store.create(
        project.id,
        MdlFileCreateRequest(
            path="models/birth_names.yaml",
            content=(
                "models:\n"
                "  - name: birth_names\n"
                "    description: Annual baby name registrations and totals\n"
            ),
        ),
        owner_id="analyst",
    )
    mdl_store.update(
        file.id,
        MdlFileUpdateRequest(status="active"),
        owner_id="analyst",
    )
    model_client = FakeModelClient(
        "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name"
    )
    wren_client = LlmWrenClient(
        AgentConfig(wren_adapter="llm"),
        model_client,
        mdl_file_store=mdl_store,
    )
    graph = TextToSqlGraph(
        config=AgentConfig(agent_storage_dir=str(tmp_path), wren_adapter="llm"),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=wren_client,
        semantic_project_store=project_store,
        mdl_file_store=mdl_store,
    )

    response = graph.run(
        AgentQueryRequest(
            question="Show total births by name",
            database_id=1,
            schema_name="main",
        ),
        owner_id="analyst",
    )

    assert response.wren_context is not None
    assert response.wren_context.available is True
    # The model/column descriptions must reach the prompt the model received.
    prompt_text = "\n".join(
        message.content
        for messages in model_client.messages
        for message in messages
    )
    assert "Annual baby name registrations" in prompt_text


def test_graph_executes_valid_sql_when_requested() -> None:
    superset_client = FakeSupersetClient()
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            "SELECT name, SUM(num) AS total_births "
            "FROM birth_names GROUP BY name LIMIT 10"
        ),
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
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
    assert response.answer_summary is not None
    assert response.insight_cards
    assert response.chart_spec is not None
    assert response.chart_spec.type == "bar"
    assert response.data_preview is not None
    assert response.recommended_followups
    assert superset_client.execution_sources
    assert superset_client.execution_sources[0] is not None
    assert superset_client.execution_sources[0].source == "ai_agent"
    assert [event.step for event in response.trace] == [
        "load_context",
        "load_wren_context",
        "draft_sql",
        "validate_sql",
        "execute_sql",
        "build_artifacts",
    ]
