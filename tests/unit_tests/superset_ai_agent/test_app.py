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

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from superset_ai_agent.app import create_app
from superset_ai_agent.auth import AgentIdentity, sign_identity_payload
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationMessage,
    ConversationSqlExecutionRequest,
    ConversationTurnRequest,
    ConversationTurnResponse,
)
from superset_ai_agent.integrations.superset.client import SupersetAuthError
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    ModelInfo,
    SqlValidation,
)


class FakeOllamaClient:
    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="qwen2.5-coder:7b")]


class RaisingGraph:
    def run(
        self,
        request: AgentQueryRequest,
        *,
        owner_id: str = "default",
    ) -> AgentQueryResponse:
        _ = owner_id
        raise RuntimeError("ollama unavailable")


class AuthRaisingGraph:
    def run(
        self,
        request: AgentQueryRequest,
        *,
        owner_id: str = "default",
    ) -> AgentQueryResponse:
        _ = owner_id
        raise SupersetAuthError("Superset session expired.", status_code=401)


class StaticGraph:
    def run(
        self,
        request: AgentQueryRequest,
        *,
        owner_id: str = "default",
    ) -> AgentQueryResponse:
        _ = owner_id
        return AgentQueryResponse(
            status="needs_review",
            sql="select 1",
            explanation="Returns one row.",
            validation=SqlValidation(
                is_valid=True,
                is_read_only=True,
                normalized_sql="select 1",
            ),
        )


class StaticConversationGraph:
    def __init__(self, store: InMemoryConversationStore):
        self.store = store
        self.requests: list[ConversationTurnRequest] = []
        self.sql_execution_requests: list[ConversationSqlExecutionRequest] = []

    def run(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str = "local",
    ) -> ConversationTurnResponse:
        self.requests.append(request)
        message = ConversationMessage(
            role="assistant",
            content=f"Answered: {request.message}",
        )
        conversation = self.store.append(
            conversation_id,
            ConversationMessage(role="user", content=request.message),
            owner_id=owner_id,
        )
        conversation = self.store.append(
            conversation_id,
            message,
            owner_id=owner_id,
        )
        return ConversationTurnResponse(
            status="ok",
            conversation_id=conversation_id,
            message=message,
            conversation=conversation,
        )

    def run_stream(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str = "local",
    ):
        self.requests.append(request)
        yield {
            "type": "progress",
            "step": "draft_response",
            "status": "ok",
            "summary": "Generated a SQL draft.",
        }
        message = ConversationMessage(
            role="assistant",
            content=f"Answered: {request.message}",
        )
        self.store.append(
            conversation_id,
            ConversationMessage(role="user", content=request.message),
            owner_id=owner_id,
        )
        conversation = self.store.append(
            conversation_id,
            message,
            owner_id=owner_id,
        )
        yield {
            "type": "complete",
            "response": ConversationTurnResponse(
                status="ok",
                conversation_id=conversation_id,
                message=message,
                conversation=conversation,
            ),
        }

    def execute_approved_sql_stream(
        self,
        *,
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        owner_id: str = "local",
    ):
        self.sql_execution_requests.append(request)
        yield {
            "type": "progress",
            "step": "execute_sql",
            "status": "ok",
            "summary": "Executed SQL and returned 1 row(s).",
        }
        message = ConversationMessage(
            role="assistant",
            content=f"Executed: {request.sql}",
        )
        conversation = self.store.append(
            conversation_id,
            message,
            owner_id=owner_id,
        )
        yield {
            "type": "complete",
            "response": ConversationTurnResponse(
                status="ok",
                conversation_id=conversation_id,
                message=message,
                conversation=conversation,
            ),
        }

    def execute_approved_sql(
        self,
        *,
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        owner_id: str = "local",
    ) -> ConversationTurnResponse:
        self.sql_execution_requests.append(request)
        message = ConversationMessage(
            role="assistant",
            content=f"Executed: {request.sql}",
        )
        conversation = self.store.append(
            conversation_id,
            message,
            owner_id=owner_id,
        )
        return ConversationTurnResponse(
            status="ok",
            conversation_id=conversation_id,
            message=message,
            conversation=conversation,
        )


class AuthRaisingConversationGraph:
    def run(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str = "local",
    ) -> ConversationTurnResponse:
        raise SupersetAuthError("Superset session expired.", status_code=401)

    def execute_approved_sql(
        self,
        *,
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        owner_id: str = "local",
    ) -> ConversationTurnResponse:
        raise SupersetAuthError("Superset session expired.", status_code=401)


class HeaderIdentityProvider:
    def get_identity(self, request: Request) -> AgentIdentity:
        owner_id = request.headers.get("x-test-superset-user", "1")
        return AgentIdentity(
            owner_id=f"superset:{owner_id}",
            username=owner_id,
            source="superset_session",
        )


