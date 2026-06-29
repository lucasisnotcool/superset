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

# Semantic-Layer Evaluation v2 — Specification

**Status:** Spec (build pending review). **Supersedes nothing** — extends the
2026-06-24 evaluation in [`RESULTS.md`](RESULTS.md).
**Author intent:** keep the legacy framework runnable, re-baseline the conditions
that drifted, add the experiments the product grew past, and move the whole suite
onto a **multi-schema** fixture with **distractor tables** so it finally exercises
cross-schema MDL and table-selection discrimination.

This document is parts 1–4 of the task. The concrete build (data generator,
recomputed ground truth, harness code, notebooks) follows in a second pass per the
agreed **spec-first** sequencing. The two decisions already locked by the owner:

- **Fixture strategy:** *split the existing seagate tables across schemas + add
  distractors* (so existing ground truth survives byte-for-byte).
- **This pass:** spec + fix the dead manual walkthrough + skeleton/outline only.

---

## Part 1 — What the legacy framework tested, and its integrity today

### 1.1 What it measures

The legacy suite is an **ablation of grounding strength** for one-shot text-to-SQL,
graded against hand-computed ground truth on a deliberately small 7-table fixture
(`dev_fixtures/seagate_manufacturing/`). It runs five conditions as a **monotonic
progression on a single semantic project** (each step only adds state, no teardown):

| # | Condition | What the agent has | 2026-06-24 mean (of 15) |
| --- | --- | --- | ---: |
| 1 | `basic` | DB only, no semantic layer | 4.3 |
| 2 | `context_dump` | DB + full BI glossary in the prompt | 13.7 |
| 3 | `wren_base` | DB + onboarded **base** MDL (structure only) | 4.0 |
| 4 | `wren_bi` | DB + MDL **enriched** from the glossary | 8.7 |
| 5 | `wren_bi_context` | enriched MDL **and** glossary in the prompt | 11.7 |

The questions (`test_queries.md`) are tiered L1–L4: L1 jargon→column, L2 joins +
markdown-only mappings (regions), L3 custom metrics (Golden Yield, True Pass Rate),
L4 chained multi-hop, plus one **trap** (Q12) where the correct answer is a refusal.
Grading is two-layer: an assistive grader in `eval_common.py` (`grade_one`) and an
exact ground-truth scorer (`seagate_scoring.py`, 2% float tolerance).

The deliverable was never the absolute scores — it is the **delta** between
conditions, which isolates how much each grounding mechanism contributes.

### 1.2 Integrity audit — does it still run? (verified against current code)

**Verdict: the Python harness (`eval_common.py`) is fully intact.** Every endpoint
it calls still exists with a matching method, path, request body, and response
shape. The only rot is in the *manual* `README.md` walkthrough, which used an
overlay API that has since been deleted.

| Harness call | Route (current) | Status |
| --- | --- | --- |
| `GET /health` | `app.py:575` | OK |
| `POST /agent/query` (`question, database_id, schema_name, catalog_name, execute`) | `app.py:604`; resp keys `status/sql/answer_summary/execution_result.{rows,row_count}/wren_context.{enabled,available,matched_models,retrieved_item_count,warnings}` all present | OK |
| `GET /…/projects?database_id=&schema_name=` | `app.py:943` | OK (may now return >1 row — see §2.4) |
| `POST /…/projects/resolve` (`…, create_if_missing`) | `app.py:909` | OK |
| `DELETE /…/projects/{id}` (`clean_baseline`) | `app.py:999` (soft-archive) | OK |
| `POST /…/projects/{id}/onboard` → `{id}` job, auto-activates base | `app.py:2890`; auto-activation at `app.py:2801` | OK |
| `GET /…/projects/{id}/jobs/{job_id}` → `status,error` | `app.py:2968` | OK |
| `POST /…/documents/text` (`filename,text,content_type`) | `app.py:3130` | OK |
| `POST /…/documents/{id}/enrich` → `{proposed_path,proposed_content}` | `app.py:3179` (now 409 if no base models — harness onboards first) | OK |
| `GET/POST/PATCH /…/mdl-files[/{id}]` (`path,content,source_type`/`content,status`) | `app.py:1148/1470/1684` | OK |

