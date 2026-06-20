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
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from superset_ai_agent.schemas import ExecutionResult, SqlValidation, TraceEvent

ConversationRole = Literal["user", "assistant"]
ConversationStatus = Literal["ok", "needs_review", "error"]
ExecutionMode = Literal["manual", "read_only", "auto"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class ConversationScope(BaseModel):
    """Superset context attached to a conversation turn."""

    database_id: int
    schema_name: str | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    query_editor_id: str | None = None
    current_sql: str | None = None
    selected_text: str | None = None


class ConversationArtifact(BaseModel):
    """Structured artifact emitted by the assistant."""

    type: Literal["sql"] = "sql"
    sql: str
    explanation: str | None = None
    validation: SqlValidation | None = None
    execution_result: ExecutionResult | None = None
    trace: list[TraceEvent] = Field(default_factory=list)


class ConversationMessage(BaseModel):
    """A persisted conversation message."""

    id: str = Field(default_factory=_new_id)
    role: ConversationRole
    content: str
    created_at: datetime = Field(default_factory=_utc_now)
    artifacts: list[ConversationArtifact] = Field(default_factory=list)


class Conversation(BaseModel):
    """Conversation transcript and scope."""

    id: str = Field(default_factory=_new_id)
    title: str = "New chat"
    owner_id: str = "local"
    scope: ConversationScope
    messages: list[ConversationMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ConversationSummary(BaseModel):
    """Compact conversation metadata for history lists."""

    id: str
    title: str
    owner_id: str
    database_id: int
    schema_name: str | None = None
    updated_at: datetime
    last_message: str | None = None


class ConversationCreateRequest(BaseModel):
    """Request to start a conversation."""

    scope: ConversationScope


class ConversationTurnRequest(BaseModel):
    """Request to append a user message and run the conversation agent."""

    message: str = Field(min_length=1)
    scope: ConversationScope
    execution_mode: ExecutionMode = "manual"
    execute: bool | None = None
    model: str | None = None
    max_steps: int = Field(default=8, ge=2, le=16)

    def resolved_execution_mode(self) -> ExecutionMode:
        """Return the execution policy after applying legacy request fields."""

        if self.execution_mode != "manual" or self.execute is None:
            return self.execution_mode
        return "read_only" if self.execute else "manual"


class ConversationTurnResponse(BaseModel):
    """Response from a conversation turn."""

    status: ConversationStatus
    conversation_id: str
    message: ConversationMessage
    artifacts: list[ConversationArtifact] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    conversation: Conversation
