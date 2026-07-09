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
from superset_ai_agent.llm.rerank import llm_rerank
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
from superset_ai_agent.semantic_layer.dimension_values import (
    extract_quoted_literals,
    probe_dimension_values,
)
from superset_ai_agent.semantic_layer.document_retriever import (
    DocumentChunkIndex,
    retrieve_document_context,
)
from superset_ai_agent.semantic_layer.engine import (
    create_semantic_engine,
    finalization_guidance,
    guidance_enabled,
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
from superset_ai_agent.semantic_layer.instructions import (
    InstructionStore,
    NullInstructionStore,
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
from superset_ai_agent.semantic_layer.runtime import (
    build_unified_context,
    ModelSelector,
)
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    project_schema_items,
    retrieve_mdl_context,
    Retriever,
)
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.store import (
    scope_hashes,
    SemanticLayerStore,
)
from superset_ai_agent.semantic_layer.wren_runtime import (
    materialize_request_semantic_project,
    resolve_effective_schema,
)
from superset_ai_agent.tools.sql import validate_read_only_sql

logger = logging.getLogger(__name__)

#: Authoring guidance injected when semantic-SQL mode is active (engine rewrites
#: model-qualified SQL into native SQL). See wren_full.md Phase 1.3.
_SEMANTIC_SQL_GUIDANCE = (
    "Semantic-SQL mode is ON. Write SQL against the semantic models by their "
    "MDL model names (see wren_context.matched_models and context_items), "
    "referencing model columns and defined relationships. Do not "
    "hand-write physical joins for defined relationships; the semantic engine "
    "rewrites your query into native SQL. A metric is a formula, NOT a "
    "selectable column: substitute its measure expression inline (e.g. write "
    "SUM(amount) AS total_revenue), never SELECT the metric by its name. "
    "Never reference tables or columns absent from the provided semantic context."
)


def _compose_semantic_guidance(
    semantic_sql_mode: bool,
    *,
    backend: str | None,
    finalize_enabled: bool,
) -> str | None:
    """Base semantic guidance plus a per-dialect finalization addendum (D3).

    ``None`` when semantic mode is off. When the backend's SQL is finalized by a
    transpile pass (e.g. Oracle), the agent is told so it prefers portable SQL.
    """

    if not semantic_sql_mode:
        return None
    addendum = finalization_guidance(backend, enabled=finalize_enabled)
    if addendum:
        return f"{_SEMANTIC_SQL_GUIDANCE} {addendum}"
    return _SEMANTIC_SQL_GUIDANCE


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


_CANDIDATE_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": ["a", "b"]},
        "reason": {"type": "string"},
    },
    "required": ["choice"],
}


