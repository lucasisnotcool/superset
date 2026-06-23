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

"""D7 — golden contract pinning the native MDL shape to the installed wren-core.

MDL is authored, stored, and validated in wren-core's native manifest shape, with
no translation layer. That makes the authored shape *itself* the engine contract,
so it must be anchored by an executable test against the installed wheel rather
than by a hand-maintained mapping. These tests are the single source of truth for
"what wren-core accepts"; a wheel bump that changes the shape fails here loudly
instead of silently at activation time.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

import pytest

from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
from tests.unit_tests.superset_ai_agent.wren_core_markers import requires_wren_core

# A representative native manifest: physical model with typed columns + a second
# model joined by an explicit relationship (camelCase joinType / tableReference).
NATIVE_MANIFEST = {
    "models": [
        {
            "name": "orders",
            "tableReference": {"schema": "public", "table": "orders"},
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "customer_id", "type": "integer"},
                {"name": "amount", "type": "double"},
            ],
            "primaryKey": "id",
        },
        {
            "name": "customers",
            "tableReference": {"schema": "public", "table": "customers"},
            "columns": [
                {"name": "id", "type": "integer"},
                {"name": "region", "type": "varchar"},
            ],
            "primaryKey": "id",
        },
    ],
    "relationships": [
        {
            "name": "orders_customers",
            "models": ["orders", "customers"],
            "joinType": "MANY_TO_ONE",
            "condition": "orders.customer_id = customers.id",
        }
    ],
}


@requires_wren_core
def test_native_manifest_loads_and_rewrites() -> None:
    """compile_manifest output loads into wren-core and rewrites SQL."""

    from wren_core import SessionContext

    compiled = compile_manifest(json_contents=[json.dumps(NATIVE_MANIFEST)])
    ctx = SessionContext(compiled.to_base64_json())
    rewritten = ctx.transform_sql("SELECT id, amount FROM orders")

    assert isinstance(rewritten, str)
    assert rewritten
    # The semantic engine rewrites the logical model to the physical table.
    assert "orders" in rewritten.lower()


@requires_wren_core
def test_native_explicit_join_rewrites() -> None:
    """An explicit multi-model join over a relationship rewrites natively."""

    from wren_core import SessionContext

    compiled = compile_manifest(json_contents=[json.dumps(NATIVE_MANIFEST)])
    ctx = SessionContext(compiled.to_base64_json())
    rewritten = ctx.transform_sql(
        "SELECT c.region, o.amount FROM orders o "
        "JOIN customers c ON o.customer_id = c.id"
    )

    assert isinstance(rewritten, str)
    assert rewritten
    assert "customers" in rewritten.lower()


@requires_wren_core
def test_missing_column_type_is_rejected_by_engine() -> None:
    """A column without `type` is rejected — pins the production failure mode.

    This is the exact error observed in the field (`missing field 'type'`): the
    contract test makes the engine's hard requirement explicit so the validation
    layers that guard against it cannot be quietly removed.
    """

    from wren_core import SessionContext

    broken = json.loads(json.dumps(NATIVE_MANIFEST))
    del broken["models"][0]["columns"][0]["type"]
    compiled = compile_manifest(json_contents=[json.dumps(broken)])

    with pytest.raises(Exception, match="type"):
        SessionContext(compiled.to_base64_json())


# A metric and a cube in wren-core's native shape. Metrics are authored by the
# agent; cubes are not, but both are pinned here so the structural validator's
# shape (F4) stays in lockstep with what the engine actually accepts.
_METRIC = {
    "name": "total_amount",
    "baseObject": "orders",
    "expression": "sum(amount)",
}
_CUBE = {
    "name": "sales",
    "baseObject": "orders",
    "measures": [{"name": "amt", "type": "double", "expression": "sum(amount)"}],
    "dimensions": [{"name": "region", "type": "varchar", "expression": "region"}],
}


@requires_wren_core
def test_native_metric_loads_into_engine() -> None:
    """A metric in {name, baseObject, expression} shape loads into wren-core."""

    from wren_core import SessionContext

    manifest = json.loads(json.dumps(NATIVE_MANIFEST))
    manifest["metrics"] = [_METRIC]
    compiled = compile_manifest(json_contents=[json.dumps(manifest)])

    # Constructing the context loads + validates the manifest; no raise == valid.
    SessionContext(compiled.to_base64_json())


@requires_wren_core
def test_native_cube_loads_into_engine() -> None:
    """A cube with baseObject + {name,type,expression} entries loads into wren-core.

    Pins the F4 cube shape: ``cube_without_base`` / ``cube_measure_without_type`` /
    ``cube_entry_without_expression`` exist because the engine *requires* those
    fields — this proves the well-formed shape is accepted.
    """

    from wren_core import SessionContext

    manifest = json.loads(json.dumps(NATIVE_MANIFEST))
    manifest["cubes"] = [_CUBE]
    compiled = compile_manifest(json_contents=[json.dumps(manifest)])

    SessionContext(compiled.to_base64_json())


@requires_wren_core
def test_cube_missing_measure_type_is_rejected_by_engine() -> None:
    """A cube measure without `type` is rejected — pins the F4 structural error."""

    from wren_core import SessionContext

    manifest = json.loads(json.dumps(NATIVE_MANIFEST))
    broken_cube = json.loads(json.dumps(_CUBE))
    del broken_cube["measures"][0]["type"]
    manifest["cubes"] = [broken_cube]
    compiled = compile_manifest(json_contents=[json.dumps(manifest)])

    with pytest.raises(Exception, match="type"):
        SessionContext(compiled.to_base64_json())
