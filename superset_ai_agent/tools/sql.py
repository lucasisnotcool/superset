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

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from superset_ai_agent.schemas import SqlValidation

FORBIDDEN_KEYWORDS = {
    "ALTER",
    "CREATE",
    "DELETE",
    "DROP",
    "GRANT",
    "INSERT",
    "MERGE",
    "REPLACE",
    "REVOKE",
    "TRUNCATE",
    "UPDATE",
}

DESTRUCTIVE_EXPRESSIONS = (
    exp.Alter,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Update,
)

SQLGLOT_DIALECT_ALIASES = {
    "postgresql": "postgres",
    "postgresql+psycopg2": "postgres",
    "postgresql+psycopg": "postgres",
    "postgresql+asyncpg": "postgres",
    "postgres": "postgres",
    "mysql+pymysql": "mysql",
    "mysql+mysqldb": "mysql",
    "mariadb": "mysql",
    "mssql": "tsql",
    "sqlserver": "tsql",
    "sql_server": "tsql",
    "presto": "presto",
    "prestodb": "presto",
    "trino": "trino",
    "bigquery": "bigquery",
    "sqlite": "sqlite",
    "sqlite+pysqlite": "sqlite",
}


def normalize_sqlglot_dialect(dialect: str | None) -> str | None:
    """Normalize Superset/SQLAlchemy dialect names for sqlglot."""

    if not dialect:
        return None
    normalized = dialect.strip().lower().replace("-", "_")
    if not normalized:
        return None
    return SQLGLOT_DIALECT_ALIASES.get(normalized, normalized)


def parse_sql(sql: str, dialect: str | None) -> list[exp.Expression | None]:
    """Parse SQL with a dialect hint, falling back to generic SQL when needed."""

    try:
        return sqlglot.parse(sql, read=dialect)
    except ValueError:
        return sqlglot.parse(sql)


def validate_read_only_sql(
    sql: str,
    *,
    dialect: str | None = None,
    default_limit: int = 1000,
) -> SqlValidation:
    """Validate SQL for the POC's read-only execution policy."""

    stripped = sql.strip().rstrip(";")
    errors: list[str] = []
    parse_dialect = normalize_sqlglot_dialect(dialect)

    if not stripped:
        return SqlValidation(
            is_valid=False,
            is_read_only=False,
            dialect=parse_dialect,
            errors=["SQL is empty."],
        )

    keyword_hits = [
        keyword
        for keyword in FORBIDDEN_KEYWORDS
        if re.search(rf"\b{keyword}\b", stripped, flags=re.IGNORECASE)
    ]
    if keyword_hits:
        errors.append(
            "Forbidden SQL keyword(s): " + ", ".join(sorted(set(keyword_hits)))
        )

    try:
        expressions = parse_sql(stripped, parse_dialect)
    except sqlglot.errors.ParseError as ex:
        return SqlValidation(
            is_valid=False,
            is_read_only=False,
            dialect=parse_dialect,
            errors=[f"SQL parse failed: {ex}"],
        )

    if len(expressions) != 1:
        errors.append("Exactly one SQL statement is allowed.")

    for parsed in expressions:
        if parsed is None:
            errors.append("SQL parser returned an empty statement.")
            continue
        if any(
            parsed.find(expression_type)
            for expression_type in DESTRUCTIVE_EXPRESSIONS
        ):
            errors.append("Statement contains a non-read-only SQL expression.")
        if not isinstance(parsed, (exp.Select, exp.Union, exp.With)):
            root = parsed.__class__.__name__
            if root not in {"Select", "Union", "With"}:
                errors.append(
                    f"Only SELECT/CTE query statements are allowed; got {root}."
                )

    normalized = None if errors else ensure_limit(stripped, default_limit)
    return SqlValidation(
        is_valid=not errors,
        is_read_only=not errors,
        normalized_sql=normalized,
        dialect=parse_dialect,
        errors=errors,
    )


def ensure_limit(sql: str, default_limit: int) -> str:
    """Append a conservative LIMIT when the query does not include one."""

    if re.search(r"\blimit\b", sql, flags=re.IGNORECASE):
        return sql
    return f"{sql}\nLIMIT {default_limit}"
