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

"""Scientist agent v1 (F12, P3.1): read-only benchmark-run analysis.

Turns a completed run into a diagnosis report: per-failure classification on
the text-to-SQL error taxonomy (each class maps to a specific MDL fix type),
an honest "the test may be wrong" escape hatch, and a **deterministic
statistical gate** — when a previous run exists, the paired delta + CI is
computed in code and handed to the model, and within-noise movement must be
reported as such, never acted on (spec §16; GEPA-style reflection with
Wren-AI-Advisor discipline). v1 proposes nothing and mutates nothing; the
Copilot-changeset handoff is v2.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent, independent of Superset
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from superset_ai_agent.evals.schemas import EvalResult, EvalRun
from superset_ai_agent.evals.stats import PairedDelta
from superset_ai_agent.llm.base import ChatMessage

logger = logging.getLogger(__name__)

#: Failure taxonomy → the MDL fix type each class points at (spec §13/§15).
DIAGNOSIS_TAXONOMY: dict[str, str] = {
    "schema_linking": "Add synonyms/descriptions so the right table/column is chosen.",
    "join_path": "Add or correct relationships (join paths) in the MDL.",
    "aggregation": "Define or fix the metric/aggregation definition.",
    "filter_value": "Add sample values/enums so filters use real literals.",
    "time_semantics": "Configure time dimensions/grains (fiscal calendars, ranges).",
    "test_is_wrong": (
        "The gold answer or question looks incorrect/ambiguous — review the test."
    ),
    "other": "Needs human investigation.",
}

#: Failures shown to the model per analysis (cost control).
_MAX_FINDINGS_INPUT = 25


class ScientistFinding(BaseModel):
    """One diagnosed failure."""

    item_id: str
    question: str
    diagnosis: str = "other"
    suggested_fix_type: str | None = None
    suggested_action: str | None = None
    test_suspect: bool = False


class ScientistReport(BaseModel):
    """The analysis deliverable for one run."""

    summary: str
    #: Deterministic, code-computed comparison note (never model-authored).
    stats_note: str | None = None
    #: True when the delta vs the previous run is within noise (code-computed).
    within_noise: bool | None = None
    findings: list[ScientistFinding] = Field(default_factory=list)
    #: Raw model text when the reply could not be parsed as a report.
    parse_degraded: bool = False


_ANALYSIS_PROMPT = """You are an evaluation scientist for a text-to-SQL agent \
over a governed semantic model (MDL).

A benchmark run just completed. Diagnose each failed question using EXACTLY one
of these diagnosis classes:
{taxonomy}

Statistical context (computed, trustworthy): {stats_note}
RULES:
- If the statistical context says the movement is within noise, your summary
  MUST say the change is not statistically meaningful and MUST NOT recommend
  model changes on the basis of the delta alone.
- If a gold answer itself looks wrong or the question is ambiguous, use
  diagnosis "test_is_wrong" and set "test_suspect": true — do not force-blame
  the agent.
- Be specific in suggested_action (name the tables/columns/joins involved when
  the evidence shows them).

Run summary: {run_summary}

Failed / review questions (agent SQL, named reasons, result previews):
{failures}

Respond with ONLY a JSON object:
{{"summary": "<3-6 sentences>",
  "findings": [{{"item_id": "...", "question": "...",
                 "diagnosis": "<one taxonomy key>",
                 "suggested_action": "<concrete step>",
                 "test_suspect": false}}]}}
