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
from typing import List
from uuid import uuid4

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
    ConversationSummary,
    ConversationTruncation,
)
from superset_ai_agent.conversations.store import (
    ConversationArtifactNotFoundError,
    ConversationMessageNotFoundError,
    ConversationNotFoundError,
    ConversationRewriteError,
    DEFAULT_OWNER_ID,
)


class InMemoryConversationStore:
    """Process-local conversation store for development and tests."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        #: Rewrite bookkeeping mirroring the SQL store's soft-delete markers:
        #: ``(conversation_id, anchor_message_id) -> (cut_index, removed batch)``.
        self._truncations: dict[
            tuple[str, str], tuple[int, list[ConversationMessage]]
        ] = {}

    def create(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        kind: str = "sql",
        project_id: str | None = None,
    ) -> Conversation:
        conversation = Conversation(
            owner_id=owner_id,
            scope=scope,
            kind=kind,
            project_id=project_id,
        )
        self._conversations[conversation.id] = conversation
        return conversation.model_copy(deep=True)

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        kind: str | None = None,
        project_id: str | None = None,
    ) -> list[ConversationSummary]:
        conversations = [
            conversation
            for conversation in self._conversations.values()
            if conversation.owner_id == owner_id
            and (kind is None or conversation.kind == kind)
            and (project_id is None or conversation.project_id == project_id)
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

    def update_title(
        self,
        conversation_id: str,
        title: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        conversation.title = title
        conversation.updated_at = _utc_now()
        return conversation.model_copy(deep=True)

    def update_project_id(
        self,
        conversation_id: str,
        project_id: str | None,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        conversation.project_id = project_id
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

    def truncate_from(
        self,
        conversation_id: str,
        message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> ConversationTruncation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        cut_index = next(
            (
                index
                for index, message in enumerate(conversation.messages)
                if message.id == message_id
            ),
            None,
        )
        if cut_index is None:
            raise ConversationMessageNotFoundError(message_id)
        if conversation.messages[cut_index].role != "user":
            raise ConversationRewriteError("A rewrite must anchor on a user message.")
        removed = conversation.messages[cut_index:]
        conversation.messages = conversation.messages[:cut_index]
        conversation.updated_at = _utc_now()
        self._truncations[(conversation_id, message_id)] = (
            cut_index,
            [message.model_copy(deep=True) for message in removed],
        )
        return ConversationTruncation(
            conversation=conversation.model_copy(deep=True),
            removed_messages=[message.model_copy(deep=True) for message in removed],
        )

    def undo_truncate(
        self,
        conversation_id: str,
        anchor_message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        conversation = self._find(conversation_id, owner_id=owner_id)
        record = self._truncations.pop((conversation_id, anchor_message_id), None)
        if record is None:
            raise ConversationRewriteError(
                "No rewrite batch to restore for this message."
            )
        cut_index, batch = record
        replacement = conversation.messages[cut_index:]
        replacement_anchor = next(
            (message for message in replacement if message.role == "user"),
            None,
        )
        if replacement_anchor is not None:
            self._truncations[(conversation_id, replacement_anchor.id)] = (
                cut_index,
                [message.model_copy(deep=True) for message in replacement],
            )
        conversation.messages = conversation.messages[:cut_index] + batch
        conversation.updated_at = _utc_now()
        return conversation.model_copy(deep=True)

    def fork(
        self,
        conversation_id: str,
        message_id: str | None = None,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        source = self._find(conversation_id, owner_id=owner_id)
        cut_index = len(source.messages) - 1
        if message_id is not None:
            anchor_index = next(
                (
                    index
                    for index, message in enumerate(source.messages)
                    if message.id == message_id
                ),
                None,
            )
            if anchor_index is None:
                raise ConversationMessageNotFoundError(message_id)
            cut_index = anchor_index
        fork = Conversation(
            title=f"{source.title} (branch)",
            owner_id=owner_id,
            kind=source.kind,
            project_id=source.project_id,
            parent_conversation_id=source.id,
            forked_from_sequence=cut_index if source.messages else None,
            scope=source.scope.model_copy(deep=True),
            messages=[
                _copy_message_for_fork(message)
                for message in source.messages[: cut_index + 1]
            ],
        )
        self._conversations[fork.id] = fork
        return fork.model_copy(deep=True)

    def list_attempts(
        self,
        conversation_id: str,
        anchor_message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        # ``List`` (not ``list``): the class's own ``list`` method shadows the
        # builtin inside this class body.
    ) -> List[ConversationMessage]:
        self._find(conversation_id, owner_id=owner_id)
        record = self._truncations.get((conversation_id, anchor_message_id))
        if record is None:
            return []
        return [
            message.model_copy(deep=True)
            for message in record[1]
            if message.role == "assistant"
        ]

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


def _copy_message_for_fork(message: ConversationMessage) -> ConversationMessage:
    """Fresh-id copy for a forked thread; changeset artifacts become inert."""

    return ConversationMessage(
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        artifacts=[
            artifact.model_copy(
                update={
                    "id": str(uuid4()),
                    "inert": artifact.inert or artifact.type == "changeset",
                }
            )
            for artifact in message.artifacts
        ],
    )


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
        kind=conversation.kind,
        project_id=conversation.project_id,
        database_id=conversation.scope.database_id,
        catalog_name=conversation.scope.catalog_name,
        schema_name=conversation.scope.schema_name,
        updated_at=conversation.updated_at,
        last_message=last_message,
    )
