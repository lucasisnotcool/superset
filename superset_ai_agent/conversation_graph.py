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
import logging
from collections.abc import Generator, Iterator
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from superset_ai_agent.artifacts.charts import infer_chart_spec
from superset_ai_agent.artifacts.insights import build_artifact_bundle, profile_result
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
    ConversationSqlExecutionRequest,
    ConversationTurnRequest,
    ConversationTurnResponse,
    ExecutionMode,
)
from superset_ai_agent.conversations.store import (
    ConversationArtifactNotFoundError,
    ConversationStore,
    DEFAULT_OWNER_ID,
)
from superset_ai_agent.explain import (
    attempt_index_at,
    build_agent_timeline,
    compact_recalled_examples,
    step_from_event,
)
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    SupersetAuthError,
    SupersetClient,
)
from superset_ai_agent.integrations.wren.client import DisabledWrenClient, WrenClient
from superset_ai_agent.intent import classify_intent
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.llm.embeddings import create_embedder
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentStep,
    AuditInfo,
    ChartSpec,
    ExecutionResult,
    InsightCard,
    SqlExecutionSource,
    SqlValidation,
    TraceEvent,
    WrenContextArtifact,
    WrenRetrievalArtifact,
)
from superset_ai_agent.semantic_layer.engine import (
    create_semantic_engine,
    SemanticEngine,
)
from superset_ai_agent.semantic_layer.engine.planning import (
    plan_semantic_sql_step,
    with_engine_provenance,
)
from superset_ai_agent.semantic_layer.golden_queries import (
    merge_recalled_examples,
    recall_golden_queries,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.memory_store import (
    build_recall_access,
    load_recall_access,
    Memory,
    NullMemory,
    RecallAccess,
    refs_from_sql,
)
from superset_ai_agent.semantic_layer.projects import SemanticProjectStore
from superset_ai_agent.semantic_layer.runtime import cap_context_items
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    retrieve_mdl_context,
    Retriever,
)
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.wren_runtime import (
    materialize_request_semantic_project,
    resolve_effective_schema,
)
from superset_ai_agent.tools.sql import validate_read_only_sql
from superset_ai_agent.tools.sql_policy import decide, SqlClassification

logger = logging.getLogger(__name__)

#: Authoring guidance injected when semantic-SQL mode is active (engine rewrites
#: model-qualified SQL into native SQL). See wren_full.md Phase 1.3.
_SEMANTIC_SQL_GUIDANCE = (
    "Semantic-SQL mode is ON. Write SQL against the semantic models by their "
    "MDL model names (see wren_context.matched_models and context_items), "
    "referencing model columns, defined relationships, and metrics. Do not "
    "hand-write physical joins for defined relationships; the semantic engine "
    "rewrites your query into native SQL. Never reference tables or columns "
    "absent from the provided semantic context."
)


class ConversationDraft(BaseModel):
    """Structured model output for a conversation turn."""

    response_type: Literal["answer", "sql"] = Field(
        description="Whether this turn is a natural-language answer or SQL artifact."
    )
    message: str = Field(description="Assistant response shown in the chat.")
    sql: str = Field(default="", description="Generated read-only SQL, if any.")
    explanation: str | None = Field(default=None, description="Short SQL explanation.")


