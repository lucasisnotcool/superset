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

"""Eval store: CRUD + run lifecycle, parameterized memory/sqlalchemy."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from superset_ai_agent.evals.schemas import (
    BenchmarkItem,
    EvalResult,
    EvalRun,
    EvalScore,
    RunProgress,
    RunTotals,
)
from superset_ai_agent.evals.store import (
    BenchmarkItemNotFoundError,
    BenchmarkNotFoundError,
    compute_benchmark_checksum,
    InMemoryEvalStore,
    SqlAlchemyEvalStore,
)
from superset_ai_agent.persistence.models import Base


def _sqlalchemy_store() -> SqlAlchemyEvalStore:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlAlchemyEvalStore(sessionmaker(bind=engine))


@pytest.fixture(params=["memory", "sqlalchemy"])
def store(request):
    if request.param == "memory":
        return InMemoryEvalStore()
    return _sqlalchemy_store()


def _benchmark(store, project_id="p1"):
    return store.create_benchmark(
        project_id=project_id, owner_id="u1", name="Core questions", description=None
    )


def _item(store, benchmark_id, question="How many drives shipped?", position=0):
    return store.add_item(
        BenchmarkItem(
            benchmark_id=benchmark_id,
            position=position,
            question=question,
            answer_type="gold_sql",
            answer_spec={"sql": "SELECT count(*) FROM shipments"},
        )
    )


def _run(store, benchmark):
    return store.create_run(
        EvalRun(
            benchmark_id=benchmark.id,
            project_id=benchmark.project_id,
            owner_id="u1",
            benchmark_checksum="abc",
        )
    )


def test_benchmark_crud_and_item_count(store) -> None:
    benchmark = _benchmark(store)
    _item(store, benchmark.id)
    _item(store, benchmark.id, question="Second?", position=1)

    fetched = store.get_benchmark(benchmark.id)
    assert fetched.name == "Core questions"
    assert fetched.item_count == 2

    updated = store.update_benchmark(benchmark.id, name="Renamed")
    assert updated.name == "Renamed"
    assert [b.id for b in store.list_benchmarks("p1")] == [benchmark.id]


def test_benchmark_soft_delete_hides_from_reads(store) -> None:
    benchmark = _benchmark(store)
    store.delete_benchmark(benchmark.id)
    assert store.list_benchmarks("p1") == []
    with pytest.raises(BenchmarkNotFoundError):
        store.get_benchmark(benchmark.id)


def test_item_crud_ordering_and_soft_delete(store) -> None:
    benchmark = _benchmark(store)
    second = _item(store, benchmark.id, question="B?", position=1)
    first = _item(store, benchmark.id, question="A?", position=0)

    items = store.list_items(benchmark.id)
    assert [i.id for i in items] == [first.id, second.id]

    first.question = "A updated?"
    first.use_as_example = True
    stored = store.update_item(first)
    assert stored.question == "A updated?"
    assert stored.use_as_example is True

    store.delete_item(second.id)
    assert [i.id for i in store.list_items(benchmark.id)] == [first.id]
    with pytest.raises(BenchmarkItemNotFoundError):
        store.get_item(second.id)


def test_run_claim_is_single_winner(store) -> None:
    benchmark = _benchmark(store)
    run = _run(store, benchmark)
    assert store.claim_run(run.id) is True
    assert store.claim_run(run.id) is False
    assert store.get_run(run.id).status == "running"


def test_supersede_marks_inflight_except_target(store) -> None:
    benchmark = _benchmark(store)
    old = _run(store, benchmark)
    new = _run(store, benchmark)
    count = store.supersede_runs(benchmark.id, except_run_id=new.id)
    assert count == 1
    assert store.get_run(old.id).status == "superseded"
    assert store.get_run(new.id).status == "pending"


def test_run_progress_then_complete_clears_progress(store) -> None:
    benchmark = _benchmark(store)
    run = _run(store, benchmark)
    store.claim_run(run.id)
    store.report_run_progress(run.id, RunProgress(completed=1, total=4))
    assert store.get_run(run.id).progress.completed == 1

    totals = RunTotals(items=4, passed=3, failed=1)
    done = store.complete_run(run.id, totals=totals, score=0.75)
    assert done.status == "complete"
    assert done.score == 0.75
    assert done.progress is None
    assert done.totals.passed == 3


def test_run_failure_records_error(store) -> None:
    benchmark = _benchmark(store)
    run = _run(store, benchmark)
    store.claim_run(run.id)
    failed = store.fail_run(run.id, "boom")
    assert failed.status == "failed"
    assert failed.error == "boom"


def test_runs_list_newest_first(store) -> None:
    benchmark = _benchmark(store)
    first = _run(store, benchmark)
    second = _run(store, benchmark)
    runs = store.list_runs(benchmark.id)
    assert {runs[0].id, runs[1].id} == {first.id, second.id}


def test_result_round_trip_with_scores(store) -> None:
    benchmark = _benchmark(store)
    run = _run(store, benchmark)
    result = store.add_result(
        EvalResult(
            run_id=run.id,
            item_id="item-1",
            question="Q?",
            answer_type="gold_sql",
            answer_spec={"sql": "SELECT 1"},
            agent_sql="SELECT 1",
            verdict="pass",
            scores=[
                EvalScore(name="ex", value=1.0, label="pass"),
                EvalScore(name="soft_f1", value=1.0),
            ],
        )
    )

    fetched = store.get_result(result.id)
    assert fetched.verdict == "pass"
    assert {s.name for s in fetched.scores} == {"ex", "soft_f1"}
    assert all(s.result_id == result.id for s in fetched.scores)
    assert [r.id for r in store.list_results(run.id)] == [result.id]


def test_override_result_records_human_score_and_wins(store) -> None:
    benchmark = _benchmark(store)
    run = _run(store, benchmark)
    result = store.add_result(
        EvalResult(
            run_id=run.id,
            item_id="item-1",
            question="Q?",
            answer_type="eval_note",
            answer_spec={"note": "check tone"},
            verdict="needs_review",
        )
    )

    overridden = store.override_result(
        result.id, verdict="pass", by="reviewer", comment="Looks right."
    )
    assert overridden.override_verdict == "pass"
    assert overridden.effective_verdict == "pass"
    assert overridden.override_by == "reviewer"
    human = [s for s in overridden.scores if s.source == "human"]
    assert len(human) == 1
    assert human[0].label == "pass"


def test_benchmark_checksum_is_content_stable(store) -> None:
    benchmark = _benchmark(store)
    a = _item(store, benchmark.id, question="A?", position=0)
    _item(store, benchmark.id, question="B?", position=1)

    items = store.list_items(benchmark.id)
    checksum = compute_benchmark_checksum(items)
    assert checksum == compute_benchmark_checksum(list(reversed(items)))

    a.question = "A changed?"
    store.update_item(a)
    assert compute_benchmark_checksum(store.list_items(benchmark.id)) != checksum
