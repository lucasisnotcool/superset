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

"""Benchmark run job: agent + gold + scoring + totals + compare (P1.3/P1.4)."""

from __future__ import annotations

from typing import Any

from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    ExecutionResult,
    SqlValidation,
    WrenContextArtifact,
)
from tests.unit_tests.superset_ai_agent.test_benchmark_api import (
    add_gold_item,
    create_benchmark,
    FakeSupersetClient,
    GOLD_SQL,
    make_client,
    resolve_project,
)

GOLD_RESULT = ExecutionResult(
    columns=["region", "revenue"],
    rows=[{"region": "emea", "revenue": 10}, {"region": "apac", "revenue": 20}],
    row_count=2,
)


class FakeGraph:
    """Answers questions from a canned map of question -> rows (or error)."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []
        self.requests: list[AgentQueryRequest] = []

    def run(
        self, request: AgentQueryRequest, *, owner_id: str = ""
    ) -> AgentQueryResponse:
        self.calls.append(request.question)
        self.requests.append(request)
        answer = self.answers.get(request.question)
        validation = SqlValidation(is_valid=True, is_read_only=True)
        if answer is None:
            return AgentQueryResponse(
                status="error",
                validation=SqlValidation(is_valid=False, is_read_only=False),
            )
        return AgentQueryResponse(
            status="ok",
            sql=f"SELECT /* answer */ '{request.question}'",
            validation=validation,
            execution_result=answer,
            wren_context=WrenContextArtifact(
                enabled=True,
                available=True,
                matched_models=["sales"],
                recalled_example_count=1,
            ),
        )


def _gold_superset() -> FakeSupersetClient:
    return FakeSupersetClient(results={GOLD_SQL: GOLD_RESULT})


def _start_run(client, pid, bid, *, trials: int = 1, item_ids=None):
    body: dict[str, Any] = {"trials": trials}
    if item_ids is not None:
        body["item_ids"] = item_ids
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs",
        json=body,
    )
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _get_run(client, pid, bid, run_id):
    return client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs/{run_id}"
    ).json()


def _results(client, pid, bid, run_id):
    return client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs/{run_id}/results"
    ).json()


def test_run_completes_with_mixed_verdicts(tmp_path) -> None:
    graph = FakeGraph(
        answers={
            "Right answer?": GOLD_RESULT,
            "Wrong answer?": ExecutionResult(
                columns=["region", "revenue"],
                rows=[{"region": "emea", "revenue": 999}],
                row_count=1,
            ),
            # "Broken?" is absent -> agent error
        }
    )
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Right answer?")
    add_gold_item(client, pid, bid, question="Wrong answer?")
    add_gold_item(client, pid, bid, question="Broken?")

    run_id = _start_run(client, pid, bid)
    run = _get_run(client, pid, bid, run_id)
    assert run["status"] == "complete"
    assert run["totals"] == {
        "items": 3,
        "trials": 1,
        "passed": 1,
        "failed": 1,
        "needs_review": 0,
        "errors": 1,
        "pass_hat_k": None,
        # All three gold items carry the "metric" tag (P2.3 breakdown).
        "by_capability": {"metric": {"items": 3, "passed": 1}},
    }
    assert abs(run["score"] - 1 / 3) < 1e-9
    assert run["benchmark_checksum"]

    results = _results(client, pid, bid, run_id)
    by_question = {r["question"]: r for r in results}
    assert by_question["Right answer?"]["verdict"] == "pass"
    assert by_question["Wrong answer?"]["verdict"] == "fail"
    assert by_question["Broken?"]["verdict"] == "error"
    # Frozen spec + previews + scores persisted.
    right = by_question["Right answer?"]
    assert right["answer_spec"] == {"sql": GOLD_SQL}
    assert right["gold_rows_preview"] == GOLD_RESULT.rows
    assert {s["name"] for s in right["scores"]} >= {"ex", "soft_f1"}
    assert right["matched_models"] == ["sales"]


def test_gold_sql_failure_marks_item_error_not_run_failure(tmp_path) -> None:
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    client = make_client(
        tmp_path,
        superset_client=FakeSupersetClient(),  # no canned gold -> gold exec raises
        text_to_sql_graph=graph,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")

    run_id = _start_run(client, pid, bid)
    run = _get_run(client, pid, bid, run_id)
    assert run["status"] == "complete"
    assert run["totals"]["errors"] == 1
    results = _results(client, pid, bid, run_id)
    assert results[0]["verdict"] == "error"
    assert "Run step failed" in results[0]["reasons"][0]


def test_expected_values_item_scores_without_gold_execution(tmp_path) -> None:
    graph = FakeGraph(
        answers={
            "How many drives?": ExecutionResult(
                columns=["n"], rows=[{"n": 42}], row_count=1
            )
        }
    )
    superset = FakeSupersetClient()  # would raise if gold execution were attempted
    client = make_client(tmp_path, superset_client=superset, text_to_sql_graph=graph)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "How many drives?",
            "answer_type": "expected_values",
            "answer_spec": {"nums": [42]},
        },
    )

    run_id = _start_run(client, pid, bid)
    run = _get_run(client, pid, bid, run_id)
    assert run["status"] == "complete"
    assert run["totals"]["passed"] == 1
    assert superset.executed == []


def test_eval_note_items_route_to_needs_review_when_judge_off(tmp_path) -> None:
    graph = FakeGraph(
        answers={"Vibes?": ExecutionResult(columns=["x"], rows=[{"x": 1}], row_count=1)}
    )
    client = make_client(
        tmp_path,
        superset_client=_gold_superset(),
        text_to_sql_graph=graph,
        wren_benchmark_judge_enabled=False,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "Vibes?",
            "answer_type": "eval_note",
            "answer_spec": {"note": "should mention EMEA"},
        },
    )

    run_id = _start_run(client, pid, bid)
    run = _get_run(client, pid, bid, run_id)
    assert run["totals"]["needs_review"] == 1


def test_eval_note_items_are_judged_when_enabled(tmp_path) -> None:
    from superset_ai_agent.llm.base import ModelResult
    from superset_ai_agent.schemas import ModelInfo

    class JudgingModel:
        def is_reachable(self) -> bool:
            return True

        def list_models(self) -> list[ModelInfo]:
            return []

        def chat(self, messages: Any, **kwargs: Any) -> ModelResult:
            return ModelResult(
                content='{"verdict": "pass", "critique": "Mentions EMEA."}'
            )

    graph = FakeGraph(
        answers={
            "Vibes?": ExecutionResult(
                columns=["region"], rows=[{"region": "EMEA"}], row_count=1
            )
        }
    )
    client = make_client(
        tmp_path,
        superset_client=_gold_superset(),
        text_to_sql_graph=graph,
        model_client=JudgingModel(),
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "Vibes?",
            "answer_type": "eval_note",
            "answer_spec": {"note": "should mention EMEA"},
        },
    )

    run_id = _start_run(client, pid, bid)
    run = _get_run(client, pid, bid, run_id)
    assert run["totals"]["passed"] == 1
    result = _results(client, pid, bid, run_id)[0]
    assert result["verdict"] == "pass"
    assert result["verdict_source"] == "llm_judge"
    assert "Mentions EMEA" in result["reasons"][0]


def test_two_trials_report_pass_hat_k(tmp_path) -> None:
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")

    run_id = _start_run(client, pid, bid, trials=2)
    run = _get_run(client, pid, bid, run_id)
    assert run["trials"] == 2
    assert run["totals"]["pass_hat_k"] == 1.0
    assert len(_results(client, pid, bid, run_id)) == 2
    assert graph.calls == ["Q?", "Q?"]


def test_item_subset_run(tmp_path) -> None:
    graph = FakeGraph(answers={"A?": GOLD_RESULT, "B?": GOLD_RESULT})
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    a_id = add_gold_item(client, pid, bid, question="A?")
    add_gold_item(client, pid, bid, question="B?")

    run_id = _start_run(client, pid, bid, item_ids=[a_id])
    run = _get_run(client, pid, bid, run_id)
    assert run["totals"]["items"] == 1
    assert graph.calls == ["A?"]


def test_empty_run_request_is_rejected(tmp_path) -> None:
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=FakeGraph({})
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs",
        json={},
    )
    assert response.status_code == 400


def test_exclusion_on_passes_item_question_and_suppresses_leakage_flag(
    tmp_path,
) -> None:
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    item_id = add_gold_item(client, pid, bid, question="Q?")
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/items/{item_id}/promote-example"
    )

    run_id = _start_run(client, pid, bid)  # exclude_example_recall defaults True
    results = _results(client, pid, bid, run_id)
    names = {s["name"] for s in results[0]["scores"]}
    assert "leakage_suspected" not in names
    # The graph received the leakage-guard field (P2.4 pass-through).
    assert graph.requests[0].exclude_example_questions == ["Q?"]


def test_exclusion_off_flags_leakage_on_golden_items(tmp_path) -> None:
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    item_id = add_gold_item(client, pid, bid, question="Q?")
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/items/{item_id}/promote-example"
    )

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs",
        json={"exclude_example_recall": False},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    results = _results(client, pid, bid, run_id)
    names = {s["name"] for s in results[0]["scores"]}
    assert "leakage_suspected" in names
    assert graph.requests[0].exclude_example_questions is None


def test_model_override_is_threaded_to_the_agent(tmp_path) -> None:
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/runs",
        json={"model": "gpt-5.2-mini"},
    )
    assert response.status_code == 202
    run = _get_run(client, pid, bid, response.json()["run_id"])
    assert run["config"]["model"] == "gpt-5.2-mini"
    assert graph.requests[0].model == "gpt-5.2-mini"


def test_compare_runs_reports_paired_ci_and_direction(tmp_path) -> None:
    questions = [f"Q{i}?" for i in range(12)]
    wrong = ExecutionResult(
        columns=["region", "revenue"],
        rows=[{"region": "emea", "revenue": 999}],
        row_count=1,
    )
    client_graph_bad = FakeGraph(answers={q: wrong for q in questions})
    client = make_client(
        tmp_path,
        superset_client=_gold_superset(),
        text_to_sql_graph=client_graph_bad,
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    for q in questions:
        add_gold_item(client, pid, bid, question=q)

    baseline = _start_run(client, pid, bid)
    # Flip the graph to all-correct for the second run.
    client_graph_bad.answers = {q: GOLD_RESULT for q in questions}
    improved = _start_run(client, pid, bid)

    response = client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{improved}/compare/{baseline}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delta"] == 1.0
    assert body["significant"] is True
    assert body["n_items"] == 12
    assert len(body["improved"]) == 12
    assert body["benchmark_changed"] is False


def test_override_flips_verdict_and_recomputes_totals(tmp_path) -> None:
    graph = FakeGraph(
        answers={
            "Q?": ExecutionResult(
                columns=["region", "revenue"],
                rows=[{"region": "emea", "revenue": 999}],
                row_count=1,
            )
        }
    )
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")

    run_id = _start_run(client, pid, bid)
    assert _get_run(client, pid, bid, run_id)["totals"]["failed"] == 1
    result_id = _results(client, pid, bid, run_id)[0]["id"]

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{run_id}/results/{result_id}/override",
        json={"verdict": "pass", "comment": "Tie order; actually right."},
    )
    assert response.status_code == 200, response.text
    assert response.json()["override_verdict"] == "pass"

    run = _get_run(client, pid, bid, run_id)
    assert run["totals"]["passed"] == 1
    assert run["totals"]["failed"] == 0
    assert run["score"] == 1.0


def test_new_run_supersedes_pending_run(tmp_path) -> None:
    """With a deferred runner, submitting run B supersedes still-pending run A."""

    class DeferredRunner:
        def __init__(self) -> None:
            self.jobs: list[Any] = []

        def submit(self, fn) -> None:
            self.jobs.append(fn)

    from superset_ai_agent.evals.store import InMemoryEvalStore

    store = InMemoryEvalStore()
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    runner = DeferredRunner()
    # Build the app manually to control the job runner.
    from fastapi.testclient import TestClient

    from superset_ai_agent.app import create_app
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.conversations.memory import InMemoryConversationStore
    from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
    from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
    from tests.unit_tests.superset_ai_agent.test_benchmark_api import (
        _ContextProvider,
        _Model,
    )

    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
            wren_copilot_enabled=True,
            agent_storage_dir=str(tmp_path),
        ),
        model_client=_Model(),
        text_to_sql_graph=graph,
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=_ContextProvider(),
        superset_client=_gold_superset(),
        job_runner=runner,
        eval_store=store,
    )
    client = TestClient(app)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")

    first = _start_run(client, pid, bid)
    second = _start_run(client, pid, bid)
    assert _get_run(client, pid, bid, first)["status"] == "superseded"
    assert _get_run(client, pid, bid, second)["status"] == "pending"

    # Draining the queue: the superseded job exits without writing results;
    # the live one completes.
    for job in runner.jobs:
        job()
    assert _get_run(client, pid, bid, first)["status"] == "superseded"
    assert _get_run(client, pid, bid, second)["status"] == "complete"
    assert _results(client, pid, bid, first) == []


def test_matrix_runs_fan_out_labeled_arms_without_mutual_supersession(
    tmp_path,
) -> None:
    graph = FakeGraph(answers={"Q?": GOLD_RESULT})
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/matrix-runs",
        json={
            "configs": [
                {"model": "small-model"},
                {"model": "big-model"},
                {"label": "no-guard", "exclude_example_recall": False},
            ]
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert [arm["label"] for arm in body["runs"]] == [
        "small-model",
        "big-model",
        "no-guard",
    ]

    # All three arms completed (InlineJobRunner) — none superseded a sibling.
    for arm in body["runs"]:
        run = _get_run(client, pid, bid, arm["run_id"])
        assert run["status"] == "complete"
        assert run["config"]["label"] == arm["label"]
    # Each arm's model reached the agent.
    assert [r.model for r in graph.requests] == ["small-model", "big-model", None]
    # The no-guard arm ran without exclusion.
    assert graph.requests[2].exclude_example_questions is None
    assert graph.requests[0].exclude_example_questions == ["Q?"]


def test_matrix_rejects_duplicate_labels(tmp_path) -> None:
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=FakeGraph({})
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/matrix-runs",
        json={"configs": [{"model": "m"}, {"model": "m"}]},
    )
    assert response.status_code == 400


def test_override_recomputes_capability_breakdown(tmp_path) -> None:
    graph = FakeGraph(
        answers={
            "Q?": ExecutionResult(
                columns=["region", "revenue"],
                rows=[{"region": "emea", "revenue": 999}],
                row_count=1,
            )
        }
    )
    client = make_client(
        tmp_path, superset_client=_gold_superset(), text_to_sql_graph=graph
    )
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid, question="Q?")  # tag: metric

    run_id = _start_run(client, pid, bid)
    run = _get_run(client, pid, bid, run_id)
    assert run["totals"]["by_capability"] == {"metric": {"items": 1, "passed": 0}}

    result_id = _results(client, pid, bid, run_id)[0]["id"]
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/runs/{run_id}/results/{result_id}/override",
        json={"verdict": "pass"},
    )
    run = _get_run(client, pid, bid, run_id)
    assert run["totals"]["by_capability"] == {"metric": {"items": 1, "passed": 1}}
