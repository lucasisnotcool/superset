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
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_schema_names(
    primary: str | None,
    extras: list[str] | None,
) -> list[str]:
    """Return an ordered, de-duplicated schema set with ``primary`` first.

    The single source of truth for "what schemas does this scope/project/request
    cover". ``primary`` (the scalar ``schema_name``) is always element 0 when
    present, so every existing reader of ``schema_name`` keeps seeing the primary
    schema while ``schema_names`` carries the full multi-schema set.
    """

    ordered: list[str] = []
    for name in [primary, *(extras or [])]:
        if name and name not in ordered:
            ordered.append(name)
    return ordered


class TraceEvent(BaseModel):
    """A compact event for UI display and debugging."""

    step: str
    status: Literal["ok", "warning", "error"] = "ok"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    #: When the node emitted this event. Used to derive per-step durations for
    #: the explain-and-audit timeline (ai_agent_explain_and_audit.md). Defaults so
    #: every existing construction site stays valid without change.
    created_at: datetime = Field(default_factory=_utc_now)


class AgentQueryRequest(BaseModel):
    """Request for natural-language to SQL generation."""

    question: str = Field(min_length=1)
    database_id: int
    catalog_name: str | None = None
    #: Primary schema (back-compat scalar). For a multi-schema scope this is the
    #: first element of ``schema_names``.
    schema_name: str | None = None
    #: Full schema set when grounding on a multi-schema semantic project.
    #: ``None``/empty means single-schema (use ``schema_name``). Prefer
    #: ``effective_schema_names``. Mirrors ``ConversationScope`` so both the
    #: one-shot query path and the conversation path carry multi-schema intent.
    schema_names: list[str] | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    #: Explicit semantic-layer project to ground on; honored only after the
    #: backend re-checks access + schema coverage (see ``ConversationScope``).
    project_id: str | None = None
    execute: bool = False
    model: str | None = None
    max_steps: int = Field(default=6, ge=2, le=12)

    @property
    def effective_schema_names(self) -> list[str]:
        """Ordered schema set, primary first; ``[]`` when no schema is set."""

        return normalize_schema_names(self.schema_name, self.schema_names)


class SqlValidation(BaseModel):
    """Validation result for generated SQL.

    ``classification`` is the deterministic verdict from
    ``superset_ai_agent.tools.sql_policy`` (backed by Superset core's
    ``SQLScript``); ``reason`` is the user-facing explanation for a block (R3).
    ``is_read_only`` is kept as the derived ``classification == "read_only"``
    convenience so existing call sites and the execution gate stay stable.
    """

    is_valid: bool
    is_read_only: bool
    classification: Literal[
        "read_only", "mutating", "opaque", "multi", "unparseable"
    ] = "unparseable"
    reason: str | None = None
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
    #: Names of MDL views surfaced into the prompt for this turn (a view is a
    #: vetted, named query the agent can select from instead of re-deriving the
    #: joins). Empty when the project has no views or none match the question.
    matched_views: list[str] = Field(default_factory=list)
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


# --- Explain & audit timeline (ai_agent_explain_and_audit.md) -----------------
#
# One typed, ordered timeline of what happened between a user message and the
# agent's final response. Assembled post hoc from the graph's TraceEvents plus
# the late-bound WrenContextArtifact/AuditInfo carriers by
# ``superset_ai_agent.explain.build_agent_timeline`` — the graphs are unchanged.
#
# ``AgentStep.kind`` is the graph node name (kept as ``str`` so a newly added
# node never breaks rendering — it degrades to its bare summary). Each step's
# ``detail`` is a discriminated union keyed on a *shape tag* (``detail.kind``),
# decoupled from the node name so several nodes (e.g. repair/correct) can share
# one shape while staying unambiguous on JSON round-trip through the store.

#: Every step name the two graphs emit. The drift-guard test asserts the graph
#: source only emits names in this set (ai_agent_explain_and_audit.md R4).
KNOWN_AGENT_STEP_KINDS: frozenset[str] = frozenset(
    {
        "load_conversation",
        "classify_intent",
        "answer_directly",
        "load_context",
        "load_wren_context",
        "draft_sql",
        "draft_response",
        "approved_sql",
        "dry_plan_with_wren",
        "plan_semantic_sql",
        "validate_sql",
        "repair_sql",
        "correct_semantic_sql",
        "execute_sql",
        "duplicate_sql",
        "build_artifacts",
        "reflect_sql_outcome",
        "conversation_error",
        "agent_error",
    }
)

