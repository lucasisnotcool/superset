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

"""Run statistics: pass^k reliability and paired-delta bootstrap CIs.

Implements the spec §16 statistics rules: multi-trial runs report **pass^k**
(the probability that ALL k trials pass — tau-bench's reliability metric, which
exposes agents that look fine at pass@1), and run-vs-run comparisons report a
**paired delta on the shared item set with a bootstrap confidence interval**
(Anthropic "Adding Error Bars to Evals" recs) — never a bare percentage delta.
Pure Python, deterministic (seeded), no numpy/scipy dependency.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

#: Verdicts counted as a pass when aggregating (needs_review/error never pass).
PASSING_VERDICTS = frozenset({"pass"})


def _item_pass_map(
    verdicts_by_item: dict[str, list[str]],
) -> dict[str, list[bool]]:
    return {
        item: [v in PASSING_VERDICTS for v in trials]
        for item, trials in verdicts_by_item.items()
        if trials
    }


def pass_hat_k(verdicts_by_item: dict[str, list[str]]) -> float:
    """Fraction of items whose EVERY trial passed (pass^k, tau-bench).

    ``verdicts_by_item`` maps item id → per-trial verdict strings.
    Returns 0.0 for an empty input.
    """

    passes = _item_pass_map(verdicts_by_item)
    if not passes:
        return 0.0
    return sum(1 for trials in passes.values() if all(trials)) / len(passes)


def mean_pass_rate(verdicts_by_item: dict[str, list[str]]) -> float:
    """Item-mean of per-item trial pass rates (the headline score)."""

    passes = _item_pass_map(verdicts_by_item)
    if not passes:
        return 0.0
    return sum(
        sum(trials) / len(trials) for trials in passes.values()
    ) / len(passes)


@dataclass
class PairedDelta:
    """Paired run-vs-run comparison on the shared item set."""

    delta: float
    ci_low: float
    ci_high: float
    #: True when the 95% CI excludes zero — the gate for "this change is real".
    significant: bool
    n_items: int
    improved: list[str] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def paired_delta_ci(
    a_by_item: dict[str, list[str]],
    b_by_item: dict[str, list[str]],
    *,
    n_boot: int = 2000,
    seed: int = 7,
) -> PairedDelta:
    """Bootstrap CI over per-item paired deltas (run B minus run A).

    Only items present in BOTH runs are compared (paired design eliminates
    question-difficulty variance — Anthropic rec 4). Per-item scores are trial
    pass rates, so it works for 1-trial and k-trial runs alike. Deterministic
    via ``seed``.
    """

    a_scores = {
        item: sum(t) / len(t) for item, t in _item_pass_map(a_by_item).items()
    }
    b_scores = {
        item: sum(t) / len(t) for item, t in _item_pass_map(b_by_item).items()
    }
    shared = sorted(set(a_scores) & set(b_scores))
    if not shared:
        return PairedDelta(
            delta=0.0, ci_low=0.0, ci_high=0.0, significant=False, n_items=0
        )

    deltas = [b_scores[item] - a_scores[item] for item in shared]
    improved = [i for i, d in zip(shared, deltas, strict=False) if d > 0]
    regressed = [i for i, d in zip(shared, deltas, strict=False) if d < 0]
    unchanged = [i for i, d in zip(shared, deltas, strict=False) if d == 0]
    point = sum(deltas) / len(deltas)

    if len(shared) == 1 or all(d == deltas[0] for d in deltas):
        # Degenerate spread: the bootstrap collapses to the point estimate.
        return PairedDelta(
            delta=round(point, 6),
            ci_low=round(point, 6),
            ci_high=round(point, 6),
            significant=point != 0.0,
            n_items=len(shared),
            improved=improved,
            regressed=regressed,
            unchanged=unchanged,
        )

    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not crypto
    n = len(deltas)
    means = sorted(
        sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)
    )
    lo = means[int(0.025 * n_boot)]
    hi = means[min(int(0.975 * n_boot), n_boot - 1)]
    return PairedDelta(
        delta=round(point, 6),
        ci_low=round(lo, 6),
        ci_high=round(hi, 6),
        significant=not (lo <= 0.0 <= hi),
        n_items=len(shared),
        improved=improved,
        regressed=regressed,
        unchanged=unchanged,
    )
