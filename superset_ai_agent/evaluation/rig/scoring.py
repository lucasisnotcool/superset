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
"""Three-way scoring dispatch, reusing the in-app platform's primitives.

Mirrors the shipped ``_score_benchmark_result`` (``app.py``) so the research rig and
the in-app Benchmarks feature grade identically (plan §4, DP-1):

- ``expected_values`` -> ``evals.typed_spec.score_expected_values`` (deterministic)
- ``gold_sql``        -> ``evals.comparator.compare_result_sets`` (result-set EX)
- ``eval_note``       -> ``evals.judge.judge_eval_note``          (LLM judge, PoLL)

The in-app scorers speak ``pass``/``fail``/``needs_review``; this module normalizes
to the rig verdict vocabulary (``correct``/``wrong``/``needs_review``/``error``) so
``run_eval_v4.build_scoreboard`` counts passes as correct.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Normalized rig verdicts. ``correct`` is what build_scoreboard counts.
CORRECT = "correct"
WRONG = "wrong"
NEEDS_REVIEW = "needs_review"
ERROR = "error"
CORRECT_VERDICTS = frozenset({CORRECT})

_NORMALIZE = {"pass": CORRECT, "fail": WRONG, "needs_review": NEEDS_REVIEW}


@dataclass
class AgentAnswer:
    """The agent's answer to one question, extracted from a query response."""

    status: str | None = None
    sql: str | None = None
    summary: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


@dataclass
class GoldResult:
    """Executed gold-SQL result (columns + rows) for a ``gold_sql`` item."""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ScoreOutcome:
    """A normalized verdict plus its reasons and provenance."""

    verdict: str
    reasons: list[str] = field(default_factory=list)
    source: str = "code"  # "code" | "llm_judge"


def answer_from_response(resp: dict[str, Any]) -> AgentAnswer:
    """Adapt an ``AgentClient.query`` response into an :class:`AgentAnswer`."""

    execution = resp.get("execution_result") or {}
    return AgentAnswer(
        status=resp.get("status"),
        sql=resp.get("sql"),
        summary=resp.get("answer_summary"),
        rows=execution.get("rows") or [],
        columns=execution.get("columns") or [],
    )


def _errored(answer: AgentAnswer) -> bool:
    return (answer.status or "").lower() == "error"


def score_item(
    *,
    answer_type: str,
    answer_spec: dict[str, Any],
    question: str,
    answer: AgentAnswer,
    gold: GoldResult | None = None,
    model_client: Any | None = None,
    judge_enabled: bool = True,
    judge_votes: int = 1,
    judge_model: str | None = None,
) -> ScoreOutcome:
    """Score one answered item against its typed spec. Pure except the judge call."""

    if _errored(answer):
        return ScoreOutcome(ERROR, [f"Agent errored (status={answer.status})."])

    if answer_type == "expected_values":
        from superset_ai_agent.evals.typed_spec import (  # noqa: PLC0415
            score_expected_values,
        )

        outcome = score_expected_values(answer_spec, answer.rows)
        return ScoreOutcome(
            _NORMALIZE.get(outcome.verdict, WRONG), list(outcome.reasons)
        )

    if answer_type == "eval_note":
        note = str(answer_spec.get("note") or "")
        if not judge_enabled:
            return ScoreOutcome(
                NEEDS_REVIEW, ["Free-text expectation — judge disabled."], "llm_judge"
            )
        if model_client is None:
            return ScoreOutcome(
                NEEDS_REVIEW, ["No model client available for the judge."], "llm_judge"
            )
        from superset_ai_agent.evals.judge import judge_eval_note  # noqa: PLC0415

        outcome = judge_eval_note(
            model_client,
            question=question,
            note=note,
            sql=answer.sql,
            rows=answer.rows,
            summary=answer.summary,
            votes=judge_votes,
            model=judge_model,
        )
        reasons = [outcome.critique] if outcome.critique else []
        return ScoreOutcome(
            _NORMALIZE.get(outcome.verdict, NEEDS_REVIEW), reasons, "llm_judge"
        )

    if answer_type == "gold_sql":
        if gold is None:
            return ScoreOutcome(
                ERROR, ["gold_sql item scored with no executed gold result."]
            )
        from superset_ai_agent.evals.comparator import (  # noqa: PLC0415
            compare_result_sets,
        )

        comparison = compare_result_sets(
            predicted_columns=answer.columns,
            predicted_rows=answer.rows,
            gold_columns=gold.columns,
            gold_rows=gold.rows,
        )
        return ScoreOutcome(
            _NORMALIZE.get(comparison.verdict, WRONG), list(comparison.reasons)
        )

    return ScoreOutcome(ERROR, [f"Unknown answer_type {answer_type!r}."])
