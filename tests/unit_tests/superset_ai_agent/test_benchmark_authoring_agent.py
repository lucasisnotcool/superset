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

"""Authoring agent loop: validation, probes, self-correction (plan P2.1-P2.5)."""

from __future__ import annotations

from typing import Any

from superset_ai_agent.evals.authoring.author_agent import (
    assemble_context_doc,
    AuthoringDraft,
    run_authoring,
    SqlProbe,
    validate_human_items,
)
from superset_ai_agent.evals.authoring.corpus_csv import parse_corpus_csv
from superset_ai_agent.llm.base import ModelResult, ToolCall
from superset_ai_agent.schemas import AgentStep

HEADER = (
    "type,question,gold_sql,expected_values,eval_note,"
    "answer_type,capability_tags,target_schema,context,notes\n"
)


def ok_executor(sql: str) -> SqlProbe:
    return SqlProbe(ok=True, row_count=3, columns=("a",), rows_preview=({"a": 1},))


def failing_executor(sql: str) -> SqlProbe:
    return SqlProbe(ok=False, error="ORA-00942: table or view does not exist")


class ScriptedModel:
    """Returns canned ModelResults in order; repeats the last; can raise."""

    def __init__(self, results: list[ModelResult | Exception]) -> None:
        self.results = results
        self.calls = 0
        self.seen_messages: list[list[Any]] = []

    def chat(self, messages, *, model=None, format_schema=None, tools=None):
        self.seen_messages.append(list(messages))
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def propose(items: list[dict[str, Any]], call_id: str = "c1") -> ModelResult:
    return ModelResult(
        content="",
        tool_calls=[
            ToolCall(id=call_id, name="propose_items", arguments={"items": items})
        ],
    )


def finish(call_id: str = "f1") -> ModelResult:
    return ModelResult(
        content="",
        tool_calls=[ToolCall(id=call_id, name="finish", arguments={"summary": "done"})],
    )


# --- deterministic paths -----------------------------------------------------


def test_human_items_validated_without_model():
    corpus = parse_corpus_csv(
        HEADER  # noqa: S608 - CSV test fixture, not SQL construction
        + "question,How many?,SELECT COUNT(*) FROM t,,,,metric,,,\n"
        + "question,Total?,,42,,,,,,\n"
        + "question,Churn ok?,,,Must cite churn definition,,,,,\n"
    )
    model = ScriptedModel([finish()])
    draft = run_authoring(
        corpus=corpus, model_client=model, execute_sql=ok_executor, mode="extract"
    )
    assert model.calls == 0  # nothing needed the model
    assert [i.origin for i in draft.items] == ["human", "human", "human"]
    assert [i.validation for i in draft.items] == ["verified"] * 3


def test_failing_human_gold_sql_flagged_needs_review():
    corpus = parse_corpus_csv(
        HEADER + "question,How many?,SELECT x FROM missing,,,,,,,\n"  # noqa: S608
    )
    items = validate_human_items(corpus, failing_executor, lambda s: None)
    assert items[0].validation == "needs_review"
    assert "ORA-00942" in items[0].problems[0]


def test_zero_row_gold_sql_flagged_for_review():
    corpus = parse_corpus_csv(HEADER + "question,Empty?,SELECT 1 WHERE 1=0,,,,,,,\n")
    items = validate_human_items(
        corpus, lambda sql: SqlProbe(ok=True, row_count=0), lambda s: None
    )
    assert items[0].validation == "needs_review"
    assert "no rows" in items[0].problems[0]


def test_context_doc_is_verbatim():
    corpus = parse_corpus_csv(
        HEADER
        + 'context,,,,,,,,"Revenue is net of tax.",\n'
        + 'context,,,,,,,,"A widget is one physical unit.",\n'
    )
    doc = assemble_context_doc(corpus)
    assert "Revenue is net of tax." in doc
    assert "A widget is one physical unit." in doc
    assert assemble_context_doc(parse_corpus_csv(HEADER)) is None


# --- the model loop ----------------------------------------------------------


def test_extract_mode_authors_open_question():
    corpus = parse_corpus_csv(HEADER + "question,Who buys most?,,,,,,,,\n")
    model = ScriptedModel(
        [
            propose(
                [
                    {
                        "question": "Who buys most?",
                        "answer_type": "gold_sql",
                        "answer_spec": {"sql": "SELECT buyer FROM orders"},
                        "capability_tags": ["aggregation"],
                    }
                ]
            ),
            finish(),
        ]
    )
    steps: list[AgentStep] = []
    draft = run_authoring(
        corpus=corpus,
        model_client=model,
        execute_sql=ok_executor,
        mode="extract",
        on_step=steps.append,
    )
    assert len(draft.items) == 1
    item = draft.items[0]
    assert item.origin == "extracted"
    assert item.validation == "verified"
    assert item.capability_tags == ["aggregation"]
    assert any(s.kind == "authoring_sql_probe" for s in steps)
    assert any(s.kind == "authoring_done" for s in steps)


