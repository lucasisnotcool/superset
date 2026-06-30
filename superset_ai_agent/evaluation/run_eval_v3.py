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
"""Headless orchestrator for the v3 (Views + Golden Queries) live experiments.

Two memory regimes (operator toggles ``WREN_MEMORY_STORE`` out of band):

* ``views``  (memory OFF)  — E13 view authoring, E14 query-time lift,
  E15 native-vs-semantic authoring gate.
* ``golden`` (memory ON)   — E16 golden-recall accuracy lift, E17 recall signal.

Usage::

    EVAL_AGENT_BASE_URL=http://localhost:8090/ai-agent \\
    EVAL_SUPERSET_BASE_URL=http://localhost:8090 \\
    python run_eval_v3.py views --trials 2

Results land in ``results/seagate_multi_v3/<phase>.json``.
"""

from __future__ import annotations

import argparse
import json  # noqa: TID251 - standalone eval tooling
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import eval_common as ec
import eval_v2 as ev2
import eval_v3 as ev3
import seagate_scoring as score

SCHEMAS = ["seagate_core", "seagate_ops"]
PHYS_SCHEMAS = ["seagate_core", "seagate_ops", "seagate_ref"]
OUT_DIR = Path(__file__).resolve().parent / "results" / "seagate_multi_v3"
CROSS = ("Q16", "Q17", "Q18")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def client() -> ev3.AgentClientV3:
    cfg = ec.EvalConfig.from_env(schema_name="seagate_core")
    c = ev3.AgentClientV3(cfg, schema_names=SCHEMAS)
    c.login()
    return c


def fresh_base_project(c: ev3.AgentClientV3, *, deterministic: bool = True) -> str:
    """Resolve a brand-new project and onboard a base manifest, activated.

    Deterministic ``/onboard`` (models every in-schema table) is the controlled
    base for the view experiments: it isolates *view authoring* from the
    auto-onboard selection variance measured separately in v2 (E11).
    """
    for p in c.list_projects():
        c.delete_project(p["id"])
    proj = c.resolve_project(create_if_missing=True)
    pid = proj["id"]
    if deterministic:
        c.onboard(pid)
        c.activate_all(pid)
    log(f"  base project {pid[:8]} models={sorted(c.active_model_names(pid))}")
    return pid


