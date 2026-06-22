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

import pytest

from superset_ai_agent.semantic_layer.mdl_validator import validate_project_manifest
from superset_ai_agent.semantic_layer.wren_core_validator import (
    to_wren_core_manifest,
    validate_with_wren_core,
    wren_core_available,
)

# wren-core requires every column to carry a ``type``; a complete model
# round-trips through deep validation cleanly.
_VALID_MODEL = (
    "models:\n"
    "  - name: deals\n"
    "    table_reference:\n"
    "      table: deals\n"
    "    columns:\n"
    "      - name: stage\n"
    "        type: VARCHAR\n"
)

# Same model but a column is missing its required ``type`` — wren-core rejects it.
_INCOMPLETE_MODEL = (
    "models:\n"
    "  - name: deals\n"
    "    table_reference:\n"
    "      table: deals\n"
    "    columns:\n"
    "      - name: stage\n"
)


def test_to_wren_core_manifest_maps_snake_case_to_camel_case() -> None:
    manifest = to_wren_core_manifest(
        [
            {
                "name": "deals",
                "table_reference": {"schema": "sales", "table": "deals"},
                "columns": [
                    {"name": "stage", "type": "VARCHAR"},
                    {"name": "total", "is_calculated": True, "expression": "SUM(x)"},
                ],
            }
        ],
        [
            {
                "name": "deals_sites",
                "models": ["deals", "sites"],
                "join_type": "MANY_TO_ONE",
                "condition": "deals.site_id = sites.id",
            }
        ],
    )
    model = manifest["models"][0]
    assert model["tableReference"] == {"schema": "sales", "table": "deals"}
    assert model["columns"][1]["isCalculated"] is True
    assert manifest["relationships"][0]["joinType"] == "MANY_TO_ONE"


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


@pytest.mark.skipif(
    not wren_core_available(), reason="wren-core not installed"
)
def test_wren_core_rejects_incomplete_manifest() -> None:
    # A column missing its required ``type`` is rejected by the live engine.
    result = validate_project_manifest([_INCOMPLETE_MODEL], deep_validate=True)
    assert result.valid is False
    assert any(m.code == "wren_core_error" for m in result.messages)
