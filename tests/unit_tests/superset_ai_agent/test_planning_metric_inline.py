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

"""Metric inlining wired into the shared semantic-planning step.

The engine (wren_core 0.7.1) drops the manifest ``metrics`` key, so a metric
reference must be inlined to its expression before the engine plans the SQL —
otherwise the bare metric name reaches the DB and Oracle raises ORA-00904.
"""

from __future__ import annotations

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.semantic_layer.engine.base import PlannedSql
from superset_ai_agent.semantic_layer.engine.planning import plan_semantic_sql_step
from superset_ai_agent.semantic_layer.mdl_compile import CompiledManifest


class _EchoEngine:
    """SemanticEngine stand-in: records the SQL it is asked to plan, echoes it."""

    name = "echo"

    def __init__(self, manifest: CompiledManifest) -> None:
        self._manifest = manifest
        self.received_sql: str | None = None

    def is_available(self) -> bool:
        return True

    def compile(self, mdl_files):
        return self._manifest

    def validate(self, manifest, *, deep=False, schema_index=None):
        raise NotImplementedError

    def plan_sql(self, semantic_sql, manifest, *, dialect=None) -> PlannedSql:
        self.received_sql = semantic_sql
        return PlannedSql(native_sql=semantic_sql, engine=self.name, rewritten=False)


def _context(*, metrics: list[MetricSummary] | None = None) -> AgentContext:
    dataset = DatasetMetadata(
        id=1,
        table_name="orders",
        schema_name="public",
        database_id=1,
        columns=[ColumnSummary(name="amount", type="DOUBLE")],
        metrics=metrics or [],
    )
    return AgentContext(
        database=DatabaseSummary(id=1, name="db", backend="oracle"),
        datasets=[dataset],
    )


def _manifest(*, metrics: list[dict] | None = None) -> CompiledManifest:
    return CompiledManifest(
        models=[{"name": "orders", "columns": [{"name": "amount", "type": "DOUBLE"}]}],
        metrics=metrics or [],
    )


def _plan(engine: _EchoEngine, context: AgentContext, sql: str):
    return plan_semantic_sql_step(
        engine,
        sql=sql,
        context=context,
        owner_id="o",
        project_id=None,
        mdl_file_store=None,
        finalize_enabled=False,
    )


def test_mdl_metric_is_inlined_before_the_engine_sees_it() -> None:
    manifest = _manifest(
        metrics=[{"name": "total_revenue", "expression": "SUM(amount)"}]
    )
    engine = _EchoEngine(manifest)
    result = _plan(engine, _context(), "SELECT total_revenue FROM orders")

    # The engine received the inlined expression, never the bare metric name.
    assert engine.received_sql == "SELECT SUM(amount) AS total_revenue FROM orders"
    assert result.inlined_metrics == ["total_revenue"]
    assert result.semantic_sql == "SELECT total_revenue FROM orders"
    assert result.native_sql == "SELECT SUM(amount) AS total_revenue FROM orders"


def test_superset_dataset_metric_is_inlined() -> None:
    engine = _EchoEngine(_manifest())
    context = _context(
        metrics=[MetricSummary(name="avg_order", expression="AVG(amount)")]
    )
    result = _plan(engine, context, "SELECT avg_order FROM orders")

    assert engine.received_sql == "SELECT AVG(amount) AS avg_order FROM orders"
    assert result.inlined_metrics == ["avg_order"]


def test_metric_colliding_with_real_column_is_not_inlined() -> None:
    # ``amount`` is a physical column; a metric of the same name must not win.
    manifest = _manifest(metrics=[{"name": "amount", "expression": "SUM(amount)"}])
    engine = _EchoEngine(manifest)
    result = _plan(engine, _context(), "SELECT amount FROM orders")

    assert engine.received_sql == "SELECT amount FROM orders"
    assert result.inlined_metrics == []


def test_non_metric_draft_is_unchanged() -> None:
    engine = _EchoEngine(
        _manifest(metrics=[{"name": "total_revenue", "expression": "SUM(amount)"}])
    )
    result = _plan(engine, _context(), "SELECT amount FROM orders")

    assert engine.received_sql == "SELECT amount FROM orders"
    assert result.inlined_metrics == []
    assert result.rewritten is False
