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

"""Purge job rows whose stored result no longer validates against the schema.

A job persisted by an older revision can hold an ``OnboardingResult`` whose
files carry the pre-native-JSON ``content_type='application/x-yaml'`` (or any
other shape the current schema rejects). Such a row breaks every code path that
lists jobs -- notably the MDL Copilot readiness gate
(``_project_readiness`` -> ``list_for_project`` -> ``_from_model``) -- because
deserialization raises ``ValidationError`` for the whole listing.

The job store already degrades an unparseable result to ``None`` at read time
(so the Copilot keeps working), but the dead row lingers and logs a warning on
every readiness check. This script removes those rows for good. It complements
``purge_legacy_mdl`` (which only cleans ``ai_agent_semantic_mdl_files`` and does
NOT touch the jobs table).

Detection is validation-based, not a string match: a row is "legacy" when its
non-null ``result`` fails ``OnboardingResult.model_validate``. Rows with a
``NULL`` result or a result the current schema accepts are left untouched.

Usage (reads the same ``AI_AGENT_*`` config as the app, so it targets whatever
``AI_AGENT_DATABASE_URL`` points at)::

    # Report what would be purged (default, no writes):
    python -m superset_ai_agent.scripts.purge_legacy_jobs

    # Perform the purge (backs up a SQLite file to <db>.bak-purge-jobs first):
    python -m superset_ai_agent.scripts.purge_legacy_jobs --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.engine import make_url

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
)
from superset_ai_agent.persistence.models import AiAgentJob
from superset_ai_agent.semantic_layer.schemas import OnboardingResult


def _is_legacy_result(job: AiAgentJob) -> bool:
    """True when the row has a non-null result the current schema rejects."""

    if job.result is None:
        return False
    try:
        OnboardingResult.model_validate(job.result)
    except ValidationError:
        return True
    return False


def _backup_sqlite(database_url: str) -> Path | None:
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return None
    if not url.database or url.database == ":memory:":
        return None
    source = Path(url.database).expanduser().resolve()
    if not source.exists():
        return None
    backup = source.with_suffix(source.suffix + ".bak-purge-jobs")
    shutil.copy2(source, backup)
    return backup


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
    session_factory = create_session_factory(engine)

    print(f"Database URL: {config.agent_database_url}")

    with session_factory() as session:
        legacy = [
            job for job in session.query(AiAgentJob).all() if _is_legacy_result(job)
        ]

        if not legacy:
            print("\nNo legacy job rows found — every stored result validates.")
            return 0

        print(f"\nLegacy job rows (unparseable result): {len(legacy)}")
        for job in legacy:
            print(
                f"  {job.id}  kind={job.kind}  "
                f"status={job.status}  project={job.project_id}"
            )

        if not args.apply:
            print("\nDry run. Re-run with --apply to delete these rows.")
            return 0

        backup = _backup_sqlite(config.agent_database_url)
        if backup is not None:
            print(f"\nBacked up SQLite database to: {backup}")

        for job in legacy:
            session.delete(job)
        session.commit()
        print(f"\nDeleted {len(legacy)} legacy job row(s). Restart the agent.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
