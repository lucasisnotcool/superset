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

# Seagate Semantic-Layer Evaluation — Results & Findings

**Run:** live Docker stack (`http://localhost:8090`), agent provider **OpenAI
`gpt-4.1-mini`** (from `superset_ai_agent/.env`), Wren full-parity profile
(embedding retriever + LanceDB, deep validation, engine-gated activation).
**Date:** 2026-06-24. **Trials:** 3 per condition (LLM is non-deterministic);
scores are the **mean of 3**, graded against the ground truth in
[`../dev_fixtures/seagate_manufacturing/test_queries.md`](../dev_fixtures/seagate_manufacturing/test_queries.md)
by [`seagate_scoring.py`](seagate_scoring.py).

> ⚠️ **Fairness control (important).** The agent's durable learning loop
> (`WREN_MEMORY_STORE=lancedb`) recalls prior NL→SQL pairs across queries scoped
> only by database+schema — so running the experiments in sequence let later
> experiments **recall earlier ones' SQL**. This is a real confound (see Finding
> F1). The numbers below were produced with the learning loop **disabled**
> (`WREN_MEMORY_STORE=none`) so each condition is measured on its grounding alone.
> The contaminated first run is preserved in `results/contaminated_memory_on/`.

## Headline

| # | Experiment | What the agent had | Mean correct (of 15) |
| --- | --- | --- | ---: |
| 1 | **basic** | DB only, no semantic layer | **4.3** |
| 2 | **context_dump** | DB + full BI glossary in the prompt | **13.7** |
| 3 | **wren_base** | DB + onboarded base Wren layer (structure only) | **4.0** |
| 4 | **wren_bi** | DB + Wren layer enriched from the BI glossary | **8.7** |
| 5 | **wren_bi + context** | DB + enriched Wren layer **and** the glossary in the prompt | **11.7** |

**One-line read:** on this small 7-table schema, **dumping the whole glossary into
the prompt wins decisively (13.7/15)**; **enrichment roughly doubles the baseline
(4.3 → 8.7)** and is the best *structured* option; the **base layer alone adds
nothing** (4.0 ≈ 4.3); and **stacking the enriched layer on top of the context dump
does not help — it slightly hurts (11.7 < 13.7)** because the two grounding paths
interfere (Finding F10).

## Per-question matrix (correct out of 3 trials)

| Q | Lvl | Tests | basic | context | wren_base | wren_bi | bi+ctx |
| --- | --- | --- | :-: | :-: | :-: | :-: | :-: |
| Q1 | L1 | jargon "86'd" → units_scrapped | 2 | 3 | 3 | 3 | 3 |
| Q2 | L1 | jargon short-order open | 0 | 3 | 0 | 3 | 3 |
| Q3 | L1 | jargon "on the griddle" (SUM target_qty) | 0 | 2 | 0 | 0 | 0 |
| Q4 | L1 | jargon "garnish" defect | 0 | 3 | 0 | 3 | 3 |
| Q5 | L2 | join → WARM sites | 3 | 3 | 3 | 3 | 3 |
| Q6 | L2 | **region rollup** (markdown-only) | 0 | 3 | 0 | 3 | 3 |
| Q7 | L2 | region + To-Go (markdown-only) | 0 | 3 | 0 | 0 | 3 |
| Q8 | L2 | join → drive family breakdown | 3 | 3 | 2 | 3 | 3 |
| Q9 | L3 | **Golden Yield** (STANDARD-only rule) | 2 | 3 | 1 | 1 | 3 |
| Q10 | L3 | **True Pass Rate** (garnish-exclusion rule) | 0 | 2 | 0 | 3 | 1 |
| Q11 | L3 | Diner Week + Moonlight shift (markdown-only) | 0 | 3 | 0 | 0 | 0 |
| Q12 | L3 | **trap** — should refuse | 3 | 3 | 3 | 3 | 3 |
| Q13 | L4 | chained region + metric + jargon | 0 | 3 | 0 | 0 | 1 |
| Q14 | L4 | chained region + True Pass Rate | 0 | 2 | 0 | 1 | 3 |
| Q15 | L4 | chained region + recency + share | 0 | 2 | 0 | 0 | 3 |
| | | **mean correct** | **4.3** | **13.7** | **4.0** | **8.7** | **11.7** |

(`bi+ctx` = the enriched Wren layer **and** the glossary in the prompt — experiment 5.)

## Findings

