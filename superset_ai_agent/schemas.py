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
    schema_name: str | None = None
    row_limit: int | None = None
    timeout_seconds: int | None = None
    source: str | None = None


class WrenContextArtifact(BaseModel):
    """Wren context, examples, planning, and semantic-layer metadata."""

    enabled: bool = False
    available: bool = False
    matched_models: list[str] = Field(default_factory=list)
    example_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    semantic_layer_version: str | None = None
    indexing_status: str | None = None
    context_items: list[dict[str, Any]] = Field(default_factory=list)
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
