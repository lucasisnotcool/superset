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

"""SemanticEngine seam — the wren-core parity boundary (wren_full.md Phase 1).

The engine compiles MDL, validates it, and — the parity unlock — **plans**
semantic SQL (written against MDL logical models) into native source-dialect SQL
(`plan_sql`). It never executes: the rewritten native SQL still flows through
`validate_read_only_sql` and the Superset executor.

Two bindings: `PassthroughEngine` (default; returns SQL unchanged) and
`WrenCoreEngine` (real rewrite via the optional `wren-core` engine).
"""

from __future__ import annotations

from typing import Protocol

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from superset_ai_agent.semantic_layer.mdl_compile import CompiledManifest
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlValidationResult,
)

#: Superset DB backend -> wren-core source/dialect token. Unknown backends fall
#: back to passthrough planning with a warning (wren_full.md R-B). Re-verify the
#: token set against the installed wren-core version on upgrade (R-A/R16).
BACKEND_TO_WREN_DIALECT: dict[str, str] = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "bigquery": "bigquery",
    "snowflake": "snowflake",
    "mysql": "mysql",
    "duckdb": "duckdb",
    "clickhouse": "clickhouse",
    "trino": "trino",
    "presto": "trino",
    "mssql": "mssql",
    "redshift": "redshift",
}


def resolve_dialect(backend: str | None) -> str | None:
    """Map a Superset database backend to a wren-core dialect token, or None."""

    if not backend:
        return None
    return BACKEND_TO_WREN_DIALECT.get(backend.strip().lower())


class PlannedSql(BaseModel):
    """Result of planning semantic SQL into native source SQL."""

    native_sql: str
    engine: str
    rewritten: bool = False
    referenced_tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SemanticEngine(Protocol):
    """Compile + validate + plan MDL-backed SQL. Never executes."""

    name: str

    def is_available(self) -> bool:
        """Return whether the engine's backing dependency is usable."""

    def compile(self, mdl_files: list[MdlFile]) -> CompiledManifest:
        """Compile authoring MDL files into a canonical engine manifest."""

    def validate(
        self,
        manifest: CompiledManifest,
        *,
        deep: bool = False,
        schema_index: SchemaIndex | None = None,
    ) -> MdlValidationResult:
        """Validate a compiled manifest (structural/physical; deep when supported)."""

    def plan_sql(
        self,
        semantic_sql: str,
        manifest: CompiledManifest,
        *,
        dialect: str | None = None,
    ) -> PlannedSql:
        """Rewrite semantic SQL into native source SQL (no execution)."""


def extract_referenced_tables(sql: str, *, dialect: str | None = None) -> list[str]:
    """Best-effort physical-table extraction for the resolution gate.

    Uses sqlglot (always available); on a parse failure returns ``[]`` so the
    caller degrades to structural-only checks rather than blocking execution.
    """

    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:  # pylint: disable=broad-except - parser is best-effort
        return []
    return sorted({table.name for table in parsed.find_all(exp.Table) if table.name})


def extract_qualified_tables(
    sql: str, *, dialect: str | None = None
) -> list[tuple[str | None, str]]:
    """Schema-qualified physical-table extraction for the access-aware recall gate.

    Returns ``(schema, table)`` pairs (``schema`` is ``None`` when the reference is
    unqualified). Used to RBAC-filter recalled NL->SQL pairs against the set of
    tables a user can actually reach: a pair is safe only if *every* table it
    references is in that set. On a parse failure returns ``[]`` so the caller
    **fails closed** (an example whose tables we cannot prove is dropped from
    recall, never surfaced). ``table.db`` is sqlglot's schema component.
    """

    try:
        parsed = sqlglot.parse_one(sql, dialect=dialect)
    except Exception:  # pylint: disable=broad-except - parser is best-effort
        return []
    pairs: set[tuple[str | None, str]] = set()
    for table in parsed.find_all(exp.Table):
        if not table.name:
            continue
        pairs.add((table.db or None, table.name))
    return sorted(pairs, key=lambda pair: (pair[0] or "", pair[1]))
