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

"""SemanticPipeline — the embeddable plan-and-execute facade (wren_full.md 4.1).

Composes the deterministic seams (Retriever, SemanticEngine, Executor, Memory)
plus the intent classifier into one importable object so callers outside the
LangGraph graphs — automations, programmatic replays of confirmed examples, or a
future non-LangGraph orchestrator — can drive the same governed path:

    classify_intent → retrieve context → engine.plan_sql (rewrite) →
    validate_read_only_sql → Superset execute → memory.store_confirmed

It reuses the exact shared steps the graphs use (`plan_semantic_sql_step`,
`validate_read_only_sql`, the Superset executor) so it cannot drift from them.
The **LLM drafting loop stays in the graphs**; this facade takes semantic SQL as
input. Inverting the graphs into thin callers of this pipeline (so it owns
drafting too) remains deferred (wren_full.md RO2).

Governance invariants are preserved: the engine only *rewrites*, Superset is the
sole executor behind `validate_read_only_sql`, and memory is context-not-permission
(owner+scope scoped, written only on confirmed success).
"""

from __future__ import annotations

import logging
from uuid import uuid4

from pydantic import BaseModel, Field

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.intent import classify_intent, IntentResult
from superset_ai_agent.llm.base import ModelClient
from superset_ai_agent.llm.embeddings import create_embedder
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    ExecutionResult,
    SqlExecutionSource,
    SqlValidation,
)
from superset_ai_agent.semantic_layer.engine import (
    create_semantic_engine,
    SemanticEngine,
)
from superset_ai_agent.semantic_layer.engine.planning import (
    plan_semantic_sql_step,
    PlanStepResult,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.memory_store import Memory, NullMemory
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    retrieve_mdl_context,
    Retriever,
)
from superset_ai_agent.semantic_layer.store import scope_hash
from superset_ai_agent.tools.sql import validate_read_only_sql

logger = logging.getLogger(__name__)


class SemanticPlanResult(BaseModel):
    """Outcome of a pipeline plan-and-execute call."""

    semantic_sql: str
    native_sql: str
    engine: str
    rewritten: bool = False
    validation: SqlValidation
    execution_result: ExecutionResult | None = None
    referenced_tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stored_example: bool = False


class SemanticPipeline:
    """Reusable facade composing the deterministic semantic seams."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        superset_client: SupersetClient,
        model_client: ModelClient | None = None,
        semantic_engine: SemanticEngine | None = None,
        retriever: Retriever | None = None,
        memory: Memory | None = None,
        mdl_file_store: MdlFileStore | None = None,
    ) -> None:
        self.config = config
        self.superset_client = superset_client
        self.model_client = model_client
        self.semantic_engine = semantic_engine or create_semantic_engine(config)
        self.retriever = retriever or create_retriever(config, create_embedder(config))
        self.memory = memory or NullMemory()
        self.mdl_file_store = mdl_file_store

    def classify_intent(self, question: str) -> IntentResult:
        """Classify the question; defaults to ``text_to_sql`` without a model."""

        if self.model_client is None:
            return IntentResult(intent="text_to_sql", reason="no-model-client")
        return classify_intent(self.model_client, question)

    def retrieve_context(
        self,
        question: str,
        *,
        project_id: str | None,
        owner_id: str,
    ) -> list[dict[str, object]]:
        """Rank the project's active MDL into prompt context items."""

        return retrieve_mdl_context(
            config=self.config,
            retriever=self.retriever,
            question=question,
            project_id=project_id,
            owner_id=owner_id,
            mdl_file_store=self.mdl_file_store,
        )

    def plan_and_execute(
        self,
        *,
        semantic_sql: str,
        context: AgentContext,
        request: AgentQueryRequest,
        owner_id: str,
        project_id: str | None = None,
        execute: bool = True,
        store_on_success: bool = True,
    ) -> SemanticPlanResult:
        """Rewrite → validate → (optionally) execute → store a confirmed example.

        The engine only rewrites; Superset is the sole executor and runs behind
        ``validate_read_only_sql``. A confirmed NL→SQL pair is stored only on a
        successful execution when ``store_on_success`` is set.
        """

        plan = plan_semantic_sql_step(
            self.semantic_engine,
            sql=semantic_sql,
            context=context,
            owner_id=owner_id,
            project_id=project_id,
            mdl_file_store=self.mdl_file_store,
        )
        dialect = self.superset_client.get_database_dialect(request.database_id)
        validation = validate_read_only_sql(
            plan.native_sql,
            dialect=dialect,
            default_limit=self.config.default_sql_limit,
            policy_mode=self.config.sql_policy_mode,
        )

        execution_result: ExecutionResult | None = None
        stored_example = False
        if execute and validation.is_valid and validation.normalized_sql:
            execution_result = self.superset_client.execute_sql(
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
            if store_on_success:
                stored_example = self._store_confirmed(
                    request=request,
                    plan=plan,
                    normalized_sql=validation.normalized_sql,
                    owner_id=owner_id,
                    project_id=project_id,
                    result=execution_result,
                )

        return SemanticPlanResult(
            semantic_sql=plan.semantic_sql,
            native_sql=plan.native_sql,
            engine=plan.engine,
            rewritten=plan.rewritten,
            validation=validation,
            execution_result=execution_result,
            referenced_tables=plan.referenced_tables,
            warnings=plan.warnings,
            stored_example=stored_example,
        )

    def _store_confirmed(
        self,
        *,
        request: AgentQueryRequest,
        plan: PlanStepResult,
        normalized_sql: str,
        owner_id: str,
        project_id: str | None,
        result: ExecutionResult,
    ) -> bool:
        try:
            self.memory.store_confirmed(
                question=request.question,
                semantic_sql=plan.semantic_sql or normalized_sql,
                native_sql=plan.native_sql or normalized_sql,
                scope_hash=scope_hash(
                    ConversationScope(
                        database_id=request.database_id,
                        catalog_name=request.catalog_name,
                        schema_name=request.schema_name,
                        dataset_ids=request.dataset_ids,
                    )
                ),
                owner_id=owner_id,
                project_id=project_id,
                result_meta={"row_count": result.row_count},
            )
            return True
        except Exception as ex:  # pylint: disable=broad-except - memory is best-effort
            logger.warning("Pipeline failed to store learning-loop example: %s", ex)
            return False
