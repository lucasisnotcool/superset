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
"""Offline tests for the headless runner's pure logic (no live server)."""

from __future__ import annotations

import pytest
import run_eval_v2 as runner


def test_parse_args_defaults():
    args = runner.parse_args([])
    assert args.fixture == "seagate_multi"
    assert args.schema_name == "seagate_ops"
    assert args.schema_name_list == ["seagate_core", "seagate_ops"]
    assert args.condition_list == list(runner.DEFAULT_CONDITIONS)
    assert args.trials == 1


def test_parse_args_rejects_unknown_condition():
    with pytest.raises(SystemExit):
        runner.parse_args(["--conditions", "basic,bogus"])


def test_parse_args_schema_names_excludes_ref_by_default():
    # The out-of-scope seagate_ref schema must not be in the default project scope.
    args = runner.parse_args([])
    assert "seagate_ref" not in args.schema_name_list


def _rows(qid_to_rows):
    return [
        {"id": q, "result_rows": rows, "answer_summary": None}
        for q, rows in qid_to_rows.items()
    ]


def test_score_sweep_counts_correct_verdicts():
    results = _rows({"Q1": [{"v": 6}], "Q3": [{"v": 999}]})  # Q1 right, Q3 wrong
    correct, verdicts = runner.score_sweep(results)
    assert verdicts["Q1"] == "correct"
    assert verdicts["Q3"] == "wrong"
    assert correct == 1


def test_build_headline_breaks_out_cross_schema_only():
    named = {
        "wren_bi": _rows(
            {
                "Q1": [{"v": 6}],  # relevant single-schema, correct
                "Q16": [{"v": 1751}, {"v": 3017}],  # cross-schema-only, correct
                "Q17": [{"v": 0.951}],  # cross-schema-only, correct
                "Q18": [{"v": 0}],  # cross-schema-only, wrong
            }
        ),
    }
    rows = runner.build_headline(named)
    assert len(rows) == 1
    row = rows[0]
    assert row["condition"] == "wren_bi"
    assert row["correct_of_18"] == 3  # Q1 + Q16 + Q17
    assert row["cross_schema_only_of_3"] == 2  # Q16 + Q17 (Q18 wrong)


def test_format_headline_is_tabular():
    rows = [{"condition": "basic", "correct_of_18": 4, "cross_schema_only_of_3": 0}]
    out = runner.format_headline(rows)
    assert "condition" in out
    assert "basic" in out


def test_condition_groups_partition_defaults():
    assert set(runner.NO_MDL) | set(runner.WITH_MDL) == set(runner.DEFAULT_CONDITIONS)
    assert not (set(runner.NO_MDL) & set(runner.WITH_MDL))


# --- multi-trial aggregation (result refinement) ---------------------------- #
def _headline(basic, ctx):
    return [
        {"condition": "basic", "correct_of_18": basic, "cross_schema_only_of_3": 0},
        {
            "condition": "context_dump",
            "correct_of_18": ctx,
            "cross_schema_only_of_3": 1,
        },
    ]


def test_aggregate_trials_computes_mean_min_max():
    agg = runner.aggregate_trials([_headline(2, 9), _headline(4, 11), _headline(3, 10)])
    basic = next(r for r in agg if r["condition"] == "basic")
    assert basic["trials"] == 3
    assert basic["correct_of_18"] == {"mean": 3.0, "min": 2, "max": 4}
    ctx = next(r for r in agg if r["condition"] == "context_dump")
    assert ctx["correct_of_18"]["mean"] == 10.0


def test_aggregate_trials_preserves_condition_order():
    agg = runner.aggregate_trials([_headline(2, 9)])
    assert [r["condition"] for r in agg] == ["basic", "context_dump"]


def test_aggregate_trials_handles_condition_missing_in_some_trials():
    t1 = _headline(2, 9)
    t2 = [{"condition": "basic", "correct_of_18": 4, "cross_schema_only_of_3": 0}]
    agg = runner.aggregate_trials([t1, t2])
    basic = next(r for r in agg if r["condition"] == "basic")
    assert basic["trials"] == 2
    assert basic["correct_of_18"]["mean"] == 3.0
    ctx = next(r for r in agg if r["condition"] == "context_dump")
    assert ctx["trials"] == 1


def test_format_aggregate_renders_mean_and_range():
    agg = runner.aggregate_trials([_headline(2, 9), _headline(4, 11)])
    out = runner.format_aggregate(agg)
    assert "basic" in out
    assert "[2-4]" in out  # min-max range shown
