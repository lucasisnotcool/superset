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

from typing import Any, Literal

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """A compact event for UI display and debugging."""

    step: str
    status: Literal["ok", "warning", "error"] = "ok"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class AgentQueryRequest(BaseModel):
    """Request for natural-language to SQL generation."""

    question: str = Field(min_length=1)
    database_id: int
    catalog_name: str | None = None
    schema_name: str | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    execute: bool = False
    model: str | None = None
    max_steps: int = Field(default=6, ge=2, le=12)


class SqlValidation(BaseModel):
    """Validation result for generated SQL."""

    is_valid: bool
    is_read_only: bool
    normalized_sql: str | None = None
    dialect: str | None = None
    errors: list[str] = Field(default_factory=list)


class InsightCard(BaseModel):
    """Small insight shown next to an executed analytics result."""

    title: str
    value: str | int | float | None = None
    metric: str | None = None
    category: str | None = None
    description: str | None = None
    severity: Literal["info", "success", "warning"] = "info"


class ChartEncoding(BaseModel):
    """Minimal frontend chart encoding."""

    x: str | None = None
    y: str | list[str] | None = None
    series: str | None = None
    time: str | None = None
    label: str | None = None


class ChartSpec(BaseModel):
    """Lightweight chart preview contract for returned rows."""

    type: Literal["bar", "line", "table"]
    title: str | None = None
    encoding: ChartEncoding = Field(default_factory=ChartEncoding)
    options: dict[str, Any] = Field(default_factory=dict)


class AuditInfo(BaseModel):
    """Audit metadata propagated from the governed Superset execution path."""

    adapter: Literal["rest", "mcp", "local"] | None = None
    query_id: int | str | None = None
    results_key: str | None = None
    executed_sql: str | None = None
    database_id: int | None = None
    catalog_name: str | None = None
    schema_name: str | None = None
    row_limit: int | None = None
    timeout_seconds: int | None = None
    client_id: str | None = None
    sql_editor_id: str | None = None
    tab: str | None = None
    source_hash: str | None = None
    source: str | None = None
    # Semantic-engine provenance (wren_full.md Phase 1.2): the SQL the model
    # wrote vs. what the engine rewrote and Superset executed.
    semantic_sql: str | None = None
    native_sql: str | None = None
    engine: str | None = None


class SqlExecutionSource(BaseModel):
    """Source marker for governed SQL Lab executions started by the agent."""

    source: Literal[
        "ai_agent",
        "ai_agent_conversation",
        "ai_agent_manual",
    ] = "ai_agent"
    request_id: str | None = None
    conversation_id: str | None = None
    artifact_id: str | None = None
    tab: str = "AI Agent"
    client_id: str | None = Field(default=None, max_length=11)
    sql_editor_id: str | None = None


class WrenRetrievalArtifact(BaseModel):
    """Bounded schema retrieval metadata for large semantic projects."""

    project_id: str | None = None
    database_id: int | None = None
    catalog_name: str | None = None
    schema_name: str | None = None
    candidate_table_names: list[str] = Field(default_factory=list)
    candidate_metric_names: list[str] = Field(default_factory=list)
    candidate_example_ids: list[str] = Field(default_factory=list)
    candidate_document_ids: list[str] = Field(default_factory=list)
    scanned_table_count: int = 0
    omitted_table_count: int = 0
    context_truncated: bool = False


class WrenContextArtifact(BaseModel):
    """Wren context, examples, planning, and semantic-layer metadata."""

    enabled: bool = False
    available: bool = False
    project_id: str | None = None
    mdl_path: str | None = None
    materialized_file_count: int | None = None
    materialized_checksum: str | None = None
    matched_models: list[str] = Field(default_factory=list)
    example_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    context_items: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_mode: str | None = None
    #: How many MDL schema chunks the Retriever seam contributed to the prompt
    #: for this turn (0 when the retriever has nothing to add). Surfaced as a UI
    #: badge so the embedding/keyword retrieval activity is visible (RV3/G8).
    retrieved_item_count: int = 0
    #: How many confirmed NL->SQL examples the memory seam recalled into the
    #: prompt for this turn (0 when learning is off). Surfaced as a UI badge.
    recalled_example_count: int = 0
    retrieval: WrenRetrievalArtifact | None = None
    dry_plan: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    """Small, model-safe SQL execution result."""

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    audit: AuditInfo | None = None
    is_truncated: bool = False


class AgentQueryResponse(BaseModel):
    """Response from the text-to-SQL agent."""

    status: Literal["ok", "needs_review", "error"]
    sql: str | None = None
    explanation: str | None = None
    validation: SqlValidation
    execution_result: ExecutionResult | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    answer_summary: str | None = None
    insight_cards: list[InsightCard] = Field(default_factory=list)
    chart_spec: ChartSpec | None = None
    data_preview: ExecutionResult | None = None
    audit: AuditInfo | None = None
    recommended_followups: list[str] = Field(default_factory=list)
    wren_context: WrenContextArtifact | None = None


class ValidateSqlRequest(BaseModel):
    """Request to validate SQL outside the agent workflow."""

    sql: str = Field(min_length=1)
    dialect: str | None = None
    default_limit: int | None = Field(default=None, ge=1, le=100000)


class ModelInfo(BaseModel):
    """Model metadata exposed by the POC API."""

    name: str
    modified_at: str | None = None
    size: int | None = None


class HealthResponse(BaseModel):
    """Health response for the standalone API."""

    status: Literal["ok", "degraded"]
    model_provider: str
    base_url: str
    default_model: str
    reachable: bool
    ollama_base_url: str | None = None
    ollama_reachable: bool | None = None
    #: False when the semantic layer runs in-memory (models lost on restart), so
    #: the UI can warn before users model against an ephemeral store.
    semantic_layer_persistent: bool = True
    #: Effective embedding vector index: "memory" | "lancedb" | "memory_fallback".
    #: "memory_fallback" means LanceDB was configured but did not connect (C1).
    vector_index: str = "memory"
