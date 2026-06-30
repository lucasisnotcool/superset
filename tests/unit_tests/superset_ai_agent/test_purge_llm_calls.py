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

"""Retention purge CLI: dry-run reports, --apply deletes, days<=0 is a no-op."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.persistence.models import AiAgentLlmCall
from superset_ai_agent.scripts.purge_llm_calls import main


def _seed_db(tmp_path, ages_days: list[int]) -> str:
    url = f"sqlite:///{tmp_path}/agent.db"
    config = AgentConfig(agent_database_url=url, agent_migration_bootstrap="error")
    run_migrations(config)
    factory = create_session_factory(create_engine_from_config(config))
    now = datetime.now(timezone.utc)
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
    return url


def _count(url: str) -> int:
    factory = create_session_factory(
        create_engine_from_config(AgentConfig(agent_database_url=url))
    )
    with factory() as session:
        return session.execute(
            select(func.count()).select_from(AiAgentLlmCall)
        ).scalar_one()


def test_dry_run_reports_without_deleting(tmp_path, monkeypatch) -> None:
    url = _seed_db(tmp_path, [100, 1])
    monkeypatch.setenv("AI_AGENT_DATABASE_URL", url)

    assert main(["--days", "30"]) == 0
    assert _count(url) == 2  # dry run leaves rows intact


def test_apply_deletes_rows_older_than_window(tmp_path, monkeypatch) -> None:
    url = _seed_db(tmp_path, [100, 31, 29, 1])
    monkeypatch.setenv("AI_AGENT_DATABASE_URL", url)

    assert main(["--days", "30", "--apply"]) == 0
    assert _count(url) == 2  # only the 100d and 31d rows removed


def test_zero_days_is_a_noop(tmp_path, monkeypatch) -> None:
    url = _seed_db(tmp_path, [100])
    monkeypatch.setenv("AI_AGENT_DATABASE_URL", url)

    assert main(["--days", "0", "--apply"]) == 0
    assert _count(url) == 1
