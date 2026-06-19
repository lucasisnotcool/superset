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
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    ExecutionResult,
    SqlValidation,
    TraceEvent,
)
from superset_ai_agent.tools.sql import validate_read_only_sql


class SqlDraft(BaseModel):
    """Structured model output for SQL generation."""

    sql: str = Field(description="The generated read-only SQL query.")
    explanation: str = Field(description="Short explanation of the query.")


class AgentState(TypedDict, total=False):
    request: AgentQueryRequest
    context: AgentContext
    sql: str | None
    explanation: str | None
    validation: SqlValidation
    execution_result: ExecutionResult | None
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
    ):
        self.config = config
        self.model_client = model_client
        self.context_provider = context_provider
        self.superset_client = superset_client
        self.graph = self._compile_graph()

    def run(self, request: AgentQueryRequest) -> AgentQueryResponse:
        initial_state: AgentState = {
            "request": request,
            "trace": [],
            "repair_attempts": 0,
            "execution_result": None,
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
        )

    def _compile_graph(self) -> Any:
        graph = StateGraph(AgentState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("draft_sql", self._draft_sql)
        graph.add_node("validate_sql", self._validate_sql)
        graph.add_node("repair_sql", self._repair_sql)
        graph.add_node("execute_sql", self._execute_sql)

        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "draft_sql")
        graph.add_edge("draft_sql", "validate_sql")
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
        graph.add_edge("execute_sql", END)
        return graph.compile()

    def _load_context(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = self.context_provider.get_context(request)
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

    def _draft_sql(self, state: AgentState) -> AgentState:
        request = state["request"]
        context = state["context"]
        draft = self._call_sql_model(
            request=request,
            context=context,
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
                schema_name=request.schema_name,
                limit=self.config.default_sql_limit,
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
        validation_errors: list[str],
    ) -> SqlDraft:
        prompt = get_prompt("text_to_sql")
        user_payload = {
            "question": request.question,
            "database": context.database.model_dump(),
            "datasets": [dataset.model_dump() for dataset in context.datasets],
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