def llm_select_candidate(
    model_client: ModelClient,
    question: str,
    sql_a: str,
    sql_b: str,
    context_items: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Pairwise LLM judgment between two SQL candidates (C3).

    Returns ``(choice, reason)`` with ``choice`` in ``{"a", "b"}``. Degrades
    closed to the semantic candidate (``"a"``) on a missing prompt, provider
    error, or malformed output — the semantic layer encodes curated meaning,
    so it is the safe default.
    """

    fallback = ("a", "Judge unavailable; kept the semantic-layer candidate.")
    try:
        prompt = get_prompt("candidate_selection")
    except OSError:
        return fallback
    payload = {
        "question": question,
        "candidate_a": sql_a,
        "candidate_b": sql_b,
        "context": (context_items or [])[:10],
    }
    try:
        result = model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Pick the better candidate. Return only JSON matching "
                        f"the schema.\n{json.dumps(payload, default=str)}"
                    ),
                ),
            ],
            format_schema=_CANDIDATE_SELECTION_SCHEMA,
        )
        data = json.loads(result.content)
    except Exception:  # pylint: disable=broad-except - degrade to semantic
        return fallback
    choice = data.get("choice") if isinstance(data, dict) else None
    if choice not in ("a", "b"):
        return fallback
    reason = data.get("reason")
    return choice, reason if isinstance(reason, str) and reason.strip() else ""


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
    document_context: dict[str, Any] | None
    dimension_values: list[dict[str, Any]]
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
        semantic_layer_store: SemanticLayerStore | None = None,
        document_index: DocumentChunkIndex | None = None,
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
        # Doc-RAG channel (A1): both optional so the channel is inert (and the
        # graph unchanged) for callers that don't carry a document corpus.
        self.semantic_layer_store = semantic_layer_store
        self.document_index = document_index
        self.graph = self._compile_graph()

    def _with_inferred_schema(
        self, request: AgentQueryRequest, *, owner_id: str
    ) -> AgentQueryRequest:
        """Project-wins schema inference (backend-only).

        When the request pins a project but carries no (or a different) tab schema,
        ground on the project's schema(s) — and for a multi-schema project, on the
        **full** set (``schema_names``). Selects *context*, not *access*: the
        per-schema context-load stays Superset-gated. No-op when nothing changes.
        """

        schema_name, schema_names = resolve_effective_schema(
            semantic_project_store=self.semantic_project_store,
            owner_id=owner_id,
            database_id=request.database_id,
            schema_name=request.schema_name,
            project_id=request.project_id,
            database_uri_fingerprint=self._scope_fingerprint(
                request.database_id, request.catalog_name
            ),
        )
        if (
            schema_name == request.schema_name
            and schema_names == request.effective_schema_names
        ):
            return request
        return request.model_copy(
            update={"schema_name": schema_name, "schema_names": schema_names}
        )

    def run(
        self,
        request: AgentQueryRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> AgentQueryResponse:
        request = self._with_inferred_schema(request, owner_id=owner_id)
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
            "document_context": None,
            "dimension_values": [],
            "wren_materialization": None,
            "wren_mdl_path": None,
            "error": None,
        }
        state = self.graph.invoke(
            initial_state,
            {"recursion_limit": self.config.agent_graph_recursion_limit},
        )
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
        graph.add_node("load_document_context", self._load_document_context)
        graph.add_node("probe_dimension_values", self._probe_dimension_values)
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
        graph.add_edge("load_wren_context", "load_document_context")
        graph.add_edge("load_document_context", "probe_dimension_values")
        graph.add_edge("probe_dimension_values", "draft_sql")
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
            materialized = materialize_request_semantic_project(
                config=self.config,
                semantic_project_store=self.semantic_project_store,
                mdl_file_store=self.mdl_file_store,
                owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                project_id=request.project_id,
                database_uri_fingerprint=self._scope_fingerprint(
                    request.database_id, request.catalog_name
                ),
            )
            if materialized is not None:
                project, materialization, resolve_warnings = materialized
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
        owner_id = state.get("owner_id", DEFAULT_OWNER_ID)
        retrieved_items = retrieve_mdl_context(
            config=self.config,
            retriever=self.retriever,
            question=request.question,
            project_id=project_id,
            owner_id=owner_id,
            mdl_file_store=self.mdl_file_store,
        )
        # Join-closure source: the project's FULL active MDL (unranked), so a join
        # partner that ranked out of the retriever top-k can still be injected.
        manifest_items = project_schema_items(
            project_id=project_id,
            owner_id=owner_id,
            mdl_file_store=self.mdl_file_store,
        )
        # R2/C1: one post-retrieval entrypoint — unify fetch_context + retriever
        # chunks, run table-selection over the *unified* set (C1.1), apply
        # cross-schema join-closure, then dedup + bound across all sources
        # (R-RET-E). C1.3: an opt-in LLM selector picks the relevant model subset,
        # degrading closed to the heuristic.
        wren_context = build_unified_context(
            wren_context=wren_context,
            retrieved_items=retrieved_items,
            table_selection_limit=self.config.wren_table_selection_limit,
            max_context_items=self.config.wren_max_context_items,
            model_selector=self._model_selector(request.question),
            manifest_items=manifest_items,
            join_closure_limit=self.config.wren_join_closure_limit,
            # B4 size gate: a small project's full manifest is dumped whole —
            # below the collapse zone, pruning risks recall for no gain.
            dump_threshold_chars=self.config.wren_context_dump_char_threshold,
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

    def _load_document_context(self, state: AgentState) -> AgentState:
        """Retrieve uploaded-document passages for the question (doc RAG, A1).

        Advisory grounding: eval v4 measured the raw BI document worth ~+12/30
        over the bare semantic layer, so relevant passages are retrieved
        (budgeted) into the prompt. Structurally inert — no trace noise — when
        the channel is off or this deployment carries no document corpus; a
        trace event is emitted whenever the channel actually ran, including
        the zero-passage case, so the explain UI shows what grounded (or
        didn't ground) the draft.
        """

        request = state["request"]
        # Only the access-checked resolution from load_wren_context — never the
        # raw request pin, so an inaccessible project's documents cannot ground
        # a draft (the materialize path re-checks access + schema coverage).
        project_id = getattr(state.get("wren_context"), "project_id", None)
        if (
            not self.config.wren_sql_doc_context_enabled
            or self.semantic_layer_store is None
            or self.document_index is None
            or project_id is None
        ):
            return {**state, "document_context": None}
        document_context = retrieve_document_context(
            question=request.question,
            project_id=project_id,
            owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
            store=self.semantic_layer_store,
            index=self.document_index,
            k=self.config.wren_sql_doc_retrieve_k,
            max_chars=self.config.wren_sql_doc_context_max_chars,
            reranker=self._doc_reranker(),
        )
        wren_context = state.get("wren_context")
        if document_context and wren_context is not None:
            wren_context = wren_context.model_copy(
                update={"document_ids": document_context["document_ids"]}
            )
        passages = (document_context or {}).get("passages") or []
        document_ids = (document_context or {}).get("document_ids") or []
        details = {
            "available": bool(passages),
            "document_count": len(document_ids),
            "passage_count": len(passages),
            "retriever": (document_context or {}).get("retriever"),
            "truncated": bool((document_context or {}).get("truncated", False)),
            "passages": passages,
        }
        return {
            **state,
            "document_context": document_context,
            "wren_context": wren_context,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="load_document_context",
                    summary=(
                        f"Retrieved {len(passages)} passage(s) from "
                        f"{len(document_ids)} uploaded document(s)."
                        if passages
                        else "No relevant uploaded-document passages."
                    ),
                    details=details,
                ),
            ],
        }

    def _probe_dimension_values(self, state: AgentState) -> AgentState:
        """Probe stored values for the question's quoted literals (C2, gated).

        Inert (no trace noise) when the flag is off or the question quotes no
        literal; when it fires, the hints and probe activity are traced so the
        explain UI shows what value evidence grounded the draft.
        """

        request = state["request"]
        if not self.config.wren_dimension_value_probe_enabled:
            return {**state, "dimension_values": []}
        if not extract_quoted_literals(request.question):
            return {**state, "dimension_values": []}
        context = state["context"]
        hints = probe_dimension_values(
            question=request.question,
            datasets=context.datasets,
            superset_client=self.superset_client,
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            max_queries=self.config.wren_dimension_value_probe_max_queries,
        )
        return {
            **state,
            "dimension_values": hints,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="probe_dimension_values",
                    summary=(
                        f"Matched stored values for {len(hints)} quoted literal(s)."
                        if hints
                        else "No stored values matched the quoted literal(s)."
                    ),
                    details={"hints": hints},
                ),
            ],
        }

    def _doc_reranker(self):
        """Build the B3 LLM reranker for doc-context candidates when enabled.

        ``None`` (the default) keeps the first-stage hybrid/keyword order —
        reranking adds one model call per query, so it is opt-in.
        """

        if not self.config.wren_rerank_enabled:
            return None

        def reranker(question: str, texts: list[str], k: int) -> list[int] | None:
            return llm_rerank(self.model_client, question, texts, k)

        return reranker

    def _recall_access(
        self, request: AgentQueryRequest, project: SemanticProject | None
    ) -> RecallAccess | None:
        """Build the F2 recall access set across the project's full schema set (R1).

        Decoupled from grounding ``context.datasets`` (a ranked, single-schema
        subset): lists the user's reachable tables per project schema so a
        cross-schema golden/memory pair can pass the Stage-A access filter. Returns
        ``None`` when recall is inert (no project and learning off) so the draft
        node falls back to the prior behaviour without an extra scan.
        """

        schema_names = (
            project.schema_names
            if project is not None and project.schema_names
            else request.effective_schema_names
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
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_names=schema_names,
            limit=cap,
        )

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
            # Carry the full multi-schema set so scope_hash/memory key on the whole
            # project scope, not just the primary schema.
            schema_names=request.schema_names,
            dataset_ids=request.dataset_ids,
            database_uri_fingerprint=(
                request.database_uri_fingerprint
                or self._scope_fingerprint(request.database_id, request.catalog_name)
            ),
        )

    def _scope_fingerprint(
        self,
        database_id: int,
        catalog_name: str | None,
    ) -> str | None:
        """Resolve the physical-database fingerprint (DB-tied sharing key).

        Resolved through the caller's own Superset client (so it is
        access-gated server-side, never trusted from the request payload) and
        memoized per graph instance — graphs are request-scoped, so one lookup
        serves every node in a run. Fail-open to ``None``: stores then fall
        back to per-connection ``database_id`` behavior rather than losing the
        turn.
        """

        key = (database_id, catalog_name)
        cache = getattr(self, "_fingerprint_cache", None)
        if cache is None:
            cache = {}
            self._fingerprint_cache = cache
        if key not in cache:
            fingerprint: str | None = None
            try:
                identity = self.superset_client.get_database_identity(
                    database_id=database_id,
                    catalog_name=catalog_name,
                )
                fingerprint = getattr(identity, "uri_fingerprint", None) or None
            except Exception:  # pylint: disable=broad-except - best-effort key
                fingerprint = None
            cache[key] = fingerprint
        return cache[key]

    def _instruction_scope_hashes(self, request: AgentQueryRequest) -> list[str]:
        """DB-tied hash first, legacy per-connection hash second (back-compat).

        Instructions are schema-scoped (dataset selection ignored), mirroring
        ``instruction_scope_hash``.
        """

        scope = self._request_scope(request)
        if scope.dataset_ids:
            scope = scope.model_copy(update={"dataset_ids": []})
        return scope_hashes(scope)

    def _draft_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = state["context"]
        owner_id = state.get("owner_id", DEFAULT_OWNER_ID)
        k = self.config.wren_memory_recall_k
        # Prefer the project-wide, access-filtered recall set (R1); fall back to the
        # single-schema grounding datasets only when it could not be built.
        access = state.get("recall_access") or build_recall_access(context.datasets)
        memory_pairs = self.memory.recall_examples(
            request.question,
            database_id=request.database_id,
            k=k,
            access=access,
            # DB-tied pool: shared across every user's own connection to the
            # same physical database.
            database_uri_fingerprint=self._scope_fingerprint(
                request.database_id, request.catalog_name
            ),
        )
        # Project-scoped golden queries lead the few-shot set (priority); runtime
        # memory fills the rest. Golden are access-filtered identically (F3/2C).
        golden_pairs = recall_golden_queries(
            mdl_file_store=self.mdl_file_store,
            project_id=getattr(state.get("wren_context"), "project_id", None)
            or request.project_id,
            owner_id=owner_id,
            question=request.question,
            k=k,
            embedder=getattr(self.memory, "embedder", None),
            access=access,
        )
        # Leakage guard (testing platform P2.4): a benchmark run must not hand
        # the agent an item's own golden exemplar while measuring that item.
        excluded = {
            " ".join(q.lower().split())
            for q in (request.exclude_example_questions or [])
        }
        if excluded:
            memory_pairs = [
                pair
                for pair in memory_pairs
                if " ".join(pair.question.lower().split()) not in excluded
            ]
            golden_pairs = [
                pair
                for pair in golden_pairs
                if " ".join(pair.question.lower().split()) not in excluded
            ]
        recalled = [
            pair.model_dump()
            for pair in merge_recalled_examples(golden_pairs, memory_pairs, k)
        ]
        instructions = [
            item.instruction
            for item in self.instruction_store.recall(
                request.question,
                # Instructions are schema-scoped (dataset selection ignored) so an
                # editor-authored instruction is recalled regardless of the query's
                # selected datasets (C5.1 fix); memory recall above stays query-scoped.
                # DB-tied hash first, legacy per-connection hash second.
                scope_hashes=self._instruction_scope_hashes(request),
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
            document_context=state.get("document_context"),
            dimension_values=state.get("dimension_values", []),
        )
        # C3 (gated): also draft a raw-schema candidate and let a pairwise
        # judge pick — candidate diversity + selection beats retry-on-error by
        # ablation. Fires only when semantic context actually differentiates
        # the two candidates.
        selection_event: TraceEvent | None = None
        wren_ctx = state.get("wren_context")
        if (
            self.config.wren_dual_candidate_enabled
            and wren_ctx is not None
            and wren_ctx.available
            and wren_ctx.context_items
        ):
            raw_draft = self._call_sql_model(
                request=request,
                context=context,
                wren_context=None,
                validation_errors=[],
                recalled_examples=recalled,
                instructions=instructions,
                document_context=state.get("document_context"),
                dimension_values=state.get("dimension_values", []),
            )
            draft, selection_event = self._select_candidate(
                request, semantic=draft, raw=raw_draft
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
                *([selection_event] if selection_event is not None else []),
            ],
        }

    def _select_candidate(
        self,
        request: AgentQueryRequest,
        *,
        semantic: SqlDraft,
        raw: SqlDraft,
    ) -> tuple[SqlDraft, TraceEvent]:
        """Pick between the semantic and raw-schema drafts (C3).

        Cheap validity check first (an invalid candidate never wins); a
        pairwise LLM judge breaks the both-valid case, preferring the semantic
        candidate on any failure. The decision is traced for the explain UI.
        """

        dialect = self.superset_client.get_database_dialect(request.database_id)

        def _valid(draft: SqlDraft) -> bool:
            if not (draft.sql or "").strip():
                return False
            return validate_read_only_sql(
                draft.sql,
                dialect=dialect,
                default_limit=self.config.default_sql_limit,
                policy_mode=self.config.sql_policy_mode,
            ).is_valid

        semantic_valid, raw_valid = _valid(semantic), _valid(raw)
        if semantic_valid and not raw_valid:
            choice, reason = "a", "Only the semantic-layer candidate validated."
        elif raw_valid and not semantic_valid:
            choice, reason = "b", "Only the raw-schema candidate validated."
        elif not semantic_valid and not raw_valid:
            choice, reason = "a", "Neither validated; kept the semantic candidate."
        else:
            choice, reason = llm_select_candidate(
                self.model_client,
                request.question,
                semantic.sql,
                raw.sql,
            )
        chosen = semantic if choice == "a" else raw
        event = TraceEvent(
            step="select_sql_candidate",
            summary=(
                "Selected the semantic-layer SQL candidate."
                if choice == "a"
                else "Selected the raw-schema SQL candidate."
            ),
            details={
                "chosen": "semantic" if choice == "a" else "raw",
                "reason": reason,
                "semantic_sql": semantic.sql,
                "raw_sql": raw.sql,
                "semantic_valid": semantic_valid,
                "raw_valid": raw_valid,
            },
        )
        return chosen, event

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
            finalize_enabled=self.config.wren_dialect_finalize_enabled,
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
                        "inlined_metrics": result.inlined_metrics,
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
            policy_mode=self.config.sql_policy_mode,
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
            document_context=state.get("document_context"),
            dimension_values=state.get("dimension_values", []),
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

        # Learning loop: store the confirmed NL->SQL pair for future recall. Pooled
        # per database (shared); referenced tables captured for the access filter.
        try:
            native_sql = state.get("native_sql") or validation.normalized_sql
            referenced_tables, referenced_schemas = refs_from_sql(native_sql)
            self.memory.store_confirmed(
                question=request.question,
                semantic_sql=state.get("semantic_sql") or validation.normalized_sql,
                native_sql=native_sql,
                database_id=request.database_id,
                created_by=state.get("owner_id", DEFAULT_OWNER_ID),
                referenced_tables=referenced_tables,
                referenced_schemas=referenced_schemas,
                result_meta={"row_count": result.row_count},
                database_uri_fingerprint=self._scope_fingerprint(
                    request.database_id, request.catalog_name
                ),
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
            document_context=state.get("document_context"),
            dimension_values=state.get("dimension_values", []),
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
        document_context: dict[str, Any] | None = None,
        dimension_values: list[dict[str, Any]] | None = None,
    ) -> SqlDraft:
        prompt = get_prompt("text_to_sql")
        # Authoring-guidance flag (factors 1-2 only). Centralized in the engine
        # module so the badge's mode evaluator and this call-site share one
        # definition. Deliberately narrower than the badge's semantic verdict.
        semantic_sql_mode = guidance_enabled(self.config, self.semantic_engine)
        semantic_sql_instructions = _compose_semantic_guidance(
            semantic_sql_mode,
            backend=context.database.backend,
            finalize_enabled=self.config.wren_dialect_finalize_enabled,
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
            "semantic_sql_instructions": semantic_sql_instructions,
            "recalled_examples": recalled_examples or [],
            # User-authored guidance (Wren `instructions`) steers generation.
            "instructions": instructions or [],
            # Advisory business context retrieved from uploaded BI documents
            # (doc RAG, A1). The prompt's trust ladder ranks it below the
            # semantic layer; it never authorizes new tables/columns.
            "document_context": document_context,
            # Stored-value evidence for quoted literals (C2): the exact
            # dimension values found in the data for the question's strings.
            "dimension_values": dimension_values or [],
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
