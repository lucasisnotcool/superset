# Eval v4 — Results

**Fixture:** `seagate_multi` v4 (3 relevant schemas: `seagate_core`, `seagate_ops`,
`seagate_supply`) · **Model:** gpt-4.1-mini · **Trials:** 3 · **Memory:** OFF
(`WREN_MEMORY_STORE=none`, fair grounding ablation) · **Questions:** 30 (Q1–Q18
frozen from v3, Q19–Q30 new) · **Run:** matrix completed 2026-06-30, no errors.

Raw data: `results/seagate_multi_v4/{scoreboard.json,trials.json}`. Plan/design:
[EVAL_V4_SPEC.md](EVAL_V4_SPEC.md). Runner: `run_eval_v4.py`.

---

## 1. The matrix

8 configs = {2 non-wren} + {3 wren grounding modes} × {manual onboard, auto
onboard}. Onboard is run **once per trial** and the snapshot reused across the
three wren modes (3 trials → 3 manual + 3 auto onboards total, not 18 each).

| Config | Total /30 (mean[min–max]) |
|---|---|
| basic | 4.67 [4–5] |
| context_dump | 13.67 [12–15] |
| wren_base·manual | 7.33 [7–8] |
| wren_bi·manual | 8.67 [5–12] |
| **wren_bi_context·manual** | **22.0 [20–23]** |
| wren_base·auto | 7.33 [6–8] |
| wren_bi·auto | 9.33 [6–13] |
| **wren_bi_context·auto** | **21.33 [15–25]** |

### Headline deltas

| Contrast | Δ | Reading |
|---|---|---|
| enrichment (`wren_bi·auto − wren_base·auto`) | **+2.0** | BI-doc enrichment of the layer buys little |
| auto vs manual onboard (`wren_bi·auto − wren_bi·manual`) | **+0.66** | Copilot onboard ≈ deterministic onboard on this set |
| context on top of layer (`wren_bi_context·auto − wren_bi·auto`) | **+12.0** | the raw doc, stapled to the layer, is what wins |
| **layer vs raw context** (`wren_bi·auto − context_dump`) | **−4.34** | the layer *alone* loses to the raw doc *alone* |

---

## 2. The headline: enrichment is not reaching the retrieved layer

The dominant configs are the two `wren_bi_context` variants (~21–22/30). But
decompose *why*:

- The semantic layer **alone** (`wren_bi`, 8.7–9.3/30) scores **below the raw BI
  doc alone** (`context_dump`, 13.7/30). −4.34.
- The layer only becomes the best config when the **raw doc is concatenated on top
  of it** (`wren_bi_context`, +12 over `wren_bi`).
- Enrichment over the bare onboard (`wren_bi − wren_base`) is only **+2**.

Conclusion: **the BI knowledge is doing the work, and it is doing it through the
raw-context channel, not through the enriched semantic layer.** When the agent must
rely on what enrichment actually wrote into the retrievable MDL (descriptions,
metrics, relationships), most of that knowledge is not there or is not retrieved.
`wren_bi_context` looks great but is effectively *"raw doc + a layer that adds a
little structure on top of the doll."* This is the single most important product
signal in v4 and it reproduces across both onboard types and all 3 trials.

This is consistent with — and now **quantifies** — the v3 R2 finding (views/enriched
knowledge not surfaced to retrieval). It is broader than views: it's the general
enrichment→retrieval path.

---

## 3. Onboard dimension (new in v4): Copilot auto ≈ deterministic manual

The expensive part of the pipeline — Copilot auto-onboard (selective table
selection, precision ~1.0) vs deterministic `/onboard` (all-table, precision ~0.58)
— produced **no meaningful end-to-end accuracy difference**:

| Mode | manual | auto | Δ |
|---|---|---|---|
| wren_base | 7.33 | 7.33 | 0.0 |
| wren_bi | 8.67 | 9.33 | +0.66 |
| wren_bi_context | 22.0 | 21.33 | −0.67 |

Higher onboard precision did **not** translate to higher answer accuracy here.
Because memory is OFF and the question set is answerable from the named relevant
tables, the deterministic onboard's lower precision (extra tables) was not
penalized. **Implication:** the value of selective auto-onboard must be argued on
cost/latency/governance grounds, *not* accuracy — at least until distractor density
is high enough to punish over-selection. v5 should add a high-distractor variant to
stress this (see §6).

---