def test_failed_probe_feeds_back_and_self_corrects():
    corpus = parse_corpus_csv(HEADER + "question,Who buys most?,,,,,,,,\n")
    fails_then_ok = iter([failing_executor, ok_executor])

    def executor(sql: str) -> SqlProbe:
        return next(fails_then_ok)(sql)

    bad = propose(
        [
            {
                "question": "Who buys most?",
                "answer_type": "gold_sql",
                "answer_spec": {"sql": "SELECT bad"},
            }
        ]
    )
    good = propose(
        [
            {
                "question": "Who buys most?",
                "answer_type": "gold_sql",
                "answer_spec": {"sql": "SELECT good"},
            }
        ],
        call_id="c2",
    )
    model = ScriptedModel([bad, good, finish()])
    draft = run_authoring(
        corpus=corpus, model_client=model, execute_sql=executor, mode="extract"
    )
    assert len(draft.items) == 1
    assert draft.items[0].validation == "verified"
    # The failure round-trip reached the model as tool feedback.
    tool_feedback = [
        m.content
        for turn in model.seen_messages
        for m in turn
        if getattr(m, "role", "") == "tool"
    ]
    assert any("fix and re-propose" in c for c in tool_feedback)


def test_retry_exhaustion_accepts_flagged_item():
    corpus = parse_corpus_csv(HEADER + "question,Who buys most?,,,,,,,,\n")
    proposal = {
        "question": "Who buys most?",
        "answer_type": "gold_sql",
        "answer_spec": {"sql": "SELECT bad"},
    }
    model = ScriptedModel(
        [propose([proposal]), propose([proposal]), propose([proposal]), finish()]
    )
    draft = run_authoring(
        corpus=corpus, model_client=model, execute_sql=failing_executor, mode="extract"
    )
    assert len(draft.items) == 1
    assert draft.items[0].validation == "needs_review"
    assert any("gold SQL failed" in p for p in draft.items[0].problems)


def test_generate_mode_marks_origin_generated():
    corpus = parse_corpus_csv(HEADER)  # no questions at all
    model = ScriptedModel(
        [
            propose(
                [
                    {
                        "question": "What is the average order value?",
                        "answer_type": "eval_note",
                        "answer_spec": {"note": "Must compute AOV = revenue/orders."},
                        "capability_tags": ["metric"],
                    }
                ]
            ),
            finish(),
        ]
    )
    draft = run_authoring(
        corpus=corpus, model_client=model, execute_sql=ok_executor, mode="generate"
    )
    assert draft.items[0].origin == "generated"
    assert draft.items[0].validation == "verified"


def test_bad_proposals_rejected_with_feedback_and_unknown_tags_warn():
    corpus = parse_corpus_csv(HEADER + "question,Q1?,,,,,,,,\n")
    model = ScriptedModel(
        [
            propose(
                [
                    {
                        "question": "",
                        "answer_type": "gold_sql",
                        "answer_spec": {"sql": "x"},
                    },
                    {"question": "Q1?", "answer_type": "bogus", "answer_spec": {}},
                    {
                        "question": "Q1?",
                        "answer_type": "eval_note",
                        "answer_spec": {"note": "rubric"},
                        "capability_tags": ["made_up_tag"],
                    },
                ]
            ),
            finish(),
        ]
    )
    draft = run_authoring(
        corpus=corpus, model_client=model, execute_sql=ok_executor, mode="extract"
    )
    assert len(draft.items) == 1  # only the valid third proposal landed
    assert any("unknown capability tag" in w for w in draft.warnings)
    feedback = [
        m.content
        for turn in model.seen_messages
        for m in turn
        if getattr(m, "role", "") == "tool"
    ]
    assert any("rejected" in c for c in feedback)


def test_model_failure_keeps_human_items():
    corpus = parse_corpus_csv(
        HEADER + "question,Done one,SELECT 1,,,,,,,\n" + "question,Open one?,,,,,,,,\n"
    )
    model = ScriptedModel([RuntimeError("provider down")])
    draft = run_authoring(
        corpus=corpus, model_client=model, execute_sql=ok_executor, mode="extract"
    )
    assert draft.model_failed is True
    assert [i.origin for i in draft.items] == ["human"]
    assert any("no item authored" in w for w in draft.warnings)


def test_step_budget_warning():
    corpus = parse_corpus_csv(HEADER + "question,Open?,,,,,,,,\n")
    chatty = ModelResult(content="thinking out loud", tool_calls=[])
    model = ScriptedModel([chatty])
    draft = run_authoring(
        corpus=corpus,
        model_client=model,
        execute_sql=ok_executor,
        mode="extract",
        max_steps=2,
    )
    assert isinstance(draft, AuthoringDraft)
    assert any("step budget" in w for w in draft.warnings)
