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
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from superset_ai_agent.artifacts.charts import infer_chart_spec
from superset_ai_agent.artifacts.insights import build_artifact_bundle, profile_result
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.explain import build_agent_timeline, compact_recalled_examples
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.integrations.wren.client import DisabledWrenClient, WrenClient
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.llm.embeddings import create_embedder
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
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
from superset_ai_agent.semantic_layer.instructions import (
    InstructionStore,
    NullInstructionStore,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.memory_store import Memory, NullMemory
from superset_ai_agent.semantic_layer.projects import SemanticProjectStore
from superset_ai_agent.semantic_layer.runtime import (
    build_unified_context,
    ModelSelector,
)
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    retrieve_mdl_context,
    Retriever,
)
from superset_ai_agent.semantic_layer.schemas import WrenMaterializationResult
from superset_ai_agent.semantic_layer.store import (
    instruction_scope_hash,
    scope_hash,
)
from superset_ai_agent.semantic_layer.wren_runtime import (
    materialize_request_semantic_project,
)
from superset_ai_agent.tools.sql import validate_read_only_sql

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


_TABLE_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"models": {"type": "array", "items": {"type": "string"}}},
    "required": ["models"],
}


def llm_select_models(
    model_client: ModelClient,
    question: str,
    candidates: list[str],
    limit: int,
) -> list[str] | None:
    """Ask the model to pick the relevant model subset (C1.3); ``None`` to defer.

    Returns chosen names validated against ``candidates`` (hallucinated names
    dropped), in retriever-rank order, capped to ``limit`` (when > 0). Returns
    ``None`` on a missing prompt, a provider error, or an unparseable/empty result —
    so :func:`build_unified_context` degrades closed to the heuristic selector.
    """

    if not candidates:
        return None
    try:
        prompt = get_prompt("table_selection")
    except OSError:
        return None
    payload = {
        "question": question,
        "candidate_models": candidates,
        "max_models": limit,
    }
    try:
        result = model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Select the relevant models. Return only JSON matching the "
                        f"schema.\n{json.dumps(payload, default=str)}"
                    ),
                ),
            ],
            format_schema=_TABLE_SELECTION_SCHEMA,
        )
        data = json.loads(result.content)
    except Exception:  # pylint: disable=broad-except - degrade to heuristic
        return None
    chosen = data.get("models") if isinstance(data, dict) else None
    if not isinstance(chosen, list):
        return None
    chosen_names = {str(name) for name in chosen}
    # Preserve retriever rank order; keep only real candidates; cap to the limit.
    ordered = [name for name in candidates if name in chosen_names]
    if limit > 0:
        ordered = ordered[:limit]
    return ordered or None


def dry_plan_diagnostics(dry_plan: dict[str, Any] | None) -> list[str]:
    """Actionable engine diagnostics from a Wren dry-plan, for repair (C2.2).

    The dry-plan node collects engine planning metadata once on the initial draft;
    its error signals — a hallucinated table/column the engine could not resolve, an
    unsupported expression — are exactly what a repair should address, beyond the
    read-only validator's syntactic errors. Pulls the common diagnostic shapes
    (``error`` string, ``errors`` list) defensively and degrades to ``[]`` for an
    unavailable or diagnostic-free plan. Deduped to avoid inflating the prompt.

    Note: the dry-plan runs once on the initial draft (not re-run inside the repair
    loop), so these diagnostics describe the *first* SQL — still useful guidance for
    every repair attempt.
    """

    if not isinstance(dry_plan, dict):
        return []
    raw: list[str] = []
    error = dry_plan.get("error")
    if isinstance(error, str) and error.strip():
        raw.append(error.strip())
    errors = dry_plan.get("errors")
    if isinstance(errors, list):
        for item in errors:
            text = (item if isinstance(item, str) else str(item)).strip()
            if text:
                raw.append(text)
    seen: set[str] = set()
    deduped: list[str] = []
    for text in raw:
        if text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


class SqlDraft(BaseModel):
    """Structured model output for SQL generation."""

    sql: str = Field(description="The generated read-only SQL query.")
    explanation: str = Field(description="Short explanation of the query.")


