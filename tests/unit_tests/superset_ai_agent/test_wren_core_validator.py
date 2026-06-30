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

import json  # noqa: TID251 - standalone agent JSON contract

import pytest

from superset_ai_agent.semantic_layer.mdl_validator import validate_project_manifest
from superset_ai_agent.semantic_layer.wren_core_validator import (
    _friendly_engine_error,
    to_wren_core_manifest,
    validate_with_wren_core,
    wren_core_available,
)
from tests.unit_tests.superset_ai_agent.wren_core_markers import requires_wren_core


def test_friendly_engine_error_maps_missing_type() -> None:
    msg = _friendly_engine_error("missing field `type` at line 1 column 4109")
    assert msg.code == "wren_core_missing_field"
    assert "type" in msg.message
    assert "column" in msg.message.lower()


def test_friendly_engine_error_maps_unknown_variant() -> None:
    msg = _friendly_engine_error("unknown variant `SIDEWAYS`, expected one of ...")
    assert msg.code == "wren_core_unknown_variant"
    assert "SIDEWAYS" in msg.message


def test_friendly_engine_error_passes_through_unknown_shape() -> None:
    msg = _friendly_engine_error("some other engine failure")
    assert msg.code == "wren_core_error"
    assert "some other engine failure" in msg.message

# wren-core requires every column to carry a ``type``; a complete model
# round-trips through deep validation cleanly.
_VALID_MODEL = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"table": "deals"},
                "columns": [{"name": "stage", "type": "VARCHAR"}],
            }
        ]
    }
)

# Same model but a column is missing its required ``type`` — wren-core rejects it.
_INCOMPLETE_MODEL = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"table": "deals"},
                "columns": [{"name": "stage"}],
            }
        ]
    }
)


def test_to_wren_core_manifest_wraps_native_entities() -> None:
    manifest = to_wren_core_manifest(
        [
            {
                "name": "deals",
                "tableReference": {"schema": "sales", "table": "deals"},
                "columns": [
                    {"name": "stage", "type": "VARCHAR"},
                    {"name": "total", "isCalculated": True, "expression": "SUM(x)"},
                ],
            }
        ],
        [
            {
                "name": "deals_sites",
                "models": ["deals", "sites"],
                "joinType": "MANY_TO_ONE",
                "condition": "deals.site_id = sites.id",
            }
        ],
    )
    model = manifest["models"][0]
    # Pass-through: native entities are placed in the envelope unchanged.
    assert model["tableReference"] == {"schema": "sales", "table": "deals"}
    assert model["columns"][1]["isCalculated"] is True
    assert manifest["relationships"][0]["joinType"] == "MANY_TO_ONE"


def test_to_wren_core_manifest_includes_views_only_when_present() -> None:
    # Views reach the engine so their statements are resolved against models.
    with_views = to_wren_core_manifest(
        [{"name": "deals", "columns": []}],
        [],
        [{"name": "big_deals", "statement": "SELECT 1 FROM deals"}],
    )
    assert with_views["views"][0]["name"] == "big_deals"

    # Absent/empty views keep the envelope minimal (no behavior change for the
    # models+relationships-only callers).
    assert "views" not in to_wren_core_manifest([], [])
    assert "views" not in to_wren_core_manifest([], [], [])


def test_validate_with_wren_core_no_op_when_unavailable() -> None:
    if wren_core_available():
        pytest.skip("wren-core is installed; this asserts the fallback path")
    result = validate_with_wren_core([{"name": "deals", "columns": []}], [])
    assert result.valid is True
    assert any(m.code == "wren_core_unavailable" for m in result.messages)


def test_deep_validate_passes_complete_manifest() -> None:
    # A complete manifest is valid whether or not wren-core is installed
    # (no-op valid when absent; engine-accepted when present).
    result = validate_project_manifest([_VALID_MODEL], deep_validate=True)
    assert result.valid is True


def test_incomplete_manifest_is_caught_structurally() -> None:
    # W5: a column missing its required ``type`` is now caught by the structural
    # validator (with a readable message) before deep validation — no wren-core
    # needed, no opaque serde offset.
    result = validate_project_manifest([_INCOMPLETE_MODEL])
    assert result.valid is False
    assert any(m.code == "column_without_type" for m in result.messages)


@requires_wren_core
def test_wren_core_missing_field_is_mapped_friendly() -> None:
    # If a typeless column reaches the engine (deep validate), the serde error is
    # translated to a field-anchored message rather than a byte offset.
    result = validate_project_manifest([_INCOMPLETE_MODEL], deep_validate=True)
    assert result.valid is False
    assert any(
        m.code in {"column_without_type", "wren_core_missing_field"}
        for m in result.messages
    )


# --- Views in deep validation (G2 Layer A) -------------------------------------
# wren-core loads a manifest eagerly: a view whose statement references an unknown
# column or model fails at load, so deep validation catches a bad view at the
# activation gate instead of at query time. These pin that contract against the
# installed wheel.

_ORDERS_MODEL = {
    "name": "orders",
    "tableReference": {"schema": "public", "table": "orders"},
    "columns": [
        {"name": "id", "type": "integer"},
        {"name": "customer_id", "type": "integer"},
        {"name": "amount", "type": "double"},
    ],
    "primaryKey": "id",
}
# A second model in a *different* physical schema — the cross-schema regression
# guard (spec §5.6): a view joining the two must validate clean.
_CUSTOMERS_MODEL = {
    "name": "customers",
    "tableReference": {"schema": "analytics", "table": "customers"},
    "columns": [
        {"name": "id", "type": "integer"},
        {"name": "region", "type": "varchar"},
    ],
    "primaryKey": "id",
}


def _project(*, view: dict) -> str:
    return json.dumps(
        {
            "models": [_ORDERS_MODEL, _CUSTOMERS_MODEL],
            "relationships": [
                {
                    "name": "orders_customers",
                    "models": ["orders", "customers"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "orders.customer_id = customers.id",
                }
            ],
            "views": [view],
        }
    )


@requires_wren_core
def test_deep_validation_accepts_valid_semantic_view() -> None:
    content = _project(
        view={
            "name": "big_orders",
            "statement": "SELECT id, amount FROM orders WHERE amount > 100",
        }
    )
    result = validate_project_manifest([content], deep_validate=True)
    assert result.valid is True


@requires_wren_core
def test_deep_validation_rejects_view_with_unknown_column() -> None:
    content = _project(
        view={
            "name": "bad_view",
            "statement": "SELECT id, nonexistent_col FROM orders",
        }
    )
    result = validate_project_manifest([content], deep_validate=True)
    assert result.valid is False
    assert any(m.code.startswith("wren_core") for m in result.messages)


@requires_wren_core
def test_deep_validation_accepts_cross_schema_view() -> None:
    # The view joins two models whose tableReference.schema differ; cross-schema
    # correctness is inherited from the model layer (spec §5.6).
    content = _project(
        view={
            "name": "order_regions",
            "statement": (
                "SELECT o.amount, c.region FROM orders o "
                "JOIN customers c ON o.customer_id = c.id"
            ),
        }
    )
    result = validate_project_manifest([content], deep_validate=True)
    assert result.valid is True


@requires_wren_core
def test_native_view_does_not_poison_deep_validation() -> None:
    # A native view (carries ``dialect``, references a physical table that is not a
    # model) is filtered out before the engine load, so it never breaks the
    # manifest for the real models. R9 guard.
    content = _project(
        view={
            "name": "native_view",
            "dialect": "postgres",
            "statement": "SELECT * FROM public.raw_external_table",
        }
    )
    result = validate_project_manifest([content], deep_validate=True)
    assert result.valid is True
