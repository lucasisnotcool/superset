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


def _client(
    tmp_path, *, model_client=None, enabled=True, coverage_run_store=None, **config
) -> TestClient:
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
        coverage_run_store=coverage_run_store,
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


def test_promote_golden_query_creates_draft_and_is_idempotent(tmp_path) -> None:
    client = _client(tmp_path)
    pid = _resolve(client)["id"]

    promoted = client.post(
        f"/agent/semantic-layer/projects/{pid}/golden-queries/promote",
        json={
            "question": "who are the top customers?",
            "semantic_sql": "SELECT * FROM customers",
        },
    )
    assert promoted.status_code == 200, promoted.text
    file = promoted.json()
    assert file["path"] == "queries.json"
    assert file["status"] == "draft"  # promotion lands a reviewable draft
    body = json.loads(file["content"])
    assert body["queries"][0]["question"] == "who are the top customers?"
    assert body["queries"][0]["verified_at"] is not None  # human-asserted

    # Re-promoting the same question refreshes in place (copy-not-move; idempotent).
    again = client.post(
        f"/agent/semantic-layer/projects/{pid}/golden-queries/promote",
        json={
            "question": "Who are the TOP customers? ",
            "semantic_sql": "SELECT 2",
        },
    )
    assert again.status_code == 200, again.text
    body2 = json.loads(again.json()["content"])
    assert len(body2["queries"]) == 1
    assert body2["queries"][0]["semantic_sql"] == "SELECT 2"


def test_promote_golden_query_requires_question_and_sql(tmp_path) -> None:
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    resp = client.post(
        f"/agent/semantic-layer/projects/{pid}/golden-queries/promote",
        json={"question": "", "semantic_sql": ""},
    )
    assert resp.status_code == 400


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


def test_directory_coverage_auto_runs_and_labels_the_version(tmp_path) -> None:
    # Activating MDL while a document exists auto-runs directory coverage (inline
    # job runner) and stores a latest report. Coverage is decoupled from
    # provenance (Feature B): it is NOT a timeline entry — instead the score is
    # exposed per MDL version (mdl_checksum) and the version-producing provenance
    # entry carries that checksum so the UI can label it.
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
    checksum = body["mdl_checksum"]

    status = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/status").json()
    assert status["status"] == "ready"
    assert status["running"] is False
    # No run in flight → the live-progress field is present but empty (Feature C).
    assert status["progress"] is None

    # Coverage is NOT a provenance timeline entry anymore...
    provenance = client.get(f"/agent/semantic-layer/projects/{pid}/provenance").json()
    assert [e for e in provenance if e["kind"] == "coverage"] == []
    # ...but the version-producing entry carries the checksum the label joins on.
    activations = [e for e in provenance if e["detail"].get("mdl_checksum") == checksum]
    assert activations, "expected a provenance entry stamped with the MDL checksum"

    # The score is exposed as a version label, keyed by mdl_checksum.
    scores = client.get(
        f"/agent/semantic-layer/projects/{pid}/coverage/scores-by-version"
    ).json()
    assert checksum in scores
    assert scores[checksum]["score"] == body["score"]
    assert scores[checksum]["run_id"] == body["id"]


def test_directory_coverage_is_idempotent_for_same_version(tmp_path) -> None:
    client = _client(tmp_path, model_client=CoverageModel())
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)

    # Idempotent (re)scheduling on the unchanged version reuses the stored run.
    before = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    refresh = client.post(
        f"/agent/semantic-layer/projects/{pid}/coverage/refresh?force=false"
    )
    assert refresh.status_code == 200, refresh.text
    after = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    assert before["id"] == after["id"]


def test_manual_refresh_forces_a_fresh_run(tmp_path) -> None:
    """The explicit "Re-run analysis" action recomputes even on an unchanged
    version: the refresh endpoint defaults to force=True so the button is never a
    silent no-op."""

    client = _client(tmp_path, model_client=CoverageModel())
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)

    before = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    # Default (force) refresh: a brand-new run is produced for the same version.
    refresh = client.post(f"/agent/semantic-layer/projects/{pid}/coverage/refresh")
    assert refresh.status_code == 200, refresh.text
    after = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    assert after["id"] != before["id"], "forced re-run must create a new run"
    assert after["status"] == "complete"
    # Same MDL version, freshly recomputed (score label still joins on checksum).
    assert after["mdl_checksum"] == before["mdl_checksum"]