**Dead — manual `README.md` steps 4–5 only** (migration
`persistence/migrations/versions/0006_drop_semantic_overlay.py` removed the overlay
model):

- `PATCH /…/documents/{id}/review` — **route removed.**
- `POST /…/index/rebuild` — **route removed.**
- `POST /…/documents` (multipart) still exists (`app.py:840`) but **no longer
  returns `proposed_updates`** — `SemanticDocument` has no such field.

**Action (this pass):** rewrite `README.md` steps 4–5 to the project-scoped flow the
Python harness already uses (resolve → `documents/text` → `enrich` → activate
`mdl-files`). Done in §"README fix" below.

---

## Part 2 — How the product drifted, and what each drift does to the methodology

Run date of the baseline: **2026-06-24**. Everything below landed after.

### 2.1 Onboarding — `wren_base` still valid, but no longer representative

- **Contract unchanged.** `POST /onboard` still runs the deterministic structure-only
  pipeline (`semantic_layer/onboarding.py:59`): one base model per catalog table,
  structure seeded from the Superset catalog, an LLM overlay only for *descriptions*,
  then auto-activation. `wren_base` still measures "structure, no BI-doc semantics."
- **What changed:** onboarding in the *product* is now **Copilot-driven** (commit
  `0510be36ab`; `plan_copilot_onboarding_spec.md`): an `AutoOnboardModal` → seeded
  chat → multi-turn tool loop (`find_tables`/`read_document`/`propose_onboard_tables`)
  over `POST …/copilot/stream`. **The eval never exercises this path.**
- **Effect:** `wren_base` is still a clean baseline, but it is now a *legacy* path.
  The number that matters for the live product — "how good is the MDL a user actually
  gets from auto-onboard" — is **uncovered**. → New experiment **E8**.

### 2.2 Enrichment — `wren_bi` plumbing intact, number no longer frozen-comparable

- **Contract unchanged.** `POST /enrich` still returns one whole-manifest
  `MdlEnrichmentProposal` (`proposed_path`/`proposed_content`); `apply_enrichment`
  still activates it and deactivates superseded base files.
- **What changed:** the enrichment **prompt** changed (commit `89afa141c7`,
  `plan_enrichment_relationship_model_fix.md`): joins are now forbidden as `models[]`
  entries and steered into `relationships[]`; `validate_mdl` can hard-reject malformed
  relationship-models. Same glossary → potentially differently-shaped manifest.
- **Effect:** `wren_bi` still *runs*, but the **8.7/15 is stale** — the verified
  "7 models, 6 ONE_TO_MANY relationships" is not guaranteed reproducible. The
  Copilot also has a *separate* enrichment path (overlay `write_mdl_file` →
  multi-file `Changeset`) the eval never touches. → re-baseline `wren_bi`; add **E8**.

### 2.3 Coverage & provenance — a new **intrinsic** signal the eval should adopt

- **New since the run** (`d83567ab0d`, `695b48f70b`;
  `MDL_PROVENANCE_AND_COVERAGE.md`). **Coverage** = % of BI-document claims captured
  by the *active* MDL (`CoverageReport.score = (covered + 0.5·partial)/total`), run
  as a **debounced background job** on onboarding-complete / MDL activation / active
  edit, surfaced as `project.coverage_score` on the project-list response
  (`app.py:968`). There is **no public refresh/poll route** — the harness reads the
  field and waits for the background run. A deterministic offline scorer
  (`semantic_layer/copilot/coverage_eval.score_coverage(findings, gold)`) scores a
  detector against gold labels with per-status precision/recall/F1.
- **Provenance** = append-only event timeline + tool-call ledger
  (`GET /…/projects/{id}/provenance`), including `coverage_completed` events.
