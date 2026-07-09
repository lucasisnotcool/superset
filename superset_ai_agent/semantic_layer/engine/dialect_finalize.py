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

"""Dialect finalization — the stage Wren's own pipeline has and we skipped.

wren-core rewrites semantic SQL into a *canonical* (DataFusion-flavoured) native
SQL. For most backends that output already runs (Postgres-ish). For a few, the
engine does not render clause-level dialect specifics — e.g. Oracle needs
``FETCH FIRST`` instead of ``LIMIT``. Wren's real pipeline finishes with a
``sqlglot.transpile(read="wren", write=<target>)`` pass; our embedded integration
omitted it, so those backends produced broken SQL and were kept out of the
supported-dialect map.

This module is that missing finalization pass, gated per-backend by
:data:`POST_TRANSPILE_DIALECTS`. Backends absent from the map (all wren-native
dialects) are a no-op. To support a new non-native dialect, add one line here and
ensure the backend is in ``BACKEND_TO_WREN_DIALECT``.
"""

from __future__ import annotations

import logging
import re

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

logger = logging.getLogger(__name__)

#: Oracle's nonquoted-identifier grammar: must start with a letter, then
#: letters/digits/``_``/``$``/``#``. Anything else — notably wren-core's internal
#: ``__source`` alias, whose leading underscore Oracle rejects with ORA-00911
#: "invalid character" — must be quoted to execute at all.
_ORACLE_SAFE_UNQUOTED = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]*$")

#: Target dialects whose UNQUOTED identifiers fold to UPPERCASE at parse time AND
#: whose columns SQLAlchemy reflects back as lowercase (so Superset's metadata — and
#: therefore the MDL and wren-core's quoted output — is lowercase). For these, an
#: all-lowercase *quoted* identifier denotes an uppercase-stored name, so we
#: uppercase the quoted content to match exactly (otherwise Oracle raises
#: ORA-00904). Reserved words stay quoted (safe); genuine mixed-case identifiers
#: (which SQLAlchemy never lowercases) are left untouched. T-SQL is absent — it is
#: case-insensitive and SQLAlchemy preserves its identifier case.
UPPERCASE_FOLD_DIALECTS = {"oracle"}

#: Superset DB backend -> sqlglot write-dialect. THE per-dialect seam: a backend
#: listed here has wren-core's canonical output finalized (transpiled) into the
#: mapped dialect. wren-native dialects (postgres, bigquery, snowflake, ...) are
#: deliberately absent — their canonical output already runs, so finalization is a
#: no-op and would only add risk. ``mssql`` shares Oracle's ``LIMIT`` gap (T-SQL
#: rejects ``LIMIT`` too), so it is finalized to ``tsql``.
POST_TRANSPILE_DIALECTS: dict[str, str] = {
    "oracle": "oracle",
    "mssql": "tsql",
}

#: wren-core emits DataFusion-flavoured SQL, which is not a native sqlglot dialect.
#: Postgres is the closest parse target (verified against realistic rewritten SQL);
#: kept as a single named constant so it is trivial to tune.
WREN_OUTPUT_READ_DIALECT = "postgres"


class FinalizeResult(BaseModel):
    """Outcome of the dialect-finalization pass."""

    sql: str
    #: The dialect the SQL was transpiled to, or ``None`` when no pass ran.
    target_dialect: str | None = None
    transpiled: bool = False
    #: Non-correctable warnings (a semantic re-draft cannot fix a transpiler gap),
    #: surfaced to the LLM via ``engine_warnings`` so a failed transpile is visible.
    warnings: list[str] = Field(default_factory=list)


def needs_finalization(backend: str | None) -> str | None:
    """Return the target sqlglot dialect for ``backend``, or ``None`` if native."""

    if not backend:
        return None
    return POST_TRANSPILE_DIALECTS.get(backend.strip().lower())


