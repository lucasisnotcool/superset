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
"""Fixture-agnostic experiment runner.

Generalises ``run_eval_v4.run_trial``/``grade_sweep`` to iterate an arbitrary
corpus against fixture-declared grounding modes, execute gold SQL for ``gold_sql``
items, dispatch to :mod:`rig.scoring`, and aggregate with the reused scoreboard math
(``run_eval_v4.total_correct`` / ``capability_scores`` / ``_stat``). No Seagate or
schema constants live here — everything comes from the :class:`~rig.fixture.Fixture`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import run_eval_v4 as v4  # reused pure scoreboard math + layer-build helpers

from rig import scoring
from rig.corpus import QuestionRecord
from rig.fixture import Fixture
from rig.model_client import JudgeSettings

WREN_MODES = ("wren_base", "wren_bi", "wren_bi_context")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested offline)
# --------------------------------------------------------------------------- #
def capability_map(records: list[QuestionRecord]) -> dict[str, tuple[str, ...]]:
    return {r.id: r.capability_tags for r in records}


def context_text(fixture: Fixture) -> str:
    """Concatenate the fixture's context docs (the ``context_dump`` / enrich text)."""

    return "\n\n".join(p.read_text(encoding="utf-8") for p in fixture.context_docs)


def build_scoreboard(
    trials: list[dict[str, dict[str, str]]],
    *,
    capability: dict[str, tuple[str, ...]],
    meta: dict[str, Any],
    config_names: list[str],
) -> dict[str, Any]:
    """Assemble the scoreboard, reusing v4's counting math with rig deltas.

    Same shape as ``run_eval_v4.build_scoreboard`` (by_config / by_capability /
    deltas) but the deltas are fixture-appropriate (single onboard mode, so no
    ``·auto`` suffixes) and any absent-config delta is simply omitted.
    """

    by_config: dict[str, Any] = {}
    cap_table: dict[str, dict[str, float]] = {}
    for name in config_names:
        totals = [v4.total_correct(t[name]) for t in trials if name in t]
        caps_per_trial = [
            v4.capability_scores(t[name], capability) for t in trials if name in t
        ]
        cap_mean: dict[str, str] = {}
        for tag in sorted({tg for cpt in caps_per_trial for tg in cpt}):
            corrects = [cpt.get(tag, [0, 0])[0] for cpt in caps_per_trial]
            total = next((cpt[tag][1] for cpt in caps_per_trial if tag in cpt), 0)
            mean_correct = round(sum(corrects) / len(corrects), 2) if corrects else 0.0
            cap_mean[tag] = f"{mean_correct}/{total}"
            cap_table.setdefault(tag, {})[name] = mean_correct
        by_config[name] = {
            "total": v4._stat([float(x) for x in totals]),
            "by_capability": cap_mean,
            "trials": len(totals),
        }

    def delta(a: str, b: str) -> float | None:
        if a in by_config and b in by_config:
            return round(
                by_config[a]["total"]["mean"] - by_config[b]["total"]["mean"], 2
            )
        return None

    candidate = {
        "enrichment (wren_bi − wren_base)": ("wren_bi", "wren_base"),
        "context on top of layer (wren_bi_context − wren_bi)": (
            "wren_bi_context",
            "wren_bi",
        ),
        "layer vs raw context (wren_bi − context_dump)": ("wren_bi", "context_dump"),
        "context lift (context_dump − basic)": ("context_dump", "basic"),
    }
    deltas = {
        label: delta(a, b)
        for label, (a, b) in candidate.items()
        if delta(a, b) is not None
    }
    return {
        "meta": meta,
        "by_config": by_config,
        "by_capability": cap_table,
        "deltas": deltas,
    }


# --------------------------------------------------------------------------- #
# Live orchestration
# --------------------------------------------------------------------------- #
@dataclass
class RunContext:
    """Everything a grade sweep needs beyond the corpus (keeps signatures small)."""

    client: Any
    database_id: int
    schema: str | None
    model_client: Any | None = None
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    qids: list[str] | None = None


def execute_sql(
    client: Any,
    *,
    database_id: int,
    sql: str,
    schema: str | None = None,
    catalog: str | None = None,
    limit: int = 1000,
) -> scoring.GoldResult:
    """Execute raw gold SQL via Superset SQL Lab (mirrors rest.py execute_sql_raw)."""

    payload = {
        "database_id": database_id,
        "sql": sql,
        "catalog": catalog,
        "schema": schema,
        "queryLimit": limit,
        "runAsync": False,
        "expand_data": True,
    }
    resp = client._ok(
        client._superset("POST", "/api/v1/sqllab/execute/", json=payload),
        "POST /api/v1/sqllab/execute/",
    )
    query = resp.get("query")
    if isinstance(query, dict) and query.get("resultsKey") and "data" not in resp:
        key = query["resultsKey"]
        resp = client._ok(
            client._superset(
                "GET", "/api/v1/sqllab/results/", params={"q": f"(key:{key})"}
            ),
            "GET /api/v1/sqllab/results/",
        )
    rows = resp.get("data") or []
    columns = [
        (c.get("name") or c.get("column_name"))
        for c in (resp.get("columns") or [])
        if isinstance(c, dict)
    ]
    return scoring.GoldResult(columns=[c for c in columns if c], rows=rows)


