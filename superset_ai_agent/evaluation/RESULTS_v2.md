<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Semantic-Layer Evaluation v2 — Results & Findings (scaffold)

**Status:** scaffold — the harness, fixture, and notebooks are built and unit-tested;
the **live numbers are not yet filled in** (they require a running Postgres/Docker
stack with `WREN_MEMORY_STORE=none`). This file is the template the live run writes
into, plus the run metadata that makes drift detectable. Companion spec:
[`EVAL_V2_SPEC.md`](EVAL_V2_SPEC.md). Legacy run: [`RESULTS.md`](RESULTS.md).

## Run metadata (fill in at run time — this is what makes results comparable)

| Field | Value |
| --- | --- |
| Date | 2026-06-29 |
| Stack | Docker / Postgres (`examples` DB backend = `postgresql`) |
| Agent provider / model | OpenAI `gpt-4.1-mini` |
| `WREN_MEMORY_STORE` | `none` (learning `false`) — verified in-container ✓ (R1) |
| Enrichment prompt commit | `89afa141c7` (2026-06-27) — post-drift (R2) |
| `wren_copilot_coverage_votes` | `1` (default) |
| Fixture | `seagate_multi` (14 tables, 3 schemas: seagate_core/ops/ref) |
| Trials per condition | **1** (first live run — see "trials caveat" below) |

> **Trials caveat.** The headline below is a **single trial**. The legacy run
> averaged 3 (LLM variance ±1–2/18). Treat the per-question matrix as indicative,
> not final; the multi-trial sweep is the follow-up. Aggregate *ordering* is the
> trustworthy signal, not any single cell.

> ⚠️ Carry forward every caveat from [`RESULTS.md`](RESULTS.md): disable the learning
> loop (F1), expect ±1–2 run-to-run variance (F9), and remember number-match ≠
> logic-correct (F6). The v2 fixture's split does **not** change those.

## E10 — Cross-schema grounding ablation (the v2 headline)

Same conditions as the legacy run, now on the split fixture. Q1–Q15 keep the legacy
answers (byte-identical data); Q16–Q18 are cross-schema-only. Scored by
[`seagate_scoring.py`](seagate_scoring.py) (now 18 questions).

| Condition | correct / 18 | cross-schema-only (Q16–Q18) / 3 | legacy single-schema (of 15) |
| --- | ---: | ---: | --- |
| basic | **2** | 0 | 4.3 |
| context_dump | **9** | 1 | 13.7 |
| wren_base | **5** | 1 | 4.0 |
| wren_bi | **9** | 2 | 8.7 |

**Per-question verdict matrix (1 trial):**

| Q | basic | context | wren_base | wren_bi | note |
| --- | :-: | :-: | :-: | :-: | --- |
| Q1 | ✓ | ✓ | ✓ | ✓ | jargon, single-table |
| Q2 | ✗ | ✓ | ✗ | ✓ | short-order jargon |
| Q3 | ✗ | ✗ | ✗ | ✗ | on-griddle=SUM(target_qty) — fails everywhere (legacy F4 gap) |
| Q4 | ✗ | ✓ | ✗ | ✓ | garnish defect |
| Q5 | ✗ | ✗ | ✗ | ✓ | WARM sites (within-schema control) |
| Q6 | ✗ | ✓ | ✗ | ✗ | Tigerline region (cross-schema) |
| Q7 | ✗ | ✓ | ✗ | ✗ | Reef To-Go (cross-schema) |
| Q8 | ✗ | ✗ | ✓ | ✓ | drive-family breakdown (cross-schema) |
| Q9 | ✗ | ✓ | ✓ | ✗ | Golden Yield Cobalt |
| Q10 | ✗ | ✗ | ✗ | ✓ | True Pass Rate (enrichment metric — wren_bi only) |
| Q11 | ✗ | ✓ | ✗ | ✗ | Moonlight/Diner Week (within-schema control) |
| Q12 | trap_ok | trap_ok | trap_ok | trap_ok | trap held in all conditions |
| Q13–Q15 | ✗ | ✗ | ✗ | ✗ | L4 chained cross-schema — fail everywhere |
| Q16 | ✗ | ✓ | ✗ | ✓ | cross-schema-only: WARM-line plated by family |
| Q17 | ✗ | ✗ | ✓ | ✓ | cross-schema-only: Golden Yield Vantage Q4 |
| Q18 | ✗ | ✗ | ✗ | ✗ | cross-schema-only L4 chain — fail everywhere |

**Findings (V-E10):**
- **The multi-schema feature works.** Cross-schema joins execute and return exact
  ground truth (verified directly: Q6=9,386; Q16=Cobalt 1,751/Vantage 3,017). The
  cross-schema-only column rises 0→2 with grounding (`basic`→`wren_bi`), so grounding
  *does* lift cross-schema text-to-SQL.