- **Effect on methodology:** today the eval *infers* grounding quality from
  number-matching only. Coverage measures the **artifact directly** and should track
  the `wren_base → wren_bi` lift. → New experiment **E6/E7**.

### 2.4 MDL Lab — lifecycle holds, with one resolve nuance

- Projects are now first-class (stable `slug` identity; schema removed from identity;
  migration `0011_project_slug_identity.py`), DB-access scoped (FULL→write,
  PARTIAL→read), Copilot ungated pre-onboarding (`MDL_LAB.md`).
- **Resolve nuance:** a schema is no longer unique, so `resolve`/`list` can return
  **multiple** projects and `resolve` picks the **newest-updated**. With a persistent
  `ai_agent.db` volume carrying legacy rows, that can be the wrong project.
  **Mitigation:** keep `clean_baseline` (DELETE archives competitors so a fresh
  resolve lands on the just-created project). Already in the harness.
- Multi-schema support shipped — the basis for Part 4.

### 2.5 Memory / learning loop — F1 confound still on by default

- `graph.py` still `recall_examples` (≈:558) + `store_confirmed` (≈:815), scoped by
  database+schema. **Unchanged.** The code default is `wren_memory_store="none"`, but
  the shipped `.env`/`.env.example` still set `WREN_MEMORY_STORE=lancedb` +
  `WREN_MEMORY_LEARNING_ENABLED=true` (the "full parity" profile the eval ran under).
- **Effect:** the F1 cross-condition SQL-leak is **fully intact**. The mandatory
  mitigation is unchanged: recreate the agent with `WREN_MEMORY_STORE=none` before any
  grounding A/B. v2 must keep this and assert it programmatically (see §5 R1).

### 2.6 Drift summary

| Area | Harness still works? | Score comparable? | Action |
| --- | :-: | :-: | --- |
| Onboarding (`wren_base`) | ✅ | ✅ | keep; add Copilot path (E8) |
| Enrichment (`wren_bi`) | ✅ | ⚠️ prompt drift | **re-baseline**; add E8 |
| Coverage/provenance | n/a (additive) | ➕ new signal | adopt as E6/E7 |
| MDL Lab | ✅ | ✅ | keep `clean_baseline` |
| Memory loop (F1) | ✅ | ⚠️ on by default | assert `=none` (R1) |

---

## Part 3 — Coverage gaps and the new experiments

Gaps the 2026-06-24 suite does **not** touch: repeated-run convergence; coverage as
a first-class metric; the live Copilot onboarding/enrichment path; table-selection
discrimination against distractors; and the layer's scaling premise (F2). The new
experiments E6–E10 close these. E6–E9 are fixture-agnostic; E10 requires Part 4.

### E6 — Repeated-run convergence (the headline new experiment)

**Dev intent:** each onboarding/enrichment run loses some coverage (LLM
non-determinism, partial extraction). Repeated runs over the *same* documents should
recover it. We want the **marginal coverage gain per run** and the **knee** — the run
count after which extra runs stop paying for themselves — to set a product default.

**Design.** On one project, with the glossary uploaded:
1. Onboard once. Record coverage `c0` and graded score `s0`.
2. For `k = 1..N` (N≈5): run `enrich` → `apply_enrichment` → activate → wait for the
   background coverage run → record `c_k`, `s_k`, and the **provenance delta** (models
   /relationships/metrics added this run).
3. Plot `c_k` and `s_k` vs `k`. Report `Δc_k = c_k − c_{k-1}`, the run where
   `Δc_k < ε` (knee), the variance across the 3 seeds, and whether `s_k` tracks `c_k`.

Run two regimes: **(a)** re-enrich the same single document (does the model converge
on its own output?); **(b)** independent fresh enrich runs averaged (the
multi-sample-then-union ceiling). (b) is the practical upper bound; (a) is what a
user clicking "enrich again" actually gets.

**Output:** recommended default run count `N*` for onboarding and for enrichment,
with the coverage/score curve and cost (LLM calls) per run.

