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

"""Result-set comparator v2 (spec §16, normative).

Compares an agent-produced result set against a gold result set the way the
text-to-SQL evaluation literature and shipped products do it:

- rows as **multisets** (bag semantics), order-insensitive unless ``ordered``;
- cells canonicalized before comparison (numeric strings → numbers, dates →
  ISO, trimmed strings, ``None`` normalized);
- numerics matched at a significant-digit precision (default 4, the rule
  Databricks Genie ships) with an optional relative-tolerance override;
- columns matched by best value-alignment (BIRD soft-F1 method), never by the
  generated alias;
- dual scores: binary execution-accuracy (``ex``) plus ``soft_f1`` partial
  credit (matched cells TP / extra predicted cells FP / missing gold cells FN);
- empty-vs-empty agreement passes but is flagged ``low_confidence`` (the
  classic execution-accuracy false positive).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Sequence

#: Hard cap on rows entering the alignment matrix (protects the O(cols²·rows)
#: column-alignment step; agent execution is capped well below this anyway).
MAX_COMPARE_ROWS = 5000

Verdict = Literal["pass", "fail", "needs_review"]


@dataclass
class ComparisonOutcome:
    """Outcome of one predicted-vs-gold result-set comparison."""

    verdict: Verdict
    ex: bool
    soft_f1: float
    matched_cells: int = 0
    false_positive_cells: int = 0
    false_negative_cells: int = 0
    low_confidence: bool = False
    reasons: list[str] = field(default_factory=list)


def canonicalize_cell(value: Any, *, casefold: bool = False) -> Any:
    """Normalize one cell to a comparison-stable representation."""

    if value is None:
        return None
    if isinstance(value, bool):
        # bool is an int subclass; keep it distinct from numerics.
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        # Numeric strings compare as numbers ("42" == 42, "0.5" == 0.5).
        try:
            return float(text)
        except ValueError:
            pass
        return text.casefold() if casefold else text
    return str(value)


def _round_sig(value: float, sig_digits: int) -> float:
    if value == 0:
        return 0.0
    magnitude = math.floor(math.log10(abs(value)))
    return round(value, -magnitude + (sig_digits - 1))


def numbers_match(
    a: float,
    b: float,
    *,
    sig_digits: int = 4,
    rel_tol: float | None = None,
) -> bool:
    """Numeric match at N significant digits, or a relative tolerance."""

    if rel_tol is not None:
        if a == b:
            return True
        denom = max(abs(a), abs(b))
        if denom == 0:
            return True
        return abs(a - b) / denom <= rel_tol
    return _round_sig(a, sig_digits) == _round_sig(b, sig_digits)


def cells_match(
    a: Any,
    b: Any,
    *,
    sig_digits: int = 4,
    rel_tol: float | None = None,
) -> bool:
    """Whether two canonicalized cells agree."""

    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, float) and isinstance(b, float):
        return numbers_match(a, b, sig_digits=sig_digits, rel_tol=rel_tol)
    if isinstance(a, float) or isinstance(b, float):
        return False
    return a == b


def _column_values(
    rows: Sequence[dict[str, Any]], column: str, *, casefold: bool
) -> list[Any]:
    return [canonicalize_cell(row.get(column), casefold=casefold) for row in rows]


def _greedy_alignment(
    gold_cols: list[str],
    pred_cols: list[str],
    gold_values: dict[str, list[Any]],
    pred_values: dict[str, list[Any]],
    *,
    sig_digits: int,
    rel_tol: float | None,
) -> dict[str, str | None]:
    """Best value-alignment of gold columns onto predicted columns.

    Greedy on the highest cell-agreement count; each predicted column is used
    at most once. Alias-insensitive by construction — only values matter.
    """

    scores: list[tuple[int, str, str]] = []
    for g in gold_cols:
        for p in pred_cols:
            gv, pv = gold_values[g], pred_values[p]
            # Compare as sorted multisets so row order doesn't bias alignment.
            agree = _multiset_agreement(gv, pv, sig_digits=sig_digits, rel_tol=rel_tol)
            scores.append((agree, g, p))
    scores.sort(key=lambda t: (-t[0], t[1], t[2]))
    mapping: dict[str, str | None] = {g: None for g in gold_cols}
    used_pred: set[str] = set()
    for agree, g, p in scores:
        if agree <= 0:
            continue
        if mapping[g] is not None or p in used_pred:
            continue
        mapping[g] = p
        used_pred.add(p)
    return mapping


def _sort_key(value: Any) -> tuple[int, str]:
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, float):
        return (2, f"{value:.12g}")
    return (3, str(value))


def _multiset_agreement(
    a: list[Any],
    b: list[Any],
    *,
    sig_digits: int,
    rel_tol: float | None,
) -> int:
    """Count of cells that pair up across two multisets (greedy on sorted)."""

    remaining = sorted(b, key=_sort_key)
    matched = 0
    for cell in sorted(a, key=_sort_key):
        for i, other in enumerate(remaining):
            if cells_match(cell, other, sig_digits=sig_digits, rel_tol=rel_tol):
                matched += 1
                remaining.pop(i)
                break
    return matched


def _rows_as_tuples(
    rows: Sequence[dict[str, Any]],
    columns: list[str],
    *,
    casefold: bool,
) -> list[tuple[Any, ...]]:
    return [
        tuple(canonicalize_cell(row.get(col), casefold=casefold) for col in columns)
        for row in rows
    ]


def _tuple_multiset_equal(
    a: list[tuple[Any, ...]],
    b: list[tuple[Any, ...]],
    *,
    sig_digits: int,
    rel_tol: float | None,
) -> bool:
    if len(a) != len(b):
        return False
    remaining = list(b)
    for row in a:
        hit = None
        for i, other in enumerate(remaining):
            if len(row) == len(other) and all(
                cells_match(x, y, sig_digits=sig_digits, rel_tol=rel_tol)
                for x, y in zip(row, other, strict=False)
            ):
                hit = i
                break
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def compare_result_sets(  # noqa: C901 - the normative rule set is inherently branchy
    *,
    predicted_columns: Sequence[str],
    predicted_rows: Sequence[dict[str, Any]],
    gold_columns: Sequence[str],
    gold_rows: Sequence[dict[str, Any]],
    ordered: bool = False,
    sig_digits: int = 4,
    rel_tol: float | None = None,
    extra_columns_policy: Literal["strict", "lenient"] = "strict",
    casefold: bool = False,
) -> ComparisonOutcome:
    """Compare predicted vs gold result sets per the spec §16 rules."""

    reasons: list[str] = []
    pred_cols = [c for c in predicted_columns if c]
    gold_cols = [c for c in gold_columns if c]
    pred_rows = list(predicted_rows)[:MAX_COMPARE_ROWS]
    gold_rows_l = list(gold_rows)[:MAX_COMPARE_ROWS]

    # Empty-vs-empty: agreement, but a low-confidence pass (EX false-positive
    # guard — two wrong queries can both return nothing).
    if not pred_rows and not gold_rows_l:
        return ComparisonOutcome(
            verdict="pass",
            ex=True,
            soft_f1=1.0,
            low_confidence=True,
            reasons=["Both result sets are empty — low-confidence pass."],
        )
    if not gold_cols or not gold_rows_l:
        return ComparisonOutcome(
            verdict="fail",
            ex=False,
            soft_f1=0.0,
            false_positive_cells=len(pred_rows) * len(pred_cols),
            reasons=["Gold result set is empty but the agent returned rows."],
        )
    if not pred_rows or not pred_cols:
        return ComparisonOutcome(
            verdict="fail",
            ex=False,
            soft_f1=0.0,
            false_negative_cells=len(gold_rows_l) * len(gold_cols),
            reasons=["Agent returned no rows but the gold result has rows."],
        )

    gold_values = {
        c: _column_values(gold_rows_l, c, casefold=casefold) for c in gold_cols
    }
    pred_values = {
        c: _column_values(pred_rows, c, casefold=casefold) for c in pred_cols
    }
    mapping = _greedy_alignment(
        gold_cols,
        pred_cols,
        gold_values,
        pred_values,
        sig_digits=sig_digits,
        rel_tol=rel_tol,
    )

    unmatched_gold_cols = [g for g, p in mapping.items() if p is None]
    extra_pred_cols = [p for p in pred_cols if p not in set(mapping.values())]

    # Soft-F1 over cells (BIRD mini_dev method): TP = paired cells across the
    # aligned columns, FP = predicted cells that found no pair (incl. entire
    # extra columns), FN = gold cells that found no pair.
    tp = 0
    for g, p in mapping.items():
        if p is None:
            continue
        tp += _multiset_agreement(
            gold_values[g], pred_values[p], sig_digits=sig_digits, rel_tol=rel_tol
        )
    gold_cells = len(gold_rows_l) * len(gold_cols)
    pred_cells = len(pred_rows) * len(pred_cols)
    fn = gold_cells - tp
    fp = pred_cells - tp
    precision = tp / pred_cells if pred_cells else 0.0
    recall = tp / gold_cells if gold_cells else 0.0
    soft_f1 = (
        2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    )

    if unmatched_gold_cols:
        reasons.append(
            f"{len(unmatched_gold_cols)} gold column(s) have no matching "
            "predicted column."
        )
    if extra_pred_cols:
        reasons.append(f"{len(extra_pred_cols)} extra predicted column(s).")

    # Binary EX: aligned gold columns must reproduce the gold table exactly
    # (as multisets of aligned-row tuples; positionally when ordered).
    ex = not unmatched_gold_cols
    if ex:
        aligned_pred_cols = [mapping[g] for g in gold_cols]
        gold_tuples = _rows_as_tuples(gold_rows_l, gold_cols, casefold=casefold)
        pred_tuples = _rows_as_tuples(
            pred_rows, [c for c in aligned_pred_cols if c], casefold=casefold
        )
        if len(pred_tuples) != len(gold_tuples):
            ex = False
            reasons.append(
                f"Row count mismatch: agent returned {len(pred_tuples)}, "
                f"gold has {len(gold_tuples)}."
            )
        elif ordered:
            ex = all(
                len(a) == len(b)
                and all(
                    cells_match(x, y, sig_digits=sig_digits, rel_tol=rel_tol)
                    for x, y in zip(a, b, strict=False)
                )
                for a, b in zip(pred_tuples, gold_tuples, strict=False)
            )
            if not ex:
                # Tie-safe fallback: same multiset, different order, is only a
                # failure when ordering was demanded AND the multisets agree —
                # report it as an ordering mismatch for a precise reason.
                if _tuple_multiset_equal(
                    pred_tuples, gold_tuples, sig_digits=sig_digits, rel_tol=rel_tol
                ):
                    reasons.append("Rows match but ordering differs.")
                else:
                    reasons.append("Row values mismatch under ordered comparison.")
        else:
            ex = _tuple_multiset_equal(
                pred_tuples, gold_tuples, sig_digits=sig_digits, rel_tol=rel_tol
            )
            if not ex:
                reasons.append(f"{fn} gold cell(s) unmatched across aligned columns.")
    if ex and extra_pred_cols and extra_columns_policy == "strict":
        ex = False
        reasons.append("Extra columns fail exact match under the strict policy.")

    verdict: Verdict = "pass" if ex else "fail"
    return ComparisonOutcome(
        verdict=verdict,
        ex=ex,
        soft_f1=round(soft_f1, 6),
        matched_cells=tp,
        false_positive_cells=fp,
        false_negative_cells=fn,
        reasons=reasons,
    )
