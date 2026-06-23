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

"""SemanticEngine seam — passthrough binding + wren-core degrade-closed."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.semantic_layer.engine import (
    create_semantic_engine,
    extract_referenced_tables,
    PassthroughEngine,
    resolve_dialect,
    WrenCoreEngine,
)
from superset_ai_agent.semantic_layer.engine.wren_core_engine import wren_core_available
from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
from superset_ai_agent.semantic_layer.schemas import MdlFile
from tests.unit_tests.superset_ai_agent.wren_core_markers import requires_wren_core

_MDL = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"schema": "sales", "table": "deals"},
                "columns": [{"name": "amount", "type": "DOUBLE"}],
            }
        ]
    }
)

_SQL = "SELECT amount FROM deals"


def _manifest():
    return compile_manifest(json_contents=[_MDL])


def test_resolve_dialect_maps_known_and_unknown_backends() -> None:
    assert resolve_dialect("postgresql") == "postgres"
    assert resolve_dialect("BigQuery") == "bigquery"
    assert resolve_dialect("exotic-db") is None
    assert resolve_dialect(None) is None


def test_extract_referenced_tables_handles_joins_and_bad_sql() -> None:
    tables = extract_referenced_tables(
        "SELECT * FROM sales.deals d JOIN customers c ON d.cid = c.id"
    )
    assert tables == ["customers", "deals"]
    assert extract_referenced_tables("not valid sql ;;;") == []


def test_factory_defaults_to_wren_core() -> None:
    # Wren's engine is enabled by default.
    assert isinstance(create_semantic_engine(AgentConfig()), WrenCoreEngine)


def test_factory_returns_passthrough_when_selected() -> None:
    assert isinstance(
        create_semantic_engine(AgentConfig(wren_engine="passthrough")),
        PassthroughEngine,
    )


def test_factory_returns_wren_core_when_selected() -> None:
    engine = create_semantic_engine(AgentConfig(wren_engine="wren_core"))
    assert isinstance(engine, WrenCoreEngine)


def test_passthrough_plan_sql_returns_sql_unchanged() -> None:
    engine = PassthroughEngine()
    planned = engine.plan_sql(_SQL, _manifest(), dialect="postgres")
    assert planned.native_sql == _SQL
    assert planned.rewritten is False
    assert planned.engine == "passthrough"
    assert planned.referenced_tables == ["deals"]
    assert any("passthrough" in w for w in planned.warnings)


def test_passthrough_compile_roundtrips_manifest() -> None:
    engine = PassthroughEngine()
    mdl_file = MdlFile(
        id="f1",
        project_id="p1",
        path="m.json",
        filename="m.json",
        content=_MDL,
        checksum="abc",
    )
    manifest = engine.compile([mdl_file])
    assert manifest.model_names == ["deals"]


def test_wren_core_degrades_to_passthrough_when_absent() -> None:
    if wren_core_available():
        pytest.skip("wren-core is installed; this asserts the absent-degrade path")
    engine = WrenCoreEngine()
    assert engine.is_available() is False
    planned = engine.plan_sql(_SQL, _manifest(), dialect="postgres")
    assert planned.native_sql == _SQL
    assert planned.rewritten is False
    assert any("not installed" in w for w in planned.warnings)


def test_wren_core_unknown_dialect_degrades(monkeypatch) -> None:
    engine = WrenCoreEngine()
    # Force availability so we reach the dialect check without the engine.
    monkeypatch.setattr(engine, "is_available", lambda: True)
    planned = engine.plan_sql(_SQL, _manifest(), dialect=None)
    assert planned.rewritten is False
    assert any("dialect" in w.lower() for w in planned.warnings)


@requires_wren_core
def test_wren_core_rewrites_model_to_physical_table() -> None:
    engine = WrenCoreEngine()
    planned = engine.plan_sql(_SQL, _manifest(), dialect="postgres")
    assert planned.engine == "wren_core"
    assert planned.rewritten is True
    # The logical model `deals` is expanded to the physical `sales.deals`.
    assert "sales.deals" in planned.native_sql


_CALC_MDL = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"schema": "sales", "table": "deals"},
                "columns": [
                    {"name": "amount", "type": "DOUBLE"},
                    {
                        "name": "margin",
                        "type": "DOUBLE",
                        "isCalculated": True,
                        "expression": "amount * 0.1",
                    },
                ],
            }
        ]
    }
)


@requires_wren_core
def test_wren_core_computes_calculated_column() -> None:
    engine = WrenCoreEngine()
    planned = engine.plan_sql(
        "SELECT margin FROM deals",
        compile_manifest(json_contents=[_CALC_MDL]),
        dialect="postgres",
    )
    # The engine — not the LLM — generates the calculated expression.
    assert "amount * 0.1" in planned.native_sql.replace(" ", " ")


# Parity litmus (wren_full.md Phase 1 Tests): a query joining two logical models
# is rewritten into native SQL whose physical tables + join the engine — not the
# LLM — materialized as CTEs.
_MULTI_MODEL_MDL = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"schema": "sales", "table": "deals"},
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "customer_id", "type": "BIGINT"},
                    {"name": "amount", "type": "DOUBLE"},
                ],
            },
            {
                "name": "Customers",
                "tableReference": {"schema": "sales", "table": "customers"},
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "region", "type": "VARCHAR"},
                ],
            },
        ]
    }
)


@requires_wren_core
def test_wren_core_rewrites_multi_model_join() -> None:
    engine = WrenCoreEngine()
    planned = engine.plan_sql(
        "SELECT c.region, SUM(d.amount) AS total FROM deals d "
        "JOIN Customers c ON d.customer_id = c.id GROUP BY c.region",
        compile_manifest(json_contents=[_MULTI_MODEL_MDL]),
        dialect="postgres",
    )
    assert planned.rewritten is True
    native = planned.native_sql.lower()
    # Both logical models expanded to their physical tables, joined natively.
    assert "sales.deals" in native
    assert "sales.customers" in native
    assert "join" in native
