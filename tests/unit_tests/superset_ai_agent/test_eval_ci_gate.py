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

"""CI regression gate (P3.4): paired deltas with statistical allowance."""

from __future__ import annotations

from superset_ai_agent.evals.ci_gate import gate_regression, verdicts_by_item
from superset_ai_agent.evals.schemas import EvalResult


def _run(pass_items: int, fail_items: int, total: int = 30) -> dict:
    verdicts = {}
    for i in range(total):
        if i < pass_items:
            verdicts[f"q{i}"] = ["pass"]
        elif i < pass_items + fail_items:
            verdicts[f"q{i}"] = ["fail"]
        else:
            verdicts[f"q{i}"] = ["pass"]
    return verdicts


def test_identical_runs_pass_the_gate() -> None:
    run = _run(20, 10)
    outcome = gate_regression(run, run)
    assert outcome.passed
    assert outcome.message.startswith("OK")


def test_large_significant_regression_fails() -> None:
    baseline = {f"q{i}": ["pass"] for i in range(30)}
    current = {f"q{i}": ["fail" if i < 20 else "pass"] for i in range(30)}
    outcome = gate_regression(baseline, current)
    assert not outcome.passed
    assert outcome.significant
    assert len(outcome.regressed_items) == 20
    assert outcome.message.startswith("GATE FAILED")


def test_small_noise_flip_passes_with_significance_requirement() -> None:
    baseline = {f"q{i}": ["pass" if i < 15 else "fail"] for i in range(30)}
    current = dict(baseline)
    current["q0"] = ["fail"]  # one flip down: within noise
    outcome = gate_regression(baseline, current)
    assert outcome.passed


def test_allowed_regression_band_tolerates_a_known_dip() -> None:
    baseline = {f"q{i}": ["pass"] for i in range(30)}
    current = {f"q{i}": ["fail" if i < 3 else "pass"] for i in range(30)}
    strict = gate_regression(baseline, current, require_significance=False)
    tolerant = gate_regression(
        baseline, current, allowed_regression=0.15, require_significance=False
    )
    assert not strict.passed
    assert tolerant.passed


def test_improvement_always_passes() -> None:
    baseline = {f"q{i}": ["fail" if i < 10 else "pass"] for i in range(30)}
    current = {f"q{i}": ["pass"] for i in range(30)}
    outcome = gate_regression(baseline, current, require_significance=False)
    assert outcome.passed
    assert outcome.delta > 0


def test_disjoint_runs_pass_vacuously_with_explanation() -> None:
    outcome = gate_regression({"a": ["pass"]}, {"b": ["fail"]})
    assert outcome.passed
    assert "No shared items" in outcome.message


def test_verdicts_by_item_uses_effective_verdict() -> None:
    results = [
        EvalResult(
            run_id="r",
            item_id="q1",
            question="?",
            answer_type="gold_sql",
            answer_spec={"sql": "SELECT 1"},
            verdict="fail",
            override_verdict="pass",  # human override wins
        ),
        EvalResult(
            run_id="r",
            item_id="q1",
            question="?",
            answer_type="gold_sql",
            answer_spec={"sql": "SELECT 1"},
            trial_index=1,
            verdict="pass",
        ),
    ]
    assert verdicts_by_item(results) == {"q1": ["pass", "pass"]}