#: Node names that start a fresh drafting cycle, used to group steps into SQL
#: attempts for the UI (ai_agent_explain_and_audit.md Seam 5).
DRAFT_STEP_KINDS: frozenset[str] = frozenset(
    {"draft_sql", "draft_response", "approved_sql", "answer_directly"}
)


class LoadContextDetail(BaseModel):
    """Schema/context load (``load_context``)."""

    kind: Literal["load_context"] = "load_context"
    dataset_count: int = 0
    database_name: str | None = None
    retrieval: WrenRetrievalArtifact | None = None


class IntentDetail(BaseModel):
    """Intent classification (``classify_intent``)."""

    kind: Literal["intent"] = "intent"
    intent: str | None = None
    reason: str | None = None


class RetrievedChunk(BaseModel):
    """One MDL schema chunk the Retriever seam ranked into the prompt.

    Mirrors the ``context_items`` dict the retriever emits (schema_retriever.py),
    surfaced in the explain timeline so users see *what* grounded the answer, not
    just how many chunks were retrieved.
    """

    kind: str | None = None
    name: str | None = None
    model: str | None = None
    text: str
    retriever: str | None = None
    #: Relevance score from the ranker (cosine for embedding, normalized token
    #: overlap for keyword). ``None`` on the cold ANN path where it is unavailable.
    score: float | None = None


class LoadWrenContextDetail(BaseModel):
    """Semantic/MDL retrieval (``load_wren_context``)."""

    kind: Literal["wren_context"] = "wren_context"
    available: bool = False
    project_id: str | None = None
    mdl_path: str | None = None
    matched_models: list[str] = Field(default_factory=list)
    #: Names of MDL views surfaced into the prompt this turn — the view provenance
    #: shown in the Explain dialog ("which vetted, named query grounded the
    #: answer"). Only semantic views appear; native views are never surfaced.
    matched_views: list[str] = Field(default_factory=list)
    retrieval_mode: str | None = None
    retrieved_item_count: int = 0
    context_item_count: int = 0
    recalled_example_count: int = 0
    #: The ranked MDL chunks contributed by the retriever this turn (A1). Bounded
    #: by ``retrieved_item_count``; ``text`` is truncated for display.
    retrieved_chunks: list[RetrievedChunk] = Field(default_factory=list)
    #: Why context is unavailable / degraded, surfaced verbatim (B6/B8).
    warnings: list[str] = Field(default_factory=list)


class RecalledExample(BaseModel):
    """A confirmed NL->SQL example the memory seam recalled into the prompt (B1).

    Surfaced in the explain timeline so users see *which* learned examples
    grounded the draft, *and where each came from*, not just how many.
    ``native_sql`` is truncated for display. Provenance (F3/2C):

    - ``source`` — ``"golden"`` (curated project query) or ``"memory"`` (learned
      runtime example).
    - ``verified`` — a golden query whose answer was human-verified.
    - ``name`` — the golden query's curated name, when present.
    - ``in_scope`` — for a learned example, whether its tables are onboarded in
      the active project; ``False`` marks one recalled from a broader same-database
      context (Stage C native-only).
    """

    question: str
    native_sql: str | None = None
    source: Literal["golden", "memory"] = "memory"
    verified: bool = False
    name: str | None = None
    in_scope: bool = True


class DraftDetail(BaseModel):
    """Model draft (``draft_sql``/``draft_response``/``approved_sql``/answer)."""

    kind: Literal["draft"] = "draft"
    response_type: str | None = None
    model: str | None = None
    recalled_example_count: int = 0
    #: The recalled examples that grounded this draft (B1); bounded by
    #: ``recalled_example_count``.
    recalled_examples: list[RecalledExample] = Field(default_factory=list)


class DryPlanDetail(BaseModel):
    """Engine dry-plan diagnostics (``dry_plan_with_wren``)."""

    kind: Literal["dry_plan"] = "dry_plan"
    available: bool = True
    diagnostics: list[str] = Field(default_factory=list)


