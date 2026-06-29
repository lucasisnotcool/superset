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

"""Name-keyed structure-preserving merge (shared by enrichment + copilot patch)."""

from __future__ import annotations

from superset_ai_agent.semantic_layer.mdl_merge import (
    merge_columns_preserving_structure,
    merge_manifest_sections,
    merge_named,
)


def test_sparse_overlay_keeps_omitted_models() -> None:
    base = {"models": [{"name": "orders"}, {"name": "customers"}]}
    overlay = {"models": [{"name": "orders", "description": "Sales orders"}]}

    merged = merge_manifest_sections(base, overlay)

    names = [model["name"] for model in merged["models"]]
    assert names == ["orders", "customers"]  # untouched model survives, order kept
    assert merged["models"][0]["description"] == "Sales orders"


def test_column_merge_preserves_type_and_omitted_columns() -> None:
    base = {
        "models": [
            {
                "name": "orders",
                "tableReference": {"table": "orders"},
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "amount", "type": "DOUBLE"},
                ],
            }
        ]
    }
    # Overlay refines only `amount`'s description; omits `id`, omits `type`.
    overlay = {
        "models": [
            {"name": "orders", "columns": [{"name": "amount", "description": "USD"}]}
        ]
    }

    merged = merge_manifest_sections(base, overlay)
    columns = {col["name"]: col for col in merged["models"][0]["columns"]}

    assert set(columns) == {"id", "amount"}  # omitted column kept
    assert columns["amount"]["type"] == "DOUBLE"  # type never dropped (E4)
    assert columns["amount"]["description"] == "USD"
    # tableReference / physical mapping untouched by a sparse overlay.
    assert merged["models"][0]["tableReference"] == {"table": "orders"}


def test_properties_merge_is_additive() -> None:
    base = {
        "models": [
            {
                "name": "orders",
                "properties": {"displayName": "Orders", "superset_dataset_id": "7"},
                "columns": [
                    {"name": "rev", "type": "DOUBLE", "properties": {"alias": "Rev"}}
                ],
            }
        ]
    }
    overlay = {
        "models": [
            {
                "name": "orders",
                "properties": {"synonyms": ["sales"]},
                "columns": [{"name": "rev", "properties": {"synonyms": ["topline"]}}],
            }
        ]
    }

    merged = merge_manifest_sections(base, overlay)
    model = merged["models"][0]

    # Existing governance/provenance keys survive; new key added.
    assert model["properties"] == {
        "displayName": "Orders",
        "superset_dataset_id": "7",
        "synonyms": ["sales"],
    }
    assert model["columns"][0]["properties"] == {
        "alias": "Rev",
        "synonyms": ["topline"],
    }


def test_new_named_entity_appends() -> None:
    base = {"models": [{"name": "orders", "columns": [{"name": "id", "type": "INT"}]}]}
    overlay = {
        "models": [
            {
                "name": "orders",
                "columns": [{"name": "tax", "type": "DOUBLE", "isCalculated": True}],
            }
        ]
    }

    merged = merge_manifest_sections(base, overlay)
    columns = [col["name"] for col in merged["models"][0]["columns"]]

    assert columns == ["id", "tax"]  # genuinely-new column appended after existing


def test_relationships_section_replaces_by_name() -> None:
    base = {"relationships": [{"name": "o_to_c", "joinType": "MANY_TO_ONE"}]}
    overlay = {
        "relationships": [
            {"name": "o_to_c", "joinType": "ONE_TO_MANY"},  # replace colliding
            {"name": "o_to_p", "joinType": "MANY_TO_ONE"},  # append new
        ]
    }

    merged = merge_manifest_sections(base, overlay)
    rels = {rel["name"]: rel["joinType"] for rel in merged["relationships"]}

    assert rels == {"o_to_c": "ONE_TO_MANY", "o_to_p": "MANY_TO_ONE"}


def test_envelope_keys_preserved() -> None:
    base = {"catalog": "wren", "schema": "public", "models": [{"name": "x"}]}
    overlay = {"models": [{"name": "x", "description": "d"}]}

    merged = merge_manifest_sections(base, overlay)

    assert merged["catalog"] == "wren"
    assert merged["schema"] == "public"


def test_merge_named_default_replaces_without_merge_entry() -> None:
    base = [{"name": "a", "v": 1}, {"name": "b", "v": 2}]
    overlay = [{"name": "a", "v": 9}]

    result = merge_named(base, overlay)

    assert result == [{"name": "a", "v": 9}, {"name": "b", "v": 2}]


def test_merge_columns_ignores_non_dict_entries() -> None:
    # Defensive: malformed entries are skipped, not crashed on.
    result = merge_columns_preserving_structure(
        [{"name": "id", "type": "INT"}, "junk"], [{"name": "id", "description": "pk"}]
    )
    assert result == [{"name": "id", "type": "INT", "description": "pk"}]
