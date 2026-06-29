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

# Consultant Handoff — Semantic-Layer Eval v2 Results Debrief Deck

This file has two parts:
1. **The prompt** to give a consultant LLM (copy everything under "PROMPT BEGINS").
2. **The file manifest** — exactly which files to attach alongside the prompt.

The consultant has **no prior context** about this codebase or project. The prompt
below is written to be self-contained; the attached files provide the source of
truth for every number and design detail.

---

## File manifest — attach these with the prompt

Attach all of the following (paths are repo-relative). Tier 1 is mandatory; Tier 2
lets the consultant describe methodology precisely; Tier 3 is optional depth.

**Tier 1 — results & methodology narrative (the spine of the deck):**
- `superset_ai_agent/evaluation/RESULTS_v2.md` — the live v2 run: every number, every
  finding, all caveats. **This is the primary source.**
- `superset_ai_agent/evaluation/EVAL_V2_SPEC.md` — the design spec: what each
  experiment (E6–E10) tests, dev intent, risks/mitigations, decisions.
- `superset_ai_agent/evaluation/RESULTS.md` — the legacy single-schema run (the
  before-state and the original 5-condition methodology). Needed for the
  "what changed / why v2" framing and the legacy baseline numbers.
- `superset_ai_agent/evaluation/results/seagate_multi/summary.json` — machine-readable
  Option-1 headline + distractor metrics.

**Tier 2 — the test data & scoring (so methodology slides are accurate):**
- `superset_ai_agent/dev_fixtures/seagate_multi/README.md` — fixture layout (3 schemas,
  distractors).
- `superset_ai_agent/dev_fixtures/seagate_multi/bi_glossary.md` — the BI document the
  agent is graded on grounding to (shows the "jargon → column" premise).
- `superset_ai_agent/dev_fixtures/seagate_multi/test_queries.md` — the 18 graded
  questions (L1–L5) with ground-truth answers.
- `superset_ai_agent/dev_fixtures/seagate_multi/tables.json` — relevant vs distractor
  table sets, schema map, adversarial distractors.
- `superset_ai_agent/evaluation/seagate_scoring.py` — the exact ground-truth scorer
  (how "correct" is defined per question).

**Tier 3 — harness internals (optional, for a methodology appendix):**
- `superset_ai_agent/evaluation/README.md` — how the harness talks to the agent.
- `superset_ai_agent/evaluation/eval_v2.py` — the v2 harness (coverage, Copilot,
  distractor metrics, multi-schema).
- `superset_ai_agent/evaluation/run_eval_v2.py` — the headless runner.
- `superset/examples/seagate_multi/generate_data.py` — the deterministic data
  generator (byte-parity with the legacy fixture).

---

## PROMPT BEGINS — give everything below (plus the attached files) to the consultant

You are a senior technical writer and data-storyteller preparing a **results-debrief
slide deck** for a team of **AI engineers** who build a natural-language-to-SQL
feature. They are technical but were **not** involved in this evaluation. Your deck
must let them quickly understand **what works, what doesn't, the methodology behind
each test, and what to do next.** Prioritize correctness and clarity over polish; do
not invent numbers — every figure must come from the attached files (chiefly
`RESULTS_v2.md` and `summary.json`). When a result is single-trial or uncertain, say
so on the slide.

### Background you need (plain-language; the team knows the product but not this eval)

- **The product.** Apache Superset has an AI agent that answers analytical questions
  by generating SQL against a user's database ("text-to-SQL"). To do this well it can
  use a **semantic layer** (an MDL — Modeling Definition Language — manifest) that
  describes tables, columns, joins, business metrics, and synonyms. The agent retrieves
  relevant pieces of this layer to ground its SQL.