class SqlReflection(BaseModel):
    """Structured model output for a SQL execution observation."""

    outcome: Literal["answer", "retry", "clarify"] = Field(
        description=(
            "Whether the observations are enough to answer, need a different "
            "SQL retry, or require clarification from the user."
        )
    )
    message: str = Field(
        description=(
            "User-facing answer or explanation. For retry, summarize why a "
            "different query is needed."
        )
    )
    retry_feedback: str | None = Field(
        default=None,
        description="Feedback for the SQL drafting model when outcome is retry.",
    )


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
    answer_summary: str | None
    insight_cards: list[InsightCard]
    chart_spec: ChartSpec | None
    data_preview: ExecutionResult | None
    audit: AuditInfo | None
    recommended_followups: list[str]
    wren_context: WrenContextArtifact | None
    wren_retrieval: WrenRetrievalArtifact | None
    wren_materialization: WrenMaterializationResult | None
    wren_mdl_path: str | None
    recall_access: RecallAccess | None
    semantic_sql: str | None
    native_sql: str | None
    engine: str | None
    engine_warnings: list[str]
    engine_correctable_warnings: list[str]
    engine_correction_attempts: int
    recalled_examples: list[dict[str, Any]]
    intent: str | None
    sql_iterations: int
    sql_observations: list[dict[str, Any]]
    attempted_sql: list[str]
    sql_reflection: SqlReflection | None
    reflection_feedback: str | None
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
        wren_client: WrenClient | None = None,
        semantic_project_store: SemanticProjectStore | None = None,
        mdl_file_store: MdlFileStore | None = None,
        semantic_engine: SemanticEngine | None = None,
        memory: Memory | None = None,
        retriever: Retriever | None = None,
    ):
        self.config = config
        self.model_client = model_client
        self.context_provider = context_provider
        self.superset_client = superset_client
        self.conversation_store = conversation_store
        self.wren_client = wren_client or DisabledWrenClient()
        self.semantic_project_store = semantic_project_store
        self.mdl_file_store = mdl_file_store
        self.semantic_engine = semantic_engine or create_semantic_engine(config)
        self.memory = memory or NullMemory()
        self.retriever = retriever or create_retriever(config, create_embedder(config))
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

        state = self._invoke_graph(
            conversation_id=conversation_id,
            request=request,
            owner_id=owner_id,
        )
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
            timeline=self._turn_timeline(state),
            conversation=conversation,
        )

    @staticmethod
    def _approved_turn_request(
        request: ConversationSqlExecutionRequest,
    ) -> ConversationTurnRequest:
        return ConversationTurnRequest(
            message="Execute selected SQL.",
            scope=request.scope,
            execution_mode=request.execution_mode,
            approved_sql=request.sql,
            approved_artifact_id=request.artifact_id,
            model=request.model,
            max_steps=request.max_steps,
        )

    def execute_approved_sql(
        self,
        *,
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> ConversationTurnResponse:
        """Execute approved SQL and update the original artifact in place."""

        self.conversation_store.update_scope(
            conversation_id,
            request.scope,
            owner_id=owner_id,
        )
        state = self._invoke_graph(
            conversation_id=conversation_id,
            request=self._approved_turn_request(request),
            owner_id=owner_id,
        )
        return self._assemble_execute_response(
            conversation_id=conversation_id,
            request=request,
            state=state,
            owner_id=owner_id,
        )

    def _assemble_execute_response(
        self,
        *,
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        state: ConversationState,
        owner_id: str,
    ) -> ConversationTurnResponse:
        """Replace the approved artifact in place and append the assistant turn."""

        conversation = self.conversation_store.get(
            conversation_id,
            owner_id=owner_id,
        )
        original_artifact = _find_artifact(
            conversation,
            artifact_id=request.artifact_id,
            sql=request.sql,
        )
        updated_artifact = _artifact_with_execution_state(
            original_artifact=original_artifact,
            state=state,
        )
        response_artifacts: list[ConversationArtifact] = []
        if updated_artifact and original_artifact:
            try:
                conversation = self.conversation_store.replace_artifact(
                    conversation_id,
                    original_artifact.id,
                    updated_artifact,
                    owner_id=owner_id,
                )
                response_artifacts = [updated_artifact]
            except ConversationArtifactNotFoundError:
                response_artifacts = [updated_artifact]
        elif updated_artifact:
            response_artifacts = [updated_artifact]

        assistant_message = self._assistant_message_from_state(state)
        assistant_artifacts = assistant_message.artifacts
        if updated_artifact:
            updated_artifact_key = _sql_match_key(updated_artifact.sql)
            assistant_artifacts = [
                artifact
                for artifact in assistant_artifacts
                if _sql_match_key(artifact.sql) != updated_artifact_key
            ]
        assistant_message = assistant_message.model_copy(
            update={
                "content": _approved_sql_response_content(state, assistant_message),
                "artifacts": assistant_artifacts,
            }
        )
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
            artifacts=[
                _with_artifact_timeline(artifact) for artifact in response_artifacts
            ],
            trace=state.get("trace", []),
            timeline=self._turn_timeline(state),
            conversation=conversation,
        )

    def execute_approved_sql_stream(
        self,
        *,
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Iterator[dict[str, Any]]:
        """Stream an approved-SQL execution turn (progress then complete).

        Mirrors :meth:`execute_approved_sql` but streams each newly produced
        trace event, so retries during execution are visible live.
        """

        self.conversation_store.update_scope(
            conversation_id,
            request.scope,
            owner_id=owner_id,
        )
        turn_request = self._approved_turn_request(request)
        initial_state = self._initial_state(
            conversation_id=conversation_id,
            request=turn_request,
            owner_id=owner_id,
        )
        final_state: ConversationState = initial_state
        emitted = 0
        try:
            for state in self.graph.stream(initial_state, stream_mode="values"):
                final_state = state
                emitted = yield from self._emit_new_trace(state, emitted)
        except GeneratorExit:
            self._append_cancellation_message(conversation_id, owner_id)
            raise
        except Exception as ex:  # pylint: disable=broad-except
            final_state, error_event = self._error_state(initial_state, final_state, ex)
            yield _progress_event(error_event)

        response = self._assemble_execute_response(
            conversation_id=conversation_id,
            request=request,
            state=final_state,
            owner_id=owner_id,
        )
        yield {"type": "complete", "response": response}

    def _initial_state(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str,
    ) -> ConversationState:
        return {
            "conversation_id": conversation_id,
            "owner_id": owner_id,
            "request": request,
            "trace": [],
            "repair_attempts": 0,
            "engine_correction_attempts": 0,
            "execution_result": None,
            "answer_summary": None,
            "insight_cards": [],
            "chart_spec": None,
            "data_preview": None,
            "audit": None,
            "recommended_followups": [],
            "wren_context": None,
            "wren_retrieval": None,
            "wren_materialization": None,
            "wren_mdl_path": None,
            "recalled_examples": [],
            "intent": None,
            "artifacts": [],
            "pending_artifact": None,
            "sql_iterations": 0,
            "sql_observations": [],
            "attempted_sql": [],
            "sql_reflection": None,
            "reflection_feedback": None,
            "error": None,
        }

    def _invoke_graph(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str,
    ) -> ConversationState:
        initial_state = self._initial_state(
            conversation_id=conversation_id,
            request=request,
            owner_id=owner_id,
        )
        try:
            return self.graph.invoke(initial_state)
        except SupersetAuthError:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            return {
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

    def run_stream(
        self,
        *,
        conversation_id: str,
        request: ConversationTurnRequest,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Iterator[dict[str, Any]]:
        """Run a conversation turn, yielding step progress then the final turn.

        Mirrors :meth:`run` but streams each newly produced trace event as a
        ``progress`` dict so callers can surface live agent activity, then yields
        a terminal ``complete`` dict carrying the full turn response. All
        failures are converted into progress/complete events rather than raised,
        because the HTTP status is already committed once streaming begins.
        """

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

        initial_state = self._initial_state(
            conversation_id=conversation_id,
            request=request,
            owner_id=owner_id,
        )
        final_state: ConversationState = initial_state
        emitted = 0
        try:
            for state in self.graph.stream(initial_state, stream_mode="values"):
                final_state = state
                emitted = yield from self._emit_new_trace(state, emitted)
        except GeneratorExit:
            # The client disconnected (e.g. pressed Stop); record a cancellation
            # so the stored transcript stays consistent, then propagate.
            self._append_cancellation_message(conversation_id, owner_id)
            raise
        except Exception as ex:  # pylint: disable=broad-except
            final_state, error_event = self._error_state(initial_state, final_state, ex)
            yield _progress_event(error_event)

        assistant_message = self._assistant_message_from_state(final_state)
        conversation = self.conversation_store.append(
            conversation_id,
            assistant_message,
            owner_id=owner_id,
        )
        status = self._status_from_state(final_state)
        response = ConversationTurnResponse(
            status=status,
            conversation_id=conversation_id,
            message=assistant_message,
            artifacts=assistant_message.artifacts,
            trace=final_state.get("trace", []),
            timeline=self._turn_timeline(final_state),
            conversation=conversation,
        )
        yield {"type": "complete", "response": response}

    def _emit_new_trace(
        self,
        state: ConversationState,
        emitted: int,
    ) -> Generator[dict[str, Any], None, int]:
        """Yield a ``progress`` event for each trace entry not yet streamed.

        Returns the updated count of emitted trace entries.
        """

        trace = state.get("trace", [])
        while emitted < len(trace):
            yield _progress_event(trace[emitted], attempt_index_at(trace, emitted))
            emitted += 1
        return emitted

    @staticmethod
    def _error_state(
        initial_state: ConversationState,
        final_state: ConversationState,
        ex: Exception,
    ) -> tuple[ConversationState, TraceEvent]:
        error_event = TraceEvent(
            step="conversation_error",
            status="error",
            summary=str(ex),
        )
        return (
            {
                **initial_state,
                "error": str(ex),
                "trace": [*final_state.get("trace", []), error_event],
            },
            error_event,
        )

    def _append_cancellation_message(
        self,
        conversation_id: str,
        owner_id: str,
    ) -> None:
        self.conversation_store.append(
            conversation_id,
            ConversationMessage(role="assistant", content="Generation cancelled."),
            owner_id=owner_id,
        )

    def _compile_graph(self) -> Any:
        graph = StateGraph(ConversationState)
        graph.add_node("load_conversation", self._load_conversation)
        graph.add_node("classify_intent", self._classify_intent)
        graph.add_node("answer_directly", self._answer_directly)
        graph.add_node("load_context", self._load_context)
        graph.add_node("load_wren_context", self._load_wren_context)
        graph.add_node("draft_response", self._draft_response)
        graph.add_node("dry_plan_with_wren", self._dry_plan_with_wren)
        graph.add_node("plan_semantic_sql", self._plan_semantic_sql)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("repair_sql", self._repair_sql)
        graph.add_node("correct_semantic_sql", self._correct_semantic_sql)
        graph.add_node("execute_sql", self._execute_sql)
        graph.add_node("build_artifacts", self._build_artifacts)
        graph.add_node("reflect_sql_outcome", self._reflect_sql_outcome)

        graph.set_entry_point("load_conversation")
        graph.add_edge("load_conversation", "classify_intent")
        # Intent routing short-circuit (RO1a, gated): general/clarify answers
        # directly and skips context-load + the SQL path. Default routes through.
        graph.add_conditional_edges(
            "classify_intent",
            self._route_after_intent,
            {
                "answer": "answer_directly",
                "continue": "load_context",
            },
        )
        graph.add_edge("answer_directly", END)
        graph.add_edge("load_context", "load_wren_context")
        graph.add_edge("load_wren_context", "draft_response")
        graph.add_conditional_edges(
            "draft_response",
            self._route_after_draft,
            {
                "validate": "dry_plan_with_wren",
                "end": END,
            },
        )
        graph.add_edge("dry_plan_with_wren", "plan_semantic_sql")
        graph.add_edge("plan_semantic_sql", "validate_sql")
        graph.add_conditional_edges(
            "validate_sql",
            self._route_after_validation,
            {
                "repair": "repair_sql",
                "correct": "correct_semantic_sql",
                "execute": "execute_sql",
                "end": END,
            },
        )
        graph.add_edge("repair_sql", "plan_semantic_sql")
        graph.add_edge("correct_semantic_sql", "plan_semantic_sql")
        graph.add_conditional_edges(
            "execute_sql",
            self._route_after_execution,
            {
                "build": "build_artifacts",
                "reflect": "reflect_sql_outcome",
                "end": END,
            },
        )
        graph.add_edge("build_artifacts", "reflect_sql_outcome")
        graph.add_conditional_edges(
            "reflect_sql_outcome",
            self._route_after_reflection,
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

    def _classify_intent(self, state: ConversationState) -> ConversationState:
        """Classify question intent and stash it as a model hint (gated, RO1).

        Off by default; when enabled, the label is passed to the conversation
        model as a hint (see ``_call_conversation_model``). Approved-SQL turns and
        the disabled path are no-ops. Fails closed to ``text_to_sql``.
        """

        request = state["request"]
        if not self.config.wren_intent_classification_enabled or request.approved_sql:
            return state
        result = classify_intent(self.model_client, request.message)
        return {
            **state,
            "intent": result.intent,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="classify_intent",
                    summary=f"Classified intent as {result.intent}.",
                    details={"intent": result.intent, "reason": result.reason},
                ),
            ],
        }

    def _route_after_intent(self, state: ConversationState) -> str:
        """Route a classified non-SQL intent to a direct answer (RO1a, gated)."""

        if (
            self.config.wren_intent_routing_enabled
            and not state["request"].approved_sql
            and state.get("intent") in {"general", "clarify"}
        ):
            return "answer"
        return "continue"

    def _answer_directly(self, state: ConversationState) -> ConversationState:
        """Answer a general/clarify turn without loading schema context or SQL.

        Uses only the conversation history + the intent label, so the expensive
        context-load + MDL materialization + SQL machinery are skipped entirely.
        """

        request = state["request"]
        intent = state.get("intent") or "general"
        conversation = state["conversation"]
        prompt = get_prompt("conversation")
        payload = {
            "user_message": request.message,
            "intent": intent,
            "direct_answer_mode": True,
            "instruction": (
                f"This message was classified as '{intent}'. Respond directly "
                "without SQL: answer the general/capability question, or ask one "
                "concise clarifying question. Do not generate or reference SQL."
            ),
            "conversation": _conversation_payload(
                conversation,
                max_history_messages=self.config.max_history_messages,
                max_prompt_result_rows=self.config.max_prompt_result_rows,
            ),
        }
        draft = self._direct_answer_draft(request, payload, prompt)
        return {
            **state,
            "draft": draft,
            "validation": None,
            "execution_result": None,
            "pending_artifact": None,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="answer_directly",
                    summary=f"Answered {intent} intent directly (no SQL).",
                    details={"intent": intent},
                ),
            ],
        }

    def _direct_answer_draft(
        self,
        request: ConversationTurnRequest,
        payload: dict[str, Any],
        prompt: str,
    ) -> ConversationDraft:
        result = self.model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Answer this conversation turn directly without SQL.\n"
                        f"{json.dumps(payload, default=str)}"
                    ),
                ),
            ],
            model=request.model,
            format_schema=ConversationDraft.model_json_schema(),
        )
        try:
            draft = ConversationDraft.model_validate(json.loads(result.content))
        except Exception:  # pylint: disable=broad-except
            return ConversationDraft(
                response_type="answer",
                message=result.content or "Could you clarify what you need?",
                sql="",
            )
        # Force an answer shape — this path never executes SQL.
        return draft.model_copy(update={"response_type": "answer", "sql": ""})

    def _inferred_scope(self, state: ConversationState) -> ConversationScope:
        """Project-wins schema inference for the turn's scope (backend-only).

        When a project is pinned (this turn's scope or the conversation's stable
        pin) but the turn carries no/a different tab schema, ground on the
        project's schema(s) — the full set for a multi-schema project. Selects
        *context*, not *access* (per-schema context-load stays Superset-gated).
        Applied once here and propagated on ``state['request']`` so the gate and
        grounding in ``_load_wren_context`` see the inferred scope too.
        """

        scope = state["request"].scope
        conversation = state.get("conversation")
        project_id = scope.project_id or (
            conversation.project_id if conversation is not None else None
        )
        schema_name, schema_names = resolve_effective_schema(
            semantic_project_store=self.semantic_project_store,
            owner_id=state["owner_id"],
            database_id=scope.database_id,
            schema_name=scope.schema_name,
            project_id=project_id,
        )
        if (
            schema_name == scope.schema_name
            and schema_names == scope.effective_schema_names
        ):
            return scope
        return scope.model_copy(
            update={"schema_name": schema_name, "schema_names": schema_names}
        )

    def _load_context(self, state: ConversationState) -> ConversationState:
        request = state["request"]
        scope = self._inferred_scope(state)
        if scope is not request.scope:
            # Propagate the inferred scope so the gate + grounding downstream
            # (``_load_wren_context``) ground on the same schema set.
            request = request.model_copy(update={"scope": scope})
        agent_request = AgentQueryRequest(
            question=request.message,
            database_id=scope.database_id,
            catalog_name=scope.catalog_name,
            schema_name=scope.schema_name,
            schema_names=scope.schema_names,
            dataset_ids=scope.dataset_ids,
            execute=request.resolved_execution_mode() != "manual",
            model=request.model,
            max_steps=min(request.max_steps, 12),
        )
        context = self.context_provider.get_context(agent_request)
        retrieval = getattr(self.context_provider, "last_retrieval", None)
        retrieval_artifact = retrieval.retrieval if retrieval is not None else None
        details = {
            "dataset_count": len(context.datasets),
            "database_name": context.database.name,
            "retrieval": (
                retrieval_artifact.model_dump()
                if retrieval_artifact is not None
                else None
            ),
        }
        return {
            **state,
            "request": request,
            "context": context,
            "wren_retrieval": retrieval_artifact,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="load_context",
                    summary=(
                        f"Loaded {len(context.datasets)} dataset(s) from "
                        f"database {context.database.name}."
                    ),
                    details=details,
                ),
            ],
        }

    def _resolve_semantic_grounding(
        self,
        state: ConversationState,
        request: ConversationTurnRequest,
    ) -> tuple[SemanticProject, WrenMaterializationResult, list[str]] | None:
        """Resolve the grounding project and pin it onto the conversation.

        Resolution order (F1/F2): an explicit scope pin wins; else the project
        already pinned to this conversation (stable across turns); else the
        heuristic most-recent match. The resolver re-checks access + schema
        coverage, so a stale/unauthorized pin degrades to the heuristic. The
        resolved project is recorded on the conversation so later turns reuse it
        deterministically instead of re-racing the most-recent match.
        """

        conversation = state.get("conversation")
        pinned_project_id = conversation.project_id if conversation else None
        requested_project_id = request.scope.project_id or pinned_project_id
        materialized = materialize_request_semantic_project(
            config=self.config,
            semantic_project_store=self.semantic_project_store,
            mdl_file_store=self.mdl_file_store,
            owner_id=state["owner_id"],
            database_id=request.scope.database_id,
            catalog_name=request.scope.catalog_name,
            schema_name=request.scope.schema_name,
            project_id=requested_project_id,
        )
        if materialized is None:
            return None
        project, _materialization, _warnings = materialized
        if conversation is not None and conversation.project_id != project.id:
            self.conversation_store.update_project_id(
                state["conversation_id"],
                project.id,
                owner_id=state["owner_id"],
            )
        return materialized

    def _load_wren_context(self, state: ConversationState) -> ConversationState:
        request = state["request"]
        context = state["context"]
        if self.config.wren_require_schema_scope and not request.scope.schema_name:
            wren_context = WrenContextArtifact(
                enabled=self.config.wren_enabled,
                available=False,
                warnings=[
                    "Select a semantic-layer project or a database schema before "
                    "loading Wren context."
                ],
            )
            return {
                **state,
                "wren_context": wren_context,
                "trace": [
                    *state.get("trace", []),
                    TraceEvent(
                        step="load_wren_context",
                        status="warning",
                        summary="Wren context requires a selected project or schema.",
                        details=wren_context.model_dump(),
                    ),
                ],
            }
        materialization = None
        project = None
        project_id = None
        mdl_path = None
        resolve_warnings: list[str] = []
        try:
            materialized = self._resolve_semantic_grounding(state, request)
            if materialized is not None:
                project, materialization, resolve_warnings = materialized
                project_id = project.id
                mdl_path = materialization.path
            wren_context = self.wren_client.fetch_context(
                question=request.message,
                superset_context=context,
                mdl_path=mdl_path,
            )
        except Exception as ex:  # pylint: disable=broad-except
            wren_context = WrenContextArtifact(
                enabled=self.config.wren_enabled,
                available=False,
                warnings=[str(ex)],
            )
            status: Literal["ok", "warning", "error"] = "warning"
        else:
            status = "ok" if wren_context.available else "warning"
        if materialization is not None:
            warnings = [*wren_context.warnings, *resolve_warnings]
            if materialization.file_count == 0:
                warnings.append("Semantic project has no active MDL files.")
            wren_context = wren_context.model_copy(
                update={
                    "project_id": project_id,
                    "mdl_path": materialization.path,
                    "materialized_file_count": materialization.file_count,
                    "materialized_checksum": materialization.checksum,
                    "warnings": warnings,
                }
            )
        retrieved_items = retrieve_mdl_context(
            config=self.config,
            retriever=self.retriever,
            question=request.message,
            project_id=project_id,
            owner_id=state["owner_id"],
            mdl_file_store=self.mdl_file_store,
        )
        if retrieved_items:
            wren_context = wren_context.model_copy(
                update={
                    "context_items": [
                        *wren_context.context_items,
                        *retrieved_items,
                    ],
                    "retrieval_mode": retrieved_items[0]["retriever"],
                    "retrieved_item_count": len(retrieved_items),
                }
            )
        # Dedup + bound the merged context across all sources (R-RET-E).
        wren_context = wren_context.model_copy(
            update={
                "context_items": cap_context_items(
                    wren_context.context_items, self.config.wren_max_context_items
                )
            }
        )
        retrieval_artifact = state.get("wren_retrieval")
        if retrieval_artifact is not None and project_id is not None:
            retrieval_artifact = retrieval_artifact.model_copy(
                update={"project_id": project_id}
            )
            wren_context = wren_context.model_copy(
                update={"retrieval": retrieval_artifact}
            )
        elif retrieval_artifact is not None:
            wren_context = wren_context.model_copy(
                update={"retrieval": retrieval_artifact}
            )
        status = "ok" if wren_context.available else status
        return {
            **state,
            "wren_context": wren_context,
            "wren_retrieval": retrieval_artifact,
            "wren_materialization": materialization,
            "wren_mdl_path": mdl_path,
            "recall_access": self._recall_access(request, project),
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="load_wren_context",
                    status=status,
                    summary=(
                        "Loaded Wren semantic context."
                        if wren_context.available
                        else "Wren semantic context is unavailable."
                    ),
                    details=wren_context.model_dump(),
                ),
            ],
        }

    def _recall_access(
        self, request: ConversationTurnRequest, project: SemanticProject | None
    ) -> RecallAccess | None:
        """Build the F2 recall access set across the project's full schema set (R1).

        See ``Graph._recall_access``: lists the user's reachable tables per project
        schema so a cross-schema golden/memory pair passes the Stage-A access filter.
        Returns ``None`` when recall is inert so the draft node falls back.
        """

        scope = request.scope
        schema_names = (
            project.schema_names
            if project is not None and project.schema_names
            else scope.effective_schema_names
        )
        recall_inert = project is None and self.config.wren_memory_store == "none"
        if recall_inert or not schema_names:
            return None
        cap = max(
            self.config.wren_schema_table_scan_limit,
            self.config.wren_schema_table_candidate_limit,
            self.config.max_context_datasets,
        )
        return load_recall_access(
            self.superset_client,
            database_id=scope.database_id,
            catalog_name=scope.catalog_name,
            schema_names=schema_names,
            limit=cap,
        )

    def _draft_response(self, state: ConversationState) -> ConversationState:
        request = state["request"]
        approved_sql = request.approved_sql
        if approved_sql and state.get("sql_iterations", 0) == 0:
            draft = ConversationDraft(
                response_type="sql",
                message="Executing approved SQL.",
                sql=approved_sql,
                explanation="Approved SQL from the chat artifact.",
            )
            return {
                **state,
                "draft": draft,
                "validation": None,
                "execution_result": None,
                "pending_artifact": None,
                "trace": [
                    *state.get("trace", []),
                    TraceEvent(
                        step="approved_sql",
                        summary="Using approved SQL artifact for execution.",
                    ),
                ],
            }

        if state.get("recalled_examples"):
            recalled = state["recalled_examples"]
        else:
            k = self.config.wren_memory_recall_k
            # Prefer the project-wide, access-filtered recall set (R1); fall back to
            # the single-schema grounding datasets only when it could not be built.
            access = state.get("recall_access") or build_recall_access(
                state["context"].datasets
            )
            memory_pairs = self.memory.recall_examples(
                request.message,
                database_id=request.scope.database_id,
                k=k,
                access=access,
            )
            golden_pairs = recall_golden_queries(
                mdl_file_store=self.mdl_file_store,
                project_id=getattr(state.get("wren_context"), "project_id", None)
                or request.scope.project_id,
                owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
                question=request.message,
                k=k,
                embedder=getattr(self.memory, "embedder", None),
                access=access,
            )
            recalled = [
                pair.model_dump()
                for pair in merge_recalled_examples(golden_pairs, memory_pairs, k)
            ]
        draft = self._call_conversation_model(
            state={**state, "recalled_examples": recalled},
            validation_errors=[],
        )
        # Stamp how many learned examples were recalled so the UI can badge it.
        wren_context = state.get("wren_context")
        if wren_context is not None:
            wren_context = wren_context.model_copy(
                update={"recalled_example_count": len(recalled)}
            )
        return {
            **state,
            "draft": draft,
            "recalled_examples": recalled,
            "wren_context": wren_context,
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
                        "recalled_examples": compact_recalled_examples(recalled),
                    },
                ),
            ],
        }

    def _dry_plan_with_wren(
        self,
        state: ConversationState,
    ) -> ConversationState:
        if not self.config.wren_dry_plan_enabled:
            return state
        draft = state["draft"]
        request = state["request"]
        if self.config.wren_require_schema_scope and not request.scope.schema_name:
            return state
        try:
            dry_plan = self.wren_client.dry_plan(
                question=request.message,
                sql=draft.sql,
                context=state["context"],
                mdl_path=state.get("wren_mdl_path"),
            )
        except Exception as ex:  # pylint: disable=broad-except
            dry_plan = {"error": str(ex), "planning_only": True}
            status: Literal["ok", "warning", "error"] = "warning"
        else:
            status = "ok" if dry_plan.get("available", True) else "warning"

        wren_context = (
            state.get("wren_context") or WrenContextArtifact(enabled=True)
        ).model_copy(update={"dry_plan": dry_plan})
        return {
            **state,
            "wren_context": wren_context,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="dry_plan_with_wren",
                    status=status,
                    summary="Collected Wren dry-plan metadata.",
                    details=dry_plan,
                ),
            ],
        }

    def _plan_semantic_sql(self, state: ConversationState) -> ConversationState:
        """Rewrite the draft's semantic SQL into native SQL via the engine."""

        draft = state["draft"]
        sql = draft.sql or ""
        # Pre-approved SQL is the user-confirmed final native query; never send it
        # back through the engine rewrite. Also a no-op for passthrough/empty SQL.
        is_approved = bool(state["request"].approved_sql)
        if self.semantic_engine.name == "passthrough" or not sql or is_approved:
            return {
                **state,
                "semantic_sql": sql,
                "native_sql": sql,
                "engine": self.semantic_engine.name if not is_approved else "approved",
                "engine_correctable_warnings": [],
            }

        result = plan_semantic_sql_step(
            self.semantic_engine,
            sql=sql,
            context=state["context"],
            owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
            project_id=getattr(state.get("wren_context"), "project_id", None),
            mdl_file_store=self.mdl_file_store,
        )
        status: Literal["ok", "warning", "error"] = (
            "warning" if result.warnings else "ok"
        )
        return {
            **state,
            "draft": draft.model_copy(update={"sql": result.native_sql}),
            "semantic_sql": result.semantic_sql,
            "native_sql": result.native_sql,
            "engine": result.engine,
            "engine_warnings": result.warnings,
            "engine_correctable_warnings": result.correctable_warnings,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="plan_semantic_sql",
                    status=status,
                    summary=(
                        "Rewrote semantic SQL to native SQL."
                        if result.rewritten
                        else "Semantic engine returned SQL unchanged."
                    ),
                    details={
                        "engine": result.engine,
                        "rewritten": result.rewritten,
                        "semantic_sql": result.semantic_sql,
                        "native_sql": result.native_sql,
                        "referenced_tables": result.referenced_tables,
                        "warnings": result.warnings,
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
            policy_mode=self.config.sql_policy_mode,
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
                    else f"SQL blocked: {validation.reason or 'not read-only'}"
                ),
                details={
                    "classification": validation.classification,
                    "reason": validation.reason,
                    "errors": validation.errors,
                    "dialect": dialect,
                },
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

    def _build_artifacts(self, state: ConversationState) -> ConversationState:
        result = state.get("execution_result")
        if result is None:
            return state

        request = state["request"]
        bundle = build_artifact_bundle(
            question=request.message,
            result=result,
            row_limit=self.config.default_sql_limit,
        )
        analysis = profile_result(
            result,
            question=request.message,
            row_limit=self.config.default_sql_limit,
        )
        chart_spec = infer_chart_spec(
            question=request.message,
            result=result,
            analysis=analysis,
        )
        trace = [
            *state.get("trace", []),
            TraceEvent(
                step="build_artifacts",
                summary="Built conversational analytics artifacts.",
                details={
                    "insight_card_count": len(bundle.insight_cards),
                    "chart_type": chart_spec.type if chart_spec else None,
                    "has_data_preview": bundle.data_preview is not None,
                },
            ),
        ]
        audit = with_engine_provenance(
            result.audit,
            engine=state.get("engine"),
            semantic_sql=state.get("semantic_sql"),
            native_sql=state.get("native_sql"),
        )
        artifacts = list(state.get("artifacts", []))
        if artifacts:
            artifacts[-1] = artifacts[-1].model_copy(
                update={
                    "answer_summary": bundle.answer_summary,
                    "insight_cards": bundle.insight_cards,
                    "chart_spec": chart_spec,
                    "data_preview": bundle.data_preview,
                    "audit": audit,
                    "recommended_followups": bundle.recommended_followups,
                    "wren_context": state.get("wren_context"),
                    "trace": trace,
                }
            )
        return {
            **state,
            "answer_summary": bundle.answer_summary,
            "insight_cards": bundle.insight_cards,
            "chart_spec": chart_spec,
            "data_preview": bundle.data_preview,
            "audit": audit,
            "recommended_followups": bundle.recommended_followups,
            "artifacts": artifacts,
            "trace": trace,
        }

    def _repair_sql(self, state: ConversationState) -> ConversationState:
        validation = state.get("validation")
        errors = validation.errors if validation else ["SQL failed validation."]
        # Fold semantic-engine feedback into the repair prompt (1.4).
        errors = [*errors, *state.get("engine_warnings", [])]
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

        sql = validation.normalized_sql
        sql_key = _sql_match_key(sql)
        attempted_sql = state.get("attempted_sql", [])
        if sql_key in attempted_sql:
            trace = [
                *state.get("trace", []),
                TraceEvent(
                    step="duplicate_sql",
                    status="warning",
                    summary="Skipped a duplicate SQL attempt.",
                    details={"sql": sql},
                ),
            ]
            return {
                **state,
                "execution_result": None,
                "pending_artifact": None,
                "sql_iterations": state.get("sql_iterations", 0) + 1,
                "sql_observations": [
                    *state.get("sql_observations", []),
                    _execution_observation(
                        sql=sql,
                        result=None,
                        max_prompt_result_rows=self.config.max_prompt_result_rows,
                        error=(
                            "The same SQL was already attempted in this turn. "
                            "A retry must use a materially different query."
                        ),
                        is_duplicate=True,
                    ),
                ],
                "trace": trace,
            }

        try:
            result = self.superset_client.execute_sql(
                database_id=request.scope.database_id,
                sql=sql,
                catalog_name=request.scope.catalog_name,
                schema_name=request.scope.schema_name,
                limit=self.config.default_sql_limit,
                source=SqlExecutionSource(
                    source=(
                        "ai_agent_manual"
                        if request.approved_sql
                        else "ai_agent_conversation"
                    ),
                    request_id=uuid4().hex,
                    conversation_id=state.get("conversation_id"),
                    artifact_id=(
                        request.approved_artifact_id
                        or (
                            state["pending_artifact"].id
                            if state.get("pending_artifact")
                            else None
                        )
                    ),
                    sql_editor_id=request.scope.query_editor_id,
                ),
            )
        except Exception as ex:  # pylint: disable=broad-except
            error = str(ex)
            trace = [
                *state.get("trace", []),
                TraceEvent(
                    step="execute_sql",
                    status="error",
                    summary="SQL execution failed.",
                    # Record the SQL that failed so the client can attribute this
                    # error to the artifact that produced it. The turn-level trace
                    # is cumulative, so a later retry artifact inherits this event;
                    # without the SQL the UI cannot tell the failed draft apart
                    # from a fresh, never-executed retry draft.
                    details={"error": error, "sql": sql},
                ),
            ]
            pending_artifact = state.get("pending_artifact")
            artifact = None
            if pending_artifact:
                artifact = pending_artifact.model_copy(update={"trace": trace})
            return {
                **state,
                "execution_result": None,
                "artifacts": (
                    [*state.get("artifacts", []), artifact]
                    if artifact
                    else state.get("artifacts", [])
                ),
                "pending_artifact": None,
                "attempted_sql": [*attempted_sql, sql_key],
                "sql_iterations": state.get("sql_iterations", 0) + 1,
                "sql_observations": [
                    *state.get("sql_observations", []),
                    _execution_observation(
                        sql=sql,
                        result=None,
                        max_prompt_result_rows=self.config.max_prompt_result_rows,
                        error=error,
                    ),
                ],
                "trace": trace,
            }

        trace = [
            *state.get("trace", []),
            TraceEvent(
                step="execute_sql",
                summary=f"Executed SQL and returned {result.row_count} row(s).",
                details={"row_count": result.row_count},
            ),
        ]
        pending_artifact = state.get("pending_artifact")
        artifact = ConversationArtifact(
            sql=sql,
            explanation=state["draft"].explanation,
            validation=validation,
            execution_result=result,
            trace=trace,
        )
        if pending_artifact:
            artifact = pending_artifact.model_copy(
                update={"execution_result": result, "trace": trace}
            )

        # Learning loop: store the confirmed NL->SQL pair for future recall. Skip
        # approved-SQL turns — their message ("Execute selected SQL.") is not a
        # natural-language question and would pollute recall (RV4).
        if not request.approved_sql:
            try:
                native_sql = state.get("native_sql") or sql
                referenced_tables, referenced_schemas = refs_from_sql(native_sql)
                self.memory.store_confirmed(
                    question=request.message,
                    semantic_sql=state.get("semantic_sql") or sql,
                    native_sql=native_sql,
                    database_id=request.scope.database_id,
                    created_by=state.get("owner_id", DEFAULT_OWNER_ID),
                    project_id=getattr(state.get("wren_context"), "project_id", None),
                    referenced_tables=referenced_tables,
                    referenced_schemas=referenced_schemas,
                    result_meta={"row_count": result.row_count},
                )
            except Exception as ex:  # pylint: disable=broad-except - best-effort
                logger.warning("Failed to store learning-loop example: %s", ex)

        return {
            **state,
            "execution_result": result,
            "artifacts": [*state.get("artifacts", []), artifact],
            "pending_artifact": None,
            "attempted_sql": [*attempted_sql, sql_key],
            "sql_iterations": state.get("sql_iterations", 0) + 1,
            "sql_observations": [
                *state.get("sql_observations", []),
                _execution_observation(
                    sql=sql,
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
            # Engine-feedback correction (1.4, symmetric with the one-shot graph):
            # valid native SQL can still reference a hallucinated model the gate
            # flagged; re-draft if a correction budget remains. Default 0 → execute.
            if (
                not state["request"].approved_sql
                and state.get("engine_correctable_warnings")
                and state.get("engine_correction_attempts", 0)
                < self.config.wren_engine_max_correction_retries
            ):
                return "correct"
            return "execute" if self._can_execute_sql(state) else "end"
        if state["request"].approved_sql:
            return "end"
        if state.get("repair_attempts", 0) < self.config.max_repair_attempts:
            return "repair"
        return "end"

    def _correct_semantic_sql(self, state: ConversationState) -> ConversationState:
        """Re-draft on the engine's hallucination feedback (1.4), then re-plan.

        Distinct from ``_repair_sql`` (invalid SQL): validation passed but the
        engine flagged unknown models/tables. Bounded by
        ``wren_engine_max_correction_retries``.
        """

        warnings = state.get("engine_correctable_warnings", [])
        attempt = state.get("engine_correction_attempts", 0) + 1
        draft = self._call_conversation_model(state=state, validation_errors=warnings)
        return {
            **state,
            "draft": draft,
            "engine_correction_attempts": attempt,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="correct_semantic_sql",
                    status="warning",
                    summary=(
                        f"Re-drafted semantic SQL (correction attempt {attempt}) "
                        "from engine feedback."
                    ),
                    details={"warnings": warnings, "attempt": attempt},
                ),
            ],
        }

    def _reflect_sql_outcome(self, state: ConversationState) -> ConversationState:
        reflection = self._call_sql_reflection_model(state=state)
        remaining_iterations = max(
            self.config.max_agent_sql_iterations - state.get("sql_iterations", 0),
            0,
        )
        if reflection.outcome == "retry" and remaining_iterations <= 0:
            reflection = reflection.model_copy(
                update={
                    "outcome": "clarify",
                    "message": (
                        reflection.message
                        or "The SQL attempts did not produce enough usable data."
                    ),
                }
            )

        draft = state.get("draft") or ConversationDraft(
            response_type="answer",
            message=reflection.message,
            sql="",
            explanation=None,
        )
        if reflection.outcome in {"answer", "clarify"}:
            draft = ConversationDraft(
                response_type="answer",
                message=reflection.message,
                sql="",
                explanation=None,
            )

        return {
            **state,
            "draft": draft,
            "sql_reflection": reflection,
            "reflection_feedback": reflection.retry_feedback,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="reflect_sql_outcome",
                    status="warning" if reflection.outcome == "retry" else "ok",
                    summary=f"SQL reflection selected {reflection.outcome}.",
                    details={
                        "outcome": reflection.outcome,
                        "remaining_sql_iterations": remaining_iterations,
                        "retry_feedback": reflection.retry_feedback,
                    },
                ),
            ],
        }

    @staticmethod
    def _route_after_execution(state: ConversationState) -> str:
        if state.get("error"):
            return "end"
        if state.get("execution_result") is not None:
            return "build"
        return "reflect"

    def _route_after_reflection(self, state: ConversationState) -> str:
        reflection = state.get("sql_reflection")
        if (
            reflection
            and reflection.outcome == "retry"
            and state.get("sql_iterations", 0) < self.config.max_agent_sql_iterations
        ):
            return "draft"
        return "end"

    def _can_execute_sql(self, state: ConversationState) -> bool:
        """Single execution gate: bounded retries + the policy decision (R4/R5).

        The tier/approval decision is delegated to ``sql_policy.decide`` so the
        read-only invariant lives in one tested place: a non-read-only
        classification can never auto-run, and human approval (``approved_sql``
        on the first iteration) runs only read-only SQL — it never promotes a
        mutating/opaque/multi statement.
        """

        validation = state.get("validation")
        if not validation:
            return False
        if state.get("sql_iterations", 0) >= self.config.max_agent_sql_iterations:
            return False
        request = state["request"]
        approved = bool(request.approved_sql) and state.get("sql_iterations", 0) == 0
        classification = SqlClassification(
            kind=validation.classification,
            reason=validation.reason or "",
        )
        return decide(
            classification,
            tier=request.resolved_execution_mode(),
            approved=approved,
        ).allow

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
        wren_context = state.get("wren_context")
        semantic_sql_mode = (
            self.config.wren_semantic_sql_enabled
            and self.semantic_engine.name != "passthrough"
        )
        payload = {
            "user_message": request.message,
            "semantic_sql_mode": semantic_sql_mode,
            "semantic_sql_instructions": (
                _SEMANTIC_SQL_GUIDANCE if semantic_sql_mode else None
            ),
            "execution_mode": execution_mode,
            "execute": execution_mode != "manual",
            "max_sql_iterations": self.config.max_agent_sql_iterations,
            "remaining_sql_iterations": max(
                self.config.max_agent_sql_iterations - state.get("sql_iterations", 0),
                0,
            ),
            "sql_observations": state.get("sql_observations", []),
            "attempted_sql": state.get("attempted_sql", []),
            "reflection_feedback": state.get("reflection_feedback"),
            "database": context.database.model_dump(),
            "datasets": [dataset.model_dump() for dataset in context.datasets],
            "wren_context": wren_context.model_dump() if wren_context else None,
            "conversation": _conversation_payload(
                conversation,
                max_history_messages=self.config.max_history_messages,
                max_prompt_result_rows=self.config.max_prompt_result_rows,
            ),
            "scope": request.scope.model_dump(),
            "validation_errors_to_fix": validation_errors,
            "recalled_examples": state.get("recalled_examples", []),
            "intent": state.get("intent"),
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

    def _call_sql_reflection_model(
        self,
        *,
        state: ConversationState,
    ) -> SqlReflection:
        request = state["request"]
        context = state["context"]
        conversation = state["conversation"]
        prompt = get_prompt("sql_reflection")
        execution_mode: ExecutionMode = request.resolved_execution_mode()
        remaining_iterations = max(
            self.config.max_agent_sql_iterations - state.get("sql_iterations", 0),
            0,
        )
        validation = state.get("validation")
        draft = state.get("draft")
        wren_context = state.get("wren_context")
        latest_sql = (
            validation.normalized_sql if validation else draft.sql if draft else ""
        )
        payload = {
            "user_message": request.message,
            "execution_mode": execution_mode,
            "remaining_sql_iterations": remaining_iterations,
            "sql_observations": state.get("sql_observations", []),
            "attempted_sql": state.get("attempted_sql", []),
            "latest_sql": latest_sql,
            "database": context.database.model_dump(),
            "datasets": [dataset.model_dump() for dataset in context.datasets],
            "wren_context": wren_context.model_dump() if wren_context else None,
            "conversation": _conversation_payload(
                conversation,
                max_history_messages=self.config.max_history_messages,
                max_prompt_result_rows=self.config.max_prompt_result_rows,
            ),
            "scope": request.scope.model_dump(),
        }
        schema = SqlReflection.model_json_schema()
        result = self.model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Review the latest SQL execution outcome and decide "
                        "whether to answer, retry with a different query, or "
                        "ask for missing requirements.\n"
                        f"{json.dumps(payload, default=str)}"
                    ),
                ),
            ],
            model=request.model,
            format_schema=schema,
        )
        try:
            data = json.loads(result.content)
            reflection = SqlReflection.model_validate(data)
        except Exception as ex:  # pylint: disable=broad-except
            return _fallback_sql_reflection(state=state, error=str(ex))
        if reflection.outcome == "retry" and not reflection.retry_feedback:
            return reflection.model_copy(update={"retry_feedback": reflection.message})
        return reflection

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
        return [_with_artifact_timeline(artifact) for artifact in artifacts]

    @staticmethod
    def _turn_timeline(state: ConversationState) -> list[AgentStep]:
        return build_agent_timeline(
            state.get("trace", []),
            wren_context=state.get("wren_context"),
            audit=state.get("audit"),
            artifacts=state.get("artifacts"),
        )

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


