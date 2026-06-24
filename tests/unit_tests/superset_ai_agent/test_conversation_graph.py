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
from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.conversation_graph import ConversationGraph
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
    ConversationSqlExecutionRequest,
    ConversationTurnRequest,
)
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
    ModelInfo,
    SqlExecutionSource,
)


class FakeModelClient:
    def __init__(self, response: dict[str, Any] | list[dict[str, Any]]):
        self.responses = response if isinstance(response, list) else [response]
        self.response_index = 0
        self.messages: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        self.messages.append(messages)
        response = self.responses[min(self.response_index, len(self.responses) - 1)]
        self.response_index += 1
        return ModelResult(content=json.dumps(response))

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]


class FakeContextProvider(ContextProvider):
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
    def __init__(
        self,
        results: list[ExecutionResult | Exception] | None = None,
    ) -> None:
        self.executed_sql: list[str] = []
        self.execution_sources: list[SqlExecutionSource | None] = []
        self.results = results or []

    def list_databases(self) -> list[DatabaseSummary]:
        return [DatabaseSummary(id=1, name="examples", backend="sqlite")]

    def list_datasets(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ) -> list[DatasetMetadata]:
        return (
            FakeContextProvider()
            .get_context(AgentQueryRequest(question="context", database_id=database_id))
            .datasets
        )

    def get_agent_context(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
    ) -> AgentContext:
        return FakeContextProvider().get_context(
            AgentQueryRequest(
                question="context",
                database_id=database_id,
                catalog_name=catalog_name,
                schema_name=schema_name,
                dataset_ids=dataset_ids or [],
            )
        )

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
        self.executed_sql.append(sql)
        self.execution_sources.append(source)
        if self.results:
            result = self.results[
                min(len(self.executed_sql) - 1, len(self.results) - 1)
            ]
            if isinstance(result, Exception):
                raise result
            return result
        return ExecutionResult(
            columns=["name", "total_births"],
            rows=[{"name": "Michael", "total_births": 2467129}],
            row_count=1,
        )


class RaisingContextProvider(ContextProvider):
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        raise RuntimeError("metadata unavailable")


def test_conversation_graph_answers_without_sql_artifact() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            {
                "response_type": "answer",
                "message": "The birth_names dataset has name and num columns.",
                "sql": "",
                "explanation": None,
            }
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="What columns are available?",
            scope=scope,
        ),
    )

    assert response.status == "ok"
    assert response.artifacts == []
    assert [message.role for message in response.conversation.messages] == [
        "user",
        "assistant",
    ]


def test_conversation_graph_generates_valid_sql_artifact() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    graph = ConversationGraph(
        config=AgentConfig(default_sql_limit=25),
        model_client=FakeModelClient(
            {
                "response_type": "sql",
                "message": "I drafted SQL for the top names.",
                "sql": (
                    "SELECT name, SUM(num) AS total_births "
                    "FROM birth_names GROUP BY name"
                ),
                "explanation": "Groups names and sums births.",
            }
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Show top names",
            scope=scope,
        ),
    )

    assert response.status == "needs_review"
    assert response.artifacts[0].validation is not None
    assert response.artifacts[0].validation.is_valid is True
    assert response.artifacts[0].sql.endswith("LIMIT 25")


