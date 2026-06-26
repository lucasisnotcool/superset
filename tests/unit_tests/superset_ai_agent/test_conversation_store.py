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
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
)
from superset_ai_agent.conversations.store import ConversationNotFoundError


def test_conversation_defaults_to_sql_kind() -> None:
    """Existing AI SQL threads keep validating: kind defaults to ``sql``."""

    conversation = Conversation(scope=ConversationScope(database_id=1))
    assert conversation.kind == "sql"
    assert conversation.project_id is None


def test_sql_artifact_round_trips() -> None:
    artifact = ConversationArtifact(type="sql", sql="SELECT 1")
    restored = ConversationArtifact.model_validate(artifact.model_dump(mode="json"))
    assert restored.type == "sql"
    assert restored.sql == "SELECT 1"
    assert restored.payload is None


def test_changeset_artifact_carries_generic_payload() -> None:
    """A non-SQL agent artifact validates with no SQL and an opaque payload."""

    artifact = ConversationArtifact(
        type="changeset",
        payload={"items": [{"op": "create", "path": "models/orders.json"}]},
    )
    restored = ConversationArtifact.model_validate(artifact.model_dump(mode="json"))
    assert restored.type == "changeset"
    assert restored.sql is None
    assert restored.payload == {
        "items": [{"op": "create", "path": "models/orders.json"}]
    }


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


def test_in_memory_store_filters_by_kind_and_project() -> None:
    store = InMemoryConversationStore()
    sql_thread = store.create(ConversationScope(database_id=1), owner_id="u1")
    copilot_thread = store.create(
        ConversationScope(database_id=1),
        owner_id="u1",
        kind="copilot",
        project_id="proj-1",
    )
    store.create(
        ConversationScope(database_id=1),
        owner_id="u1",
        kind="copilot",
        project_id="proj-2",
    )

    # Default list (no filter) still returns everything the owner has.
    assert len(store.list(owner_id="u1")) == 3

    # The SQL agent's history excludes copilot threads.
    sql_ids = [s.id for s in store.list(owner_id="u1", kind="sql")]
    assert sql_ids == [sql_thread.id]

    # A project-scoped copilot list returns only that project's threads.
    copilot = store.list(owner_id="u1", kind="copilot", project_id="proj-1")
    assert [s.id for s in copilot] == [copilot_thread.id]
    assert copilot[0].kind == "copilot"
    assert copilot[0].project_id == "proj-1"


def test_in_memory_store_round_trips_changeset_artifact() -> None:
    store = InMemoryConversationStore()
    conversation = store.create(
        ConversationScope(database_id=1),
        owner_id="u1",
        kind="copilot",
        project_id="proj-1",
    )
    artifact = ConversationArtifact(
        type="changeset",
        payload={"items": [{"op": "create", "path": "models/orders.json"}]},
    )
    store.append(
        conversation.id,
        ConversationMessage(role="assistant", content="Proposed.", artifacts=[artifact]),
        owner_id="u1",
    )

    reloaded = store.get(conversation.id, owner_id="u1")
    stored = reloaded.messages[-1].artifacts[0]
    assert stored.type == "changeset"
    assert stored.sql is None
    assert stored.payload == {
        "items": [{"op": "create", "path": "models/orders.json"}]
    }
