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
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    SupersetAuthError,
)
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import AgentQueryRequest, ModelInfo
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.jobs import InlineJobRunner
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore


class FakeModelClient:
    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]


class ChatModelClient(FakeModelClient):
    """Model client returning a fixed structured MDL proposal payload."""

    def __init__(self, content: str) -> None:
        self.content = content

    def chat(self, messages: list[ChatMessage], **_: object) -> ModelResult:
        return ModelResult(content=self.content)


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


class ToggleContextProvider:
    """Returns a fixed schema until ``fail`` is set.

    When failing, only the validation-time schema fetch raises (authorization
    proofs are assumed cached/session-backed in a real deployment), isolating
    the snapshot-fallback path in ``_schema_index_for_project``.
    """

    def __init__(self) -> None:
        self.fail = False

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        if self.fail and request.question == "semantic layer validation":
            raise SupersetAuthError("Superset outage.", status_code=503)
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=99,
                    table_name="moves",
                    database_id=request.database_id,
                    columns=[ColumnSummary(name="stage")],
                    metrics=[],
                )
            ],
        )


def _local_config(**overrides) -> AgentConfig:
    defaults = {
        "identity_provider": "static",
        "superset_auth_mode": "service_account",
        # These API tests inject in-memory stores; keep config lightweight so the
        # persistence enforcement + wren engine don't require a DB here.
        "conversation_store": "memory",
        "semantic_layer_store": "memory",
        "wren_engine": "passthrough",
        "wren_core_validation_enabled": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _client(
    tmp_path, model_client=None
) -> tuple[TestClient, StaticContextProvider]:
    semantic_store = InMemorySemanticLayerStore()
    context_provider = StaticContextProvider()
    app = create_app(
        config=_local_config(agent_storage_dir=str(tmp_path)),
        model_client=model_client or FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=semantic_store,
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=context_provider,
        job_runner=InlineJobRunner(),
    )
    return TestClient(app), context_provider


def _resolve_project(client: TestClient) -> dict:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={
            "database_id": 1,
            "database_label": "Sales",
            "schema_name": "pipeline",
        },
    )
    assert response.status_code == 200
    return response.json()


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


def test_semantic_project_mdl_and_document_flow(tmp_path) -> None:
    client, context_provider = _client(tmp_path)

    resolve_response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={
            "database_id": 1,
            "database_label": "Sales",
            "catalog_name": "prod",
            "schema_name": "pipeline",
            "supplied_uri": "postgresql://user:secret@example.com/sales",
        },
    )

    assert resolve_response.status_code == 200
    project = resolve_response.json()
    assert project["name"] == "Sales.prod.pipeline"
    assert context_provider.requests[-1].catalog_name == "prod"

    create_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/gross_moves.yaml",
            "content": "models:\n  - name: gross_moves\n",
        },
    )

    assert create_response.status_code == 200
    mdl_file = create_response.json()
    assert mdl_file["validation"]["valid"] is True

    update_response = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{mdl_file['id']}",
        json={"status": "active"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["status"] == "active"

    validate_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/"
        f"{mdl_file['id']}/validate"
    )
    assert validate_response.status_code == 200
    assert validate_response.json()["valid"] is True

    materialize_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/materialize"
    )
    assert materialize_response.status_code == 200
    materialized = materialize_response.json()
    assert materialized["project_id"] == project["id"]
    assert materialized["file_count"] == 1
    assert materialized["path"].endswith("mdl.json")

    document_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents",
        files={
            "file": (
                "gross_moves.md",
                b"Gross moves count opportunities by stage.",
                "text/markdown",
            )
        },
    )

    assert document_response.status_code == 200
    document = document_response.json()
    assert document["project_id"] == project["id"]
    assert document["scope"]["catalog_name"] == "prod"

    enrichment_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/"
        f"{document['id']}/enrich"
    )

    assert enrichment_response.status_code == 200
    proposal = enrichment_response.json()
    assert proposal["source_document_id"] == document["id"]
    assert proposal["validation"]["valid"] is True
    assert "models:" in proposal["proposed_yaml"]


def test_onboard_creates_draft_models_deterministic_fallback(tmp_path) -> None:
    # FakeModelClient has no chat(), so onboarding falls back to deterministic
    # schema introspection. The inline job runner completes the job before the
    # 202 response returns.
    client, _ = _client(tmp_path)
    project = _resolve_project(client)

    response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/onboard"
    )

    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "onboarding"
    assert job["status"] == "completed"
    result = job["result"]
    assert result["project_id"] == project["id"]
    assert result["model_count"] == 1
    assert result["files"][0]["source_type"] == "onboarding"
    assert result["files"][0]["status"] == "draft"
    assert "moves" in result["files"][0]["content"]

    # The job is pollable.
    poll = client.get(
        f"/agent/semantic-layer/projects/{project['id']}/jobs/{job['id']}"
    )
    assert poll.status_code == 200
    assert poll.json()["status"] == "completed"


def test_onboard_uses_llm_when_available(tmp_path) -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/moves.yaml",
                    "yaml": (
                        "models:\n"
                        "  - name: moves\n"
                        "    description: Pipeline moves documented by the model\n"
                    ),
                }
            ]
        }
    )
    client, _ = _client(tmp_path, model_client=ChatModelClient(payload))
    project = _resolve_project(client)

    response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/onboard"
    )

    assert response.status_code == 202
    result = response.json()["result"]
    assert result["model_count"] == 1
    assert "documented by the model" in result["files"][0]["content"]


