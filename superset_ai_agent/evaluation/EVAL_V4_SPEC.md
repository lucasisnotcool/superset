<!--
v4 evaluation spec — the consolidated, reusable benchmark + science platform.
Supersedes the ad-hoc v2/v3 experiment scripts as the single source of truth for
how we measure the agent each iteration. Status: PROPOSED (awaiting sign-off before
the live full-suite rerun; depends on the in-flight R1/R2 product fixes).
-->

# Eval v4 — Consolidated Benchmark & Science Platform

## 0. Why v4 exists

v2/v3 grew organically into ~17 experiments across 11 notebooks + 3 runners. The
results are real but **not comparable across iterations** (different fixtures,
trial counts, condition subsets) and **don't cover the whole agent** (audit below).
v4 reconsolidates everything into **one parameterised runner + one versioned
fixture + one scoreboard**, so every future iteration produces a row in the same
table and we can watch the agent improve (or regress) over time.

Design goals:
1. **One matrix.** Every config is a point in `(grounding × onboard)` and every
   question carries `capability` tags → the scoreboard is `config × capability`.
2. **Comparable.** Versioned fixture + versioned ground truth + fixed trial policy
   → numbers mean the same thing next quarter.
3. **Control onboard cost/variance.** Onboard **once per type per trial**, snapshot,
   reuse across the grounding modes (no re-onboarding per mode).
4. **Cover the gaps.** Add experiments for the audited blind spots (view surfacing
   post-fix, cross-schema golden recall post-fix, intent/clarification, repair,
   distractor-avoidance, 3-schema joins).
5. **Reuse, don't rebuild.** v2/v3 harness code (`eval_common`, `eval_v2`,
   `eval_v3`, `seagate_scoring`) stays; v4 is an orchestration + fixture layer on top.

---

## 1. The config matrix (8 configs)

Five grounding modes, with the **onboard dimension** applied to the three that use a
Wren MDL:

| # | config | MDL? | onboard | what it isolates |
|---|--------|------|---------|------------------|
| 1 | `basic` | no | — | raw-schema text-to-SQL floor |
| 2 | `context_dump` | no | — | full glossary in prompt, no layer |
| 3 | `wren_base · manual` | base | deterministic `/onboard` | structure-only layer, all-table onboard |
| 4 | `wren_base · auto` | base | Copilot selective onboard | structure-only layer, selective onboard |
| 5 | `wren_bi · manual` | enriched | deterministic onboard→enrich | BI semantics, all-table onboard |
| 6 | `wren_bi · auto` | enriched | Copilot onboard→enrich | BI semantics, selective onboard |
| 7 | `wren_bi_context · manual` | enriched | deterministic | enriched layer **+** glossary in prompt |
| 8 | `wren_bi_context · auto` | enriched | Copilot | enriched layer **+** glossary in prompt |

= **2 non-wren + 3 wren × 2 onboard = 8**, exactly as requested.

### 1.1 Onboard-once-snapshot-reuse (the cost/variance control)

The expensive, high-variance step is onboarding. We do it **once per onboard-type
per trial** and reuse the result across the three grounding modes, by **ordering the
sweep within one project** so no MDL state is rebuilt:

```
per trial, per onboard-type ∈ {manual, auto}:
   onboard            → BASE layer active
   run wren_base sweep            ← grade all questions on the base layer
   enrich (same type) → ENRICHED layer active (base superseded)
   run wren_bi sweep              ← grade all questions on the enriched layer
   run wren_bi_context sweep      ← same enriched layer, glossary added to the prompt
```

- **1 onboard + 1 enrich per type per trial.** 3 trials → **3 manual + 3 auto
  onboards total** (the requested "3 onboards each"), never 6/trial.
- `wren_base` is graded *before* enrichment supersedes it (snapshot-by-ordering);
  `wren_bi_context` reuses the enriched layer and only changes the *prompt*
  (glossary appended) — no new onboard.