- **Enrichment ≈ doubles the base** (`wren_base` 5 → `wren_bi` 9), matching legacy F3.
  `wren_bi` ties `context_dump` (9) here — unlike the legacy run where context_dump
  dominated — because on the split schema the raw dump must also emit cross-schema
  qualification, which costs it some wins (Q9, Q17 it loses vs the structured layer).
- **Absolute scores are lower than legacy** (context 9 vs 13.7) because (a) joins are
  now cross-schema (harder), (b) 7 distractor tables share the scoped schemas, and
  (c) single trial. The L4 chains (Q13–Q15, Q18) fail in *every* condition — the
  hardest cross-schema chained reasoning is unsolved here.
- **Only `wren_bi` gets Q10** (True Pass Rate) — its enriched metric encodes the
  garnish-exclusion rule, exactly the legacy F3 win, preserved cross-schema.
- **Second independent sweep (notebook `10`) corroborates the ordering**: basic 2,
  context_dump 11, wren_base 4, wren_bi 10 — and critically on the **cross-schema-only
  Q16–Q18, `wren_bi` scored 2/3 vs `context_dump` 0/3**. The structured layer beats
  raw context-dump on the genuinely cross-schema questions — the *inverse* of the
  legacy small-schema finding (where context dominated). This is the clearest live
  evidence that the semantic layer earns its keep specifically when joins span
  schemas and the model must be *told* the join path.

## E6 — Repeated-run convergence

Onboard → 12 models (7 relevant + 5 in-schema distractors). Then re-enrich the
**same** glossary document each round:

| round k | coverage | active models | correct / 18 | Δcoverage |
| --- | ---: | ---: | ---: | ---: |
| 1 | 0.872 | 22 | 9 | — |
| 2 | 0.872 | 22 | 8 | 0.000 |
| 3 | 0.890 | 22 | 8 | +0.018 |
| 4 | 0.869 | 22 | 6 | −0.021 |
| 5 | 0.869 | 22 | 7 | 0.000 |

**Recommended N\* = 1–2** (first round with <2% marginal coverage gain is round 2).

**Findings (V-E6) — the headline, and it is counterintuitive:**
- **Coverage plateaus at round 1 (~0.87) and does NOT improve with repetition.** The
  first enrichment captures ~87% of glossary claims; rounds 2–5 oscillate within
  ±2% noise. The original hypothesis — "repeated runs recover coverage lost to
  non-determinism" — is **not supported** for same-document re-enrichment.
- **Graded score drifts *down* across rounds (9→8→8→6→7).** Extra enrichment passes
  on the same doc add churn, not value, and mildly *hurt* answer quality. **Product
  takeaway: enrich once; do not auto-re-run enrichment on an unchanged document.**
- **Robustness validated live:** the original run aborted at round 2 on a `422`
  (enrichment emitted an MDL the engine-gated activation rejected — legacy F9, live).
  The hardened `enrich_round` now records the failed round and continues, which is
  how this 5-round curve was obtained at all.
- **Anomaly to investigate:** active models jumped 12→**22** after round 1 and stayed
  there, though only 12 tables are in scope. Enrichment is emitting ~10 extra model
  entries (likely view/metric "models" or partial duplicates from the whole-manifest
  re-emit). Worth a follow-up — it inflates the manifest and is a candidate cause of
  the score drift. The deterministic offline `coverage_eval.score_coverage` + a
  gold-label set would also pin whether coverage 0.87 is real or judge-inflated.
- **Not yet run:** regime (b) — *independent fresh* enrich runs unioned (the
  multi-sample ceiling), vs the regime (a) same-doc re-enrichment measured here.
  That is the remaining E6 question.

(notebook `06_repeated_runs.ipynb`; coverage values vary run-to-run 0.42–0.89 — see
the variance note under E7.)

## E7 — Coverage as a metric

| condition | coverage | graded /18 |
| --- | ---: | ---: |
| wren_base | N/A (no BI doc to reconcile) | 4 |
| wren_bi | **0.417** | 5 |

**Findings (V-E7):**
- **Coverage is doc-relative.** `wren_base` has no document, so coverage is
  undefined (the layer captures nothing because there are no claims to capture) —
  the harness correctly returns no score. Coverage only becomes meaningful once a BI
  doc is attached, i.e. for `wren_bi`.
- **wren_bi coverage = 0.417–0.727 across runs** (0.417 here, 0.727 in `06`'s
  round 1). The enriched layer captures roughly **40–70%** of the glossary claims —
  consistent with `wren_bi` being a partial (not complete) grounding (it solves
  jargon + one metric but misses the calendar/region/chain facts, matching the
  per-question matrix and legacy F4).
