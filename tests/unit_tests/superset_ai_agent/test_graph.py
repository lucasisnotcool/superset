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
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.graph import dry_plan_diagnostics, TextToSqlGraph
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.integrations.wren.llm_client import LlmWrenClient
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    ExecutionResult,
    SqlExecutionSource,
    SqlValidation,
    WrenContextArtifact,
)
from superset_ai_agent.semantic_layer.instructions import InMemoryInstructionStore
from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
from superset_ai_agent.semantic_layer.projects import InMemorySemanticProjectStore
from superset_ai_agent.semantic_layer.schemas import (
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
    SemanticProject,
    SemanticProjectResolveRequest,
)
from superset_ai_agent.semantic_layer.store import (
    instruction_scope_hash,
    scope_hash,
)


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
        "plan_semantic_sql",
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
    assert (
        response.trace[1].summary
        == "Wren context requires a selected project or schema."
    )


def test_graph_infers_schema_from_pinned_project(tmp_path) -> None:
    # The AI SQL dropdown bug: a pinned project arrives with project_id but no
    # schema_name (the SQL Lab tab has none). The project's schema is inferred so
    # Wren context loads instead of blocking on "select a schema".
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
            path="models/birth_names.json",
            content=json.dumps({"models": [{"name": "birth_names"}]}),
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
            # No schema_name — only the project pin, as the dropdown sends it.
            project_id=project.id,
        ),
        owner_id="analyst",
    )

    assert response.wren_context is not None
    assert response.wren_context.available is True
    assert response.wren_context.project_id == project.id
    assert response.wren_context.materialized_file_count == 1


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
            path="models/birth_names.json",
            content=json.dumps({"models": [{"name": "birth_names"}]}),
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
            path="models/birth_names.json",
            content=json.dumps(
                {
                    "models": [
                        {
                            "name": "birth_names",
                            "properties": {
                                "description": "Annual baby name registrations"
                            },
                        }
                    ]
                }
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
        message.content for messages in model_client.messages for message in messages
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
        "plan_semantic_sql",
        "validate_sql",
        "execute_sql",
        "build_artifacts",
    ]
    # The one-shot response carries the explain-and-audit timeline mirroring the
    # trace, with the executed row count surfaced as a typed step detail.
    assert [step.kind for step in response.timeline] == [
        event.step for event in response.trace
    ]
    execute_step = next(s for s in response.timeline if s.kind == "execute_sql")
    assert execute_step.detail.row_count == 1


def test_instruction_scope_hash_ignores_dataset_selection() -> None:
    # C5.1 fix: instructions are schema-scoped; the editor authors with no datasets,
    # a chat query carries selected datasets — both must hash equal. Memory's
    # scope_hash, by contrast, stays dataset-sensitive.
    schema = ConversationScope(database_id=1, schema_name="main", dataset_ids=[])
    query = ConversationScope(database_id=1, schema_name="main", dataset_ids=[16, 3])
    assert instruction_scope_hash(schema) == instruction_scope_hash(query)
    other = ConversationScope(database_id=1, schema_name="other", dataset_ids=[])
    assert instruction_scope_hash(query) != instruction_scope_hash(other)
    assert scope_hash(schema) != scope_hash(query)  # memory still dataset-scoped


def test_graph_injects_instructions_into_sql_prompt() -> None:
    # R3 instructions + C5.1 fix: an instruction authored at SCHEMA scope (as the
    # editor does — no dataset_ids) is recalled into the SQL prompt even when the
    # chat query selects datasets. Previously the differing scope hashes hid it.
    instruction_store = InMemoryInstructionStore()
    authored_scope = ConversationScope(
        database_id=1, schema_name="main", dataset_ids=[]
    )
    instruction_store.add(
        instruction="ALWAYS exclude test accounts",
        scope_hash=instruction_scope_hash(authored_scope),
        owner_id=DEFAULT_OWNER_ID,
        is_global=True,
    )
    model = FakeModelClient("SELECT name FROM birth_names")
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        instruction_store=instruction_store,
    )

    graph.run(
        AgentQueryRequest(
            question="top names",
            database_id=1,
            schema_name="main",
            dataset_ids=[16],
            execute=False,
        )
    )

    user_messages = [
        message.content
        for messages in model.messages
        for message in messages
        if message.role == "user"
    ]
    assert any("ALWAYS exclude test accounts" in content for content in user_messages)


# --- C2.2: Wren dry-plan diagnostics feed the repair prompt --------------------


def test_dry_plan_diagnostics_extracts_error_and_errors() -> None:
    assert dry_plan_diagnostics({"error": "table foo missing"}) == ["table foo missing"]
    assert dry_plan_diagnostics({"errors": ["bad column a", "bad column b"]}) == [
        "bad column a",
        "bad column b",
    ]
    # error + errors combine, with dedup and order preserved.
    assert dry_plan_diagnostics({"error": "dup", "errors": ["dup", "other"]}) == [
        "dup",
        "other",
    ]


def test_dry_plan_diagnostics_degrades_for_clean_or_missing_plan() -> None:
    assert dry_plan_diagnostics(None) == []
    assert dry_plan_diagnostics({"available": True, "planning_only": True}) == []
    assert dry_plan_diagnostics({"error": "  "}) == []  # blank ignored
    assert dry_plan_diagnostics("not a dict") == []  # type: ignore[arg-type]


