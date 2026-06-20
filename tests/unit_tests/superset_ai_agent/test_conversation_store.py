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

import pytest

from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationMessage,
    ConversationScope,
)
from superset_ai_agent.conversations.store import ConversationNotFoundError


def test_in_memory_store_appends_messages_and_summarizes() -> None:
    store = InMemoryConversationStore()
    conversation = store.create(ConversationScope(database_id=1), owner_id="u1")

    store.append(
        conversation.id,
        ConversationMessage(role="user", content="Show top names"),
        owner_id="u1",
    )
    updated = store.append(
        conversation.id,
        ConversationMessage(role="assistant", content="I drafted SQL."),
        owner_id="u1",
    )
    summaries = store.list(owner_id="u1")

    assert updated.title == "Show top names"
    assert [message.role for message in updated.messages] == ["user", "assistant"]
    assert summaries[0].id == conversation.id
    assert summaries[0].last_message == "I drafted SQL."


def test_in_memory_store_enforces_owner_scope() -> None:
    store = InMemoryConversationStore()
    conversation = store.create(ConversationScope(database_id=1), owner_id="u1")

    with pytest.raises(ConversationNotFoundError):
        store.get(conversation.id, owner_id="u2")
