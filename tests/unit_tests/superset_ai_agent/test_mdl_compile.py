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

"""MDL compile — merge native JSON files into one engine manifest (no mapping)."""

from __future__ import annotations

import base64
import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.semantic_layer.mdl_compile import (
    compile_manifest,
    CompiledManifest,
)
from superset_ai_agent.semantic_layer.wren_core_validator import to_wren_core_manifest

_DEALS_JSON = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"schema": "sales", "table": "deals"},
                "primaryKey": "id",
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "amount", "type": "DOUBLE"},
                    {
                        "name": "net_amount",
                        "type": "DOUBLE",
                        "isCalculated": True,
                        "expression": "amount * 0.9",
                    },
                ],
            }
        ],
        "relationships": [
            {
                "name": "deal_customer",
                "models": ["deals", "customers"],
                "joinType": "MANY_TO_ONE",
                "condition": "deals.customer_id = customers.id",
            }
        ],
        "views": [
            {"name": "top_deals", "statement": "SELECT * FROM deals ORDER BY amount"}
        ],
    }
)

_CUSTOMERS_JSON = json.dumps(
    {
        "models": [
            {
                "name": "customers",
                "tableReference": {"schema": "sales", "table": "customers"},
                "columns": [{"name": "id", "type": "BIGINT"}],
            }
        ]
    }
)


def test_compile_manifest_passes_native_entities_through() -> None:
    manifest = compile_manifest(json_contents=[_DEALS_JSON])
    model = manifest.models[0]
    assert model["name"] == "deals"
    # Native camelCase is preserved verbatim — no translation.
    assert model["tableReference"] == {"schema": "sales", "table": "deals"}
    assert model["primaryKey"] == "id"
    net = next(col for col in model["columns"] if col["name"] == "net_amount")
    assert net["isCalculated"] is True
    assert net["expression"] == "amount * 0.9"
    assert manifest.relationships[0]["joinType"] == "MANY_TO_ONE"
    assert manifest.views[0]["name"] == "top_deals"


def test_compile_manifest_merges_multiple_files_in_order() -> None:
    manifest = compile_manifest(json_contents=[_DEALS_JSON, _CUSTOMERS_JSON])
    assert manifest.model_names == ["deals", "customers"]


def test_to_engine_manifest_and_base64_roundtrip() -> None:
    manifest = compile_manifest(
        json_contents=[_DEALS_JSON], catalog="wren", schema="public"
    )
    engine = manifest.to_engine_manifest()
    assert engine["catalog"] == "wren"
    assert engine["schema"] == "public"
    assert "relationships" in engine
    assert "views" in engine
    decoded = json.loads(base64.b64decode(manifest.to_base64_json()))
    assert decoded == engine


def test_invalid_json_is_skipped_not_raised() -> None:
    manifest = compile_manifest(json_contents=["{ this is not json"])
    assert isinstance(manifest, CompiledManifest)
    assert manifest.models == []


def test_to_wren_core_manifest_wraps_native_entities() -> None:
    """Deep-validation manifest wraps native entities in the envelope, no mapping."""

    compiled = compile_manifest(json_contents=[_DEALS_JSON])
    shared = to_wren_core_manifest(compiled.models, compiled.relationships)
    assert shared["models"][0]["tableReference"] == {
        "schema": "sales",
        "table": "deals",
    }
    assert shared["models"][0] == compiled.models[0]
    assert shared["relationships"][0] == compiled.relationships[0]


_CUBES_JSON = json.dumps(
    {
        "models": [
            {
                "name": "sales",
                "tableReference": {"schema": "public", "table": "sales"},
                "columns": [{"name": "amount", "type": "DOUBLE"}],
            }
        ],
        "metrics": [
            {"name": "total_amount", "baseObject": "sales", "expression": "SUM(amount)"}
        ],
        "cubes": [
            {
                "name": "sales_cube",
                "measures": [{"name": "total", "expression": "SUM(amount)"}],
                "dimensions": [{"name": "region"}],
                "timeDimensions": [{"name": "order_date"}],
                "hierarchies": [{"name": "geo"}],
            }
        ],
    }
)


def test_compile_manifest_carries_metrics_and_cubes() -> None:
    manifest = compile_manifest(json_contents=[_CUBES_JSON])
    assert manifest.metrics[0]["name"] == "total_amount"
    assert manifest.metrics[0]["baseObject"] == "sales"
    cube = manifest.cubes[0]
    assert cube["name"] == "sales_cube"
    assert cube["timeDimensions"][0]["name"] == "order_date"
    engine = manifest.to_engine_manifest()
    assert "metrics" in engine
    assert "cubes" in engine
