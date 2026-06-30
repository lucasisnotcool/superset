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
"""v4 consolidated benchmark runner — the 8-config grounding matrix + scoreboard.

The matrix is ``grounding x onboard`` (EVAL_V4_SPEC.md §1):

    basic, context_dump              (no MDL, no onboard)
    wren_base/wren_bi/wren_bi_context  x  {manual, auto} onboard

Onboard is done **once per type per trial** and reused across the three wren modes
by ordering the sweep (onboard -> grade base -> enrich -> grade bi/bi_context), so a
3-trial run does 3 manual + 3 auto onboards total — never 6 per trial.

Pure functions (config expansion, capability scoring, scoreboard aggregation) are
module-level and unit-tested offline; the live orchestration reuses
:class:`eval_v3.AgentClientV3`. Memory is OFF for the grounding matrix (clean F1
ablation); the golden/shared-memory probes run separately (run_eval_v3_golden.py).
"""

from __future__ import annotations

import argparse
import json  # noqa: TID251 - standalone eval tooling
import sys
import time
from pathlib import Path
from typing import Any

import eval_common as ec
import eval_v2 as ev2
import eval_v3 as ev3
import seagate_scoring as score

OUT_DIR = Path(__file__).resolve().parent / "results" / "seagate_multi_v4"
SCHEMAS = ["seagate_core", "seagate_ops", "seagate_supply"]

#: The grounding modes that use a Wren MDL (each crossed with the onboard dimension).
WREN_MODES = ("wren_base", "wren_bi", "wren_bi_context")
NON_WREN_MODES = ("basic", "context_dump")
ONBOARDS = ("manual", "auto")


def expand_configs() -> list[dict[str, Any]]:
    """The 8 configs: 2 non-wren + 3 wren x 2 onboard (EVAL_V4_SPEC.md §1)."""
    configs: list[dict[str, Any]] = [
        {"name": m, "grounding": m, "onboard": None} for m in NON_WREN_MODES
    ]
    for onboard in ONBOARDS:
        for mode in WREN_MODES:
            configs.append(
                {"name": f"{mode}·{onboard}", "grounding": mode, "onboard": onboard}
            )
    return configs


# --------------------------------------------------------------------------- #
# Pure scoring / scoreboard
# --------------------------------------------------------------------------- #
def grade_verdicts(graded: list[dict[str, Any]]) -> dict[str, str]:
    """``{qid: verdict}`` for a sweep's per-question results."""
    return {g["id"]: g["verdict"] for g in graded}


def total_correct(verdicts: dict[str, str]) -> int:
    return sum(1 for v in verdicts.values() if v in score.CORRECT_VERDICTS)


def capability_scores(
    verdicts: dict[str, str], capability: dict[str, tuple[str, ...]]
) -> dict[str, list[int]]:
    """Per-capability ``[correct, total]`` over the questions carrying each tag."""
    out: dict[str, list[int]] = {}
    for qid, tags in capability.items():
        if qid not in verdicts:
            continue
        ok = 1 if verdicts[qid] in score.CORRECT_VERDICTS else 0
        for tag in tags:
            cell = out.setdefault(tag, [0, 0])
            cell[0] += ok
            cell[1] += 1
    return out