def test_conversation_graph_run_stream_emits_progress_then_complete() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    graph = ConversationGraph(
        config=AgentConfig(default_sql_limit=25),
        model_client=FakeModelClient(
            {
                "response_type": "sql",
                "message": "I drafted SQL for the top names.",
                "sql": (
                    "SELECT name, SUM(num) AS total_births "
                    "FROM birth_names GROUP BY name"
                ),
                "explanation": "Groups names and sums births.",
            }
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )

    events = list(
        graph.run_stream(
            conversation_id=conversation.id,
            request=ConversationTurnRequest(
                message="Show top names",
                scope=scope,
            ),
        )
    )

    progress_events = [event for event in events if event["type"] == "progress"]
    complete_events = [event for event in events if event["type"] == "complete"]
    # Each graph node contributes at least one streamed progress step.
    assert [event["step"] for event in progress_events][:2] == [
        "load_conversation",
        "load_context",
    ]
    assert len(complete_events) == 1
    response = complete_events[0]["response"]
    assert response.status == "needs_review"
    assert response.artifacts[0].sql.endswith("LIMIT 25")
    # Each streamed progress frame carries the full typed step (Seam 1): the
    # plan_semantic_sql frame exposes the semantic/native rewrite live.
    assert all("agent_step" in event for event in progress_events)
    plan_frame = next(
        event for event in progress_events if event["step"] == "plan_semantic_sql"
    )
    assert plan_frame["agent_step"]["detail"]["kind"] == "plan_semantic_sql"
    # The terminal complete frame carries the assembled turn timeline.
    assert [step.kind for step in response.timeline][:2] == [
        "load_conversation",
        "load_context",
    ]
    # The user message and the streamed assistant turn are both persisted.
    assert [message.role for message in response.conversation.messages] == [
        "user",
        "assistant",
    ]


def test_conversation_graph_run_stream_records_cancellation_on_disconnect() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    graph = ConversationGraph(
        config=AgentConfig(default_sql_limit=25),
        model_client=FakeModelClient(
            {
                "response_type": "answer",
                "message": "Working on it.",
                "sql": "",
            }
        ),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )

    stream = graph.run_stream(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(message="Show top names", scope=scope),
    )
    # Consume the first streamed event, then simulate a client disconnect.
    next(stream)
    stream.close()

    messages = store.get(conversation.id).messages
    # The user turn is persisted and a cancellation marker is appended.
    assert messages[0].role == "user"
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "Generation cancelled."


def test_conversation_graph_executes_valid_sql_when_requested() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    superset_client = FakeSupersetClient()
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            [
                {
                    "response_type": "sql",
                    "message": "I ran the SQL.",
                    "sql": (
                        "SELECT name, SUM(num) AS total_births "
                        "FROM birth_names GROUP BY name LIMIT 10"
                    ),
                    "explanation": "Groups names and sums births.",
                },
                {
                    "outcome": "answer",
                    "message": "Michael has the highest total in the sample.",
                    "retry_feedback": None,
                },
            ]
        ),
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Run top names",
            scope=scope,
            execution_mode="read_only",
        ),
    )

    assert response.status == "ok"
    assert response.message.content == "Michael has the highest total in the sample."
    assert response.artifacts[0].execution_result is not None
    assert response.artifacts[0].execution_result.row_count == 1
    assert response.artifacts[0].answer_summary is not None
    assert response.artifacts[0].insight_cards
    assert response.artifacts[0].chart_spec is not None
    assert response.artifacts[0].data_preview is not None
    assert response.artifacts[0].recommended_followups
    assert superset_client.executed_sql == [
        "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name LIMIT 10"
    ]
    assert superset_client.execution_sources
    assert superset_client.execution_sources[0] is not None
    assert superset_client.execution_sources[0].source == "ai_agent_conversation"
    assert [event.step for event in response.trace] == [
        "load_conversation",
        "load_context",
        "load_wren_context",
        "draft_response",
        "plan_semantic_sql",
        "validate_sql",
        "execute_sql",
        "build_artifacts",
        "reflect_sql_outcome",
    ]
    # The explain-and-audit timeline mirrors the trace one-for-one and carries
    # typed details + the per-artifact copy for history re-render.
    assert [step.kind for step in response.timeline] == [
        event.step for event in response.trace
    ]
    execute_step = next(s for s in response.timeline if s.kind == "execute_sql")
    assert execute_step.detail.row_count == 1
    # The per-artifact timeline (for history re-render) covers that artifact's
    # own trace, which ends when the artifact was finalized in build_artifacts.
    artifact_kinds = [step.kind for step in response.artifacts[0].timeline]
    assert "execute_sql" in artifact_kinds
    assert artifact_kinds[-1] == "build_artifacts"


