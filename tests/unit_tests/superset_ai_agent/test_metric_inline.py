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

"""Metric-expression inlining (wren_core has no top-level ``metrics`` concept)."""

from __future__ import annotations

from superset_ai_agent.semantic_layer.metric_inline import (
    build_metric_expression_map,
    inline_metrics,
)

_METRICS = {"total_revenue": "SUM(amount)", "avg_order": "AVG(amount)"}


def test_projection_metric_inlines_and_keeps_name_as_alias() -> None:
    result = inline_metrics("SELECT total_revenue FROM orders", metrics=_METRICS)
    assert result.sql == "SELECT SUM(amount) AS total_revenue FROM orders"
    assert result.inlined == ["total_revenue"]


def test_group_by_projection_inlines() -> None:
    result = inline_metrics(
        "SELECT region, total_revenue FROM orders GROUP BY region",
        metrics=_METRICS,
    )
    assert "SUM(amount) AS total_revenue" in result.sql
    assert "GROUP BY region" in result.sql


def test_author_alias_is_preserved() -> None:
    result = inline_metrics("SELECT total_revenue AS rev FROM orders", metrics=_METRICS)
    assert result.sql == "SELECT SUM(amount) AS rev FROM orders"


def test_qualified_reference_is_left_untouched() -> None:
    # ``o.total_revenue`` means a real column of ``orders``, not the metric.
    result = inline_metrics("SELECT o.total_revenue FROM orders o", metrics=_METRICS)
    assert result.inlined == []
    assert "total_revenue" in result.sql


def test_where_clause_reference_inlines_bare_expression() -> None:
    result = inline_metrics(
        "SELECT region FROM orders WHERE total_revenue > 5", metrics=_METRICS
    )
    assert "WHERE SUM(amount) > 5" in result.sql


def test_real_column_shadows_metric() -> None:
    result = inline_metrics(
        "SELECT total_revenue FROM orders",
        metrics=_METRICS,
        known_columns=frozenset({"total_revenue"}),
    )
    assert result.inlined == []
    assert result.sql == "SELECT total_revenue FROM orders"


def test_no_metric_reference_is_a_noop() -> None:
    sql = "SELECT SUM(amount) FROM orders"
    result = inline_metrics(sql, metrics=_METRICS)
    assert result.sql == sql
    assert result.inlined == []


def test_unparseable_sql_degrades_closed() -> None:
    result = inline_metrics("NOT SQL ((", metrics=_METRICS)
    assert result.sql == "NOT SQL (("
    assert result.inlined == []


def test_empty_metric_map_is_a_noop() -> None:
    result = inline_metrics("SELECT total_revenue FROM orders", metrics={})
    assert result.inlined == []


def test_case_insensitive_metric_match() -> None:
    result = inline_metrics("SELECT TOTAL_REVENUE FROM orders", metrics=_METRICS)
    assert "SUM(amount)" in result.sql
    assert result.inlined == ["TOTAL_REVENUE"]


def test_map_prefers_mdl_metric_over_dataset_metric() -> None:
    mapping = build_metric_expression_map(
        manifest_metrics=[{"name": "total_revenue", "expression": "SUM(amount)"}],
        dataset_metrics=[("total_revenue", "WRONG"), ("avg_order", "AVG(amount)")],
    )
    assert mapping["total_revenue"] == "SUM(amount)"
    assert mapping["avg_order"] == "AVG(amount)"


def test_map_reads_measure_array_expression() -> None:
    mapping = build_metric_expression_map(
        manifest_metrics=[
            {
                "name": "total_revenue",
                "baseObject": "orders",
                "measure": [{"name": "total_revenue", "expression": "SUM(amount)"}],
            }
        ],
    )
    assert mapping["total_revenue"] == "SUM(amount)"


def test_map_drops_metric_colliding_with_known_column() -> None:
    mapping = build_metric_expression_map(
        manifest_metrics=[{"name": "amount", "expression": "SUM(amount)"}],
        dataset_metrics=[("amount", "SUM(amount)")],
        known_columns=frozenset({"amount"}),
    )
    assert mapping == {}
