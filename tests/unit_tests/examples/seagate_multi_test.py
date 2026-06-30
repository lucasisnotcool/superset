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
"""Unit tests for the seagate_multi cross-schema eval fixture generator.

These guard the two invariants the eval depends on:
  * the seven core tables are byte-identical to the seagate_manufacturing
    fixture (so the ground truth in test_queries.md still holds), and
  * the distractor tables / schema split are present and stable.

The generator has no Superset runtime dependency, so it is loaded by file path
(mirroring how it imports its own sibling) and these tests run without a DB.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN_PATH = REPO_ROOT / "superset" / "examples" / "seagate_multi" / "generate_data.py"
SEAGATE_DATA = REPO_ROOT / "superset" / "examples" / "seagate_manufacturing" / "data"


def _load_generator():
    spec = importlib.util.spec_from_file_location("_seagate_multi_gen", GEN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def test_core_data_is_byte_identical_to_seagate_fixture():
    core = gen.build_core_tables()
    assert set(core) == set(gen.CORE_TABLES)
    for name, df in core.items():
        ref = pd.read_parquet(SEAGATE_DATA / f"{name}.parquet")
        pd.testing.assert_frame_equal(
            df.reset_index(drop=True),
            ref.reset_index(drop=True),
            check_dtype=False,
        )


def test_core_generation_is_deterministic():
    first = gen.build_core_tables()
    second = gen.build_core_tables()
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_distractor_generation_is_deterministic():
    first = gen.build_distractor_tables()
    second = gen.build_distractor_tables()
    assert set(first) == set(gen.DISTRACTOR_TABLES)
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_relevant_and_distractor_sets_are_disjoint_and_cover_all_tables():
    overlap = set(gen.RELEVANT_TABLES) & set(gen.DISTRACTOR_TABLES)
    assert not overlap, f"R and D overlap: {overlap}"
    assert set(gen.TABLE_SCHEMA) == set(gen.RELEVANT_TABLES) | set(
        gen.DISTRACTOR_TABLES
    )


def test_schema_split_places_master_and_transactional_correctly():
    assert gen.TABLE_SCHEMA["seagate_sites"] == "seagate_core"
    assert gen.TABLE_SCHEMA["seagate_production_lines"] == "seagate_core"
    assert gen.TABLE_SCHEMA["seagate_drive_skus"] == "seagate_core"
    assert gen.TABLE_SCHEMA["seagate_work_orders"] == "seagate_ops"
    assert gen.TABLE_SCHEMA["seagate_production_events"] == "seagate_ops"
    assert gen.TABLE_SCHEMA["seagate_quality_tests"] == "seagate_ops"
    assert gen.TABLE_SCHEMA["seagate_shipments"] == "seagate_ops"


def test_adversarial_distractors_have_colliding_columns():
    distractors = gen.build_distractor_tables()
    # `units` collides with units_completed/units_scrapped jargon.
    assert "units" in distractors["seagate_finance_ledger"].columns
    # `temperature_c` evokes "heat lamp"; shares the line_id FK.
    assert "temperature_c" in distractors["seagate_iot_sensor_logs"].columns
    assert "line_id" in distractors["seagate_iot_sensor_logs"].columns
    # `shift_code` collides with the shift mapping; shares the site_id FK.
    assert "shift_code" in distractors["seagate_hr_roster"].columns
    assert "site_id" in distractors["seagate_hr_roster"].columns
    for name in gen.ADVERSARIAL_DISTRACTORS:
        assert name in gen.DISTRACTOR_TABLES


def test_dataset_uuids_are_unique_and_disjoint_from_seagate():
    seagate_uuids = set(gen.sg.DATASET_UUIDS.values())
    multi_uuids = set(gen.DATASET_UUIDS.values())
    assert len(multi_uuids) == len(gen.DATASET_UUIDS), "duplicate UUIDs in fixture"
    assert not (seagate_uuids & multi_uuids), "UUID collision with seagate fixture"
    assert set(gen.DATASET_UUIDS) == set(gen.TABLE_SCHEMA)


def test_yaml_carries_schema_and_fresh_uuid():
    core = gen.build_core_tables()
    doc = gen.build_dataset_yaml("seagate_work_orders", core["seagate_work_orders"])
    assert doc["schema"] == "seagate_ops"
    assert doc["uuid"] == gen.DATASET_UUIDS["seagate_work_orders"]
    assert doc["database_uuid"] == gen.EXAMPLES_DATABASE_UUID
    # table_name stays the real name; the data_file (and loader stem) is prefixed.
    assert doc["table_name"] == "seagate_work_orders"
    assert doc["data_file"] == "mx_seagate_work_orders.parquet"


def test_file_stems_do_not_collide_with_seagate_manufacturing():
    """Regression: the examples loader keys ``load_<stem>`` and keeps the first dir
    discovered, so a shared parquet stem means these tables never load into their
    schemas. Every seagate_multi stem must be unique vs the single-schema fixture."""
    multi_data = GEN_PATH.parent / "data"
    seagate_stems = {p.stem for p in SEAGATE_DATA.glob("*.parquet")}
    multi_stems = {p.stem for p in multi_data.glob("*.parquet")}
    assert multi_stems, "fixture not generated yet"
    assert not (seagate_stems & multi_stems), (
        f"stem collision with seagate_manufacturing: {seagate_stems & multi_stems}"
    )
    assert all(s.startswith("mx_") for s in multi_stems)


def test_out_of_scope_schema_has_only_distractors():
    ref_tables = [t for t, s in gen.TABLE_SCHEMA.items() if s == "seagate_ref"]
    assert ref_tables, "expected an out-of-scope seagate_ref schema"
    assert all(t in gen.DISTRACTOR_TABLES for t in ref_tables)


@pytest.mark.parametrize(
    "qid,expected",
    [
        ("Q16_cobalt", 1751),
        ("Q16_vantage", 3017),
        ("Q17_n", 1567),
        ("Q18_tigerline_units", 175),
        ("Q18_reef_units", 151),
    ],
)
def test_cross_schema_ground_truth_is_stable(qid, expected):
    """Pin the Q16+ numbers so a silent data shift is caught by CI."""
    core = gen.build_core_tables()
    sites = core["seagate_sites"]
    lines = core["seagate_production_lines"]
    skus = core["seagate_drive_skus"]
    wo = (
        core["seagate_work_orders"]
        .merge(lines, on="line_id", suffixes=("", "_line"))
        .merge(skus, on="sku_id", suffixes=("", "_sku"))
        .merge(sites, on="site_id", suffixes=("", "_site"))
    )
    ev = core["seagate_production_events"].merge(wo, on="work_order_id")
    sh = core["seagate_shipments"].merge(wo, on="work_order_id")
    tiger = sites[sites["site_code"].isin(["SGY", "SGT"])]["site_id"]
    reef = sites[sites["site_code"].isin(["SGW", "SGN"])]["site_id"]

    warm = ev[ev["status_line"] == "WARM"]
    by_family = warm.groupby("drive_family")["units_completed"].sum()
    q4_vantage = ev[
        (ev["drive_family"] == "Vantage")
        & (ev["event_date"] >= pd.Timestamp(2025, 10, 1))
        & (ev["event_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    q4_sh = sh[
        (sh["ship_date"] >= pd.Timestamp(2025, 10, 1))
        & (sh["ship_date"] <= pd.Timestamp(2025, 12, 31))
    ]

    def nimbus_combo(site_ids):
        return int(
            q4_sh[
                q4_sh["site_id"].isin(site_ids)
                & (q4_sh["drive_family"] == "Nimbus")
                & (q4_sh["pallet_type"] == "COMBO")
                & (q4_sh["fulfillment_type"] == "DINE_IN")
            ]["qty_units"].sum()
        )

    values = {
        "Q16_cobalt": int(by_family["Cobalt"]),
        "Q16_vantage": int(by_family["Vantage"]),
        "Q17_n": int(
            q4_vantage[q4_vantage["ticket_type"] == "STANDARD"]["units_completed"].sum()
        ),
        "Q18_tigerline_units": nimbus_combo(tiger),
        "Q18_reef_units": nimbus_combo(reef),
    }
    assert values[qid] == expected
