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

from fastapi.testclient import TestClient

from superset_ai_agent.app import create_app
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import AgentQueryRequest, ModelInfo
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.jobs import InlineJobRunner
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore


class _Context:
    """Offline context provider so project resolution needs no live Superset."""

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=42,
                    table_name="moves",
                    database_id=request.database_id,
                    columns=[ColumnSummary(name="stage")],
                    metrics=[],
                )
            ],
        )


_DOC = (
    b"Revenue is grouped by sales region.\n\n"
    b"Weather notes unrelated to anything.\n\n"
    b"Customer churn is driven by support latency.\n\n"
    b"Revenue is grouped by sales region.\n"  # exact duplicate of chunk 0
)


class _Model:
    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: list[ChatMessage], **_: object) -> ModelResult:
        return ModelResult(content="A concise model-written summary.")


def _client(tmp_path, *, indexing=True) -> TestClient:
    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
            agent_storage_dir=str(tmp_path),
            wren_document_indexing_enabled=indexing,
        ),
        model_client=_Model(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=_Context(),
        job_runner=InlineJobRunner(),
    )
    return TestClient(app)


def _project(client: TestClient) -> dict:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={"database_id": 1, "database_label": "Sales", "schema_name": "pipeline"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client: TestClient, project_id: str) -> dict:
    response = client.post(
        f"/agent/semantic-layer/projects/{project_id}/documents",
        files={"file": ("glossary.md", _DOC, "text/markdown")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_indexes_chunks(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    document = _upload(client, project["id"])

    response = client.get(f"/agent/semantic-layer/documents/{document['id']}/chunks")
    assert response.status_code == 200, response.text
    chunks = response.json()
    assert len(chunks) == 4  # 4 sections -> 4 chunks (R1)
    assert chunks[0]["chunk_index"] == 0


def test_download_returns_raw_bytes(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    document = _upload(client, project["id"])

    response = client.get(f"/agent/semantic-layer/documents/{document['id']}/content")
    assert response.status_code == 200
    assert response.content == _DOC
    assert "attachment" in response.headers["content-disposition"]


def test_retrieve_ranks_relevant_chunk_first(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    document = _upload(client, project["id"])

    response = client.get(
        f"/agent/semantic-layer/documents/{document['id']}/retrieve",
        params={"q": "customer churn", "k": 1},
    )
    assert response.status_code == 200, response.text
    chunks = response.json()
    assert chunks
    assert "churn" in chunks[0]["text"]


def test_duplicates_finds_exact_pair(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    _upload(client, project["id"])

    response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/duplicates"
    )
    assert response.status_code == 200, response.text
    matches = response.json()
    assert len(matches) == 1  # chunk 0 and chunk 3 are identical
    assert matches[0]["exact"] is True


def test_reindex_is_idempotent(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    document = _upload(client, project["id"])

    first = client.get(
        f"/agent/semantic-layer/documents/{document['id']}/chunks"
    ).json()
    reindexed = client.post(f"/agent/semantic-layer/documents/{document['id']}/reindex")
    assert reindexed.status_code == 200, reindexed.text
    assert [c["id"] for c in reindexed.json()] == [c["id"] for c in first]


def test_summarize_updates_summary(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    document = _upload(client, project["id"])

    response = client.post(
        f"/agent/semantic-layer/documents/{document['id']}/summarize"
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "A concise model-written summary."


def test_delete_removes_document_and_chunks(tmp_path) -> None:
    client = _client(tmp_path)
    project = _project(client)
    document = _upload(client, project["id"])

    deleted = client.delete(f"/agent/semantic-layer/documents/{document['id']}")
    assert deleted.status_code == 200, deleted.text

    # Document and its content are gone.
    assert (
        client.get(f"/agent/semantic-layer/documents/{document['id']}").status_code
        == 404
    )
    assert (
        client.get(
            f"/agent/semantic-layer/documents/{document['id']}/content"
        ).status_code
        == 404
    )


def test_chunks_gated_when_indexing_disabled(tmp_path) -> None:
    client = _client(tmp_path, indexing=False)
    project = _project(client)
    document = _upload(client, project["id"])

    # Upload still works; chunk routes are hidden behind the feature gate.
    assert (
        client.get(
            f"/agent/semantic-layer/documents/{document['id']}/chunks"
        ).status_code
        == 404
    )
    # ...but plain CRUD (download) stays available.
    assert (
        client.get(
            f"/agent/semantic-layer/documents/{document['id']}/content"
        ).status_code
        == 200
    )


def test_missing_document_is_404(tmp_path) -> None:
    client = _client(tmp_path)
    assert client.get("/agent/semantic-layer/documents/nope/chunks").status_code == 404
    assert client.delete("/agent/semantic-layer/documents/nope").status_code == 404