def test_conversation_graph_updates_approved_sql_artifact_in_manual_mode() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    artifact = ConversationArtifact(
        sql="SELECT name FROM birth_names",
        explanation="Returns names.",
    )
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="Show names"),
    )
    store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="I drafted SQL.",
            artifacts=[artifact],
        ),
    )
    superset_client = FakeSupersetClient()
    model_client = FakeModelClient(
        {
            "outcome": "answer",
            "message": "The approved query returned Michael.",
            "retry_feedback": None,
        }
    )
    graph = ConversationGraph(
        config=AgentConfig(default_sql_limit=25),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.execute_approved_sql(
        conversation_id=conversation.id,
        request=ConversationSqlExecutionRequest(
            scope=scope,
            execution_mode="manual",
            sql="SELECT name FROM birth_names",
            artifact_id=artifact.id,
        ),
    )

    assert response.status == "ok"
    assert response.message.content == "The approved query returned Michael."
    assert response.message.artifacts == []
    assert response.artifacts[0].id == artifact.id
    assert response.artifacts[0].execution_result is not None
    assert superset_client.execution_sources
    assert superset_client.execution_sources[0] is not None
    assert superset_client.execution_sources[0].source == "ai_agent_manual"
    assert superset_client.execution_sources[0].artifact_id == artifact.id
    assert [message.role for message in response.conversation.messages] == [
        "user",
        "assistant",
        "assistant",
    ]
    updated_artifact = response.conversation.messages[1].artifacts[0]
    assert updated_artifact.id == artifact.id
    assert updated_artifact.execution_result is not None
    assert updated_artifact.answer_summary is not None
    assert updated_artifact.insight_cards
    assert updated_artifact.chart_spec is not None
    assert updated_artifact.data_preview is not None
    assert response.conversation.messages[-1].content == (
        "The approved query returned Michael."
    )
    assert superset_client.executed_sql == ["SELECT name FROM birth_names\nLIMIT 25"]
    assert len(model_client.messages) == 1
    assert [event.step for event in response.trace] == [
        "load_conversation",
        "load_context",
        "load_wren_context",
        "approved_sql",
        "validate_sql",
        "execute_sql",
        "build_artifacts",
        "reflect_sql_outcome",
    ]


def test_conversation_graph_updates_invalid_approved_sql_without_execution() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    artifact = ConversationArtifact(
        sql="DROP TABLE birth_names",
        explanation="Invalid SQL.",
    )
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="Drop the table"),
    )
    store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="I drafted SQL.",
            artifacts=[artifact],
        ),
    )
    superset_client = FakeSupersetClient()
    model_client = FakeModelClient(
        {
            "response_type": "answer",
            "message": "This should not be used.",
            "sql": "",
            "explanation": None,
        }
    )
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.execute_approved_sql(
        conversation_id=conversation.id,
        request=ConversationSqlExecutionRequest(
            scope=scope,
            execution_mode="manual",
            sql="DROP TABLE birth_names",
            artifact_id=artifact.id,
        ),
    )

    assert response.status == "error"
    updated_artifact = response.conversation.messages[1].artifacts[0]
    assert updated_artifact.id == artifact.id
    assert updated_artifact.validation is not None
    assert updated_artifact.validation.is_valid is False
    assert updated_artifact.execution_result is None
    assert response.conversation.messages[-1].role == "assistant"
    assert response.conversation.messages[-1].content.startswith(
        "SQL validation failed before execution."
    )
    assert superset_client.executed_sql == []
    assert model_client.messages == []


def test_conversation_graph_can_take_multiple_sql_steps() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    superset_client = FakeSupersetClient()
    model_client = FakeModelClient(
        [
            {
                "response_type": "sql",
                "message": "I will inspect top names.",
                "sql": (
                    "SELECT name, SUM(num) AS total_births "
                    "FROM birth_names GROUP BY name LIMIT 5"
                ),
                "explanation": "Gets candidate top names.",
            },
            {
                "outcome": "retry",
                "message": "The first result needs detail for the top candidate.",
                "retry_feedback": (
                    "Inspect detail rows for the top candidate using a different "
                    "query."
                ),
            },
            {
                "response_type": "sql",
                "message": "I will check one candidate.",
                "sql": (
                    "SELECT name, num FROM birth_names WHERE name = 'Michael' LIMIT 5"
                ),
                "explanation": "Checks detail rows for Michael.",
            },
            {
                "outcome": "answer",
                "message": "The executed queries returned enough context.",
                "retry_feedback": None,
            },
        ]
    )
    graph = ConversationGraph(
        config=AgentConfig(max_agent_sql_iterations=2),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Find top names and inspect the winner",
            scope=scope,
            execution_mode="auto",
        ),
    )

    assert response.status == "ok"
    assert len(response.artifacts) == 2
    assert len(superset_client.executed_sql) == 2
    second_draft_payload = json.loads(
        model_client.messages[2][1].content.split("\n", 1)[1]
    )
    assert len(second_draft_payload["sql_observations"]) == 1
    assert second_draft_payload["reflection_feedback"] == (
        "Inspect detail rows for the top candidate using a different query."
    )
    final_reflection_payload = json.loads(
        model_client.messages[3][1].content.split("\n", 1)[1]
    )
    assert len(final_reflection_payload["sql_observations"]) == 2