**Risks:** (i) coverage is LLM-judged → noisy; mitigate with
`wren_copilot_coverage_votes>1` and 3 seeds. (ii) `apply_enrichment` is idempotent on
identical MDL (checksum) → re-enrich may no-op; detect via provenance and treat a
no-op as "converged." (iii) the F1 memory loop must be off or later runs recall
earlier SQL (R1).

### E7 — Coverage as a metric (validate the intrinsic signal)

**Dev intent:** confirm coverage % is a usable proxy for answer quality, so future
evals can use the cheap intrinsic metric instead of the expensive graded sweep.

**Design.** For every condition that produces an active MDL (`wren_base`, `wren_bi`,
each E6 run), capture `project.coverage_score` *and* the graded score (of 15).
Correlate across conditions/seeds (Spearman). Cross-check the live LLM coverage judge
against the deterministic `coverage_eval.score_coverage` using a small **gold-label**
set (hand-label each glossary claim covered/partial/missing per condition) to bound
judge noise (per-status precision/recall/F1).

**Output:** correlation coefficient + a statement of whether coverage can stand in
for the graded sweep, and the judge's measured accuracy vs gold.

### E8 — Copilot path vs deterministic endpoints (parity / representativeness)

**Dev intent:** the product onboards and enriches through the **Copilot stream**, not
the deterministic endpoints the eval uses. Measure the gap so we know whether the
legacy numbers describe what users get.

**Design.** Add `wren_base_copilot` and `wren_bi_copilot` conditions driven through
`POST …/projects/{id}/copilot/stream` (`CopilotTurnRequest`: seeded onboarding /
enrichment message + the glossary as a structured `MessageAttachment`
`{filename, content_type, text}` — the inline channel the server renders into the
prompt via `_attachments_text`; no upload step), consuming the SSE `progress`→
`complete` events, then `POST …/copilot/apply` the changeset and activate, and
reading the resulting active MDL. Grade with the same 15 questions; capture coverage
and the tool-call provenance ledger (which tools fired, which tables were proposed).
Compare to deterministic `wren_base`/`wren_bi`.

**Output:** per-condition score + coverage deltas (Copilot vs deterministic), and a
provenance trace of the Copilot's table/relationship/metric decisions.

**Risks:** SSE parsing + non-determinism (multi-turn); pin model/seed where possible,
average trials, and treat the streamed `complete` payload as the source of truth.

### E9 — Distractor discrimination (needs Part 4 distractors; runnable on either fixture)

**Dev intent:** with irrelevant tables present, does onboarding/enrichment/query
**ignore** tables the BI doc never mentions? This is the core new robustness claim.

**Design.** Define the **relevant set** R (tables named/implied by the glossary) and
the **distractor set** D (tables present but unmentioned — see §4.3). Then measure:
- **Onboarding/enrichment selection:** of the models in the active MDL, how many are
  in D (false inclusions) vs missing from R (false exclusions)? → precision/recall of
  table selection (read from MDL + provenance `propose_onboard_tables` calls).
- **Query-time leakage:** across the 15 questions, does any emitted SQL reference a
  table in D? Count distractor-touch rate.
- **Adversarial distractors:** D includes "tempting" tables whose columns collide with
  jargon (a `finance_ledger.units`, an `iot_sensor_logs.temperature` vs "heat lamp", an
  `hr_roster.shift_code` vs the shift mapping). Report whether these specifically get
  mis-selected.

**Output:** table-selection precision/recall and distractor-touch rate per condition;
explicit call-out of which adversarial distractor (if any) fooled the agent.

### E10 — Cross-schema correctness (needs Part 4)

**Dev intent:** does the multi-schema MDL feature actually let text-to-SQL join across
schemas, and does grounding still lift the score when joins are cross-schema?

**Design.** Run the full E1–E8 sweep on the split fixture (Part 4). Because the data
is byte-identical, **the existing Q1–Q15 ground truth is unchanged** — the only
variable is that Q6–Q10/Q12–Q15 are now cross-schema joins (Q5, Q11 stay
within-schema as controls). Add net-new questions Q16+ that can *only* be answered by
joining across the schema boundary. Compare each condition's score on
single-schema (legacy) vs split (v2) to isolate the cross-schema penalty, if any.

