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

"""Agent-agnostic conversation turn orchestration.

This is the reusable *seam* for persistent, multi-turn agents. It owns only the
store choreography that every agent shares — load the thread, window prior turns
into model history, persist the user and assistant messages (and artifacts) — and
nothing agent-specific (no SQL, no MDL, no graph construction).

A new agent reuses persistence + the turn lifecycle by:

1. ``begin_turn`` — record the user's message (and optional scope refresh);
2. ``history_messages`` — get prior turns as ``ChatMessage``s to prepend to its
   own loop;
3. run its own agentic loop (the part that *is* agent-specific);
4. ``commit_turn`` — persist the assistant message + any artifacts.

The AI SQL agent's :class:`ConversationGraph` predates this and keeps its own
inlined choreography; adopting this service there is an optional, additive
follow-up. The MDL Copilot uses it directly (see ``plan_copilot_parity_impl.md``
§4).
"""

from __future__ import annotations

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
)
from superset_ai_agent.conversations.store import (
    ConversationStore,
    DEFAULT_OWNER_ID,
)
from superset_ai_agent.llm.base import ChatMessage

#: Roles a stored conversation message can carry into model history. Tool/system
#: turns are never persisted as conversation messages, so history is user/assistant.
_HISTORY_ROLES = ("user", "assistant")


class ConversationTurnService:
    """Store choreography shared by persistent, multi-turn agents."""

    def __init__(self, store: ConversationStore) -> None:
        self.store = store

    def begin_turn(
        self,
        conversation_id: str,
        *,
        user_content: str,
        scope: ConversationScope | None = None,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Refresh scope (optional) and append the user's message.

        Returns the loaded thread *including* the just-appended user message, so
        callers can derive history from a single read.
        """

        if scope is not None:
            self.store.update_scope(conversation_id, scope, owner_id=owner_id)
        return self.store.append(
            conversation_id,
            ConversationMessage(role="user", content=user_content),
            owner_id=owner_id,
        )

    @staticmethod
    def history_messages(
        conversation: Conversation,
        *,
        max_messages: int,
        exclude_last: bool = True,
    ) -> list[ChatMessage]:
        """Window prior turns into ``ChatMessage`` history for a tool-calling loop.

        ``exclude_last`` drops the trailing message (the just-appended user turn
        the caller will send fresh), so the returned list is *prior* context only.
        Only user/assistant turns become history; their textual ``content`` is
        carried (artifacts are display-only and excluded from the model window).
        """

        messages = conversation.messages
        if exclude_last and messages:
            messages = messages[:-1]
        windowed = messages[-max_messages:] if max_messages > 0 else []
        return [
            ChatMessage(role=message.role, content=message.content)
            for message in windowed
            if message.role in _HISTORY_ROLES and message.content.strip()
        ]

    def commit_turn(
        self,
        conversation_id: str,
        *,
        assistant_content: str,
        artifacts: list[ConversationArtifact] | None = None,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Append the assistant's message (and any artifacts) to the thread."""

        return self.store.append(
            conversation_id,
            ConversationMessage(
                role="assistant",
                content=assistant_content,
                artifacts=artifacts or [],
            ),
            owner_id=owner_id,
        )
