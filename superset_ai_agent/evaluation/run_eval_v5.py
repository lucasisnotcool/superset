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
"""v5 benchmark runner — the v4 grounding matrix + the doc-RAG channel configs.

Extends v4 (plan_sql_agent_doc_grounding_spec.md A4) with:

    wren_bi_rag·{manual,auto} — enriched layer + the SQL agent's budgeted
    document-RAG channel (WREN_SQL_DOC_CONTEXT_ENABLED) retrieving glossary
    passages, instead of dumping the whole glossary into the prompt.

Ship gate (spec §Phase A): ``wren_bi_rag ≥ wren_bi_context − 1`` at a fraction
of the tokens, with no regression on the temporal/metric capability splits.

Because the doc channel is a *server* flag, one server cannot produce both
``wren_bi`` (channel off) and ``wren_bi_rag`` (channel on) in a single run.
Instead of trusting configuration, every sweep VERIFIES the served mode from
the response timeline (the ``load_document_context`` step, A1) and counts
mismatches loudly in the results — a ``wren_bi`` sweep served with doc
passages, or a ``wren_bi_rag`` sweep served without, is flagged per question.

Pure functions (config expansion, doc-rag signal extraction, mismatch
accounting) are module-level and unit-tested offline; live orchestration
reuses the v4 trial flow.
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
import run_eval_v4 as v4
import seagate_scoring as score

OUT_DIR = Path(__file__).resolve().parent / "results" / "seagate_multi_v5"

#: The rag configs added on top of the v4 matrix (crossed with onboard type).
RAG_MODES = ("wren_bi_rag",)

#: v5 headline deltas: the retrieval-vs-dump gate and the rag lift.
V5_DELTAS: dict[str, tuple[str, str]] = {
    "rag vs dump — SHIP GATE (wren_bi_rag·auto − wren_bi_context·auto)": (
        "wren_bi_rag·auto",
        "wren_bi_context·auto",
    ),
    "rag lift over bare layer (wren_bi_rag·auto − wren_bi·auto)": (
        "wren_bi_rag·auto",
        "wren_bi·auto",
    ),
    "rag vs raw dump alone (wren_bi_rag·auto − context_dump)": (
        "wren_bi_rag·auto",
        "context_dump",
    ),
}


def expand_configs() -> list[dict[str, Any]]:
    """The 10 v5 configs: v4's 8 + wren_bi_rag x {manual, auto}."""
    configs = v4.expand_configs()
    for onboard in v4.ONBOARDS:
        for mode in RAG_MODES:
            configs.append(
                {"name": f"{mode}·{onboard}", "grounding": mode, "onboard": onboard}
            )
    return configs


def config_names() -> list[str]:
    return [c["name"] for c in expand_configs()]