def grade_questions(
    c: ev3.AgentClientV3,
    qids: tuple[str, ...],
    questions: list[dict[str, Any]],
    *,
    view_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    by_id = {q["id"]: q for q in questions}
    out = []
    for qid in qids:
        q = by_id[qid]
        try:
            sig = c.query_signal(q["question"])
            verdict = score.score_result(qid, sig["rows"], sig["answer_summary"])
            used = ev3.sql_uses_any(sig["sql"], view_names or [])
            out.append(
                {
                    "id": qid,
                    "verdict": verdict,
                    "used_views": used,
                    "recalled": sig.get("recalled_examples"),
                    "sql": (sig.get("sql") or "")[:600],
                }
            )
            log(f"    {qid}: {verdict}  used_views={used}")
        except Exception as ex:  # noqa: BLE001
            out.append({"id": qid, "verdict": "error", "error": str(ex)})
            log(f"    {qid}: ERROR {ex}")
    return out


# --------------------------------------------------------------------------- #
# E13 — view authoring quality (semantic addendum)
# E15 — native-vs-semantic gate (raw-SQL addendum)
# --------------------------------------------------------------------------- #
def authoring_trial(c: ev3.AgentClientV3, doc: str, label: str) -> dict[str, Any]:
    pid = fresh_base_project(c)
    log(f"  [{label}] authoring views on {pid[:8]} ...")
    res = c.author_views(pid, doc)
    active = res["active_views"]
    proposed = res["proposed_views"]
    metrics_active = ev3.view_authoring_metrics(active, physical_schemas=PHYS_SCHEMAS)
    metrics_proposed = ev3.view_authoring_metrics(
        proposed, physical_schemas=PHYS_SCHEMAS
    )
    out = {
        "project": pid,
        "items": res.get("items"),
        "applied": res.get("applied"),
        "activated": res.get("activated"),
        "activate_error": res.get("activate_error"),
        "copilot_error": res.get("error"),
        "active_view_metrics": metrics_active,
        "proposed_view_metrics": metrics_proposed,
        "active_view_names": metrics_active["names"],
    }
    log(
        f"  [{label}] proposed={metrics_proposed['count']} "
        f"active={metrics_active['count']} semantic={metrics_active['semantic']} "
        f"native={metrics_active['native']} "
        f"desc={metrics_active['with_description']} "
        f"phys_leak={metrics_active['semantic_referencing_physical_schema']} "
        f"activate_error={bool(res.get('activate_error'))}"
    )
    return out


def run_authoring(c: ev3.AgentClientV3, trials: int) -> dict[str, Any]:
    fdir = ev2.fixture_dir("seagate_multi")
    glossary = (fdir / "bi_glossary.md").read_text()
    views_addendum = (fdir / "views_addendum.md").read_text()
    raw_doc = (fdir / "rawsql_addendum.md").read_text()
    # E13: the realistic path — the agent has the glossary (slang→column mappings,
    # metric defs) AND the standard-reports addendum, and must combine them to
    # author correct semantic views.
    e13_doc = f"{glossary}\n\n{views_addendum}"
    e13 = []
    for t in range(trials):
        log(f"E13 trial {t + 1}/{trials} (glossary + standard-reports addendum)")
        e13.append(_safe(authoring_trial, c, e13_doc, f"E13.{t + 1}"))
    # E15: the native-vs-semantic gate — correct raw physical SQL; the only burden
    # is physical→model name substitution.
    e15 = []
    for t in range(trials):
        log(f"E15 trial {t + 1}/{trials} (correct raw-SQL addendum)")
        e15.append(_safe(authoring_trial, c, raw_doc, f"E15.{t + 1}"))
    return {"E13_glossary_plus_addendum": e13, "E15_rawsql_addendum": e15}


#: Two known-VALID semantic views (real model columns) for the E14 lift test, so
#: the measurement isolates a view's *query-time value* from authoring reliability.
VALID_VIEWS = {
    "views": [
        {
            "name": "warm_line_output_by_family",
            "statement": (
                "SELECT sku.drive_family, SUM(ev.units_completed) AS plated_units "
                "FROM seagate_drive_skus sku "
                "JOIN seagate_work_orders wo ON wo.sku_id = sku.sku_id "
                "JOIN seagate_production_lines ln ON ln.line_id = wo.line_id "
                "JOIN seagate_production_events ev "
                "ON ev.work_order_id = wo.work_order_id "
                "WHERE ln.status = 'WARM' GROUP BY sku.drive_family"
            ),
            "properties": {
                "description": (
                    "Plated patties (units_completed) produced on WARM (idle) "
                    "production lines, by drive family. Cross-schema."
                )
            },
        },
        {
            "name": "standard_golden_yield_by_family",
            "statement": (
                "SELECT sku.drive_family, "
                "SUM(ev.units_completed - ev.units_scrapped - ev.units_reworked) "
                "* 1.0 / NULLIF(SUM(ev.units_completed), 0) AS golden_yield "
                "FROM seagate_production_events ev "
                "JOIN seagate_work_orders wo "
                "ON wo.work_order_id = ev.work_order_id "
                "JOIN seagate_drive_skus sku ON sku.sku_id = wo.sku_id "
                "WHERE wo.ticket_type = 'STANDARD' GROUP BY sku.drive_family"
            ),
            "properties": {
                "description": (
                    "Golden Yield = (completed-scrapped-reworked)/completed over "
                    "STANDARD tickets, by drive family. Cross-schema."
                )
            },
        },
    ]
}


# --------------------------------------------------------------------------- #
# E14 — query-time lift (views active vs deactivated on the SAME project)
# --------------------------------------------------------------------------- #
def run_query_lift(c: ev3.AgentClientV3, repeats: int) -> dict[str, Any]:
    questions = ec.parse_test_queries(
        ev2.fixture_dir("seagate_multi") / "test_queries.md"
    )
    pid = fresh_base_project(c)
    view_names = [v["name"] for v in VALID_VIEWS["views"]]

    # Baseline FIRST (no views), then add the known-valid views and re-grade — the
    # same project + models both times, so the only delta is the view's presence.
    without_views = []
    for r in range(repeats):
        log(f"E14 without-views pass {r + 1}/{repeats}")
        without_views.append(
            grade_questions(c, CROSS, questions, view_names=view_names)
        )

    log("E14: creating + activating known-valid views ...")
    vf = c.create_mdl_file(
        pid,
        "views/standard_reports.json",
        json.dumps(VALID_VIEWS),
        source_type="manual",
    )
    c.update_mdl_file(pid, vf["id"], status="active")
    active = [v.get("name") for v in c.active_views(pid)]
    log(f"E14: active views now = {active}")

    with_views = []
    for r in range(repeats):
        log(f"E14 with-views pass {r + 1}/{repeats}")
        with_views.append(grade_questions(c, CROSS, questions, view_names=view_names))

    return {
        "project": pid,
        "valid_views_activated": active,
        "without_views": without_views,
        "with_views": with_views,
    }


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as ex:  # noqa: BLE001
        log(f"  !! trial failed: {ex}")
        traceback.print_exc()
        return {"error": str(ex)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["views", "query_lift", "all"])
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = client()
    pre = c.assert_eval_preconditions(require_postgres=True)
    log(f"preconditions: backend={pre['backend']}  warnings={pre['warnings']}")

    if args.phase in ("views", "all"):
        log("=== Regime 1: VIEW AUTHORING (E13/E15) — memory OFF ===")
        res = run_authoring(c, args.trials)
        (OUT_DIR / "authoring.json").write_text(json.dumps(res, indent=2, default=str))
        log("wrote authoring.json")
    if args.phase in ("query_lift", "all"):
        log("=== Regime 1: VIEW QUERY-TIME LIFT (E14) — memory OFF ===")
        res = run_query_lift(c, args.repeats)
        (OUT_DIR / "query_lift.json").write_text(json.dumps(res, indent=2, default=str))
        log("wrote query_lift.json")
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
