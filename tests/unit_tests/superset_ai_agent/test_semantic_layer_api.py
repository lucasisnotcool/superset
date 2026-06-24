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

import json  # noqa: TID251 - standalone agent JSON contract

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
    tmp_path, model_client=None, **config_overrides
) -> tuple[TestClient, StaticContextProvider]:
    semantic_store = InMemorySemanticLayerStore()
    context_provider = StaticContextProvider()
    app = create_app(
        config=_local_config(agent_storage_dir=str(tmp_path), **config_overrides),
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


def test_semantic_layer_document_upload_state_and_events(tmp_path) -> None:
    # C6: the legacy review/index/overlay routes are gone; upload still extracts the
    # document (an enrichment source) and surfaces state + events.
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
    assert document["status"] == "extracted"
    assert "proposed_updates" not in document
    assert context_provider.requests[0].database_id == 1

    # The removed review/index routes return 404/405.
    assert (
        client.patch(
            f"/agent/semantic-layer/documents/{document['id']}/review",
            json={"approved_update_ids": [], "notes": "x"},
        ).status_code
        in {404, 405}
    )
    assert (
        client.post(
            "/agent/semantic-layer/index/rebuild", json={"scope": scope}
        ).status_code
        in {404, 405}
    )

    state = client.get(
        "/agent/semantic-layer/state?database_id=1&dataset_ids=42",
    ).json()
    assert state["document_count"] == 1
    assert "indexed_document_count" not in state
    assert "semantic_layer_version" not in state

    events_response = client.get(
        "/agent/semantic-layer/events?database_id=1&dataset_ids=42",
    )
    assert events_response.status_code == 200
    assert "event: document_uploaded" in events_response.text


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
            "path": "models/gross_moves.json",
            "content": json.dumps({"models": [{"name": "gross_moves"}]}),
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
    assert "models" in proposal["proposed_content"]


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
                    "path": "models/moves.json",
                    "manifest": {
                        "models": [
                            {
                                "name": "moves",
                                "description": "Pipeline moves documented by the model",
                                "tableReference": {"table": "moves"},
                                "columns": [{"name": "stage", "type": "varchar"}],
                            }
                        ]
                    },
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


def test_onboard_seeding_ignores_invented_columns(tmp_path) -> None:
    # W3: onboarding structure is seeded from the catalog, not authored by the
    # model. An LLM that invents a column cannot inject it — the column is simply
    # absent from the seeded draft, so the structural-hallucination class is gone.
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/moves.json",
                    "manifest": {
                        "models": [
                            {
                                "name": "moves",
                                "tableReference": {"table": "moves"},
                                "columns": [
                                    {"name": "ghost_metric", "type": "varchar"}
                                ],
                            }
                        ]
                    },
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
    # The invented column never made it into the seeded model.
    assert "ghost_metric" not in result["files"][0]["content"]


def test_enrich_flags_hallucinated_columns(tmp_path) -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/moves.json",
                    "manifest": {
                        "models": [
                            {
                                "name": "moves",
                                "tableReference": {"table": "moves"},
                                "columns": [
                                    {"name": "ghost_metric", "type": "varchar"}
                                ],
                            }
                        ]
                    },
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
            "path": "models/moves.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": "moves",
                            "tableReference": {"table": "moves"},
                            "columns": [{"name": "stage", "type": "varchar"}],
                        }
                    ]
                }
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
            "path": "models/ghosts.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": "ghosts",
                            "tableReference": {"table": "moves"},
                            "columns": [{"name": "phantom", "type": "varchar"}],
                        }
                    ]
                }
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


def test_activation_dedups_re_emitted_model_across_files(tmp_path) -> None:
    # W4: an enrichment that re-emits an existing model into a new file must not
    # block activation with duplicate_model. The newer file supersedes the older.
    client, _ = _client(tmp_path)
    project = _resolve_project(client)

    def _make(path: str, description: str | None) -> str:
        model = {"name": "moves", "tableReference": {"table": "moves"}}
        if description:
            model["description"] = description
        created = client.post(
            f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
            json={"path": path, "content": json.dumps({"models": [model]})},
        ).json()
        return created["id"]

    first = _make("models/moves.json", None)
    activate_first = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{first}",
        json={"status": "active"},
    )
    assert activate_first.status_code == 200

    # A second file re-declares `moves` (the enrichment cascade) — activates via
    # the dedup safety net rather than failing as duplicate_model.
    second = _make("models/moves_enriched.json", "Enriched moves")
    activate_second = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{second}",
        json={"status": "active"},
    )
    assert activate_second.status_code == 200