def _local_config(**overrides) -> AgentConfig:
    defaults = {
        "identity_provider": "static",
        "superset_auth_mode": "service_account",
        # Keep these app tests lightweight/in-memory; persistence + the wren
        # engine have their own dedicated tests.
        "conversation_store": "memory",
        "semantic_layer_store": "memory",
        "wren_engine": "passthrough",
        "wren_core_validation_enabled": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _create_test_app(store: InMemoryConversationStore | None = None) -> FastAPI:
    active_store = store or InMemoryConversationStore()
    return create_app(
        config=_local_config(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=StaticConversationGraph(active_store),
        conversation_store=active_store,
    )


def _signed_identity_header(owner_id: str) -> dict[str, str]:
    return {
        "x-agent-identity": sign_identity_payload(
            {"owner_id": owner_id, "username": owner_id},
            secret="secret",
        )
    }


def test_health_and_models_use_injected_ollama_client() -> None:
    app = _create_test_app()
    client = TestClient(app)

    health = client.get("/health").json()
    assert health["reachable"] is True
    assert health["model_provider"] == "ollama"
    assert health["ollama_reachable"] is True
    # Test config uses semantic_layer_store="memory" → flagged non-persistent.
    assert health["semantic_layer_persistent"] is False
    assert client.get("/models").json()[0]["name"] == "qwen2.5-coder:7b"


def test_agent_query_returns_error_payload_when_graph_fails() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=_local_config(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=RaisingGraph(),
        conversation_graph=StaticConversationGraph(store),
        conversation_store=store,
    )
    client = TestClient(app)

    response = client.post(
        "/agent/query",
        json={
            "question": "show sales",
            "database_id": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["trace"][0]["step"] == "agent_error"
    assert "ollama unavailable" in body["trace"][0]["summary"]


def test_agent_query_returns_auth_status_when_superset_session_fails() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=_local_config(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=AuthRaisingGraph(),
        conversation_graph=StaticConversationGraph(store),
        conversation_store=store,
    )
    client = TestClient(app)

    response = client.post(
        "/agent/query",
        json={
            "question": "show sales",
            "database_id": 1,
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Superset session expired."


def test_validate_sql_endpoint_adds_limit() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=_local_config(default_sql_limit=25),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=StaticConversationGraph(store),
        conversation_store=store,
    )
    client = TestClient(app)

    response = client.post(
        "/agent/validate-sql",
        json={"sql": "select * from birth_names", "dialect": "sqlite"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is True
    assert body["normalized_sql"] == "select * from birth_names\nLIMIT 25"


def test_conversation_endpoints_create_list_message_and_delete() -> None:
    app = _create_test_app()
    client = TestClient(app)

    create_response = client.post(
        "/agent/conversations",
        json={
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
        },
    )

    assert create_response.status_code == 200
    conversation_id = create_response.json()["id"]
    assert client.get("/agent/conversations").json()[0]["id"] == conversation_id

    message_response = client.post(
        f"/agent/conversations/{conversation_id}/messages",
        json={
            "message": "What columns are available?",
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
            "execution_mode": "manual",
        },
    )

    assert message_response.status_code == 200
    body = message_response.json()
    assert body["status"] == "ok"
    assert [message["role"] for message in body["conversation"]["messages"]] == [
        "user",
        "assistant",
    ]

    delete_response = client.delete(f"/agent/conversations/{conversation_id}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}
    assert client.get(f"/agent/conversations/{conversation_id}").status_code == 404


def test_stream_conversation_message_emits_progress_then_complete() -> None:
    app = _create_test_app()
    client = TestClient(app)

    conversation_id = client.post(
        "/agent/conversations",
        json={"scope": {"database_id": 1, "schema_name": None, "dataset_ids": []}},
    ).json()["id"]

    response = client.post(
        f"/agent/conversations/{conversation_id}/messages/stream",
        json={
            "message": "What columns are available?",
            "scope": {"database_id": 1, "schema_name": None, "dataset_ids": []},
            "execution_mode": "manual",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = [
        json.loads(line[len("data:") :].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    types = [event["type"] for event in events]
    assert types[0] == "progress"
    assert types[-1] == "complete"
    assert events[0]["summary"] == "Generated a SQL draft."
    complete = events[-1]["response"]
    assert complete["status"] == "ok"
    assert [message["role"] for message in complete["conversation"]["messages"]] == [
        "user",
        "assistant",
    ]


def test_stream_conversation_message_returns_404_for_missing_conversation() -> None:
    app = _create_test_app()
    client = TestClient(app)

    response = client.post(
        "/agent/conversations/does-not-exist/messages/stream",
        json={
            "message": "hello",
            "scope": {"database_id": 1, "schema_name": None, "dataset_ids": []},
            "execution_mode": "manual",
        },
    )

    assert response.status_code == 404


def test_rename_conversation_updates_title() -> None:
    app = _create_test_app()
    client = TestClient(app)

    conversation_id = client.post(
        "/agent/conversations",
        json={"scope": {"database_id": 1, "schema_name": None, "dataset_ids": []}},
    ).json()["id"]

    response = client.patch(
        f"/agent/conversations/{conversation_id}",
        json={"title": "Revenue by region"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Revenue by region"
    assert (
        client.get(f"/agent/conversations/{conversation_id}").json()["title"]
        == "Revenue by region"
    )


def test_rename_conversation_returns_404_for_missing_conversation() -> None:
    app = _create_test_app()
    client = TestClient(app)

    response = client.patch(
        "/agent/conversations/does-not-exist",
        json={"title": "Nope"},
    )

    assert response.status_code == 404


def test_stream_execute_conversation_sql_emits_progress_then_complete() -> None:
    app = _create_test_app()
    client = TestClient(app)

    conversation_id = client.post(
        "/agent/conversations",
        json={"scope": {"database_id": 1, "schema_name": None, "dataset_ids": []}},
    ).json()["id"]

    response = client.post(
        f"/agent/conversations/{conversation_id}/execute-sql/stream",
        json={
            "sql": "SELECT 1",
            "scope": {"database_id": 1, "schema_name": None, "dataset_ids": []},
            "execution_mode": "manual",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[len("data:") :].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert events[0]["type"] == "progress"
    assert events[-1]["type"] == "complete"
    assert events[-1]["response"]["message"]["content"] == "Executed: SELECT 1"


def test_execute_conversation_sql_endpoint_passes_approved_sql_to_graph() -> None:
    store = InMemoryConversationStore()
    graph = StaticConversationGraph(store)
    app = create_app(
        config=_local_config(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=graph,
        conversation_store=store,
    )
    client = TestClient(app)

    create_response = client.post(
        "/agent/conversations",
        json={
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
        },
    )
    conversation_id = create_response.json()["id"]

    response = client.post(
        f"/agent/conversations/{conversation_id}/execute-sql",
        json={
            "sql": "select 1",
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
            "execution_mode": "manual",
            "artifact_id": "artifact-1",
        },
    )

    assert response.status_code == 200
    assert graph.requests == []
    assert graph.sql_execution_requests[-1].sql == "select 1"
    assert graph.sql_execution_requests[-1].artifact_id == "artifact-1"
    assert graph.sql_execution_requests[-1].execution_mode == "manual"


def test_conversation_message_returns_auth_status_when_superset_session_fails() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=_local_config(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=AuthRaisingConversationGraph(),
        conversation_store=store,
    )
    client = TestClient(app)

    create_response = client.post(
        "/agent/conversations",
        json={
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
        },
    )
    conversation_id = create_response.json()["id"]

    response = client.post(
        f"/agent/conversations/{conversation_id}/messages",
        json={
            "message": "What columns are available?",
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
            "execution_mode": "manual",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Superset session expired."


def test_conversation_endpoints_scope_history_by_signed_identity() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=AgentConfig(
            identity_provider="signed_header",
            signed_identity_header="x-agent-identity",
            signed_identity_secret="secret",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
        ),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=StaticConversationGraph(store),
        conversation_store=store,
    )
    client = TestClient(app)

    create_response = client.post(
        "/agent/conversations",
        headers=_signed_identity_header("user-1"),
        json={
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
        },
    )

    assert create_response.status_code == 200
    conversation_id = create_response.json()["id"]
    assert (
        client.get(
            "/agent/conversations",
            headers=_signed_identity_header("user-1"),
        ).json()[0]["id"]
        == conversation_id
    )
    assert (
        client.get(
            "/agent/conversations",
            headers=_signed_identity_header("user-2"),
        ).json()
        == []
    )
    assert (
        client.get(
            f"/agent/conversations/{conversation_id}",
            headers=_signed_identity_header("user-2"),
        ).status_code
        == 404
    )


def test_conversation_endpoints_scope_history_by_superset_session_identity() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=_local_config(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=StaticConversationGraph(store),
        conversation_store=store,
        identity_provider=HeaderIdentityProvider(),
    )
    client = TestClient(app)

    create_response = client.post(
        "/agent/conversations",
        headers={"x-test-superset-user": "42"},
        json={
            "scope": {
                "database_id": 1,
                "schema_name": None,
                "dataset_ids": [16],
            },
        },
    )

    assert create_response.status_code == 200
    conversation_id = create_response.json()["id"]
    assert (
        client.get(
            "/agent/conversations",
            headers={"x-test-superset-user": "42"},
        ).json()[0]["id"]
        == conversation_id
    )
    assert (
        client.get(
            "/agent/conversations",
            headers={"x-test-superset-user": "7"},
        ).json()
        == []
    )


def test_persistent_stores_reject_static_identity_without_local_override() -> None:
    try:
        create_app(
            config=AgentConfig(
                identity_provider="static",
                superset_auth_mode="service_account",
                conversation_store="sqlalchemy",
                agent_database_url="sqlite+pysqlite:///:memory:",
            ),
            ollama_client=FakeOllamaClient(),
            text_to_sql_graph=StaticGraph(),
            conversation_graph=StaticConversationGraph(InMemoryConversationStore()),
        )
    except ValueError as ex:
        assert "require non-static identity" in str(ex)
    else:
        raise AssertionError("Expected static identity persistence guard.")
