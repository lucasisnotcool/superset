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

"""Migration 0011 backfills slugs and swaps the project identity constraint."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_HERE = Path(__file__).resolve()
_MIGRATIONS_DIR = (
    _HERE.parents[3] / "superset_ai_agent" / "persistence" / "migrations"
)
_ALEMBIC_INI = _MIGRATIONS_DIR.parent / "alembic.ini"


def _config(db_url: str) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def _insert_project(
    conn, *, pid: str, name: str, fp: str = "fp", catalog: str = "prod"
):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    conn.execute(
        text(
            "INSERT INTO ai_agent_semantic_projects "
            "(id, name, owner_id, database_uri_fingerprint, catalog_name, "
            "schema_name, visibility, status, created_at, updated_at) VALUES "
            "(:id, :name, 'o', :fp, :catalog, 'sales', 'db_access', 'active', "
            ":now, :now)"
        ),
        {"id": pid, "name": name, "fp": fp, "catalog": catalog, "now": now},
    )


def test_migration_0011_backfills_unique_slugs_and_swaps_constraint(tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'agent.db'}"
    config = _config(db_url)
    engine = create_engine(db_url, future=True)

    command.upgrade(config, "0010_semantic_project_schemas")
    with engine.begin() as conn:
        # Two same-named projects in one (db, catalog) → must get distinct slugs.
        _insert_project(conn, pid="p1", name="Revenue Model")
        _insert_project(conn, pid="p2", name="Revenue Model")

    command.upgrade(config, "0011_project_slug_identity")

    with engine.connect() as conn:
        slugs = dict(
            conn.execute(
                text("SELECT id, slug FROM ai_agent_semantic_projects ORDER BY id")
            ).fetchall()
        )
    assert slugs == {"p1": "revenue-model", "p2": "revenue-model-2"}

    # The new constraint forbids a duplicate active slug in the same (db, catalog).
    with engine.begin() as conn:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        failed = False
        try:
            conn.execute(
                text(
                    "INSERT INTO ai_agent_semantic_projects "
                    "(id, name, slug, owner_id, database_uri_fingerprint, "
                    "catalog_name, schema_name, visibility, status, created_at, "
                    "updated_at) VALUES ('p3', 'Dup', 'revenue-model', 'o', 'fp', "
                    "'prod', 'sales', 'db_access', 'active', :now, :now)"
                ),
                {"now": now},
            )
        except Exception:  # noqa: BLE001 - IntegrityError surfaces as DBAPIError
            failed = True
        assert failed, "duplicate active slug should violate the unique constraint"

    # Downgrade restores the old constraint and drops the column.
    command.downgrade(config, "0010_semantic_project_schemas")
    with engine.connect() as conn:
        cols = {
            row[1]
            for row in conn.execute(
                text("PRAGMA table_info(ai_agent_semantic_projects)")
            ).fetchall()
        }
    assert "slug" not in cols