def score_record(
    ctx: RunContext, record: QuestionRecord, *, extra_context: str | None
) -> scoring.ScoreOutcome:
    """Answer one question and score it (executing gold SQL when needed)."""

    resp = ctx.client.query(record.question, execute=True, extra_context=extra_context)
    answer = scoring.answer_from_response(resp)
    gold: scoring.GoldResult | None = None
    if record.answer_type == "gold_sql":
        try:
            gold = execute_sql(
                ctx.client,
                database_id=ctx.database_id,
                sql=str(record.answer_spec.get("sql") or ""),
                schema=ctx.schema,
            )
        except Exception as ex:  # noqa: BLE001 - a broken gold query is a wrong item
            return scoring.ScoreOutcome(
                scoring.ERROR, [f"gold SQL execution failed: {ex}"]
            )
    return scoring.score_item(
        answer_type=record.answer_type,
        answer_spec=record.answer_spec,
        question=record.question,
        answer=answer,
        gold=gold,
        model_client=ctx.model_client,
        judge_enabled=ctx.judge.enabled,
        judge_votes=ctx.judge.votes,
        judge_model=ctx.judge.model,
    )


def grade_sweep(
    ctx: RunContext,
    records: list[QuestionRecord],
    *,
    extra_context: str | None = None,
) -> dict[str, str]:
    """Grade the corpus once; return ``{qid: verdict}``."""

    verdicts: dict[str, str] = {}
    for record in records:
        if ctx.qids and record.id not in ctx.qids:
            continue
        try:
            verdicts[record.id] = score_record(
                ctx, record, extra_context=extra_context
            ).verdict
        except Exception as ex:  # noqa: BLE001 - capture, never abort the sweep
            verdicts[record.id] = scoring.ERROR
            log(f"    {record.id} ERROR {ex}")
    return verdicts


def _clean_projects(client: Any) -> None:
    for p in client.list_projects():
        client.delete_project(p["id"])


def run_trial(  # noqa: C901 - one block per grounding mode (basic/dump/base/bi/bi_ctx)
    ctx: RunContext,
    fixture: Fixture,
    records: list[QuestionRecord],
    *,
    trial: int,
) -> dict[str, dict[str, str]]:
    """One trial across the fixture's grounding modes (project built once)."""

    ctx_text = context_text(fixture) or ""
    modes = list(fixture.grounding_modes)
    out: dict[str, dict[str, str]] = {}
    _clean_projects(ctx.client)

    if "basic" in modes:
        log(f"trial {trial}: basic")
        out["basic"] = grade_sweep(ctx, records)
    if "context_dump" in modes:
        log(f"trial {trial}: context_dump")
        out["context_dump"] = grade_sweep(ctx, records, extra_context=ctx_text or None)

    wren = [m for m in modes if m in WREN_MODES]
    if wren:
        if fixture.onboard_mode == "none":
            log("wren modes requested but onboard_mode=none — skipping")
            return out
        _clean_projects(ctx.client)
        pid = ctx.client.resolve_project(create_if_missing=True)["id"]
        try:
            v4.build_base_layer(ctx.client, pid, fixture.onboard_mode, ctx_text)
        except Exception as ex:  # noqa: BLE001
            log(f"  base onboard ({fixture.onboard_mode}) failed: {ex}")
        if "wren_base" in wren:
            log(f"trial {trial}: wren_base")
            out["wren_base"] = grade_sweep(ctx, records)
        if any(m in wren for m in ("wren_bi", "wren_bi_context")):
            try:
                v4.build_enriched_layer(ctx.client, pid, fixture.onboard_mode, ctx_text)
            except Exception as ex:  # noqa: BLE001
                log(f"  enrich ({fixture.onboard_mode}) failed: {ex}")
            if "wren_bi" in wren:
                log(f"trial {trial}: wren_bi")
                out["wren_bi"] = grade_sweep(ctx, records)
            if "wren_bi_context" in wren:
                log(f"trial {trial}: wren_bi_context")
                out["wren_bi_context"] = grade_sweep(
                    ctx, records, extra_context=ctx_text or None
                )
    return out


def run(
    client: Any,
    fixture: Fixture,
    records: list[QuestionRecord],
    *,
    trials: int,
    model_client: Any | None,
    judge: JudgeSettings,
    qids: list[str] | None = None,
    meta_extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, dict[str, str]]]]:
    """Run all trials and return ``(scoreboard, trials)``."""

    database_id = client.resolve_database_id()
    schema = fixture.schemas[0] if fixture.schemas else None
    ctx = RunContext(
        client=client,
        database_id=database_id,
        schema=schema,
        model_client=model_client,
        judge=judge,
        qids=qids,
    )
    trial_data = [run_trial(ctx, fixture, records, trial=t + 1) for t in range(trials)]
    present = [m for m in fixture.grounding_modes if any(m in t for t in trial_data)]
    meta = {
        "fixture_id": fixture.id,
        "trials": trials,
        "grounding_modes": present,
        "onboard_mode": fixture.onboard_mode,
        **(meta_extra or {}),
    }
    sb = build_scoreboard(
        trial_data, capability=capability_map(records), meta=meta, config_names=present
    )
    return sb, trial_data
