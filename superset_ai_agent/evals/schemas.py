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

"""Domain + API schemas for Project Benchmarks (testing platform F11)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

AnswerType = Literal["gold_sql", "expected_values", "eval_note"]
RunStatus = Literal["pending", "running", "complete", "failed", "superseded"]
ResultVerdict = Literal["pass", "fail", "needs_review", "error"]
ScoreSource = Literal["code", "llm_judge", "human", "api"]

#: Genie-validated scale ceiling for one benchmark's item count.
MAX_ITEMS_PER_BENCHMARK = 500

#: Rows persisted on result previews (full sets are compared, never stored).
PREVIEW_ROW_CAP = 50


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class Benchmark(BaseModel):
    """A project-scoped test set (Dataset)."""

    id: str = Field(default_factory=_new_id)
    project_id: str
    owner_id: str
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    #: Denormalized for list views; populated by the store on read.
    item_count: int = 0


class BenchmarkItem(BaseModel):
    """One test case: NL question + typed expected answer."""

    id: str = Field(default_factory=_new_id)
    benchmark_id: str
    position: int = 0
    question: str
    answer_type: AnswerType
    answer_spec: dict[str, Any]
    capability_tags: list[str] = Field(default_factory=list)
    use_as_example: bool = False
    verified_by: str | None = None
    verified_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class RunTotals(BaseModel):
    """Terminal counts for a run."""

    items: int = 0
    trials: int = 1
    passed: int = 0
    failed: int = 0
    needs_review: int = 0
    errors: int = 0
    #: Reliability score when trials > 1 (fraction of items passing ALL trials).
    pass_hat_k: float | None = None
    #: Diagnostic breakdown per capability tag: tag -> {"items": n, "passed": n}.
    #: The capability scoreboard for this run (single-config paradigm: compared
    #: across runs over time, never across config arms).
    by_capability: dict[str, dict[str, int]] | None = None


class RunProgress(BaseModel):
    """Live progress while a run is executing."""

    completed: int = 0
    total: int = 0
    current_question: str | None = None


class EvalRun(BaseModel):
    """One scored execution of a benchmark."""

    id: str = Field(default_factory=_new_id)
    benchmark_id: str
    project_id: str
    owner_id: str
    status: RunStatus = "pending"
    trials: int = 1
    config: dict[str, Any] = Field(default_factory=dict)
    mdl_checksum: str | None = None
    benchmark_checksum: str = ""
    database_id: int | None = None
    score: float | None = None
    totals: RunTotals | None = None
    progress: RunProgress | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class EvalScore(BaseModel):
    """One OTel-``gen_ai.evaluation.result``-shaped score row."""

    id: str = Field(default_factory=_new_id)
    result_id: str = ""
    name: str
    value: float | None = None
    label: str | None = None
    explanation: str | None = None
    source: ScoreSource = "code"
    created_at: datetime = Field(default_factory=_utc_now)


class EvalResult(BaseModel):
    """Per-item, per-trial outcome (frozen question/spec, capped previews)."""

    id: str = Field(default_factory=_new_id)
    run_id: str
    item_id: str
    trial_index: int = 0
    question: str
    answer_type: AnswerType
    answer_spec: dict[str, Any]
    agent_sql: str | None = None
    agent_status: str | None = None
    agent_rows_preview: list[dict[str, Any]] | None = None
    gold_rows_preview: list[dict[str, Any]] | None = None
    verdict: ResultVerdict
    verdict_source: ScoreSource = "code"
    reasons: list[str] = Field(default_factory=list)
    matched_models: list[str] | None = None
    duration_ms: int | None = None
    override_verdict: ResultVerdict | None = None
    override_by: str | None = None
    override_at: datetime | None = None
    override_comment: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    scores: list[EvalScore] = Field(default_factory=list)

    @property
    def effective_verdict(self) -> ResultVerdict:
        """Human override wins over the automated verdict."""

        return self.override_verdict or self.verdict


# --- API request/response models --------------------------------------------


class BenchmarkCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class BenchmarkUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class BenchmarkItemCreateRequest(BaseModel):
    question: str = Field(min_length=1)
    answer_type: AnswerType
    answer_spec: dict[str, Any]
    capability_tags: list[str] = Field(default_factory=list)
    use_as_example: bool = False
    verified: bool = False


class BenchmarkItemUpdateRequest(BaseModel):
    question: str | None = Field(default=None, min_length=1)
    answer_type: AnswerType | None = None
    answer_spec: dict[str, Any] | None = None
    capability_tags: list[str] | None = None
    use_as_example: bool | None = None
    verified: bool | None = None


class BenchmarkRunRequest(BaseModel):
    """Single-config run submission (plan_benchmark_authoring_agent_impl.md §1.1).

    The benchmark always tests the agent **as-is** — its configured model and
    grounding, with each item's own golden example excluded from recall (the
    anti-cheat invariant). The former per-run ``model`` override and
    ``exclude_example_recall`` toggle were multi-config residue and are gone.
    """

    trials: int = Field(default=1, ge=1, le=5)
    item_ids: list[str] | None = None
    execute: bool = True


class BenchmarkRunSubmitted(BaseModel):
    run_id: str
    status: RunStatus
    total_items: int


class DryRunResponse(BaseModel):
    """Preview of what an item's gold side produces."""

    answer_type: AnswerType
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    note: str | None = None
    spec: dict[str, Any] | None = None
    problems: list[str] = Field(default_factory=list)


class ResultOverrideRequest(BaseModel):
    verdict: ResultVerdict
    comment: str | None = None


class RunComparisonResponse(BaseModel):
    """Run-vs-run paired comparison (never a bare delta — spec §16)."""

    run_id: str
    other_run_id: str
    delta: float
    ci_low: float
    ci_high: float
    significant: bool
    n_items: int
    improved: list[str] = Field(default_factory=list)
    regressed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    #: True when the two runs tested different item sets/content.
    benchmark_changed: bool = False


class GoldenImportResponse(BaseModel):
    created: int
    skipped_duplicates: int


class BenchmarkAuthorRequest(BaseModel):
    """One authoring pass over uploaded CSV/context text (plan P3.1, DP-B3)."""

    csv_text: str = Field(min_length=1)
    #: Extra business context (e.g. an uploaded .md), appended verbatim to the
    #: CSV's own context rows for doc assembly and prompt grounding.
    context_text: str | None = None
    mode: Literal["extract", "generate", "both"] = "extract"
    generate_count: int = Field(default=5, ge=1, le=25)


class BenchmarkItemsImportRequest(BaseModel):
    """Bulk import of REVIEWED authored items (plan P3.2, DP-B7)."""

    items: list[BenchmarkItemCreateRequest] = Field(
        min_length=1, max_length=MAX_ITEMS_PER_BENCHMARK
    )


class BenchmarkItemsImportResponse(BaseModel):
    created: int
    skipped_duplicates: int
    errors: list[str] = Field(default_factory=list)
