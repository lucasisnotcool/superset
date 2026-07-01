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

"""Deterministic SQL safety policy for the AI agent.

Classification delegates to :mod:`superset_ai_agent.tools._sql_parse`, a
sqlglot-only port of the minimal slice of Superset core's ``SQLScript`` /
``SQLStatement`` (``superset.sql.parse``) that this policy needs. The agent
runs as a standalone microservice that ships only sqlglot — importing
``superset`` would boot the entire Superset Flask app — so the parser cannot
live in core at runtime; a parity test cross-checks the port against core
whenever ``superset`` is importable, keeping the two in lockstep. The policy
decision (``decide``) is a pure, table-driven function over (classification,
execution tier, approval).

The two concerns are intentionally separated:

* ``classify_sql`` answers *what is this SQL?* — deterministically and
  fail-closed (anything we cannot prove safe is blocked).
* ``decide`` answers *may this tier auto-run it?* — an explicit matrix so the
  policy can be read and tested without tracing control flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import sqlglot
from sqlglot import exp

# The agent ships only sqlglot, never the ``superset`` package (importing it
# boots the whole Superset Flask app — see ``superset/__init__.py``), so the
# minimal parse surface this policy needs is vendored sqlglot-only in
# ``_sql_parse``. A parity test cross-checks it against core when ``superset``
# is importable.
from superset_ai_agent.tools._sql_parse import (
    SQLGLOT_DIALECTS,
    SqlParseError,
    SQLScript,
)

#: Deterministic classes a statement/script can fall into. ``is_read_only`` is
#: derived as ``kind == "read_only"`` everywhere downstream.
SqlClassificationKind = Literal[
    "read_only",
    "mutating",
    "opaque",
    "multi",
    "unparseable",
]

#: Execution tiers from ``ConversationTurnRequest.execution_mode``.
ExecutionTier = Literal["manual", "read_only", "auto"]

#: Operator-tunable strictness for multi-statement handling. ``strict`` (default)
#: blocks every multi-statement script; ``permissive`` reclassifies a script as
#: read-only when *every* statement in it is individually read-only (a write or
#: opaque statement anywhere keeps it blocked). The knob only ever relaxes the
#: ``multi`` case — it can never let a mutating statement through.
PolicyMode = Literal["strict", "permissive"]

#: Root AST node types we positively recognise as read-only query shapes. The
#: allowlist is deliberate: anything that is not provably one of these (e.g. a
#: structured ``SET``/``USE`` ``exp.Set``, an ``exp.Command``) fails closed to
#: ``opaque`` rather than being assumed safe.
_QUERY_ROOTS = (exp.Select, exp.Union, exp.Subquery)

#: Command head-keywords for read-only introspection. ``EXPLAIN <query>`` and
#: ``DESCRIBE <table>`` inspect a plan/schema without executing the body, so
#: they are read-only — but only when the body itself is read-only (see
#: ``_is_read_only_introspection``). ``SHOW`` is intentionally excluded: it is
#: information disclosure that belongs in a denylist, not the read-only gate.
_INTROSPECTION_COMMANDS = frozenset({"EXPLAIN", "DESCRIBE", "DESC"})

#: Prefixes on an ``EXPLAIN`` body that cause the statement to actually run
#: (``EXPLAIN ANALYZE`` executes the plan). These never qualify as read-only
#: introspection regardless of the body — they require explicit approval.
_EXECUTING_EXPLAIN_PREFIXES = ("ANALYZE", "ANALYSE")


@dataclass(frozen=True)
class SqlClassification:
    """Deterministic verdict for a piece of SQL."""

    kind: SqlClassificationKind
    reason: str
    statement_count: int = 0

    @property
    def is_read_only(self) -> bool:
        return self.kind == "read_only"


@dataclass(frozen=True)
class Decision:
    """Outcome of applying the policy matrix to a classification."""

    allow: bool
    reason: str


def _is_read_only_introspection(parsed: exp.Expression, *, engine: str | None) -> bool:
    """True for ``EXPLAIN``/``DESCRIBE`` that inspect a read-only body.

    Two AST shapes occur across dialects:

    * Structured ``exp.Describe`` (e.g. MySQL ``EXPLAIN SELECT``, ``DESCRIBE t``):
      ``.this`` is the explained ``Select``/table.
    * Opaque ``exp.Command`` with head ``EXPLAIN`` (e.g. PostgreSQL): the body is
      carried as a raw string that is re-classified.

    Never returns True for ``EXPLAIN ANALYZE`` (which executes the statement) or
    for a body that does not itself classify as read-only — so an EXPLAIN of a
    DELETE stays blocked.
    """

    if isinstance(parsed, exp.Describe):
        inner = parsed.this
        if isinstance(inner, exp.Table):
            return True  # DESCRIBE <table> — schema metadata read
        return isinstance(inner, _QUERY_ROOTS)

    if (
        isinstance(parsed, exp.Command)
        and (parsed.name or "").upper() in _INTROSPECTION_COMMANDS
    ):
        # exp.Command keeps the head keyword in ``.this`` ("EXPLAIN") and the
        # remainder as a string Literal in ``.expression`` ("SELECT ...").
        body_expr = parsed.expression
        body = body_expr.name if body_expr is not None else None
        if not body or not isinstance(body, str):
            return False
        body = body.strip()
        if body.upper().startswith(_EXECUTING_EXPLAIN_PREFIXES):
            return False
        # The explained body must itself be a read-only statement. Re-classifying
        # it (not the EXPLAIN wrapper) means an EXPLAIN of any write stays blocked.
        return classify_sql(body, engine=engine).kind == "read_only"

    return False


def classify_sql(
    sql: str,
    *,
    engine: str | None,
    policy_mode: PolicyMode = "strict",
) -> SqlClassification:
    """Classify ``sql`` using Superset core's mutation detector.

    Fails closed: any parser error, opaque statement, or unrecognised root
    shape is blocked rather than assumed read-only. ``engine`` is the Superset
    database backend string (e.g. ``"postgresql"``); unknown engines fall back
    to sqlglot's base dialect inside ``SQLScript``.

    ``policy_mode`` only affects multi-statement scripts (see :data:`PolicyMode`):
    ``permissive`` reclassifies an all-read-only script as ``read_only``; a write
    or opaque statement anywhere still blocks it.
    """

    stripped = (sql or "").strip().rstrip(";").strip()
    if not stripped:
        return SqlClassification("unparseable", "SQL is empty.", 0)

    engine_name = engine or "base"
    try:
        script = SQLScript(stripped, engine=engine_name)
    except SqlParseError as ex:
        return SqlClassification("unparseable", f"SQL parse failed: {ex.message}")
    except Exception as ex:  # pylint: disable=broad-except
        # Fail closed on any unexpected parser failure (R2).
        return SqlClassification("unparseable", f"SQL could not be parsed: {ex}")

    count = len(script.statements)
    if count == 0:
        return SqlClassification("unparseable", "No SQL statement was found.", 0)
    if count > 1:
        return _classify_multi(script, engine=engine_name, policy_mode=policy_mode)
    return _classify_single(script, engine=engine_name)


def _classify_multi(
    script: SQLScript, *, engine: str | None, policy_mode: PolicyMode
) -> SqlClassification:
    """Classify a multi-statement script (``count > 1``)."""

    count = len(script.statements)
    # Permissive mode allows a multi-statement script only when every statement
    # is itself read-only. Each is re-classified through ``classify_sql`` (the
    # single-statement path), so the all-read-only check is exactly as strict as
    # the single-statement gate — a single write/opaque statement keeps the
    # whole script blocked.
    if policy_mode == "permissive" and all(
        classify_sql(statement.format(), engine=engine).kind == "read_only"
        for statement in script.statements
    ):
        return SqlClassification(
            "read_only",
            "Multiple read-only statements (permissive policy).",
            count,
        )
    return SqlClassification(
        "multi",
        f"Only a single statement may run automatically; got {count}.",
        count,
    )


def _classify_single(script: SQLScript, *, engine: str | None) -> SqlClassification:
    """Classify a single-statement script (``count == 1``)."""

    count = 1
    # Authoritative DDL/DML detection (COPY, GRANT, COMMENT, SELECT INTO CTAS,
    # lo_export/setval/nextval, EXPLAIN ANALYZE <dml>, CALL of a mutating body,
    # ...). See superset/sql/parse.py::SQLStatement.is_mutating. Checked before
    # the opaque gate so command-form writes (CALL, EXPLAIN ANALYZE DELETE) get
    # the precise "writes data" reason rather than the generic opaque one.
    if script.has_mutation():
        return SqlClassification(
            "mutating",
            "Statement writes data or changes server state.",
            count,
        )

    statement = script.statements[0]
    parsed = getattr(statement, "_parsed", None)

    # Read-only introspection (EXPLAIN <query> / DESCRIBE <table>) inspects a
    # plan or schema without running the body. Allowed only when the body is
    # itself read-only and not EXPLAIN ANALYZE — checked before the opaque gate,
    # which would otherwise block these as unparseable Commands.
    if parsed is not None and _is_read_only_introspection(parsed, engine=engine):
        return SqlClassification(
            "read_only",
            "Read-only introspection (EXPLAIN/DESCRIBE).",
            count,
        )

    # ``has_unparseable_statement`` is True for opaque ``exp.Command`` nodes
    # (SHOW, dynamic SQL) and non-sqlglot engines — statements whose
    # tables/effects core cannot enumerate. Fail closed: we cannot prove they
    # are read-only.
    if script.has_unparseable_statement:
        return SqlClassification(
            "opaque",
            "Statement cannot be fully parsed; refusing to auto-run.",
            count,
        )

    if not isinstance(parsed, _QUERY_ROOTS):
        root = type(parsed).__name__ if parsed is not None else "unknown"
        return SqlClassification(
            "opaque",
            f"Only SELECT/CTE/UNION queries auto-run; got {root}.",
            count,
        )

    return SqlClassification("read_only", "Read-only query.", count)


def decide(
    classification: SqlClassification,
    *,
    tier: ExecutionTier,
    approved: bool = False,
) -> Decision:
    """Apply the policy matrix. Pure function — the single decision point.

    Only ``read_only`` SQL ever auto-runs. Human approval (``approved``) lets a
    read-only statement run without a tier upgrade, but never promotes a
    mutating/opaque/multi/unparseable statement (R4).
    """

    if classification.kind != "read_only":
        return Decision(False, classification.reason)
    if approved:
        return Decision(True, "User-approved read-only query.")
    if tier in ("read_only", "auto"):
        return Decision(True, "Read-only query auto-executed per execution mode.")
    return Decision(False, "Manual mode requires explicit approval.")


def _has_top_level_limit(sql: str, engine: str | None) -> bool:
    """True if the single top-level query already carries a LIMIT.

    AST-based so a LIMIT buried in a subquery no longer suppresses the outer
    cap (§2.3). On any parse ambiguity returns True so we never append a LIMIT
    to SQL that will be blocked or that we cannot model.
    """

    dialect = SQLGLOT_DIALECTS.get(engine or "base")
    try:
        statements = [stmt for stmt in sqlglot.parse(sql, dialect=dialect) if stmt]
    except Exception:  # pylint: disable=broad-except
        return True
    if len(statements) != 1:
        return True
    root = statements[0]
    if not isinstance(root, (exp.Select, exp.Union)):
        return True
    return root.args.get("limit") is not None


#: C0/C1 control characters except tab, newline and carriage return (ordinary SQL
#: whitespace). Stray control bytes from copy-paste / LLM output are a documented
#: ORA-00911 "invalid character" trigger, so they are removed before execution.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: The terminating run of semicolons/whitespace (mirrors WrenAI's per-connector
#: helper). Only the trailing terminator is removed, so a ``;`` inside a string
#: literal is preserved.
_TRAILING_TERMINATORS = re.compile(r"[;\s]+\Z")


def sanitize_sql(sql: str) -> str:
    """Strip stray control characters and the trailing statement terminator.

    Programmatic single-statement execution rejects a trailing ``;`` on Oracle and
    Trino (Oracle raises ORA-00911), and non-printable characters trigger the same
    Oracle error regardless of engine. This removes both defensively. Safe for the
    agent, which only ever executes ONE read-only statement — never a PL/SQL block
    whose closing ``END;`` must be kept.
    """

    if not sql:
        return sql
    cleaned = _CONTROL_CHARS.sub("", sql)
    return _TRAILING_TERMINATORS.sub("", cleaned).strip()


def apply_limit(sql: str, *, engine: str | None, default_limit: int) -> str:
    """Cap a top-level query that lacks a limit, in the engine's own dialect.

    Sanitizes first (:func:`sanitize_sql`), then — only when the single top-level
    query has no limit — appends the cap via the AST so the emitted clause is
    dialect-correct: ``FETCH FIRST n ROWS ONLY`` for Oracle, ``TOP n`` for T-SQL,
    ``LIMIT n`` elsewhere. Regenerating from the AST also guarantees no stray
    newline/terminator reaches the driver. Degrades to the sanitized SQL (no cap)
    when the query is already limited, unparseable, or not a SELECT/UNION — the
    executor's fetch cap still bounds rows in that case.
    """

    stripped = sanitize_sql(sql)
    if not stripped or _has_top_level_limit(stripped, engine):
        return stripped
    dialect = SQLGLOT_DIALECTS.get(engine or "base")
    try:
        # _has_top_level_limit returned False, so this parses as a single
        # SELECT/UNION; append the cap on the AST and render in the target dialect.
        root = sqlglot.parse_one(stripped, dialect=dialect)
        return root.limit(default_limit).sql(dialect=dialect)
    except Exception:  # pylint: disable=broad-except
        return stripped
