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

"""Stats: pass^k reliability + paired bootstrap CIs (spec §16 statistics)."""

from __future__ import annotations

from superset_ai_agent.evals.stats import (
    mean_pass_rate,
    paired_delta_ci,
    pass_hat_k,
)


def test_pass_hat_k_diverges_from_mean_pass_rate() -> None:
    # Each item passes 2 of 3 trials: mean pass rate is 2/3, pass^3 is 0.
    verdicts = {
        "q1": ["pass", "pass", "fail"],
        "q2": ["fail", "pass", "pass"],
    }
    assert abs(mean_pass_rate(verdicts) - 2 / 3) < 1e-9
    assert pass_hat_k(verdicts) == 0.0


def test_pass_hat_k_all_trials_pass() -> None:
    verdicts = {"q1": ["pass", "pass"], "q2": ["pass", "pass"]}
    assert pass_hat_k(verdicts) == 1.0


def test_needs_review_and_error_never_count_as_pass() -> None:
    verdicts = {"q1": ["needs_review"], "q2": ["error"], "q3": ["pass"]}
    assert abs(mean_pass_rate(verdicts) - 1 / 3) < 1e-9


def test_empty_inputs_are_zero() -> None:
    assert pass_hat_k({}) == 0.0
    assert mean_pass_rate({}) == 0.0


def test_identical_runs_are_not_significant() -> None:
    run = {f"q{i}": ["pass" if i % 2 else "fail"] for i in range(20)}
    outcome = paired_delta_ci(run, run)
    assert outcome.delta == 0.0
    assert not outcome.significant
    assert outcome.n_items == 20


def test_uniform_large_improvement_is_significant() -> None:
    run_a = {f"q{i}": ["fail"] for i in range(30)}
    run_b = {f"q{i}": ["pass" if i < 24 else "fail"] for i in range(30)}
    outcome = paired_delta_ci(run_a, run_b)
    assert outcome.delta > 0.7
    assert outcome.significant
    assert len(outcome.improved) == 24


def test_small_noisy_delta_is_not_significant() -> None:
    # One item flips up, one flips down, 18 unchanged: obviously noise.
    run_a = {f"q{i}": ["pass" if i < 10 else "fail"] for i in range(20)}
    run_b = dict(run_a)
    run_b["q0"] = ["fail"]
    run_b["q19"] = ["pass"]
    outcome = paired_delta_ci(run_a, run_b)
    assert outcome.delta == 0.0
    assert not outcome.significant
    assert outcome.improved == ["q19"]
    assert outcome.regressed == ["q0"]


def test_only_shared_items_are_compared() -> None:
    run_a = {"q1": ["pass"], "q2": ["fail"], "only_a": ["pass"]}
    run_b = {"q1": ["pass"], "q2": ["pass"], "only_b": ["fail"]}
    outcome = paired_delta_ci(run_a, run_b)
    assert outcome.n_items == 2


def test_disjoint_runs_yield_empty_comparison() -> None:
    outcome = paired_delta_ci({"a": ["pass"]}, {"b": ["pass"]})
    assert outcome.n_items == 0
    assert not outcome.significant


def test_bootstrap_is_deterministic() -> None:
    run_a = {f"q{i}": ["pass" if i % 3 else "fail"] for i in range(15)}
    run_b = {f"q{i}": ["pass" if i % 2 else "fail"] for i in range(15)}
    first = paired_delta_ci(run_a, run_b)
    second = paired_delta_ci(run_a, run_b)
    assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)