**Output:** per-condition single-vs-split score deltas, and pass/fail on the cross-
schema-only Q16+.

### E11 — Auto-onboard vs all-table (deterministic) onboard

**Dev intent.** The product onboards through the **MDL Copilot** ("auto-onboard"): the
user hands the BI document to the Copilot, which **selects** the tables the doc
describes (`propose_onboard_tables`) and onboards + enriches them in one turn. The
legacy eval used the **deterministic `/onboard`**, which models **every** table in the
scoped schemas. The live v2 run showed the deterministic path has **no distractor
discrimination** (E9: precision 0.583 — it modelled 5/7 in-schema distractors). The
question: **does the Copilot's selective auto-onboard produce a better MDL?**

**Contract (verified — replicate the UI faithfully).** Auto-onboard is the *same*
`POST …/copilot/stream` endpoint as enrichment; only the seeded message differs (no
mode flag). The exact production message (`eval_v2.AUTO_ONBOARD_MESSAGE`) asks the
Copilot to onboard **and** enrich in one turn. The UI persists the doc as a project
document (so `read_document`/coverage tools see it) **and** attaches it inline; the
harness's `auto_onboard()` does both. Greenfield projects are ungated (the MDL-Lab
change). Apply = `/copilot/apply` (drafts) → `mdl-files/bulk-status` (atomic activate).

**Design.** Two pipelines on the same `seagate_multi` fixture, each from a fresh
`clean_baseline`:
- **all-table** (legacy): deterministic `/onboard` → `wren_base`; then `/enrich` →
  `wren_bi`.
- **auto-onboard** (new condition `auto_onboard`): one Copilot turn via
  `auto_onboard(project, glossary)` (selective onboard + first enrich).

For each, capture: **table-selection precision/recall** vs the R/D sets
(`selection_metrics`), **graded /18**, **coverage**, **query-time distractor-touch
rate** (`sql_references_tables`), and **active model count**.

**Primary metric:** selection precision (distractor exclusion) and graded score.
**Hypotheses:** auto-onboard precision ≫ 0.583 (it should exclude in-schema
distractors the deterministic path always includes) → fewer SQL leaks; graded score
comparable-or-better from a smaller, cleaner MDL. **Risk:** selectivity can also *miss*
a relevant table (recall < 1) — report both precision and recall, per-table.

**Output:** a side-by-side table (auto vs all-table) on selection P/R, graded, coverage,
leakage, model count — averaged over ≥3 trials (Copilot is non-deterministic; the live
run showed `wren_bi` variance 5–10/18, so single trials are not trustworthy — R3/var).

### E12 — Auto-onboard + N enrichment passes (does pass 2 help?)

**Dev intent.** E6 found that re-running the **deterministic** enrichment on the same
document **plateaus after one pass** (coverage flat, graded score drifts down). Does the
same hold on the **Copilot** path the product ships? How many enrichment passes beyond
auto-onboard are worth running?

**Design.** From one auto-onboarded project, run K additional Copilot enrichment
refinement passes (`copilot_enrich_pass`, message `eval_v2.COPILOT_ENRICH_MESSAGE`),
K ∈ {0, 1, 2} → three measured states:
- `auto_onboard` (K=0) — the auto-onboard turn already includes a first enrich.
- `auto_onboard_enrich1` (K=1) — one extra refinement pass.
- `auto_onboard_enrich2` (K=2) — two extra passes.

After each state capture **graded /18**, **coverage**, **active model count**, and the
**provenance** delta. A pass that emits an MDL the engine rejects (422) is recorded as
a no-op round and the loop continues (`copilot_build` captures `activate_error`).

