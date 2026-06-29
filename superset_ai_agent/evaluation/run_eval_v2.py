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
"""Headless runner for the v2 cross-schema sweep (notebooks 10 + 09, scripted).

Runs the grounding ablation (``basic`` / ``context_dump`` / ``wren_base`` /
``wren_bi``) on the multi-schema fixture against a **running** agent, scores it with
the exact ground-truth scorer (incl. the cross-schema-only Q16-Q18), captures the E9
distractor-selection metrics, and writes per-condition results + a ``summary.json``.

Usage (Docker/Postgres stack, with the agent started ``WREN_MEMORY_STORE=none``)::

    cd superset_ai_agent/evaluation
    python run_eval_v2.py \
        --agent-base-url http://localhost:8090/ai-agent \
        --superset-base-url http://localhost:8090 \
        --trials 3

The orchestration in :func:`main` needs the live server; the pure helpers
(:func:`parse_args`, :func:`score_sweep`, :func:`build_headline`) are unit-tested
offline so the runner's logic is verified without a stack.
"""

from __future__ import annotations

import argparse
import json  # noqa: TID251 - standalone eval tooling, independent of Superset
import sys
from pathlib import Path
from typing import Any, Sequence

import eval_common as ec
import eval_v2 as v2
import seagate_scoring as scoring

#: Conditions that need no MDL vs. those that do (drives the run order).
NO_MDL = ("basic", "context_dump")
WITH_MDL = ("wren_base", "wren_bi")
DEFAULT_CONDITIONS = NO_MDL + WITH_MDL


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested offline)
# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v2 cross-schema eval sweep.")
    parser.add_argument("--fixture", default="seagate_multi")
    parser.add_argument("--agent-base-url", default=None)
    parser.add_argument("--superset-base-url", default=None)
    parser.add_argument("--schema-name", default="seagate_ops")
    parser.add_argument(
        "--schema-names",
        default="seagate_core,seagate_ops",
        help="Comma-separated project schema scope (excludes seagate_ref by design).",
    )
    parser.add_argument(
        "--conditions",
        default=",".join(DEFAULT_CONDITIONS),
        help="Comma-separated subset of basic,context_dump,wren_base,wren_bi.",
    )
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument(
        "--no-require-postgres",
        action="store_true",
        help="Skip the Postgres guard (the multi-schema fixture needs Postgres).",
    )
    args = parser.parse_args(argv)
    args.schema_name_list = [s for s in args.schema_names.split(",") if s]
    args.condition_list = [c for c in args.conditions.split(",") if c]
    unknown = set(args.condition_list) - set(DEFAULT_CONDITIONS)
    if unknown:
        parser.error(f"unknown condition(s): {sorted(unknown)}")
    return args


def score_sweep(results: list[dict[str, Any]]) -> tuple[int, dict[str, str]]:
    """Score one condition's results with the exact ground-truth scorer."""
    verdicts = {
        r["id"]: scoring.score_result(
            r["id"], r.get("result_rows", []), r.get("answer_summary")
        )
        for r in results
    }
    correct = sum(1 for v in verdicts.values() if v in scoring.CORRECT_VERDICTS)
    return correct, verdicts