class PlanSemanticSqlDetail(BaseModel):
    """Semantic->native rewrite (``plan_semantic_sql``)."""

    kind: Literal["plan_semantic_sql"] = "plan_semantic_sql"
    engine: str | None = None
    rewritten: bool = False
    semantic_sql: str | None = None
    native_sql: str | None = None
    referenced_tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidateSqlDetail(BaseModel):
    """Read-only validation (``validate_sql``)."""

    kind: Literal["validate_sql"] = "validate_sql"
    is_valid: bool = False
    dialect: str | None = None
    errors: list[str] = Field(default_factory=list)


class RepairDetail(BaseModel):
    """Repair/correction re-draft (``repair_sql``/``correct_semantic_sql``)."""

    kind: Literal["repair"] = "repair"
    errors: list[str] = Field(default_factory=list)
    dry_plan_diagnostics: list[str] = Field(default_factory=list)
    attempt: int | None = None


class ExecuteSqlDetail(BaseModel):
    """Governed execution (``execute_sql``/``duplicate_sql``)."""

    kind: Literal["execute"] = "execute"
    row_count: int | None = None
    sql: str | None = None
    executed_sql: str | None = None
    query_id: int | str | None = None
    adapter: str | None = None
    error: str | None = None
    is_duplicate: bool = False


class BuildArtifactsDetail(BaseModel):
    """Artifact synthesis (``build_artifacts``)."""

    kind: Literal["build_artifacts"] = "build_artifacts"
    insight_card_count: int = 0
    chart_type: str | None = None
    has_data_preview: bool = False


class ReflectDetail(BaseModel):
    """SQL reflection decision (``reflect_sql_outcome``)."""

    kind: Literal["reflect"] = "reflect"
    outcome: str | None = None
    remaining_sql_iterations: int | None = None
    retry_feedback: str | None = None


AgentStepDetail = Annotated[
    Union[
        LoadContextDetail,
        IntentDetail,
        LoadWrenContextDetail,
        DraftDetail,
        DryPlanDetail,
        PlanSemanticSqlDetail,
        ValidateSqlDetail,
        RepairDetail,
        ExecuteSqlDetail,
        BuildArtifactsDetail,
        ReflectDetail,
    ],
    Field(discriminator="kind"),
]


class AgentStep(BaseModel):
    """One ordered step in the message->response explain-and-audit timeline."""

    kind: str
    status: Literal["ok", "warning", "error"] = "ok"
    summary: str
    started_at: datetime = Field(default_factory=_utc_now)
    duration_ms: int | None = None
    #: Which SQL drafting cycle this step belongs to (0-based), so retries group.
    attempt_index: int = 0
    #: The SQL artifact this step produced/acted on, when it can be matched.
    artifact_id: str | None = None
    detail: AgentStepDetail | None = None


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
    #: Ordered explain-and-audit timeline of the message->response chain
    #: (ai_agent_explain_and_audit.md). Assembled by ``explain.build_agent_timeline``.
    timeline: list[AgentStep] = Field(default_factory=list)


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
    #: Effective max upload size for source documents (WREN_MAX_DOCUMENT_BYTES), so
    #: the UI can reject oversized files before the upload round-trip.
    max_document_bytes: int = 10_000_000


class LlmUsageBucket(BaseModel):
    """Aggregated LLM-call metrics for one grouping key (a day, model, provider)."""

    key: str
    calls: int = 0
    failures: int = 0
    total_duration_ms: int = 0
    avg_duration_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LlmUsageSummary(BaseModel):
    """Aggregated LLM-call telemetry surfaced in the admin usage view.

    Totals plus breakdowns by day, model, and provider over an optional time
    window. Token totals are best-effort (only providers that report usage
    contribute). ``kinds`` is the set of call classes included ("chat" today; the
    deferred embedding meter would add "embedding").
    """

    total_calls: int = 0
    total_failures: int = 0
    total_duration_ms: int = 0
    avg_duration_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    by_day: list[LlmUsageBucket] = Field(default_factory=list)
    by_model: list[LlmUsageBucket] = Field(default_factory=list)
    by_provider: list[LlmUsageBucket] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    since: datetime | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
