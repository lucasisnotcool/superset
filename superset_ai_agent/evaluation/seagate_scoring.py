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
"""Ground-truth-keyed scorer for the Seagate evaluation.

The generic grader in ``eval_common`` is intentionally conservative (it defers
multi-value questions to manual review). This module encodes the *exact* expected
answers from ``dev_fixtures/seagate_manufacturing/test_queries.md`` and checks a
result against them programmatically, so multi-trial runs can be aggregated
without per-row hand grading. Verdicts:

- ``correct``   — the required value(s) are present in the executed result.
- ``partial``   — some but not all required values present (multi-part questions).
- ``wrong``     — no rows / NULL / wrong value.
- ``trap_ok``   — the trap (Q12): the agent did **not** return a confident number.
- ``trap_fail`` — the trap: a confident number was returned.

Floating-point answers use a 2% relative tolerance; integer answers must match
exactly (after the agent's own rounding). The check scans every numeric cell in
the result so it is insensitive to column aliasing.
"""

from __future__ import annotations

from typing import Any

# Required answer(s) per question. ``nums`` = numbers that must all appear in the
# result; ``names`` = strings that must all appear; ``parts`` = count for partial
# credit; ``trap`` marks the refusal question.
EXPECTED: dict[str, dict[str, Any]] = {
    "Q1": {"nums": [6]},
    "Q2": {"nums": [1]},
    "Q3": {"nums": [57]},
    "Q4": {"nums": [14]},
    "Q5": {
        "names": ["Shugart Yard", "Scotts Valley West", "Reef Hollow"],
        "absent": ["Tigerline Point"],
    },
    "Q6": {"nums": [9386]},
    "Q7": {"nums": [2979]},
    "Q8": {"nums": [193, 106, 40]},  # Cobalt 193, Vantage 106, Nimbus 40
    "Q9": {"nums": [0.961]},  # Golden Yield Cobalt Dec (STANDARD only)
    "Q10": {"nums": [0.935]},  # True Pass Rate Plate Spin Tigerline
    "Q11": {"nums": [145]},
    "Q12": {"trap": True},
    "Q13": {"nums": [0.960, 0.962, 729, 521]},  # Tigerline/Reef GY + Combo+DineIn
    "Q14": {"nums": [0.972, 0.882]},  # Tigerline vs Reef True Pass Rate (Heat Lamp)
    "Q15": {"nums": [0.355, 0.120]},  # Combo share Tigerline 35.5% / Reef 12.0%
    # --- L5: cross-schema only (seagate_multi fixture; see EVAL_V2_SPEC.md) ---
    "Q16": {"nums": [1751, 3017]},  # patties plated on WARM lines: Cobalt / Vantage
    "Q17": {"nums": [0.951]},  # Golden Yield, Vantage family, Q4 2025 (n=1567)
    "Q18": {"nums": [175, 151, 0.960, 0.962]},  # Nimbus Combo+DineIn units + region GY
}

#: Questions that only exist in the multi-schema fixture (seagate_multi). The
#: single-schema scoring run keys off ``EXPECTED`` minus these.
CROSS_SCHEMA_ONLY = ("Q16", "Q17", "Q18")


def _all_numbers(rows: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for row in rows or []:
        for v in row.values():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(float(v))
            elif isinstance(v, str):
                s = v.strip().replace(",", "").rstrip("%")
                try:
                    out.append(float(s))
                except ValueError:
                    continue
    return out


def _all_strings(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows or []:
        for v in row.values():
            if isinstance(v, str):
                out.append(v)
    return out


def _num_present(target: float, pool: list[float]) -> bool:
    # A fractional rate (0<t<1) may be reported as a percentage (t*100); accept
    # either scale. Do NOT expand integer counts (would let 1 match 0.01≈0).
    candidates = {target}
    if 0 < abs(target) < 1:
        candidates.add(target * 100)
    for t in candidates:
        scale = max(abs(t), 1.0)
        if any(abs(t - p) <= max(0.02 * scale, 0.02) for p in pool):
            return True
    return False


def score_result(
    qid: str, rows: list[dict[str, Any]], answer_summary: str | None
) -> str:
    """Return a verdict for one question's result against ground truth."""
    spec = EXPECTED[qid]
    if spec.get("trap"):
        # Correct iff the agent did not assert a confident number.
        nums = _all_numbers(rows)
        if not nums:
            return "trap_ok"
        return "trap_fail"

    if "names" in spec:
        pool = " | ".join(_all_strings(rows)).lower()
        present = sum(1 for n in spec["names"] if n.lower() in pool)
        absent_ok = all(a.lower() not in pool for a in spec.get("absent", []))
        if present == len(spec["names"]) and absent_ok:
            return "correct"
        return "partial" if present else "wrong"

    targets = spec["nums"]
    pool = _all_numbers(rows)
    hits = sum(1 for t in targets if _num_present(t, pool))
    if hits == len(targets):
        return "correct"
    if hits == 0:
        return "wrong"
    return "partial"


CORRECT_VERDICTS = {"correct", "trap_ok"}
