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
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from superset_ai_agent.schemas import (
    AgentStep,
    AuditInfo,
    ChartSpec,
    ExecutionResult,
    InsightCard,
    normalize_schema_names,
    SqlValidation,
    TraceEvent,
    WrenContextArtifact,
)

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
    catalog_name: str | None = None
    #: Primary schema (back-compat scalar). For a multi-schema scope this is the
    #: first element of ``schema_names``.
    schema_name: str | None = None
    #: Full schema set for a multi-schema semantic project. ``None``/empty means the
    #: scope is single-schema (use ``schema_name``). Prefer ``effective_schema_names``.
    schema_names: list[str] | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    #: Explicit semantic-layer project the AI SQL agent should ground on. When a
    #: schema is covered by more than one active project the client may pin a
    #: choice here; the backend honors it only after re-checking access + schema
    #: coverage, falling back to the heuristic otherwise. ``None`` means "let the
    #: backend resolve" (pinned-conversation project, then most-recent match).
    project_id: str | None = None
    query_editor_id: str | None = None
    current_sql: str | None = None
    selected_text: str | None = None

    @property
    def effective_schema_names(self) -> list[str]:
        """Ordered schema set, primary first; ``[]`` when no schema is set."""

        return normalize_schema_names(self.schema_name, self.schema_names)


class ConversationArtifact(BaseModel):
    """Structured artifact emitted by an assistant turn.

    Generic across agents: ``type`` is a free-form discriminator (``"sql"`` for the
    AI SQL agent, ``"changeset"`` for the MDL Copilot). SQL-specific fields stay for
    the SQL agent; agents whose output has a different shape carry it in the generic
    ``payload`` dict (e.g. the Copilot stores ``Changeset.model_dump()`` there).
    """

    id: str = Field(default_factory=_new_id)
    type: str = "sql"
    #: SQL text for ``type="sql"`` artifacts; ``None`` for non-SQL agent artifacts.
    sql: str | None = None
    #: Opaque per-agent payload for artifacts whose shape is not SQL (e.g. a
    #: Copilot ``Changeset`` serialized to JSON). Keeps the conversation layer
    #: agent-agnostic — it never imports an agent's payload type.
    payload: dict[str, Any] | None = None
    explanation: str | None = None
    validation: SqlValidation | None = None
    execution_result: ExecutionResult | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    answer_summary: str | None = None
    insight_cards: list[InsightCard] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    data_preview: ExecutionResult | None = None
    audit: AuditInfo | None = None
    recommended_followups: list[str] = Field(default_factory=list)
    wren_context: WrenContextArtifact | None = None
    #: Per-artifact explain-and-audit timeline so reopened conversations
    #: re-render the message->response chain (ai_agent_explain_and_audit.md).
    timeline: list[AgentStep] = Field(default_factory=list)


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
    #: Agent discriminator: which agent owns this thread. Defaults to ``"sql"`` so
    #: pre-existing AI SQL threads remain valid; the MDL Copilot uses ``"copilot"``.
    kind: str = "sql"
    #: Semantic project binding for project-scoped agents (the Copilot). ``None`` for
    #: database-scoped SQL threads.
    project_id: str | None = None
    scope: ConversationScope
    messages: list[ConversationMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class ConversationSummary(BaseModel):
    """Compact conversation metadata for history lists."""

    id: str
    title: str
    owner_id: str
    kind: str = "sql"
    project_id: str | None = None
    database_id: int
    catalog_name: str | None = None
    schema_name: str | None = None
    updated_at: datetime
    last_message: str | None = None


class ConversationCreateRequest(BaseModel):
    """Request to start a conversation."""

    scope: ConversationScope


class ConversationTitleUpdateRequest(BaseModel):
    """Request to rename a conversation."""

    title: str = Field(min_length=1, max_length=255)


class ConversationTurnRequest(BaseModel):
    """Request to append a user message and run the conversation agent."""

    message: str = Field(min_length=1)
    scope: ConversationScope
    execution_mode: ExecutionMode = "manual"
    execute: bool | None = None
    approved_sql: str | None = Field(default=None, min_length=1)
    approved_artifact_id: str | None = None
    model: str | None = None
    max_steps: int = Field(default=8, ge=2, le=16)

    def resolved_execution_mode(self) -> ExecutionMode:
        """Return the execution policy after applying legacy request fields."""

        if self.execution_mode != "manual" or self.execute is None:
            return self.execution_mode
        return "read_only" if self.execute else "manual"


class ConversationSqlExecutionRequest(BaseModel):
    """Request to execute a SQL artifact that the user approved in chat."""

    sql: str = Field(min_length=1)
    scope: ConversationScope
    execution_mode: ExecutionMode = "manual"
    artifact_id: str | None = None
    model: str | None = None
    max_steps: int = Field(default=8, ge=2, le=16)


class ConversationTurnResponse(BaseModel):
    """Response from a conversation turn."""

    status: ConversationStatus
    conversation_id: str
    message: ConversationMessage
    artifacts: list[ConversationArtifact] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    #: Turn-level explain-and-audit timeline of the whole message->response chain
    #: (ai_agent_explain_and_audit.md).
    timeline: list[AgentStep] = Field(default_factory=list)
    conversation: Conversation