# A second model that maps the known ``moves`` table — the recovery agent's
# proposed file, so manifest validation passes in the test schema.
RECOVERY_MDL = json.dumps(
    {
        "models": [
            {
                "name": "moves_documented",
                "tableReference": {"table": "moves"},
                "columns": [
                    {
                        "name": "id",
                        "type": "BIGINT",
                        "properties": {"description": "the order id"},
                    }
                ],
            }
        ]
    }
)


class CoverageThenRecoveryModel:
    """Coverage extract+judge (one missing claim), then a Copilot recovery turn.

    Dispatches on call shape: coverage stages pass ``format_schema``; the Copilot
    loop passes ``tools``. The recovery turn writes one model then finalizes.
    """

    def __init__(self) -> None:
        self.copilot_calls = 0

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ModelResult:
        schema = kwargs.get("format_schema")
        if schema is not None:
            props = (schema or {}).get("properties", {})
            if "claims" in props:
                return ModelResult(
                    content=json.dumps(
                        {
                            "claims": [
                                {
                                    "kind": "definition",
                                    "subject": "id",
                                    "statement": "id is the order id",
                                }
                            ]
                        }
                    )
                )
            # Judge: the single claim is missing → the report has a gap.
            return ModelResult(
                content=json.dumps(
                    {"findings": [{"claim_id": "c0", "status": "missing"}]}
                )
            )
        # Copilot recovery turn: propose one file, then finalize.
        self.copilot_calls += 1
        if self.copilot_calls == 1:
            return ModelResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="r1",
                        name="write_mdl_file",
                        arguments={
                            "path": "models/recovery.json",
                            "content": RECOVERY_MDL,
                        },
                    )
                ],
            )
        return ModelResult(content="Proposed documenting the order id.")


def test_build_recovery_message_lists_gaps_only() -> None:
    from superset_ai_agent.app import _build_recovery_message
    from superset_ai_agent.semantic_layer.copilot.schemas import (
        CoverageClaim,
        CoverageFinding,
        CoverageReport,
    )

    report = CoverageReport(
        findings=[
            CoverageFinding(
                claim=CoverageClaim(subject="id", statement="id is the order id"),
                status="missing",
                suggestion="Add a description to orders.id",
                document_filename="glossary.md",
            ),
            CoverageFinding(
                claim=CoverageClaim(subject="x", statement="covered already"),
                status="covered",
            ),
        ],
        total=2,
        covered=1,
        missing=1,
        score=0.5,
    )
    message = _build_recovery_message(report)
    assert "id is the order id" in message
    assert "glossary.md" in message
    assert "Add a description to orders.id" in message
    # Covered findings are not work items — they must not appear.
    assert "covered already" not in message
    # The instruction explicitly permits justified removals.
    assert "remove" in message.lower()


def test_coverage_recovery_auto_runs_and_surfaces_suggestions(tmp_path) -> None:
    client = _client(
        tmp_path,
        model_client=CoverageThenRecoveryModel(),
        wren_coverage_recovery_enabled=True,
    )
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "glossary.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    # Activation → coverage (gap) → chained recovery agent (all inline).
    _seed_active_model(client, pid)

    latest = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    run_id = latest["id"]
    assert latest["report"]["missing"] == 1
    assert latest["recovery_status"] == "ready"
    assert latest["recovery_conversation_id"]

    rec = client.get(
        f"/agent/semantic-layer/projects/{pid}/coverage/runs/{run_id}/recovery"
    )
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body["status"] == "ready"
    assert body["suggestion_count"] >= 1
    assert body["changeset"]["items"][0]["op"] == "create"
    assert body["dismissed"] is False
    assert body["stale"] is False

    # The proposed file is NOT persisted (propose, don't apply).
    listing = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert "models/recovery.json" not in {f["path"] for f in listing}

    # The status endpoint surfaces the recovery state for the banner.
    status = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/status").json()
    assert status["recovery_status"] == "ready"
    assert status["recovery_run_id"] == run_id
    assert status["recovery_dismissed"] is False


