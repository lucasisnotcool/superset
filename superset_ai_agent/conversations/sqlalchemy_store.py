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
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationRole,
    ConversationScope,
    ConversationSummary,
)
from superset_ai_agent.conversations.store import (
    ConversationArtifactNotFoundError,
    ConversationNotFoundError,
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
    ) -> Conversation:
        conversation = Conversation(owner_id=owner_id, scope=scope)
        now = _utc_now()
        with self.session_factory() as session:
            model = AiAgentConversation(
                id=conversation.id,
                owner_id=owner_id,
                title=conversation.title,
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
    ) -> list[ConversationSummary]:
        with self.session_factory() as session:
            conversations = (
                session.execute(
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
                    .order_by(AiAgentConversation.updated_at.desc())
                )
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
            sequence = len(conversation.messages)
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


def _conversation_from_model(model: AiAgentConversation) -> Conversation:
    messages = [
        ConversationMessage(
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
        for message in sorted(model.messages, key=lambda item: item.sequence)
    ]
    return Conversation(
        id=model.id,
        title=model.title,
        owner_id=model.owner_id,
        scope=ConversationScope.model_validate(model.scope),
        messages=messages,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _summarize_model(model: AiAgentConversation) -> ConversationSummary:
    messages = sorted(model.messages, key=lambda item: item.sequence)
    last_message = messages[-1].content if messages else None
    return ConversationSummary(
        id=model.id,
        title=model.title,
        owner_id=model.owner_id,
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
