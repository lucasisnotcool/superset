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
"""Orchestrate the prepare stage: inputs/ -> fixture/ (steps 2a-2d in one command).

    python prepare/run_prepare.py --inputs inputs --out fixture \\
        --fixture-id oracle_v1 --database-name ORCLPDB1 [--review]

Runs (2a) BI-doc generation, (2d) target-schema inference, (2c/2b) corpus generation
with gold-SQL validation, then writes ``fixture/{fixture.yaml, questions.csv,
context/bi_context.md}``. ``--review`` writes drafts and prints a summary for the
dumber agent to approve before a real run. Schema grounding is derived from the
dumped CSV headers (real data samples) — no separate schema dump required.
"""

from __future__ import annotations

import argparse
import csv
import json  # noqa: TID251 - standalone eval tooling
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # evaluation/  (eval_common, rig, prepare)
sys.path.insert(0, str(_HERE.parents[2]))  # repo root  (superset_ai_agent.*)

import eval_common as ec  # noqa: E402
import eval_v3 as ev3  # noqa: E402
from rig import (  # noqa: E402
    harness,
    model_client as mc,
    preflight as pf,
)

from prepare import (  # noqa: E402
    _agent_pass as ap,
    prepare_bi_docs,
    prepare_corpus,
    prepare_targets,
)


def _csv_schema_hint(path: Path) -> str:
    """One line naming a CSV's columns (its header) — grounding for gold_sql."""

    with path.open(encoding="utf-8") as fh:
        header = next(csv.reader(fh), [])
    stem = path.stem
    return f"{stem}({', '.join(header)})" if header else stem


def read_inputs(inputs_dir: Path) -> tuple[str, str]:
    """Return (context_text, schema_text) from the dumped inputs folder."""

    md = sorted(inputs_dir.glob("*.md"))
    csvs = sorted(inputs_dir.glob("*.csv"))
    schema_files = [p for p in md + csvs if "schema" in p.name.lower()]
    context_text = ap.read_inputs([p for p in md if p not in schema_files])
    schema_lines = [_csv_schema_hint(p) for p in csvs if p not in schema_files]
    schema_lines += [p.read_text(encoding="utf-8") for p in schema_files]
    return context_text, "\n".join(schema_lines)


def main(argv=None) -> int:  # noqa: C901 - linear orchestration, one block per step
    ap_ = argparse.ArgumentParser(description=__doc__)
    ap_.add_argument("--inputs", default="inputs", help="folder of dumped CSVs + .md")
    ap_.add_argument("--out", default="fixture", help="fixture output folder")
    ap_.add_argument("--fixture-id", required=True)
    ap_.add_argument("--database-name", default=None)
    ap_.add_argument("--connection-uri-env", default=None)
    ap_.add_argument("--onboard-mode", default="auto")
    ap_.add_argument(
        "--review", action="store_true", help="write drafts + summary only"
    )
    ap_.add_argument(
        "--keep-invalid", action="store_true", help="keep unvalidated gold_sql"
    )
    args = ap_.parse_args(argv)

    inputs_dir = Path(args.inputs).resolve()
    out_dir = Path(args.out).resolve()
    (out_dir / "context").mkdir(parents=True, exist_ok=True)
    if not inputs_dir.is_dir():
        print(f"✗ inputs folder not found: {inputs_dir}")
        return 2
    context_text, schema_text = read_inputs(inputs_dir)
    print(
        f"inputs: {len(context_text)} chars context, schema hint: {schema_text[:120]}…"
    )

    model = mc.get_model_client()

    def chat(system, user):
        return ap.chat(model, system, user)

    # 2a — BI doc
    bi_doc = prepare_bi_docs.generate_bi_doc(context_text, chat=chat)
    (out_dir / "context" / "bi_context.md").write_text(bi_doc, encoding="utf-8")
    print(f"✓ 2a BI doc: {len(bi_doc)} chars -> context/bi_context.md")

    # Live client for schema listing + gold_sql validation.
    config = ec.EvalConfig.from_env()
    if args.database_name:
        config.database_name = args.database_name
    client = ev3.AgentClientV3(config, schema_names=[])
    execute_sql = None
    available_schemas: list[str] = []
    database_id = None
    try:
        client.login()
        database_id = client.resolve_database_id()
        available_schemas = pf.list_schemas(client, database_id)
    except Exception as ex:  # noqa: BLE001
        print(f"⚠ no live DB ({ex}); targets use context only, gold_sql unvalidated")

    # 2d — target schemas
    schemas, rationale = (
        prepare_targets.generate_targets(context_text, available_schemas, chat=chat)
        if available_schemas
        else ([], {})
    )
    if not schemas:
        print("⚠ no target schemas inferred; set them manually in fixture.yaml")
    else:
        print(f"✓ 2d target schemas: {schemas}  rationale={rationale}")

    if database_id is not None:

        def execute_sql(sql):  # noqa: E306 - closure over the live client
            return harness.execute_sql(
                client,
                database_id=database_id,
                sql=sql,
                schema=(schemas[0] if schemas else None),
            )

    # 2b/2c — corpus (generate + validate gold_sql)
    report = prepare_corpus.generate_corpus(
        context_text,
        schema_text,
        chat=chat,
        execute_sql=execute_sql,
        drop_invalid=not args.keep_invalid,
    )
    (out_dir / "questions.csv").write_text(
        prepare_corpus.to_csv(report), encoding="utf-8"
    )
    print(
        f"✓ 2b/2c corpus: {len(report.kept)} kept, {len(report.dropped)} dropped, "
        f"{len(report.flagged)} flagged, {len(report.parse_errors)} parse errors"
    )
    for line in report.dropped + report.flagged + report.parse_errors:
        print(f"    - {line}")

    # Manifest
    manifest = prepare_targets.as_manifest_dict(
        args.fixture_id,
        schemas or ["<FILL_ME>"],
        database_name=args.database_name,
        connection_uri_env=args.connection_uri_env,
        onboard_mode=args.onboard_mode,
    )
    _write_yaml(out_dir / "fixture.yaml", manifest)
    print(f"✓ wrote {out_dir}/fixture.yaml")
    if args.review:
        print(
            "\n[REVIEW] Drafts written. Inspect questions.csv + context/ then re-run "
            "without --review, or edit and run run_rig.py directly."
        )
    return 0


def _write_yaml(path: Path, data: dict) -> None:
    try:
        import yaml  # noqa: PLC0415

        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    except ImportError:  # pragma: no cover
        path.with_suffix(".json").write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    sys.exit(main())
