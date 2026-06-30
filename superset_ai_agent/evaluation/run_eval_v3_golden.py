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
"""Regime 2 (memory ON): golden-query recall lift + recall-signal experiments.

Run with ``WREN_MEMORY_STORE=lancedb`` (or sqlalchemy) so recall fires:

    EVAL_AGENT_BASE_URL=http://localhost:8090/ai-agent \\
    EVAL_SUPERSET_BASE_URL=http://localhost:8090 \\
    python run_eval_v3_golden.py --repeats 3

* **E16** golden-recall accuracy lift — baseline a hard question, promote a
  verified golden query, re-ask the question *and a paraphrase*, measure the
  before/after verdict + attribution (recall count, SQL match).
* **E17** recall signal — confirm the promoted golden activates, is recalled, and
  (the verified-answer signal) the agent reproduces it.
"""

from __future__ import annotations

import argparse
import json  # noqa: TID251 - standalone eval tooling
import sys
import time
from pathlib import Path
from typing import Any

import eval_common as ec
import eval_v3 as ev3
import seagate_scoring as score

SCHEMAS = ["seagate_core", "seagate_ops"]
OUT_DIR = Path(__file__).resolve().parent / "results" / "seagate_multi_v3"

# Hard cross-schema questions + a verified golden semantic SQL (real model
# columns) + a paraphrase that should ride the same golden via similarity.
GOLDEN_CASES = [
    {
        "qid": "Q16",
        "name": "Warm-line plated output by family",
        "question": (
            "How many patties were plated on WARM lines, company-wide, broken down "
            "by drive family?"
        ),
        "paraphrase": (
            "Per drive family, how many finished patties came off the idle "
            "(WARM-status) production lines?"
        ),
        "semantic_sql": (
            "SELECT sku.drive_family, SUM(ev.units_completed) AS plated_units "
            "FROM seagate_drive_skus sku "
            "JOIN seagate_work_orders wo ON wo.sku_id = sku.sku_id "
            "JOIN seagate_production_lines ln ON ln.line_id = wo.line_id "
            "JOIN seagate_production_events ev ON ev.work_order_id = wo.work_order_id "
            "WHERE ln.status = 'WARM' GROUP BY sku.drive_family"
        ),
    },
    {
        "qid": "Q17",
        "name": "Golden Yield — Vantage, Q4 2025",
        "question": (
            "What was the Golden Yield for the Vantage drive family in Q4 2025 "
            "(2025-10-01 to 2025-12-31)?"
        ),
        "paraphrase": (
            "Compute the Golden Yield of Vantage-family drives over the final "
            "quarter of 2025."
        ),
        "semantic_sql": (
            "SELECT SUM(ev.units_completed - ev.units_scrapped - ev.units_reworked) "
            "* 1.0 / NULLIF(SUM(ev.units_completed), 0) AS golden_yield "
            "FROM seagate_production_events ev "
            "JOIN seagate_work_orders wo ON wo.work_order_id = ev.work_order_id "
            "JOIN seagate_drive_skus sku ON sku.sku_id = wo.sku_id "
            "WHERE wo.ticket_type = 'STANDARD' AND sku.drive_family = 'Vantage' "
            "AND ev.event_date >= '2025-10-01' AND ev.event_date <= '2025-12-31'"
        ),
    },
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def client() -> ev3.AgentClientV3:
    c = ev3.AgentClientV3(
        ec.EvalConfig.from_env(schema_name="seagate_core"), schema_names=SCHEMAS
    )
    c.login()
    return c


def base_project(c: ev3.AgentClientV3) -> str:
    for p in c.list_projects():
        c.delete_project(p["id"])
    pid = c.resolve_project(create_if_missing=True)["id"]
    c.onboard(pid)
    c.activate_all(pid)
    log(f"base project {pid[:8]} models={len(c.active_model_names(pid))}")
    return pid


def grade_case(c: ev3.AgentClientV3, case: dict[str, Any], key: str) -> dict[str, Any]:
    q = case[key]
    sig = c.query_signal(q)
    verdict = score.score_result(case["qid"], sig["rows"], sig["answer_summary"])
    return {
        "verdict": verdict,
        "recalled": sig.get("recalled_examples"),
        "sql_matches_golden": ev3.sql_matches_golden(sig["sql"], case["semantic_sql"]),
        "sql": (sig.get("sql") or "")[:500],
    }


def run(repeats: int) -> dict[str, Any]:
    c = client()
    pid = base_project(c)
    results: list[dict[str, Any]] = []

    for case in GOLDEN_CASES:
        log(f"=== E16/{case['qid']} : {case['name']} ===")
        rec = {"qid": case["qid"], "name": case["name"]}

        # Baseline (no golden active).
        rec["baseline"] = {
            "question": [grade_case(c, case, "question") for _ in range(repeats)],
            "paraphrase": [grade_case(c, case, "paraphrase") for _ in range(repeats)],
        }
        log(
            f"  baseline q={[r['verdict'] for r in rec['baseline']['question']]} "
            f"para={[r['verdict'] for r in rec['baseline']['paraphrase']]}"
        )

        # Promote + activate the verified golden query.
        c.promote_golden_query(
            pid, case["question"], case["semantic_sql"], name=case["name"]
        )
        status = c.activate_golden(pid)
        gq = c.golden_queries(pid)
        log(f"  promoted golden; queries.json status={status} entries={len(gq)}")

        # With golden active.
        rec["with_golden"] = {
            "question": [grade_case(c, case, "question") for _ in range(repeats)],
            "paraphrase": [grade_case(c, case, "paraphrase") for _ in range(repeats)],
        }
        log(
            f"  w/golden q={[r['verdict'] for r in rec['with_golden']['question']]} "
            f"para={[r['verdict'] for r in rec['with_golden']['paraphrase']]} "
            f"recalled={[r['recalled'] for r in rec['with_golden']['question']]} "
            f"sqlmatch="
            f"{[r['sql_matches_golden'] for r in rec['with_golden']['question']]}"
        )
        rec["golden_status"] = status
        rec["golden_entries"] = len(gq)
        results.append(rec)

    out = {"project": pid, "repeats": repeats, "cases": results}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "golden.json").write_text(json.dumps(out, indent=2, default=str))
    log("wrote golden.json")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args(argv)
    c = client()
    # Surface the memory regime (recall needs memory ON; this guard only warns).
    h = c.health()
    log(
        f"agent health: memory/vector_index={h.get('vector_index')} "
        f"model={h.get('default_model')}"
    )
    run(args.repeats)
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
