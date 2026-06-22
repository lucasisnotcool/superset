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
"""One-off generator for the seagate_manufacturing mock dataset.

Run with: python superset/examples/seagate_manufacturing/generate_data.py

Builds the seven seagate_* tables as seeded, deterministic pandas
DataFrames, writes them to data/*.parquet (picked up by
superset.examples.data_loading.discover_datasets), and prints the
ground-truth answer for every question in
superset_ai_agent/dev_fixtures/seagate_manufacturing/test_queries.md so
that doc can ship with real numbers instead of bare formulas.

This script is not auto-discovered by the examples loader (which only
globs *.parquet files) and has no Superset runtime dependency.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

SEED = 20251231
DATA_DIR = Path(__file__).parent / "data"
DATASETS_DIR = Path(__file__).parent / "datasets"
EXAMPLES_DATABASE_UUID = "a2dc77af-e654-49bb-b321-40f6b559a1ee"

DATASET_UUIDS = {
    "seagate_sites": "cf061f5a-a834-427f-b2c0-df969e4a34c8",
    "seagate_production_lines": "5cb8c228-3bbb-4b59-a360-801ec57dc8c1",
    "seagate_drive_skus": "e2d2fcb2-cd92-43f8-b6d4-c3934b49a83c",
    "seagate_work_orders": "08622e65-639d-4957-94ea-4e2fd76bbb67",
    "seagate_production_events": "6a31bd10-2707-4d25-9be6-e0de06174a52",
    "seagate_quality_tests": "4d8f808b-08bd-490c-811f-6f13d6e94e28",
    "seagate_shipments": "ea854f3a-3b8e-4426-9dfc-fbc8bd85fcdd",
}

# main_dttm_col per table; tables not listed have no temporal column.
MAIN_DTTM_COL = {
    "seagate_work_orders": "opened_at",
    "seagate_production_events": "event_date",
    "seagate_quality_tests": "tested_at",
    "seagate_shipments": "ship_date",
}

DTTM_COLUMNS = {"opened_at", "closed_at", "event_date", "tested_at", "ship_date"}

WINDOW_START = date(2025, 7, 1)
WINDOW_END = date(2025, 12, 31)
TODAY = WINDOW_END
# Anchor for "this Diner Week" examples: chosen mid-window (not TODAY) so the
# full Wed->Tue Diner Week it falls in has production data on both sides.
DINER_WEEK_ANCHOR = date(2025, 12, 17)

SHIFT_CODES = ["SUNRISE", "DAYLIGHT", "MOONLIGHT"]
TEST_TYPES = ["HEAT_LAMP", "TASTE_TEST", "PLATE_SPIN"]
REGIONS = ["NA", "EMEA", "APAC"]


def build_sites() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "site_id": 1,
                "site_code": "SGY",
                "site_name": "Shugart Yard",
                "country": "USA",
                "opened_year": 1989,
            },
            {
                "site_id": 2,
                "site_code": "SGW",
                "site_name": "Scotts Valley West",
                "country": "USA",
                "opened_year": 1995,
            },
            {
                "site_id": 3,
                "site_code": "SGT",
                "site_name": "Tigerline Point",
                "country": "Singapore",
                "opened_year": 2001,
            },
            {
                "site_id": 4,
                "site_code": "SGN",
                "site_name": "Reef Hollow",
                "country": "Thailand",
                "opened_year": 2004,
            },
        ]
    )


def build_production_lines() -> pd.DataFrame:
    rows = [
        (1, 1, "L1", "Cobalt", "HOT"),
        (2, 1, "L2", "Tundra", "HOT"),
        (3, 1, "L3", "Vantage", "WARM"),
        (4, 2, "L1", "Nimbus", "HOT"),
        (5, 2, "L2", "Cobalt", "WARM"),
        (6, 2, "L3", "Tundra", "DARK"),
        (7, 3, "L1", "Vantage", "HOT"),
        (8, 3, "L2", "Nimbus", "HOT"),
        (9, 3, "L3", "Cobalt", "HOT"),
        (10, 4, "L1", "Tundra", "HOT"),
        (11, 4, "L2", "Vantage", "WARM"),
        (12, 4, "L3", "Nimbus", "DARK"),
    ]
    return pd.DataFrame(
        rows,
        columns=["line_id", "site_id", "line_code", "drive_family", "status"],
    )


def build_drive_skus() -> pd.DataFrame:
    rows = [
        (1, "COB-08", "Cobalt", 8, "SATA", 2019),
        (2, "COB-16", "Cobalt", 16, "SAS", 2022),
        (3, "TUN-04", "Tundra", 4, "SATA", 2017),
        (4, "TUN-12", "Tundra", 12, "SATA", 2020),
        (5, "VAN-10", "Vantage", 10, "SAS", 2018),
        (6, "VAN-20", "Vantage", 20, "NVMe", 2023),
        (7, "NIM-02", "Nimbus", 2, "SATA", 2015),
        (8, "NIM-08", "Nimbus", 8, "NVMe", 2021),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "sku_id",
            "sku_code",
            "drive_family",
            "capacity_tb",
            "interface",
            "launch_year",
        ],
    )


def build_work_orders(
    rng: np.random.Generator, lines: pd.DataFrame, skus: pd.DataFrame
) -> pd.DataFrame:
    active_lines = lines[lines["status"] != "DARK"]
    window_days = (WINDOW_END - WINDOW_START).days
    rows = []
    work_order_id = 1
    for _, line in active_lines.iterrows():
        family_skus = skus[skus["drive_family"] == line["drive_family"]][
            "sku_id"
        ].tolist()
        n_tickets = int(rng.integers(24, 33))
        for _ in range(n_tickets):
            ticket_type = "SHORT_ORDER" if rng.random() < 0.15 else "STANDARD"
            opened_offset = int(rng.integers(0, window_days))
            opened_at = WINDOW_START + timedelta(days=opened_offset)
            if ticket_type == "STANDARD":
                bake_duration = int(rng.integers(3, 8))
                target_qty = int(rng.integers(40, 91))
            else:
                bake_duration = int(rng.integers(1, 3))
                target_qty = int(rng.integers(10, 31))
            qa_lag = int(rng.integers(1, 4))
            plate_date = opened_at + timedelta(days=bake_duration)
            close_date = plate_date + timedelta(days=qa_lag)

            if TODAY < plate_date:
                status = "BAKING"
            elif TODAY < close_date:
                status = "PLATED"
            else:
                status = "CLOSED"

            rows.append(
                {
                    "work_order_id": work_order_id,
                    "line_id": int(line["line_id"]),
                    "sku_id": int(rng.choice(family_skus)),
                    "ticket_type": ticket_type,
                    "opened_at": opened_at,
                    "closed_at": close_date if status == "CLOSED" else pd.NaT,
                    "target_qty": target_qty,
                    "status": status,
                    "_bake_duration": bake_duration,
                    "_plate_date": plate_date,
                    "_close_date": close_date,
                }
            )
            work_order_id += 1
    return pd.DataFrame(rows)


def build_production_events(
    rng: np.random.Generator, work_orders: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    event_id = 1
    for _, wo in work_orders.iterrows():
        last_bake_day = min(wo["_plate_date"], TODAY + timedelta(days=1))
        n_days = (last_bake_day - wo["opened_at"]).days
        scrap_lo, scrap_hi = (
            (0.03, 0.10) if wo["ticket_type"] == "SHORT_ORDER" else (0.01, 0.06)
        )
        for day_offset in range(n_days):
            event_date = wo["opened_at"] + timedelta(days=day_offset)
            base = wo["target_qty"] / wo["_bake_duration"]
            units_completed = max(1, int(round(base * rng.uniform(0.85, 1.15))))
            units_scrapped = int(
                round(units_completed * rng.uniform(scrap_lo, scrap_hi))
            )
            units_reworked = int(round(units_completed * rng.uniform(0.01, 0.04)))
            rows.append(
                {
                    "event_id": event_id,
                    "work_order_id": int(wo["work_order_id"]),
                    "event_date": event_date,
                    "shift_code": SHIFT_CODES[day_offset % 3],
                    "units_completed": units_completed,
                    "units_scrapped": units_scrapped,
                    "units_reworked": units_reworked,
                }
            )
            event_id += 1
    return pd.DataFrame(rows)


def build_quality_tests(
    rng: np.random.Generator, work_orders: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    test_id = 1
    eligible = work_orders[work_orders["status"].isin(["PLATED", "CLOSED"])]
    for _, wo in eligible.iterrows():
        n_tests = 1 if wo["ticket_type"] == "SHORT_ORDER" else 2
        for i in range(n_tests):
            tested_offset = int(rng.integers(0, 3))
            tested_at = min(wo["_plate_date"] + timedelta(days=tested_offset), TODAY)
            result = "FAIL" if rng.random() < 0.12 else "PASS"
            garnish_defect = bool(result == "FAIL" and rng.random() < 0.4)
            rows.append(
                {
                    "test_id": test_id,
                    "work_order_id": int(wo["work_order_id"]),
                    "test_type": TEST_TYPES[(test_id + i) % 3],
                    "result": result,
                    "tested_at": tested_at,
                    "garnish_defect": garnish_defect,
                }
            )
            test_id += 1
    return pd.DataFrame(rows)


def build_shipments(
    rng: np.random.Generator, work_orders: pd.DataFrame
) -> pd.DataFrame:
    rows = []
    shipment_id = 1
    closed = work_orders[work_orders["status"] == "CLOSED"]
    for _, wo in closed.iterrows():
        n_shipments = 1 if rng.random() < 0.7 else 2
        remaining = wo["target_qty"]
        for i in range(n_shipments):
            qty = (
                remaining
                if i == n_shipments - 1
                else int(remaining * rng.uniform(0.4, 0.6))
            )
            remaining -= qty
            ship_offset = int(rng.integers(1, 6)) * (i + 1)
            ship_date = min(wo["_close_date"] + timedelta(days=ship_offset), TODAY)
            rows.append(
                {
                    "shipment_id": shipment_id,
                    "work_order_id": int(wo["work_order_id"]),
                    "ship_date": ship_date,
                    "destination_region": REGIONS[rng.integers(0, 3)],
                    "fulfillment_type": "DINE_IN" if rng.random() < 0.55 else "TO_GO",
                    "pallet_type": "COMBO" if rng.random() < 0.3 else "SINGLE",
                    "qty_units": max(qty, 1),
                }
            )
            shipment_id += 1
    return pd.DataFrame(rows)


def diner_week_bounds(anchor: date) -> tuple[date, date]:
    # Diner Week runs Wednesday -> Tuesday (weekday(): Mon=0 ... Wed=2, Tue=1)
    days_since_wed = (anchor.weekday() - 2) % 7
    start = anchor - timedelta(days=days_since_wed)
    return start, start + timedelta(days=6)


def print_ground_truth(
    sites: pd.DataFrame,
    lines: pd.DataFrame,
    skus: pd.DataFrame,
    work_orders: pd.DataFrame,
    events: pd.DataFrame,
    tests: pd.DataFrame,
    shipments: pd.DataFrame,
) -> None:
    wo = (
        work_orders.merge(lines, on="line_id", suffixes=("", "_line"))
        .merge(skus, on="sku_id", suffixes=("", "_sku"))
        .merge(sites, on="site_id", suffixes=("", "_site"))
    )
    ev = events.merge(wo, on="work_order_id")
    qt = tests.merge(wo, on="work_order_id")
    sh = shipments.merge(wo, on="work_order_id")

    print("=== L1 ===")
    pick_date = events["event_date"].mode().iloc[0]
    print(
        "patties 86'd on",
        pick_date,
        "=",
        events.loc[events["event_date"] == pick_date, "units_scrapped"].sum(),
    )
    print(
        "tickets currently short order (not closed) =",
        len(
            work_orders[
                (work_orders["ticket_type"] == "SHORT_ORDER")
                & (work_orders["status"] != "CLOSED")
            ]
        ),
    )
    print(
        "tickets currently on the griddle (BAKING), target_qty sum =",
        work_orders.loc[work_orders["status"] == "BAKING", "target_qty"].sum(),
    )
    print(
        "heat lamp tests with a garnish problem =",
        len(tests[(tests["test_type"] == "HEAT_LAMP") & (tests["garnish_defect"])]),
    )

    print("=== L2 ===")
    warm_sites = (
        lines[lines["status"] == "WARM"]
        .merge(sites, on="site_id")["site_name"]
        .unique()
    )
    print("sites with a WARM line =", list(warm_sites))
    tiger_sites = sites[sites["site_code"].isin(["SGY", "SGT"])]["site_id"]
    reef_sites = sites[sites["site_code"].isin(["SGW", "SGN"])]["site_id"]
    tiger_plated = ev[ev["site_id"].isin(tiger_sites)]["units_completed"].sum()
    print("Tigerline region total patties plated =", tiger_plated)
    reef_togo = sh[
        sh["site_id"].isin(reef_sites) & (sh["fulfillment_type"] == "TO_GO")
    ]["qty_units"].sum()
    print("Reef region To-Go shipped units =", reef_togo)
    last_week_start = TODAY - timedelta(days=6)
    by_family = (
        ev[ev["event_date"] >= pd.Timestamp(last_week_start)]
        .groupby("drive_family")["units_completed"]
        .sum()
    )
    print(f"patties plated {last_week_start}..{TODAY} by family =\n{by_family}")

    print("=== L3 ===")

    def golden_yield(df: pd.DataFrame) -> tuple[float, int]:
        std = df[df["ticket_type"] == "STANDARD"]
        completed = std["units_completed"].sum()
        if completed == 0:
            return float("nan"), 0
        gy = (
            completed - std["units_scrapped"].sum() - std["units_reworked"].sum()
        ) / completed
        return gy, int(completed)

    cobalt_last_month = ev[
        (ev["drive_family"] == "Cobalt")
        & (ev["event_date"] >= pd.Timestamp(2025, 12, 1))
        & (ev["event_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    print("Golden Yield, Cobalt, December 2025 =", golden_yield(cobalt_last_month))

    def true_pass_rate(df: pd.DataFrame) -> tuple[float, int]:
        denom = df[~((df["result"] == "FAIL") & (df["garnish_defect"]))]
        if len(denom) == 0:
            return float("nan"), 0
        return (denom["result"] == "PASS").sum() / len(denom), len(denom)

    tigerline_spin = qt[
        (qt["site_name"] == "Tigerline Point") & (qt["test_type"] == "PLATE_SPIN")
    ]
    print(
        "True Pass Rate, Plate Spin, Tigerline Point (rate, denom n) =",
        true_pass_rate(tigerline_spin),
    )

    dw_start, dw_end = diner_week_bounds(DINER_WEEK_ANCHOR)
    print(f"Diner Week containing {DINER_WEEK_ANCHOR} = {dw_start}..{dw_end}")
    moonlight_diner_week = ev[
        (ev["shift_code"] == "MOONLIGHT")
        & (ev["event_date"] >= pd.Timestamp(dw_start))
        & (ev["event_date"] <= pd.Timestamp(dw_end))
    ]
    print(
        "patties plated, Moonlight shift, that Diner Week =",
        moonlight_diner_week["units_completed"].sum(),
    )

    print(
        "Golden Yield, Short Order tickets at Scotts Valley West = "
        "UNDEFINED (excluded by definition)"
    )

    print("=== L4 ===")
    q4 = ev[
        (ev["event_date"] >= pd.Timestamp(2025, 10, 1))
        & (ev["event_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    q4_sh = sh[
        (sh["ship_date"] >= pd.Timestamp(2025, 10, 1))
        & (sh["ship_date"] <= pd.Timestamp(2025, 12, 31))
    ]
    for region_name, site_ids in [("Tigerline", tiger_sites), ("Reef", reef_sites)]:
        combo_dinein = q4_sh[
            q4_sh["site_id"].isin(site_ids)
            & (q4_sh["pallet_type"] == "COMBO")
            & (q4_sh["fulfillment_type"] == "DINE_IN")
        ]
        gy = golden_yield(q4[q4["site_id"].isin(site_ids)])
        print(
            f"{region_name} region, Q4 2025: Golden Yield={gy}, "
            f"Combo+Dine-In shipped units={combo_dinein['qty_units'].sum()}"
        )

    for region_name, site_ids in [("Tigerline", tiger_sites), ("Reef", reef_sites)]:
        scoped = qt[
            qt["site_id"].isin(site_ids)
            & (qt["test_type"] == "HEAT_LAMP")
            & (qt["ticket_type"] != "SHORT_ORDER")
            & (qt["tested_at"] >= pd.Timestamp(2025, 10, 1))
            & (qt["tested_at"] <= pd.Timestamp(2025, 12, 31))
        ]
        print(
            f"{region_name} True Pass Rate "
            "(Heat Lamp, Q4 2025, non-Short-Order, rate/denom n) =",
            true_pass_rate(scoped),
        )

    last_60_start = TODAY - timedelta(days=59)
    for region_name, site_ids in [("Tigerline", tiger_sites), ("Reef", reef_sites)]:
        scoped_sh = sh[
            sh["site_id"].isin(site_ids)
            & (sh["drive_family"] == "Nimbus")
            & (sh["ship_date"] >= pd.Timestamp(last_60_start))
            & (sh["fulfillment_type"] == "DINE_IN")
        ]
        total = scoped_sh["qty_units"].sum()
        combo = scoped_sh[scoped_sh["pallet_type"] == "COMBO"]["qty_units"].sum()
        share = combo / total if total else float("nan")
        print(
            f"{region_name} Nimbus Dine-In last 60 days: "
            f"total={total}, combo_share={share}"
        )


def _superset_column_type(series: pd.Series, column_name: str) -> str:
    if column_name in DTTM_COLUMNS:
        return "TIMESTAMP WITHOUT TIME ZONE"
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    return "TEXT"


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
                "type": _superset_column_type(df[column_name], column_name),
                "verbose_name": None,
            }
        )
    return {
        "always_filter_main_dttm": False,
        "cache_timeout": None,
        "catalog": None,
        "columns": columns,
        "data_file": f"{table_name}.parquet",
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
        "schema": None,
        "sql": None,
        "table_name": table_name,
        "template_params": None,
        "uuid": DATASET_UUIDS[table_name],
        "version": "1.0.0",
    }


ASF_HEADER = """# Licensed to the Apache Software Foundation (ASF) under one
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
"""


def main() -> None:
    rng = np.random.default_rng(SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    sites = build_sites()
    lines = build_production_lines()
    skus = build_drive_skus()
    work_orders = build_work_orders(rng, lines, skus)
    events = build_production_events(rng, work_orders)
    tests = build_quality_tests(rng, work_orders)
    shipments = build_shipments(rng, work_orders)

    work_orders["opened_at"] = pd.to_datetime(work_orders["opened_at"])
    work_orders["closed_at"] = pd.to_datetime(work_orders["closed_at"])
    events["event_date"] = pd.to_datetime(events["event_date"])
    tests["tested_at"] = pd.to_datetime(tests["tested_at"])
    shipments["ship_date"] = pd.to_datetime(shipments["ship_date"])

    print_ground_truth(sites, lines, skus, work_orders, events, tests, shipments)

    clean_work_orders = work_orders.drop(
        columns=["_bake_duration", "_plate_date", "_close_date"]
    )

    tables = {
        "seagate_sites": sites,
        "seagate_production_lines": lines,
        "seagate_drive_skus": skus,
        "seagate_work_orders": clean_work_orders,
        "seagate_production_events": events,
        "seagate_quality_tests": tests,
        "seagate_shipments": shipments,
    }
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    for table_name, df in tables.items():
        out_path = DATA_DIR / f"{table_name}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"wrote {out_path} ({len(df)} rows)")

        yaml_path = DATASETS_DIR / f"{table_name}.yaml"
        yaml_body = yaml.safe_dump(build_dataset_yaml(table_name, df), sort_keys=True)
        yaml_path.write_text(ASF_HEADER + yaml_body)
        print(f"wrote {yaml_path}")


if __name__ == "__main__":
    main()