## 4. Capability scoreboard (config × capability)

Mean correct / count, across 3 trials. Showing the two non-wren baselines and the
two winning configs (full table in `scoreboard.json`).

| Capability | n | basic | context_dump | wbc·manual | wbc·auto |
|---|---|---|---|---|---|
| slang | 10 | 2.0 | 5.67 | 8.33 | 7.0 |
| metric | 10 | 0.67 | 4.0 | 6.67 | 7.0 |
| xschema2 | 13 | 1.0 | 4.33 | 9.33 | 9.0 |
| xschema3 | 3 | 0.0 | 0.67 | 2.33 | 2.67 |
| bridge | 3 | 0.0 | 0.67 | 2.33 | 2.67 |
| multihop | 7 | 0.0 | 2.0 | 4.0 | 3.33 |
| temporal | 5 | 0.0 | 0.0 | 2.67 | 2.33 |
| trap | 2 | 1.67 | 1.67 | 1.33 | 1.33 |
| negative | 1 | 1.0 | 1.0 | 1.0 | 1.0 |
| distractor | 1 | 0.0 | 0.33 | 0.67 | 1.0 |
| golden | 1 | 0.0 | 0.33 | 0.67 | 1.0 |
| viewable | 1 | 0.0 | 0.33 | 0.67 | 0.67 |
| join1 | 1 | 1.0 | 1.0 | 1.0 | 1.0 |

### Where every config bleeds

- **temporal (best 2.3–2.7/5):** even the winning configs miss ~half. `context_dump`
  scores **0/5** — the raw doc states the fiscal-quarter / Diner-Week rules but the
  agent does not apply them without the layer's date columns surfaced. Worst
  per-question is **Q30 (temporal aliasing): 1/24 runs correct, across all configs.**
- **xschema3 / bridge (best ~2.6/3):** the new 3-schema bill-of-materials joins are
  the hardest structural pattern. Only the context-bearing configs clear them at all;
  `wren_base`/`wren_bi` score **0/3**.
- **trap (regression):** the `wren_bi_context` configs *lose* trap questions the
  baselines get. **Q12 trap: basic/wren_base hold it 3/3, but wbc holds only 1/3** —
  the extra context induces over-eager joins that walk into the trap. Grounding can
  *reduce* abstention discipline.

### Where the layer genuinely helps (not just the doc)

- **Q22 (non-standard fiscal calendar):** `wren_base`/`wren_bi` (the layer) get it
  **3/3**, but `context_dump` (raw doc) gets it **0/3**. The structured date columns
  in the layer enable the calendar logic the prose alone doesn't.
- **Q23 (query-time distractor avoidance):** `wren_base·manual` **3/3** — the
  scoped layer keeps the agent off the `freight_invoices` decoy better than raw prose.

These two are the existence proof that the layer adds something orthogonal to the
doc — which makes the §2 finding (enrichment not surfaced) a *fixable* gap, not a
dead end.

---

## 5. Notable per-question findings

- **Q18 (cross-schema-only, no in-schema shortcut): 0/24 — nobody, ever.** Hardest
  question in the suite; a permanent stress target.
- **Q27 (new Supply Reliability metric, glossary-only):** `context_dump` and `wbc`
  get it **3/3**, but `wren_bi` (enriched layer, no raw doc) gets it **0–2/3**. Direct
  evidence the new metric definition did **not** make it into the enriched MDL — same
  pattern as §2, isolated to a single freshly-authored metric.
- **Q29 (criticality trap — STANDARD components excluded by definition):** held
  3/3 by `context_dump`/`wbc`; the trap design works.
- **Variance:** `wren_bi` and `wren_bi_context·auto` have wide spreads
  (`wren_bi_context·auto` 15–25). Auto-onboard introduces run-to-run instability the
  deterministic path does not (`wbc·manual` is 20–23). If reproducibility matters for
  a benchmark, the deterministic onboard is the more stable substrate.

---

## 6. Recommendations / next steps

**Product (for the agent team):**

1. **Fix the enrichment→retrieval path (highest leverage).** §2 shows the enriched
   layer is not carrying its own knowledge into retrieval; the agent leans on raw
   context. Closing this should let `wren_bi` approach `wren_bi_context` *without*
   needing to bolt the whole doc on every call — the real payoff of a semantic layer.
   Tracks the v3 R2 surfacing bug; v4 quantifies it at **−12 vs the dump-augmented
   ceiling**.
