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

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
from superset_ai_agent.semantic_layer.jobs import (
    InMemoryJobStore,
    JobNotFoundError,
    SqlAlchemyJobStore,
)
from superset_ai_agent.semantic_layer.schemas import OnboardingResult


def _engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    return engine


def test_inmemory_job_store_lifecycle() -> None:
    store = InMemoryJobStore()
    job = store.create(kind="onboarding", project_id="p1")
    assert job.status == "running"

    completed = store.complete(job.id, OnboardingResult(project_id="p1", model_count=2))
    assert completed.status == "completed"
    assert store.get(job.id).result.model_count == 2

    with pytest.raises(JobNotFoundError):
        store.get("missing")


def test_sqlalchemy_job_store_is_visible_across_instances() -> None:
    engine = _engine()
    session_factory = create_session_factory(engine)
    # Two store instances simulate two workers sharing the same database.
    worker_a = SqlAlchemyJobStore(session_factory)
    worker_b = SqlAlchemyJobStore(session_factory)

    job = worker_a.create(kind="onboarding", project_id="p1")
    # Worker B can poll a job created by worker A.
    assert worker_b.get(job.id).status == "running"

    worker_a.complete(job.id, OnboardingResult(project_id="p1", model_count=1))
    fetched = worker_b.get(job.id)
    assert fetched.status == "completed"
    assert fetched.result is not None
    assert fetched.result.model_count == 1


def test_sqlalchemy_job_store_records_failure() -> None:
    store = SqlAlchemyJobStore(create_session_factory(_engine()))
    job = store.create(kind="onboarding", project_id="p1")
    store.fail(job.id, "boom")
    fetched = store.get(job.id)
    assert fetched.status == "failed"
    assert fetched.error == "boom"


def test_sqlalchemy_job_store_tolerates_legacy_unparseable_result() -> None:
    # A job persisted by an older revision can hold a result whose files carry
    # the pre-native-JSON ``content_type='application/x-yaml'``, which no longer
    # validates against the current schema. Listing such a row must degrade the
    # result to None instead of crashing the whole readiness gate.
    from datetime import datetime, timezone

    from superset_ai_agent.persistence.models import AiAgentJob

    session_factory = create_session_factory(_engine())
    store = SqlAlchemyJobStore(session_factory)

    legacy_result = {
        "project_id": "p1",
        "model_count": 1,
        "files": [
            {
                "id": "f1",
                "project_id": "p1",
                "path": "models/x.yaml",
                "filename": "x.yaml",
                "content": "models: []",
                "content_type": "application/x-yaml",
                "checksum": "abc",
            }
        ],
    }
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add(
            AiAgentJob(
                id="legacy-1",
                kind="onboarding",
                status="completed",
                project_id="p1",
                result=legacy_result,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    # Neither listing nor fetching raises; the unparseable result is dropped.
    jobs = store.list_for_project("p1")
    assert len(jobs) == 1
    assert jobs[0].status == "completed"
    assert jobs[0].result is None
    assert store.get("legacy-1").result is None
