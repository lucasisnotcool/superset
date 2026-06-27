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
from superset_ai_agent.semantic_layer.copilot.schemas import CoverageReport
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


def test_active_run_returns_newest_inflight(store) -> None:
    assert store.active_run("p1") is None
    run = store.create(
        project_id="p1", owner_id="u1", mdl_checksum="c1", docs_checksum="d1"
    )
    active = store.active_run("p1")
    assert active is not None and active.id == run.id
    store.fail(run.id, "boom")
    assert store.active_run("p1") is None