**Primary metric:** Δgraded and Δcoverage per added pass.
**Hypothesis (from E6):** K=1 adds little over K=0 (auto-onboard already enriched once),
K=2 ≈ K=1 — i.e. **at most one extra pass**. Confirming this on the Copilot path
generalises the E6 "enrich once" finding to the product's real flow. **Risk:** each pass
is a full Copilot turn (cost/latency) and non-deterministic — average ≥3 trials and
report mean±range; treat a single down-tick as noise, a *consistent* plateau as signal.

**Output:** coverage/graded curve over K=0,1,2 with marginal deltas, and a one-line
recommendation ("auto-onboard alone" vs "auto-onboard + 1 pass").

---

## Part 4 — Multi-schema + distractor fixture (`seagate_multi`)

### 4.1 Strategy (locked): split, don't regenerate

Reorganize the **same** seagate data across schemas so existing graded queries become
cross-schema joins **with identical ground truth**, then layer distractors on top.
Data is regenerated from the **same deterministic generator** (`SEED = 20251231`), so
every number in `test_queries.md` (9,386; 0.961; 0.935; …) holds byte-for-byte. The
*only* change is table placement + added distractors + a rewritten glossary.

We create a **new** example directory rather than mutating `seagate_manufacturing`, so
the legacy single-schema suite stays runnable for the single-vs-split comparison (E10).

### 4.2 Schema boundary (recommended: master vs transactional)

| Schema | Tables | Rationale |
| --- | --- | --- |
| `seagate_core` | `sites`, `production_lines`, `drive_skus` | master / reference data |
| `seagate_ops` | `work_orders`, `production_events`, `quality_tests`, `shipments` | transactional facts |

Which legacy queries become cross-schema under this boundary:

| Q | Tables | Cross-schema? |
| --- | --- | :-: |
| Q5 (WARM sites) | sites+lines (both core) | within (control) |
| Q6 (Tigerline units) | sites,lines (core) + work_orders,events (ops) | **cross** |
| Q7 (Reef To-Go) | shipments,work_orders (ops) + lines,sites (core) | **cross** |
| Q8 (drive family) | events,work_orders (ops) + skus (core) | **cross** |
| Q9 (Golden Yield) | events,work_orders (ops) + skus (core) | **cross** |
| Q10 (True Pass Rate) | tests,work_orders (ops) + lines,sites (core) | **cross** |
| Q11 (Moonlight/Diner) | events (ops only) | within (control) |
| Q12 (trap) | events,work_orders (ops) + skus (core) | **cross** |
| Q13–Q15 (L4 chains) | span both | **cross** |

This boundary maximizes cross-schema coverage (10 of 15) while preserving two
within-schema controls (Q5, Q11). **Decision D1** below records the alternative
(splitting `sites` from `production_lines` to force Q5 cross-schema) and why it's
rejected (it scatters dimensions and muddies the master/transactional narrative).

### 4.3 Distractor tables

Distractors are present in the DB but **never mentioned in the glossary**. Two
placements, each testing a different thing:

- **In-schema distractors** (hardest, most realistic — a schema-level onboard
  enumerates them): add to `seagate_core`/`seagate_ops` a handful of plausible but
  irrelevant tables, including **adversarial** ones whose columns collide with jargon:
  - `seagate_finance_ledger` (`cost_center`, `units`, `amount`) — collides with "units".
  - `seagate_iot_sensor_logs` (`sensor_id`, `temperature_c`, `line_id`) — collides with
    "heat lamp"/thermal and even shares `line_id`.
  - `seagate_hr_roster` (`employee_id`, `shift_code`, `site_id`) — collides with the
    shift mapping and shares `site_id`/`shift_code` keys.
  - `seagate_maintenance_logs`, `seagate_vendor_contracts` — neutral noise.
