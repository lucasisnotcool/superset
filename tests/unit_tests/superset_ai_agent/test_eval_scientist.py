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

"""Scientist v1 analysis + OTel export over benchmark runs (P3.1/P3.3)."""

from __future__ import annotations

from typing import Any

from superset_ai_agent.evals.schemas import EvalRun, RunTotals
from superset_ai_agent.evals.scientist import analyze_run
from superset_ai_agent.llm.base import ModelResult
from superset_ai_agent.schemas import ExecutionResult, ModelInfo
from tests.unit_tests.superset_ai_agent.test_benchmark_api import (
    add_gold_item,
    create_benchmark,
    make_client,
    resolve_project,
)
from tests.unit_tests.superset_ai_agent.test_benchmark_run_job import (
    _gold_superset,
    _results,
    _start_run,
    FakeGraph,
    GOLD_RESULT,
)

ANALYSIS_REPLY = """{
  "summary": "One failure: the agent joined the wrong table for revenue.",
  "findings": [
    {"item_id": "ITEM", "question": "Wrong answer?",
     "diagnosis": "join_path",
     "suggested_action": "Add a relationship from sales to regions.",
     "test_suspect": false}
  ]
}"""


class AnalystModel:
    def __init__(self, reply: str = ANALYSIS_REPLY) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return []

    def chat(self, messages: Any, **kwargs: Any) -> ModelResult:
        self.prompts.append(messages[0].content)
        return ModelResult(content=self.reply)


def _client_with_failed_run(tmp_path, model: AnalystModel):
    graph = FakeGraph(
        answers={
            "Right answer?": GOLD_RESULT,
            "Wrong answer?": ExecutionResult(
                columns=["region", "revenue"],
                rows=[{"region": "emea", "revenue": 999}],
                row_count=1,
            ),
        }
    )
    client = make_client(
        tmp_path,
        superset_client=_gold_superset(),
        text_to_sql_graph=graph,
        model_client=model,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Right answer?")
    add_gold_item(client, pid, bid, question="Wrong answer?")
    run_id = _start_run(client, pid, bid)
    return client, pid, bid, run_id


def test_analyze_returns_taxonomy_findings_and_persists_conversation(
    tmp_path,
) -> None:
    model = AnalystModel()
    client, pid, bid, run_id = _client_with_failed_run(tmp_path, model)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs/{run_id}/analyze"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    report = body["report"]
    assert "wrong table" in report["summary"]
    assert report["findings"][0]["diagnosis"] == "join_path"
    assert "relationship" in report["findings"][0]["suggested_fix_type"].lower()
    assert report["stats_note"].startswith("No previous completed run")
    assert body["conversation_id"]

    # The failure evidence (SQL, reasons, previews) reached the analyst.
    assert "Wrong answer?" in model.prompts[0]
    assert "999" in model.prompts[0]

    # The report is persisted as a `scientist` conversation turn.
    conversation = client.get(f"/agent/conversations/{body['conversation_id']}").json()
    assert conversation["kind"] == "scientist"
    assert "join_path" in conversation["messages"][-1]["content"]


def test_analyze_includes_within_noise_gate_against_previous_run(tmp_path) -> None:
    model = AnalystModel()
    client, pid, bid, first_run = _client_with_failed_run(tmp_path, model)
    # Identical second run -> zero delta -> within-noise stats note.
    second_run = _start_run(client, pid, bid)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{second_run}/analyze"
    )
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["within_noise"] is True
    assert "WITHIN NOISE" in model.prompts[-1]
    assert first_run != second_run


def test_analyze_degrades_gracefully_on_unparseable_reply(tmp_path) -> None:
    model = AnalystModel(reply="I could not decide, sorry.")
    client, pid, bid, run_id = _client_with_failed_run(tmp_path, model)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs/{run_id}/analyze"
    )
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["parse_degraded"] is True
    assert report["summary"] == "I could not decide, sorry."


def test_analyze_rejects_incomplete_runs(tmp_path) -> None:
    model = AnalystModel()
    client, pid, bid, run_id = _client_with_failed_run(tmp_path, model)
    # Unknown run id -> 404; a pending run would 409 (covered by status gate).
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs/nope/analyze"
    )
    assert response.status_code == 404


