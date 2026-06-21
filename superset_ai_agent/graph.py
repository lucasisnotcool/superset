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
from uuid import uuid4

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from superset_ai_agent.artifacts.charts import infer_chart_spec
from superset_ai_agent.artifacts.insights import build_artifact_bundle, profile_result
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.integrations.wren.client import DisabledWrenClient, WrenClient
from superset_ai_agent.llm.base import ChatMessage, ModelClient
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
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.projects import SemanticProjectStore
from superset_ai_agent.semantic_layer.runtime import merge_indexed_semantic_context
from superset_ai_agent.semantic_layer.schemas import WrenMaterializationResult
from superset_ai_agent.semantic_layer.store import SemanticLayerStore
from superset_ai_agent.semantic_layer.wren_runtime import (
    materialize_request_semantic_project,
)
from superset_ai_agent.tools.sql import validate_read_only_sql


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
        semantic_layer_store: SemanticLayerStore | None = None,
        semantic_project_store: SemanticProjectStore | None = None,
        mdl_file_store: MdlFileStore | None = None,
    ):
        self.config = config
        self.model_client = model_client
        self.context_provider = context_provider
        self.superset_client = superset_client
        self.wren_client = wren_client or DisabledWrenClient()
        self.semantic_layer_store = semantic_layer_store
        self.semantic_project_store = semantic_project_store
        self.mdl_file_store = mdl_file_store
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

        return AgentQueryResponse(
            status=status,
            sql=state.get("sql"),
            explanation=state.get("explanation"),
            validation=validation,
            execution_result=state.get("execution_result"),
            trace=state.get("trace", []),
            answer_summary=state.get("answer_summary"),
            insight_cards=state.get("insight_cards", []),
            chart_spec=state.get("chart_spec"),
            data_preview=state.get("data_preview"),
            audit=state.get("audit"),
            recommended_followups=state.get("recommended_followups", []),
            wren_context=state.get("wren_context"),
        )

    def _compile_graph(self) -> Any:
        graph = StateGraph(AgentState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("load_wren_context", self._load_wren_context)
        graph.add_node("draft_sql", self._draft_sql)
        graph.add_node("dry_plan_with_wren", self._dry_plan_with_wren)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("repair_sql", self._repair_sql)
        graph.add_node("execute_sql", self._execute_sql)
        graph.add_node("build_artifacts", self._build_artifacts)

        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "load_wren_context")
        graph.add_edge("load_wren_context", "draft_sql")
        graph.add_edge("draft_sql", "dry_plan_with_wren")
        graph.add_edge("dry_plan_with_wren", "validate_sql")
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
        details = (
            retrieval_artifact.model_dump()
            if retrieval_artifact is not None
            else {}
        )
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
        wren_context = merge_indexed_semantic_context(
            semantic_layer_store=self.semantic_layer_store,
            scope=ConversationScope(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                dataset_ids=request.dataset_ids,
            ),
            owner_id=state.get("owner_id", DEFAULT_OWNER_ID),
            wren_context=wren_context,
        )
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

    def _draft_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = state["context"]
        draft = self._call_sql_model(
            request=request,
            context=context,
            wren_context=state.get("wren_context"),
            validation_errors=[],
        )
        return {
            **state,
            "sql": draft.sql,
            "explanation": draft.explanation,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="draft_sql",
                    summary="Generated an initial SQL draft.",
                    details={"model": request.model or self.config.default_model()},
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
        draft = self._call_sql_model(
            request=request,
            context=context,
            wren_context=state.get("wren_context"),
            validation_errors=validation.errors,
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
                    details={"errors": validation.errors},
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

        return {
            **state,
            "execution_result": result,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="execute_sql",
                    summary=f"Executed SQL and returned {result.row_count} row(s).",
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
        return {
            **state,
            "answer_summary": bundle.answer_summary,
            "insight_cards": bundle.insight_cards,
            "chart_spec": chart_spec,
            "data_preview": bundle.data_preview,
            "audit": result.audit,
            "recommended_followups": bundle.recommended_followups,
            "trace": [
                *state.get("trace", []),
                TraceEvent(
                    step="build_artifacts",
                    summary="Built conversational analytics artifacts.",
                    details={
                        "insight_card_count": len(bundle.insight_cards),
                        "chart_type": chart_spec.type if chart_spec else None,
                    },
                ),
            ],
        }

    def _route_after_validation(self, state: AgentState) -> str:
        request = state["request"]
        validation = state["validation"]
        if validation.is_valid:
            return "execute" if request.execute else "end"
        if state.get("repair_attempts", 0) < self.config.max_repair_attempts:
            return "repair"
        return "end"

    def _call_sql_model(
        self,
        *,
        request: AgentQueryRequest,
        context: AgentContext,
        wren_context: WrenContextArtifact | None,
        validation_errors: list[str],
    ) -> SqlDraft:
        prompt = get_prompt("text_to_sql")
        user_payload = {
            "question": request.question,
            "database": context.database.model_dump(),
            "datasets": [dataset.model_dump() for dataset in context.datasets],
            "wren_context": (
                wren_context.model_dump() if wren_context is not None else None
            ),
            "validation_errors_to_fix": validation_errors,
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