- **Out-of-scope-schema distractor:** a third schema `seagate_ref` (e.g.
  `marketing_campaigns`, `web_sessions`) that the project must **not** pull in —
  tests schema scoping (the project's `schema_names` should exclude it).

The relevant set R = the 7 original tables; the distractor set D = the above. E9
scores precision/recall of selection against R/D.

### 4.4 Cross-schema BI glossary

Rewrite `bi_glossary.md` so the join guide and at least the region/shipment facts are
explicitly cross-schema, e.g. "to get a region's shipped units, join
`seagate_core.sites → seagate_core.production_lines → seagate_ops.work_orders →
seagate_ops.shipments`." Add 2–3 net-new facts that *force* a cross-schema hop for the
Q16+ questions. The glossary must remain the *only* place jargon→column and region
membership live (the "don't guess it" premise), and must **omit all distractors**.

### 4.5 New cross-schema-only questions (Q16+)

Computed from the same deterministic data (so ground truth is exact). Each must be
unanswerable without a `seagate_core`↔`seagate_ops` join, e.g.:
- Q16 (L2): "units plated per drive family at WARM-line sites" — `sites`+`lines`
  (core) status filter joined to `events`+`work_orders` (ops) + `drive_skus` (core).
- Q17 (L3): a metric whose numerator and denominator straddle the boundary.
- Q18 (L4): region rollup + cross-schema metric + distractor-adjacent jargon
  (to double as an E9 adversarial probe).

Exact SQL + numbers are produced in the build pass by running the generator and
computing answers (same method as the original fixture), then encoded in
`seagate_scoring.py`'s `EXPECTED` map.

### 4.6 Loading mechanics (confirmed)

Registration is **pure filesystem glob** — no code wiring. A new sibling dir
`superset/examples/seagate_multi/` with `data/*.parquet` + `datasets/*.yaml` (each
YAML carrying its `schema:` and a **fresh** `uuid` + the existing `database_uuid`) is
auto-discovered by `superset/examples/data_loading.py:152` and loaded by
`superset load-examples`. The generic loader `CREATE SCHEMA IF NOT EXISTS`-es each
schema and `to_sql(schema=…)` (`generic_loader.py:83–163`).

**Hard constraint:** this requires **Postgres** (the Docker dev examples DB —
`docker/.env:55`). SQLite has no real schemas, so the multi-schema fixture and
cross-schema joins **cannot** run on a bare `superset run` SQLite setup. v2 targets
Docker/Postgres and must fail fast with a clear message on SQLite (R4).

---

## Part 5 — Risks & mitigations

| # | Risk | Mitigation |
| --- | --- | --- |
| R1 | **F1 memory leak** still on by default; later conditions recall earlier SQL → invalid A/B | Harness asserts `WREN_MEMORY_STORE=none` at startup (read `/health` or a config echo) and **aborts** if learning is on; documented in v2 README |
| R2 | **Enrichment prompt drift** → `wren_bi` ≠ 8.7 | Re-baseline `wren_bi`; record the prompt/commit hash in results metadata so future drift is detectable |
| R3 | **Coverage is LLM-judged** → noisy E6/E7 | `wren_copilot_coverage_votes>1`; 3 seeds; cross-check with deterministic `score_coverage` against gold labels |
| R4 | **SQLite can't represent schemas** | Detect dialect at harness start; hard-fail with "run on Docker/Postgres" if not Postgres |
| R5 | **Persistent `ai_agent.db` legacy rows** make `resolve` pick the wrong project (newest-updated tiebreak) | Keep/strengthen `clean_baseline`; assert the resolved project id matches the one just created |
| R6 | **`apply_enrichment` idempotent no-op** masks "did the run do anything?" in E6 | Read provenance/tool-call ledger per run; treat checksum-identical MDL as "converged," not "ran" |
| R7 | **Ground-truth drift** if the split accidentally changes data | Generate `seagate_multi` from the **same** generator/seed; add a parity assertion that row counts + a checksum per table equal the single-schema fixture |
| R8 | **Copilot SSE non-determinism** (E8) | Pin model + seed where the provider allows; average trials; source-of-truth is the `complete` event payload, not intermediate `progress` |
| R9 | **Distractor over-tuning** (distractors too obviously irrelevant) | Include adversarial column-name collisions (§4.3); report per-distractor outcomes, not just an aggregate |

---

## Part 6 — Decision points

- **D1 — Schema boundary.** *Recommend* master/transactional (`seagate_core` /
  `seagate_ops`, §4.2): clean narrative, 10/15 cross-schema, 2 controls. *Rejected:*
  splitting `sites`↔`lines` to force Q5 cross-schema (scatters dimensions). **Default:
  master/transactional unless the owner wants Q5 cross-schema too.**
- **D2 — Distractor placement.** *Recommend* both in-schema (adversarial) **and** a
  third out-of-scope schema (§4.3) — they test different things (selection vs
  scoping). **Default: both.**
- **D3 — Mutate vs new fixture.** *Recommend* a **new** `seagate_multi` dir, leaving
  `seagate_manufacturing` intact for the single-vs-split comparison (E10). **Default:
  new dir.**
- **D4 — Coverage read path.** No public refresh route exists. *Recommend* poll the
  project list for `coverage_score` after activation (real path), **and** add an
  offline deterministic `score_coverage`-vs-gold check for E7 rigor. **Default: both.**
- **D5 — Re-baseline vs pin.** *Recommend* **re-baseline** `wren_bi` (and record the
  prompt hash) rather than pinning the old prompt — we want current-product numbers.
  **Default: re-baseline.**

---

## Part 7 — Build order (next pass)

1. **README fix** (this pass) — rewrite dead manual steps 4–5.
2. **Fixture** — `superset/examples/seagate_multi/generate_data.py` importing the
   seagate builders (same seed) + schema assignment + distractor generators; emit
   parquet + dataset YAMLs (fresh UUIDs). Parity assertion (R7).
3. **Ground truth** — run the generator, compute Q16+ answers, extend
   `seagate_scoring.py` `EXPECTED`.
4. **Harness** — extend `eval_common.py`:
   - `assert_memory_disabled()` + dialect/Postgres guard (R1, R4).
   - `coverage_for_project()` (poll project list) + `provenance(project_id)` readers.
   - `enrich_until_converged(project, doc, n)` for E6; per-run coverage/score/provenance.
   - `copilot_stream(project, message, attachments)` SSE driver for E8.
   - `table_selection_metrics(active_mdl, R, D)` for E9.
   - Multi-schema config: `schema_names: list[str]`, resolve passes the set.
5. **Notebooks** — `06_repeated_runs.ipynb` (E6), `07_coverage_metric.ipynb` (E7),
   `08_copilot_path.ipynb` (E8), `09_distractors.ipynb` (E9),
   `10_cross_schema.ipynb` (E10); update `05_compare_and_score.ipynb` for the new
   conditions.
6. **RESULTS_v2.md** — re-baselined numbers + E6–E10 findings, with prompt/commit
   metadata for drift-tracking.

---

## Appendix — proposed `eval_common.py` additions (signatures only)

```python
# --- environment guards (R1, R4) ---
def assert_eval_preconditions(client: AgentClient) -> None:
    """Abort unless memory-learning is OFF and the examples DB is Postgres."""

# --- coverage + provenance (E6/E7) ---
def coverage_for_project(client, project_id, *, timeout=120) -> float | None:
    """Poll the project list until coverage_score settles; return it."""
def provenance(client, project_id) -> list[dict]:
    """GET /projects/{id}/provenance — events + tool-call ledger."""

# --- repeated-run convergence (E6) ---
def enrich_until_converged(client, project_id, document_id, *, n=5, seeds=3
) -> list[dict]:
    """Per run k: enrich→apply→activate→coverage→graded score→provenance delta."""

# --- copilot path (E8) ---
def copilot_stream(client, project_id, message, *, attachments=None) -> dict:
    """Drive POST /copilot/stream (SSE); return the final `complete` payload."""

# --- distractor discrimination (E9) ---
def table_selection_metrics(active_mdl_models: set[str],
                            relevant: set[str], distractors: set[str]) -> dict:
    """precision/recall of selected tables vs R; distractor inclusions."""
```
