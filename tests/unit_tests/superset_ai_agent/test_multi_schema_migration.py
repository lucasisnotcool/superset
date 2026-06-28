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

"""Migration 0010 backfills schema memberships from existing projects."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_HERE = Path(__file__).resolve()
_MIGRATIONS_DIR = (
    _HERE.parents[3]
    / "superset_ai_agent"
    / "persistence"
    / "migrations"
)
_ALEMBIC_INI = _MIGRATIONS_DIR.parent / "alembic.ini"


def _config(db_url: str) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_migration_0010_backfills_primary_schema_membership(tmp_path) -> None:
    db_path = tmp_path / "agent.db"
    db_url = f"sqlite+pysqlite:///{db_path}"
    config = _config(db_url)
    engine = create_engine(db_url, future=True)

    # Bring the DB up to the revision *before* the multi-schema table exists.
    command.upgrade(config, "0009_coverage_runs")

    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_agent_semantic_projects "
                "(id, name, owner_id, database_uri_fingerprint, catalog_name, "
                "schema_name, visibility, status, created_at, updated_at) VALUES "
                "(:id, :name, :owner, :fp, :catalog, :schema, 'db_access', "
                "'active', :created, :updated)"
            ),
            {
                "id": "proj-1",
                "name": "Sales.prod.sales",
                "owner": "owner",
                "fp": "fp",
                "catalog": "prod",
                "schema": "sales",
                "created": now,
                "updated": now,
            },
        )

    # Expand step: create membership table and backfill.
    command.upgrade(config, "0010_semantic_project_schemas")

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT project_id, schema_name, position FROM "
                "ai_agent_semantic_project_schemas"
            )
        ).fetchall()
    assert rows == [("proj-1", "sales", 0)]

    # Contract step is reversible: downgrade drops the table without touching
    # the projects row.
    command.downgrade(config, "0009_coverage_runs")
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    names = {name for (name,) in tables}
    assert "ai_agent_semantic_project_schemas" not in names
    assert "ai_agent_semantic_projects" in names