def doc_rag_signal(resp: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the doc-RAG channel's activity from an agent response.

    Reads the ``load_document_context`` trace/timeline step (A1). Returns
    ``None`` when the step is absent (channel inert), else a dict with
    ``passage_count``/``document_count``/``retriever``/``truncated``. Pure —
    unit-tested offline.
    """

    events = list(resp.get("trace") or [])
    for event in events:
        if (event or {}).get("step") != "load_document_context":
            continue
        details = event.get("details") or {}
        return {
            "passage_count": int(details.get("passage_count", 0) or 0),
            "document_count": int(details.get("document_count", 0) or 0),
            "retriever": details.get("retriever"),
            "truncated": bool(details.get("truncated", False)),
        }
    return None


def doc_rag_active(resp: dict[str, Any]) -> bool:
    """Whether retrieved document passages actually grounded this response."""

    signal = doc_rag_signal(resp)
    return bool(signal and signal["passage_count"] > 0)


def mismatch(expected_rag: bool, resp: dict[str, Any]) -> bool:
    """True when the served grounding mode contradicts the config's intent."""

    return doc_rag_active(resp) != expected_rag


def grade_sweep(
    client: ev3.AgentClientV3,
    questions: list[dict[str, Any]],
    *,
    extra_context: str | None = None,
    expect_doc_rag: bool = False,
    qids: list[str] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Grade the question set once; return ``({qid: verdict}, channel audit)``.

    The audit records, per question, whether the doc-RAG channel served
    passages, plus the total count of expectation mismatches — so a
    misconfigured server (channel on during a ``wren_bi`` sweep, or off during
    ``wren_bi_rag``) is visible in the results instead of silently polluting
    the ablation.
    """

    verdicts: dict[str, str] = {}
    audit: dict[str, Any] = {"expected_doc_rag": expect_doc_rag, "by_question": {}}
    mismatches = 0
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
            signal = doc_rag_signal(resp)
            audit["by_question"][qid] = signal
            if mismatch(expect_doc_rag, resp):
                mismatches += 1
        except Exception as ex:  # noqa: BLE001
            verdicts[qid] = "error"
            v4.log(f"    {qid} ERROR {ex}")
    audit["mismatches"] = mismatches
    return verdicts, audit


def run_trial(client, glossary, questions, trial, qids=None):
    """One v5 trial: the v4 flow + the rag sweep after each enriched layer.

    ``wren_bi_rag`` reuses the enriched project as-is — the glossary document
    was already uploaded during enrichment, so the SQL agent's channel (A1)
    retrieves passages from it with NO extra_context dump.
    """

    out: dict[str, dict[str, str]] = {}
    audits: dict[str, dict[str, Any]] = {}
    for p in client.list_projects():
        client.delete_project(p["id"])
    v4.log(f"trial {trial}: basic")
    out["basic"], audits["basic"] = grade_sweep(client, questions, qids=qids)
    v4.log(f"trial {trial}: context_dump")
    out["context_dump"], audits["context_dump"] = grade_sweep(
        client, questions, extra_context=glossary, qids=qids
    )

    for onboard in v4.ONBOARDS:
        for p in client.list_projects():
            client.delete_project(p["id"])
        pid = client.resolve_project(create_if_missing=True)["id"]
        try:
            v4.build_base_layer(client, pid, onboard, glossary)
        except Exception as ex:  # noqa: BLE001
            v4.log(f"  base onboard ({onboard}) failed: {ex}")
        name = f"wren_base·{onboard}"
        v4.log(f"trial {trial}: {name}")
        out[name], audits[name] = grade_sweep(client, questions, qids=qids)
        try:
            v4.build_enriched_layer(client, pid, onboard, glossary)
        except Exception as ex:  # noqa: BLE001
            v4.log(f"  enrich ({onboard}) failed: {ex}")
        name = f"wren_bi·{onboard}"
        v4.log(f"trial {trial}: {name}")
        out[name], audits[name] = grade_sweep(client, questions, qids=qids)
        name = f"wren_bi_rag·{onboard}"
        v4.log(f"trial {trial}: {name}")
        out[name], audits[name] = grade_sweep(
            client, questions, expect_doc_rag=True, qids=qids
        )
        name = f"wren_bi_context·{onboard}"
        v4.log(f"trial {trial}: {name}")
        out[name], audits[name] = grade_sweep(
            client, questions, extra_context=glossary, qids=qids
        )
    out["_channel_audit"] = audits  # type: ignore[assignment]
    return out


def split_trials(
    raw_trials: list[dict[str, Any]],
) -> tuple[list[dict[str, dict[str, str]]], list[dict[str, Any]]]:
    """Separate verdict maps from the per-trial channel audits (pure)."""

    verdicts: list[dict[str, dict[str, str]]] = []
    audits: list[dict[str, Any]] = []
    for trial in raw_trials:
        audits.append(trial.get("_channel_audit") or {})
        verdicts.append({k: v for k, v in trial.items() if k != "_channel_audit"})
    return verdicts, audits


def total_mismatches(audits: list[dict[str, Any]]) -> int:
    """Sum of served-mode mismatches across every sweep of every trial (pure)."""

    return sum(
        int((sweep or {}).get("mismatches", 0) or 0)
        for trial in audits
        for sweep in trial.values()
    )


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
        ec.EvalConfig.from_env(schema_name="seagate_core"), schema_names=v4.SCHEMAS
    )
    client.login()
    fdir = ev2.fixture_dir("seagate_multi")
    glossary = (fdir / "bi_glossary.md").read_text()
    questions = ec.parse_test_queries(fdir / "test_queries.md")
    qids = args.questions.split(",") if args.questions else None
    raw_trials = [
        run_trial(client, glossary, questions, t + 1, qids=qids)
        for t in range(args.trials)
    ]
    verdict_trials, audits = split_trials(raw_trials)
    meta = {
        "fixture_version": "v5",
        "trials": args.trials,
        "memory": "off",
        "model": client.health().get("default_model"),
        "channel_mismatches": total_mismatches(audits),
    }
    sb = v4.build_scoreboard(
        verdict_trials,
        capability=score.CAPABILITY,
        meta=meta,
        config_names=config_names(),
        extra_deltas=V5_DELTAS,
    )
    (OUT_DIR / "scoreboard.json").write_text(json.dumps(sb, indent=2, default=str))
    (OUT_DIR / "trials.json").write_text(json.dumps(raw_trials, indent=2, default=str))
    (OUT_DIR / "channel_audit.json").write_text(
        json.dumps(audits, indent=2, default=str)
    )
    print(v4.format_scoreboard(sb).replace("# v4 scoreboard", "# v5 scoreboard"))
    return 0


if __name__ == "__main__":
    start = time.time()
    code = main()
    v4.log(f"done in {time.time() - start:.0f}s")
    sys.exit(code)
