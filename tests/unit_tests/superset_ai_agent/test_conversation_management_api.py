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

"""Conversation-management HTTP routes: rewrite preview, undo, fork, feedback
(plan_conversation_management_impl.md 1a/1c/1d)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from fastapi.testclient import TestClient

from superset_ai_agent.app import create_app
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
)
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


def _harness(
    tmp_path, *, apply_snapshot_store=None
) -> tuple[TestClient, InMemoryConversationStore]:
    store = InMemoryConversationStore()
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
        conversation_store=store,
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=_ContextProvider(),
        job_runner=InlineJobRunner(),
        apply_snapshot_store=apply_snapshot_store,
    )
    return TestClient(app), store


OWNER = "local"


def _seed_sql_thread(store: InMemoryConversationStore, turns: int = 2) -> str:
    conversation = store.create(
        ConversationScope(database_id=1, schema_name="main"), owner_id=OWNER
    )
    for index in range(turns):
        store.append(
            conversation.id,
            ConversationMessage(role="user", content=f"question {index}"),
            owner_id=OWNER,
        )
        store.append(
            conversation.id,
            ConversationMessage(
                role="assistant",
                content=f"answer {index}",
                artifacts=[ConversationArtifact(sql=f"select {index}")],
            ),
            owner_id=OWNER,
        )
    return conversation.id


def _resolve(client: TestClient) -> dict:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={"database_id": 1, "database_label": "Sales", "schema_name": "pipeline"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# -- SQL-thread routes --------------------------------------------------------


def test_rewrite_preview_counts_removed_messages(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store)
    thread = store.get(cid, owner_id=OWNER)
    anchor = thread.messages[2]  # second user turn

    response = client.get(
        f"/agent/conversations/{cid}/rewrite-preview",
        params={"from_message_id": anchor.id},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["removed_message_count"] == 2
    assert preview["applied_changeset_items"] == []
    assert preview["unknown_applies"] is False
    assert preview["memory_write_count"] == 0


def test_rewrite_preview_unknown_message_404s(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store)
    response = client.get(
        f"/agent/conversations/{cid}/rewrite-preview",
        params={"from_message_id": "nope"},
    )
    assert response.status_code == 404


def test_fork_conversation_copies_and_backlinks(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store)
    thread = store.get(cid, owner_id=OWNER)
    anchor = thread.messages[1]  # first assistant turn

    response = client.post(
        f"/agent/conversations/{cid}/fork",
        json={"message_id": anchor.id},
    )
    assert response.status_code == 200, response.text
    fork = response.json()
    assert fork["parent_conversation_id"] == cid
    assert [m["content"] for m in fork["messages"]] == ["question 0", "answer 0"]
    # Source thread untouched.
    assert len(store.get(cid, owner_id=OWNER).messages) == 4


def test_fork_whole_thread_when_no_anchor(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store)
    response = client.post(f"/agent/conversations/{cid}/fork", json={})
    assert response.status_code == 200, response.text
    assert len(response.json()["messages"]) == 4


def test_sql_fork_route_rejects_copilot_threads(tmp_path) -> None:
    client, store = _harness(tmp_path)
    conversation = store.create(
        ConversationScope(database_id=1),
        owner_id=OWNER,
        kind="copilot",
        project_id="p1",
    )
    response = client.post(f"/agent/conversations/{conversation.id}/fork", json={})
    assert response.status_code == 409


def test_undo_rewrite_endpoint_restores_batch(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store)
    thread = store.get(cid, owner_id=OWNER)
    anchor = thread.messages[2]
    store.truncate_from(cid, anchor.id, owner_id=OWNER)

    response = client.post(f"/agent/conversations/{cid}/rewrites/{anchor.id}/undo")
    assert response.status_code == 200, response.text
    assert len(response.json()["messages"]) == 4

    # A second undo has nothing to restore.
    again = client.post(f"/agent/conversations/{cid}/rewrites/{anchor.id}/undo")
    assert again.status_code == 409


def test_list_message_attempts_returns_superseded_turns(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store, turns=1)
    anchor = store.get(cid, owner_id=OWNER).messages[0]
    store.truncate_from(cid, anchor.id, owner_id=OWNER)

    response = client.get(f"/agent/conversations/{cid}/messages/{anchor.id}/attempts")
    assert response.status_code == 200, response.text
    assert [m["content"] for m in response.json()] == ["answer 0"]

    none = client.get(f"/agent/conversations/{cid}/messages/nope/attempts")
    assert none.status_code == 200
    assert none.json() == []


def test_message_feedback_upserts(tmp_path) -> None:
    client, store = _harness(tmp_path)
    cid = _seed_sql_thread(store, turns=1)
    thread = store.get(cid, owner_id=OWNER)
    message_id = thread.messages[1].id

    down = client.post(
        f"/agent/conversations/{cid}/messages/{message_id}/feedback",
        json={"rating": "down", "comment": "wrong join"},
    )
    assert down.status_code == 200, down.text
    assert down.json()["rating"] == "down"

    up = client.post(
        f"/agent/conversations/{cid}/messages/{message_id}/feedback",
        json={"rating": "up"},
    )
    assert up.status_code == 200
    assert up.json()["rating"] == "up"

    missing = client.post(
        f"/agent/conversations/{cid}/messages/nope/feedback",
        json={"rating": "up"},
    )
    assert missing.status_code == 404


# -- Copilot-thread routes ----------------------------------------------------


def test_copilot_fork_marks_changeset_inert_and_blocks_apply(tmp_path) -> None:
    client, _store = _harness(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]
    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    assert run.status_code == 200, run.text
    items = run.json()["items"]

    fork = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}/fork",
        json={},
    )
    assert fork.status_code == 200, fork.text
    forked = fork.json()
    assert forked["parent_conversation_id"] == cid
    artifact = forked["messages"][1]["artifacts"][0]
    assert artifact["type"] == "changeset"
    assert artifact["inert"] is True

    # Applying against the fork must refuse: its only changeset is a copy.
    blocked = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": items, "conversation_id": forked["id"]},
    )
    assert blocked.status_code == 409

    # The source thread still applies fine.
    allowed = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/apply",
        json={"items": items, "conversation_id": cid},
    )
    assert allowed.status_code == 200, allowed.text


def test_copilot_rewrite_preview_flags_applied_turns(tmp_path) -> None:
    client, store = _harness(tmp_path)
    project = _resolve(client)
    pid = project["id"]
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

    thread = store.get(cid, owner_id=OWNER)
    anchor = thread.messages[0]
    preview = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
        "/rewrite-preview",
        params={"from_message_id": anchor.id},
    )
    assert preview.status_code == 200, preview.text
    manifest = preview.json()
    assert manifest["removed_message_count"] == 3
    # The in-memory harness has no durable snapshot store, so the applied turn
    # must surface as an unknown apply rather than silently vanishing.
    assert manifest["unknown_applies"] is True or manifest["applied_changeset_items"]


def test_copilot_rewrite_turn_replaces_tail(tmp_path) -> None:
    client, store = _harness(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]
    first = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    assert first.status_code == 200, first.text
    anchor = store.get(cid, owner_id=OWNER).messages[0]

    rewritten = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={
            "message": "model the moves table with synonyms",
            "conversation_id": cid,
            "rewrite_from_message_id": anchor.id,
        },
    )
    assert rewritten.status_code == 200, rewritten.text

    thread = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
    ).json()
    contents = [m["content"] for m in thread["messages"]]
    assert contents[0] == "model the moves table with synonyms"
    assert len(thread["messages"]) == 2  # rewritten user + fresh assistant turn


def test_copilot_rewrite_rejects_assistant_anchor(tmp_path) -> None:
    client, store = _harness(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]
    run = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={"message": "model the moves table", "conversation_id": cid},
    )
    assert run.status_code == 200, run.text
    assistant = store.get(cid, owner_id=OWNER).messages[1]

    blocked = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot",
        json={
            "message": "again",
            "conversation_id": cid,
            "rewrite_from_message_id": assistant.id,
        },
    )
    assert blocked.status_code == 409


def _durable_snapshot_store():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from superset_ai_agent.persistence.database import (
        create_all_for_tests,
        create_session_factory,
    )
    from superset_ai_agent.semantic_layer.apply_snapshots import ApplySnapshotStore

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    return ApplySnapshotStore(create_session_factory(engine))


def test_copilot_apply_records_snapshots_and_revert_restores(tmp_path) -> None:
    client, store = _harness(tmp_path, apply_snapshot_store=_durable_snapshot_store())
    project = _resolve(client)
    pid = project["id"]
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
    applied_file_id = apply.json()[0]["id"]

    # The apply produced a snapshot group attributed to the "Applied" turn.
    applies = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}/applies"
    )
    assert applies.status_code == 200, applies.text
    groups = applies.json()
    assert len(groups) == 1
    group = groups[0]
    assert group["reverted"] is False
    assert group["items"][0]["op"] == "create"
    applied_turn = store.get(cid, owner_id=OWNER).messages[-1]
    assert applied_turn.content == "Applied 1 draft."
    assert group["message_id"] == applied_turn.id

    # The rewrite preview now names the applied item exactly (no unknowns).
    anchor = store.get(cid, owner_id=OWNER).messages[0]
    preview = client.get(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
        "/rewrite-preview",
        params={"from_message_id": anchor.id},
    ).json()
    assert preview["unknown_applies"] is False
    assert [item["path"] for item in preview["applied_changeset_items"]] == [
        "models/moves.json"
    ]

    # Revert restores the before-image (a created draft is deleted again).
    revert = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
        f"/applies/{group['apply_group_id']}/revert"
    )
    assert revert.status_code == 200, revert.text
    assert revert.json()["reverted_count"] == 1
    assert revert.json()["excluded"] == []
    files = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert all(
        file["id"] != applied_file_id or file["status"] == "deleted" for file in files
    )
    # The action is recorded on the thread; a second revert refuses.
    assert (
        store.get(cid, owner_id=OWNER).messages[-1].content
        == "Reverted 1 applied draft."
    )
    again = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
        f"/applies/{group['apply_group_id']}/revert"
    )
    assert again.status_code == 409


def test_copilot_revert_unknown_group_404s(tmp_path) -> None:
    client, _store = _harness(tmp_path, apply_snapshot_store=_durable_snapshot_store())
    project = _resolve(client)
    pid = project["id"]
    cid = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations"
    ).json()["id"]
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/{cid}"
        "/applies/nope/revert"
    )
    assert response.status_code == 404


def test_copilot_fork_rejects_recovery_threads(tmp_path) -> None:
    client, store = _harness(tmp_path)
    project = _resolve(client)
    pid = project["id"]
    recovery = store.create(
        ConversationScope(database_id=1),
        owner_id=OWNER,
        kind="recovery",
        project_id=pid,
    )
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/copilot/conversations/"
        f"{recovery.id}/fork",
        json={},
    )
    assert response.status_code == 404
