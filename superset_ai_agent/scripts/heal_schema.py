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

"""Diagnose and heal an agent database whose schema drifted from the models.

This targets the failure mode where the agent process answers a request with a
bare ``500`` because a model expects a table/column the live database does not
have -- typically a persistent SQLite volume created by an older revision than
the code now running (a common symptom after pulling a branch whose migration
was not applied, or whose model gained a column without a matching migration).

It is **non-destructive and idempotent**:

* runs Alembic ``upgrade head`` first (applies any pending migrations);
* creates any whole table present in the models but missing from the DB
  (``create_all`` only creates absent tables -- it never drops or alters);
* adds any column present in the models but missing from a table via
  ``ALTER TABLE ... ADD COLUMN`` (added **nullable**, so existing rows are
  preserved and SQLite accepts it on a populated table);
* backs up a SQLite file to ``<db>.bak-heal`` before writing.

It never drops or retypes a column, so it cannot lose data. A column the models
removed is left in place (harmless); a type that genuinely changed is reported
but not altered (resolve those with a real migration).

Usage (reads the same ``AI_AGENT_*`` config as the app, so it targets whatever
``AI_AGENT_DATABASE_URL`` points at)::

    # Report drift only (default, no writes):
    python -m superset_ai_agent.scripts.heal_schema

    # Apply the heal (migrate + create missing tables + add missing columns):
    python -m superset_ai_agent.scripts.heal_schema --apply

    # Skip the Alembic upgrade and only reconcile against the models:
    python -m superset_ai_agent.scripts.heal_schema --apply --no-migrate
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    run_migrations,
)
from superset_ai_agent.persistence.models import Base


def _alembic_revision(engine: Engine) -> str | None:
    """Current Alembic revision stamped in the DB, or ``None`` if unversioned."""

    inspector = sa.inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        return None
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()


def _missing_tables(engine: Engine) -> list[str]:
    existing = set(sa.inspect(engine).get_table_names())
    return [name for name in Base.metadata.tables if name not in existing]


def _missing_columns(engine: Engine) -> dict[str, list[sa.Column[Any]]]:
    """Map of table -> model columns absent from the live table.

    Only tables that already exist are inspected; whole missing tables are
    handled by ``create_all``.
    """

    inspector = sa.inspect(engine)
    existing_tables = set(inspector.get_table_names())
    drift: dict[str, list[sa.Column[Any]]] = {}
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue
        live_columns = {col["name"] for col in inspector.get_columns(table_name)}
        absent = [col for col in table.columns if col.name not in live_columns]
        if absent:
            drift[table_name] = absent
    return drift


def _add_column_sql(engine: Engine, table: str, column: sa.Column[Any]) -> str:
    """``ALTER TABLE`` clause adding ``column`` as nullable, default-preserving."""

    coltype = column.type.compile(dialect=engine.dialect)
    clause = f'ALTER TABLE {table} ADD COLUMN "{column.name}" {coltype}'
    # A server_default lets SQLite back-fill existing rows; without one the
    # column is added nullable so the ALTER is always accepted. Only literal
    # ``DefaultClause`` values carry an ``arg``; ``FetchedValue`` and friends do
    # not, so resolve defensively and skip what we cannot render.
    default = getattr(column.server_default, "arg", None)
    if default is not None:
        default_text = getattr(default, "text", default)
        clause += f" DEFAULT {default_text}"
    return clause


def _backup_sqlite(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return None
    if not url.database or url.database == ":memory:":
        return None
    source = Path(url.database).expanduser().resolve()
    if not source.exists():
        return None
    backup = source.with_suffix(source.suffix + ".bak-heal")
    shutil.copy2(source, backup)
    return backup


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the heal. Without this flag the script only reports drift.",
    )
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="Skip 'alembic upgrade head' and only reconcile against the models.",
    )
    args = parser.parse_args(argv)

    config = AgentConfig.from_env()
    engine = create_engine_from_config(config)

    print(f"Database URL: {config.agent_database_url}")
    print(f"Alembic revision (before): {_alembic_revision(engine) or '<none>'}")

    if args.apply and not args.no_migrate:
        try:
            run_migrations(config)
            print("Ran 'alembic upgrade head'.")
        except Exception as ex:  # pylint: disable=broad-except
            print(f"WARNING: alembic upgrade skipped/failed ({ex}).")
            print("Continuing with model reconciliation.")

    missing_tables = _missing_tables(engine)
    missing_columns = _missing_columns(engine)

    if not missing_tables and not missing_columns:
        print("\nNo schema drift detected — the database matches the models.")
        return 0

    print("\nSchema drift detected:")
    for table in missing_tables:
        print(f"  MISSING TABLE   {table}")
    for table, columns in missing_columns.items():
        for column in columns:
            print(f"  MISSING COLUMN  {table}.{column.name} ({column.type})")

    if not args.apply:
        print("\nDry run. Re-run with --apply to heal the schema.")
        return 0

    backup = _backup_sqlite(config.agent_database_url)
    if backup is not None:
        print(f"\nBacked up SQLite database to: {backup}")

    if missing_tables:
        Base.metadata.create_all(
            engine,
            tables=[Base.metadata.tables[name] for name in missing_tables],
        )
        print(f"Created {len(missing_tables)} missing table(s).")

    added = 0
    with engine.begin() as connection:
        for table, columns in missing_columns.items():
            for column in columns:
                connection.execute(sa.text(_add_column_sql(engine, table, column)))
                added += 1
    if added:
        print(f"Added {added} missing column(s).")

    revision_after = _alembic_revision(engine) or "<none>"
    print(f"\nHeal complete. Alembic revision (after): {revision_after}")
    print("Restart the agent and retry the request.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
