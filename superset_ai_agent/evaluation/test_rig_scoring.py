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
"""Offline tests for rig.scoring (3-way dispatch; judge uses a fake client)."""

from __future__ import annotations

from dataclasses import dataclass

from rig import scoring


@dataclass
class _FakeResult:
    content: str


class _FakeModelClient:
    """Returns a scripted judge JSON, records the prompts it saw."""

    def __init__(self, verdict: str = "pass", critique: str = "meets rubric"):
        self._payload = f'{{"verdict": "{verdict}", "critique": "{critique}"}}'
        self.calls = 0

    def chat(self, messages, model=None):  # noqa: ANN001 - test double
        self.calls += 1
        return _FakeResult(self._payload)


def test_expected_values_pass_normalizes_to_correct():
    out = scoring.score_item(
        answer_type="expected_values",
        answer_spec={"nums": [6]},
        question="how many?",
        answer=scoring.AgentAnswer(status="ok", rows=[{"n": 6}]),
    )
    assert out.verdict == scoring.CORRECT


def test_expected_values_miss_is_wrong():
    out = scoring.score_item(
        answer_type="expected_values",
        answer_spec={"nums": [6]},
        question="how many?",
        answer=scoring.AgentAnswer(status="ok", rows=[{"n": 99}]),
    )
    assert out.verdict == scoring.WRONG


def test_trap_expected_values_pass():
    out = scoring.score_item(
        answer_type="expected_values",
        answer_spec={"trap": True},
        question="undefined?",
        answer=scoring.AgentAnswer(status="ok", rows=[]),
    )
    assert out.verdict == scoring.CORRECT


def test_agent_error_short_circuits():
    out = scoring.score_item(
        answer_type="expected_values",
        answer_spec={"nums": [6]},
        question="q",
        answer=scoring.AgentAnswer(status="error"),
    )
    assert out.verdict == scoring.ERROR


def test_gold_sql_matching_rows_correct():
    ans = scoring.AgentAnswer(status="ok", columns=["a"], rows=[{"a": 1}, {"a": 2}])
    gold = scoring.GoldResult(columns=["a"], rows=[{"a": 1}, {"a": 2}])
    out = scoring.score_item(
        answer_type="gold_sql",
        answer_spec={"sql": "SELECT a"},
        question="q",
        answer=ans,
        gold=gold,
    )
    assert out.verdict == scoring.CORRECT


def test_gold_sql_mismatch_wrong():
    ans = scoring.AgentAnswer(status="ok", columns=["a"], rows=[{"a": 1}])
    gold = scoring.GoldResult(columns=["a"], rows=[{"a": 2}])
    out = scoring.score_item(
        answer_type="gold_sql",
        answer_spec={"sql": "x"},
        question="q",
        answer=ans,
        gold=gold,
    )
    assert out.verdict == scoring.WRONG


def test_gold_sql_without_gold_is_error():
    out = scoring.score_item(
        answer_type="gold_sql",
        answer_spec={"sql": "x"},
        question="q",
        answer=scoring.AgentAnswer(status="ok", rows=[{"a": 1}]),
        gold=None,
    )
    assert out.verdict == scoring.ERROR


def test_eval_note_uses_judge_and_normalizes():
    fake = _FakeModelClient(verdict="pass", critique="rubric satisfied")
    out = scoring.score_item(
        answer_type="eval_note",
        answer_spec={"note": "must state the trend"},
        question="is it improving?",
        answer=scoring.AgentAnswer(status="ok", summary="up 3pp QoQ"),
        model_client=fake,
        judge_enabled=True,
        judge_votes=1,
    )
    assert out.verdict == scoring.CORRECT
    assert out.source == "llm_judge"
    assert fake.calls == 1
    assert "rubric satisfied" in out.reasons


def test_eval_note_disabled_needs_review():
    out = scoring.score_item(
        answer_type="eval_note",
        answer_spec={"note": "rubric"},
        question="q",
        answer=scoring.AgentAnswer(status="ok"),
        model_client=None,
        judge_enabled=False,
    )
    assert out.verdict == scoring.NEEDS_REVIEW


def test_eval_note_no_client_needs_review():
    out = scoring.score_item(
        answer_type="eval_note",
        answer_spec={"note": "rubric"},
        question="q",
        answer=scoring.AgentAnswer(status="ok"),
        model_client=None,
        judge_enabled=True,
    )
    assert out.verdict == scoring.NEEDS_REVIEW


def test_answer_from_response_adapter():
    resp = {
        "status": "ok",
        "sql": "SELECT 1",
        "answer_summary": "one",
        "execution_result": {"rows": [{"a": 1}], "columns": ["a"]},
    }
    ans = scoring.answer_from_response(resp)
    assert ans.rows == [{"a": 1}]
    assert ans.columns == ["a"]
    assert ans.sql == "SELECT 1"
    assert ans.summary == "one"
