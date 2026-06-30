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
"""Offline unit tests for the v3 eval helpers (views + golden queries).

These exercise the pure parsing/metric functions with no live server.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset

import eval_v3 as ev3

# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
_SEMANTIC_VIEW = {
    "name": "warm_line_output_by_family",
    "statement": (
        "SELECT sku.sku_family, SUM(ev.units_completed) AS plated "
        "FROM seagate_drive_skus sku "
        "JOIN seagate_work_orders wo ON wo.sku_id = sku.sku_id "
        "JOIN seagate_production_events ev ON ev.work_order_id = wo.work_order_id "
        "GROUP BY sku.sku_family"
    ),
    "properties": {"description": "Warm-line plated output by SKU family."},
}
_NATIVE_VIEW = {
    "name": "legacy_yield",
    "statement": "SELECT * FROM seagate_ops.seagate_production_events",
    "dialect": "postgres",
    "properties": {},
}
_PHYSICAL_SEMANTIC_VIEW = {
    "name": "leaky",
    "statement": "SELECT * FROM seagate_ops.seagate_work_orders wo",
    "properties": {"description": "x"},
}


def test_file_views_parses_string_and_dict_content():
    as_str = {"content": json.dumps({"views": [_SEMANTIC_VIEW]}), "status": "active"}
    as_dict = {"content": {"views": [_NATIVE_VIEW]}, "status": "active"}
    assert len(ev3.views_from_files([as_str, as_dict])) == 2


def test_views_from_files_respects_active_filter():
    files = [
        {"content": json.dumps({"views": [_SEMANTIC_VIEW]}), "status": "draft"},
        {"content": json.dumps({"views": [_NATIVE_VIEW]}), "status": "active"},
    ]
    assert len(ev3.views_from_files(files, only_active=True)) == 1
    assert len(ev3.views_from_files(files, only_active=False)) == 2


def test_views_from_changeset_reads_proposed_content():
    items = [
        {"proposed_content": json.dumps({"views": [_SEMANTIC_VIEW]})},
        {"content": json.dumps({"models": [{"name": "m"}]})},  # no views
    ]
    views = ev3.views_from_changeset(items)
    assert len(views) == 1
    assert views[0]["name"] == "warm_line_output_by_family"


def test_view_is_semantic_distinguishes_dialect():
    assert ev3.view_is_semantic(_SEMANTIC_VIEW) is True
    assert ev3.view_is_semantic(_NATIVE_VIEW) is False


def test_view_references_physical_schema_detects_leak():
    schemas = ["seagate_core", "seagate_ops", "seagate_ref"]
    assert ev3.view_references_physical_schema(_PHYSICAL_SEMANTIC_VIEW, schemas) is True
    assert ev3.view_references_physical_schema(_SEMANTIC_VIEW, schemas) is False


def test_view_authoring_metrics_summarises():
    schemas = ["seagate_core", "seagate_ops"]
    m = ev3.view_authoring_metrics(
        [_SEMANTIC_VIEW, _NATIVE_VIEW, _PHYSICAL_SEMANTIC_VIEW],
        physical_schemas=schemas,
    )
    assert m["count"] == 3
    assert m["semantic"] == 2  # semantic + the leaky one (no dialect)
    assert m["native"] == 1
    assert m["with_description"] == 2  # native view has empty properties
    assert m["semantic_referencing_physical_schema"] == 1
    assert m["physical_leak_names"] == ["leaky"]


def test_view_authoring_metrics_empty():
    m = ev3.view_authoring_metrics([])
    assert m["count"] == 0
    assert m["description_rate"] == 0.0


def test_sql_uses_any_case_insensitive():
    sql = "SELECT * FROM Warm_Line_Output_By_Family"
    assert ev3.sql_uses_any(sql, ["warm_line_output_by_family", "other"]) == [
        "warm_line_output_by_family"
    ]
    assert ev3.sql_uses_any(None, ["x"]) == []


# --------------------------------------------------------------------------- #
# Golden queries / recall
# --------------------------------------------------------------------------- #
_GOLDEN = {
    "name": "Warm line output",
    "question": "How many patties were plated on warm lines per family?",
    "semantic_sql": "SELECT sku_family FROM seagate_drive_skus",
    "verified_by": "admin",
    "verified_at": 1700000000,
}


def test_parse_golden_queries_handles_str_and_dict():
    as_str = json.dumps({"queries": [_GOLDEN]})
    assert len(ev3.parse_golden_queries(as_str)) == 1
    assert len(ev3.parse_golden_queries({"queries": [_GOLDEN, _GOLDEN]})) == 2
    assert ev3.parse_golden_queries("not json") == []


def test_find_golden_file_matches_reserved_path():
    files = [
        {"path": "models/seagate_sites.json", "content": "{}"},
        {"path": "queries.json", "content": json.dumps({"queries": [_GOLDEN]})},
    ]
    f = ev3.find_golden_file(files)
    assert f is not None
    assert ev3.parse_golden_queries(f["content"])[0]["name"] == "Warm line output"


def test_find_golden_file_handles_leading_slash_and_case():
    assert (
        ev3.find_golden_file([{"path": "/Queries.json", "content": "{}"}]) is not None
    )
    assert ev3.find_golden_file([{"path": "models/x.json"}]) is None


def test_recalled_example_count_reads_wren_context():
    assert (
        ev3.recalled_example_count({"wren_context": {"recalled_example_count": 3}}) == 3
    )
    assert (
        ev3.recalled_example_count({"wren_context": {"recalled_examples": [1, 2]}}) == 2
    )
    assert ev3.recalled_example_count({"wren_context": {}}) is None
    assert ev3.recalled_example_count({}) is None


def test_sql_matches_golden_tolerates_whitespace_and_wrapping():
    golden = "SELECT sku_family FROM seagate_drive_skus"
    produced = "select   sku_family\nfrom seagate_drive_skus"
    assert ev3.sql_matches_golden(produced, golden) is True
    wrapped = f"{golden} ORDER BY 1 LIMIT 1000"
    assert ev3.sql_matches_golden(wrapped, golden) is True
    assert ev3.sql_matches_golden("SELECT 1", golden) is False
    assert ev3.sql_matches_golden(None, golden) is False