def test_recovery_dismissal_is_durable_and_keeps_suggestions(tmp_path) -> None:
    client = _client(
        tmp_path,
        model_client=CoverageThenRecoveryModel(),
        wren_coverage_recovery_enabled=True,
    )
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)
    run_id = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()[
        "id"
    ]

    dismiss = client.post(
        f"/agent/semantic-layer/projects/{pid}/coverage/runs/{run_id}/recovery/dismiss"
    )
    assert dismiss.status_code == 200, dismiss.text
    assert dismiss.json()["dismissed"] is True

    after = client.get(
        f"/agent/semantic-layer/projects/{pid}/coverage/runs/{run_id}/recovery"
    ).json()
    assert after["dismissed"] is True
    # Dismissal hides the banner but the suggestions remain reachable.
    assert after["status"] == "ready"
    assert after["suggestion_count"] >= 1


class AlwaysProposesModel:
    """Coverage with a gap; every recovery turn writes one model then finalizes.

    Detects a fresh turn by the absence of tool-result messages, so it proposes an
    edit on each independent recovery run (used to exercise the back-fill path).
    """

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ModelResult:
        schema = kwargs.get("format_schema")
        if schema is not None:
            props = (schema or {}).get("properties", {})
            if "claims" in props:
                return ModelResult(
                    content=json.dumps(
                        {
                            "claims": [
                                {
                                    "kind": "definition",
                                    "subject": "id",
                                    "statement": "id is the order id",
                                }
                            ]
                        }
                    )
                )
            return ModelResult(
                content=json.dumps(
                    {"findings": [{"claim_id": "c0", "status": "missing"}]}
                )
            )
        # Copilot turn: propose once (no tool result yet), else finalize.
        if not any(message.role == "tool" for message in messages):
            return ModelResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="r",
                        name="write_mdl_file",
                        arguments={
                            "path": "models/recovery.json",
                            "content": RECOVERY_MDL,
                        },
                    )
                ],
            )
        return ModelResult(content="Proposed documenting the order id.")


def test_recovery_backfills_an_unrecovered_run(tmp_path) -> None:
    # A coverage run that completed WITHOUT recovery (e.g. audited before the
    # recovery feature existed) carries recovery_status "none". The next coverage
    # trigger / manual refresh on that unchanged version must back-fill recovery,
    # so already-active projects pick up suggestions without a fresh audit.
    from superset_ai_agent.semantic_layer.copilot.schemas import CoverageReport
    from superset_ai_agent.semantic_layer.coverage_store import (
        InMemoryCoverageRunStore,
    )

    store = InMemoryCoverageRunStore()
    client = _client(
        tmp_path,
        model_client=AlwaysProposesModel(),
        wren_coverage_recovery_enabled=True,
        coverage_run_store=store,
    )
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)

    # The auto-run already recovered this version; simulate a pre-recovery run by
    # adding a newer completed run on the SAME version with recovery_status "none".
    seed = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    legacy = store.create(
        project_id=seed["project_id"],
        owner_id=seed["owner_id"],
        mdl_checksum=seed["mdl_checksum"],
        docs_checksum=seed["docs_checksum"],
    )
    store.claim(legacy.id)
    store.complete(
        legacy.id,
        CoverageReport(total=1, missing=1, score=0.0),
        score=0.0,
    )
    assert store.get(legacy.id).recovery_status == "none"

    # Idempotent (re)scheduling on the unchanged version back-fills recovery for
    # the existing run instead of re-auditing (force=false → the back-fill path).
    refresh = client.post(
        f"/agent/semantic-layer/projects/{pid}/coverage/refresh?force=false"
    )
    assert refresh.status_code == 200, refresh.text

    recovered = store.get(legacy.id)
    assert recovered.recovery_status == "ready"
    assert recovered.recovery_conversation_id is not None


def test_recovery_does_not_run_when_flag_disabled(tmp_path) -> None:
    # Default: recovery feature off → coverage runs, no recovery is scheduled.
    client = _client(tmp_path, model_client=CoverageModel())
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)

    latest = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    assert latest["report"]["missing"] == 1  # there is a gap…
    # …but recovery never ran (feature gated off).
    assert latest["recovery_status"] == "none"
    assert latest["recovery_conversation_id"] is None


