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

import json  # noqa: TID251 - keep the standalone agent independent of Superset
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationTurnRequest,
    ConversationTurnResponse,
    ExecutionMode,
)
from superset_ai_agent.conversations.store import (
    ConversationStore,
    DEFAULT_OWNER_ID,
)
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    ExecutionResult,
    SqlValidation,
    TraceEvent,
)
from superset_ai_agent.tools.sql import validate_read_only_sql


class ConversationDraft(BaseModel):
    """Structured model output for a conversation turn."""

    response_type: Literal["answer", "sql"] = Field(
        description="Whether this turn is a natural-language answer or SQL artifact."
    )
    message: str = Field(description="Assistant response shown in the chat.")
    sql: str = Field(default="", description="Generated read-only SQL, if any.")
    explanation: str | None = Field(default=None, description="Short SQL explanation.")


class ConversationState(TypedDict, total=False):
    conversation_id: str
    owner_id: str
    request: ConversationTurnRequest
    conversation: Conversation
    context: AgentContext
    draft: ConversationDraft
    artifacts: list[ConversationArtifact]
    pending_artifact: ConversationArtifact | None
    validation: SqlValidation | None
    execution_result: ExecutionResult | None
    sql_iterations: int
    sql_observations: list[dict[str, Any]]
    trace: list[TraceEvent]
    repair_attempts: int
    error: str | None


