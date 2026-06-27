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

"""Self-contained, sqlglot-only SQL parsing for the standalone agent.

The AI agent runs as a standalone microservice (``docker/Dockerfile.ai-agent``)
that ships **only** ``sqlglot`` — never the full ``superset`` package. Importing
anything under ``superset`` would execute ``superset/__init__.py``, which boots
the entire Superset Flask application (app factory, extensions, security
manager); that dependency tree is intentionally absent from the agent image, so
a top-level ``from superset.sql.parse import ...`` crashes the service at start.

This module therefore reproduces the *minimal* slice of ``superset.sql.parse``
that the deterministic SQL policy depends on, using only ``sqlglot``:

* :data:`SQLGLOT_DIALECTS` — engine-string → sqlglot dialect map.
* :class:`SQLScript` — statement splitting plus ``has_mutation`` /
  ``has_unparseable_statement``.
* :class:`SqlParseError` — local stand-in for ``SupersetParseError``.

The mutation-detection logic mirrors
``superset.sql.parse.SQLStatement.is_mutating`` one-to-one. A parity test
(``tests/unit_tests/superset_ai_agent/test_sql_policy_parity.py``) cross-checks
this port against Superset core whenever ``superset`` is importable, so any
drift in core is caught in CI even though the agent never imports core at
runtime.

The agent's SQL policy is defense-in-depth, not the authoritative database
boundary: when SQL actually executes through the REST adapter, Superset's own
``raise_for_access`` / RLS enforce per-user authorization (see
``superset_ai_agent.config`` R-CFG). This port is fail-closed — anything it
cannot prove safe is left for the policy to block.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialects

#: Engine-string → sqlglot dialect, mirroring
#: ``superset.sql.parse.SQLGLOT_DIALECTS``. Engines that core maps to a *custom*
#: Superset dialect class (db2, dremio, firebolt, odelasticsearch, pinot,
#: vertica, singlestoredb) or to the ``ydb`` plugin are mapped to ``None`` (the
#: sqlglot base dialect) here, because those dialect classes live in the
#: ``superset`` package the agent does not ship. Base-dialect parsing is
#: strictly more conservative: dialect-specific syntax that fails to parse falls
#: closed to ``unparseable``/``opaque`` rather than being assumed safe.
SQLGLOT_DIALECTS: dict[str, Dialects | None] = {
    "base": Dialects.DIALECT,
    "ascend": Dialects.HIVE,
    "awsathena": Dialects.ATHENA,
    "bigquery": Dialects.BIGQUERY,
    "datastore": Dialects.BIGQUERY,
    "clickhouse": Dialects.CLICKHOUSE,
    "clickhousedb": Dialects.CLICKHOUSE,
    "cockroachdb": Dialects.POSTGRES,
    "couchbase": Dialects.MYSQL,
    "databricks": Dialects.DATABRICKS,
    "db2": None,  # core: DB2 (custom)
    "dremio": None,  # core: Dremio (custom)
    "drill": Dialects.DRILL,
    "druid": Dialects.DRUID,
    "duckdb": Dialects.DUCKDB,
    "firebolt": None,  # core: Firebolt (custom)
    "gsheets": Dialects.SQLITE,
    "hana": Dialects.POSTGRES,
    "hive": Dialects.HIVE,
    "impala": Dialects.HIVE,
    "mariadb": Dialects.MYSQL,
    "motherduck": Dialects.DUCKDB,
    "mssql": Dialects.TSQL,
    "mysql": Dialects.MYSQL,
    "netezza": Dialects.POSTGRES,
    "oceanbase": Dialects.MYSQL,
    "odelasticsearch": None,  # core: OpenSearch (custom)
    "oracle": Dialects.ORACLE,
    "parseable": Dialects.POSTGRES,
    "pinot": None,  # core: Pinot (custom)
    "postgresql": Dialects.POSTGRES,
    "presto": Dialects.PRESTO,
    "pydoris": Dialects.DORIS,
    "redshift": Dialects.REDSHIFT,
    "risingwave": Dialects.RISINGWAVE,
    "shillelagh": Dialects.SQLITE,
    "singlestoredb": None,  # core: SingleStore (custom)
    "snowflake": Dialects.SNOWFLAKE,
    "spark": Dialects.SPARK,
    "sqlite": Dialects.SQLITE,
    "starrocks": Dialects.STARROCKS,
    "superset": Dialects.SQLITE,
    "teradatasql": Dialects.TERADATA,
    "trino": Dialects.TRINO,
    "vertica": None,  # core: Vertica (custom)
    "yql": None,  # core: "ydb" plugin dialect
}


class SqlParseError(Exception):
    """Raised when sqlglot cannot parse a script.

    Mirrors the ``.message`` attribute the policy reads off Superset core's
    ``SupersetParseError`` so ``classify_sql``'s error handling is unchanged.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _Statement:
    """A single parsed SQL statement (sqlglot-only).

    Exposes the attributes the policy reads: ``_parsed`` (the sqlglot AST) and
    ``format()``. Mirrors the subset of
    ``superset.sql.parse.SQLStatement`` the policy uses.
    """

    def __init__(self, parsed: exp.Expression, dialect: Dialects | None) -> None:
        self._parsed = parsed
        self._dialect = dialect

    def format(self, comments: bool = True) -> str:
        """Render the statement back to SQL (used to re-classify each
        statement of a multi-statement script)."""
        return self._parsed.sql(dialect=self._dialect, pretty=True, comments=comments)

    # Function names that mutate server-side state but appear in the AST as a
    # plain function call inside a non-mutating wrapper. Mirrors
    # ``SQLStatement._MUTATING_FUNCTION_NAMES``.
    _MUTATING_FUNCTION_NAMES = frozenset(
        {
            "LO_FROM_BYTEA",
            "LO_EXPORT",
            "LO_IMPORT",
            "LO_PUT",
            "LO_CREATE",
            "LOWRITE",
            "LO_UNLINK",
            "SETVAL",
            "NEXTVAL",
        }
    )

    # PostgreSQL constructs sqlglot represents as an opaque ``exp.Command``.
    # Mirrors ``SQLStatement._POSTGRES_MUTATING_COMMAND_NAMES``.
    _POSTGRES_MUTATING_COMMAND_NAMES = frozenset(
        {
            "DO",
            "PREPARE",
            "EXECUTE",
            "CALL",
            "COPY",
            "GRANT",
            "REVOKE",
            "SET",
            "RESET",
            "REFRESH",
            "REINDEX",
            "VACUUM",
            "CREATE",
            "ALTER",
            "DROP",
            "LOAD",
        }
    )

    # Dialects where ``SELECT ... INTO target`` is CTAS (mutates schema).
    # Mirrors ``SQLStatement._SELECT_INTO_CTAS_DIALECTS``.
    _SELECT_INTO_CTAS_DIALECTS = frozenset(
        {Dialects.POSTGRES, Dialects.REDSHIFT, Dialects.TSQL}
    )

    def is_mutating(self) -> bool:
        """Port of ``superset.sql.parse.SQLStatement.is_mutating``."""
        mutating_nodes = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Merge,
            exp.Create,
            exp.Drop,
            exp.TruncateTable,
            exp.Alter,
            exp.Copy,
            exp.Grant,
            exp.Revoke,
            exp.Comment,
        )
        if self._parsed.find(*mutating_nodes):
            return True

        # `SELECT ... INTO new_table FROM ...` is CTAS only in some dialects.
        if (
            self._dialect in self._SELECT_INTO_CTAS_DIALECTS
            and isinstance(self._parsed, exp.Select)
            and self._parsed.args.get("into")
        ):
            return True

        # PostgreSQL large-object / sequence mutators that parse as plain
        # function calls (``exp.Anonymous``) inside an otherwise read-only AST.
        if self._dialect == Dialects.POSTGRES and any(
            function.name.upper() in self._MUTATING_FUNCTION_NAMES
            for function in self._parsed.find_all(exp.Anonymous)
        ):
            return True

        # ALTER parsed as a Command in some dialects (Oracle, MS SQL).
        if isinstance(self._parsed, exp.Command) and self._parsed.name == "ALTER":
            return True

        # PostgreSQL constructs sqlglot represents as an opaque ``exp.Command``.
        if (
            self._dialect == Dialects.POSTGRES
            and isinstance(self._parsed, exp.Command)
            and self._parsed.name.upper() in self._POSTGRES_MUTATING_COMMAND_NAMES
        ):
            return True

        # PostgreSQL runs DMLs prefixed by ``EXPLAIN ANALYZE``.
        if (
            self._dialect == Dialects.POSTGRES
            and isinstance(self._parsed, exp.Command)
            and self._parsed.name == "EXPLAIN"
            and self._parsed.expression is not None
            and self._parsed.expression.name.upper().startswith("ANALYZE ")
        ):
            analyzed_sql = self._parsed.expression.name[len("ANALYZE ") :]
            return _Statement(
                _parse_one(analyzed_sql, self._dialect),
                self._dialect,
            ).is_mutating()

        return False


