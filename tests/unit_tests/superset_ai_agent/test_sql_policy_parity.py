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

"""Parity: the agent's vendored sqlglot parser must match Superset core.

The agent ships a sqlglot-only port of the minimal ``superset.sql.parse``
surface (``superset_ai_agent.tools._sql_parse``) because it cannot import core
at runtime. This test cross-checks the port against core's real
``SQLScript`` for the SQL-policy corpus — statement count, ``has_mutation`` and
``has_unparseable_statement`` must agree on every shared-dialect case. It runs
in CI (where ``superset`` is importable) and is the guard against the port
silently drifting from core. It is skipped if ``superset`` is unavailable.
"""

from __future__ import annotations

import pytest

from superset_ai_agent.tools._sql_parse import (
    SqlParseError,
    SQLScript as AgentSQLScript,
)
from superset_ai_agent.tools.sql_policy import classify_sql

core_parse = pytest.importorskip(
    "superset.sql.parse",
    reason="superset core not installed (standalone agent env)",
)
core_exc = pytest.importorskip("superset.exceptions")

CoreSQLScript = core_parse.SQLScript
SupersetParseError = core_exc.SupersetParseError

# The policy corpus, restricted to engines both sides map to the *same* sqlglot
# dialect (the port maps custom Superset dialects to the base dialect, so those
# engines are intentionally out of scope for byte-for-byte parity).
from tests.unit_tests.superset_ai_agent.test_sql_policy import (  # noqa: E402
    CLASSIFY_CASES,
)

_SHARED_DIALECT_ENGINES = {"postgresql", "sqlite", "mysql", "trino", None}
PARITY_CASES = [
    (engine, sql)
    for engine, sql, _ in CLASSIFY_CASES
    if engine in _SHARED_DIALECT_ENGINES
]


def _normalize(sql: str) -> str:
    # Mirror the preprocessing classify_sql applies before constructing a script.
    return (sql or "").strip().rstrip(";").strip()


@pytest.mark.parametrize("engine,sql", PARITY_CASES)
def test_vendored_parser_matches_core(engine: str, sql: str) -> None:
    stripped = _normalize(sql)
    if not stripped:
        pytest.skip("empty SQL is short-circuited by the policy, never parsed")

    engine_name = engine or "base"

    core_failed = False
    try:
        core = CoreSQLScript(stripped, engine=engine_name)
    except SupersetParseError:
        core_failed = True

    agent_failed = False
    try:
        agent = AgentSQLScript(stripped, engine=engine_name)
    except SqlParseError:
        agent_failed = True

    assert agent_failed == core_failed, (
        f"parse-failure mismatch for {sql!r}: core_failed={core_failed} "
        f"agent_failed={agent_failed}"
    )
    if core_failed:
        return

    assert len(agent.statements) == len(core.statements), (
        f"statement count mismatch for {sql!r}"
    )
    assert agent.has_mutation() == core.has_mutation(), (
        f"has_mutation mismatch for {sql!r}"
    )
    assert agent.has_unparseable_statement == core.has_unparseable_statement, (
        f"has_unparseable_statement mismatch for {sql!r}"
    )


@pytest.mark.parametrize("engine,sql,expected", CLASSIFY_CASES)
def test_classification_unchanged_against_core_corpus(
    engine: str, sql: str, expected: str
) -> None:
    # The end-to-end classification verdicts (the user-facing contract) are the
    # same ones test_sql_policy pins; asserting them here too ties the corpus to
    # the parser-parity check above in one place.
    if engine not in _SHARED_DIALECT_ENGINES:
        pytest.skip("engine uses a custom Superset dialect not vendored in the agent")
    assert classify_sql(sql, engine=engine).kind == expected