- `basic` / `context_dump` need no project.
- **manual** = deterministic `/onboard` (models every in-schema table; the all-table
  baseline, distractor precision ~0.58) then deterministic enrich. **auto** = Copilot
  `auto_onboard` (selective, precision ~1.0 in v2 E11) then a Copilot enrich pass.
- Memory **OFF** for the whole grounding matrix (clean F1 ablation, parity with
  v2/v3). The golden-query / shared-memory experiments run separately with memory ON.

### 1.2 What the matrix answers (the headline science questions)
- Does the semantic layer beat raw context, and **where** (per-capability, esp.
  cross-schema)? — modes 1/2 vs 3–8.
- Does **enrichment** add over structure? — `wren_base` vs `wren_bi` (per onboard).
- Is **auto-onboard** better than all-table onboard, held against the same grounding
  modes? — `·auto` vs `·manual` (generalises v2 E11 beyond a single number).
- Is `wren_bi_context` (belt-and-suspenders) worth the token cost over `wren_bi`?

---

## 2. Capability taxonomy (per-question tags)

Every question is tagged with one or more capabilities so the scoreboard reports a
**`config × capability` breakdown**, not just a single score. This is what makes v4 a
*diagnostic* benchmark (you see *what* improved), and lets us add questions per
capability over time.

| tag | capability | why it discriminates |
|-----|-----------|----------------------|
| `slang` | glossary-only term→column mapping | basic/wren_base fail (no doc); context_dump/wren_bi pass |
| `join1` | single-schema join | structure suffices |
| `xschema2` | 2-schema join (core↔ops) | needs relationships/manifest; cross-schema layer earns keep |
| `xschema3` | **3-schema join** (supply→core→ops) | NEW; deepest join, bridge table |
| `bridge` | many-to-many / bill-of-materials aggregation | NEW; needs the bridge, not derivable from a dimension |
| `metric` | glossary-defined custom metric (Golden Yield, True Pass Rate, Supply Reliability) | doc-only formula; enrichment must capture it |
| `trap` | exclusion-rule trap (answer is "undefined") | tests rule internalisation vs mechanical formula |
| `negative` | correct answer is 0 / none | NEW; robustness vs hallucinated rows |
| `temporal` | calendar reasoning (Diner Week Wed–Tue, fiscal quarter, recency) | non-standard calendar in the doc |
| `multihop` | chained multi-step | compounding error surface |
| `distractor` | adversarial decoy column must NOT be used | NEW explicit; tests selectivity at query time |
| `viewable` | reusable pattern best served by a view | feeds the view-surfacing experiment (E14′) |
| `golden` | niche/hard question a verified golden query should rescue | feeds the golden-recall experiment (E16′) |

---

## 3. Question set v4

**Q1–Q18 are frozen** (existing ground truth, byte-identical core data) and gain
capability tags. **Q19–Q30 are new**, each justified below; ground truth was computed
locally from the generator data (pure pandas, same seed) and is reproducible by
re-running `generate_data.py` (which prints every answer).

### 3.1 Existing Q1–Q18 → tags (no value change)
- Q1–Q4 `slang`; Q5 `slang,join1`; Q6–Q8 `slang,xschema2`; Q9–Q10
  `metric,xschema2`; Q11 `slang,temporal`; **Q12 `trap`**; Q13–Q15
  `metric,multihop,xschema2,temporal`; Q16–Q18 `xschema2,metric,multihop` (cross-schema-only).

### 3.2 New questions Q19–Q30 (with justification + ground truth)