class AgentState(TypedDict, total=False):
    owner_id: str
    request: AgentQueryRequest
    context: AgentContext
    sql: str | None
    explanation: str | None
    validation: SqlValidation
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
    semantic_sql: str | None
    native_sql: str | None
    engine: str | None
    engine_warnings: list[str]
    engine_correctable_warnings: list[str]
    engine_correction_attempts: int
    recalled_examples: list[dict[str, Any]]
    instructions: list[str]
    trace: list[TraceEvent]
    repair_attempts: int
    error: str | None


class TextToSqlGraph:
    """Small LangGraph workflow for Phase 1 text-to-SQL generation."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        model_client: ModelClient,
        context_provider: ContextProvider,
        superset_client: SupersetClient,
        wren_client: WrenClient | None = None,
        semantic_project_store: SemanticProjectStore | None = None,
        mdl_file_store: MdlFileStore | None = None,
        semantic_engine: SemanticEngine | None = None,
        memory: Memory | None = None,
        retriever: Retriever | None = None,
        instruction_store: InstructionStore | None = None,
    ):
        self.config = config
        self.model_client = model_client
        self.context_provider = context_provider
        self.superset_client = superset_client
        self.wren_client = wren_client or DisabledWrenClient()
        self.semantic_project_store = semantic_project_store
        self.mdl_file_store = mdl_file_store
        self.semantic_engine = semantic_engine or create_semantic_engine(config)
        self.memory = memory or NullMemory()
        self.retriever = retriever or create_retriever(config, create_embedder(config))
        self.instruction_store = instruction_store or NullInstructionStore()
        self.graph = self._compile_graph()

    def run(
        self,
        request: AgentQueryRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> AgentQueryResponse:
        initial_state: AgentState = {
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
            "error": None,
        }
        state = self.graph.invoke(initial_state)
        validation = state.get(
            "validation",
            SqlValidation(
                is_valid=False,
                is_read_only=False,
                errors=[state.get("error") or "Agent did not produce validation."],
            ),
        )
        status: Literal["ok", "needs_review", "error"]
        if state.get("error"):
            status = "error"
        elif request.execute and state.get("execution_result") is not None:
            status = "ok"
        elif validation.is_valid:
            status = "needs_review"
        else:
            status = "error"

        trace = state.get("trace", [])
        return AgentQueryResponse(
            status=status,
            sql=state.get("sql"),
            explanation=state.get("explanation"),
            validation=validation,
            execution_result=state.get("execution_result"),
            trace=trace,
            answer_summary=state.get("answer_summary"),
            insight_cards=state.get("insight_cards", []),
            chart_spec=state.get("chart_spec"),
            data_preview=state.get("data_preview"),
            audit=state.get("audit"),
            recommended_followups=state.get("recommended_followups", []),
            wren_context=state.get("wren_context"),
            timeline=build_agent_timeline(
                trace,
                wren_context=state.get("wren_context"),
                audit=state.get("audit"),
            ),
        )

    def _compile_graph(self) -> Any:
        graph = StateGraph(AgentState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("load_wren_context", self._load_wren_context)
        graph.add_node("draft_sql", self._draft_sql)
        graph.add_node("dry_plan_with_wren", self._dry_plan_with_wren)
        graph.add_node("plan_semantic_sql", self._plan_semantic_sql)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("repair_sql", self._repair_sql)
        graph.add_node("correct_semantic_sql", self._correct_semantic_sql)
        graph.add_node("execute_sql", self._execute_sql)
        graph.add_node("build_artifacts", self._build_artifacts)

        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "load_wren_context")
        graph.add_edge("load_wren_context", "draft_sql")
        graph.add_edge("draft_sql", "dry_plan_with_wren")
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
        # Repaired/corrected drafts are re-planned through the engine before
        # validation, so the engine rewrite + hallucination gate run again.
        graph.add_edge("repair_sql", "plan_semantic_sql")
        graph.add_edge("correct_semantic_sql", "plan_semantic_sql")
        graph.add_edge("execute_sql", "build_artifacts")
        graph.add_edge("build_artifacts", END)
        return graph.compile()

    def _load_context(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = self.context_provider.get_context(request)
        retrieval = getattr(self.context_provider, "last_retrieval", None)
        retrieval_artifact = (
            retrieval.retrieval if retrieval is not None else None
        )
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

    def _load_wren_context(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = state["context"]
        if self.config.wren_require_schema_scope and not request.schema_name:
            wren_context = WrenContextArtifact(
                enabled=self.config.wren_enabled,
                available=False,
                warnings=["Select a database schema before loading Wren context."],
            )
            return {
                **state,
                "wren_context": wren_context,
                "trace": [
                    *state.get("trace", []),
                    TraceEvent(
                        step="load_wren_context",
                        status="warning",
                        summary="Wren context requires a selected schema.",
                        details=wren_context.model_dump(),
                    ),
                ],
            }
        materialization = None
        project_id = None
        mdl_path = None
        try:
            materialized = materialize_request_semantic_project(
                config=self.config,
                semantic_project_store=self.semantic_project_store,
                mdl_file_store=self.mdl_file_store,
                owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
            )
            if materialized is not None:
                project, materialization = materialized
                project_id = project.id
                mdl_path = materialization.path
            wren_context = self.wren_client.fetch_context(
                question=request.question,
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
            warnings = list(wren_context.warnings)
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
            question=request.question,
            project_id=project_id,
            owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
            mdl_file_store=self.mdl_file_store,
        )
        # R2/C1: one post-retrieval entrypoint — unify fetch_context + retriever
        # chunks, run table-selection over the *unified* set (C1.1), then dedup +
        # bound across all sources (R-RET-E). C1.3: an opt-in LLM selector picks the
        # relevant model subset, degrading closed to the heuristic.
        wren_context = build_unified_context(
            wren_context=wren_context,
            retrieved_items=retrieved_items,
            table_selection_limit=self.config.wren_table_selection_limit,
            max_context_items=self.config.wren_max_context_items,
            model_selector=self._model_selector(request.question),
        )
        retrieval_artifact = state.get("wren_retrieval")
        if retrieval_artifact is not None and project_id is not None:
            retrieval_artifact = retrieval_artifact.model_copy(
                update={"project_id": project_id}
            )
        if retrieval_artifact is not None:
            wren_context = wren_context.model_copy(
                update={"retrieval": retrieval_artifact}
            )
        return {
            **state,
            "wren_context": wren_context,
            "wren_retrieval": retrieval_artifact,
            "wren_materialization": materialization,
            "wren_mdl_path": mdl_path,
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

    def _model_selector(self, question: str) -> ModelSelector | None:
        """Build the C1.3 LLM model selector when enabled, else ``None`` (heuristic).

        The closure binds the question + model client; ``build_unified_context``
        calls it with the candidate model names and degrades closed on a ``None``.
        """

        if not self.config.wren_llm_table_selection:
            return None

        def selector(candidates: list[str]) -> list[str] | None:
            return llm_select_models(
                self.model_client,
                question,
                candidates,
                self.config.wren_table_selection_limit,
            )

        return selector

    def _request_scope(self, request: AgentQueryRequest) -> ConversationScope:
        return ConversationScope(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            dataset_ids=request.dataset_ids,
        )

    def _request_scope_hash(self, request: AgentQueryRequest) -> str:
        return scope_hash(self._request_scope(request))

    def _instruction_scope_hash(self, request: AgentQueryRequest) -> str:
        return instruction_scope_hash(self._request_scope(request))

    def _draft_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = state["context"]
        scope_hash_value = self._request_scope_hash(request)
        owner_id = state.get("owner_id", DEFAULT_OWNER_ID)
        recalled = [
            pair.model_dump()
            for pair in self.memory.recall_examples(
                request.question,
                scope_hash=scope_hash_value,
                owner_id=owner_id,
                k=self.config.wren_memory_recall_k,
            )
        ]
        instructions = [
            item.instruction
            for item in self.instruction_store.recall(
                request.question,
                # Instructions are schema-scoped (dataset selection ignored) so an
                # editor-authored instruction is recalled regardless of the query's
                # selected datasets (C5.1 fix); memory recall above stays query-scoped.
                scope_hash=self._instruction_scope_hash(request),
                owner_id=owner_id,
                k=self.config.wren_instruction_recall_k,
            )
        ]
        draft = self._call_sql_model(
            request=request,
            context=context,
            wren_context=state.get("wren_context"),
            validation_errors=[],
            recalled_examples=recalled,
            instructions=instructions,
        )
        # Stamp how many learned examples were recalled so the UI can badge it.
        wren_context = state.get("wren_context")
        if wren_context is not None:
            wren_context = wren_context.model_copy(
                update={"recalled_example_count": len(recalled)}
            )
        return {
            **state,
            "sql": draft.sql,
            "explanation": draft.explanation,
            "recalled_examples": recalled,
            "instructions": instructions,
            "wren_context": wren_context,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="draft_sql",
                    summary="Generated an initial SQL draft.",
                    details={
                        "model": request.model or self.config.default_model(),
                        "recalled_examples": compact_recalled_examples(recalled),
                    },
                ),
            ],
        }

    def _dry_plan_with_wren(self, state: AgentState) -> AgentState:
        if not self.config.wren_dry_plan_enabled:
            return state
        request = state["request"]
        if self.config.wren_require_schema_scope and not request.schema_name:
            return state
        try:
            dry_plan = self.wren_client.dry_plan(
                question=request.question,
                sql=state.get("sql"),
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

    def _plan_semantic_sql(self, state: AgentState) -> AgentState:
        """Rewrite semantic SQL into native SQL via the engine (never executes).

        The engine output replaces ``state['sql']`` so validation + Superset
        execution operate on native SQL. The passthrough engine returns SQL
        unchanged, so this is a no-op when ``wren_engine=passthrough``.
        """

        sql = state.get("sql") or ""
        if self.semantic_engine.name == "passthrough":
            # Record provenance for audit; no rewrite, no extra trace event.
            return {
                **state,
                "semantic_sql": sql,
                "native_sql": sql,
                "engine": self.semantic_engine.name,
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
            "sql": result.native_sql,
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

    def _validate_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        sql = state.get("sql") or ""
        dialect = self.superset_client.get_database_dialect(request.database_id)
        validation = validate_read_only_sql(
            sql,
            dialect=dialect,
            default_limit=self.config.default_sql_limit,
        )
        normalized_sql = validation.normalized_sql or sql
        status: Literal["ok", "warning", "error"] = (
            "ok" if validation.is_valid else "error"
        )
        return {
            **state,
            "sql": normalized_sql,
            "validation": validation,
            "trace": [
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
            ],
        }

    def _repair_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = state["context"]
        validation = state["validation"]
        # Fold semantic-engine feedback (1.4) and Wren dry-plan diagnostics (C2.2)
        # into the repair prompt — the engine's planning errors, not just the
        # read-only validator's syntax errors.
        dry_plan_errors = dry_plan_diagnostics(
            getattr(state.get("wren_context"), "dry_plan", None)
        )
        repair_errors = [
            *validation.errors,
            *state.get("engine_warnings", []),
            *dry_plan_errors,
        ]
        draft = self._call_sql_model(
            request=request,
            context=context,
            wren_context=state.get("wren_context"),
            validation_errors=repair_errors,
            recalled_examples=state.get("recalled_examples", []),
            instructions=state.get("instructions", []),
        )
        return {
            **state,
            "sql": draft.sql,
            "explanation": draft.explanation,
            "repair_attempts": state.get("repair_attempts", 0) + 1,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="repair_sql",
                    summary="Asked the model to repair invalid SQL.",
                    details={
                        "errors": validation.errors,
                        "dry_plan_diagnostics": dry_plan_errors,
                    },
                ),
            ],
        }

    def _execute_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        validation = state["validation"]
        if not validation.normalized_sql:
            return {**state, "error": "No validated SQL is available to execute."}

        try:
            result = self.superset_client.execute_sql(
                database_id=request.database_id,
                sql=validation.normalized_sql,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                limit=self.config.default_sql_limit,
                source=SqlExecutionSource(
                    source="ai_agent",
                    request_id=uuid4().hex,
                ),
            )
        except Exception as ex:  # pylint: disable=broad-except
            return {
                **state,
                "error": str(ex),
                "trace": [
                    *state.get("trace", []),
                    TraceEvent(
                        step="execute_sql",
                        status="error",
                        summary="SQL execution failed.",
                        details={"error": str(ex)},
                    ),
                ],
            }

        # Learning loop: store the confirmed NL->SQL pair for future recall.
        try:
            self.memory.store_confirmed(
                question=request.question,
                semantic_sql=state.get("semantic_sql") or validation.normalized_sql,
                native_sql=state.get("native_sql") or validation.normalized_sql,
                scope_hash=self._request_scope_hash(request),
                owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
                result_meta={"row_count": result.row_count},
            )
        except Exception as ex:  # pylint: disable=broad-except - memory is best-effort
            logger.warning("Failed to store learning-loop example: %s", ex)

        return {
            **state,
            "execution_result": result,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="execute_sql",
                    summary=f"Executed SQL and returned {result.row_count} row(s).",
                    details={"row_count": result.row_count},
                ),
            ],
        }

    def _build_artifacts(self, state: AgentState) -> AgentState:
        result = state.get("execution_result")
        if result is None:
            return state

        request = state["request"]
        bundle = build_artifact_bundle(
            question=request.question,
            result=result,
            row_limit=self.config.default_sql_limit,
        )
        analysis = profile_result(
            result,
            question=request.question,
            row_limit=self.config.default_sql_limit,
        )
        chart_spec = infer_chart_spec(
            question=request.question,
            result=result,
            analysis=analysis,
        )
        audit = with_engine_provenance(
            result.audit,
            engine=state.get("engine"),
            semantic_sql=state.get("semantic_sql"),
            native_sql=state.get("native_sql"),
        )
        return {
            **state,
            "answer_summary": bundle.answer_summary,
            "insight_cards": bundle.insight_cards,
            "chart_spec": chart_spec,
            "data_preview": bundle.data_preview,
            "audit": audit,
            "recommended_followups": bundle.recommended_followups,
            "trace": [
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
            ],
        }

    def _route_after_validation(self, state: AgentState) -> str:
        request = state["request"]
        validation = state["validation"]
        if validation.is_valid:
            # Engine-feedback correction (1.4): valid native SQL can still
            # reference a hallucinated model the gate flagged; re-draft if a
            # correction budget remains. Default budget 0 → straight to execute.
            if (
                state.get("engine_correctable_warnings")
                and state.get("engine_correction_attempts", 0)
                < self.config.wren_engine_max_correction_retries
            ):
                return "correct"
            return "execute" if request.execute else "end"
        if state.get("repair_attempts", 0) < self.config.max_repair_attempts:
            return "repair"
        return "end"

    def _correct_semantic_sql(self, state: AgentState) -> AgentState:
        """Re-draft semantic SQL using the engine's hallucination feedback (1.4).

        Distinct from ``_repair_sql`` (which fixes *invalid* SQL): here validation
        passed but the engine flagged unknown models/tables. Bounded by
        ``wren_engine_max_correction_retries``; re-planned before re-validation.
        """

        request = state["request"]
        context = state["context"]
        warnings = state.get("engine_correctable_warnings", [])
        attempt = state.get("engine_correction_attempts", 0) + 1
        draft = self._call_sql_model(
            request=request,
            context=context,
            wren_context=state.get("wren_context"),
            validation_errors=warnings,
            recalled_examples=state.get("recalled_examples", []),
            instructions=state.get("instructions", []),
        )
        return {
            **state,
            "sql": draft.sql,
            "explanation": draft.explanation,
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

    def _call_sql_model(
        self,
        *,
        request: AgentQueryRequest,
        context: AgentContext,
        wren_context: WrenContextArtifact | None,
        validation_errors: list[str],
        recalled_examples: list[dict[str, Any]] | None = None,
        instructions: list[str] | None = None,
    ) -> SqlDraft:
        prompt = get_prompt("text_to_sql")
        semantic_sql_mode = (
            self.config.wren_semantic_sql_enabled
            and self.semantic_engine.name != "passthrough"
        )
        user_payload = {
            "question": request.question,
            "database": context.database.model_dump(),
            "datasets": [dataset.model_dump() for dataset in context.datasets],
            "wren_context": (
                wren_context.model_dump() if wren_context is not None else None
            ),
            "validation_errors_to_fix": validation_errors,
            "semantic_sql_mode": semantic_sql_mode,
            "semantic_sql_instructions": (
                _SEMANTIC_SQL_GUIDANCE if semantic_sql_mode else None
            ),
            "recalled_examples": recalled_examples or [],
            # User-authored guidance (Wren `instructions`) steers generation.
            "instructions": instructions or [],
        }
        schema = SqlDraft.model_json_schema()
        result = self.model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Generate SQL for this request using the provided context.\n"
                        f"{json.dumps(user_payload, default=str)}"
                    ),
                ),
            ],
            model=request.model,
            format_schema=schema,
        )
        try:
            data = json.loads(result.content)
            return SqlDraft.model_validate(data)
        except Exception as ex:  # pylint: disable=broad-except
            return SqlDraft(
                sql="",
                explanation=f"Model did not return valid structured JSON: {ex}",
            )