def test_conversation_graph_skips_duplicate_sql_retry() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    superset_client = FakeSupersetClient()
    repeated_sql = (
        "SELECT name, SUM(num) AS total_births "
        "FROM birth_names GROUP BY name LIMIT 5"
    )
    model_client = FakeModelClient(
        [
            {
                "response_type": "sql",
                "message": "I will inspect top names.",
                "sql": repeated_sql,
                "explanation": "Gets candidate top names.",
            },
            {
                "outcome": "retry",
                "message": "The query needs a different attempt.",
                "retry_feedback": "Use a materially different query.",
            },
            {
                "response_type": "sql",
                "message": "I will retry.",
                "sql": repeated_sql,
                "explanation": "Repeats the same query.",
            },
            {
                "outcome": "clarify",
                "message": "I could not find a different useful query.",
                "retry_feedback": None,
            },
        ]
    )
    graph = ConversationGraph(
        config=AgentConfig(max_agent_sql_iterations=2),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Find top names and try again if needed",
            scope=scope,
            execution_mode="auto",
        ),
    )

    assert response.status == "ok"
    assert response.message.content == "I could not find a different useful query."
    assert superset_client.executed_sql == [
        "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name LIMIT 5"
    ]
    assert len(response.artifacts) == 1
    assert [event.step for event in response.trace].count("duplicate_sql") == 1
    duplicate_reflection_payload = json.loads(
        model_client.messages[3][1].content.split("\n", 1)[1]
    )
    assert duplicate_reflection_payload["sql_observations"][-1]["is_duplicate"] is True


def test_conversation_graph_retries_empty_result_with_different_sql() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    superset_client = FakeSupersetClient(
        results=[
            ExecutionResult(columns=["name"], rows=[], row_count=0),
            ExecutionResult(
                columns=["name", "total_births"],
                rows=[{"name": "Michael", "total_births": 2467129}],
                row_count=1,
            ),
        ]
    )
    graph = ConversationGraph(
        config=AgentConfig(max_agent_sql_iterations=2),
        model_client=FakeModelClient(
            [
                {
                    "response_type": "sql",
                    "message": "I will check an exact match.",
                    "sql": "SELECT name FROM birth_names WHERE name = 'Nope' LIMIT 5",
                    "explanation": "Looks for the requested name.",
                },
                {
                    "outcome": "retry",
                    "message": "The first query returned no rows.",
                    "retry_feedback": (
                        "Use a broader aggregate query against birth_names."
                    ),
                },
                {
                    "response_type": "sql",
                    "message": "I will broaden the search.",
                    "sql": (
                        "SELECT name, SUM(num) AS total_births "
                        "FROM birth_names GROUP BY name LIMIT 5"
                    ),
                    "explanation": "Uses an aggregate query.",
                },
                {
                    "outcome": "answer",
                    "message": "The broader query returned Michael.",
                    "retry_feedback": None,
                },
            ]
        ),
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Find a useful name result",
            scope=scope,
            execution_mode="auto",
        ),
    )

    assert response.status == "ok"
    assert response.message.content == "The broader query returned Michael."
    assert superset_client.executed_sql == [
        "SELECT name FROM birth_names WHERE name = 'Nope' LIMIT 5",
        "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name LIMIT 5",
    ]
    assert len(response.artifacts) == 2
    assert response.artifacts[0].execution_result is not None
    assert response.artifacts[0].execution_result.row_count == 0
    assert response.artifacts[1].execution_result is not None
    assert response.artifacts[1].execution_result.row_count == 1