| Q | capability | question (abbrev) | ground truth | what it tests / why added |
|---|-----------|-------------------|--------------|---------------------------|
| **Q19** | `xschema3,bridge` | Platters consumed plating **Vantage** drives in Q4 2025 | **14,300** | NEW 3-schema join supply.`sku_components`→core.`drive_skus`→ops.`work_orders`→`events`; platters-per-drive lives **only** in the bridge → not derivable from a dimension. Discriminates layers that model the bridge from raw context. |
| **Q20** | `xschema3,bridge` | Platters consumed plating **each family**, Dec 2025 | Cobalt 5124, Vantage 4380, Tundra 1878, Nimbus 1048 | 3-schema + grouped; multi-row grading. |
| **Q21** | `negative,xschema2` | Patties plated on **Tundra** WARM lines | **0** | NEW negative-result: Tundra has no WARM line → correct answer is "none". Catches agents that hallucinate a number or drop the family filter. |
| **Q22** | `temporal` | Patties plated in the Diner Week **2025-12-24 → 12-30** (Wed–Tue) | **378** | NEW explicit non-standard-calendar test (the doc defines Diner Week Wed–Tue); basic/wren_base lack the calendar rule. |
| **Q23** | `distractor` | Total patties **86'd** company-wide in Q4 2025 | **228** | NEW adversarial: `seagate_finance_ledger.units` is a decoy "units" column; a correct answer uses `production_events.units_scrapped`, never the ledger. Query-time distractor-avoidance (E9 measured *modeling* leakage; this measures *answering* leakage). |
| **Q24** | `metric,multihop` | Tigerline **True Pass Rate**: Taste Test vs Heat Lamp | 0.922 / 0.935 | NEW multi-metric within a region; exercises the True Pass Rate garnish-exclusion rule on two test types. |
| **Q25** | `slang` | Which drive family has the **highest average capacity**? | **Vantage** (15.0 TB) | NEW easy single-table control (should be ~all-modes-correct) — anchors the low end of the difficulty curve so the scoreboard has a floor. |
| **Q26** | `golden,xschema3,bridge` | "Critical-component load": **actuators** consumed plating STANDARD **Cobalt** tickets in 2025 | computed at gen (`build` prints) | NEW niche/hard golden target — a very specific 3-schema+bridge+ticket-type slice that all 8 modes are expected to miss; used to show a **promoted golden query** rescuing it (compare all 5 grounding modes ± golden). |
| **Q27** | `metric` | **Supply Reliability** for Cobalt SKUs (glossary-only new metric) | computed at gen | NEW custom metric defined *only* in the v4 BI doc → isolates enrichment's ability to capture a brand-new metric (vs Golden Yield which the agent may have memorised). |
| **Q28** | `viewable,xschema2` | Standard report: plated units by family **and** line status | computed at gen | NEW view-shaped reusable pattern → the E14′ view-surfacing probe (does a published view get used?). |
| **Q29** | `trap,negative` | **Supply Reliability** for Short-Order-only Nimbus | undefined / n.a. | NEW second trap on the new metric (Short Orders excluded by definition) → tests rule-internalisation generalises beyond Golden Yield. |
| **Q30** | `temporal,multihop` | Combo Dine-In Nimbus units in the **last fiscal quarter**, Tigerline vs Reef | = Q18 units (alias window) | NEW phrasing-robustness: "last fiscal quarter" must resolve to Q4 2025; tests temporal aliasing without changing the underlying answer. |

> Q26/Q27/Q28 ground truth is emitted by the extended `generate_data.py`
> ground-truth printer and transcribed into `seagate_scoring.EXPECTED` when the
> fixture is regenerated (same workflow as Q16–Q18).

### 3.3 Discrimination design (the point of the set)
A good benchmark spreads modes apart. The set is built so that, ideally:
- `basic` passes only `join1`/easy (`Q25`), fails `slang`/`metric`/`xschema*`.
- `context_dump` / `wren_bi_context` pass `slang`/`metric` but may leak on
  `distractor` (v2: decoys leak except under context_dump) and degrade on deep
  `xschema3`.
- `wren_bi` earns its keep on `xschema2`/`xschema3` (relationships encoded once).
- `golden`/`viewable` questions stay failed across all 8 until the **feature
  experiments** (golden recall, view surfacing) turn them on — that delta *is* the
  feature's measured value.

---

## 4. DB complexity v4 — `seagate_supply` (3rd relevant schema)

Added **without touching the frozen core** (parity assertion still guards Q1–Q18).
New, fully-deterministic, relevant schema enabling 3-schema joins + a bridge:

- `seagate_supply.seagate_components` — component **dimension** (6 rows): Platter,
  Spindle Motor, Actuator, Controller Board, Bracket Kit, Firmware Image; each with
  `component_type` + `criticality` (CRITICAL/STANDARD).
- `seagate_supply.seagate_sku_components` — **bridge** (8 SKUs × 6 components = 48
  rows): `(sku_id, component_id, qty_per_drive)`. `qty_per_drive` for Platter is the
  bridge-only fact that powers Q19/Q20 (not derivable from `capacity_tb`).
- (optional) `seagate_supply.seagate_supplier_deliveries` — a small fact for the
  **Supply Reliability** metric (Q27/Q29) + a supply-schema **distractor**
  (`seagate_supply.seagate_freight_invoices`, another decoy `units`).

Relevant set grows 7 → 9 (or 10 with the deliveries fact); distractor pressure is
preserved (a supply distractor is added so selectivity is still tested across 3
schemas). The generator computes + prints all new ground truth; `tables.json` gains
the supply schema so E9/E11 selection metrics extend automatically.

**Cross-iteration stability:** the fixture is versioned (`FIXTURE_VERSION = "v4"` in
the generator). Any change that alters a frozen answer fails the parity assertion;
new complexity only ever *adds* tables/questions.

---

## 5. BI doc v4 additions

The glossary (`bi_glossary.md`) gains, additively (existing slang/metrics frozen):
- **Supply section:** component slang (a "platter" = the disk inside a patty; a
  "spinner" = Spindle Motor; "the brains" = Controller Board), the bill-of-materials
  join path (supply→core→ops), and the `criticality` rule.
