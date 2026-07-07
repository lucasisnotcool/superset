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

"""Dimension-value probing (C2, plan_sql_agent_doc_grounding_spec.md).

Wrong string literals ("chicken biryani" vs the stored "Biryani (Chicken)")
are a text-to-SQL failure class neither the semantic layer nor documents
solve; production systems (Snowflake Cortex Analyst's per-column search,
CHESS's LSH value index) retrieve *actual stored values* for the literals a
question quotes. This is the bounded, governed-execution variant:

- fires only when the question carries an explicitly quoted literal
  (single/double quotes) — no literal, no probes;
- probes at most ``wren_dimension_value_probe_max_queries`` (column, literal)
  pairs, choosing string columns whose names best match the question;
- every probe runs through the caller's governed Superset execution path
  (per-user authorization, read-only) with a hard LIMIT;
- results are TTL-cached per (database, schema, table, column, literal) so
  repeated questions don't re-hit the warehouse;
- degrades closed: any probe error simply yields no hint.

Opt-in (``wren_dimension_value_probe_enabled``, default off): each probe is a
real warehouse query.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from superset_ai_agent.persistence.ttl_cache import TtlCache

logger = logging.getLogger(__name__)

#: Quoted literals: '...' or "..." with at least one word character inside.
_LITERAL_RE = re.compile(r"""['"]([^'"]{2,64})['"]""")

#: Column types treated as string dimensions (probe-eligible).
_STRING_TYPE_RE = re.compile(r"CHAR|TEXT|STRING", re.IGNORECASE)

#: Distinct values returned per probe.
_PROBE_LIMIT = 5

#: Shared probe-result cache (module-level: graphs are request-scoped in
#: user-session mode, so an instance cache would never get a hit). 15 minutes —
#: dimension values drift slowly.
_probe_cache: TtlCache[tuple[Any, ...], list[str]] = TtlCache(ttl_seconds=900.0)


class _SqlExecutor(Protocol):
    def execute_sql(self, **kwargs: Any) -> Any: ...


def extract_quoted_literals(question: str) -> list[str]:
    """The distinct quoted literals in a question, in order of appearance."""

    seen: list[str] = []
    for match in _LITERAL_RE.finditer(question):
        literal = match.group(1).strip()
        if literal and literal not in seen:
            seen.append(literal)
    return seen


def _tokens(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {token for token in normalized.split() if token}


def candidate_columns(
    datasets: list[Any],
    question: str,
    *,
    max_columns: int,
) -> list[tuple[Any, Any]]:
    """String columns most likely to hold the question's quoted values.

    Ranks each dataset's string-typed columns by name-token overlap with the
    question (a "status"/"region" column matches a question that mentions it),
    ties broken by dataset order. Returns ``(dataset, column)`` pairs, capped.
    """

    q_tokens = _tokens(question)
    scored: list[tuple[int, int, tuple[Any, Any]]] = []
    for dataset_index, dataset in enumerate(datasets):
        for column in getattr(dataset, "columns", []) or []:
            col_type = str(getattr(column, "type", "") or "")
            if not _STRING_TYPE_RE.search(col_type):
                continue
            name = str(getattr(column, "name", "") or "")
            if not name:
                continue
            overlap = len(q_tokens & _tokens(name))
            scored.append((-overlap, dataset_index, (dataset, column)))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [pair for _, _, pair in scored[:max_columns]]


def _escape_literal(literal: str) -> str:
    return literal.replace("'", "''")


def _qualified_table(dataset: Any) -> str:
    schema = getattr(dataset, "schema_name", None) or getattr(dataset, "schema", None)
    table = getattr(dataset, "table_name", "")
    return f"{schema}.{table}" if schema else str(table)


def probe_sql(dataset: Any, column: Any, literal: str) -> str:
    """The bounded, read-only probe for stored values matching ``literal``."""

    name = getattr(column, "name", "")
    return (
        f"SELECT DISTINCT {name} FROM {_qualified_table(dataset)} "  # noqa: S608
        f"WHERE LOWER({name}) LIKE '%{_escape_literal(literal.lower())}%' "
        f"LIMIT {_PROBE_LIMIT}"
    )


def probe_dimension_values(
    *,
    question: str,
    datasets: list[Any],
    superset_client: _SqlExecutor,
    database_id: int,
    catalog_name: str | None,
    schema_name: str | None,
    max_queries: int,
) -> list[dict[str, Any]]:
    """Probe stored values for the question's quoted literals (bounded).

    Returns hint dicts ``{literal, table, column, values}`` for probes that
    found at least one matching stored value. ``[]`` when the question quotes
    nothing, no string columns exist, or every probe fails/misses — the
    channel never blocks or degrades the turn.
    """

    if max_queries <= 0:
        return []
    literals = extract_quoted_literals(question)
    if not literals:
        return []
    columns = candidate_columns(datasets, question, max_columns=max_queries)
    if not columns:
        return []
    hints: list[dict[str, Any]] = []
    budget = max_queries
    for literal in literals:
        for dataset, column in columns:
            if budget <= 0:
                return hints
            table = _qualified_table(dataset)
            cache_key = (
                database_id,
                catalog_name,
                table,
                getattr(column, "name", ""),
                literal.lower(),
            )
            values = _probe_cache.get(cache_key)
            if values is None:
                budget -= 1
                try:
                    result = superset_client.execute_sql(
                        database_id=database_id,
                        sql=probe_sql(dataset, column, literal),
                        catalog_name=catalog_name,
                        schema_name=schema_name,
                        limit=_PROBE_LIMIT,
                    )
                    values = [
                        str(next(iter(row.values())))
                        for row in getattr(result, "rows", []) or []
                        if row
                    ]
                except Exception as ex:  # pylint: disable=broad-except
                    logger.warning("Dimension-value probe failed (non-fatal): %s", ex)
                    values = []
                _probe_cache.set(cache_key, values)
            if values:
                hints.append(
                    {
                        "literal": literal,
                        "table": table,
                        "column": getattr(column, "name", ""),
                        "values": values,
                    }
                )
                break  # literal resolved; next literal
    return hints
