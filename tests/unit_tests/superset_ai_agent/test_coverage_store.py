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

"""Coverage-run store: claim CAS, supersession, idempotency (Feature B)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from superset_ai_agent.persistence.models import Base
from superset_ai_agent.semantic_layer.copilot.schemas import (
    CoverageProgress,
    CoverageReport,
)
from superset_ai_agent.semantic_layer.coverage_store import (
    InMemoryCoverageRunStore,
    SqlAlchemyCoverageRunStore,
)


def _sqlalchemy_store() -> SqlAlchemyCoverageRunStore:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlAlchemyCoverageRunStore(sessionmaker(bind=engine))


@pytest.fixture(params=["memory", "sqlalchemy"])
def store(request):
    if request.param == "memory":
        return InMemoryCoverageRunStore()
    return _sqlalchemy_store()


def test_claim_is_a_single_winner_compare_and_set(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    # First claim wins (pending → running); a second claim loses.
    assert store.claim(run.id) is True
    assert store.claim(run.id) is False
    assert store.get(run.id).status == "running"


def test_supersede_marks_inflight_except_target(store) -> None:
    old = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    new = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c2", docs_checksum="d1"
    )

    superseded = store.supersede("p1", except_run_id=new.id)
    assert superseded == 1
    assert store.get(old.id).status == "superseded"
    assert store.get(new.id).status == "pending"
    # The superseded run can no longer be claimed (newest wins).
    assert store.claim(old.id) is False
    assert store.claim(new.id) is True


def test_complete_stores_report_and_latest_complete(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    report = CoverageReport(total=4, covered=3, partial=0, missing=1, score=0.75)
    store.complete(run.id, report, score=0.75)

    latest = store.latest_complete("p1")
    assert latest is not None
    assert latest.status == "complete"
    assert latest.score == 0.75
    assert latest.report is not None
    assert latest.report.total == 4


def test_find_complete_is_idempotency_key(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    store.complete(run.id, CoverageReport(score=1.0), score=1.0)

    assert store.find_complete("p1", "c1", "d1") is not None
    # A different directory version has no cached run.
    assert store.find_complete("p1", "c2", "d1") is None


def test_latest_complete_bulk_returns_one_entry_per_project(store) -> None:
    # Two projects with a complete run, one with only an in-flight (pending) run,
    # and one unknown id. The bulk lookup must return only the complete ones.
    p1 = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(p1.id)
    store.complete(p1.id, CoverageReport(score=0.5), score=0.5)

    p2 = store.create(
        project_id="p2", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(p2.id)
    store.complete(p2.id, CoverageReport(score=0.9), score=0.9)

    # p3 has only a pending run — no complete score yet.
    store.create(project_id="p3", owner_id="u1", mdl_checksum="c1", docs_checksum="d1")

    result = store.latest_complete_bulk(["p1", "p2", "p3", "p4"])
    assert set(result) == {"p1", "p2"}
    assert result["p1"].score == 0.5
    assert result["p2"].score == 0.9
    # Matches the per-project method one-for-one (same answer, one query).
    assert result["p1"].id == store.latest_complete("p1").id


def test_latest_complete_bulk_empty_input(store) -> None:
    assert store.latest_complete_bulk([]) == {}


def test_scores_by_checksum_keeps_latest_run_per_version(store) -> None:
    # Version c1 audited twice (the later run wins), version c2 once. The overlay
    # is keyed by MDL version, not by run, so c1 returns only its newest score.
    first = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(first.id)
    store.complete(first.id, CoverageReport(score=0.5), score=0.5)

    second = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d2"
    )
    store.claim(second.id)
    store.complete(second.id, CoverageReport(score=0.6), score=0.6)

    other = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c2", docs_checksum="d1"
    )
    store.claim(other.id)
    store.complete(other.id, CoverageReport(score=0.9), score=0.9)

    # A different project's run must not leak into p1's overlay.
    elsewhere = store.create(
        project_id="p2", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(elsewhere.id)
    store.complete(elsewhere.id, CoverageReport(score=0.1), score=0.1)

    scores = store.scores_by_checksum("p1")
    assert set(scores) == {"c1", "c2"}
    assert scores["c1"].id == second.id  # newest run for c1
    assert scores["c1"].score == 0.6
    assert scores["c2"].score == 0.9


def test_active_run_returns_newest_inflight(store) -> None:
    assert store.active_run("p1") is None
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    active = store.active_run("p1")
    assert active is not None
    assert active.id == run.id
    store.fail(run.id, "boom")
    assert store.active_run("p1") is None


def test_report_progress_records_live_stage(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    store.report_progress(
        run.id,
        CoverageProgress(stage="extracting", detail="orders.pdf", current=1, total=5),
    )
    persisted = store.get(run.id)
    assert persisted.progress is not None
    assert persisted.progress.stage == "extracting"
    assert persisted.progress.current == 1
    assert persisted.progress.total == 5
    # Live progress surfaces through the active-run lookup the badge reads.
    active = store.active_run("p1")
    assert active is not None
    assert active.progress is not None
    assert active.progress.detail == "orders.pdf"


def test_terminal_transition_clears_progress(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    store.report_progress(run.id, CoverageProgress(stage="judging"))
    store.complete(run.id, CoverageReport(score=0.8), score=0.8)
    # A completed run carries no live progress (it no longer applies).
    assert store.get(run.id).progress is None


def test_set_recovery_status_and_conversation(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    store.complete(run.id, CoverageReport(score=0.5), score=0.5)
    # Defaults: no recovery yet.
    fresh = store.get(run.id)
    assert fresh.recovery_status == "none"
    assert fresh.recovery_conversation_id is None
    assert fresh.recovery_dismissed_at is None

    store.set_recovery(run.id, status="running")
    assert store.get(run.id).recovery_status == "running"
    # Completing the run did not clobber recovery; setting ready links the thread.
    store.set_recovery(run.id, status="ready", conversation_id="conv-1")
    persisted = store.get(run.id)
    assert persisted.recovery_status == "ready"
    assert persisted.recovery_conversation_id == "conv-1"
    # The completed report is preserved across recovery updates.
    assert persisted.report is not None
    assert persisted.score == 0.5


def test_dismiss_recovery_is_durable_and_status_preserving(store) -> None:
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    store.complete(run.id, CoverageReport(score=0.5), score=0.5)
    store.set_recovery(run.id, status="ready", conversation_id="conv-1")

    store.dismiss_recovery(run.id)
    persisted = store.get(run.id)
    assert persisted.recovery_dismissed_at is not None
    # Dismissal hides the notification but keeps the suggestions reachable.
    assert persisted.recovery_status == "ready"
    assert persisted.recovery_conversation_id == "conv-1"


def _complete(store, project_id, *, missing=0, recovery=None, dismissed=False):
    run = store.create(
        project_id=project_id, owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(run.id)
    store.complete(
        run.id,
        CoverageReport(total=missing, missing=missing, score=0.0),
        score=0.0,
    )
    if recovery is not None:
        store.set_recovery(run.id, status=recovery)
    if dismissed:
        store.dismiss_recovery(run.id)
    return run


def test_iter_recoverable_selects_only_runs_that_need_recovery(store) -> None:
    # Eligible: completed, has a gap, recovery not started/failed, not dismissed.
    needs = _complete(store, "needs", missing=1)
    failed = _complete(store, "failed", missing=2, recovery="failed")
    # Ineligible cases:
    _complete(store, "no_gap", missing=0)  # fully covered → nothing to recover
    _complete(store, "ready", missing=1, recovery="ready")  # already has suggestions
    _complete(store, "empty", missing=1, recovery="empty")  # judged: no work
    _complete(store, "dismissed", missing=1, recovery="ready", dismissed=True)
    # In-flight (no complete run) → not recoverable.
    pending = store.create(
        project_id="pending", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(pending.id)

    recoverable = {run.project_id for run in store.iter_recoverable()}
    assert recoverable == {"needs", "failed"}
    # owner_id rides along so the sweep can reload the project without an identity.
    assert all(run.owner_id == "u1" for run in store.iter_recoverable())
    del needs, failed


def test_iter_recoverable_returns_only_latest_complete_run_per_project(store) -> None:
    # An older complete run with a gap, then a newer complete run that is already
    # recovered: the project must NOT be returned (newest run wins, and it is done).
    old = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    store.claim(old.id)
    store.complete(old.id, CoverageReport(total=1, missing=1, score=0.0), score=0.0)
    new = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c2", docs_checksum="d1"
    )
    store.claim(new.id)
    store.complete(new.id, CoverageReport(total=1, missing=1, score=0.0), score=0.0)
    store.set_recovery(new.id, status="ready")

    assert store.iter_recoverable() == []