- **New custom metric — Supply Reliability** = on-time deliveries / total deliveries
  over `supplier_deliveries`, STANDARD-ticket-scoped, **Short Orders excluded**
  (mirrors Golden Yield's exclusion so Q29 is a real trap). Defined *only here* so
  enrichment capture is measurable (Q27).
- **Calendar:** an explicit "fiscal quarter = calendar quarter" line so Q30's "last
  fiscal quarter" resolves deterministically.
- **Standard reusable reports** (the `views_addendum.md` content) folded in as a
  named section so the view-authoring + view-surfacing experiments draw from the
  same doc.

---

## 6. Experiments reconsolidated

### 6.1 Core matrix (the full-suite rerun)
- **M (the 8-config grounding matrix)** over **all 30 questions**, **3 trials**,
  memory OFF, onboard-once-reuse. Output: the scoreboard (§7). Subsumes legacy
  E1–E5, E10 (cross-schema), and the onboard dimension of E11.

### 6.2 Feature experiments (kept, refreshed against the v4 fixture)
- **E9′ distractor discrimination** — selection precision/recall across **3 schemas**
  now (manual vs auto onboard); plus the new **query-time** distractor-avoidance via
  Q23.
- **E12′ enrich-once** — K∈{0,1,2} enrich passes on the auto layer; confirm the
  v2/v3 "enrich once" finding holds on the richer fixture.
- **E14′ view surfacing** (post-R2) — publish a valid view (Q28 pattern); does the
  agent use it now that views are indexed into retrieval? Measures the fix.
- **E16′ golden cross-schema recall** (post-R1) — promote a verified golden for the
  niche Q26; re-run all 5 grounding modes ± golden; the **rescue delta** is the
  feature's value. Memory ON.
- **E18 intent/clarification** (NEW, audit gap) — feed ambiguous / non-SQL prompts;
  assert the router returns `clarify`/`general` not a hallucinated SQL.
- **E19 repair loop** (NEW, audit gap) — seed an invalid draft; assert the validator
  feedback → repair produces valid SQL within `max_repair_attempts`.
- **E20 execution-mode safety** (NEW, audit gap) — assert DDL/DML is blocked and
  `execution_mode` gates auto-run. (Mostly assertion-level, cheap.)

### 6.3 Offline invariants (kept)
- The 66 product unit tests (F1 DB-scoping, F2 fail-closed RBAC, golden kind
  validation, view deep-validation) + the eval harness unit tests run every time as
  the regression floor.

---

## 7. Scoreboard (the comparable artifact)

One JSON + one markdown table per run, versioned by `(fixture_version, agent_git_sha,
date, model)`:

```
scoreboard.json
  meta: {fixture_version, agent_sha, model, date, trials, memory}
  by_config:   { config → {correct/30 mean[min-max], by_capability: {slang: x/ y, xschema3: …}} }
  by_capability: { capability → {best_config, basic, context_dump, wren_bi·auto, …} }
  deltas: { "wren_bi·auto − wren_bi·manual", "wren_bi − wren_base", "view_surfacing rescue", "golden rescue" }
```

The headline table is `config (8 rows) × capability (13 cols) + total`. Each future
iteration appends a row-set under a new `agent_sha`; a `compare(prev, curr)` helper
diffs them so we see per-capability regressions/improvements at a glance.

---

## 8. Run plan & cost

- **Phase 0 (this turn):** fixture + questions + BI doc + scorer + runner skeleton +
  offline tests. No live LLM. ✅ implementing now.
- **Phase 1 (after R1/R2 land + sign-off):** regenerate fixture, reload Postgres
  (`make up-ai` already loads examples; add the supply parquet), run the **8-config ×
  30-question × 3-trial** matrix (memory OFF) + E9′/E12′/E14′/E16′/E18/E19/E20.
- **Cost estimate:** matrix ≈ 8 configs × 30 q × 3 trials = 720 graded queries +
  (3 manual + 3 auto) onboards + 6 enrichments per trial set. At ~10–20 s/query that
  is ~3–5 h wall-clock; gpt-4.1-mini. Onboard-reuse keeps onboards at 6 total, not
  ~144. Trial count is the main cost knob (start at 3).

---

## 9. Open decisions (for sign-off)

| id | decision | recommendation |
|----|----------|----------------|
| **DV1** | Add the full `seagate_supply` schema (3-schema joins) vs. keep 2-schema only? | **Add it** — 3-schema joins + a bridge are the biggest realistic complexity gain and exercise the cross-schema fixes directly. |
| **DV2** | Trial count for the matrix (cost vs. confidence). | **3** (mean[min-max]); bump to 5 for a publication-grade baseline once stable. |
| **DV3** | Does `wren_base · auto` make sense (auto-onboard inherently reads the doc)? | **Yes, as "structure-only auto"** — run the Copilot onboard with a *structure-only* seed (select+onboard, no enrich) for the base snapshot, then a separate enrich pass for `wren_bi`. Keeps the base/bi distinction symmetric with manual. |
| **DV4** | Include the niche golden (Q26) in the matrix totals, or hold it out as a feature-only probe? | **Hold out of the matrix total**, report separately under E16′ (else it depresses every config equally and adds noise). |
| **DV5** | Gate the live rerun on R1/R2 (so E14′/E16′ measure the fix) or run now to capture the *pre-fix* baseline too? | **Both** — run the matrix + a pre-fix E14′/E16′ now-ish for a regression baseline, then re-run the two feature probes after the fixes to quantify the delta. |
| **DV6** | Promote v4 to the canonical suite and retire the v2/v3 notebooks? | **Keep v2/v3 as archived provenance**, make v4 the runnable canonical; notebooks become read-only history. |

---

## 10. What I am implementing in Phase 0 (this turn)
- `seagate_supply` schema in `generate_data.py` (+ ground truth printer, parity-safe).
- `test_queries_v4.md` (Q1–Q30 with capability tags) + `seagate_scoring` EXPECTED for
  Q19–Q30 (+ a `CAPABILITY` map).
- `bi_glossary.md` additive v4 section (supply slang + Supply Reliability + fiscal).
- `run_eval_v4.py` skeleton: the 8-config onboard-reuse loop + `scoreboard` builder +
  multi-trial aggregation, with the live steps stubbed behind the existing
  `eval_v3.AgentClientV3`.
- `test_eval_v4.py` offline tests (matrix expansion, capability scoreboard math,
  onboard-reuse ordering).
