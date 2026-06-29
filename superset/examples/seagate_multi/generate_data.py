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
"""Generator for the seagate_multi mock dataset (cross-schema eval v2).

Run with: python superset/examples/seagate_multi/generate_data.py

This fixture is the multi-schema sibling of ``seagate_manufacturing``. It exists
to exercise the cross-schema MDL feature and table-selection discrimination that
the single-schema fixture cannot. Two design rules make it trustworthy:

1. **Byte-identical core data.** The seven business tables are produced by
   importing the *same* builders from ``seagate_manufacturing/generate_data.py``
   with the *same* seed and call order, so every ground-truth number in
   ``test_queries.md`` (9,386; 0.961; 0.935; ...) holds unchanged. The only thing
   that changes is *where* each table lives:

   - schema ``seagate_core`` — master/reference data: ``seagate_sites``,
     ``seagate_production_lines``, ``seagate_drive_skus``.
   - schema ``seagate_ops``  — transactional facts: ``seagate_work_orders``,
     ``seagate_production_events``, ``seagate_quality_tests``,
     ``seagate_shipments``.

   Splitting master from transactional turns 10 of the 15 legacy questions into
   genuine cross-schema joins (Q6-Q10, Q12-Q15) while keeping two within-schema
   controls (Q5 sites+lines; Q11 events-only). A parity assertion against the
   single-schema parquet guarantees the data did not drift.

2. **Distractor tables.** Seven tables the BI glossary never mentions are added to
   test whether onboarding/enrichment/query ignore irrelevant tables. Some are
   *adversarial* — their column names collide with floor jargon (a
   ``finance_ledger.units``, an ``iot_sensor_logs.temperature_c`` vs "heat lamp",
   an ``hr_roster.shift_code`` vs the shift mapping) and even share foreign keys
   (``line_id``/``site_id``). Distractor rows are drawn from a *separate* RNG so
   they cannot perturb the deterministic core sequence.

Like the seagate generator, this writes ``data/*.parquet`` + ``datasets/*.yaml``
(picked up by ``superset.examples.data_loading.discover_datasets``) and has no
Superset runtime dependency. Requires Postgres at load time (SQLite has no real
schemas) — see EVAL_V2_SPEC.md R4.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# --------------------------------------------------------------------------- #
# Import the seagate builders directly from their file (no superset import, so
# this stays a dependency-free standalone script like its sibling).
# --------------------------------------------------------------------------- #
_SEAGATE_GEN = (
    Path(__file__).resolve().parent.parent
    / "seagate_manufacturing"
    / "generate_data.py"
)
_spec = importlib.util.spec_from_file_location("_seagate_gen", _SEAGATE_GEN)
assert _spec is not None
assert _spec.loader is not None
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

SEED = sg.SEED  # 20251231 — same seed => identical core data
DISTRACTOR_SEED = 7_700_001  # independent stream; never touches core determinism
DATA_DIR = Path(__file__).parent / "data"
DATASETS_DIR = Path(__file__).parent / "datasets"
EXAMPLES_DATABASE_UUID = sg.EXAMPLES_DATABASE_UUID

# The examples loader keys each loader function by the parquet FILE STEM
# (``load_<stem>``) and keeps the first one discovered, so two example dirs with the
# same stem collide — and ``seagate_manufacturing`` sorts first. The seven business
# tables here share their names with that fixture, so their files MUST use a unique
# stem (``mx_<table>``) or they silently never load into the seagate_core/ops
# schemas. The physical ``table_name`` (in the YAML) stays the real name; only the
# file stem + loader name are prefixed.
FILE_STEM_PREFIX = "mx_"


def file_stem(table_name: str) -> str:
    """Unique-per-repo parquet/yaml stem for a seagate_multi table."""
    return f"{FILE_STEM_PREFIX}{table_name}"

# Schema placement. The first seven are the relevant set R; the rest are
# distractors D (never named in the glossary).
SCHEMA_CORE = "seagate_core"
SCHEMA_OPS = "seagate_ops"
SCHEMA_REF = "seagate_ref"

TABLE_SCHEMA: dict[str, str] = {
    # --- relevant (R): master/reference ---
    "seagate_sites": SCHEMA_CORE,
    "seagate_production_lines": SCHEMA_CORE,
    "seagate_drive_skus": SCHEMA_CORE,
    # --- relevant (R): transactional ---
    "seagate_work_orders": SCHEMA_OPS,
    "seagate_production_events": SCHEMA_OPS,
    "seagate_quality_tests": SCHEMA_OPS,
    "seagate_shipments": SCHEMA_OPS,
    # --- distractors (D): adversarial (jargon/FK collisions) ---
    "seagate_finance_ledger": SCHEMA_OPS,  # `units` collides with units_completed
    "seagate_iot_sensor_logs": SCHEMA_OPS,  # `temperature_c` vs "heat lamp"; line_id FK
    "seagate_hr_roster": SCHEMA_CORE,  # `shift_code` vs shift map; site_id FK
    # --- distractors (D): neutral noise ---
    "seagate_maintenance_logs": SCHEMA_OPS,
    "seagate_vendor_contracts": SCHEMA_CORE,
    # --- distractors (D): out-of-scope schema (must NOT be pulled in) ---
    "seagate_marketing_campaigns": SCHEMA_REF,
    "seagate_web_sessions": SCHEMA_REF,
}

RELEVANT_TABLES: tuple[str, ...] = (
    "seagate_sites",
    "seagate_production_lines",
    "seagate_drive_skus",
    "seagate_work_orders",
    "seagate_production_events",
    "seagate_quality_tests",
    "seagate_shipments",
)
DISTRACTOR_TABLES: tuple[str, ...] = tuple(
    t for t in TABLE_SCHEMA if t not in RELEVANT_TABLES
)
ADVERSARIAL_DISTRACTORS: tuple[str, ...] = (
    "seagate_finance_ledger",
    "seagate_iot_sensor_logs",
    "seagate_hr_roster",
)

# Fresh, stable dataset UUIDs (must differ from the seagate fixture's so both
# can be imported into the same database).
DATASET_UUIDS = {
    "seagate_sites": "11e3ebd7-3458-413e-9d15-5de5f5053977",
    "seagate_production_lines": "1515f762-2512-440e-8bab-e6b98483b7af",
    "seagate_drive_skus": "c0342a21-f8c5-41fc-82a3-027abf0b105a",
    "seagate_work_orders": "655710a7-6f85-4e6e-a79f-9ed530997b76",
    "seagate_production_events": "346b2b84-393f-47d8-81f3-0eff4640cf74",
    "seagate_quality_tests": "cdee53ca-1577-4b1a-824d-c22b3e8a70ff",
    "seagate_shipments": "52c3da31-4adc-4460-bc14-48389b34b16a",
    "seagate_hr_roster": "331c1b4f-15d2-40d8-8e93-d265ec9c6c04",
    "seagate_vendor_contracts": "775f96ca-b556-4441-90ea-00bfcb3d8180",
    "seagate_finance_ledger": "8befade4-c182-490d-b3e2-9ab0e35a1546",
    "seagate_iot_sensor_logs": "7222431b-b0f4-42ba-adf6-32e4ad264912",
    "seagate_maintenance_logs": "8f0fafd5-b283-40b6-b3bd-c5a8b9c31313",
    "seagate_marketing_campaigns": "10618a5c-8437-4a56-9ee8-9c866596f547",
    "seagate_web_sessions": "df1802e7-8f77-4044-8576-b86ad4706632",
}

# Temporal columns across core + distractors (drives is_dttm + column SQL type).
DTTM_COLUMNS = set(sg.DTTM_COLUMNS) | {
    "recorded_at",
    "performed_at",
    "campaign_start",
    "campaign_end",
    "session_date",
    "hired_on",
    "contract_start",
}
MAIN_DTTM_COL: dict[str, str] = {
    **sg.MAIN_DTTM_COL,
    "seagate_iot_sensor_logs": "recorded_at",
    "seagate_maintenance_logs": "performed_at",
    "seagate_web_sessions": "session_date",
}

WINDOW_START = sg.WINDOW_START
WINDOW_END = sg.WINDOW_END


# --------------------------------------------------------------------------- #
# Distractor builders (separate RNG; never reference the core RNG stream)
# --------------------------------------------------------------------------- #
def build_hr_roster(rng: np.random.Generator) -> pd.DataFrame:
    """Adversarial: `shift_code` collides with the shift mapping; site_id FK."""
    roles = ["Operator", "Line Lead", "QA Tech", "Supervisor", "Logistics"]
    rows = []
    for emp_id in range(1, 49):
        rows.append(
            {
                "employee_id": emp_id,
                "employee_name": f"Employee {emp_id:03d}",
                "site_id": int(rng.integers(1, 5)),
                "shift_code": sg.SHIFT_CODES[int(rng.integers(0, 3))],
                "role": roles[int(rng.integers(0, len(roles)))],
                "hired_on": pd.Timestamp(2015, 1, 1)
                + pd.Timedelta(days=int(rng.integers(0, 3650))),
            }
        )
    return pd.DataFrame(rows)


def build_vendor_contracts(rng: np.random.Generator) -> pd.DataFrame:
    components = ["Spindle Motor", "Actuator", "Bracket Kit", "Firmware", "Packaging"]
    rows = []
    for vid in range(1, 23):
        rows.append(
            {
                "contract_id": vid,
                "vendor_name": f"Vendor {chr(65 + vid % 26)}{vid}",
                "component": components[int(rng.integers(0, len(components)))],
                "site_id": int(rng.integers(1, 5)),
                "annual_value_usd": int(rng.integers(50, 950)) * 1000,
                "contract_start": pd.Timestamp(2020, 1, 1)
                + pd.Timedelta(days=int(rng.integers(0, 1500))),
                "is_active": bool(rng.random() < 0.8),
            }
        )
    return pd.DataFrame(rows)


def build_finance_ledger(rng: np.random.Generator) -> pd.DataFrame:
    """Adversarial: `units` collides with the units_completed/scrapped jargon."""
    accounts = ["Revenue", "COGS", "Opex", "Capex", "Depreciation"]
    cost_centers = ["CC-100", "CC-200", "CC-300", "CC-400"]
    months = pd.date_range("2025-07-01", "2025-12-01", freq="MS")
    rows = []
    entry_id = 1
    for month in months:
        for _ in range(int(rng.integers(45, 56))):
            rows.append(
                {
                    "entry_id": entry_id,
                    "period": month.strftime("%Y-%m"),
                    "cost_center": cost_centers[
                        int(rng.integers(0, len(cost_centers)))
                    ],
                    "account": accounts[int(rng.integers(0, len(accounts)))],
                    "units": int(rng.integers(1, 500)),  # decoy column name
                    "amount_usd": round(float(rng.uniform(-50_000, 250_000)), 2),
                }
            )
            entry_id += 1
    return pd.DataFrame(rows)


def build_iot_sensor_logs(rng: np.random.Generator) -> pd.DataFrame:
    """Adversarial: `temperature_c` evokes "heat lamp"; shares line_id FK."""
    sensor_types = ["THERMAL", "VIBRATION", "ACOUSTIC", "HUMIDITY"]
    rows = []
    for reading_id in range(1, 601):
        ts = pd.Timestamp(WINDOW_START) + pd.Timedelta(
            minutes=int(rng.integers(0, 184 * 24 * 60))
        )
        rows.append(
            {
                "reading_id": reading_id,
                "line_id": int(rng.integers(1, 13)),  # decoy FK into production_lines
                "recorded_at": ts,
                "sensor_type": sensor_types[int(rng.integers(0, len(sensor_types)))],
                "temperature_c": round(float(rng.uniform(18.0, 65.0)), 1),  # decoy
                "vibration_mm_s": round(float(rng.uniform(0.1, 9.5)), 2),
            }
        )
    return pd.DataFrame(rows)


def build_maintenance_logs(rng: np.random.Generator) -> pd.DataFrame:
    maint_types = ["Preventive", "Corrective", "Calibration", "Inspection"]
    rows = []
    for maint_id in range(1, 141):
        ts = pd.Timestamp(WINDOW_START) + pd.Timedelta(days=int(rng.integers(0, 184)))
        rows.append(
            {
                "maint_id": maint_id,
                "line_id": int(rng.integers(1, 13)),
                "performed_at": ts,
                "maint_type": maint_types[int(rng.integers(0, len(maint_types)))],
                "downtime_minutes": int(rng.integers(5, 480)),
                "technician": f"Tech {int(rng.integers(1, 20)):02d}",
            }
        )
    return pd.DataFrame(rows)


def build_marketing_campaigns(rng: np.random.Generator) -> pd.DataFrame:
    channels = ["Email", "Webinar", "Trade Show", "Paid Search", "Partner"]
    regions = ["NA", "EMEA", "APAC"]
    rows = []
    for cid in range(1, 17):
        start = pd.Timestamp(2025, 1, 1) + pd.Timedelta(days=int(rng.integers(0, 330)))
        rows.append(
            {
                "campaign_id": cid,
                "campaign_name": f"Campaign {cid:02d}",
                "channel": channels[int(rng.integers(0, len(channels)))],
                "region": regions[int(rng.integers(0, len(regions)))],
                "campaign_start": start,
                "campaign_end": start + pd.Timedelta(days=int(rng.integers(7, 60))),
                "spend_usd": int(rng.integers(5, 200)) * 1000,
            }
        )
    return pd.DataFrame(rows)


def build_web_sessions(rng: np.random.Generator) -> pd.DataFrame:
    devices = ["desktop", "mobile", "tablet"]
    countries = ["USA", "Singapore", "Thailand", "Germany", "Japan"]
    rows = []
    for sid in range(1, 401):
        rows.append(
            {
                "session_id": sid,
                "session_date": pd.Timestamp(WINDOW_START)
                + pd.Timedelta(days=int(rng.integers(0, 184))),
                "country": countries[int(rng.integers(0, len(countries)))],
                "device": devices[int(rng.integers(0, len(devices)))],
                "page_views": int(rng.integers(1, 40)),
                "duration_sec": int(rng.integers(5, 1800)),
            }
        )
    return pd.DataFrame(rows)


DISTRACTOR_BUILDERS = {
    "seagate_hr_roster": build_hr_roster,
    "seagate_vendor_contracts": build_vendor_contracts,
    "seagate_finance_ledger": build_finance_ledger,
    "seagate_iot_sensor_logs": build_iot_sensor_logs,
    "seagate_maintenance_logs": build_maintenance_logs,
    "seagate_marketing_campaigns": build_marketing_campaigns,
    "seagate_web_sessions": build_web_sessions,
}


# --------------------------------------------------------------------------- #
# Core data — identical to the seagate fixture (same builders, seed, order)
# --------------------------------------------------------------------------- #
def build_core_tables() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    sites = sg.build_sites()
    lines = sg.build_production_lines()
    skus = sg.build_drive_skus()
    work_orders = sg.build_work_orders(rng, lines, skus)
    events = sg.build_production_events(rng, work_orders)
    tests = sg.build_quality_tests(rng, work_orders)
    shipments = sg.build_shipments(rng, work_orders)

    work_orders["opened_at"] = pd.to_datetime(work_orders["opened_at"])
    work_orders["closed_at"] = pd.to_datetime(work_orders["closed_at"])
    events["event_date"] = pd.to_datetime(events["event_date"])
    tests["tested_at"] = pd.to_datetime(tests["tested_at"])
    shipments["ship_date"] = pd.to_datetime(shipments["ship_date"])

    clean_work_orders = work_orders.drop(
        columns=["_bake_duration", "_plate_date", "_close_date"]
    )
    return {
        "seagate_sites": sites,
        "seagate_production_lines": lines,
        "seagate_drive_skus": skus,
        "seagate_work_orders": clean_work_orders,
        "seagate_production_events": events,
        "seagate_quality_tests": tests,
        "seagate_shipments": shipments,
    }


def build_distractor_tables() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(DISTRACTOR_SEED)
    # Deterministic order so the shared RNG stream is reproducible.
    return {name: DISTRACTOR_BUILDERS[name](rng) for name in DISTRACTOR_BUILDERS}


# --------------------------------------------------------------------------- #
# Cross-schema ground truth (Q16+) — computed from the same data
# --------------------------------------------------------------------------- #
def print_cross_schema_ground_truth(core: dict[str, pd.DataFrame]) -> None:
    sites = core["seagate_sites"]
    lines = core["seagate_production_lines"]
    skus = core["seagate_drive_skus"]
    work_orders = core["seagate_work_orders"]
    events = core["seagate_production_events"]
    shipments = core["seagate_shipments"]

    wo = (
        work_orders.merge(lines, on="line_id", suffixes=("", "_line"))
        .merge(skus, on="sku_id", suffixes=("", "_sku"))
        .merge(sites, on="site_id", suffixes=("", "_site"))
    )
    ev = events.merge(wo, on="work_order_id")
    sh = shipments.merge(wo, on="work_order_id")
    tiger_sites = sites[sites["site_code"].isin(["SGY", "SGT"])]["site_id"]
    reef_sites = sites[sites["site_code"].isin(["SGW", "SGN"])]["site_id"]

    def golden_yield(df: pd.DataFrame) -> tuple[float, int]:
        std = df[df["ticket_type"] == "STANDARD"]
        completed = std["units_completed"].sum()
        if completed == 0:
            return float("nan"), 0
        gy = (
            completed - std["units_scrapped"].sum() - std["units_reworked"].sum()
        ) / completed
        return round(float(gy), 4), int(completed)

    print("=== Q16 (L2 cross-schema): patties plated on WARM lines, by family ===")
    # production_lines.status lives on the line; after the merge it is `status_line`.
    warm = ev[ev["status_line"] == "WARM"]
    by_family = warm.groupby("drive_family")["units_completed"].sum().sort_index()
    print(by_family.to_string())

    print("\n=== Q17 (L3 cross-schema): Golden Yield, Vantage family, Q4 2025 ===")
    q4_vantage = ev[
        (ev["drive_family"] == "Vantage")
        & (ev["event_date"] >= pd.Timestamp(2025, 10, 1))
        & (ev["event_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    print("Golden Yield Vantage Q4 (rate, completed n) =", golden_yield(q4_vantage))

    print(
        "\n=== Q18 (L4 cross-schema): Nimbus Combo+Dine-In shipped units by region, "
        "Q4 2025, with each region's Golden Yield over the window ==="
    )
    q4_sh = sh[
        (sh["ship_date"] >= pd.Timestamp(2025, 10, 1))
        & (sh["ship_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    q4_ev = ev[
        (ev["event_date"] >= pd.Timestamp(2025, 10, 1))
        & (ev["event_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    for region, site_ids in [("Tigerline", tiger_sites), ("Reef", reef_sites)]:
        combo_dinein = q4_sh[
            q4_sh["site_id"].isin(site_ids)
            & (q4_sh["drive_family"] == "Nimbus")
            & (q4_sh["pallet_type"] == "COMBO")
            & (q4_sh["fulfillment_type"] == "DINE_IN")
        ]["qty_units"].sum()
        gy = golden_yield(q4_ev[q4_ev["site_id"].isin(site_ids)])
        print(
            f"{region}: Nimbus Combo+Dine-In units={int(combo_dinein)}, "
            f"region Golden Yield(all families)={gy}"
        )


# --------------------------------------------------------------------------- #
# Parity assertion (R7) — core data must equal the single-schema fixture
# --------------------------------------------------------------------------- #
def assert_parity(core: dict[str, pd.DataFrame]) -> None:
    """Fail loudly if the split core data drifted from seagate_manufacturing."""
    ref_dir = _SEAGATE_GEN.parent / "data"
    if not ref_dir.exists():
        print(f"[parity] reference dir {ref_dir} missing; skipping parity check.")
        return
    for name, df in core.items():
        ref_path = ref_dir / f"{name}.parquet"
        if not ref_path.exists():
            print(f"[parity] {ref_path} missing; skipping {name}.")
            continue
        ref = pd.read_parquet(ref_path)
        assert list(df.columns) == list(ref.columns), (
            f"{name}: column mismatch vs seagate fixture"
        )
        assert len(df) == len(ref), f"{name}: row count {len(df)} != seagate {len(ref)}"
        pd.testing.assert_frame_equal(
            df.reset_index(drop=True), ref.reset_index(drop=True), check_dtype=False
        )
    print("[parity] OK — core data is byte-identical to seagate_manufacturing.")


# --------------------------------------------------------------------------- #
# Dataset YAML (mirror of the seagate builder, parameterized by schema/uuid)
# --------------------------------------------------------------------------- #
def build_dataset_yaml(table_name: str, df: pd.DataFrame) -> dict:
    columns = []
    for column_name in df.columns:
        is_dttm = column_name in DTTM_COLUMNS
        columns.append(
            {
                "advanced_data_type": None,
                "column_name": column_name,
                "description": None,
                "expression": None,
                "extra": "{}",
                "filterable": True,
                "groupby": True,
                "is_active": True,
                "is_dttm": is_dttm,
                "python_date_format": None,
                "type": sg._superset_column_type(df[column_name], column_name),
                "verbose_name": None,
            }
        )
    return {
        "always_filter_main_dttm": False,
        "cache_timeout": None,
        "catalog": None,
        "columns": columns,
        "data_file": f"{file_stem(table_name)}.parquet",
        "database_uuid": EXAMPLES_DATABASE_UUID,
        "default_endpoint": None,
        "description": None,
        "extra": None,
        "fetch_values_predicate": None,
        "filter_select_enabled": True,
        "folders": None,
        "main_dttm_col": MAIN_DTTM_COL.get(table_name),
        "metrics": [
            {
                "currency": None,
                "d3format": None,
                "description": None,
                "expression": "COUNT(*)",
                "extra": '{"warning_markdown": ""}',
                "metric_name": "count",
                "metric_type": "count",
                "verbose_name": "COUNT(*)",
                "warning_text": None,
            }
        ],
        "normalize_columns": False,
        "offset": 0,
        "params": None,
        "schema": TABLE_SCHEMA[table_name],
        "sql": None,
        "table_name": table_name,
        "template_params": None,
        "uuid": DATASET_UUIDS[table_name],
        "version": "1.0.0",
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)

    core = build_core_tables()
    assert_parity(core)
    print_cross_schema_ground_truth(core)

    distractors = build_distractor_tables()
    tables = {**core, **distractors}

    # Remove any stale non-prefixed artifacts from earlier runs (they would collide
    # with seagate_manufacturing's stems and never load — the very bug the prefix
    # fixes). Only the mx_-prefixed files are authoritative.
    stale_files = [
        *DATA_DIR.glob("seagate_*.parquet"),
        *DATASETS_DIR.glob("seagate_*.yaml"),
    ]
    for stale in stale_files:
        stale.unlink()

    for table_name, df in tables.items():
        stem = file_stem(table_name)
        out_path = DATA_DIR / f"{stem}.parquet"
        df.to_parquet(out_path, index=False)
        schema = TABLE_SCHEMA[table_name]
        kind = "R" if table_name in RELEVANT_TABLES else "D"
        print(f"wrote {out_path} ({len(df)} rows) -> {schema}.{table_name} [{kind}]")

        yaml_path = DATASETS_DIR / f"{stem}.yaml"
        yaml_body = yaml.safe_dump(build_dataset_yaml(table_name, df), sort_keys=True)
        yaml_path.write_text(sg.ASF_HEADER + yaml_body)


if __name__ == "__main__":
    main()