class ConversationGraph:
    """LangGraph workflow for conversational database assistance."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        model_client: ModelClient,
        context_provider: ContextProvider,
        superset_client: SupersetClient,
        conversation_store: ConversationStore,
    ):
        self.config = config
        self.model_client = model_client
        self.context_provider = context_provider
        self.superset_client = superset_client
        self.conversation_store = conversation_store
        self.graph = self._compile_graph()

    def run(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> ConversationTurnResponse:
        user_message = ConversationMessage(role="user", content=request.message)
        self.conversation_store.update_scope(
            conversation_id,
            request.scope,
            owner_id=owner_id,
        )
        self.conversation_store.append(
            conversation_id,
            user_message,
            owner_id=owner_id,
        )

        initial_state: ConversationState = {
            "conversation_id": conversation_id,
            "owner_id": owner_id,
            "request": request,
            "trace": [],
            "repair_attempts": 0,
            "execution_result": None,
            "artifacts": [],
            "pending_artifact": None,
            "sql_iterations": 0,
            "sql_observations": [],
            "error": None,
        }
        try:
            state = self.graph.invoke(initial_state)
        except Exception as ex:  # pylint: disable=broad-except
            state = {
                **initial_state,
                "error": str(ex),
                "trace": [
                    TraceEvent(
                        step="conversation_error",
                        status="error",
                        summary=str(ex),
                    )
                ],
            }
        assistant_message = self._assistant_message_from_state(state)
        conversation = self.conversation_store.append(
            conversation_id,
            assistant_message,
            owner_id=owner_id,
        )
        status = self._status_from_state(state)
        return ConversationTurnResponse(
            status=status,
            conversation_id=conversation_id,
            message=assistant_message,
            artifacts=assistant_message.artifacts,
            trace=state.get("trace", []),
            conversation=conversation,
        )

    def _compile_graph(self) -> Any:
        graph = StateGraph(ConversationState)
        graph.add_node("load_conversation", self._load_conversation)
        graph.add_node("load_context", self._load_context)
        graph.add_node("draft_response", self._draft_response)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("repair_sql", self._repair_sql)
        graph.add_node("execute_sql", self._execute_sql)

        graph.set_entry_point("load_conversation")
        graph.add_edge("load_conversation", "load_context")
        graph.add_edge("load_context", "draft_response")
        graph.add_conditional_edges(
            "draft_response",
            self._route_after_draft,
            {
                "validate": "validate_sql",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "validate_sql",
            self._route_after_validation,
            {
                "repair": "repair_sql",
                "execute": "execute_sql",
                "end": END,
            },
        )
        graph.add_edge("repair_sql", "validate_sql")
        graph.add_conditional_edges(
            "execute_sql",
            self._route_after_execution,
            {
                "draft": "draft_response",
                "end": END,
            },
        )
        return graph.compile()

    def _load_conversation(self, state: ConversationState) -> ConversationState:
        conversation = self.conversation_store.get(
            state["conversation_id"],
            owner_id=state["owner_id"],
        )
        return {
            **state,
            "conversation": conversation,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="load_conversation",
                    summary=(
                        f"Loaded {len(conversation.messages)} conversation message(s)."
                    ),
                ),
            ],
        }

    def _load_context(self, state: ConversationState) -> ConversationState:
        request = state["request"]
        agent_request = AgentQueryRequest(
            question=request.message,
            database_id=request.scope.database_id,
            schema_name=request.scope.schema_name,
            dataset_ids=request.scope.dataset_ids,
            execute=request.resolved_execution_mode() != "manual",
            model=request.model,
            max_steps=min(request.max_steps, 12),
        )
        context = self.context_provider.get_context(agent_request)
        return {
            **state,
            "context": context,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="load_context",
                    summary=(
                        f"Loaded {len(context.datasets)} dataset(s) from "
                        f"database {context.database.name}."
                    ),
                ),
            ],
        }

    def _draft_response(self, state: ConversationState) -> ConversationState:
        draft = self._call_conversation_model(state=state, validation_errors=[])
        return {
            **state,
            "draft": draft,
            "validation": None,
            "execution_result": None,
            "pending_artifact": None,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="draft_response",
                    summary=(
                        "Generated a conversational answer."
                        if draft.response_type == "answer"
                        else "Generated a SQL draft."
                    ),
                    details={
                        "response_type": draft.response_type,
                        "model": state["request"].model or self.config.default_model(),
                    },
                ),
            ],
        }

    def _validate_sql(self, state: ConversationState) -> ConversationState:
        request = state["request"]
        draft = state["draft"]
        dialect = self.superset_client.get_database_dialect(request.scope.database_id)
        validation = validate_read_only_sql(
            draft.sql,
            dialect=dialect,
            default_limit=self.config.default_sql_limit,
        )
        normalized_sql = validation.normalized_sql or draft.sql
        updated_draft = draft.model_copy(update={"sql": normalized_sql})
        status: Literal["ok", "warning", "error"] = (
            "ok" if validation.is_valid else "error"
        )
        trace = [
            *state.get("trace", []),
            TraceEvent(
                step="validate_sql",
                status=status,
                summary=(
                    "SQL passed read-only validation."
                    if validation.is_valid
                    else "SQL failed read-only validation."
                ),
                details={"errors": validation.errors, "dialect": dialect},
            ),
        ]
        return {
            **state,
            "draft": updated_draft,
            "validation": validation,
            "pending_artifact": ConversationArtifact(
                sql=updated_draft.sql,
                explanation=updated_draft.explanation,
                validation=validation,
                trace=trace,
            ),
            "trace": trace,
        }

    def _repair_sql(self, state: ConversationState) -> ConversationState:
        validation = state.get("validation")
        errors = validation.errors if validation else ["SQL failed validation."]
        draft = self._call_conversation_model(
            state=state,
            validation_errors=errors,
        )
        return {
            **state,
            "draft": draft,
            "repair_attempts": state.get("repair_attempts", 0) + 1,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="repair_sql",
                    summary="Asked the model to repair invalid SQL.",
                    details={"errors": errors},
                ),
            ],
        }

    def _execute_sql(self, state: ConversationState) -> ConversationState:
        request = state["request"]
        validation = state["validation"]
        if validation is None or not validation.normalized_sql:
            return {**state, "error": "No validated SQL is available to execute."}

        try:
            result = self.superset_client.execute_sql(
                database_id=request.scope.database_id,
                sql=validation.normalized_sql,
                schema_name=request.scope.schema_name,
                limit=self.config.default_sql_limit,
            )
        except Exception as ex:  # pylint: disable=broad-except
            trace = [
                *state.get("trace", []),
                TraceEvent(
                    step="execute_sql",
                    status="error",
                    summary="SQL execution failed.",
                    details={"error": str(ex)},
                ),
            ]
            pending_artifact = state.get("pending_artifact")
            return {
                **state,
                "error": str(ex),
                "pending_artifact": (
                    pending_artifact.model_copy(update={"trace": trace})
                    if pending_artifact
                    else None
                ),
                "trace": trace,
            }

        trace = [
            *state.get("trace", []),
            TraceEvent(
                step="execute_sql",
                summary=f"Executed SQL and returned {result.row_count} row(s).",
            ),
        ]
        pending_artifact = state.get("pending_artifact")
        artifact = ConversationArtifact(
            sql=validation.normalized_sql,
            explanation=state["draft"].explanation,
            validation=validation,
            execution_result=result,
            trace=trace,
        )
        if pending_artifact:
            artifact = pending_artifact.model_copy(
                update={"execution_result": result, "trace": trace}
            )

        return {
            **state,
            "execution_result": result,
            "artifacts": [*state.get("artifacts", []), artifact],
            "pending_artifact": None,
            "sql_iterations": state.get("sql_iterations", 0) + 1,
            "sql_observations": [
                *state.get("sql_observations", []),
                _execution_observation(
                    sql=validation.normalized_sql,
                    result=result,
                    max_prompt_result_rows=self.config.max_prompt_result_rows,
                ),
            ],
            "trace": trace,
        }

    @staticmethod
    def _route_after_draft(state: ConversationState) -> str:
        draft = state["draft"]
        if draft.response_type == "sql" or draft.sql.strip():
            return "validate"
        return "end"

    def _route_after_validation(self, state: ConversationState) -> str:
        validation = state["validation"]
        if validation and validation.is_valid:
            return "execute" if self._can_execute_sql(state) else "end"
        if state.get("repair_attempts", 0) < self.config.max_repair_attempts:
            return "repair"
        return "end"

    @staticmethod
    def _route_after_execution(state: ConversationState) -> str:
        if state.get("error"):
            return "end"
        return "draft"

    def _can_execute_sql(self, state: ConversationState) -> bool:
        validation = state.get("validation")
        if not validation or not validation.is_valid or not validation.is_read_only:
            return False
        if state.get("sql_iterations", 0) >= self.config.max_agent_sql_iterations:
            return False
        return state["request"].resolved_execution_mode() in {"read_only", "auto"}

    def _call_conversation_model(
        self,
        *,
        state: ConversationState,
        validation_errors: list[str],
    ) -> ConversationDraft:
        request = state["request"]
        context = state["context"]
        conversation = state["conversation"]
        prompt = get_prompt("conversation")
        execution_mode: ExecutionMode = request.resolved_execution_mode()
        payload = {
            "user_message": request.message,
            "execution_mode": execution_mode,
            "execute": execution_mode != "manual",
            "max_sql_iterations": self.config.max_agent_sql_iterations,
            "remaining_sql_iterations": max(
                self.config.max_agent_sql_iterations - state.get("sql_iterations", 0),
                0,
            ),
            "sql_observations": state.get("sql_observations", []),
            "database": context.database.model_dump(),
            "datasets": [dataset.model_dump() for dataset in context.datasets],
            "conversation": _conversation_payload(
                conversation,
                max_history_messages=self.config.max_history_messages,
                max_prompt_result_rows=self.config.max_prompt_result_rows,
            ),
            "scope": request.scope.model_dump(),
            "validation_errors_to_fix": validation_errors,
        }
        schema = ConversationDraft.model_json_schema()
        result = self.model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Answer this database conversation turn using the "
                        "provided context.\n"
                        f"{json.dumps(payload, default=str)}"
                    ),
                ),
            ],
            model=request.model,
            format_schema=schema,
        )
        try:
            data = json.loads(result.content)
            draft = ConversationDraft.model_validate(data)
        except Exception as ex:  # pylint: disable=broad-except
            return ConversationDraft(
                response_type="answer",
                message=f"Model did not return valid structured JSON: {ex}",
                sql="",
            )
        if validation_errors and draft.response_type != "sql":
            return draft.model_copy(update={"response_type": "sql"})
        return draft

    def _assistant_message_from_state(
        self,
        state: ConversationState,
    ) -> ConversationMessage:
        draft = state.get("draft")
        if draft is None:
            return ConversationMessage(
                role="assistant",
                content=state.get("error") or "The agent could not complete the turn.",
            )

        artifacts = self._artifacts_from_state(state)
        content = draft.message
        if state.get("error"):
            content = f"{content}\n\n{state['error']}".strip()
        return ConversationMessage(
            role="assistant",
            content=content,
            artifacts=artifacts,
        )

    @staticmethod
    def _artifacts_from_state(
        state: ConversationState,
    ) -> list[ConversationArtifact]:
        artifacts = list(state.get("artifacts", []))
        pending_artifact = state.get("pending_artifact")
        if pending_artifact:
            artifacts.append(
                pending_artifact.model_copy(update={"trace": state.get("trace", [])})
            )
        return artifacts

    @staticmethod
    def _status_from_state(
        state: ConversationState,
    ) -> Literal[
        "ok",
        "needs_review",
        "error",
    ]:
        if state.get("error"):
            return "error"
        draft = state.get("draft")
        validation = state.get("validation")
        pending_artifact = state.get("pending_artifact")
        if draft is None:
            return "error"
        if not draft.sql.strip() and pending_artifact is None:
            return "ok"
        if pending_artifact and pending_artifact.validation:
            if pending_artifact.validation.is_valid:
                return "needs_review"
            return "error"
        if validation and validation.is_valid:
            return "needs_review"
        return "error"


def _conversation_payload(
    conversation: Conversation,
    *,
    max_history_messages: int,
    max_prompt_result_rows: int,
) -> list[dict[str, Any]]:
    messages = conversation.messages[-max_history_messages:]
    return [
        {
            "role": message.role,
            "content": message.content,
            "artifacts": [
                _artifact_payload(
                    artifact,
                    max_prompt_result_rows=max_prompt_result_rows,
                )
                for artifact in message.artifacts
            ],
        }
        for message in messages
    ]


def _artifact_payload(
    artifact: ConversationArtifact,
    *,
    max_prompt_result_rows: int,
) -> dict[str, Any]:
    result = artifact.execution_result
    return {
        "type": artifact.type,
        "sql": artifact.sql,
        "explanation": artifact.explanation,
        "validation_errors": artifact.validation.errors if artifact.validation else [],
        "execution_result": (
            {
                "columns": result.columns,
                "rows": result.rows[:max_prompt_result_rows],
                "row_count": result.row_count,
            }
            if result
            else None
        ),
    }


def _execution_observation(
    *,
    sql: str,
    result: ExecutionResult,
    max_prompt_result_rows: int,
) -> dict[str, Any]:
    return {
        "sql": sql,
        "columns": result.columns,
        "rows": result.rows[:max_prompt_result_rows],
        "row_count": result.row_count,
    }