def test_all_pass_report_needs_no_model_call() -> None:
    run = EvalRun(
        benchmark_id="b",
        project_id="p",
        owner_id="u",
        status="complete",
        totals=RunTotals(items=2, passed=2),
    )
    report = analyze_run(object(), run=run, results=[], comparison=None)
    assert "Every question passed" in report.summary


def test_otel_export_emits_gen_ai_evaluation_events(tmp_path) -> None:
    model = AnalystModel()
    client, pid, bid, run_id = _client_with_failed_run(tmp_path, model)

    response = client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{run_id}/export-otel"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(body["events"]) > 0
    event = body["events"][0]
    assert event["name"] == "gen_ai.evaluation.result"
    attrs = event["attributes"]
    assert attrs["gen_ai.evaluation.name"] == "ex"
    assert attrs["superset_ai_agent.eval.run_id"] == run_id
    # Score names across the run include soft_f1 for gold items.
    names = {e["attributes"]["gen_ai.evaluation.name"] for e in body["events"]}
    assert {"ex", "soft_f1"} <= names
    # Results endpoint still serves the raw rows the events were built from.
    assert len(_results(client, pid, bid, run_id)) == 2


def test_handoff_produces_reviewable_changeset_and_conversation(tmp_path) -> None:
    import json as _json  # noqa: TID251 - test fixture content

    from superset_ai_agent.llm.base import ToolCall

    moves_model = _json.dumps(
        {
            "models": [
                {
                    "name": "regions",
                    "tableReference": {"table": "regions"},
                    "columns": [{"name": "id", "type": "BIGINT"}],
                }
            ]
        }
    )

    class AnalystThenCopilotModel:
        """Serves the analysis JSON, then drives one copilot edit turn."""

        def __init__(self) -> None:
            self.copilot_calls = 0

        def is_reachable(self) -> bool:
            return True

        def list_models(self) -> list[ModelInfo]:
            return []

        def chat(self, messages: Any, **kwargs: Any) -> ModelResult:
            if kwargs.get("tools"):
                self.copilot_calls += 1
                if self.copilot_calls == 1:
                    return ModelResult(
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="c1",
                                name="write_mdl_file",
                                arguments={
                                    "path": "models/regions.json",
                                    "content": moves_model,
                                },
                            )
                        ],
                    )
                return ModelResult(content="Added the regions model.")
            return ModelResult(content=ANALYSIS_REPLY)

    model = AnalystThenCopilotModel()
    client, pid, bid, run_id = _client_with_failed_run(tmp_path, model)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{run_id}/handoff-copilot"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    changeset = body["changeset"]
    assert changeset["message"] == "Added the regions model."
    assert len(changeset["items"]) == 1
    assert changeset["items"][0]["path"] == "models/regions.json"
    assert body["conversation_id"]
    assert "re-run this benchmark" in body["verification_hint"]

    # Staged, not applied: the proposed file is NOT in the project workspace.
    files = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    assert "models/regions.json" not in {f["path"] for f in files}

    # Persisted as a scientist conversation carrying the changeset artifact.
    conversation = client.get(f"/agent/conversations/{body['conversation_id']}").json()
    assert conversation["kind"] == "scientist"


def test_handoff_refuses_runs_without_failures(tmp_path) -> None:
    graph = FakeGraph(answers={"Only right?": GOLD_RESULT})
    client = make_client(
        tmp_path,
        superset_client=_gold_superset(),
        text_to_sql_graph=graph,
        model_client=AnalystModel(),
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Only right?")
    run_id = _start_run(client, pid, bid)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{run_id}/handoff-copilot"
    )
    assert response.status_code == 400
    assert "nothing to fix" in response.json()["detail"]


def test_auto_analyze_flag_persists_report_after_failed_run(tmp_path) -> None:
    model = AnalystModel()
    graph = FakeGraph(answers={})  # every question errors -> failures exist
    client = make_client(
        tmp_path,
        superset_client=_gold_superset(),
        text_to_sql_graph=graph,
        model_client=model,
        wren_benchmark_auto_analyze_enabled=True,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Broken?")
    _start_run(client, pid, bid)  # InlineJobRunner: run + chained analysis

    # The analysis ran without any user trigger (exactly one analyst call)…
    assert len(model.prompts) == 1
    assert "Broken?" in model.prompts[0]
