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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from superset_ai_agent.app import create_app
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationMessage,
    ConversationTurnRequest,
    ConversationTurnResponse,
)
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
    def run(self, request: AgentQueryRequest) -> AgentQueryResponse:
        raise RuntimeError("ollama unavailable")


class StaticGraph:
    def run(self, request: AgentQueryRequest) -> AgentQueryResponse:
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

    def run(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str = "local",
    ) -> ConversationTurnResponse:
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


def _create_test_app(store: InMemoryConversationStore | None = None) -> FastAPI:
    active_store = store or InMemoryConversationStore()
    return create_app(
        config=AgentConfig(),
        ollama_client=FakeOllamaClient(),
        text_to_sql_graph=StaticGraph(),
        conversation_graph=StaticConversationGraph(active_store),
        conversation_store=active_store,
    )


def test_health_and_models_use_injected_ollama_client() -> None:
    app = _create_test_app()
    client = TestClient(app)

    health = client.get("/health").json()
    assert health["reachable"] is True
    assert health["model_provider"] == "ollama"
    assert health["ollama_reachable"] is True
    assert client.get("/models").json()[0]["name"] == "qwen2.5-coder:7b"


def test_agent_query_returns_error_payload_when_graph_fails() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=AgentConfig(),
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


def test_validate_sql_endpoint_adds_limit() -> None:
    store = InMemoryConversationStore()
    app = create_app(
        config=AgentConfig(default_sql_limit=25),
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