def test_approved_sql_can_return_retry_artifact_in_manual_mode() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    artifact = ConversationArtifact(
        sql="SELECT name FROM birth_names WHERE name = 'Nope'",
        explanation="Looks for a specific name.",
    )
    store.append(
        conversation.id,
        ConversationMessage(role="user", content="Find this name"),
    )
    store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="I drafted SQL.",
            artifacts=[artifact],
        ),
    )
    superset_client = FakeSupersetClient(
        results=[ExecutionResult(columns=["name"], rows=[], row_count=0)]
    )
    model_client = FakeModelClient(
        [
            {
                "outcome": "retry",
                "message": "The approved query returned no rows.",
                "retry_feedback": "Try a broader query for nearby names.",
            },
            {
                "response_type": "sql",
                "message": "I drafted a broader query for review.",
                "sql": "SELECT name FROM birth_names LIMIT 5",
                "explanation": "Broadens the search.",
            },
        ]
    )
    graph = ConversationGraph(
        config=AgentConfig(default_sql_limit=25),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.execute_approved_sql(
        conversation_id=conversation.id,
        request=ConversationSqlExecutionRequest(
            scope=scope,
            execution_mode="manual",
            sql="SELECT name FROM birth_names WHERE name = 'Nope'",
            artifact_id=artifact.id,
        ),
    )

    assert response.status == "needs_review"
    assert response.message.content == "I drafted a broader query for review."
    assert len(response.message.artifacts) == 1
    assert response.message.artifacts[0].sql == "SELECT name FROM birth_names LIMIT 5"
    assert response.message.artifacts[0].execution_result is None
    assert response.artifacts[0].id == artifact.id
    assert response.artifacts[0].execution_result is not None
    assert response.artifacts[0].execution_result.row_count == 0
    assert superset_client.executed_sql == [
        "SELECT name FROM birth_names WHERE name = 'Nope'\nLIMIT 25"
    ]


def test_conversation_graph_does_not_execute_invalid_sql() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    superset_client = FakeSupersetClient()
    graph = ConversationGraph(
        config=AgentConfig(max_repair_attempts=0),
        model_client=FakeModelClient(
            {
                "response_type": "sql",
                "message": "I drafted SQL.",
                "sql": "DELETE FROM birth_names",
                "explanation": "Invalid write statement.",
            }
        ),
        context_provider=FakeContextProvider(),
        superset_client=superset_client,
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Delete names",
            scope=scope,
            execution_mode="auto",
        ),
    )

    assert response.status == "error"
    assert response.artifacts[0].validation is not None
    assert response.artifacts[0].validation.is_valid is False
    assert superset_client.executed_sql == []


def test_conversation_graph_returns_error_turn_when_context_fails() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=FakeModelClient(
            {
                "response_type": "answer",
                "message": "Unused.",
                "sql": "",
                "explanation": None,
            }
        ),
        context_provider=RaisingContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="What columns are available?",
            scope=scope,
        ),
    )

    assert response.status == "error"
    assert "metadata unavailable" in response.message.content
    assert [message.role for message in response.conversation.messages] == [
        "user",
        "assistant",
    ]


class _RewriteEngine:
    """Stand-in SemanticEngine that rewrites a model name to a physical table."""

    name = "fake"

    def is_available(self) -> bool:
        return True

    def compile(self, mdl_files):
        from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest

        return compile_manifest(mdl_files)

    def validate(self, manifest, *, deep=False, schema_index=None):
        from superset_ai_agent.semantic_layer.schemas import MdlValidationResult

        return MdlValidationResult(valid=True)

    def plan_sql(self, semantic_sql, manifest, *, dialect=None):
        from superset_ai_agent.semantic_layer.engine import PlannedSql

        return PlannedSql(
            native_sql=semantic_sql.replace("birth_names", "main.birth_names"),
            engine=self.name,
            rewritten=True,
            referenced_tables=["birth_names"],
        )


def test_conversation_graph_engine_rewrite_reaches_execution_and_audit() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    superset = FakeSupersetClient()
    graph = ConversationGraph(
        config=AgentConfig(default_sql_limit=25),
        model_client=FakeModelClient(
            {
                "response_type": "sql",
                "message": "Drafted SQL.",
                "sql": (
                    "SELECT name, SUM(num) AS total_births "
                    "FROM birth_names GROUP BY name"
                ),
                "explanation": "Groups names.",
            }
        ),
        context_provider=FakeContextProvider(),
        superset_client=superset,
        conversation_store=store,
        semantic_engine=_RewriteEngine(),
    )

    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="Show top names",
            scope=scope,
            execution_mode="auto",
        ),
    )

    # The engine-rewritten native SQL is what Superset executed.
    assert superset.executed_sql
    assert "main.birth_names" in superset.executed_sql[0]
    # The artifact audit carries engine provenance.
    artifact = response.artifacts[0]
    assert artifact.audit is not None
    assert artifact.audit.engine == "fake"
    assert "main.birth_names" in (artifact.audit.native_sql or "")
    assert "birth_names" in (artifact.audit.semantic_sql or "")
