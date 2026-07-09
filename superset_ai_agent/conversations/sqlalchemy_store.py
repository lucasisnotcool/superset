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
from typing import cast, List
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload, Session, sessionmaker

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationRole,
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
from superset_ai_agent.persistence.models import (
    AiAgentArtifact,
    AiAgentConversation,
    AiAgentMessage,
)


class SqlAlchemyConversationStore:
    """SQLAlchemy-backed conversation store."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

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
        now = _utc_now()
        with self.session_factory() as session:
            model = AiAgentConversation(
                id=conversation.id,
                owner_id=owner_id,
                title=conversation.title,
                kind=kind,
                project_id=project_id,
                database_id=scope.database_id,
                catalog_name=scope.catalog_name,
                schema_name=scope.schema_name,
                scope=scope.model_dump(mode="json"),
                created_at=conversation.created_at,
                updated_at=now,
                deleted_at=None,
            )
            session.add(model)
            session.commit()
        return conversation

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        kind: str | None = None,
        project_id: str | None = None,
    ) -> list[ConversationSummary]:
        with self.session_factory() as session:
            query = (
                select(AiAgentConversation)
                .options(
                    selectinload(AiAgentConversation.messages).selectinload(
                        AiAgentMessage.artifacts
                    )
                )
                .where(
                    AiAgentConversation.owner_id == owner_id,
                    AiAgentConversation.deleted_at.is_(None),
                )
            )
            if kind is not None:
                query = query.where(AiAgentConversation.kind == kind)
            if project_id is not None:
                query = query.where(AiAgentConversation.project_id == project_id)
            conversations = (
                session.execute(query.order_by(AiAgentConversation.updated_at.desc()))
                .scalars()
                .all()
            )
            return [_summarize_model(conversation) for conversation in conversations]

    def get(
        self,
        conversation_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            return _conversation_from_model(conversation)

    def update_scope(
        self,
        conversation_id: str,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            conversation.database_id = scope.database_id
            conversation.catalog_name = scope.catalog_name
            conversation.schema_name = scope.schema_name
            conversation.scope = scope.model_dump(mode="json")
            conversation.updated_at = _utc_now()
            session.commit()
        return self.get(conversation_id, owner_id=owner_id)

    def update_title(
        self,
        conversation_id: str,
        title: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            conversation.title = title
            conversation.updated_at = _utc_now()
            session.commit()
        return self.get(conversation_id, owner_id=owner_id)

    def update_project_id(
        self,
        conversation_id: str,
        project_id: str | None,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            conversation.project_id = project_id
            conversation.updated_at = _utc_now()
            session.commit()
        return self.get(conversation_id, owner_id=owner_id)

    def append(
        self,
        conversation_id: str,
        message: ConversationMessage,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            # max+1 over ALL rows (soft-deleted included): a rewrite leaves
            # soft-deleted rows behind, so len() would collide on the sequence.
            sequence = (
                max(
                    (existing.sequence for existing in conversation.messages),
                    default=-1,
                )
                + 1
            )
            message_model = AiAgentMessage(
                id=message.id,
                conversation_id=conversation.id,
                owner_id=owner_id,
                role=message.role,
                content=message.content,
                sequence=sequence,
                created_at=message.created_at,
            )
            session.add(message_model)
            now = _utc_now()
            for artifact in message.artifacts:
                session.add(
                    AiAgentArtifact(
                        id=artifact.id,
                        message_id=message.id,
                        owner_id=owner_id,
                        type=artifact.type,
                        sql=artifact.sql,
                        payload=artifact.model_dump(mode="json"),
                        created_at=now,
                        updated_at=now,
                    )
                )
            if conversation.title == "New chat" and message.role == "user":
                conversation.title = _title_from_message(message.content)
            conversation.updated_at = now
            session.commit()
        return self.get(conversation_id, owner_id=owner_id)

    def replace_artifact(
        self,
        conversation_id: str,
        artifact_id: str,
        artifact: ConversationArtifact,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            artifact_model = (
                session.execute(
                    select(AiAgentArtifact)
                    .join(AiAgentMessage)
                    .where(
                        AiAgentArtifact.id == artifact_id,
                        AiAgentArtifact.owner_id == owner_id,
                        AiAgentMessage.conversation_id == conversation_id,
                    )
                )
                .scalars()
                .one_or_none()
            )
            if artifact_model is None:
                raise ConversationArtifactNotFoundError(artifact_id)
            artifact_model.type = artifact.type
            artifact_model.sql = artifact.sql
            artifact_model.payload = artifact.model_dump(mode="json")
            artifact_model.updated_at = _utc_now()
            conversation.updated_at = artifact_model.updated_at
            session.commit()
        return self.get(conversation_id, owner_id=owner_id)

    def delete(
        self,
        conversation_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            conversation.deleted_at = _utc_now()
            conversation.updated_at = conversation.deleted_at
            session.commit()

    def truncate_from(
        self,
        conversation_id: str,
        message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> ConversationTruncation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            anchor = next(
                (
                    message
                    for message in conversation.messages
                    if message.id == message_id and message.deleted_at is None
                ),
                None,
            )
            if anchor is None:
                raise ConversationMessageNotFoundError(message_id)
            if anchor.role != "user":
                raise ConversationRewriteError(
                    "A rewrite must anchor on a user message."
                )
            now = _utc_now()
            removed = sorted(
                (
                    message
                    for message in conversation.messages
                    if message.deleted_at is None
                    and message.sequence >= anchor.sequence
                ),
                key=lambda item: item.sequence,
            )
            for message in removed:
                message.deleted_at = now
                message.superseded_by_message_id = anchor.id
            conversation.updated_at = now
            removed_schemas = [_message_from_model(message) for message in removed]
            session.commit()
        return ConversationTruncation(
            conversation=self.get(conversation_id, owner_id=owner_id),
            removed_messages=removed_schemas,
        )

    def undo_truncate(
        self,
        conversation_id: str,
        anchor_message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            batch = [
                message
                for message in conversation.messages
                if message.deleted_at is not None
                and message.superseded_by_message_id == anchor_message_id
            ]
            if not batch:
                raise ConversationRewriteError(
                    "No rewrite batch to restore for this message."
                )
            anchor_sequence = min(message.sequence for message in batch)
            replacement = sorted(
                (
                    message
                    for message in conversation.messages
                    if message.deleted_at is None
                    and message.sequence >= anchor_sequence
                ),
                key=lambda item: item.sequence,
            )
            now = _utc_now()
            # The rewritten turn gets the same marker discipline (its own user
            # message anchors it), so a later rewrite of the same original
            # anchor cannot resurrect it by accident.
            replacement_anchor_id = next(
                (message.id for message in replacement if message.role == "user"),
                anchor_message_id,
            )
            for message in replacement:
                message.deleted_at = now
                message.superseded_by_message_id = replacement_anchor_id
            for message in batch:
                message.deleted_at = None
                message.superseded_by_message_id = None
            conversation.updated_at = now
            session.commit()
        return self.get(conversation_id, owner_id=owner_id)

    def fork(
        self,
        conversation_id: str,
        message_id: str | None = None,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        source = self.get(conversation_id, owner_id=owner_id)
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
        copied = [
            _copy_message_for_fork(message)
            for message in source.messages[: cut_index + 1]
        ]
        now = _utc_now()
        fork = Conversation(
            title=f"{source.title} (branch)",
            owner_id=owner_id,
            kind=source.kind,
            project_id=source.project_id,
            parent_conversation_id=source.id,
            forked_from_sequence=cut_index if source.messages else None,
            scope=source.scope,
        )
        with self.session_factory() as session:
            model = AiAgentConversation(
                id=fork.id,
                owner_id=owner_id,
                title=fork.title,
                kind=fork.kind,
                project_id=fork.project_id,
                parent_conversation_id=source.id,
                forked_from_sequence=fork.forked_from_sequence,
                database_id=source.scope.database_id,
                catalog_name=source.scope.catalog_name,
                schema_name=source.scope.schema_name,
                scope=source.scope.model_dump(mode="json"),
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            session.add(model)
            for sequence, message in enumerate(copied):
                session.add(
                    AiAgentMessage(
                        id=message.id,
                        conversation_id=fork.id,
                        owner_id=owner_id,
                        role=message.role,
                        content=message.content,
                        sequence=sequence,
                        created_at=message.created_at,
                    )
                )
                for artifact in message.artifacts:
                    session.add(
                        AiAgentArtifact(
                            id=artifact.id,
                            message_id=message.id,
                            owner_id=owner_id,
                            type=artifact.type,
                            sql=artifact.sql,
                            payload=artifact.model_dump(mode="json"),
                            created_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()
        return self.get(fork.id, owner_id=owner_id)

    def list_attempts(
        self,
        conversation_id: str,
        anchor_message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        # ``List`` (not ``list``): the store's own ``list`` method shadows the
        # builtin inside this class body.
    ) -> List[ConversationMessage]:
        with self.session_factory() as session:
            conversation = self._get_model(
                session,
                conversation_id,
                owner_id=owner_id,
            )
            attempts = sorted(
                (
                    message
                    for message in conversation.messages
                    if message.deleted_at is not None
                    and message.superseded_by_message_id == anchor_message_id
                    and message.role == "assistant"
                ),
                key=lambda item: item.sequence,
            )
            return [_message_from_model(message) for message in attempts]

    @staticmethod
    def _get_model(
        session: Session,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> AiAgentConversation:
        conversation = (
            session.execute(
                select(AiAgentConversation)
                .options(
                    selectinload(AiAgentConversation.messages).selectinload(
                        AiAgentMessage.artifacts
                    )
                )
                .where(
                    AiAgentConversation.id == conversation_id,
                    AiAgentConversation.owner_id == owner_id,
                    AiAgentConversation.deleted_at.is_(None),
                )
            )
            .scalars()
            .one_or_none()
        )
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation


def _message_from_model(message: AiAgentMessage) -> ConversationMessage:
    return ConversationMessage(
        id=message.id,
        role=cast(ConversationRole, message.role),
        content=message.content,
        created_at=message.created_at,
        artifacts=[
            ConversationArtifact.model_validate(artifact.payload)
            for artifact in sorted(
                message.artifacts,
                key=lambda item: item.created_at,
            )
        ],
    )


def _copy_message_for_fork(message: ConversationMessage) -> ConversationMessage:
    """Fresh-id copy of a message for a forked thread.

    Changeset artifacts become ``inert``: the fork renders them as history but
    must never re-apply the parent thread's proposals (double-apply guard).
    """

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


def _conversation_from_model(model: AiAgentConversation) -> Conversation:
    # Live rows only: soft-deleted messages (rewrite history) never reach the
    # transcript, the model window, or changeset/apply eligibility.
    messages = [
        _message_from_model(message)
        for message in sorted(model.messages, key=lambda item: item.sequence)
        if message.deleted_at is None
    ]
    return Conversation(
        id=model.id,
        title=model.title,
        owner_id=model.owner_id,
        kind=model.kind,
        project_id=model.project_id,
        parent_conversation_id=model.parent_conversation_id,
        forked_from_sequence=model.forked_from_sequence,
        scope=ConversationScope.model_validate(model.scope),
        messages=messages,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _summarize_model(model: AiAgentConversation) -> ConversationSummary:
    messages = sorted(
        (message for message in model.messages if message.deleted_at is None),
        key=lambda item: item.sequence,
    )
    last_message = messages[-1].content if messages else None
    return ConversationSummary(
        id=model.id,
        title=model.title,
        owner_id=model.owner_id,
        kind=model.kind,
        project_id=model.project_id,
        database_id=model.database_id,
        catalog_name=model.catalog_name,
        schema_name=model.schema_name,
        updated_at=model.updated_at,
        last_message=last_message,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _title_from_message(message: str) -> str:
    title = " ".join(message.strip().split())
    if len(title) <= 48:
        return title or "New chat"
    return f"{title[:45].rstrip()}..."