- **How the semantic layer gets built (two paths):**
  - **Onboarding** — introspects the database schema and creates a *base* MDL (one
    model per table, structure only, no business meaning). Deterministic.
  - **Enrichment** — reads a **BI glossary document** (business definitions, jargon,
    custom metrics) and rewrites the MDL to encode that meaning. LLM-driven,
    non-deterministic.
  - **MDL Copilot** — a newer, agentic chat path that does onboarding/enrichment via
    multi-step tool calls (the product's actual UX), distinct from the deterministic
    endpoints above.
- **Coverage** — an intrinsic quality metric: the % of the BI glossary's claims that
  the active MDL actually captures (LLM-judged).
- **The evaluation question.** *How much does each grounding strategy improve
  text-to-SQL answer quality, and does the semantic layer earn its cost?* We measure
  this by asking the agent a fixed set of business questions and grading its executed
  SQL against known-correct answers.

### The test fixture (see `bi_glossary.md`, `test_queries.md`, `tables.json`)

- A synthetic **hard-disk manufacturing** dataset ("Seagate"), deliberately written so
  the business meaning is **only** in the glossary, not guessable from column names
  (floor staff use diner slang: a "patty" = a drive, "86'd" = scrapped units,
  "Golden Yield" = a custom metric, "Tigerline region" = two specific sites, etc.).
- **18 graded questions in 5 difficulty tiers** (L1 jargon → L5 chained cross-schema),
  including one **trap** (Q12) whose correct answer is a *refusal* (the metric is
  undefined for that slice).
- **v2 is multi-schema.** The data is split across two schemas — `seagate_core`
  (master/reference tables) and `seagate_ops` (transactional tables) — so most
  questions now require **cross-schema joins**. The data is byte-identical to the
  earlier single-schema fixture, so the ground-truth answers are unchanged; only the
  join difficulty increased. Three net-new questions (Q16–Q18) can *only* be answered
  by joining across the schema boundary.
- **Distractor tables.** Seven irrelevant tables the glossary never mentions are
  added to test whether the agent ignores them. Three are **adversarial** — their
  column names collide with the jargon (`finance_ledger.units`,
  `iot_sensor_logs.temperature_c` + a shared `line_id` foreign key,
  `hr_roster.shift_code`). Two more sit in an out-of-scope third schema
  (`seagate_ref`) that the project should never pull in.

### The conditions and experiments (methodology — one slide each is ideal)

**Grounding conditions (the core ablation), graded out of 18:**
- `basic` — DB only, no semantic layer (lower bound).
- `context_dump` — the entire glossary pasted into the prompt (no layer).
- `wren_base` — onboarded base MDL (structure only).
- `wren_bi` — MDL enriched from the glossary.

**Experiments (each is a distinct question; designs are in `EVAL_V2_SPEC.md`):**
- **E10 — cross-schema correctness.** Run the four conditions on the split fixture.
  Does grounding still help when joins span schemas? Tracks both total score and the
  cross-schema-only Q16–Q18.
- **E9 — distractor discrimination.** With irrelevant tables present, does the agent
  avoid them? Two measures: (a) *table selection* — precision/recall of which tables
  end up in the active MDL vs the relevant set; (b) *query-time leakage* — does any
  generated SQL reference a distractor table?
- **E6 — repeated-run convergence.** Enrichment is non-deterministic; the hypothesis
  was that re-running it on the same document recovers lost coverage. Measure coverage
  and graded score across 5 successive enrichment rounds; find the "knee" (N\*).
- **E7 — coverage as a metric.** Does the cheap intrinsic coverage % track the
  expensive graded score? (If so, future evals can skip the full sweep.)
- **E8 — Copilot vs deterministic.** The product's real UX is the agentic Copilot, not
  the deterministic endpoints the legacy eval used. Drive the Copilot and compare.

**Two execution modes were used and should be mentioned:** a **headless runner**
(scripted, `run_eval_v2.py` → `summary.json`) and **Jupyter notebooks** (one per
experiment). Both hit a live agent over HTTP.

### Critical methodology caveats (put these on a "How to read these numbers" slide)

- **Single trial.** Most numbers are one run. The LLM is non-deterministic; `wren_bi`
  ranged **5–10 / 18 across five runs**. Trust the *orderings and structural findings*,
  not any single cell. ≥3–5 trials is the stated requirement for firm numbers.
- **Fairness control.** A durable "learning loop" that recalls past SQL was **disabled**
  for the run (otherwise later conditions cheat by recalling earlier ones' SQL). This
  setting is **not** exposed by any API — the operator must set it; the harness can
  only warn. State this as a known limitation.
- **Agent version skew.** The running agent image was **older than the code** (its
  source is baked into the image), which blocked one experiment (E8) — call this out
  as an environment finding, not a product defect.
- **Number-match ≠ logic-correct.** A question can be scored correct when the right
  number appears for the wrong reason; the scorer can't always tell.

### The results to convey (pull exact figures from `RESULTS_v2.md`; summary in the appendix below)

Headline arcs the deck should make land:
1. **The semantic layer earns its keep specifically on cross-schema questions.** On the
   net-new cross-schema-only questions, the enriched layer (`wren_bi`) **beat** the raw
   context-dump — the *opposite* of the legacy small-schema result where dumping the
   glossary won. This is the central "what works" story.
2. **Enrichment ≈ doubles the base layer**, and is the only condition that learns a
   custom-metric rule (True Pass Rate). Base layer alone adds little.
3. **What doesn't work / risks:**
   - **Distractor discrimination is weak.** Deterministic onboarding models *every*
     table in scope (it included 5 of 7 distractors); only schema-scoping filtered
     anything. Adversarial distractors (shared FK, decoy column names) **leak into
     generated SQL** in every condition except the full context-dump.
   - **Repeated enrichment doesn't help** (counterintuitive): coverage plateaus after
     one round and graded score *drifts down* over rounds → "enrich once."
   - **Hardest chained cross-schema questions (L5/L4) fail across the board.**
4. **Two bugs were found and fixed mid-run** (a fixture data-loading collision and a
   harness/agent activation incompatibility) — worth a short "rigor/process" slide.
5. **Open items / next steps:** multi-trial runs for confidence; rebuild the agent to
   unblock the Copilot comparison (E8); investigate a model-count inflation anomaly in
   enrichment; gold-label coverage to de-noise E7.

### Deck requirements

- **Audience:** AI engineers. Assume fluency with LLMs, SQL, prompts, retrieval; do
  **not** assume they know this eval, the fixture, or the semantic-layer internals.
- **Length:** ~12–18 slides. Suggested flow: Title/TL;DR → Why this eval (legacy →
  multi-schema) → The fixture & questions → Conditions & experiments (methodology) →
  How to read the numbers (caveats) → E10 results (what works) → E9 results (distractors)
  → E6/E7 (convergence & coverage) → E8 (Copilot, blocked) → Bugs found → Takeaways &
  next steps → Appendix (full numbers, links to files).
- **Every results slide:** lead with the one-sentence takeaway, then the evidence
  (table/chart), then the caveat. Use a consistent ✓ / ✗ / ⚠ vocabulary.
- **Make the methodology legible:** for each experiment, a one-line "what it asks,"
  "how it's measured," and "what we found."
- **Visuals:** propose simple charts (a grouped bar of the four conditions; the E6
  coverage-vs-round line; an E9 precision/recall + leakage table). You may describe
  charts in text/ASCII if you can't render them.
- **Output format:** produce the deck as **Markdown slides** (one `---`-separated
  slide per screen, Marp/reveal-compatible) **with speaker notes** under each slide,
  *plus* a short "open questions for the team" final slide. If you prefer, also offer a
  PptxGenJS or python-pptx outline. Do not fabricate; where a number is missing, write
  "TBD — see <file>".

### Appendix — verified numbers (authoritative; cross-check against `RESULTS_v2.md`)

Run: 2026-06-29, Docker/Postgres, OpenAI `gpt-4.1-mini`, learning loop OFF, **1 trial**.

**Grounding ablation (correct / 18; two independent sweeps shown where available):**

| Condition | Sweep A (runner) | Sweep B (notebook 10) | Legacy single-schema (of 15) |
| --- | ---: | ---: | ---: |
| basic | 2 | 2 | 4.3 |
| context_dump | 9 | 11 | 13.7 |
| wren_base | 5 | 4 | 4.0 |
| wren_bi | 9 | 10 | 8.7 |

**Cross-schema-only (Q16–Q18, of 3):** basic 0; context_dump 0–1; wren_base 1;
**wren_bi 2** (the layer beats context-dump here).

**E9 distractor selection (active MDL after onboard+enrich):** precision **0.583**,
recall **1.000**, distractor-inclusion rate **0.714** (5 of 7 distractors modelled;
the 2 out-of-scope `seagate_ref` tables correctly excluded).
**SQL leakage:** `iot_sensor_logs` / `finance_ledger` appeared in generated SQL under
`basic` / `wren_base` / `wren_bi`; **`context_dump` had zero leakage.**

**E6 convergence (re-enriching the same glossary):** coverage 0.872 → 0.872 → 0.890 →
0.869 → 0.869 (plateau at round 1); graded 9 → 8 → 8 → 6 → 7 (drifts down); **N\* ≈ 1–2**.
Anomaly: active model count jumped 12 → 22 and stayed (only 12 tables in scope).

**E7 coverage:** `wren_bi` 0.417–0.872 across runs; `wren_base` undefined (no document
to reconcile against).

**E8 Copilot:** turn succeeded — glossary consumed as an attachment, **21 agentic
steps, 5-item changeset** — but activation was **blocked** (the running agent predates
the atomic bulk-activation endpoint the Copilot's overlay changeset requires). Grading
deferred; rebuild the agent to HEAD to unblock.

**Variance:** `wren_bi` over five runs = 9, 10, 5, 5, 10 (mean ≈ 7.8, range 5–10).

## PROMPT ENDS
