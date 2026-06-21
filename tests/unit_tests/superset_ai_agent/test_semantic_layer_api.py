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

import json

from fastapi.testclient import TestClient

from superset_ai_agent.app import create_app
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
    DatasetMetadata,
    SupersetAuthError,
)
from superset_ai_agent.schemas import AgentQueryRequest, ModelInfo
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore


class FakeModelClient:
    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]


class StaticContextProvider:
    def __init__(self) -> None:
        self.requests: list[AgentQueryRequest] = []

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        self.requests.append(request)
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=42,
                    table_name="moves",
                    database_id=request.database_id,
                    columns=[],
                    metrics=[],
                )
            ],
        )


class AuthRaisingContextProvider:
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        raise SupersetAuthError("Superset session expired.", status_code=401)


def _local_config(**overrides) -> AgentConfig:
    return AgentConfig(
        identity_provider="static",
        superset_auth_mode="service_account",
        **overrides,
    )


def _client(tmp_path) -> tuple[TestClient, StaticContextProvider]:
    semantic_store = InMemorySemanticLayerStore()
    context_provider = StaticContextProvider()
    app = create_app(
        config=_local_config(agent_storage_dir=str(tmp_path)),
        model_client=FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=semantic_store,
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=context_provider,
    )
    return TestClient(app), context_provider


def test_semantic_layer_returns_auth_status_when_scope_auth_fails(tmp_path) -> None:
    app = create_app(
        config=_local_config(agent_storage_dir=str(tmp_path)),
        model_client=FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=AuthRaisingContextProvider(),
    )
    client = TestClient(app)

    response = client.get("/agent/semantic-layer/state?database_id=1&dataset_ids=42")

    assert response.status_code == 401
    assert response.json()["detail"] == "Superset session expired."


def test_semantic_layer_document_review_index_and_events(tmp_path) -> None:
    client, context_provider = _client(tmp_path)
    scope = {"database_id": 1, "schema_name": None, "dataset_ids": [42]}

    upload_response = client.post(
        "/agent/semantic-layer/documents",
        data={"scope": json.dumps(scope)},
        files={
            "file": (
                "gross_moves.md",
                (
                    b"Gross moves are grouped by stage.\n"
                    b"Metric gross_moves = count moves.\n"
                    b"Show gross moves by stage?"
                ),
                "text/markdown",
            )
        },
    )

    assert upload_response.status_code == 200
    document = upload_response.json()
    assert document["status"] == "needs_review"
    assert document["proposed_updates"]
    assert context_provider.requests[0].database_id == 1

    update_id = document["proposed_updates"][0]["id"]
    review_response = client.patch(
        f"/agent/semantic-layer/documents/{document['id']}/review",
        json={"approved_update_ids": [update_id], "notes": "approved"},
    )

    assert review_response.status_code == 200
    reviewed_document = review_response.json()
    assert reviewed_document["status"] == "approved"
    assert reviewed_document["proposed_updates"][0]["approved"] is True

    index_response = client.post(
        "/agent/semantic-layer/index/rebuild",
        json={"scope": scope},
    )

    assert index_response.status_code == 200
    version = index_response.json()
    assert version["wren_context"]["available"] is True
    assert version["wren_context"]["document_ids"] == [document["id"]]

    state = client.get(
        "/agent/semantic-layer/state?database_id=1&dataset_ids=42",
    ).json()
    assert state["document_count"] == 1
    assert state["indexed_document_count"] == 1
    assert state["semantic_layer_version"] == version["version"]

    events_response = client.get(
        "/agent/semantic-layer/events?database_id=1&dataset_ids=42",
    )
    assert events_response.status_code == 200
    assert "event: document_uploaded" in events_response.text
    assert "event: index_completed" in events_response.text
