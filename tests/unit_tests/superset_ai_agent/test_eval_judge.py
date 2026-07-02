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

"""LLM judge for eval_note items (P2.2): parsing, panel votes, degradation."""

from __future__ import annotations

from typing import Any

from superset_ai_agent.evals.judge import judge_eval_note
from superset_ai_agent.llm.base import ModelResult


class ScriptedModel:
    """Returns canned replies in order; repeats the last one."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0
        self.models_seen: list[str | None] = []

    def chat(self, messages: Any, *, model: str | None = None, **kwargs: Any):
        self.models_seen.append(model)
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if reply == "RAISE":
            raise RuntimeError("provider down")
        return ModelResult(content=reply)


def _judge(model: ScriptedModel, votes: int = 1):
    return judge_eval_note(
        model,
        question="Top region by revenue?",
        note="Must name EMEA and cite a revenue figure.",
        sql="SELECT ...",
        rows=[{"region": "EMEA", "revenue": 10}],
        summary="EMEA leads with 10.",
        votes=votes,
    )


def test_pass_verdict_with_critique() -> None:
    model = ScriptedModel(
        ['{"verdict": "pass", "critique": "Names EMEA and cites 10."}']
    )
    outcome = _judge(model)
    assert outcome.verdict == "pass"
    assert "EMEA" in outcome.critique
    assert model.calls == 1


def test_fail_verdict_and_json_embedded_in_prose_is_parsed() -> None:
    model = ScriptedModel(
        ['Here is my grading: {"verdict": "fail", "critique": "No figure."} Done.']
    )
    outcome = _judge(model)
    assert outcome.verdict == "fail"
    assert outcome.critique == "No figure."


def test_malformed_reply_degrades_to_needs_review() -> None:
    model = ScriptedModel(["I think it looks fine!"])
    outcome = _judge(model)
    assert outcome.verdict == "needs_review"
    assert "parseable" in outcome.critique


def test_provider_error_degrades_to_needs_review() -> None:
    model = ScriptedModel(["RAISE"])
    outcome = _judge(model)
    assert outcome.verdict == "needs_review"


def test_panel_majority_wins() -> None:
    model = ScriptedModel(
        [
            '{"verdict": "pass", "critique": "ok"}',
            '{"verdict": "fail", "critique": "missing figure"}',
            '{"verdict": "pass", "critique": "ok"}',
        ]
    )
    outcome = _judge(model, votes=3)
    assert outcome.verdict == "pass"
    assert outcome.votes == ["pass", "fail", "pass"]


def test_panel_tie_degrades_to_needs_review() -> None:
    model = ScriptedModel(
        [
            '{"verdict": "pass", "critique": "ok"}',
            '{"verdict": "fail", "critique": "nope"}',
        ]
    )
    outcome = _judge(model, votes=2)
    assert outcome.verdict == "needs_review"


def test_invalid_verdict_string_counts_as_needs_review_vote() -> None:
    model = ScriptedModel(['{"verdict": "maybe", "critique": "?"}'])
    outcome = _judge(model)
    assert outcome.verdict == "needs_review"