def _parse(script: str, engine: str) -> list[exp.Expression]:
    """Parse ``script`` into statements, mirroring core's backtick fallback.

    When the base dialect fails on backtick-quoted identifiers, retry with the
    MySQL dialect (which supports backticks natively), exactly as
    ``superset.sql.parse.SQLStatement._parse`` does.
    """
    dialect = SQLGLOT_DIALECTS.get(engine)
    try:
        statements = sqlglot.parse(script, dialect=dialect)
    except sqlglot.errors.ParseError as ex:
        if (dialect is None or dialect == Dialects.DIALECT) and "`" in script:
            try:
                statements = sqlglot.parse(script, dialect=Dialects.MYSQL)
            except sqlglot.errors.ParseError:
                raise SqlParseError(str(ex)) from ex
        else:
            raise SqlParseError(str(ex)) from ex
    except sqlglot.errors.SqlglotError as ex:
        raise SqlParseError("Unable to parse script") from ex

    # sqlglot parses comments after the last semicolon as a trailing
    # ``exp.Semicolon`` statement; drop it so the count is not inflated.
    if len(statements) > 1 and isinstance(statements[-1], exp.Semicolon):
        statements.pop()

    return [ast for ast in statements if ast]


def _parse_one(statement: str, dialect: Dialects | None) -> exp.Expression:
    """Parse a single statement (used by the EXPLAIN ANALYZE recursion)."""
    parsed = sqlglot.parse_one(statement, dialect=dialect)
    if parsed is None:  # pragma: no cover - defensive
        raise SqlParseError("Unable to parse statement")
    return parsed


class SQLScript:
    """A SQL script of 0+ statements (sqlglot-only port of core's SQLScript)."""

    def __init__(self, script: str, engine: str) -> None:
        self.engine = engine
        dialect = SQLGLOT_DIALECTS.get(engine)
        self.statements = [_Statement(ast, dialect) for ast in _parse(script, engine)]

    @property
    def has_unparseable_statement(self) -> bool:
        """True if any statement is an opaque ``exp.Command`` sqlglot could not
        fully model. Mirrors ``SQLScript.has_unparseable_statement`` for the
        sqlglot-engine case (the agent has no non-sqlglot engines)."""
        return any(
            isinstance(statement._parsed, exp.Command) for statement in self.statements
        )

    def has_mutation(self) -> bool:
        """True if any statement mutates data or server state."""
        return any(statement.is_mutating() for statement in self.statements)