def test_sweep_recovery_pass_is_decoupled_from_coverage(tmp_path) -> None:
    # The recovery pass must pick up an EXISTING completed report on its own —
    # auto-coverage is OFF here, so the sweep's coverage pass does nothing, yet
    # recovery still proposes suggestions for the stored report. This is the
    # decoupling guarantee: recovery does not require a fresh coverage run.
    from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
    from superset_ai_agent.semantic_layer.copilot.schemas import CoverageReport
    from superset_ai_agent.semantic_layer.coverage_store import (
        InMemoryCoverageRunStore,
    )

    store = InMemoryCoverageRunStore()
    client = _client(
        tmp_path,
        model_client=AlwaysProposesModel(),
        wren_coverage_recovery_enabled=True,
        wren_coverage_auto_enabled=False,  # coverage pass is a no-op
        coverage_run_store=store,
    )
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)  # active MDL, but no coverage (auto off)

    # A completed report with a gap and no recovery yet — the "existing latest
    # report" the recovery pass is meant to pick up.
    legacy = store.create(
        project_id=pid,
        owner_id=DEFAULT_OWNER_ID,
        mdl_checksum="v1",
        docs_checksum="d1",
    )
    store.claim(legacy.id)
    store.complete(legacy.id, CoverageReport(total=1, missing=1, score=0.0), score=0.0)
    assert store.get(legacy.id).recovery_status == "none"

    counts = client.app.state.run_coverage_sweep()
    assert counts["coverage_scheduled"] == 0  # auto-coverage off → pass 1 idle
    assert counts["recovery_scheduled"] == 1

    recovered = store.get(legacy.id)
    assert recovered.recovery_status == "ready"
    assert recovered.recovery_conversation_id is not None


def test_sweep_coverage_pass_audits_an_uncovered_version(tmp_path) -> None:
    # The coverage pass must schedule an audit for a project whose latest version
    # has no completed report. Adding a document changes the docs version but does
    # NOT trigger coverage (only MDL writes do), leaving the current version
    # un-audited — exactly the legacy/pre-feature gap the sweep closes.
    client = _client(tmp_path, model_client=AlwaysProposesModel())
    project = _resolve(client)
    pid = project["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "g.md",
            "text": "id = order id.",
            "content_type": "text/markdown",
        },
    )
    _seed_active_model(client, pid)  # → R1 for version (mdl, docs=g.md)
    before = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()

    # New doc → docs version changes, no coverage triggered.
    client.post(
        f"/agent/semantic-layer/projects/{pid}/documents/text",
        json={
            "filename": "h.md",
            "text": "qty = quantity.",
            "content_type": "text/markdown",
        },
    )

    counts = client.app.state.run_coverage_sweep()
    assert counts["coverage_scheduled"] == 1

    after = client.get(f"/agent/semantic-layer/projects/{pid}/coverage/latest").json()
    assert after["id"] != before["id"], "sweep must audit the uncovered version"
    assert after["status"] == "complete"
    assert after["docs_checksum"] != before["docs_checksum"]


def test_sweep_is_noop_when_both_passes_disabled(tmp_path) -> None:
    # Auto-coverage off + recovery off (default): the sweep schedules nothing.
    client = _client(
        tmp_path,
        model_client=CoverageModel(),
        wren_coverage_auto_enabled=False,
    )
    _resolve(client)
    counts = client.app.state.run_coverage_sweep()
    assert counts == {"coverage_scheduled": 0, "recovery_scheduled": 0}


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


def test_copilot_runs_on_empty_project(tmp_path) -> None:
    # F4: the Copilot is available pre-onboarding so it can *drive* onboarding
    # (propose base models from a BI doc, human-in-the-loop). An empty project no
    # longer blocks the turn; readiness is advisory, not a gate.
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert run.status_code == 200, run.text

    # And it still works once a base model is active.
    _seed_active_model(client, pid)
    ok = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert ok.status_code == 200, ok.text


def test_copilot_stream_runs_on_empty_project(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/stream",
        json={"message": "model the moves table"},
    )
    assert response.status_code == 200, response.text


class _NoOpJobRunner:
    """A runner that never executes the job, so an onboarding job stays running."""

    def submit(self, fn) -> None:  # noqa: ANN001 - test stub
        return None