- **Correlation needs more seeds.** With 2 conditions and 1 trial the Spearman
  coefficient is not meaningful; the deterministic gold-label cross-check
  (`coverage_eval.score_coverage`) is still a TODO fixture. What *is* visible:
  coverage moves the right direction (undefined→0.42–0.73 as the glossary is
  enriched in) and its magnitude (partial) matches the partial graded score.

### Cross-run variance (anomaly worth flagging) 🔴

`wren_bi` scored **9, 10, 5, 5, 10** across five independent single-trial runs
(runner, `10`, `09`, `07`, `06`-round1) → mean ≈ **7.8**, range **5–10**. Coverage
likewise swung 0.417↔0.727. This is **much larger than the legacy ±1–2/15** and is
driven by (a) stochastic distractor leakage into joins, (b) borderline cross-schema
join construction, and (c) enrichment non-determinism (one round emitted an MDL the
engine rejected — see E6). **Implication: single-trial numbers on this fixture are
unreliable; ≥3–5 trials and reporting the mean±range is mandatory.** The stable
signals are the *orderings* (basic ≪ wren_base < wren_bi ≈ context; wren_bi > context
on cross-schema-only) and the *structural* findings (E9 distractor inclusion, the
two harness bugs), not any single score.

## E8 — Copilot path vs deterministic endpoints

**Outcome: the Copilot path *runs*, the contract is validated, but grading is blocked
by agent version skew on this deployment.**

- ✅ **Attachment contract validated live.** The Copilot consumed the glossary as a
  structured `MessageAttachment` (`v2.text_attachment` → `{filename, content_type,
  text}`, rendered server-side via `_attachments_text`), ran **21 agentic steps**,
  and returned a **5-item changeset**. No upload/document-id step — inline + ephemeral
  works exactly as designed.
- 🔴 **Apply→activate→grade blocked.** Activating the changeset failed:
  - First on `POST .../mdl-files/bulk-status → 405` — the **running agent image is
    baked and older than the repo** (the AI-agent source is *not* bind-mounted, only
    `superset/` is), so it predates the atomic bulk-status route.
  - After a harness fallback to per-file activation, on `PATCH .../mdl-files → 422
    "MDL must contain at least one model, view, metric, or cube"` — the Copilot emits
    an **overlay changeset** whose files (e.g. a relationship-only overlay) are invalid
    *individually* and only valid as a **set**. That is precisely what `bulk-status`
    (atomic whole-manifest activation) was built to solve.
- **Finding (V-E8):** on a deployment whose agent has the Copilot stream but *not*
  bulk-status, Copilot changesets **cannot be activated at all** — a hard coupling
  between the two features. On this stack it's pure version skew (HEAD has the fix);
  to get graded Copilot-vs-deterministic numbers, **rebuild the agent image to HEAD**
  (`docker compose -f docker-compose.yml -f docker-compose.ai-agent.yml build
  superset-ai-agent && … up -d`) and re-run notebook 08. Not done here to keep the
  rest of the dataset on one consistent agent version.
- **Harness hardening applied:** `activate_all` now falls back to models-first
  per-file activation on 405 (helps normal MDL; cannot rescue overlay-set changesets,
  by design).

## E9 — Distractor discrimination

**Table selection (active MDL after onboard+enrich):** precision **0.583**, recall
**1.000**, F1 0.737.

- **All 7 relevant tables selected** (recall 1.0). ✓
- **But 5 of 7 distractors were also modelled** (inclusion rate **0.714**):
  `finance_ledger`, `hr_roster`, `iot_sensor_logs`, `maintenance_logs`,
  `vendor_contracts` — i.e. **every distractor in the two scoped schemas**
  (`seagate_core`/`seagate_ops`).
- **The 2 out-of-scope distractors were correctly excluded** — `marketing_campaigns`
  and `web_sessions` live in `seagate_ref`, which is outside the project scope. ✓
  **Schema scoping works; in-schema discrimination does not.**

**Finding (V-E9) — the deterministic onboard does NOT discriminate.** It models
*every* table in the scoped schemas regardless of the glossary, so precision is
bounded by how many distractors share those schemas. Discrimination is only
possible via (a) schema scoping (proven — `seagate_ref` excluded) or (b) the
**Copilot** onboard path, which is *told* to ignore unmentioned tables (E8). The
deterministic endpoint the legacy eval uses has no mechanism to drop in-schema
distractors. **This is the single most actionable result of the live run.**

**Query-time SQL leakage (adversarial distractors confirmed dangerous):**

