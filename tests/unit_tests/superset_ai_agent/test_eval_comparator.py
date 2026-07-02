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

"""Comparator v2: the normative spec §16 rules, each pinned by a test."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from superset_ai_agent.evals.comparator import (
    canonicalize_cell,
    cells_match,
    compare_result_sets,
    numbers_match,
)
from superset_ai_agent.evals.typed_spec import (
    score_expected_values,
    validate_expected_values_spec,
)


def _rows(cols: list[str], data: list[list]) -> list[dict]:
    return [dict(zip(cols, row, strict=False)) for row in data]


# --- canonicalization -------------------------------------------------------


def test_numeric_strings_compare_as_numbers() -> None:
    assert canonicalize_cell(" 42 ") == 42.0
    assert cells_match(canonicalize_cell("42"), canonicalize_cell(42))


def test_decimal_and_date_canonicalize() -> None:
    assert canonicalize_cell(Decimal("1.5")) == 1.5
    assert canonicalize_cell(date(2026, 7, 3)) == "2026-07-03"
    assert canonicalize_cell(
        datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    ).startswith("2026-07-03T12:00")


def test_bool_stays_distinct_from_int() -> None:
    assert not cells_match(canonicalize_cell(True), canonicalize_cell(1))


def test_none_matches_only_none() -> None:
    assert cells_match(None, None)
    assert not cells_match(None, canonicalize_cell(0))


def test_nan_normalizes_to_none() -> None:
    assert canonicalize_cell(float("nan")) is None


# --- numeric tolerance ------------------------------------------------------


def test_four_sig_digits_default_matches_genie_rule() -> None:
    # Same 4 significant digits => match.
    assert numbers_match(0.123449, 0.12341)
    assert not numbers_match(0.12345, 0.12395)
    assert numbers_match(123449.0, 123400.0)  # both round to 1.234e5


def test_relative_tolerance_override() -> None:
    assert numbers_match(100.0, 101.9, rel_tol=0.02)
    assert not numbers_match(100.0, 103.0, rel_tol=0.02)


# --- exact match (EX) -------------------------------------------------------


def test_identical_tables_pass() -> None:
    cols = ["region", "revenue"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [["emea", 10], ["apac", 20]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [["emea", 10], ["apac", 20]]),
    )
    assert out.ex
    assert out.verdict == "pass"
    assert out.soft_f1 == 1.0


def test_row_order_is_ignored_by_default() -> None:
    cols = ["r", "v"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [["b", 2], ["a", 1]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [["a", 1], ["b", 2]]),
    )
    assert out.ex


def test_ordered_mode_fails_on_wrong_order_with_precise_reason() -> None:
    cols = ["r", "v"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [["b", 2], ["a", 1]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [["a", 1], ["b", 2]]),
        ordered=True,
    )
    assert not out.ex
    assert any("ordering differs" in r for r in out.reasons)


def test_column_aliases_do_not_matter() -> None:
    out = compare_result_sets(
        predicted_columns=["some_alias", "x"],
        predicted_rows=_rows(["some_alias", "x"], [["emea", 10]]),
        gold_columns=["region", "revenue"],
        gold_rows=_rows(["region", "revenue"], [["emea", 10]]),
    )
    assert out.ex


def test_column_order_does_not_matter() -> None:
    out = compare_result_sets(
        predicted_columns=["v", "r"],
        predicted_rows=_rows(["v", "r"], [[10, "emea"]]),
        gold_columns=["r", "v"],
        gold_rows=_rows(["r", "v"], [["emea", 10]]),
    )
    assert out.ex


def test_multiset_semantics_duplicates_must_match() -> None:
    cols = ["v"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [[1], [1]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [[1], [2]]),
    )
    assert not out.ex


def test_row_count_mismatch_named_reason() -> None:
    cols = ["v"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [[1]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [[1], [2]]),
    )
    assert not out.ex
    assert any("Row count mismatch" in r for r in out.reasons)


# --- extra columns policy ---------------------------------------------------


def test_extra_columns_fail_strict_but_score_soft_f1() -> None:
    out = compare_result_sets(
        predicted_columns=["r", "v", "extra"],
        predicted_rows=_rows(["r", "v", "extra"], [["emea", 10, "x"]]),
        gold_columns=["r", "v"],
        gold_rows=_rows(["r", "v"], [["emea", 10]]),
        extra_columns_policy="strict",
    )
    assert not out.ex
    assert any("Extra columns" in r for r in out.reasons)
    assert 0 < out.soft_f1 < 1.0  # partial credit survives


def test_extra_columns_pass_lenient() -> None:
    out = compare_result_sets(
        predicted_columns=["r", "v", "extra"],
        predicted_rows=_rows(["r", "v", "extra"], [["emea", 10, "x"]]),
        gold_columns=["r", "v"],
        gold_rows=_rows(["r", "v"], [["emea", 10]]),
        extra_columns_policy="lenient",
    )
    assert out.ex


# --- empty results ----------------------------------------------------------


def test_empty_vs_empty_is_low_confidence_pass() -> None:
    out = compare_result_sets(
        predicted_columns=[],
        predicted_rows=[],
        gold_columns=[],
        gold_rows=[],
    )
    assert out.ex
    assert out.low_confidence


def test_agent_empty_gold_nonempty_fails() -> None:
    out = compare_result_sets(
        predicted_columns=["v"],
        predicted_rows=[],
        gold_columns=["v"],
        gold_rows=_rows(["v"], [[1]]),
    )
    assert not out.ex
    assert out.false_negative_cells == 1


def test_gold_empty_agent_nonempty_fails() -> None:
    out = compare_result_sets(
        predicted_columns=["v"],
        predicted_rows=_rows(["v"], [[1]]),
        gold_columns=["v"],
        gold_rows=[],
    )
    assert not out.ex
    assert out.false_positive_cells == 1


# --- soft F1 ----------------------------------------------------------------


def test_soft_f1_partial_credit() -> None:
    cols = ["r", "v"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [["emea", 10], ["apac", 999]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [["emea", 10], ["apac", 20]]),
    )
    assert not out.ex
    assert 0.5 < out.soft_f1 < 1.0
    assert out.matched_cells == 3


def test_numeric_tolerance_applies_inside_table_compare() -> None:
    cols = ["v"]
    out = compare_result_sets(
        predicted_columns=cols,
        predicted_rows=_rows(cols, [[0.123449]]),
        gold_columns=cols,
        gold_rows=_rows(cols, [[0.12341]]),
    )
    assert out.ex


# --- typed expected-values spec ---------------------------------------------


def test_typed_nums_pass_with_percent_scale() -> None:
    outcome = score_expected_values(
        {"nums": [0.57]}, [{"rate_pct": 57.1}]
    )
    assert outcome.verdict == "pass"


def test_typed_nums_partial_is_fail_with_reason() -> None:
    outcome = score_expected_values(
        {"nums": [10, 99]}, [{"a": 10}]
    )
    assert outcome.verdict == "fail"
    assert any("Partial match" in r for r in outcome.reasons)


def test_typed_names_and_absent() -> None:
    rows = [{"region": "EMEA"}, {"region": "APAC"}]
    ok = score_expected_values({"names": ["emea", "apac"]}, rows)
    assert ok.verdict == "pass"
    bad = score_expected_values(
        {"names": ["emea"], "absent": ["apac"]}, rows
    )
    assert bad.verdict == "fail"


def test_typed_trap_and_zero() -> None:
    trap_ok = score_expected_values({"trap": True}, [{"note": "cannot say"}])
    assert trap_ok.verdict == "pass"
    assert score_expected_values({"trap": True}, [{"n": 5}]).verdict == "fail"
    assert score_expected_values({"zero": True}, []).verdict == "pass"
    assert score_expected_values({"zero": True}, [{"n": 3}]).verdict == "fail"


def test_typed_multi_value_degrades_to_review() -> None:
    outcome = score_expected_values(
        {"nums": [10], "multi_value": True}, [{"a": 10}]
    )
    assert outcome.verdict == "needs_review"


def test_spec_validation_catches_shape_errors() -> None:
    assert validate_expected_values_spec({"nums": [1]}) == []
    assert validate_expected_values_spec({}) != []
    assert validate_expected_values_spec({"nums": ["x"]}) != []
    assert validate_expected_values_spec({"trap": True, "zero": True}) != []
    assert validate_expected_values_spec({"bogus": 1, "nums": [1]}) != []
