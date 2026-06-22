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

"""Phase 0.3 — canonical MDL compile (snake_case YAML -> camelCase manifest)."""

from __future__ import annotations

import base64
import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.semantic_layer.mdl_compile import (
    compile_manifest,
    CompiledManifest,
)
from superset_ai_agent.semantic_layer.wren_core_validator import to_wren_core_manifest

_DEALS_YAML = """
models:
  - name: deals
    table_reference:
      schema: sales
      table: deals
    primary_key: id
    columns:
      - name: id
        type: BIGINT
      - name: amount
        type: DOUBLE
      - name: net_amount
        type: DOUBLE
        is_calculated: true
        expression: amount * 0.9
relationships:
  - name: deal_customer
    models: [deals, customers]
    join_type: MANY_TO_ONE
    condition: deals.customer_id = customers.id
views:
  - name: top_deals
    statement: SELECT * FROM deals ORDER BY amount DESC
"""

_CUSTOMERS_YAML = """
models:
  - name: customers
    table_reference:
      schema: sales
      table: customers
    columns:
      - name: id
        type: BIGINT
"""


def test_compile_manifest_maps_snake_case_to_camel_case() -> None:
    manifest = compile_manifest(yaml_contents=[_DEALS_YAML])
    model = manifest.models[0]
    assert model["name"] == "deals"
    assert model["tableReference"] == {"schema": "sales", "table": "deals"}
    assert model["primaryKey"] == "id"
    # Calculated column carries camelCase isCalculated + expression.
    net = next(col for col in model["columns"] if col["name"] == "net_amount")
    assert net["isCalculated"] is True
    assert net["expression"] == "amount * 0.9"
    assert manifest.relationships[0]["joinType"] == "MANY_TO_ONE"
    assert manifest.views[0]["name"] == "top_deals"


def test_compile_manifest_merges_multiple_files_in_order() -> None:
    manifest = compile_manifest(yaml_contents=[_DEALS_YAML, _CUSTOMERS_YAML])
    assert manifest.model_names == ["deals", "customers"]


def test_to_engine_manifest_and_base64_roundtrip() -> None:
    manifest = compile_manifest(
        yaml_contents=[_DEALS_YAML], catalog="wren", schema="public"
    )
    engine = manifest.to_engine_manifest()
    assert engine["catalog"] == "wren"
    assert engine["schema"] == "public"
    assert "relationships" in engine
    assert "views" in engine
    decoded = json.loads(base64.b64decode(manifest.to_base64_json()))
    assert decoded == engine


def test_invalid_yaml_is_skipped_not_raised() -> None:
    manifest = compile_manifest(yaml_contents=["{ this: : is not yaml"])
    assert isinstance(manifest, CompiledManifest)
    assert manifest.models == []


def test_to_wren_core_manifest_delegates_to_shared_mapping() -> None:
    """The deep-validation manifest must use the same snake->camel mapping."""

    compiled = compile_manifest(yaml_contents=[_DEALS_YAML])
    shared = to_wren_core_manifest(
        [
            {
                "name": "deals",
                "table_reference": {"schema": "sales", "table": "deals"},
                "primary_key": "id",
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "amount", "type": "DOUBLE"},
                    {
                        "name": "net_amount",
                        "type": "DOUBLE",
                        "is_calculated": True,
                        "expression": "amount * 0.9",
                    },
                ],
            }
        ],
        [
            {
                "name": "deal_customer",
                "models": ["deals", "customers"],
                "join_type": "MANY_TO_ONE",
                "condition": "deals.customer_id = customers.id",
            }
        ],
    )
    assert shared["models"][0]["tableReference"] == {
        "schema": "sales",
        "table": "deals",
    }
    assert shared["models"][0] == compiled.models[0]
    assert shared["relationships"][0] == compiled.relationships[0]
