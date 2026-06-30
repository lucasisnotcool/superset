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

"""LLM-usage store: record, aggregate, window, purge (in-memory + SQLAlchemy)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from superset_ai_agent.llm.usage_store import (
    InMemoryLlmUsageStore,
    SqlAlchemyLlmUsageStore,
)
from superset_ai_agent.persistence.models import Base


def _sqlalchemy_store() -> SqlAlchemyLlmUsageStore:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlAlchemyLlmUsageStore(sessionmaker(bind=engine))


@pytest.fixture(params=["memory", "sqlalchemy"])
def store(request):
    if request.param == "memory":
        return InMemoryLlmUsageStore()
    return _sqlalchemy_store()


def _seed(store) -> None:
    store.record(
        provider="openai",
        model="gpt-5.2",
        duration_ms=1000,
        ok=True,
        prompt_tokens=100,
        completion_tokens=20,
    )
    store.record(
        provider="openai",
        model="gpt-5.2",
        duration_ms=3000,
        ok=False,
        prompt_tokens=50,
        completion_tokens=None,
    )
    store.record(
        provider="ollama",
        model="qwen2.5-coder:7b",
        duration_ms=2000,
        ok=True,
    )


def test_totals_aggregate_calls_duration_failures_and_tokens(store) -> None:
    _seed(store)
    summary = store.summary()

    assert summary.total_calls == 3
    assert summary.total_failures == 1
    assert summary.total_duration_ms == 6000
    assert summary.avg_duration_ms == 2000.0
    # None token counts are treated as 0; only reported usage contributes.
    assert summary.total_prompt_tokens == 150
    assert summary.total_completion_tokens == 20
    assert summary.kinds == ["chat"]


def test_breakdowns_by_model_and_provider(store) -> None:
    _seed(store)
    summary = store.summary()

    by_model = {b.key: b for b in summary.by_model}
    assert by_model["gpt-5.2"].calls == 2
    assert by_model["gpt-5.2"].failures == 1
    assert by_model["qwen2.5-coder:7b"].calls == 1

    by_provider = {b.key: b for b in summary.by_provider}
    assert by_provider["openai"].calls == 2
    assert by_provider["ollama"].calls == 1
    # Most-used provider sorts first.
    assert summary.by_provider[0].key == "openai"


def test_by_day_buckets_on_utc_date(store) -> None:
    _seed(store)
    summary = store.summary()
    # All records land "today" (UTC) → a single day bucket keyed on the date.
    assert len(summary.by_day) == 1
    assert summary.by_day[0].key == date.today().isoformat()
    assert summary.by_day[0].calls == 3


def test_model_unspecified_key_when_model_is_none(store) -> None:
    store.record(provider="ollama", model=None, duration_ms=10, ok=True)
    summary = store.summary()
    assert summary.by_model[0].key == "(unspecified)"


def test_since_window_filters_records(store) -> None:
    _seed(store)
    now = datetime.now(timezone.utc)
    # A window opening in the future excludes everything; the past includes all.
    assert store.summary(since=now + timedelta(days=1)).total_calls == 0
    past = store.summary(since=now - timedelta(days=1))
    assert past.total_calls == 3
    assert past.since == now - timedelta(days=1)


def test_purge_before_removes_old_rows_only(store) -> None:
    _seed(store)
    now = datetime.now(timezone.utc)
    # Nothing older than yesterday.
    assert store.purge_before(now - timedelta(days=1)) == 0
    assert store.summary().total_calls == 3
    # Everything is older than tomorrow.
    assert store.purge_before(now + timedelta(days=1)) == 3
    assert store.summary().total_calls == 0


def test_empty_summary_is_zeroed(store) -> None:
    summary = store.summary()
    assert summary.total_calls == 0
    assert summary.avg_duration_ms == 0.0
    assert summary.by_day == []
    assert summary.kinds == []


def test_purge_before_boundary_with_explicit_ages() -> None:
    # Precise retention boundary: rows are inserted with controlled timestamps so
    # the cutoff falls strictly between them (the shared fixture stamps "now").
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from superset_ai_agent.persistence.models import AiAgentLlmCall

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    store = SqlAlchemyLlmUsageStore(factory)

    now = datetime.now(timezone.utc)
    ages_days = [100, 31, 29, 1]
    with factory() as session:
        for age in ages_days:
            session.add(
                AiAgentLlmCall(
                    id=uuid4().hex,
                    created_at=now - timedelta(days=age),
                    kind="chat",
                    provider="openai",
                    model="gpt-5.2",
                    duration_ms=10,
                    ok=True,
                )
            )
        session.commit()

    removed = store.purge_before(now - timedelta(days=30))

    # Only the 100d and 31d rows are older than the 30-day cutoff.
    assert removed == 2
    with factory() as session:
        remaining = session.execute(select(AiAgentLlmCall.created_at)).all()
    assert len(remaining) == 2
    assert store.summary().total_calls == 2