def test_onboard_flags_hallucinated_columns_as_non_activatable(tmp_path) -> None:
    # "moves" exists with no columns; an LLM that invents a column produces a
    # draft that is written but flagged non-activatable (R3).
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/moves.yaml",
                    "yaml": (
                        "models:\n"
                        "  - name: moves\n"
                        "    table_reference:\n"
                        "      table: moves\n"
                        "    columns:\n"
                        "      - name: ghost_metric\n"
                    ),
                }
            ]
        }
    )
    client, _ = _client(tmp_path, model_client=ChatModelClient(payload))
    project = _resolve_project(client)

    response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/onboard"
    )

    assert response.status_code == 202
    result = response.json()["result"]
    assert result["model_count"] == 1
    assert result["files"][0]["status"] == "draft"
    assert any("cannot be activated" in w for w in result["warnings"])


def test_enrich_flags_hallucinated_columns(tmp_path) -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/moves.yaml",
                    "yaml": (
                        "models:\n"
                        "  - name: moves\n"
                        "    table_reference:\n"
                        "      table: moves\n"
                        "    columns:\n"
                        "      - name: ghost_metric\n"
                    ),
                }
            ]
        }
    )
    client, _ = _client(tmp_path, model_client=ChatModelClient(payload))
    project = _resolve_project(client)
    document = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/text",
        json={"filename": "glossary.md", "text": "Ghost metric is a thing."},
    ).json()

    enrich = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/"
        f"{document['id']}/enrich"
    )

    assert enrich.status_code == 200
    proposal = enrich.json()
    assert proposal["validation"]["valid"] is False
    assert any(
        message["code"] == "unknown_column"
        for message in proposal["validation"]["messages"]
    )


def test_activation_uses_schema_snapshot_during_outage(tmp_path) -> None:
    context_provider = ToggleContextProvider()
    app = create_app(
        config=_local_config(agent_storage_dir=str(tmp_path)),
        model_client=FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=context_provider,
        job_runner=InlineJobRunner(),
    )
    client = TestClient(app)
    project = _resolve_project(client)

    # A valid activation captures a schema snapshot ({moves: [stage]}).
    valid = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/moves.yaml",
            "content": (
                "models:\n"
                "  - name: moves\n"
                "    table_reference:\n"
                "      table: moves\n"
                "    columns:\n"
                "      - name: stage\n"
            ),
        },
    ).json()
    activate_valid = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{valid['id']}",
        json={"status": "active"},
    )
    assert activate_valid.status_code == 200

    # Simulate a Superset outage; physical validation must still work via the
    # snapshot rather than degrading to structural-only.
    context_provider.fail = True
    hallucinated = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/ghosts.yaml",
            "content": (
                "models:\n"
                "  - name: ghosts\n"
                "    table_reference:\n"
                "      table: moves\n"
                "    columns:\n"
                "      - name: phantom\n"
            ),
        },
    ).json()
    blocked = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{hallucinated['id']}",
        json={"status": "active"},
    )
    assert blocked.status_code == 422
    assert any(
        message["code"] == "unknown_column"
        for message in blocked.json()["detail"]["validation"]["messages"]
    )


def test_create_persists_physical_validation(tmp_path) -> None:
    # The created draft's stored validation reflects physical schema findings
    # immediately, not just at activation time (R15).
    client, _ = _client(tmp_path)
    project = _resolve_project(client)

    response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/moves.yaml",
            "content": (
                "models:\n"
                "  - name: moves\n"
                "    table_reference:\n"
                "      table: moves\n"
                "    columns:\n"
                "      - name: ghost_metric\n"
            ),
        },
    )
    assert response.status_code == 200
    validation = response.json()["validation"]
    assert validation["valid"] is False
    assert any(m["code"] == "unknown_column" for m in validation["messages"])


def test_activation_blocked_for_hallucinated_column(tmp_path) -> None:
    # StaticContextProvider exposes table "moves" with no columns, so any
    # referenced column is a hallucination caught by physical validation.
    client, _ = _client(tmp_path)
    project = _resolve_project(client)

    create = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/moves.yaml",
            "content": (
                "models:\n"
                "  - name: moves\n"
                "    table_reference:\n"
                "      table: moves\n"
                "    columns:\n"
                "      - name: ghost_metric\n"
            ),
        },
    )
    assert create.status_code == 200
    file_id = create.json()["id"]

    activate = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{file_id}",
        json={"status": "active"},
    )
    assert activate.status_code == 422
    detail = activate.json()["detail"]
    assert detail["validation"]["valid"] is False
    assert any(
        message["code"] == "unknown_column"
        for message in detail["validation"]["messages"]
    )


def test_create_document_from_text_and_enrich(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)

    text_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/text",
        json={
            "filename": "glossary.md",
            "text": "Gross moves count opportunities advanced per stage.",
        },
    )

    assert text_response.status_code == 200
    document = text_response.json()
    assert document["project_id"] == project["id"]
    assert document["filename"] == "glossary.md"

    enrich_response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/"
        f"{document['id']}/enrich"
    )
    assert enrich_response.status_code == 200
    assert enrich_response.json()["source_document_id"] == document["id"]
