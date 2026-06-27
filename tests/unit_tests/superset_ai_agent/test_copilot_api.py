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

"""MDL Copilot HTTP routes — workspace, copilot run/apply, inspector (Phases 1/3/5)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

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
from superset_ai_agent.llm.base import ChatMessage, ModelResult, ToolCall
from superset_ai_agent.schemas import AgentQueryRequest, ModelInfo
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.jobs import InlineJobRunner
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore

MOVES = json.dumps(
    {
        "models": [
            {
                "name": "moves",
                "tableReference": {"table": "moves"},
                "columns": [{"name": "id", "type": "BIGINT"}],
            }
        ]
    }
)


class _ContextProvider:
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=42,
                    table_name="moves",
                    database_id=request.database_id,
                    columns=[ColumnSummary(name="id", type="BIGINT")],
                    metrics=[],
                )
            ],
        )


class ToolCallingModel:
    """Writes the moves model on the first call, then finalizes."""

    def __init__(self) -> None:
        self.calls = 0

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ModelResult:
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_mdl_file",
                        arguments={"path": "models/moves.json", "content": MOVES},
                    )
                ],
            )
        return ModelResult(content="Created the moves model.")


def _client(tmp_path, *, model_client=None, enabled=True, **config) -> TestClient:
    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
            wren_copilot_enabled=enabled,
            agent_storage_dir=str(tmp_path),
            **config,
        ),
        model_client=model_client or ToolCallingModel(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=_ContextProvider(),
        job_runner=InlineJobRunner(),
    )
    return TestClient(app)


def _resolve(client: TestClient) -> dict:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={"database_id": 1, "database_label": "Sales", "schema_name": "pipeline"},
    )
    assert response.status_code == 200
    return response.json()


def _seed_active_model(
    client: TestClient, pid: str, *, path: str = "models/base.json"
) -> None:
    """Make a project 'ready' for the Copilot by activating one base model.

    The Copilot editing turns are gated until the MDL base layer exists and is
    stable; tests that exercise those turns must onboard a model first.
    """

    created = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files",
        json={"path": path, "content": MOVES},
    )
    assert created.status_code == 200, created.text
    activated = client.patch(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/{created.json()['id']}",
        json={"status": "active"},
    )
    assert activated.status_code == 200, activated.text


def test_copilot_run_apply_and_workspace_round_trip(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert run.status_code == 200, run.text
    changeset = run.json()
    assert changeset["message"] == "Created the moves model."
    assert len(changeset["items"]) == 1
    item = changeset["items"][0]
    assert item["op"] == "create"
    assert item["path"] == "models/moves.json"
    assert changeset["manifest_validation"]["valid"] is True

    # The proposed file is not persisted yet (propose, don't persist).
    listing = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files")
    assert "models/moves.json" not in {file["path"] for file in listing.json()}

    apply = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": changeset["items"]},
    )
    assert apply.status_code == 200, apply.text
    applied = apply.json()
    assert applied[0]["path"] == "models/moves.json"
    assert applied[0]["status"] == "draft"
    assert applied[0]["source_type"] == "copilot"

    workspace = client.get(f"/agent/semantic-layer/projects/{pid}/workspace")
    assert workspace.status_code == 200
    tree = workspace.json()
    names = {child["name"] for child in tree["children"]}
    assert "models" in names
    assert "instructions.md" in names


def test_copilot_inspector_exposes_prompt_tools_skills(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)

    response = client.get(
        f"/agent/semantic-layer/projects/{project['id']}/copilot/inspector"
    )
    assert response.status_code == 200
    body = response.json()
    assert "MDL Copilot" in body["system_prompt"]
    tool_names = {tool["name"] for tool in body["tools"]}
    assert "write_mdl_file" in tool_names
    # Enrichment readiness: the document-grounding tools are exposed AND the
    # system prompt steers the agent to use them (not just silently available).
    assert {"list_documents", "search_documents"} <= tool_names
    assert "search_documents" in body["system_prompt"]
    assert body["skills"]  # generate-mdl / enrich-context surfaced read-only


def test_copilot_stream_emits_progress_then_complete(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    _seed_active_model(client, project["id"])

    with client.stream(
        "POST",
        f"/agent/semantic-layer/projects/{project['id']}/copilot/stream",
        json={"message": "model the moves table"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: progress" in body
    assert "event: complete" in body
    # the terminal frame carries the changeset
    complete = body.split("event: complete")[1]
    assert "models/moves.json" in complete


def test_copilot_stream_preflight_failure_returns_diagnosable_502(
    tmp_path, monkeypatch
) -> None:
    # Request-scoped inputs (conversation store, schema, instructions) resolve
    # *before* streaming starts; a StreamingResponse has no way to change its
    # status once the body iterator is entered. A failure there must surface as a
    # 502 carrying the cause, not a bare 500 with an empty body and no log.
    from superset_ai_agent.conversations.turns import ConversationTurnService

    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)

    conversation = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    )
    assert conversation.status_code == 200, conversation.text
    conversation_id = conversation.json()["id"]

    def _boom(self, *args, **kwargs):
        raise RuntimeError("conversation store unavailable")

    monkeypatch.setattr(ConversationTurnService, "begin_turn", _boom)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/stream",
        json={"message": "model the moves table", "conversation_id": conversation_id},
    )

    assert response.status_code == 502, response.text
    detail = response.json()["detail"]
    assert "Copilot preflight failed" in detail
    assert "conversation store unavailable" in detail


def test_copilot_deploy_preview_lists_pending_drafts(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    # Create a draft directly via the MDL endpoint.
    created = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files",
        json={"path": "models/moves.json", "content": MOVES},
    )
    assert created.status_code == 200, created.text

    preview = client.get(f"/agent/semantic-layer/projects/{pid}/copilot/deploy-preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["op"] == "create"
    assert body["items"][0]["path"] == "models/moves.json"


class CoverageModel:
    """Returns claim-extraction JSON, then coverage-judgement JSON."""

    def __init__(self) -> None:
        self.calls = 0

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ModelResult:
        self.calls += 1
        if self.calls == 1:
            return ModelResult(
                content=json.dumps(
                    {
                        "claims": [
                            {
                                "kind": "definition",
                                "subject": "id",
                                "statement": "id is the order id",
                                "source_quote": "id = order id",
                            },
                            {
                                "kind": "synonym",
                                "subject": "patty",
                                "statement": "a drive unit is a patty",
                            },
                        ]
                    }
                )
            )
        return ModelResult(
            content=json.dumps(
                {
                    "findings": [
                        {"claim_id": "c0", "status": "covered", "matched": "x"},
                        {"claim_id": "c1", "status": "missing"},
                    ]
                }
            )
        )


def test_copilot_coverage_audits_a_document(tmp_path) -> None:
    client = _client(
        tmp_path,
        model_client=CoverageModel(),
        wren_document_indexing_enabled=True,
    )
    project = _resolve(client)
    pid = project["id"]

    client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files",
        json={"path": "models/moves.json", "content": MOVES},
    )
    document = client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "glossary.md",
            "text": "id = order id. A drive unit is a patty.",
            "content_type": "text/markdown",
        },
    )
    assert document.status_code == 200, document.text
    document_id = document.json()["id"]

    report = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/coverage",
        json={"document_id": document_id},
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["total"] == 2
    assert body["covered"] == 1
    assert body["missing"] == 1
    assert body["score"] == 0.5
    assert body["document_filename"] == "glossary.md"


def test_copilot_coverage_falls_back_to_extracted_text_without_indexing(
    tmp_path,
) -> None:
    # Indexing OFF → no chunks; coverage must use the document's extracted text.
    client = _client(tmp_path, model_client=CoverageModel())
    project = _resolve(client)
    pid = project["id"]

    document = client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "glossary.md",
            "text": "id = order id. A drive unit is a patty.",
            "content_type": "text/markdown",
        },
    )
    document_id = document.json()["id"]

    report = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/coverage",
        json={"document_id": document_id},
    )
    assert report.status_code == 200, report.text
    # The model extracted 2 claims from the fallback text → report is populated.
    assert report.json()["total"] == 2


def test_directory_coverage_auto_runs_and_surfaces_in_provenance(tmp_path) -> None:
    # Activating MDL while a document exists auto-runs directory coverage (inline
    # job runner) and records a coverage entry in provenance + a latest report.
    client = _client(tmp_path, model_client=CoverageModel())
    project = _resolve(client)
    pid = project["id"]

    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "glossary.md",
            "text": "id = order id. A drive unit is a patty.",
            "content_type": "text/markdown",
        },
    )
    # Activating a base model is an active-set change → schedules coverage.
    _seed_active_model(client, pid)

    latest = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest")
    assert latest.status_code == 200, latest.text
    body = latest.json()
    assert body is not None
    assert body["status"] == "complete"
    assert body["report"]["total"] == 2

    status = client.get(
        f"/agent/semantic-layer/projects/{pid}/coverage/status"
    ).json()
    assert status["status"] == "ready"
    assert status["running"] is False

    provenance = client.get(
        f"/agent/semantic-layer/projects/{pid}/provenance"
    ).json()
    coverage_entries = [e for e in provenance if e["kind"] == "coverage"]
    assert len(coverage_entries) == 1
    assert coverage_entries[0]["actor_type"] == "system"
    assert coverage_entries[0]["detail"]["run_id"] == body["id"]


def test_directory_coverage_is_idempotent_for_same_version(tmp_path) -> None:
    client = _client(tmp_path, model_client=CoverageModel())
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={"filename": "g.md", "text": "id = order id.", "content_type": "text/markdown"},
    )
    _seed_active_model(client, pid)

    # A manual refresh on the unchanged version reuses the stored run (no new one).
    before = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    refresh = client.post(f"/agent/semantic-layer/projects/{pid}/coverage/refresh")
    assert refresh.status_code == 200, refresh.text
    after = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    assert before["id"] == after["id"]


def test_copilot_routes_404_when_disabled(tmp_path) -> None:
    client = _client(tmp_path, enabled=False)
    project = _resolve(client)

    run = client.post(
        f"/agent/semantic-layer/projects/{project['id']}/copilot",
        json={"message": "x"},
    )
    assert run.status_code == 404
    workspace = client.get(f"/agent/semantic-layer/projects/{project['id']}/workspace")
    assert workspace.status_code == 404


# -- readiness gate: Copilot only edits once the MDL base layer is stable --------


def test_readiness_reports_empty_then_ready(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    empty = client.get(f"/agent/semantic-layer/projects/{pid}/readiness")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "status": "empty",
        "ready": False,
        "has_active_models": False,
        "active_model_count": 0,
        "running_job_id": None,
        "detail": "Schema has not been onboarded yet.",
    }

    _seed_active_model(client, pid)
    ready = client.get(f"/agent/semantic-layer/projects/{pid}/readiness").json()
    assert ready["status"] == "ready"
    assert ready["ready"] is True
    assert ready["active_model_count"] == 1


def test_copilot_run_blocked_until_ready(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    # No onboarded models yet → the editing turn is gated with 409.
    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert run.status_code == 409, run.text
    assert run.json()["detail"]["status"] == "empty"

    # Once a base model is active, the same request is accepted.
    _seed_active_model(client, pid)
    ok = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert ok.status_code == 200, ok.text


def test_copilot_stream_blocked_until_ready(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/stream",
        json={"message": "model the moves table"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["status"] == "empty"


# -- reset: delete-only, never auto re-onboards ----------------------------------


def test_reset_deletes_all_mdl_and_does_not_reonboard(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    # A ready project with one active model + one extra draft file.
    _seed_active_model(client, pid)
    extra = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files",
        json={"path": "models/extra.json", "content": MOVES},
    )
    assert extra.status_code == 200, extra.text
    assert (
        client.get(f"/agent/semantic-layer/projects/{pid}/readiness").json()["status"]
        == "ready"
    )

    # Reset is a plain delete: 200 with a {"deleted": count} body, no async job.
    reset = client.post(f"/agent/semantic-layer/projects/{pid}/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json() == {"deleted": 2}

    # Every MDL file is gone...
    files = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert files == []

    # ...and the project is back to `empty` — NOT `indexing`/`ready`, which proves
    # reset did not kick off onboarding (the inline runner would have completed it).
    readiness = client.get(f"/agent/semantic-layer/projects/{pid}/readiness").json()
    assert readiness["status"] == "empty"
    assert readiness["ready"] is False

    # A Copilot turn is gated again after reset (the contract still holds).
    blocked = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert blocked.status_code == 409, blocked.text


def test_reset_on_empty_project_is_a_noop(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    reset = client.post(f"/agent/semantic-layer/projects/{pid}/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json() == {"deleted": 0}
    assert (
        client.get(f"/agent/semantic-layer/projects/{pid}/readiness").json()["status"]
        == "empty"
    )


# -- Copilot conversations: persistent, multi-turn threads (parity spec) ---------


def test_copilot_conversation_turn_persists_messages_and_changeset(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)

    created = client.post(f"/agent/semantic-layer/projects/{pid}/copilot/conversations")
    assert created.status_code == 200, created.text
    conversation = created.json()
    assert conversation["kind"] == "copilot"
    assert conversation["project_id"] == pid
    cid = conversation["id"]

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    assert run.status_code == 200, run.text

    # The thread now carries the user turn + the assistant turn with a changeset.
    thread = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]
    assert thread["title"] == "model the moves table"  # auto-title from first turn
    artifact = thread["messages"][1]["artifacts"][0]
    assert artifact["type"] == "changeset"
    assert artifact["sql"] is None
    assert artifact["payload"]["items"][0]["path"] == "models/moves.json"


def test_copilot_followup_turn_feeds_prior_history(tmp_path) -> None:
    model = ToolCallingModel()
    client = _client(tmp_path, model_client=model)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    # Reset the scripted model so the follow-up starts a fresh script.
    model.calls = 0
    captured: dict[str, Any] = {}
    original_chat = model.chat

    def _spy(messages: list[ChatMessage], **kwargs: Any) -> ModelResult:
        # Snapshot: the loop mutates this list in place across tool-call rounds.
        captured.setdefault("messages", list(messages))
        return original_chat(messages, **kwargs)

    model.chat = _spy  # type: ignore[method-assign]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "now also add a synonym", "conversation_id": cid},
    )

    sent = captured["messages"]
    contents = [m.content for m in sent]
    # System prompt, then prior user + prior assistant turns, then the new message.
    assert sent[0].role == "system"
    assert "model the moves table" in contents
    assert sent[-1].content.startswith("now also add a synonym")


def test_copilot_new_chat_is_a_distinct_thread(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    first = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]
    second = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]
    assert first != second

    listing = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()
    assert {first, second} == {row["id"] for row in listing}


def test_copilot_conversation_rename_and_delete(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    renamed = client.patch(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}",
        json={"title": "Orders modeling"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "Orders modeling"

    deleted = client.delete(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    gone = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    )
    assert gone.status_code == 404


def test_copilot_conversations_excluded_from_sql_history(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    client.post(f"/agent/semantic-layer/projects/{pid}/copilot/conversations")

    # The AI SQL history surface must not surface Copilot threads.
    sql_history = client.get("/agent/conversations")
    assert sql_history.status_code == 200, sql_history.text
    assert sql_history.json() == []


def test_copilot_turn_without_conversation_id_is_stateless(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert run.status_code == 200, run.text
    # No thread was created (backward-compatible one-shot).
    listing = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()
    assert listing == []


def test_copilot_turn_failure_records_paired_assistant_turn(
    tmp_path, monkeypatch
) -> None:
    import superset_ai_agent.app as app_module

    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    def boom(**_kwargs: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_module, "run_copilot", boom)

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model it", "conversation_id": cid},
    )
    assert run.status_code == 502, run.text

    # The thread must not end on a dangling user turn: an assistant error turn
    # is paired in so a resumed thread stays consistent.
    thread = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]
    assert "failed" in thread["messages"][1]["content"].lower()
    assert thread["messages"][1]["artifacts"] == []


def test_copilot_stream_failure_records_paired_assistant_turn(
    tmp_path, monkeypatch
) -> None:
    import superset_ai_agent.app as app_module

    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    def boom(**_kwargs: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app_module, "run_copilot", boom)

    with client.stream(
        "POST",
        f"/agent/semantic-layer/projects/{pid}/copilot/stream",
        json={"message": "model it", "conversation_id": cid},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "event: error" in body

    thread = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    roles = [m["role"] for m in thread["messages"]]
    assert roles == ["user", "assistant"]
    assert "failed" in thread["messages"][1]["content"].lower()


def test_copilot_stream_cancellation_records_paired_assistant_turn(
    tmp_path, monkeypatch
) -> None:
    """A client disconnect mid-stream records a cancellation as the assistant turn.

    The Starlette test client consumes a streamed body to completion rather than
    propagating ``GeneratorExit``, so we capture the route's real SSE generator and
    ``.close()`` it directly — exactly what Starlette does on a client disconnect.
    """

    import threading

    import superset_ai_agent.app as app_module
    from superset_ai_agent.schemas import AgentStep
    from superset_ai_agent.semantic_layer.copilot.schemas import Changeset

    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    release = threading.Event()

    def slow(**kwargs: Any) -> Changeset:
        # Emit one progress step, then block so we can "disconnect" mid-run.
        on_step = kwargs.get("on_step")
        if on_step:
            on_step(AgentStep(kind="copilot_tool", summary="working", status="ok"))
        release.wait(timeout=2)
        return Changeset(message="late")

    monkeypatch.setattr(app_module, "run_copilot", slow)

    # Capture the route's SSE generator instead of letting the test client drain it.
    real_streaming = app_module.StreamingResponse
    captured: dict[str, Any] = {}

    def capture(content: Any, **kwargs: Any) -> Any:
        captured["gen"] = content
        return real_streaming(iter(()), **kwargs)

    monkeypatch.setattr(app_module, "StreamingResponse", capture)

    started = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/stream",
        json={"message": "model it", "conversation_id": cid},
    )
    assert started.status_code == 200

    gen = captured["gen"]
    first = next(gen)  # the first progress frame
    assert "progress" in first
    gen.close()  # simulate the client disconnecting mid-stream
    release.set()  # let the background worker unwind

    thread = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    # The user turn is paired with a cancellation marker (never left dangling).
    assert thread["messages"][0]["role"] == "user"
    assert thread["messages"][-1]["role"] == "assistant"
    assert thread["messages"][-1]["content"] == "Generation cancelled."


def test_copilot_apply_records_applied_turn_in_thread(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    items = run.json()["items"]

    apply = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": items, "conversation_id": cid},
    )
    assert apply.status_code == 200, apply.text

    thread = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    # user, assistant(changeset), assistant(applied note) — the apply is recorded.
    assert [m["role"] for m in thread["messages"]] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert thread["messages"][-1]["content"] == "Applied 1 draft."


def test_copilot_apply_emits_agent_provenance(tmp_path) -> None:
    # The core gap fix: applying a Copilot changeset must appear in the MDL
    # provenance timeline as an agent edit (it previously bypassed provenance).
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    items = run.json()["items"]
    apply = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": items, "conversation_id": cid},
    )
    assert apply.status_code == 200, apply.text

    provenance = client.get(
        f"/agent/semantic-layer/projects/{pid}/provenance"
    ).json()
    agent_edits = [e for e in provenance if e["kind"] == "copilot_edit"]
    assert len(agent_edits) == 1
    entry = agent_edits[0]
    assert entry["actor_type"] == "agent"
    assert entry["detail"]["source_type"] == "copilot"
    assert entry["detail"]["ops"]["create"] == 1
    assert entry["detail"]["conversation_id"] == cid


def test_copilot_apply_without_conversation_id_does_not_touch_threads(
    tmp_path,
) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    apply = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": run.json()["items"]},
    )
    assert apply.status_code == 200, apply.text
    # Stateless apply created no thread.
    assert (
        client.get(f"/agent/semantic-layer/projects/{pid}/copilot/conversations").json()
        == []
    )