### F1 — Cross-experiment memory leakage (methodology-critical) 🔴
The one-shot `/agent/query` path **stores** every successfully executed NL→SQL pair
([`graph.py` `store_confirmed`](../graph.py)) and **recalls** prior pairs into the
prompt ([`graph.py` `recall_examples`](../graph.py)), scoped only by
database+schema. Running `basic → context_dump → wren_base → wren_bi` in sequence
therefore let the later conditions recall the earlier ones' SQL. Smoking gun: in
the first (contaminated) run, **`wren_base` reproduced `context_dump`'s
glossary-derived region SQL** (`site_code IN ('SGY','SGT')`) byte-for-byte despite
having no glossary. The contaminated run scored `wren_base`/`wren_bi` far higher
than they deserve. **Fix for the eval:** disable the learning loop
(`WREN_MEMORY_STORE=none`). **Product implication:** the learning loop is a real
feature, but any A/B of grounding must isolate it, and in production the same
recall makes results history-dependent (and could surface one analyst's query
patterns to another within the same schema scope).

### F2 — Context dump dominates *on this schema* — but it's a small-schema artifact
The entire glossary is ~6,650 characters and the schema is 7 tables, so the whole
business context fits in the prompt every call → 13.7/15. This is exactly the
case the semantic layer is **not** designed for. The layer's value (selective
retrieval, governance, reuse) only appears when the schema/glossary is too large
to dump — which this fixture is deliberately small enough to avoid. **The eval
confirms the agent can use rich context well; it does not test the layer's
scaling premise.** A fair test of the layer's *raison d'être* needs a
hundreds-of-tables schema where the glossary cannot be dumped.

### F3 — Enrichment roughly doubles the baseline and fixes specific classes
`wren_bi` (8.7) vs `basic`/`wren_base` (4.3/4.0). Enrichment reliably fixed:
- **Jargon via aliases** — Q2, Q4 go 0→3 once the enriched `alias`/`description`
  is baked into the retrieval chunk (CR9 working as designed).
- **A custom metric definition** — Q10 (True Pass Rate, garnish-failure exclusion)
  goes 0→3: the enriched `true_pass_rate` metric encodes the rule correctly, and
  it is the **only** condition that gets Q10 right by *logic* (basic/base fail on
  a `'Pass'` vs `'PASS'` enum-case error the layer fixes).
- **One region rollup** — Q6 (Tigerline = SGY+SGT) reaches 3/3.

The enriched manifest was genuinely rich (verified: 7 models, 6 `ONE_TO_MANY`
relationships, `golden_yield` + `true_pass_rate` metrics) — **not** the degraded
"blob" the design docs warned about.

### F4 — Enrichment's gaps (where wren_bi still fails)
`wren_bi` did **not** robustly capture, and so failed: Q3 (on-griddle = SUM
target_qty), Q7 (To-Go region), Q11 (Diner Week / Moonlight shift), Q9 (Golden
Yield STANDARD-only — only 1/3), and the L4 chains (Q13–Q15). The markdown-only
*calendar* and *shift* mappings and the *second* region rollup never became
calculated fields/instructions, so the enriched layer is **worse than a raw context
dump** on those. This matches the design docs' own open items (region/Diner-Week
calculated fields, Golden-Yield consistency).

### F5 — The schema column names leak some jargon
`basic` still gets Q1/Q5/Q8/Q9 partly because the physical columns are
self-describing: `units_scrapped`, `garnish_defect`, `status='BAKING'`,
`ticket_type`. So the fixture's "jargon" is only partly hidden — a capable model
guesses several L1/L2 answers with no glossary at all. This slightly understates
the value of *every* grounding method and should be noted when citing the basic
baseline.

### F6 — Number-match ≠ logic-correct
Several "correct" scores match the **number** without provably applying the
**rule**. Q9 Golden Yield: `basic` scores 2/3 with a formula that omits the
STANDARD-only filter, because Cobalt-December is almost all STANDARD tickets, so
the filtered and unfiltered numbers nearly coincide (~0.961). The scorer (and any
number-based grader) cannot distinguish "right for the right reason" from "right
by luck" here. Only `wren_bi` actually emitted the `ticket_type='STANDARD'` filter.

### F7 — The Q12 trap is a weak signal
All four conditions score `trap_ok` 3/3, but this is largely **accidental**: the
Golden-Yield-for-short-order query tends to return `NULL` (no STANDARD rows in the
slice) rather than a reasoned refusal. In the contaminated run, `basic` instead
returned a confident `0.903` (trap_fail). So the trap outcome is noise-driven, not
evidence of "understanding the exclusion rule." Treat Q12 as inconclusive.