def _with_artifact_timeline(artifact: ConversationArtifact) -> ConversationArtifact:
    """Stamp a per-artifact explain-and-audit timeline for history re-render."""

    timeline = build_agent_timeline(
        artifact.trace,
        wren_context=artifact.wren_context,
        audit=artifact.audit,
        artifacts=[artifact],
    )
    return artifact.model_copy(update={"timeline": timeline})


def _progress_event(event: TraceEvent, attempt_index: int = 0) -> dict[str, Any]:
    """Serialize a trace event as a streaming ``progress`` payload.

    Carries the legacy ``step``/``status``/``summary`` keys (the one-line progress
    bubble) plus the full typed ``agent_step`` so the explain-and-audit dialog can
    fill its sequence live, losslessly (ai_agent_explain_and_audit.md Seam 1).
    """

    step = step_from_event(event, attempt_index=attempt_index)
    return {
        "type": "progress",
        "step": step.kind,
        "status": step.status,
        "summary": step.summary,
        "agent_step": step.model_dump(mode="json"),
    }


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
    result: ExecutionResult | None,
    max_prompt_result_rows: int,
    error: str | None = None,
    is_duplicate: bool = False,
) -> dict[str, Any]:
    if result is None:
        return {
            "sql": sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": error,
            "is_duplicate": is_duplicate,
        }
    return {
        "sql": sql,
        "columns": result.columns,
        "rows": result.rows[:max_prompt_result_rows],
        "row_count": result.row_count,
        "is_empty": result.row_count == 0,
    }