def test_repair_sql_folds_dry_plan_diagnostics_into_prompt() -> None:
    model = FakeModelClient("SELECT 1")
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
    )
    request = AgentQueryRequest(question="q", database_id=1, schema_name="main")
    state = {
        "request": request,
        "context": FakeContextProvider().get_context(request),
        "validation": SqlValidation(
            is_valid=False, is_read_only=True, errors=["syntax error near FROM"]
        ),
        "wren_context": WrenContextArtifact(
            enabled=True,
            dry_plan={"available": False, "error": "table foo not found in MDL"},
        ),
        "repair_attempts": 0,
    }

    graph._repair_sql(state)

    sent = model.messages[-1][-1].content
    # Both the validator's syntax error and the engine's dry-plan diagnostic reach
    # the repair prompt (C2.2).
    assert "syntax error near FROM" in sent
    assert "table foo not found in MDL" in sent


# --- C1.3: LLM table/column selection -----------------------------------------


class _SelectorModelClient:
    """Model client returning a fixed payload for the table-selection call."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def chat(self, messages, *, model=None, format_schema=None):
        self.calls += 1
        return ModelResult(content=self.content)


def test_llm_select_models_returns_validated_subset() -> None:
    from superset_ai_agent.graph import llm_select_models

    model = _SelectorModelClient(json.dumps({"models": ["beta", "ghost"]}))
    chosen = llm_select_models(model, "q", ["alpha", "beta", "gamma"], 5)
    # "ghost" is not a candidate → dropped; "beta" kept.
    assert chosen == ["beta"]


def test_llm_select_models_caps_to_limit_in_rank_order() -> None:
    from superset_ai_agent.graph import llm_select_models

    model = _SelectorModelClient(json.dumps({"models": ["gamma", "alpha", "beta"]}))
    chosen = llm_select_models(model, "q", ["alpha", "beta", "gamma"], 2)
    # Capped to 2, preserving candidate (retriever-rank) order.
    assert chosen == ["alpha", "beta"]


def test_llm_select_models_bad_json_returns_none() -> None:
    from superset_ai_agent.graph import llm_select_models

    model = _SelectorModelClient("not json at all")
    assert llm_select_models(model, "q", ["alpha"], 5) is None


def test_llm_select_models_empty_candidates_skips_call() -> None:
    from superset_ai_agent.graph import llm_select_models

    model = _SelectorModelClient(json.dumps({"models": ["alpha"]}))
    assert llm_select_models(model, "q", [], 5) is None
    assert model.calls == 0  # no model call when there is nothing to select


def test_model_selector_is_none_when_flag_off() -> None:
    graph = TextToSqlGraph(
        config=AgentConfig(wren_llm_table_selection=False),
        model_client=FakeModelClient("SELECT 1"),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
    )
    assert graph._model_selector("any question") is None


def test_model_selector_built_when_flag_on() -> None:
    graph = TextToSqlGraph(
        config=AgentConfig(wren_llm_table_selection=True),
        model_client=_SelectorModelClient(json.dumps({"models": ["alpha"]})),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
    )
    selector = graph._model_selector("any question")
    assert selector is not None
    assert selector(["alpha", "beta"]) == ["alpha"]


# --- R1: recall access set spans the project's full schema set ----------------


class _RecallDatasetClient(FakeSupersetClient):
    """Per-user access-filtered dataset listing keyed by schema."""

    def __init__(self, by_schema: dict[str, list[str]]) -> None:
        super().__init__()
        self.by_schema = by_schema
        self.list_calls: list[str | None] = []

    def list_datasets(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ):
        self.list_calls.append(schema_name)

        class _DS:
            def __init__(self, schema: str | None, table: str) -> None:
                self.schema_name = schema
                self.table_name = table

        return [_DS(schema_name, t) for t in self.by_schema.get(schema_name or "", [])]


def _project(schema_names: list[str]) -> SemanticProject:
    return SemanticProject(
        name="p",
        owner_id="local",
        database_uri_fingerprint="fp",
        schema_name=schema_names[0],
        schema_names=schema_names,
        default_database_id=1,
    )


def test_recall_access_spans_all_project_schemas() -> None:
    # R1: the access set must union every project schema (not the request's primary
    # schema), so a cross-schema golden/memory pair can pass the Stage-A filter.
    client = _RecallDatasetClient(
        {"core": ["lines", "skus"], "ops": ["events", "work_orders"]}
    )
    graph = TextToSqlGraph(
        config=AgentConfig(wren_memory_store="lancedb"),
        model_client=FakeModelClient("SELECT 1"),
        context_provider=FakeContextProvider(),
        superset_client=client,
    )
    request = AgentQueryRequest(question="x", database_id=1, schema_name="core")
    access = graph._recall_access(request, _project(["core", "ops"]))
    assert access is not None
    assert access.accessible_tables == frozenset(
        {"core.lines", "core.skus", "ops.events", "ops.work_orders"}
    )
    assert client.list_calls == ["core", "ops"]


def test_recall_access_is_none_and_skips_loads_when_inert() -> None:
    # No project + learning off (default store="none") -> inert -> no scan, None.
    client = _RecallDatasetClient({})
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=FakeModelClient("SELECT 1"),
        context_provider=FakeContextProvider(),
        superset_client=client,
    )
    request = AgentQueryRequest(question="x", database_id=1, schema_name="core")
    assert graph._recall_access(request, None) is None
    assert client.list_calls == []
