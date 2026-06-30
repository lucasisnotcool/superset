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
"""Unit tests for the ground-truth scorer (seagate_scoring), incl. Q16-Q18."""

from __future__ import annotations

import seagate_scoring as sc


def _rows(*values):
    return [{"v": v} for v in values]


def test_expected_covers_q1_through_q18():
    # Q1-Q18 are frozen (byte-identical core data); v4 adds Q19+ (supply schema etc.).
    assert [f"Q{i}" for i in range(1, 19)] == list(sc.EXPECTED)[:18]


def test_v4_questions_have_capability_tags():
    # Every gradable question carries a capability tag (and vice versa).
    assert set(sc.EXPECTED) == set(sc.CAPABILITY)


def test_zero_spec_grades_negative_result():
    assert sc.score_result("Q21", [], None) == "correct"  # empty == none
    assert sc.score_result("Q21", [{"v": 0}], None) == "correct"
    assert sc.score_result("Q21", [{"v": 1751}], None) == "wrong"


def test_cross_schema_only_set_matches_l5_questions():
    assert sc.CROSS_SCHEMA_ONLY == ("Q16", "Q17", "Q18")
    for qid in sc.CROSS_SCHEMA_ONLY:
        assert qid in sc.EXPECTED


def test_single_value_match_and_mismatch():
    assert sc.score_result("Q1", _rows(6), None) == "correct"
    assert sc.score_result("Q1", _rows(7), None) == "wrong"


def test_percentage_dual_scale_accepts_either_representation():
    # Golden Yield 0.961 may be reported as 0.961 or 96.1.
    assert sc.score_result("Q9", _rows(0.961), None) == "correct"
    assert sc.score_result("Q9", _rows(96.1), None) == "correct"


def test_trap_question_scoring():
    assert sc.score_result("Q12", _rows(), "undefined") == "trap_ok"
    assert sc.score_result("Q12", _rows(0.903), None) == "trap_fail"


def test_names_question_requires_all_present_and_absent_excluded():
    names = ["Shugart Yard", "Scotts Valley West", "Reef Hollow"]
    good = [{"site_name": n} for n in names]
    assert sc.score_result("Q5", good, None) == "correct"
    # Tigerline Point must NOT appear: a forbidden name present demotes the
    # otherwise-complete answer below "correct" (the scorer reports "partial"
    # because all three required names are still present).
    bad = good + [{"site_name": "Tigerline Point"}]
    assert sc.score_result("Q5", bad, None) != "correct"
    partial = [{"site_name": "Shugart Yard"}]
    assert sc.score_result("Q5", partial, None) == "partial"


def test_q16_cross_schema_multivalue():
    assert sc.score_result("Q16", _rows(1751, 3017), None) == "correct"
    assert sc.score_result("Q16", _rows(1751), None) == "partial"
    assert sc.score_result("Q16", _rows(0, 0), None) == "wrong"


def test_q17_cross_schema_golden_yield():
    assert sc.score_result("Q17", _rows(0.951), None) == "correct"
    assert sc.score_result("Q17", _rows(95.1), None) == "correct"
    assert sc.score_result("Q17", _rows(0.80), None) == "wrong"


def test_q18_cross_schema_all_four_values():
    assert sc.score_result("Q18", _rows(175, 151, 0.960, 0.962), None) == "correct"
    assert sc.score_result("Q18", _rows(175, 151), None) == "partial"


def test_correct_verdicts_constant():
    assert sc.CORRECT_VERDICTS == {"correct", "trap_ok"}