def _fallback_sql_reflection(
    *,
    state: ConversationState,
    error: str,
) -> SqlReflection:
    observations = state.get("sql_observations", [])
    latest_observation = observations[-1] if observations else {}
    if latest_observation.get("error") or latest_observation.get("row_count") == 0:
        return SqlReflection(
            outcome="retry",
            message="The latest SQL attempt did not produce usable results.",
            retry_feedback=(
                "The latest SQL attempt failed, returned no rows, or repeated an "
                "earlier query. Produce a materially different read-only SQL query."
            ),
        )
    return SqlReflection(
        outcome="answer",
        message=(
            "I could not parse the SQL reflection output, but the latest SQL "
            f"observation is available. Reflection error: {error}"
        ),
    )


def _find_artifact(
    conversation: Conversation,
    *,
    artifact_id: str | None,
    sql: str,
) -> ConversationArtifact | None:
    if artifact_id:
        for message in reversed(conversation.messages):
            for artifact in reversed(message.artifacts):
                if artifact.id == artifact_id:
                    return artifact

    sql_key = _sql_match_key(sql)
    for message in reversed(conversation.messages):
        for artifact in reversed(message.artifacts):
            artifact_sql = _sql_match_key(artifact.sql)
            normalized_sql = _sql_match_key(
                artifact.validation.normalized_sql if artifact.validation else None
            )
            if sql_key in {artifact_sql, normalized_sql}:
                return artifact
    return None