"""


def _stats_note(comparison: PairedDelta | None) -> tuple[str, bool | None]:
    if comparison is None or comparison.n_items == 0:
        return ("No previous completed run to compare against.", None)
    direction = "improved" if comparison.delta >= 0 else "regressed"
    note = (
        f"Vs the previous run: {direction} by "
        f"{abs(comparison.delta) * 100:.0f} points "
        f"(95% CI {comparison.ci_low * 100:.0f} to "
        f"{comparison.ci_high * 100:.0f}, n={comparison.n_items} shared "
        f"questions) — "
        + (
            "statistically meaningful."
            if comparison.significant
            else "WITHIN NOISE; do not act on this delta."
        )
    )
    return note, not comparison.significant


def _failures_payload(results: list[EvalResult]) -> list[dict[str, Any]]:
    interesting = [
        r
        for r in results
        if r.effective_verdict in ("fail", "error", "needs_review")
    ]
    payload = []
    for result in interesting[:_MAX_FINDINGS_INPUT]:
        payload.append(
            {
                "item_id": result.item_id,
                "question": result.question,
                "verdict": result.effective_verdict,
                "agent_sql": result.agent_sql,
                "reasons": result.reasons,
                "agent_rows_preview": (result.agent_rows_preview or [])[:5],
                "gold_rows_preview": (result.gold_rows_preview or [])[:5],
                "matched_models": result.matched_models,
            }
        )
    return payload


def _parse_report(content: str) -> ScientistReport | None:
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    findings = []
    for raw in parsed.get("findings", []) or []:
        diagnosis = str(raw.get("diagnosis", "other"))
        if diagnosis not in DIAGNOSIS_TAXONOMY:
            diagnosis = "other"
        try:
            findings.append(
                ScientistFinding(
                    item_id=str(raw.get("item_id", "")),
                    question=str(raw.get("question", "")),
                    diagnosis=diagnosis,
                    suggested_fix_type=DIAGNOSIS_TAXONOMY[diagnosis],
                    suggested_action=(
                        str(raw["suggested_action"])
                        if raw.get("suggested_action")
                        else None
                    ),
                    test_suspect=bool(raw.get("test_suspect", False)),
                )
            )
        except ValidationError:
            continue
    summary = str(parsed.get("summary", "")).strip()
    if not summary:
        return None
    return ScientistReport(summary=summary, findings=findings)


def analyze_run(
    model_client: Any,
    *,
    run: EvalRun,
    results: list[EvalResult],
    comparison: PairedDelta | None = None,
) -> ScientistReport:
    """Produce the Scientist v1 report for a completed run (read-only)."""

    stats_note, within_noise = _stats_note(comparison)
    totals = run.totals
    run_summary = (
        f"{totals.passed}/{totals.items} passed, {totals.failed} failed, "
        f"{totals.needs_review} need review, {totals.errors} errors "
        f"(trials={totals.trials})."
        if totals is not None
        else "Totals unavailable."
    )
    failures = _failures_payload(results)
    if not failures:
        return ScientistReport(
            summary=(
                "Every question passed — no failures to diagnose. "
                "Consider adding harder questions (joins, time semantics, "
                "traps) to keep the benchmark discriminative."
            ),
            stats_note=stats_note,
            within_noise=within_noise,
        )
    prompt = _ANALYSIS_PROMPT.format(
        taxonomy="\n".join(
            f'- "{key}": {hint}' for key, hint in DIAGNOSIS_TAXONOMY.items()
        ),
        stats_note=stats_note,
        run_summary=run_summary,
        failures=json.dumps(failures, default=str),
    )
    try:
        result = model_client.chat([ChatMessage(role="user", content=prompt)])
        content = getattr(result, "content", None) or ""
    except Exception as ex:  # pylint: disable=broad-except - degrade, don't fail
        logger.warning("Scientist analysis call failed.", exc_info=True)
        return ScientistReport(
            summary=f"Analysis unavailable (model call failed: {ex}).",
            stats_note=stats_note,
            within_noise=within_noise,
            parse_degraded=True,
        )
    report = _parse_report(content)
    if report is None:
        return ScientistReport(
            summary=content.strip() or "The analyst returned an empty reply.",
            stats_note=stats_note,
            within_noise=within_noise,
            parse_degraded=True,
        )
    report.stats_note = stats_note
    report.within_noise = within_noise
    return report
