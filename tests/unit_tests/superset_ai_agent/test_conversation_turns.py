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

from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationArtifact,
    ConversationScope,
)
from superset_ai_agent.conversations.turns import ConversationTurnService


def _service() -> tuple[ConversationTurnService, str]:
    store = InMemoryConversationStore()
    conversation = store.create(
        ConversationScope(database_id=1),
        owner_id="u1",
        kind="copilot",
        project_id="proj-1",
    )
    return ConversationTurnService(store), conversation.id


def test_begin_turn_appends_user_message() -> None:
    service, cid = _service()

    conversation = service.begin_turn(
        cid, user_content="Add a revenue metric", owner_id="u1"
    )

    assert [m.role for m in conversation.messages] == ["user"]
    assert conversation.messages[-1].content == "Add a revenue metric"


def test_history_messages_windows_and_orders_prior_turns() -> None:
    service, cid = _service()
    service.begin_turn(cid, user_content="first", owner_id="u1")
    service.commit_turn(cid, assistant_content="first reply", owner_id="u1")
    # The current turn's user message is appended, then we read history for it.
    conversation = service.begin_turn(cid, user_content="second", owner_id="u1")

    history = service.history_messages(conversation, max_messages=12)

    # Excludes the trailing (current) user turn; keeps prior user + assistant.
    assert [(m.role, m.content) for m in history] == [
        ("user", "first"),
        ("assistant", "first reply"),
    ]


def test_history_messages_respects_window_size() -> None:
    service, cid = _service()
    for i in range(5):
        service.begin_turn(cid, user_content=f"u{i}", owner_id="u1")
        service.commit_turn(cid, assistant_content=f"a{i}", owner_id="u1")
    conversation = service.begin_turn(cid, user_content="now", owner_id="u1")

    history = service.history_messages(conversation, max_messages=2)

    assert [m.content for m in history] == ["u4", "a4"]


def test_commit_turn_persists_assistant_and_artifacts() -> None:
    service, cid = _service()
    service.begin_turn(cid, user_content="Add a metric", owner_id="u1")

    artifact = ConversationArtifact(
        type="changeset", payload={"items": [{"op": "create"}]}
    )
    conversation = service.commit_turn(
        cid,
        assistant_content="Proposed a changeset.",
        artifacts=[artifact],
        owner_id="u1",
    )

    assistant = conversation.messages[-1]
    assert assistant.role == "assistant"
    assert assistant.artifacts[0].type == "changeset"
    assert assistant.artifacts[0].payload == {"items": [{"op": "create"}]}
