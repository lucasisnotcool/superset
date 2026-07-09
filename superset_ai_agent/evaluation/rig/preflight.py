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
"""Fail-fast preflight (plan R9): validate everything before any question runs.

Checks, in order: corpus wellness (already parsed), the DB connection (resolve by
name/id, or register a URI from an env var if missing — DP-9), the target schemas
exist on that connection, and judge readiness. Backend-agnostic: unlike
``eval_v2.assert_eval_preconditions`` it does **not** require Postgres (R4). The
pure checks are unit-tested; the live composition needs a stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from rig.corpus import CorpusLoad
from rig.fixture import Fixture
from rig.model_client import JudgeSettings


@dataclass
class PreflightReport:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    database_id: int | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


# --------------------------------------------------------------------------- #
# Pure checks
# --------------------------------------------------------------------------- #
def check_corpus(load: CorpusLoad) -> list[str]:
    return [f"corpus: {e}" for e in load.errors]


def check_schemas(available: list[str], required: tuple[str, ...]) -> list[str]:
    """Case-insensitive subset check (Oracle reports UPPERCASE — plan §7 risk)."""

    have = {s.lower() for s in available}
    missing = [s for s in required if s.lower() not in have]
    if missing:
        return [
            f"schema(s) not found on the connection: {', '.join(missing)} "
            f"(available: {', '.join(sorted(available)) or 'none'})"
        ]
    return []


def needs_judge(load: CorpusLoad) -> bool:
    return any(r.answer_type == "eval_note" for r in load.records)


# --------------------------------------------------------------------------- #
# Live pieces
# --------------------------------------------------------------------------- #
def list_schemas(client: Any, database_id: int) -> list[str]:
    """List a connection's schemas via Superset (best-effort; [] on failure)."""

    try:
        resp = client._superset(
            "GET", f"/api/v1/database/{database_id}/schemas/?q=(force:!f)"
        )
        data = client._ok(resp, "GET database schemas")
        return [str(s) for s in data.get("result", [])]
    except Exception:  # noqa: BLE001 - reported as a problem by the caller
        return []


def register_connection_from_uri(client: Any, *, name: str, uri: str) -> int | None:
    """Register a Superset DB connection from a URI (DP-9). None on failure.

    Admin-level; used only when the fixture gives ``connection_uri_env`` and no
    connection matches by name. The URI value comes from the environment, never the
    manifest.
    """

    body = {"database_name": name, "sqlalchemy_uri": uri, "expose_in_sqllab": True}
    try:
        resp = client._superset("POST", "/api/v1/database/", json=body)
        data = client._ok(resp, "POST /api/v1/database/")
        return int(data.get("id")) if data.get("id") is not None else None
    except Exception:  # noqa: BLE001
        return None


def resolve_connection(
    client: Any, fixture: Fixture, *, register_missing: bool = True
) -> tuple[int | None, list[str]]:
    """Resolve the fixture's DB to a Superset ``database_id`` (register if needed)."""

    problems: list[str] = []
    # Explicit id / name path (reuses AgentClient.resolve_database_id).
    if fixture.database_id is not None:
        client.config.database_id = fixture.database_id
    if fixture.database_name:
        client.config.database_name = fixture.database_name
    if fixture.database_id is not None or fixture.database_name:
        try:
            return client.resolve_database_id(), problems
        except Exception as ex:  # noqa: BLE001
            problems.append(f"connection: {ex}")
            # fall through to URI registration if available

    if fixture.connection_uri_env:
        uri = os.getenv(fixture.connection_uri_env)
        if not uri:
            problems.append(
                f"connection: env var {fixture.connection_uri_env} is unset "
                "(the Oracle URI must be provided, never hard-coded)."
            )
            return None, problems
        if register_missing:
            db_id = register_connection_from_uri(client, name=fixture.id, uri=uri)
            if db_id is None:
                problems.append(
                    "connection: failed to register the URI as a Superset "
                    "connection (needs admin; check the Oracle driver is in the "
                    "Superset image)."
                )
            return db_id, problems
    if not problems:
        problems.append("connection: no database_name/id and no connection_uri_env.")
    return None, problems


def run_preflight(
    client: Any,
    fixture: Fixture,
    corpus_load: CorpusLoad,
    *,
    model_client: Any | None = None,
    judge: JudgeSettings | None = None,
    register_missing: bool = True,
) -> PreflightReport:
    """Compose all checks. Returns a report; ``.ok`` gates the run."""

    report = PreflightReport()
    report.problems.extend(check_corpus(corpus_load))
    if report.problems:
        return report  # a broken corpus can't be run; stop early

    db_id, conn_problems = resolve_connection(
        client, fixture, register_missing=register_missing
    )
    report.problems.extend(conn_problems)
    report.database_id = db_id
    if db_id is not None:
        report.problems.extend(
            check_schemas(list_schemas(client, db_id), fixture.schemas)
        )

    if needs_judge(corpus_load):
        judge = judge or JudgeSettings()
        if not judge.enabled:
            report.warnings.append(
                "corpus has eval_note items but the judge is disabled — they will "
                "score needs_review."
            )
        elif model_client is None:
            report.warnings.append(
                "corpus has eval_note items but no model client was built — they "
                "will score needs_review."
            )
    return report