### F8 — Harness bug found & fixed: enrichment apply caused duplicate models
Enrichment re-emits the **whole** manifest into one file (e.g.
`seagate_enriched.mdl.json`), whose path differs from the onboarding files
(`models/<table>.json`). Activating it **alongside** the base files produces
`Duplicate model name` materialization errors (verified live: `file_count: 8`,
7 duplicate warnings). The original `apply_enrichment` left base files active.
**Fixed** so it deactivates the superseded base files, leaving one clean active
manifest (`file_count: 1`, no warnings).

### F9 — LLM non-determinism
Run-to-run variance is real: Q3 returned 57 / 39 / NULL / 16,282 across runs;
per-condition totals varied by ±1–2/15 between trials. Hence the 3-trial mean.
Single-run conclusions on individual questions are unreliable; the **aggregate
ordering** (context > bi+ctx > bi > basic ≈ base) is stable. Enrichment itself is
also non-deterministic: one enrich run emitted a manifest with a **duplicated
model** that the engine-gated activation correctly rejected (422) — re-running
enrichment produced a clean, activatable 7-model manifest (what an operator would do).

### F10 — Stacking the semantic layer *on top of* the context dump does not help — it slightly hurts
**Experiment 5** (enriched Wren layer **and** the full glossary in the prompt)
scored **11.7/15 — below context-dump-alone (13.7)** and above enrichment-alone
(8.7). The two grounding mechanisms **interfere**: when the enriched layer routes a
query through the wren-core semantic rewrite, that rewrite can **override the
free-form SQL the glossary alone would have produced correctly**. Mechanism,
source-backed from the trial SQL:
- **Q3** (on-griddle): context-dump emits `SUM(target_qty) WHERE status='BAKING'`
  → 57 ✅; the combined run is rewritten through the layer to
  `SUM(units_completed)` → 39 ❌.
- **Q11** (Diner Week): context-dump's clean date-filtered query → 145 ✅; the
  combined run's layer-framed query returns **no rows** ❌ (0/3).
- **Q10** (True Pass Rate): enrichment-alone got it 3/3 via its encoded metric, but
  with the glossary *also* present the model drifted to a simpler (wrong) formula
  → 1/3.

The combined run did *gain* on some L4 chains (Q7, Q14, Q15 improved), so it is not
uniformly worse — but the net is a **regression vs. the simpler context dump**.
**Takeaway:** more grounding is not automatically better; an incompletely-enriched
layer can *suppress* knowledge the raw context would otherwise supply. Getting near
15/15 here needs the enrichment **gaps closed** (region/calendar/shift as calculated
fields + the on-griddle/target_qty mapping), not the two paths bolted together.

## Code validation performed
- **Auth path** verified end-to-end against the live stack (Superset JWT login →
  `/api/v1/me/` identity → governed SQL execution); `basic`/`context_dump`
  confirmed to run with **no Wren grounding** (`wren_available=False`).
- **Parser** (`parse_test_queries`) verified to extract all 15 questions, clean
  question text, ground-truth numbers, and the trap flag.
- **Scorer** (`seagate_scoring.py`) unit-checked; fixed a false-positive where the
  percentage dual-scale heuristic matched `1`→`0.01`≈`0`.
- **Enrichment apply** fixed (F8). All eval Python is `ruff`-clean.
- **Fairness confound** (F1) identified from the data and controlled by disabling
  the learning loop.

## Reproduce
1. Bring up the Docker stack; ensure `superset load-examples` loaded the 7
   `seagate_*` tables (schema `seagate`, examples DB).
2. Disable the learning loop for a fair ablation: recreate the agent with
   `WREN_MEMORY_STORE=none` (a temporary compose override was used here).
3. Run the notebooks `00`→`05` in order (or the same `eval_common` calls). Set
   `EVAL_AGENT_BASE_URL=http://localhost:8090/ai-agent`,
   `EVAL_SUPERSET_BASE_URL=http://localhost:8090`.
4. Score with `seagate_scoring.py`; for multi-trial, repeat the 15-question sweep
   per condition and average.

**Artifacts:** per-trial results in `results/*_trial*.json`, aggregates in
`results/*_agg.json`, contaminated first run in `results/contaminated_memory_on/`.
