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

"""Agent Alembic state lives in ``ai_agent_alembic_version``, not the default.

The default ``alembic_version`` collides with Superset's own migration state
when both apps share one database (the postgres-only topology), and Alembic's
default ``VARCHAR(32)`` bootstrap truncates this tree's long revision ids on
length-enforcing dialects. These tests pin the rename, the one-time adoption
of legacy agent-owned state, and that foreign (Superset-style) state is never
touched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import run_migrations


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path}/ai_agent.db"


def _tables(url: str) -> set[str]:
    return set(inspect(create_engine(url)).get_table_names())


def test_fresh_database_migrates_with_prefixed_version_table(sqlite_url) -> None:
    run_migrations(AgentConfig(agent_database_url=sqlite_url))

    tables = _tables(sqlite_url)
    assert "ai_agent_alembic_version" in tables
    assert "alembic_version" not in tables
    assert "ai_agent_document_blobs" in tables  # head reached


def test_legacy_version_table_is_adopted(sqlite_url) -> None:
    config = AgentConfig(agent_database_url=sqlite_url)
    run_migrations(config)
    engine = create_engine(sqlite_url)
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE ai_agent_alembic_version RENAME TO alembic_version")
        )

    run_migrations(config)  # adopts, then no-ops

    tables = _tables(sqlite_url)
    assert "ai_agent_alembic_version" in tables
    assert "alembic_version" not in tables


def test_foreign_version_table_is_left_alone(sqlite_url) -> None:
    config = AgentConfig(agent_database_url=sqlite_url)
    run_migrations(config)
    engine = create_engine(sqlite_url)
    with engine.begin() as conn:
        # Superset-style state in the default table (hex revision, not ours).
        conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        conn.execute(text("INSERT INTO alembic_version VALUES ('78a40c08b4be')"))

    run_migrations(config)

    with engine.connect() as conn:
        foreign = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert foreign == "78a40c08b4be"


def test_unversioned_tables_still_demand_bootstrap(sqlite_url) -> None:
    config = AgentConfig(agent_database_url=sqlite_url)
    run_migrations(config)
    engine = create_engine(sqlite_url)
    with engine.begin() as conn:
        # A pre-created-but-empty state table must still read as unversioned.
        conn.execute(text("DELETE FROM ai_agent_alembic_version"))

    with pytest.raises(RuntimeError, match="stamp_existing"):
        run_migrations(config)

    run_migrations(
        AgentConfig(
            agent_database_url=sqlite_url,
            agent_migration_bootstrap="stamp_existing",
        )
    )
    run_migrations(config)  # versioned again; idempotent