def finalization_guidance(backend: str | None, *, enabled: bool = True) -> str | None:
    """LLM addendum disclosing that output is transpiled for a finalized backend.

    Appended to the semantic-SQL authoring guidance so the agent knows its
    rewritten SQL is transpiled to this dialect and should prefer portable SQL.
    ``None`` for wren-native backends (no transpile) or when finalization is off.
    """

    target = needs_finalization(backend) if enabled else None
    if target is None:
        return None
    return (
        f"Note: this database's SQL is finalized by transpiling the engine's "
        f"output to {target}. Write portable, engine-parseable SQL and "
        f"semantic-layer metrics; avoid {target}-specific functions or syntax "
        f"the semantic engine cannot parse. To cap rows, write LIMIT n — it is "
        f"transpiled to the correct {target} clause automatically. Never write "
        f"FETCH FIRST ... ROWS ONLY: the semantic engine rejects the FETCH "
        f"clause."
    )


def _fold_lowercase_identifiers_upper(expression: exp.Expression) -> None:
    """Uppercase all-lowercase *quoted* identifiers in place (Oracle case fold).

    Superset/SQLAlchemy reflect Oracle's uppercase-stored columns as lowercase, so
    the MDL — and thus wren-core's quoted output — is lowercase (``"id"``). Oracle
    then rejects ``"id"`` (case-sensitive) against the stored ``ID`` with ORA-00904.
    Uppercasing the quoted content (``"id"`` -> ``"ID"``) matches the stored name
    exactly, keeps reserved words safely quoted, and leaves genuine mixed-case
    identifiers (never lowercased by SQLAlchemy) untouched.
    """

    for identifier in expression.find_all(exp.Identifier):
        if identifier.quoted and identifier.name.islower():
            identifier.set("this", identifier.name.upper())


def _quote_invalid_unquoted_identifiers(expression: exp.Expression) -> None:
    """Quote identifiers Oracle rejects unquoted (ORA-00911), in place.

    wren-core expands every model as ``SELECT __source.col ... FROM tbl AS
    __source`` with its internal ``__source`` alias UNQUOTED, and sqlglot's
    Oracle writer preserves the unquoted flag — so every semantic query reached
    Oracle with an identifier whose leading underscore raises ORA-00911.
    Quoting is safe: the alias definition and all its references render from
    this same tree, so they stay consistent. Runs AFTER the uppercase fold so a
    newly-quoted name keeps its authored case — SQLAlchemy preserves the stored
    case of names that require quoting, so folding them would mismatch storage.
    """

    for identifier in expression.find_all(exp.Identifier):
        if not identifier.quoted and not _ORACLE_SAFE_UNQUOTED.match(identifier.name):
            identifier.set("quoted", True)


def finalize_native_sql(
    native_sql: str,
    *,
    backend: str | None,
    enabled: bool = True,
) -> FinalizeResult:
    """Transpile wren-core's canonical native SQL into the backend's dialect.

    No-op (returns the SQL unchanged, ``transpiled=False``) when finalization is
    disabled, the backend is wren-native (absent from
    :data:`POST_TRANSPILE_DIALECTS`), or the SQL is empty. Degrades closed: any
    sqlglot failure returns the ORIGINAL SQL plus a non-correctable warning, so a
    transpiler gap never blocks execution outright — it surfaces to the LLM and the
    un-transpiled SQL still runs (and fails loudly if truly incompatible).
    """

    target = needs_finalization(backend) if enabled else None
    if target is None or not native_sql or not native_sql.strip():
        return FinalizeResult(sql=native_sql, transpiled=False)

    try:
        tree = sqlglot.parse_one(native_sql, read=WREN_OUTPUT_READ_DIALECT)
        if target in UPPERCASE_FOLD_DIALECTS:
            _fold_lowercase_identifiers_upper(tree)
            _quote_invalid_unquoted_identifiers(tree)
        finalized = tree.sql(dialect=target)
    except Exception as ex:  # pylint: disable=broad-except
        logger.debug("dialect finalization to %s failed", target, exc_info=True)
        return FinalizeResult(
            sql=native_sql,
            target_dialect=target,
            transpiled=False,
            warnings=[
                f"Could not finalize SQL for {target}: {ex}; "
                "running the un-transpiled query."
            ],
        )

    return FinalizeResult(
        sql=finalized,
        target_dialect=target,
        transpiled=finalized.strip() != native_sql.strip(),
    )
