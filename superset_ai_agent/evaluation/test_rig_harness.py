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
"""Offline tests for rig.harness (pure math + orchestration via a fake client)."""

from __future__ import annotations

from dataclasses import dataclass

from rig import harness
from rig.corpus import QuestionRecord
from rig.fixture import Fixture
from rig.model_client import JudgeSettings


# --- pure helpers ----------------------------------------------------------- #
def test_capability_map():
    recs = [
        QuestionRecord("Q1", "q", "expected_values", {"nums": [1]}, ("metric",)),
        QuestionRecord("Q2", "q", "eval_note", {"note": "x"}, ("slang", "temporal")),
    ]
    assert harness.capability_map(recs) == {
        "Q1": ("metric",),
        "Q2": ("slang", "temporal"),
    }


def test_context_text_concatenates(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("alpha")
    b = tmp_path / "b.md"
    b.write_text("beta")
    fixture = Fixture(
        id="f",
        schemas=("S",),
        corpus_path=tmp_path / "c.csv",
        manifest_dir=tmp_path,
        context_docs=(a, b),
    )
    assert harness.context_text(fixture) == "alpha\n\nbeta"


def test_build_scoreboard_totals_and_deltas():
    trials = [
        {
            "basic": {"Q1": "correct", "Q2": "wrong"},
            "context_dump": {"Q1": "correct", "Q2": "correct"},
            "wren_base": {"Q1": "wrong", "Q2": "wrong"},
            "wren_bi": {"Q1": "correct", "Q2": "wrong"},
        }
    ]
    cap = {"Q1": ("metric",), "Q2": ("slang",)}
    sb = harness.build_scoreboard(
        trials,
        capability=cap,
        meta={"fixture_id": "f"},
        config_names=["basic", "context_dump", "wren_base", "wren_bi"],
    )
    assert sb["by_config"]["context_dump"]["total"]["mean"] == 2.0
    assert sb["by_config"]["wren_base"]["total"]["mean"] == 0.0
    # enrichment = wren_bi(1) - wren_base(0) = 1; layer vs raw = wren_bi(1) - dump(2)
    assert sb["deltas"]["enrichment (wren_bi − wren_base)"] == 1.0
    assert sb["deltas"]["layer vs raw context (wren_bi − context_dump)"] == -1.0
    assert sb["deltas"]["context lift (context_dump − basic)"] == 1.0


def test_build_scoreboard_omits_absent_deltas():
    trials = [{"basic": {"Q1": "correct"}}]
    sb = harness.build_scoreboard(
        trials, capability={"Q1": ()}, meta={}, config_names=["basic"]
    )
    assert sb["deltas"] == {}  # no wren/dump configs -> no deltas


# --- live orchestration via a fake client ----------------------------------- #
@dataclass
class _FakeResp:
    content: str


class _FakeModelClient:
    def chat(self, messages, model=None):  # noqa: ANN001
        return _FakeResp('{"verdict":"pass","critique":"ok"}')


class _FakeClient:
    """Answers questions and executes gold SQL from scripted maps."""

    def __init__(self, answers, sql_results=None):
        self.answers = answers
        self.sql_results = sql_results or {}

    def resolve_database_id(self):
        return 1

    def list_projects(self):
        return []

    def delete_project(self, pid):
        pass

    def query(self, question, execute=True, extra_context=None):
        return self.answers[question]

    def _superset(self, method, path, **kwargs):
        sql = (kwargs.get("json") or {}).get("sql")
        return self.sql_results.get(sql, {"data": [], "columns": []})

    def _ok(self, resp, what):
        return resp


def _ctx(client):
    return harness.RunContext(
        client=client,
        database_id=1,
        schema="S",
        model_client=_FakeModelClient(),
        judge=JudgeSettings(enabled=True, votes=1),
    )


def test_grade_sweep_mixed_answer_types():
    recs = [
        QuestionRecord("Q1", "count?", "expected_values", {"nums": [6]}, ("slang",)),
        QuestionRecord("Q2", "rate?", "gold_sql", {"sql": "SELECT r"}, ("metric",)),
        QuestionRecord(
            "Q3", "trend?", "eval_note", {"note": "state trend"}, ("metric",)
        ),
    ]
    answers = {
        "count?": {
            "status": "ok",
            "execution_result": {"rows": [{"n": 6}], "columns": ["n"]},
        },
        "rate?": {
            "status": "ok",
            "sql": "SELECT r",
            "execution_result": {"rows": [{"r": 1}], "columns": ["r"]},
        },
        "trend?": {
            "status": "ok",
            "answer_summary": "up QoQ",
            "execution_result": {"rows": [], "columns": []},
        },
    }
    sql_results = {"SELECT r": {"data": [{"r": 1}], "columns": [{"name": "r"}]}}
    verdicts = harness.grade_sweep(_ctx(_FakeClient(answers, sql_results)), recs)
    assert verdicts == {"Q1": "correct", "Q2": "correct", "Q3": "correct"}


def test_gold_sql_that_disagrees_is_wrong():
    recs = [QuestionRecord("Q1", "rate?", "gold_sql", {"sql": "SELECT r"}, ())]
    answers = {
        "rate?": {
            "status": "ok",
            "execution_result": {"rows": [{"r": 9}], "columns": ["r"]},
        }
    }
    sql_results = {"SELECT r": {"data": [{"r": 1}], "columns": [{"name": "r"}]}}
    verdicts = harness.grade_sweep(_ctx(_FakeClient(answers, sql_results)), recs)
    assert verdicts == {"Q1": "wrong"}


def test_run_trial_basic_and_context_dump(tmp_path):
    recs = [
        QuestionRecord("Q1", "count?", "expected_values", {"nums": [6]}, ("slang",))
    ]
    answers = {
        "count?": {
            "status": "ok",
            "execution_result": {"rows": [{"n": 6}], "columns": ["n"]},
        }
    }
    ctxdoc = tmp_path / "ctx.md"
    ctxdoc.write_text("business context")
    fixture = Fixture(
        id="f",
        schemas=("S",),
        corpus_path=tmp_path / "c.csv",
        manifest_dir=tmp_path,
        onboard_mode="none",
        context_docs=(ctxdoc,),
        grounding_modes=("basic", "context_dump"),
    )
    out = harness.run_trial(_ctx(_FakeClient(answers)), fixture, recs, trial=1)
    assert set(out) == {"basic", "context_dump"}
    assert out["basic"] == {"Q1": "correct"}


def test_qids_subset_filters():
    recs = [
        QuestionRecord("Q1", "a?", "expected_values", {"nums": [1]}, ()),
        QuestionRecord("Q2", "b?", "expected_values", {"nums": [2]}, ()),
    ]
    answers = {
        "a?": {"status": "ok", "execution_result": {"rows": [{"n": 1}]}},
        "b?": {"status": "ok", "execution_result": {"rows": [{"n": 2}]}},
    }
    ctx = _ctx(_FakeClient(answers))
    ctx.qids = ["Q2"]
    verdicts = harness.grade_sweep(ctx, recs)
    assert verdicts == {"Q2": "correct"}