def _stat(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(sum(values) / len(values), 2) if values else 0.0,
        "min": min(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


def build_scoreboard(
    trials: list[dict[str, dict[str, str]]],
    *,
    capability: dict[str, tuple[str, ...]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate per-trial ``{config: {qid: verdict}}`` into the v4 scoreboard.

    Output: ``by_config`` (total mean[min-max] + per-capability mean correct),
    ``by_capability`` (each config's mean correct), and headline ``deltas``.
    """
    config_names: list[str] = [c["name"] for c in expand_configs()]
    by_config: dict[str, Any] = {}
    cap_table: dict[str, dict[str, float]] = {}

    for name in config_names:
        totals = [total_correct(t[name]) for t in trials if name in t]
        caps_per_trial = [
            capability_scores(t[name], capability) for t in trials if name in t
        ]
        cap_mean: dict[str, str] = {}
        for tag in sorted({tg for cpt in caps_per_trial for tg in cpt}):
            corrects = [cpt.get(tag, [0, 0])[0] for cpt in caps_per_trial]
            total = next((cpt[tag][1] for cpt in caps_per_trial if tag in cpt), 0)
            mean_correct = round(sum(corrects) / len(corrects), 2) if corrects else 0.0
            cap_mean[tag] = f"{mean_correct}/{total}"
            cap_table.setdefault(tag, {})[name] = mean_correct
        by_config[name] = {
            "total": _stat([float(x) for x in totals]),
            "by_capability": cap_mean,
            "trials": len(totals),
        }

    def delta(a: str, b: str) -> float | None:
        if a in by_config and b in by_config:
            return round(
                by_config[a]["total"]["mean"] - by_config[b]["total"]["mean"], 2
            )
        return None

    deltas = {
        "enrichment (wren_bi·auto − wren_base·auto)": delta(
            "wren_bi·auto", "wren_base·auto"
        ),
        "auto vs manual onboard (wren_bi·auto − wren_bi·manual)": delta(
            "wren_bi·auto", "wren_bi·manual"
        ),
        "context on top of layer (wren_bi_context·auto − wren_bi·auto)": delta(
            "wren_bi_context·auto", "wren_bi·auto"
        ),
        "layer vs raw context (wren_bi·auto − context_dump)": delta(
            "wren_bi·auto", "context_dump"
        ),
    }
    return {
        "meta": meta,
        "by_config": by_config,
        "by_capability": cap_table,
        "deltas": deltas,
    }


def format_scoreboard(sb: dict[str, Any]) -> str:
    lines = [f"# v4 scoreboard — {sb['meta']}", "", "## Total correct (mean[min-max])"]
    for name, row in sb["by_config"].items():
        t = row["total"]
        lines.append(f"  {name:>24}  {t['mean']:>5} [{int(t['min'])}-{int(t['max'])}]")
    lines.append("\n## Headline deltas")
    for k, v in sb["deltas"].items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Live orchestration (Phase 1 — needs the stack; memory OFF)
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def grade_sweep(
    client: ev3.AgentClientV3,
    questions: list[dict[str, Any]],
    *,
    extra_context: str | None = None,
    qids: list[str] | None = None,
) -> dict[str, str]:
    """Grade the question set once; return ``{qid: verdict}``."""
    verdicts: dict[str, str] = {}
    by_id = {q["id"]: q for q in questions}
    for qid in qids or list(score.EXPECTED):
        q = by_id.get(qid)
        if q is None:
            continue
        try:
            resp = client.query(
                q["question"], execute=True, extra_context=extra_context
            )
            rows = (resp.get("execution_result") or {}).get("rows", [])
            verdicts[qid] = score.score_result(qid, rows, resp.get("answer_summary"))
        except Exception as ex:  # noqa: BLE001
            verdicts[qid] = "error"
            log(f"    {qid} ERROR {ex}")
    return verdicts


#: Copilot seed that onboards selectively but does NOT enrich (the auto "base"
#: snapshot — symmetric with the deterministic /onboard base for manual).
AUTO_STRUCTURE_ONLY_MSG = (
    "Read the attached document(s) and onboard ONLY the tables they describe from "
    "this database as base models — structure (columns + relationships) only. Do "
    "NOT add metrics, synonyms, or definitions yet. Show me one changeset to review."
)


def manual_enrich(client: ev3.AgentClientV3, pid: str, glossary: str) -> None:
    """Deterministic enrich: upload the glossary as a doc, enrich, activate."""
    doc = client.create_document_from_text(pid, glossary, "bi_glossary.md")
    doc_id = doc.get("id") or doc.get("document_id")
    proposal = client.enrich(pid, doc_id)
    client.apply_enrichment(pid, proposal)


def build_base_layer(client: ev3.AgentClientV3, pid: str, onboard: str, glossary: str):
    if onboard == "manual":
        client.onboard(pid)
        client.activate_all(pid)
    else:  # auto: Copilot selective onboard, structure only
        client.create_document_from_text(pid, glossary, "bi_glossary.md")
        client.copilot_build(pid, AUTO_STRUCTURE_ONLY_MSG, glossary=glossary)


def build_enriched_layer(
    client: ev3.AgentClientV3, pid: str, onboard: str, glossary: str
):
    if onboard == "manual":
        manual_enrich(client, pid, glossary)
    else:  # auto: a Copilot enrichment pass on the base layer
        client.copilot_enrich_pass(pid, glossary)


def run_trial(client, glossary, questions, trial, qids=None):
    """One trial across all 8 configs, onboarding once per type (snapshot-reuse)."""
    out: dict[str, dict[str, str]] = {}
    # --- non-wren (no project) ---
    for p in client.list_projects():
        client.delete_project(p["id"])
    log(f"trial {trial}: basic")
    out["basic"] = grade_sweep(client, questions, qids=qids)
    log(f"trial {trial}: context_dump")
    out["context_dump"] = grade_sweep(
        client, questions, extra_context=glossary, qids=qids
    )

    for onboard in ONBOARDS:
        for p in client.list_projects():
            client.delete_project(p["id"])
        pid = client.resolve_project(create_if_missing=True)["id"]
        # BASE layer (structure only) — graded before enrichment supersedes it.
        try:
            build_base_layer(client, pid, onboard, glossary)
        except Exception as ex:  # noqa: BLE001
            log(f"  base onboard ({onboard}) failed: {ex}")
        log(f"trial {trial}: wren_base·{onboard}")
        out[f"wren_base·{onboard}"] = grade_sweep(client, questions, qids=qids)
        # ENRICHED layer — reuse for both wren_bi and wren_bi_context.
        try:
            build_enriched_layer(client, pid, onboard, glossary)
        except Exception as ex:  # noqa: BLE001
            log(f"  enrich ({onboard}) failed: {ex}")
        log(f"trial {trial}: wren_bi·{onboard}")
        out[f"wren_bi·{onboard}"] = grade_sweep(client, questions, qids=qids)
        log(f"trial {trial}: wren_bi_context·{onboard}")
        out[f"wren_bi_context·{onboard}"] = grade_sweep(
            client, questions, extra_context=glossary, qids=qids
        )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument(
        "--dry-run", action="store_true", help="print the config matrix only"
    )
    ap.add_argument(
        "--questions", default=None, help="comma-separated qid subset (smoke)"
    )
    args = ap.parse_args(argv)
    if args.dry_run:
        for c in expand_configs():
            print(c)
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = ev3.AgentClientV3(
        ec.EvalConfig.from_env(schema_name="seagate_core"), schema_names=SCHEMAS
    )
    client.login()
    fdir = ev2.fixture_dir("seagate_multi")
    glossary = (fdir / "bi_glossary.md").read_text()
    questions = ec.parse_test_queries(fdir / "test_queries.md")
    qids = args.questions.split(",") if args.questions else None
    trials = [
        run_trial(client, glossary, questions, t + 1, qids=qids)
        for t in range(args.trials)
    ]
    meta = {
        "fixture_version": "v4",
        "trials": args.trials,
        "memory": "off",
        "model": client.health().get("default_model"),
    }
    sb = build_scoreboard(trials, capability=score.CAPABILITY, meta=meta)
    (OUT_DIR / "scoreboard.json").write_text(json.dumps(sb, indent=2, default=str))
    (OUT_DIR / "trials.json").write_text(json.dumps(trials, indent=2, default=str))
    print(format_scoreboard(sb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
