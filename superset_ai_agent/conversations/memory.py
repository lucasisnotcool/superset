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

from datetime import datetime, timezone

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
    ConversationSummary,
)
from superset_ai_agent.conversations.store import (
    ConversationArtifactNotFoundError,
    ConversationNotFoundError,
    DEFAULT_OWNER_ID,
)


class InMemoryConversationStore:
    """Process-local conversation store for development and tests."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}

    def create(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = Conversation(owner_id=owner_id, scope=scope)
        self._conversations[conversation.id] = conversation
        return conversation.model_copy(deep=True)

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[ConversationSummary]:
        conversations = [
            conversation
            for conversation in self._conversations.values()
            if conversation.owner_id == owner_id
        ]
        return [
            _summarize(conversation)
            for conversation in sorted(
                conversations,
                key=lambda item: item.updated_at,
                reverse=True,
            )
        ]

    def get(
        self,
        conversation_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        return conversation.model_copy(deep=True)

    def update_scope(
        self,
        conversation_id: str,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        conversation.scope = scope
        conversation.updated_at = _utc_now()
        return conversation.model_copy(deep=True)

    def append(
        self,
        conversation_id: str,
        message: ConversationMessage,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        conversation.messages.append(message)
        if conversation.title == "New chat" and message.role == "user":
            conversation.title = _title_from_message(message.content)
        conversation.updated_at = _utc_now()
        return conversation.model_copy(deep=True)

    def replace_artifact(
        self,
        conversation_id: str,
        artifact_id: str,
        artifact: ConversationArtifact,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        for message in conversation.messages:
            for index, existing_artifact in enumerate(message.artifacts):
                if existing_artifact.id == artifact_id:
                    message.artifacts[index] = artifact
                    conversation.updated_at = _utc_now()
                    return conversation.model_copy(deep=True)
        raise ConversationArtifactNotFoundError(artifact_id)

    def delete(
        self,
        conversation_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        self._find(conversation_id, owner_id=owner_id)
        del self._conversations[conversation_id]

    def _find(
        self,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> Conversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.owner_id != owner_id:
            raise ConversationNotFoundError(conversation_id)
        return conversation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _title_from_message(message: str) -> str:
    title = " ".join(message.strip().split())
    if len(title) <= 48:
        return title or "New chat"
    return f"{title[:45].rstrip()}..."


def _summarize(conversation: Conversation) -> ConversationSummary:
    last_message = conversation.messages[-1].content if conversation.messages else None
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        owner_id=conversation.owner_id,
        database_id=conversation.scope.database_id,
        schema_name=conversation.scope.schema_name,
        updated_at=conversation.updated_at,
        last_message=last_message,
    )
