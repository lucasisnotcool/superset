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
"""Generic entrypoint for the reusable rig — one fixture, one command.

    python run_rig.py --fixture path/to/fixture.yaml [--trials 3] [--validate]

``--validate`` runs the preflight + corpus parse and exits **without** calling the
agent — the dumber agent's dry-run. Reads endpoints/credentials from ``EVAL_*`` env
(see ``eval_common.EvalConfig``); memory regime is recorded, never toggled here.
"""

from __future__ import annotations

import argparse
import json  # noqa: TID251 - standalone eval tooling
import sys
from pathlib import Path

# Make both the eval dir (for ``eval_common``/``rig``) and the repo root (for
# ``superset_ai_agent.*``, which the rig's scorers import) importable when run as a
# script from anywhere (plan §7 packaging risk).
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1]))

import eval_common as ec  # noqa: E402
import eval_v3 as ev3  # noqa: E402
from rig import (  # noqa: E402
    corpus as corpus_mod,
    fixture as fx,
    harness,
    model_client as mc,
    preflight as pf,
)


def _print_report(report: pf.PreflightReport) -> None:
    for w in report.warnings:
        print(f"  ⚠ {w}")
    for p in report.problems:
        print(f"  ✗ {p}")
    if report.ok:
        print(f"  ✓ preflight OK (database_id={report.database_id})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", required=True, help="path to fixture.yaml/.json")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument(
        "--validate",
        action="store_true",
        help="preflight + corpus parse only; no agent calls",
    )
    ap.add_argument("--questions", default=None, help="comma-separated qid subset")
    args = ap.parse_args(argv)

    fixture = fx.load_fixture(args.fixture)
    corpus_load = corpus_mod.load_corpus_csv(fixture.corpus_path)
    print(
        f"fixture={fixture.id} schemas={list(fixture.schemas)} "
        f"corpus={len(corpus_load.records)} items "
        f"({len(corpus_load.errors)} errors, {len(corpus_load.warnings)} warnings)"
    )

    config = ec.EvalConfig.from_env(schema_name=fixture.schemas[0])
    if fixture.database_name:
        config.database_name = fixture.database_name
    if fixture.database_id is not None:
        config.database_id = fixture.database_id
    client = ev3.AgentClientV3(config, schema_names=list(fixture.schemas))

    # Best-effort auth + model client (judge/preflight readiness).
    try:
        client.login()
    except Exception as ex:  # noqa: BLE001
        print(f"  ✗ auth: {ex}")
        return 2
    model_client = None
    try:
        model_client = mc.get_model_client()
    except Exception as ex:  # noqa: BLE001
        print(f"  ⚠ model client unavailable (judge/eval_note will need_review): {ex}")
    judge = mc.judge_settings()

    report = pf.run_preflight(
        client, fixture, corpus_load, model_client=model_client, judge=judge
    )
    _print_report(report)
    if args.validate:
        return 0 if report.ok else 1
    if not report.ok:
        print("Aborting: preflight failed.")
        return 1

    qids = args.questions.split(",") if args.questions else None
    meta_extra = {"model": client.health().get("default_model"), "memory": "unverified"}
    scoreboard, trials = harness.run(
        client,
        fixture,
        corpus_load.records,
        trials=args.trials,
        model_client=model_client,
        judge=judge,
        qids=qids,
        meta_extra=meta_extra,
    )
    out_dir = _HERE / "results" / fixture.id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scoreboard.json").write_text(
        json.dumps(scoreboard, indent=2, default=str)
    )
    (out_dir / "trials.json").write_text(json.dumps(trials, indent=2, default=str))
    print(harness.v4.format_scoreboard(scoreboard).replace("# v4", "# rig"))
    print(f"\nwrote {out_dir}/scoreboard.json + trials.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