| condition | questions whose SQL touched a distractor |
| --- | --- |
| basic | Q5→`iot_sensor_logs`, Q10→`iot_sensor_logs`+`maintenance_logs`, Q13→`finance_ledger` |
| context_dump | **none** (glossary in-prompt fully suppressed leakage) |
| wren_base | Q5→`iot_sensor_logs`, Q13/Q18→`finance_ledger` |
| wren_bi | Q13/Q18→`finance_ledger` |

- The **adversarial** distractors are the ones that leak: `iot_sensor_logs`
  (shared `line_id` FK pulls it into joins) and `finance_ledger` (decoy `units`
  column attracts unit-sum questions). The neutral distractors never appear in SQL.
- **`context_dump` is the only leak-free condition** — putting the full glossary in
  the prompt steers the model away from every distractor, while the structured
  layers (which model the distractors as real tables) still occasionally pick them.
  A pointed product takeaway: modelling a table makes the agent *more* likely to use
  it, so onboarding distractors is actively harmful, not neutral.

## E11 — Auto-onboard vs all-table onboard (PENDING — next pass)

**Why this experiment.** E9 showed the deterministic onboard has no distractor
discrimination (precision 0.583 — it modelled 5/7 in-schema distractors). The product
ships **auto-onboard**: the Copilot reads the glossary and *selects* which tables to
model. This experiment tests whether that selectivity yields a cleaner, better MDL.
**Requires the rebuilt agent image** (auto-onboard's overlay changeset needs the
`mdl-files/bulk-status` activation route the baked agent lacked — the E8 blocker).

| pipeline | selection precision | recall | graded /18 | coverage | SQL leaks | model count |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| all-table (det. onboard→enrich) | 0.583 *(from E9)* | 1.000 | _TBD_ | _TBD_ | _TBD_ | 12–22 |
| auto-onboard (Copilot) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

**Hypothesis:** auto-onboard precision ≫ 0.583 (excludes in-schema distractors) →
fewer SQL leaks; graded comparable-or-better on a smaller MDL. **Watch:** recall < 1
means it *missed* a relevant table (over-selective) — report which. (notebook
`11_auto_onboard.ipynb`, harness `eval_v2.auto_onboard`.)

## E12 — Auto-onboard + N enrichment passes (PENDING — next pass)

**Why.** E6 found deterministic re-enrichment plateaus after one pass. Does the same
hold on the Copilot path? K = additional Copilot enrichment passes beyond auto-onboard.

| K (extra passes) | coverage | graded /18 | Δcoverage | Δgraded |
| --- | ---: | ---: | ---: | ---: |
| 0 (auto-onboard only) | _TBD_ | _TBD_ | — | — |
| 1 | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| 2 | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

**Hypothesis (from E6):** K=1 adds little over K=0, K=2 ≈ K=1 → "auto-onboard + at most
one pass." Confirming this on the Copilot path generalises the E6 "enrich once"
finding to the product's real flow. **Average ≥3 trials** (single-trial variance was
±4/18). (notebook `11_auto_onboard.ipynb`, harness `eval_v2.copilot_enrich_pass`.)

## Reproduce

1. Bring up the Docker/Postgres stack; `superset load-examples` loads both the legacy
   `seagate` schema and the `seagate_multi` schemas (`seagate_core`, `seagate_ops`,
   `seagate_ref`).
2. Regenerate + verify the fixture: `python superset/examples/seagate_multi/generate_data.py`
   (asserts byte-parity, prints Q16–Q18).
3. Recreate the agent with `WREN_MEMORY_STORE=none`.
4. Run notebooks `06`–`10` (each writes to `results/seagate_multi/`), then `05` for
   the exact-scored headline. Average ≥3 trials. **Or** run the scripted sweep
   headlessly:

   ```bash
   cd superset_ai_agent/evaluation
   python run_eval_v2.py --agent-base-url http://localhost:8090/ai-agent \
       --superset-base-url http://localhost:8090 --trials 3
   ```

   which writes `results/seagate_multi/summary.json` — now with **multi-trial
   aggregation** (per-condition mean[min-max] over `--trials`, plus per-trial
   headlines) to tame the ±4/18 single-trial variance.
5. **Auto-onboard experiments (E11/E12) need the agent rebuilt to HEAD** (the baked
   image lacked `mdl-files/bulk-status`, which auto-onboard's overlay changeset needs
   to activate). After `make up-ai` rebuilds it: run notebook `11_auto_onboard.ipynb`.

**Validation already done (offline, in CI):** fixture parity + determinism +
ground-truth pinning (`tests/unit_tests/examples/seagate_multi_test.py`); scorer incl.
Q16–Q18 (`test_scoring.py`); fixture consistency / no distractor leak in the glossary
(`test_fixtures.py`); harness pure logic — SSE parse, selection metrics, coverage
aggregation (`test_eval_v2.py`); notebook JSON + cell-parse (`test_notebooks.py`).
