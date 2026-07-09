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

"""Authoring HTTP routes: SSE author stream + bulk item import (plan P3.1/P3.2)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.llm.base import ModelInfo, ModelResult, ToolCall
from superset_ai_agent.schemas import ExecutionResult
from tests.unit_tests.superset_ai_agent.test_benchmark_api import (
    create_benchmark,
    FakeSupersetClient,
    make_client,
    resolve_project,
)

HEADER = "type,question,gold_sql,expected_values,eval_note\n"


class ScriptedToolModel:
    """Canned tool-calling model for the authoring loop."""

    def __init__(self, results: list[ModelResult]) -> None:
        self.results = results
        self.calls = 0

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: Any, **kwargs: Any) -> ModelResult:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


def sse_events(body: str) -> list[dict[str, Any]]:
    """Parse SSE frames into their JSON payloads."""

    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


# --- P3.2 bulk import --------------------------------------------------------


def test_bulk_import_creates_dedupes_and_reports_errors(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)

    rows = [
        {
            "question": "Revenue by region?",
            "answer_type": "gold_sql",
            "answer_spec": {"sql": "SELECT region, revenue FROM sales"},
            "capability_tags": ["metric"],
            "verified": True,
        },
        {  # duplicate of the first (case-insensitive)
            "question": "revenue by region?",
            "answer_type": "eval_note",
            "answer_spec": {"note": "dup"},
        },
        {  # invalid: empty gold sql -> row error, batch continues
            "question": "Broken row?",
            "answer_type": "gold_sql",
            "answer_spec": {"sql": ""},
        },
        {
            "question": "Churn healthy?",
            "answer_type": "eval_note",
            "answer_spec": {"note": "Must cite the churn definition."},
            "verified": False,
        },
    ]
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/import",
        json={"items": rows},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["created"] == 2
    assert payload["skipped_duplicates"] == 1
    assert len(payload["errors"]) == 1
    assert "Broken row?" in payload["errors"][0]

    items = client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items"
    ).json()
    by_question = {i["question"]: i for i in items}
    assert by_question["Revenue by region?"]["verified_by"]  # review gate stamp
    assert by_question["Churn healthy?"]["verified_by"] is None


def test_bulk_import_respects_item_cap(tmp_path, monkeypatch) -> None:
    import superset_ai_agent.app as app_module

    monkeypatch.setattr(app_module, "MAX_ITEMS_PER_BENCHMARK", 1)
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    rows = [
        {"question": f"Q{i}?", "answer_type": "eval_note", "answer_spec": {"note": "r"}}
        for i in range(3)
    ]
    payload = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/import",
        json={"items": rows},
    ).json()
    assert payload["created"] == 1
    assert sum("item cap" in e for e in payload["errors"]) == 2


# --- P3.1 author stream ------------------------------------------------------


def _author(client, pid, bid, csv_text, **extra):
    return client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/author/stream",
        json={"csv_text": csv_text, **extra},
    )


def test_authoring_disabled_is_404(tmp_path) -> None:
    client = make_client(tmp_path)  # flag defaults off
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    response = _author(client, pid, bid, HEADER + "question,Q?,,,\n")
    assert response.status_code == 404


def test_invalid_csv_is_422_before_streaming(tmp_path) -> None:
    client = make_client(tmp_path, wren_benchmark_authoring_enabled=True)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    response = _author(client, pid, bid, HEADER + "question,Q?,SELECT 1,42,\n")
    assert response.status_code == 422
    assert "multiple answer cells" in json.dumps(response.json())


def test_author_stream_yields_progress_then_draft(tmp_path) -> None:
    gold = "SELECT buyer FROM orders GROUP BY buyer"
    superset_client = FakeSupersetClient(
        results={
            gold: ExecutionResult(
                columns=["buyer"], rows=[{"buyer": "acme"}], row_count=1
            )
        }
    )
    model = ScriptedToolModel(
        [
            ModelResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="propose_items",
                        arguments={
                            "items": [
                                {
                                    "question": "Who buys most?",
                                    "answer_type": "gold_sql",
                                    "answer_spec": {"sql": gold},
                                    "capability_tags": ["aggregation"],
                                }
                            ]
                        },
                    )
                ],
            ),
            ModelResult(
                content="",
                tool_calls=[
                    ToolCall(id="f1", name="finish", arguments={"summary": "done"})
                ],
            ),
        ]
    )
    client = make_client(
        tmp_path,
        wren_benchmark_authoring_enabled=True,
        superset_client=superset_client,
        model_client=model,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)

    csv_text = HEADER + "question,Who buys most?,,,\ncontext,,,,\n"
    response = _author(client, pid, bid, csv_text, context_text="Buyers are companies.")
    assert response.status_code == 200, response.text
    events = sse_events(response.text)
    kinds = [e["type"] for e in events]
    assert "progress" in kinds
    assert kinds[-1] == "complete"

    draft = events[-1]["draft"]
    assert len(draft["items"]) == 1
    item = draft["items"][0]
    assert item["validation"] == "verified"
    assert item["origin"] == "extracted"
    assert "Buyers are companies." in draft["context_doc"]
    assert gold in superset_client.executed  # the probe really ran

    # R4: nothing was persisted by the stream — import is a separate step.
    items = client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items"
    ).json()
    assert items == []


def test_author_stream_rejects_non_read_only_probe(tmp_path) -> None:
    model = ScriptedToolModel(
        [
            ModelResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="propose_items",
                        arguments={
                            "items": [
                                {
                                    "question": "Drop it?",
                                    "answer_type": "gold_sql",
                                    "answer_spec": {"sql": "DROP TABLE sales"},
                                }
                            ]
                        },
                    )
                ],
            ),
            ModelResult(
                content="",
                tool_calls=[
                    ToolCall(id="f1", name="finish", arguments={"summary": "done"})
                ],
            ),
        ]
    )
    superset_client = FakeSupersetClient()
    client = make_client(
        tmp_path,
        wren_benchmark_authoring_enabled=True,
        superset_client=superset_client,
        model_client=model,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    response = _author(client, pid, bid, HEADER + "question,Drop it?,,,\n")
    events = sse_events(response.text)
    draft = events[-1]["draft"]
    # The destructive candidate never reached the database…
    assert superset_client.executed == []
    # …and whatever survived is flagged for human review, never verified.
    assert all(i["validation"] == "needs_review" for i in draft["items"])
