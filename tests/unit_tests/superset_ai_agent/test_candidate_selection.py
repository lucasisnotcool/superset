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

"""Dual-candidate drafting + pairwise selection (C3)."""

from __future__ import annotations

import json  # noqa: TID251 - tests cover the standalone agent JSON contract

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.graph import llm_select_candidate, TextToSqlGraph
from superset_ai_agent.llm.base import ModelResult
from superset_ai_agent.schemas import AgentQueryRequest, WrenContextArtifact
from tests.unit_tests.superset_ai_agent.test_graph import (
    FakeContextProvider,
    FakeSupersetClient,
)


class _SequenceModelClient:
    """Returns each payload once, in order (draft A, draft B, judge...)."""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls = 0
        self.messages = []

    def chat(self, messages, *, model=None, format_schema=None):
        self.messages.append(messages)
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return ModelResult(content=json.dumps(payload))


class _ContextWrenClient:
    """Wren client whose context carries items (so C3's gate fires)."""

    def fetch_context(self, *, question, superset_context, mdl_path=None):
        return WrenContextArtifact(
            enabled=True,
            available=True,
            matched_models=["birth_names"],
            context_items=[{"type": "model", "model": {"name": "birth_names"}}],
        )

    def dry_plan(self, **kwargs):
        return {"available": True, "planning_only": True}

    def is_available(self) -> bool:
        return True

    def list_models(self):
        return ["birth_names"]


def test_llm_select_candidate_parses_choice() -> None:
    model = _SequenceModelClient([{"choice": "b", "reason": "better join"}])
    assert llm_select_candidate(model, "q", "A", "B") == ("b", "better join")


def test_llm_select_candidate_degrades_to_semantic() -> None:
    class _Boom:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    choice, reason = llm_select_candidate(_Boom(), "q", "A", "B")
    assert choice == "a"
    assert "semantic" in reason.lower()
    bad = _SequenceModelClient([{"choice": "c"}])
    assert llm_select_candidate(bad, "q", "A", "B")[0] == "a"


def _dual_graph(payloads: list[dict]) -> tuple[TextToSqlGraph, _SequenceModelClient]:
    model = _SequenceModelClient(payloads)
    graph = TextToSqlGraph(
        config=AgentConfig(wren_dual_candidate_enabled=True),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=_ContextWrenClient(),
    )
    return graph, model


def test_dual_candidate_judge_picks_raw_candidate() -> None:
    semantic_sql = "SELECT name FROM birth_names"
    raw_sql = "SELECT name, num FROM birth_names"
    graph, model = _dual_graph(
        [
            {"sql": semantic_sql, "explanation": "semantic"},
            {"sql": raw_sql, "explanation": "raw"},
            {"choice": "b", "reason": "raw carries the needed column"},
        ]
    )
    response = graph.run(
        AgentQueryRequest(question="names", database_id=1, schema_name="main")
    )
    assert model.calls == 3  # two drafts + one judge call
    assert response.sql is not None
    assert response.sql.startswith(raw_sql)
    events = [e for e in response.trace if e.step == "select_sql_candidate"]
    assert len(events) == 1
    assert events[0].details["chosen"] == "raw"
    step = next(s for s in response.timeline if s.kind == "select_sql_candidate")
    assert step.detail is not None
    assert step.detail.kind == "candidate_selection"
    assert step.detail.chosen == "raw"
    assert step.detail.semantic_valid
    assert step.detail.raw_valid


def test_dual_candidate_skips_judge_when_one_candidate_invalid() -> None:
    semantic_sql = "SELECT name FROM birth_names"
    graph, model = _dual_graph(
        [
            {"sql": semantic_sql, "explanation": "semantic"},
            {"sql": "DROP TABLE birth_names", "explanation": "raw (mutating)"},
            # A judge payload that must never be consumed.
            {"choice": "b", "reason": "should not be asked"},
        ]
    )
    response = graph.run(
        AgentQueryRequest(question="names", database_id=1, schema_name="main")
    )
    assert model.calls == 2  # validity gate resolved it; no judge call
    assert response.sql is not None
    assert response.sql.startswith(semantic_sql)
    event = next(e for e in response.trace if e.step == "select_sql_candidate")
    assert event.details["chosen"] == "semantic"
    assert event.details["raw_valid"] is False


def test_dual_candidate_off_by_default_single_draft() -> None:
    model = _SequenceModelClient([{"sql": "SELECT 1", "explanation": "x"}])
    graph = TextToSqlGraph(
        config=AgentConfig(),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=_ContextWrenClient(),
    )
    response = graph.run(
        AgentQueryRequest(question="names", database_id=1, schema_name="main")
    )
    assert model.calls == 1
    assert all(e.step != "select_sql_candidate" for e in response.trace)
