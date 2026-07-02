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

"""Typed expected-value assertions (the ``expected_values`` answer type).

Generalizes the semantics proven in ``evaluation/seagate_scoring.py`` into a
user-authorable spec::

    {
      "nums": [123.4, 0.57],       # every target must appear in the result
      "tolerance": 0.02,           # relative tolerance (default 2%)
      "names": ["EMEA", "APAC"],   # every name must appear (case-insensitive)
      "absent": ["ARCHIVED"],      # none of these may appear
      "trap": true,                # correct iff NO confident number is returned
      "zero": true                 # correct iff no positive quantity is returned
    }

Scans every cell of the executed result rows (column-alias-insensitive), so the
assertion holds regardless of how the agent shaped or labeled its output.
Fractional targets (0 < t < 1) also accept the percentage scale (t*100) —
"0.57" matches a result reporting "57(%)".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Verdicts a typed-spec score can produce (three-way + partial detail).
PASS = "pass"  # noqa: S105 - verdict constant, not a credential
FAIL = "fail"
NEEDS_REVIEW = "needs_review"


@dataclass
class TypedSpecOutcome:
    """Outcome of scoring rows against a typed expected-value spec."""

    verdict: str
    reasons: list[str] = field(default_factory=list)
    hits: int = 0
    targets: int = 0


def validate_expected_values_spec(  # noqa: C901 - one branch per spec key
    spec: dict[str, Any],
) -> list[str]:
    """Shape-check a typed spec; returns a list of problems (empty = valid)."""

    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["expected_values spec must be an object."]
    known = {"nums", "tolerance", "names", "absent", "trap", "zero", "multi_value"}
    unknown = set(spec) - known
    if unknown:
        problems.append(f"Unknown spec keys: {sorted(unknown)}.")
    if "nums" in spec:
        if not isinstance(spec["nums"], list) or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool)
            for x in spec["nums"]
        ):
            problems.append("`nums` must be a list of numbers.")
    for key in ("names", "absent"):
        if key in spec and (
            not isinstance(spec[key], list)
            or not all(isinstance(x, str) and x.strip() for x in spec[key])
        ):
            problems.append(f"`{key}` must be a list of non-empty strings.")
    for key in ("trap", "zero", "multi_value"):
        if key in spec and not isinstance(spec[key], bool):
            problems.append(f"`{key}` must be a boolean.")
    if "tolerance" in spec:
        tol = spec["tolerance"]
        if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol < 0:
            problems.append("`tolerance` must be a non-negative number.")
    if spec.get("trap") and spec.get("zero"):
        problems.append("`trap` and `zero` are mutually exclusive.")
    if not any(k in spec for k in ("nums", "names", "trap", "zero")):
        problems.append(
            "Spec must assert something: one of `nums`, `names`, `trap`, `zero`."
        )
    return problems


def _all_numbers(rows: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for row in rows or []:
        for value in row.values():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                out.append(float(value))
            elif isinstance(value, str):
                text = value.strip().replace(",", "").rstrip("%")
                try:
                    out.append(float(text))
                except ValueError:
                    continue
    return out


def _all_strings(rows: list[dict[str, Any]]) -> list[str]:
    return [
        value
        for row in rows or []
        for value in row.values()
        if isinstance(value, str)
    ]


def _num_present(target: float, pool: list[float], tolerance: float) -> bool:
    # A fractional rate (0<t<1) may be reported as a percentage (t*100); accept
    # either scale. Do NOT expand integer counts (would let 1 match 0.01≈0).
    candidates = {target}
    if 0 < abs(target) < 1:
        candidates.add(target * 100)
    for t in candidates:
        scale = max(abs(t), 1.0)
        if any(abs(t - p) <= max(tolerance * scale, tolerance) for p in pool):
            return True
    return False


def score_expected_values(  # noqa: C901 - one branch per assertion kind
    spec: dict[str, Any],
    rows: list[dict[str, Any]],
) -> TypedSpecOutcome:
    """Score executed rows against a typed expected-value spec."""

    tolerance = float(spec.get("tolerance", 0.02))

    if spec.get("trap"):
        nums = _all_numbers(rows)
        if not nums:
            return TypedSpecOutcome(
                verdict=PASS, reasons=["Trap question: no confident number asserted."]
            )
        return TypedSpecOutcome(
            verdict=FAIL,
            reasons=[
                f"Trap question: agent asserted {len(nums)} number(s) where "
                "none should be confidently returned."
            ],
        )

    if spec.get("zero"):
        positive = [n for n in _all_numbers(rows) if abs(n) > 0.5]
        if not positive:
            return TypedSpecOutcome(
                verdict=PASS,
                reasons=["Negative-result question: no positive quantity returned."],
            )
        return TypedSpecOutcome(
            verdict=FAIL,
            reasons=[
                f"Expected a zero/empty result but got positive value(s): "
                f"{positive[:3]}…"
                if len(positive) > 3
                else f"Expected a zero/empty result but got: {positive}."
            ],
        )

    reasons: list[str] = []
    hits = 0
    targets = 0
    ok = True

    if "names" in spec:
        pool = " | ".join(_all_strings(rows)).lower()
        names = list(spec["names"])
        present = [n for n in names if n.lower() in pool]
        missing = [n for n in names if n.lower() not in pool]
        bad = [a for a in spec.get("absent", []) if a.lower() in pool]
        hits += len(present)
        targets += len(names)
        if missing:
            ok = False
            reasons.append(f"Missing expected name(s): {missing}.")
        if bad:
            ok = False
            reasons.append(f"Forbidden name(s) present: {bad}.")

    if "nums" in spec:
        pool_nums = _all_numbers(rows)
        num_targets = [float(t) for t in spec["nums"]]
        num_hits = [t for t in num_targets if _num_present(t, pool_nums, tolerance)]
        misses = [t for t in num_targets if t not in num_hits]
        hits += len(num_hits)
        targets += len(num_targets)
        if misses:
            ok = False
            reasons.append(
                f"{len(misses)} expected value(s) not found within "
                f"{tolerance:.0%} tolerance: {misses}."
            )

    if ok:
        # Multi-value specs the author flagged as needing human confirmation
        # degrade to review even on a full hit (conservative-grader heritage).
        if spec.get("multi_value"):
            return TypedSpecOutcome(
                verdict=NEEDS_REVIEW,
                reasons=["All targets matched; flagged multi-value — confirm."],
                hits=hits,
                targets=targets,
            )
        return TypedSpecOutcome(
            verdict=PASS,
            reasons=["All expected values present."],
            hits=hits,
            targets=targets,
        )
    if hits > 0:
        reasons.insert(0, f"Partial match: {hits}/{targets} targets found.")
    return TypedSpecOutcome(verdict=FAIL, reasons=reasons, hits=hits, targets=targets)