def test_copilot_blocked_while_onboarding_is_indexing(tmp_path) -> None:
    # F4 keeps exactly one hard gate: an in-flight onboarding *job* (``indexing``)
    # blocks Copilot edits so they don't race the file writes. (empty/ready/failed
    # all pass — proven by the tests above.)
    from superset_ai_agent.app import create_app  # local import: custom runner
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.conversations.memory import InMemoryConversationStore
    from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore

    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
            wren_copilot_enabled=True,
            agent_storage_dir=str(tmp_path),
        ),
        model_client=ToolCallingModel(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=_ContextProvider(),
        job_runner=_NoOpJobRunner(),
    )
    client = TestClient(app)
    project = _resolve(client)
    pid = project["id"]

    started = client.post(f"/agent/semantic-layer/projects/{pid}/onboard")
    assert started.status_code == 202, started.text
    readiness = client.get(f"/agent/semantic-layer/projects/{pid}/readiness").json()
    assert readiness["status"] == "indexing", readiness

    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert run.status_code == 409, run.text
    assert run.json()["detail"]["status"] == "indexing"


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

    # F4: a Copilot turn is still allowed on the reset (empty) project — onboarding
    # is a Copilot-driven action now, not a precondition for chatting.
    allowed = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table"},
    )
    assert allowed.status_code == 200, allowed.text


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

    provenance = client.get(f"/agent/semantic-layer/projects/{pid}/provenance").json()
    agent_edits = [e for e in provenance if e["kind"] == "copilot_edit"]
    assert len(agent_edits) == 1
    entry = agent_edits[0]
    assert entry["actor_type"] == "agent"
    assert entry["detail"]["source_type"] == "copilot"
    assert entry["detail"]["ops"]["create"] == 1
    assert entry["detail"]["conversation_id"] == cid


def test_copilot_apply_with_attachment_records_enrichment(tmp_path) -> None:
    # G1: an apply whose turn carried an inline attachment is recorded as an
    # enrichment pass (not a generic agent edit), with the attachment filename.
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    _seed_active_model(client, pid)
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]

    client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={
            "message": "model the moves table",
            "conversation_id": cid,
            "attachments": [
                {
                    "filename": "spec.md",
                    "content_type": "text/markdown",
                    "text": "moves has an id column",
                }
            ],
        },
    )
    run = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    items = run["messages"][-1]["artifacts"][0]["payload"]["items"]
    apply = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": items, "conversation_id": cid},
    )
    assert apply.status_code == 200, apply.text

    provenance = client.get(f"/agent/semantic-layer/projects/{pid}/provenance").json()
    enrichments = [e for e in provenance if e["kind"] == "enrichment"]
    assert len(enrichments) == 1
    assert enrichments[0]["detail"]["documents"] == [
        {"id": None, "filename": "spec.md"}
    ]


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


def test_project_list_carries_latest_coverage_score_for_the_browser_badge(
    tmp_path,
) -> None:
    # The MDL Lab browser shows a per-project coverage % from the list endpoint
    # (DP-A: one batch read, not an N+1 status fetch per row).
    from superset_ai_agent.semantic_layer.copilot.schemas import CoverageReport
    from superset_ai_agent.semantic_layer.coverage_store import (
        InMemoryCoverageRunStore,
    )

    coverage_store = InMemoryCoverageRunStore()
    client = _client(tmp_path, coverage_run_store=coverage_store)
    project = _resolve(client)
    pid = project["id"]

    listing_url = "/agent/semantic-layer/projects?database_id=1&schema_name=pipeline"

    # No coverage yet → the field is present but null.
    before = client.get(listing_url).json()
    assert before
    assert before[0]["coverage_score"] is None

    # A completed run surfaces its score on the list row.
    run = coverage_store.create(
        project_id=pid, owner_id="local", mdl_checksum="m", docs_checksum="d"
    )
    coverage_store.complete(
        run.id,
        CoverageReport(document_filename="spec.md", total=4, covered=3, missing=1),
        score=0.75,
    )
    after = client.get(listing_url).json()
    assert after[0]["coverage_score"] == 0.75


# A metric whose ``baseObject`` references the ``moves`` model. Activating this
# file before its model is the exact dependency-order case that broke
# "Activate all": the metric resolves only once the model is in the manifest.
_REVENUE_METRIC = json.dumps(
    {"metrics": [{"name": "revenue", "baseObject": "moves", "expression": "count(id)"}]}
)