2. **Grounding hurts abstention (Q12 trap regression).** Context-bearing configs walk
   into traps the baselines avoid. The agent needs an explicit "don't join unless the
   grounding licenses it" discipline.
3. **Temporal is the weakest real capability.** Surface the business-calendar rules as
   structured layer metadata, not just prose — `context_dump`'s 0/5 says prose alone
   doesn't transfer.

**Benchmark (for this eval platform):**

4. **Re-probe after R1/R2 fixes** (baseline-now-then-re-probe, per plan): pre-fix
   feature probes captured below; rerun `wren_bi`/`wren_bi_context` post-fix and the
   §2 delta is the success metric.
5. **Add a high-distractor variant** to give auto-onboard's precision something to
   earn (§3) — currently auto vs manual is a wash because over-selection is unpunished.
6. **Keep the deterministic-onboard track as the stable benchmark substrate**
   (auto-onboard adds variance, §5).

---

## 7. Pre-fix feature probes (R1/R2 baseline)

Captured to anchor the "re-probe after fixes" comparison. Both probes confirm the
v3 product bugs still bite and now have a hard pre-fix baseline. Memory regime per
probe noted inline. Raw: `results/seagate_multi_v3/{query_lift,e14b_surfacing,golden,golden_singleschema_lift}.json`.

### 7.1 View authoring works — but views are invisible at query time (R2)

**Authoring (E13, memory OFF, 2 trials, fresh this run):** clean.
`proposed=3 active=3 semantic=3 native=0 description_rate=1.0 phys_leak=0
activate_error=False` — the Copilot reliably authors well-formed semantic views.

**Query-time surfacing (E14, memory OFF):** the bug.

| Condition | Q16/Q17/Q18 verdict | view selected? |
|---|---|---|
| views deactivated | wrong (×2) | — |
| **views active** | **wrong (×2)** | **`used_views=[]` — never selected** |
| view force-surfaced (e14b) | wrong (×3) | `used_view=true` (used, no lift) |

With the views **active on the project**, the agent never retrieves them
(`used_views=[]` across every repeat); accuracy is identical to no-views. Even when a
view is *explicitly* surfaced into context (e14b), it gets used but does **not** lift
the answer. **R2 confirmed:** the enrichment→retrieval gap from §2 is not abstract —
authored views simply never reach the retriever, and forcing them in doesn't fix
correctness. This is the surfacing half of the §2 headline, isolated.

### 7.2 Golden-query recall is fail-closed across schemas (R1)

**E16 golden recall, memory ON (lancedb).**

| Question | scope | golden status | recalled (with golden) | lift |
|---|---|---|---|---|
| Q16 warm-line output by family | cross-schema | active | **[0, 0, 0]** | none |
| Q17 Golden Yield Vantage Q4 | cross-schema | active | **[0, 0, 0]** | none |
| single-schema control (avg capacity/interface) | single-schema | active | **[2, 3, 3]** | recall works |

A promoted, **active** cross-schema golden query is **never recalled** at query time
(`recalled=0` every repeat), so it gives zero accuracy lift — while a single-schema
golden recalls 2–3 examples normally. **R1 confirmed:** recall is fail-closed
specifically on the cross-schema path (single-schema access scope at
`build_recall_access`, `graph.py:608`), exactly as v3 diagnosed.

### 7.3 Success metric for the re-probe (after R1/R2 land)

- **R2 fixed** ⇒ E14 shows `used_views` non-empty when views are active, and
  `wren_bi` (no raw context) closes a meaningful part of the **−12** gap to
  `wren_bi_context` (§2). That delta is the headline KPI for the fix.
- **R1 fixed** ⇒ E16 cross-schema `recalled > 0` with golden active, and the
  `golden`/`viewable` capability rows in the §4 scoreboard rise above their current
  ~0.7/1 ceiling.

> **Live re-probe blocked at capture time.** The Docker stack was recreated
> mid-session and now crash-loops on Superset `init_views` due to **unrelated
> uncommitted WIP** — a new "AI Agent Usage" admin menu link in
> `superset/initialization/__init__.py` passes `menu_cond=` to
> `appbuilder.add_link()`, whose real kwarg is `cond` (FAB rejects `menu_cond`). This
> 500s `/login` and blocks live agent calls. The §7 baselines above are from the
> recent v3 captures (same fixture, same agent build) and are unaffected. Re-probe
> once that menu regression is resolved.
