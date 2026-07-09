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

"""Inline MDL/Superset metric references into their measure expressions.

The installed engine (``wren_core`` 0.7.1) has **no top-level ``metrics``
concept** — it silently drops the manifest ``metrics`` key, so a metric name is
never a resolvable field. A draft like ``SELECT total_revenue FROM orders`` is
rejected by ``transform_sql`` (``No field named total_revenue``) and, on the
degraded passthrough path, reaches the physical DB verbatim → Oracle ORA-00904.

The only aggregation object wren_core rewrites from raw SQL is a model column /
calculated column; a metric must therefore be **inlined as its expression**
(``SELECT SUM(amount) AS total_revenue FROM orders``), which the engine plans
cleanly. This module performs that substitution on the drafted semantic SQL
before it reaches the engine — the metric-translation layer wren_core does not
provide. See ``plan_metric_semantic_translation_impl.md``.

Degrade closed: any parse failure returns the SQL unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class InlineResult:
    """The rewritten SQL and the metric names that were inlined."""

    sql: str
    inlined: list[str] = field(default_factory=list)


def inline_metrics(
    sql: str,
    *,
    metrics: Mapping[str, str],
    known_columns: frozenset[str] | None = None,
) -> InlineResult:
    """Replace bare metric references in ``sql`` with their measure expressions.

    ``metrics`` maps a metric name to its SQL expression. Matching is
    case-insensitive on the metric name. Substitution rules (a physical column
    always wins over a metric of the same name):

    - **Projection** (``SELECT metric``): replaced with ``(<expr>) AS metric`` so
      the output column keeps the metric's name; an existing alias is preserved.
    - **Elsewhere** (WHERE/HAVING/ORDER BY): the bare expression is substituted.
    - **Qualified** references (``t.metric``) are left untouched — a qualifier
      means the author meant a real column of that table.
    - Any name in ``known_columns`` is skipped (real column shadows the metric).

    Returns the SQL unchanged (and ``inlined=[]``) when there is nothing to do or
    the SQL cannot be parsed.
    """

    if not metrics or not sql or not sql.strip():
        return InlineResult(sql=sql, inlined=[])

    lookup = {
        name.lower(): expr
        for name, expr in metrics.items()
        if name and expr and name.strip() and expr.strip()
    }
    if known_columns:
        shadowed = {col.lower() for col in known_columns}
        lookup = {name: expr for name, expr in lookup.items() if name not in shadowed}
    if not lookup:
        return InlineResult(sql=sql, inlined=[])

    try:
        root = sqlglot.parse_one(sql)
    except Exception:  # pylint: disable=broad-except  # sqlglot.ParseError et al.
        return InlineResult(sql=sql, inlined=[])

    inlined: set[str] = set()
    # Pass 1: projections keep the metric name as the output alias.
    _inline_projections(root, lookup, inlined)
    # Pass 2: remaining bare metric references anywhere else become the raw expr.
    root = root.transform(lambda node: _inline_bare(node, lookup, inlined))

    if not inlined:
        return InlineResult(sql=sql, inlined=[])
    return InlineResult(sql=root.sql(), inlined=sorted(inlined))


def _metric_replacement(
    column: exp.Column, lookup: Mapping[str, str]
) -> exp.Expression | None:
    """The parsed expression for a bare metric column, or ``None``.

    Qualified references (``t.metric``) are real columns, not metrics.
    """

    if column.table:
        return None
    expr_sql = lookup.get(column.name.lower())
    if expr_sql is None:
        return None
    try:
        return sqlglot.parse_one(expr_sql)
    except Exception:  # pylint: disable=broad-except
        return None


def _inline_projections(
    root: exp.Expression, lookup: Mapping[str, str], inlined: set[str]
) -> None:
    """Replace bare-metric projections with ``(<expr>) AS <name>`` in place."""

    for select in root.find_all(exp.Select):
        new_expressions: list[exp.Expression] = []
        for projection in select.expressions:
            target = (
                projection.this if isinstance(projection, exp.Alias) else projection
            )
            replacement = (
                _metric_replacement(target, lookup)
                if isinstance(target, exp.Column)
                else None
            )
            if replacement is None:
                new_expressions.append(projection)
                continue
            inlined.add(target.name)
            if isinstance(projection, exp.Alias):
                # ``metric AS x`` -> ``(<expr>) AS x`` (author-chosen alias)
                projection.set("this", replacement)
                new_expressions.append(projection)
            else:
                new_expressions.append(exp.alias_(replacement, target.name))
        select.set("expressions", new_expressions)


def _inline_bare(
    node: exp.Expression, lookup: Mapping[str, str], inlined: set[str]
) -> exp.Expression:
    """Replace a bare-metric column node anywhere (WHERE/HAVING/ORDER BY)."""

    if isinstance(node, exp.Column):
        replacement = _metric_replacement(node, lookup)
        if replacement is not None:
            inlined.add(node.name)
            return replacement
    return node


def build_metric_expression_map(
    *,
    manifest_metrics: list[dict[str, Any]] | None = None,
    dataset_metrics: list[tuple[str, str | None]] | None = None,
    known_columns: frozenset[str] | None = None,
) -> dict[str, str]:
    """Collect a name->expression map from MDL and Superset metric sources.

    MDL manifest metrics win over Superset dataset metrics on a name clash. A
    metric whose name collides with a known physical column is dropped (the
    column is authoritative). MDL metrics tolerate a bare ``expression`` or a
    ``measure``/``measures`` array whose first entry carries the expression.
    """

    shadowed = {col.lower() for col in (known_columns or frozenset())}
    result: dict[str, str] = {}

    for name, expression in dataset_metrics or []:
        if name and expression and name.strip() and expression.strip():
            if name.lower() in shadowed:
                continue
            result[name.lower()] = expression

    for metric in manifest_metrics or []:
        if not isinstance(metric, dict):
            continue
        name = str(metric.get("name") or "").strip()
        if not name or name.lower() in shadowed:
            continue
        expression = _manifest_metric_expression(metric)
        if expression:
            result[name.lower()] = expression

    # Re-key on the original metric names so the caller can present them; the
    # inliner lower-cases internally, so lower-cased keys are sufficient.
    return result


def _manifest_metric_expression(metric: dict[str, Any]) -> str | None:
    """The SQL expression for an MDL metric dict (expression or first measure)."""

    expression = metric.get("expression")
    if isinstance(expression, str) and expression.strip():
        return expression.strip()
    measures = metric.get("measure")
    if measures is None:
        measures = metric.get("measures")
    if isinstance(measures, list):
        for measure in measures:
            if isinstance(measure, dict):
                measure_expr = measure.get("expression")
                if isinstance(measure_expr, str) and measure_expr.strip():
                    return measure_expr.strip()
    return None