def test_create_persists_physical_validation(tmp_path) -> None:
    # The created draft's stored validation reflects physical schema findings
    # immediately, not just at activation time (R15).
    client, _ = _client(tmp_path)
    project = _resolve_project(client)

    response = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/moves.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": "moves",
                            "tableReference": {"table": "moves"},
                            "columns": [{"name": "ghost_metric", "type": "varchar"}],
                        }
                    ]
                }
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
            "path": "models/moves.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": "moves",
                            "tableReference": {"table": "moves"},
                            "columns": [{"name": "ghost_metric", "type": "varchar"}],
                        }
                    ]
                }
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


def _activatable_file(client, project_id: str) -> str:
    # "moves" is exposed by StaticContextProvider; a no-column model maps cleanly
    # (warnings only) so it activates absent the engine gate.
    create = client.post(
        f"/agent/semantic-layer/projects/{project_id}/mdl-files",
        json={
            "path": "models/moves.json",
            "content": json.dumps(
                {"models": [{"name": "moves", "tableReference": {"table": "moves"}}]}
            ),
        },
    )
    assert create.status_code == 200
    return create.json()["id"]


def test_activation_requires_engine_blocks_when_absent(tmp_path, monkeypatch) -> None:
    # F0.1: with the engine mandated but wren-core unavailable, activation must
    # degrade *closed* (409), not silently fall back to structural-only.
    import superset_ai_agent.app as agent_app

    monkeypatch.setattr(agent_app, "wren_core_available", lambda: False)
    client, _ = _client(tmp_path, wren_activation_requires_engine=True)
    project = _resolve_project(client)
    file_id = _activatable_file(client, project["id"])

    activate = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{file_id}",
        json={"status": "active"},
    )
    assert activate.status_code == 409
    assert "wren-core" in activate.json()["detail"]


def test_activation_requires_engine_passes_gate_when_present(
    tmp_path, monkeypatch
) -> None:
    # F0.1: with the engine present, the engine *gate* is satisfied — activation
    # proceeds to validation rather than being blocked by the 409 engine check.
    # (Whether the manifest itself validates is covered by other tests; here we
    # only assert the gate does not short-circuit.)
    import superset_ai_agent.app as agent_app

    monkeypatch.setattr(agent_app, "wren_core_available", lambda: True)
    client, _ = _client(tmp_path, wren_activation_requires_engine=True)
    project = _resolve_project(client)
    file_id = _activatable_file(client, project["id"])

    activate = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{file_id}",
        json={"status": "active"},
    )
    assert activate.status_code != 409


class _SpyRetriever:
    """Records index() calls so a test can prove eager activation re-index (E6)."""

    name = "keyword"

    def __init__(self) -> None:
        self.indexed: list[tuple[str, str]] = []

    def has_index(self, scope_key: str, checksum: str) -> bool:
        return (scope_key, checksum) in self.indexed

    def index(self, items, *, scope_key: str, checksum: str) -> None:
        self.indexed.append((scope_key, checksum))

    def retrieve(self, question: str, *, scope_key: str, checksum: str, k: int):
        return []

    def effective_name(self, scope_key: str) -> str:
        return "keyword"


def test_activation_eagerly_reindexes_retrieval(tmp_path) -> None:
    # E6: activating an MDL file primes the retriever index immediately (no query).
    spy = _SpyRetriever()
    semantic_store = InMemorySemanticLayerStore()
    app = create_app(
        config=_local_config(agent_storage_dir=str(tmp_path)),
        model_client=FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=semantic_store,
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=StaticContextProvider(),
        job_runner=InlineJobRunner(),
        retriever=spy,
    )
    client = TestClient(app)
    project = _resolve_project(client)

    create = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files",
        json={
            "path": "models/moves.json",
            "content": json.dumps(
                {"models": [{"name": "moves", "tableReference": {"table": "moves"}}]}
            ),
        },
    )
    assert create.status_code == 200
    assert spy.indexed == []  # not indexed until activation

    activate = client.patch(
        f"/agent/semantic-layer/projects/{project['id']}/mdl-files/{create.json()['id']}",
        json={"status": "active"},
    )
    assert activate.status_code == 200
    assert len(spy.indexed) == 1  # eager re-index fired on activation


