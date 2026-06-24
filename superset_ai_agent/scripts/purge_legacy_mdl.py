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

"""One-time purge of legacy YAML/snake-case MDL data (wren_full.md D2).

After the native-JSON rebuild, pre-existing MDL file rows hold YAML content that
no longer parses. Migration ``0004_purge_legacy_yaml_mdl`` removes them
automatically when ``AI_AGENT_RUN_MIGRATIONS=true``. This script is the manual
equivalent for operators who run migrations out of band or want to inspect the
impact first.

Usage (from the repo root, with the agent's environment configured)::

    # Report what would be purged (default, no writes):
    python -m superset_ai_agent.scripts.purge_legacy_mdl

    # Perform the purge:
    python -m superset_ai_agent.scripts.purge_legacy_mdl --apply

It reads the same ``AI_AGENT_*`` configuration as the app, so it targets whatever
database ``AI_AGENT_DATABASE_URL`` points at.
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import create_engine_from_config

#: The only valid MDL content type after the native-JSON rebuild.
_JSON_CONTENT_TYPE = "application/json"

#: Tables emptied as derived state (rebuilt on the next materialize). The legacy
#: semantic-overlay derived tables were removed in the C6 cleanup, so there are no
#: derived tables to clear; the purge now only removes legacy non-JSON MDL files.
_DERIVED_TABLES: tuple[str, ...] = ()


def _counts(connection: sa.Connection) -> dict[str, int]:
    legacy = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ai_agent_semantic_mdl_files "
            "WHERE content_type <> :ct"
        ),
        {"ct": _JSON_CONTENT_TYPE},
    ).scalar_one()
    json_files = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM ai_agent_semantic_mdl_files "
            "WHERE content_type = :ct"
        ),
        {"ct": _JSON_CONTENT_TYPE},
    ).scalar_one()
    derived = {
        table: connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608 - fixed table names
        ).scalar_one()
        for table in _DERIVED_TABLES
    }
    return {"legacy_mdl_files": legacy, "json_mdl_files": json_files, **derived}


def _purge(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "DELETE FROM ai_agent_semantic_mdl_files WHERE content_type <> :ct"
        ),
        {"ct": _JSON_CONTENT_TYPE},
    )
    for table in _DERIVED_TABLES:
        connection.execute(sa.text(f"DELETE FROM {table}"))  # noqa: S608
    connection.execute(
        sa.text("UPDATE ai_agent_semantic_projects SET current_version_id = NULL")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the purge. Without this flag the script only reports.",
    )
    args = parser.parse_args(argv)

    config = AgentConfig.from_env()
    engine = create_engine_from_config(config)

    with engine.begin() as connection:
        before = _counts(connection)
        print("Legacy (non-JSON) MDL files:", before["legacy_mdl_files"])
        print("Native JSON MDL files (kept):", before["json_mdl_files"])
        for table in _DERIVED_TABLES:
            print(f"Derived rows in {table}:", before[table])

        if before["legacy_mdl_files"] == 0:
            print("\nNothing to purge — the store is already native JSON.")
            return 0

        if not args.apply:
            print(
                "\nDry run. Re-run with --apply to purge the legacy MDL files and "
                "clear the derived semantic-layer state."
            )
            return 0

        _purge(connection)
        print(
            f"\nPurged {before['legacy_mdl_files']} legacy MDL file(s) and cleared "
            "derived semantic-layer state. Re-onboard to re-author native MDL."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
