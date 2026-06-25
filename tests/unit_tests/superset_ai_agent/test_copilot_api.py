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


def _client(tmp_path, *, model_client=None, enabled=True) -> TestClient:
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


def test_copilot_run_apply_and_workspace_round_trip(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)
    pid = project["id"]

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

    # Nothing persisted yet (propose, don't persist).
    listing = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files")
    assert listing.json() == []

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
    assert any(tool["name"] == "write_mdl_file" for tool in body["tools"])
    assert body["skills"]  # generate-mdl / enrich-context surfaced read-only


def test_copilot_stream_emits_progress_then_complete(tmp_path) -> None:
    client = _client(tmp_path)
    project = _resolve(client)

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
