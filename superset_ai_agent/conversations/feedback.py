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

"""Persisted per-message thumbs feedback.

Replaces the frontend's local-only feedback stub. One row per (message, owner):
re-rating upserts in place. Which turns get down-voted or regenerated is free
eval signal for the golden-queries loop.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.persistence.models import AiAgentMessageFeedback


class MessageFeedback(BaseModel):
    """A persisted feedback row."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    message_id: str
    owner_id: str
    rating: str
    comment: str | None = None
    created_at: datetime


class MessageFeedbackStore:
    """SQLAlchemy-backed feedback store."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def upsert(
        self,
        *,
        conversation_id: str,
        message_id: str,
        owner_id: str,
        rating: str,
        comment: str | None = None,
    ) -> MessageFeedback:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            row = session.scalars(
                select(AiAgentMessageFeedback).where(
                    AiAgentMessageFeedback.message_id == message_id,
                    AiAgentMessageFeedback.owner_id == owner_id,
                )
            ).one_or_none()
            if row is None:
                row = AiAgentMessageFeedback(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    message_id=message_id,
                    owner_id=owner_id,
                    rating=rating,
                    comment=comment,
                    created_at=now,
                )
                session.add(row)
            else:
                row.rating = rating
                row.comment = comment
                row.created_at = now
            session.commit()
            return MessageFeedback(
                id=row.id,
                conversation_id=row.conversation_id,
                message_id=row.message_id,
                owner_id=row.owner_id,
                rating=row.rating,
                comment=row.comment,
                created_at=row.created_at,
            )

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> list[MessageFeedback]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AiAgentMessageFeedback).where(
                    AiAgentMessageFeedback.conversation_id == conversation_id,
                    AiAgentMessageFeedback.owner_id == owner_id,
                )
            ).all()
            return [
                MessageFeedback(
                    id=row.id,
                    conversation_id=row.conversation_id,
                    message_id=row.message_id,
                    owner_id=row.owner_id,
                    rating=row.rating,
                    comment=row.comment,
                    created_at=row.created_at,
                )
                for row in rows
            ]


class InMemoryMessageFeedbackStore:
    """Process-local feedback store (tests / persistence disabled)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], MessageFeedback] = {}

    def upsert(
        self,
        *,
        conversation_id: str,
        message_id: str,
        owner_id: str,
        rating: str,
        comment: str | None = None,
    ) -> MessageFeedback:
        feedback = MessageFeedback(
            conversation_id=conversation_id,
            message_id=message_id,
            owner_id=owner_id,
            rating=rating,
            comment=comment,
            created_at=datetime.now(timezone.utc),
        )
        self._rows[(message_id, owner_id)] = feedback
        return feedback

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        owner_id: str,
    ) -> list[MessageFeedback]:
        return [
            row
            for row in self._rows.values()
            if row.conversation_id == conversation_id and row.owner_id == owner_id
        ]
