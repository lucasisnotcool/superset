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

"""Retention purge for the append-per-call LLM telemetry table.

``ai_agent_llm_calls`` grows one row per ``ModelClient.chat`` invocation. This
script deletes rows older than the retention window so the table stays bounded;
the admin usage view reads only aggregates, so dropping old rows just shortens
the available history. Run it on a schedule (cron / a periodic job).

The window defaults to ``AI_AGENT_LLM_USAGE_RETENTION_DAYS`` (90) and can be
overridden with ``--days``. ``--days 0`` is a no-op (keep forever).

Usage (reads the same ``AI_AGENT_*`` config as the app)::

    # Report how many rows would be purged (default, no writes):
    python -m superset_ai_agent.scripts.purge_llm_calls

    # Perform the purge:
    python -m superset_ai_agent.scripts.purge_llm_calls --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.usage_store import SqlAlchemyLlmUsageStore
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
)
from superset_ai_agent.persistence.models import AiAgentLlmCall


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the purge. Without this flag the script only reports.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help=(
            "Retention window in days (overrides AI_AGENT_LLM_USAGE_RETENTION_DAYS). "
            "0 keeps everything."
        ),
    )
    args = parser.parse_args(argv)

    config = AgentConfig.from_env()
    days = args.days if args.days is not None else config.llm_usage_retention_days

    print(f"Database URL: {config.agent_database_url}")
    print(f"Retention window: {days} day(s)")

    if days <= 0:
        print("\nRetention disabled (days <= 0); nothing to purge.")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    engine = create_engine_from_config(config)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        doomed = session.execute(
            select(func.count())
            .select_from(AiAgentLlmCall)
            .where(AiAgentLlmCall.created_at < cutoff)
        ).scalar_one()

    print(f"\nRows older than {cutoff.isoformat()}: {doomed}")
    if doomed == 0:
        print("Nothing to purge.")
        return 0
    if not args.apply:
        print("\nDry run. Re-run with --apply to delete these rows.")
        return 0

    removed = SqlAlchemyLlmUsageStore(session_factory).purge_before(cutoff)
    print(f"\nDeleted {removed} LLM-call row(s).")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