def _create_draft(client: TestClient, pid: str, path: str, content: str) -> str:
    created = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files",
        json={"path": path, "content": content},
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def test_single_file_activation_still_blocks_unresolved_metric(tmp_path) -> None:
    # The per-file gate stays strict: activating a metric whose model is not yet
    # active 422s. This is the behaviour the bulk endpoint exists to work around.
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    _create_draft(client, pid, "models/moves.json", MOVES)
    metric_id = _create_draft(client, pid, "metrics/revenue.json", _REVENUE_METRIC)

    blocked = client.patch(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/{metric_id}",
        json={"status": "active"},
    )
    assert blocked.status_code == 422, blocked.text
    codes = {msg["code"] for msg in blocked.json()["detail"]["validation"]["messages"]}
    assert "unresolved_metric_base" in codes


def test_bulk_activate_resolves_cross_file_dependency_order(tmp_path) -> None:
    # The regression: "Activate all" must succeed for a metric + its model even
    # though the metric file sorts (and could be toggled) before the model.
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    _create_draft(client, pid, "models/moves.json", MOVES)
    _create_draft(client, pid, "metrics/revenue.json", _REVENUE_METRIC)

    result = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status",
        json={"status": "active", "file_ids": None},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["changed_count"] == 2
    statuses = {file["path"]: file["status"] for file in body["files"]}
    assert statuses["models/moves.json"] == "active"
    assert statuses["metrics/revenue.json"] == "active"


def test_bulk_activate_is_atomic_when_manifest_is_invalid(tmp_path) -> None:
    # A metric referencing a model that does not exist anywhere fails the union
    # validation → 422 and NOTHING is activated (all-or-nothing).
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    _create_draft(client, pid, "models/moves.json", MOVES)
    ghost = json.dumps(
        {"metrics": [{"name": "ghost", "baseObject": "absent", "expression": "1"}]}
    )
    _create_draft(client, pid, "metrics/ghost.json", ghost)

    result = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status",
        json={"status": "active", "file_ids": None},
    )
    assert result.status_code == 422, result.text

    listing = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert all(file["status"] == "draft" for file in listing)


def test_bulk_activate_names_offending_view_file(tmp_path) -> None:
    # R3: when one bad view sinks the atomic activation, the 422 names the
    # offending file (leave-one-out attribution) so the reviewer can reject just
    # that one — the two good views are not silently auto-dropped.
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    _create_draft(client, pid, "models/moves.json", MOVES)
    good = json.dumps({"views": [{"name": "g1", "statement": "SELECT id FROM moves"}]})
    good2 = json.dumps({"views": [{"name": "g2", "statement": "SELECT id FROM moves"}]})
    bad = json.dumps({"views": [{"name": "bad"}]})  # missing statement → invalid
    _create_draft(client, pid, "views/g1.json", good)
    _create_draft(client, pid, "views/g2.json", good2)
    _create_draft(client, pid, "views/bad.json", bad)

    result = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status",
        json={"status": "active", "file_ids": None},
    )
    assert result.status_code == 422, result.text
    detail = result.json()["detail"]
    assert detail["offending_files"] == ["views/bad.json"]
    # Atomic invariant preserved: nothing activated.
    listing = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert all(file["status"] == "draft" for file in listing)


def test_bulk_deactivate_all(tmp_path) -> None:
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    _seed_active_model(client, pid)

    result = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status",
        json={"status": "draft", "file_ids": None},
    )
    assert result.status_code == 200, result.text
    assert result.json()["changed_count"] == 1
    listing = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert all(file["status"] == "draft" for file in listing)


def test_bulk_activate_subset_by_file_ids(tmp_path) -> None:
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    model_id = _create_draft(client, pid, "models/moves.json", MOVES)
    _create_draft(client, pid, "metrics/revenue.json", _REVENUE_METRIC)

    # Activate only the model; the metric is left a draft.
    result = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status",
        json={"status": "active", "file_ids": [model_id]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["changed_count"] == 1
    statuses = {file["path"]: file["status"] for file in result.json()["files"]}
    assert statuses["models/moves.json"] == "active"
    assert statuses["metrics/revenue.json"] == "draft"


def test_bulk_activate_noop_when_nothing_to_change(tmp_path) -> None:
    client = _client(tmp_path)
    pid = _resolve(client)["id"]
    _seed_active_model(client, pid)

    result = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status",
        json={"status": "active", "file_ids": None},
    )
    assert result.status_code == 200, result.text
    assert result.json()["changed_count"] == 0