def test_instructions_crud_roundtrip(tmp_path) -> None:
    client, _ = _client(tmp_path)
    scope = {"database_id": 1, "schema_name": "pipeline"}

    created = client.post(
        "/agent/semantic-layer/instructions",
        json={"scope": scope, "instruction": "Exclude test rows", "is_global": True},
    )
    assert created.status_code == 200
    instruction_id = created.json()["id"]
    assert created.json()["is_global"] is True

    listed = client.get(
        "/agent/semantic-layer/instructions",
        params={"database_id": 1, "schema_name": "pipeline"},
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [instruction_id]

    deleted = client.delete(
        f"/agent/semantic-layer/instructions/{instruction_id}"
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}

    # Gone now.
    assert (
        client.get(
            "/agent/semantic-layer/instructions",
            params={"database_id": 1, "schema_name": "pipeline"},
        ).json()
        == []
    )
    assert (
        client.delete(
            f"/agent/semantic-layer/instructions/{instruction_id}"
        ).status_code
        == 404
    )


def test_instructions_listed_regardless_of_dataset_selection(tmp_path) -> None:
    # C5.1 fix: an instruction authored at schema scope (the editor sends no
    # datasets) is still listed when the request carries selected dataset_ids —
    # instructions are schema-scoped, not per-dataset-selection.
    client, _ = _client(tmp_path)
    created = client.post(
        "/agent/semantic-layer/instructions",
        json={
            "scope": {"database_id": 1, "schema_name": "pipeline"},
            "instruction": "Exclude test rows",
            "is_global": True,
        },
    )
    assert created.status_code == 200

    listed = client.get(
        "/agent/semantic-layer/instructions",
        params={"database_id": 1, "schema_name": "pipeline", "dataset_ids": "42,7"},
    )
    assert listed.status_code == 200
    assert [item["instruction"] for item in listed.json()] == ["Exclude test rows"]


def test_instruction_create_rejects_empty(tmp_path) -> None:
    client, _ = _client(tmp_path)
    response = client.post(
        "/agent/semantic-layer/instructions",
        json={"scope": {"database_id": 1, "schema_name": "pipeline"},
              "instruction": "   "},
    )
    assert response.status_code == 400


class RecordingChatModelClient(ChatModelClient):
    """ChatModelClient that records the prompts it received."""

    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.prompts: list[str] = []

    def chat(self, messages, **kwargs):
        self.prompts.append(" ".join(m.content for m in messages))
        return super().chat(messages, **kwargs)


def test_enrich_injects_scope_instructions_into_prompt(tmp_path) -> None:
    payload = json.dumps(
        {
            "files": [
                {
                    "path": "models/moves.json",
                    "manifest": {
                        "models": [
                            {"name": "moves", "tableReference": {"table": "moves"}}
                        ]
                    },
                }
            ]
        }
    )
    model = RecordingChatModelClient(payload)
    client, _ = _client(tmp_path, model_client=model)
    project = _resolve_project(client)

    # An operator instruction for this scope.
    assert (
        client.post(
            "/agent/semantic-layer/instructions",
            json={
                "scope": {"database_id": 1, "schema_name": "pipeline"},
                "instruction": "Always exclude internal test moves",
                "is_global": True,
            },
        ).status_code
        == 200
    )
    document = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/text",
        json={"filename": "glossary.md", "text": "Moves glossary."},
    ).json()

    enrich = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/documents/"
        f"{document['id']}/enrich"
    )
    assert enrich.status_code == 200
    # The instruction reached the enrichment prompt.
    assert any(
        "Always exclude internal test moves" in prompt for prompt in model.prompts
    )