def build_headline(
    named_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build the per-condition headline rows (total + cross-schema-only breakout)."""
    cross_only = set(scoring.CROSS_SCHEMA_ONLY)
    rows: list[dict[str, Any]] = []
    for name, results in named_results.items():
        correct, verdicts = score_sweep(results)
        cross = sum(
            1 for q in cross_only if verdicts.get(q) in scoring.CORRECT_VERDICTS
        )
        rows.append(
            {
                "condition": name,
                "correct_of_18": correct,
                "cross_schema_only_of_3": cross,
            }
        )
    return rows


def format_headline(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'condition':>16}  {'correct/18':>10}  {'xschema/3':>9}"]
    for row in rows:
        lines.append(
            f"{row['condition']:>16}  {row['correct_of_18']:>10}  "
            f"{row['cross_schema_only_of_3']:>9}"
        )
    return "\n".join(lines)


def aggregate_trials(
    trials: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Aggregate per-condition headlines across trials → mean/min/max.

    The live run showed single-trial scores swing ±4/18, so a refined result must
    report the spread, not one number. Conditions are keyed by name; a condition
    missing from some trial is averaged over the trials it appears in.
    """
    by_condition: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for headline in trials:
        for row in headline:
            name = row["condition"]
            if name not in by_condition:
                by_condition[name] = []
                order.append(name)
            by_condition[name].append(row)

    def stat(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
        vals = [r[key] for r in rows]
        return {
            "mean": round(sum(vals) / len(vals), 2),
            "min": min(vals),
            "max": max(vals),
        }

    out: list[dict[str, Any]] = []
    for name in order:
        rows = by_condition[name]
        out.append(
            {
                "condition": name,
                "trials": len(rows),
                "correct_of_18": stat(rows, "correct_of_18"),
                "cross_schema_only_of_3": stat(rows, "cross_schema_only_of_3"),
            }
        )
    return out


def format_aggregate(rows: list[dict[str, Any]]) -> str:
    header = f"{'condition':>16}  {'trials':>6}  {'correct/18 (mean[min-max])':>28}"
    lines = [header]
    for row in rows:
        c = row["correct_of_18"]
        cell = f"{c['mean']} [{c['min']}-{c['max']}]"
        lines.append(f"{row['condition']:>16}  {row['trials']:>6}  {cell:>28}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Live orchestration
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else v2.fixture_dir(args.fixture).parent.parent
        / "evaluation"
        / "results"
        / args.fixture
    )
    overrides: dict[str, Any] = {
        "schema_name": args.schema_name,
        "results_dir": out_dir,
    }
    if args.agent_base_url:
        overrides["agent_base_url"] = args.agent_base_url
    if args.superset_base_url:
        overrides["superset_base_url"] = args.superset_base_url

    config = ec.EvalConfig.from_env(**overrides)
    client = v2.AgentClientV2(config, schema_names=args.schema_name_list)
    client.login()
    pre = client.assert_eval_preconditions(
        require_postgres=not args.no_require_postgres
    )
    print("DB backend:", pre["backend"])
    for warning in pre["warnings"]:
        print("WARNING:", warning)

    fdir = v2.fixture_dir(args.fixture)
    questions = ec.parse_test_queries(fdir / "test_queries.md")
    glossary = (fdir / "bi_glossary.md").read_text(encoding="utf-8")
    manifest = v2.load_table_manifest(args.fixture)

    per_trial_headlines: list[list[dict[str, Any]]] = []
    last_selection: dict[str, Any] | None = None
    for trial in range(1, args.trials + 1):
        print(f"\n=== trial {trial}/{args.trials} ===")
        named_results, selection = run_conditions(
            client, args.condition_list, questions, glossary, manifest
        )
        headline = build_headline(named_results)
        per_trial_headlines.append(headline)
        last_selection = selection or last_selection
        print(format_headline(headline))

    aggregate = aggregate_trials(per_trial_headlines)
    summary = {
        "fixture": args.fixture,
        "schema_names": args.schema_name_list,
        "backend": pre["backend"],
        "trials": args.trials,
        "aggregate": aggregate,
        "per_trial_headlines": per_trial_headlines,
        "selection_metrics": last_selection,
        "warnings": pre["warnings"],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print("\n" + format_aggregate(aggregate))
    if last_selection:
        print("\nE9 table selection (last trial):", last_selection)
    print(f"\nwrote {out_dir / 'summary.json'}")
    return 0


def run_conditions(
    client: "v2.AgentClientV2",
    conditions: list[str],
    questions: list[dict[str, Any]],
    glossary: str,
    manifest: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any] | None]:
    """Run one full trial (fresh project) over the conditions; return results + E9."""
    print(f"archived {client.clean_baseline()} existing project(s)")
    named_results: dict[str, list[dict[str, Any]]] = {}
    pid: str | None = None
    for condition in conditions:
        if condition in WITH_MDL and pid is None:
            pid = client.resolve_project()["id"]
            client.onboard(pid)
        if condition == "wren_bi" and pid is not None:
            doc = client.create_document_from_text(pid, glossary, "bi_glossary.md")
            client.enrich_round(pid, doc["id"], wait_coverage=False)
        extra = glossary if condition == "context_dump" else None
        results = ec.run_experiment(client, condition, questions, extra_context=extra)
        named_results[condition] = results
        correct, _ = score_sweep(results)
        print(f"  {condition}: {correct}/18")
    selection = client.selection_metrics(pid, manifest) if pid else None
    return named_results, selection


if __name__ == "__main__":
    sys.exit(main())
