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

"""CI regression gate over benchmark runs (F9/P3.4, DP-10/DP-20).

Gates on **paired deltas with statistical allowance**, never a hard absolute
threshold (LLM variance makes those flaky): the gate fails only when the
current run is significantly worse than the baseline beyond the allowed
regression. Framework-agnostic pure function — call it from pytest
(``assert gate(...).passed, gate(...).message``), a CI script over the run
API, or DeepEval's harness if that dependency is adopted later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from superset_ai_agent.evals.schemas import EvalResult
from superset_ai_agent.evals.stats import paired_delta_ci


@dataclass
class GateResult:
    """Outcome of one baseline-vs-current regression check."""

    passed: bool
    delta: float
    ci_low: float
    ci_high: float
    significant: bool
    n_items: int
    regressed_items: list[str] = field(default_factory=list)
    message: str = ""


def verdicts_by_item(results: list[EvalResult]) -> dict[str, list[str]]:
    """Group effective verdicts per item (the gate/compare input shape)."""

    grouped: dict[str, list[str]] = {}
    for result in results:
        grouped.setdefault(result.item_id, []).append(result.effective_verdict)
    return grouped


def gate_regression(
    baseline: dict[str, list[str]],
    current: dict[str, list[str]],
    *,
    allowed_regression: float = 0.0,
    require_significance: bool = True,
) -> GateResult:
    """Fail only on a real regression.

    ``allowed_regression`` is the tolerated drop in mean pass rate (e.g. 0.05
    allows a 5-point dip). With ``require_significance`` (default, recommended)
    a drop additionally has to be statistically meaningful (95% CI excluding
    zero) to fail the gate — the advisory-first posture of DP-10; set it False
    for a strict gate on stable, large suites.
    """

    comparison = paired_delta_ci(baseline, current)
    if comparison.n_items == 0:
        return GateResult(
            passed=True,
            delta=0.0,
            ci_low=0.0,
            ci_high=0.0,
            significant=False,
            n_items=0,
            message="No shared items between baseline and current — nothing to gate.",
        )
    regressed = comparison.delta < -abs(allowed_regression)
    if require_significance:
        regressed = regressed and comparison.significant
    message = (
        f"pass-rate delta {comparison.delta * 100:+.1f} pts "
        f"(95% CI {comparison.ci_low * 100:.1f} to {comparison.ci_high * 100:.1f}, "
        f"n={comparison.n_items}); allowed regression "
        f"{allowed_regression * 100:.1f} pts; "
        f"{len(comparison.regressed)} item(s) regressed."
    )
    return GateResult(
        passed=not regressed,
        delta=comparison.delta,
        ci_low=comparison.ci_low,
        ci_high=comparison.ci_high,
        significant=comparison.significant,
        n_items=comparison.n_items,
        regressed_items=comparison.regressed,
        message=("GATE FAILED: " if regressed else "OK: ") + message,
    )