def _artifact_with_execution_state(
    *,
    original_artifact: ConversationArtifact | None,
    state: ConversationState,
) -> ConversationArtifact | None:
    state_artifact = _latest_state_artifact(state)
    if state_artifact is None:
        return None
    if original_artifact is None:
        return state_artifact
    return original_artifact.model_copy(
        update={
            "sql": state_artifact.sql,
            "validation": state_artifact.validation,
            "execution_result": state_artifact.execution_result,
            "answer_summary": state_artifact.answer_summary,
            "insight_cards": state_artifact.insight_cards,
            "chart_spec": state_artifact.chart_spec,
            "data_preview": state_artifact.data_preview,
            "audit": state_artifact.audit,
            "recommended_followups": state_artifact.recommended_followups,
            "wren_context": state_artifact.wren_context,
            "trace": state_artifact.trace,
        }
    )


def _latest_state_artifact(state: ConversationState) -> ConversationArtifact | None:
    artifacts = state.get("artifacts", [])
    if artifacts:
        return artifacts[-1]
    return state.get("pending_artifact")


def _approved_sql_response_content(
    state: ConversationState,
    assistant_message: ConversationMessage,
) -> str:
    validation = state.get("validation")
    if validation and not validation.is_valid:
        errors = "\n".join(validation.errors)
        return f"SQL validation failed before execution.\n\n{errors}".strip()
    return assistant_message.content


def _sql_match_key(sql: str | None) -> str:
    return " ".join((sql or "").strip().rstrip(";").split())
