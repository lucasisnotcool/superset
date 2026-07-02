# Eval v4 — Results

**Fixture:** `seagate_multi` v4 (3 relevant schemas: `seagate_core`, `seagate_ops`,
`seagate_supply`) · **Model:** gpt-4.1-mini · **Trials:** 3 · **Memory:** OFF
(`WREN_MEMORY_STORE=none`, fair grounding ablation) · **Questions:** 30 (Q1–Q18
frozen from v3, Q19–Q30 new) · **Run:** matrix completed 2026-06-30, no errors.

Raw data: `results/seagate_multi_v4/{scoreboard.json,trials.json}`. Plan/design:
[EVAL_V4_SPEC.md](EVAL_V4_SPEC.md). Runner: `run_eval_v4.py`.

> **Update (2026-07-01):** §7 now includes a **post-fix re-run** of the two R1/R2
> feature probes against a rebuilt image (current `master`, commit `91f104256b`+).
> **R1 (cross-schema golden recall) is confirmed fixed** — full reversal, 0/3→3/3 on
> both held-out questions. **R2 (view surfacing) is partially fixed** — views are now
> occasionally selected organically for the first time, but correctness lift is still
> unproven. Sections 1–6 (the 8-config matrix) are unchanged and require no re-run —
> see §7.4 for why the matrix result is provably unaffected by either fix.

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

4. **Re-probe after R1/R2 fixes — done, see §7.** R1 is fully fixed (confirmed live,
   3/3 vs 0/3 reversal). R2 is partially fixed (organic view selection now happens,
   correctness lift still unproven). Neither fix touches the 8-config matrix itself
   (§7.4) — the next step is a v5 matrix variant whose enrich flow also authors
   views, to finally measure whether R2 narrows the §2 −12 gap in practice.
5. **Add a high-distractor variant** to give auto-onboard's precision something to
   earn (§3) — currently auto vs manual is a wash because over-selection is unpunished.
6. **Keep the deterministic-onboard track as the stable benchmark substrate**
   (auto-onboard adds variance, §5).
7. **Follow up on the paraphrase-recall gap (§7.2)** — a promoted golden's *exact*
   phrasing now recalls and lifts accuracy every time, but a reworded version of the
   same question doesn't reliably benefit even when recall fires. Worth an
   E17-style dedicated probe once R1's headline result has been socialized.

---

## 7. Feature probes — R1/R2, pre-fix and post-fix

Two confirmed product bugs from the pre-fix baseline (§7.1 original capture, memory
regime per probe noted inline). **A fix for both landed in commit `91f104256b`
("Add eval v4, add queries, views, api cost ui, autorecovery", 2026-06-30 20:46)** —
after every pre-fix capture below. The stack was rebuilt from current `master`
(`make up-ai`) and both probes were **re-run live against the patched image** to
confirm. Raw: `results/seagate_multi_v3/{query_lift,e14b_surfacing,golden,golden_singleschema_lift}.json`
(overwritten in place by the post-fix run; pre-fix numbers are preserved below since
they were already transcribed here before the re-run).

### 7.1 R2 — views: authoring is clean; surfacing is now partially fixed, correctness lift unproven

**Authoring (E13, memory OFF):** clean both pre- and post-fix, no regression.
Pre-fix: `proposed=3 active=3 semantic=3 description_rate=1.0 phys_leak=0`. Post-fix
(2 fresh trials): `proposed=3 active=3` and `proposed=2 active=2` (E15 variant),
same `semantic=N native=0 desc=N phys_leak=0` shape both times — the Copilot
reliably authors well-formed semantic views regardless of the fix.

**Query-time surfacing (E14, memory OFF) — before vs after:**

| Condition | Pre-fix | Post-fix |
|---|---|---|
| views deactivated | wrong ×6, n/a | wrong ×6, `used_views=[]` |
| **views active (organic retrieval)** | wrong ×6, **`used_views=[]` on all 6/6** | wrong ×5 / **correct ×1** (Q17), **`used_views` non-empty on 1/6** (Q18 pass 1 → `['standard_golden_yield_by_family']`, still graded wrong) |
| view force-surfaced (e14b) | wrong ×3, `used_view=true` (used, no lift) | not re-run (mechanism already proven pre-fix; not the fix's target) |

**Read:** the fix (`schema_retriever.py`, `_view_items`) is real and live — for the
first time ever in this eval, the retriever **organically selected an authored view**
without forcing (0/6 → 1/6). That confirms the surfacing gap is now partially
closed. But it isn't reliable yet (5/6 selections still empty) and, critically, the
one case where a view *was* selected still produced a wrong answer — echoing the
force-surfaced finding (used ≠ correct). **Verdict: R2 partially fixed at the
retrieval layer; the "using a view correctly" gap is untouched.** Not yet enough
signal to re-measure the §2 −12 gap-closure KPI — that needs a live `wren_bi` vs
`wren_bi_context` re-run once view authoring is wired into the matrix's enrich flow
(it currently isn't; see §7.4).

### 7.2 R1 — cross-schema golden recall: fixed, and it flips both test questions to 3/3

**E16 golden recall, memory ON (lancedb) — before vs after:**

| Question | scope | Pre-fix `recalled` (w/ golden) | Post-fix `recalled` | Pre-fix verdict | Post-fix verdict |
|---|---|---|---|---|---|
| Q16 warm-line output | cross-schema | **[0, 0, 0]** | **[1, 1, 1]** | wrong ×3 | **correct ×3** |
| Q17 Golden Yield Q4 | cross-schema | **[0, 0, 0]** | **[2, 2, 2]** | wrong ×3 | **correct ×3** |
| single-schema control | single-schema | [2, 3, 3] | not re-run (already working; not the fix's target) | correct | — |

**Read: full reversal.** A promoted cross-schema golden is now recalled every time
(non-zero, growing as more goldens get promoted — 1 then 2 — exactly matching
`golden_entries`), and both cross-schema questions flip from **0/3 correct to 3/3
correct** with the golden active. This is a clean, complete fix of the fail-closed
recall bug, reproduced across 3 repeats each.

**One residual, separate signal (not the R1 bug):** the **paraphrase** arm is mixed.
Q16's paraphrase stays wrong ×3 even though recall now fires (`recalled=[2,2,2]`) —
recall retrieves something, but doesn't help a differently-worded question. Q17's
paraphrase was already correct at baseline, so no lift is measurable there either
way. This looks like a distinct, smaller gap (recall → applying a recalled example to
a reworded question) worth a dedicated follow-up probe, not evidence against the R1
fix itself.

### 7.3 Net verdict against the pre-registered success KPIs (§7.3 original)

| KPI (as pre-registered) | Result |
|---|---|
| R2: `used_views` non-empty when active | **Partially met** — 1/6 organic selections (was 0/6), still no correctness lift |
| R2: `wren_bi` closes part of the −12 gap to `wren_bi_context` | **Not yet measurable** — the 8-config matrix's enrich flow never authors views (§7.4), so this KPI needs a new run, not a re-read of existing data |
| R1: cross-schema `recalled > 0` with golden active | **Met — fully** — 1,1,1 and 2,2,2, both non-zero every repeat |
| R1: `golden` capability score rises above its ~0.7/1 ceiling | **Directionally confirmed** by the underlying mechanism (0/3→3/3 correct on both held-out golden questions), though the §4 matrix number itself wasn't re-collected (golden queries aren't part of the 8-config matrix's grading path either — same caveat as R2 above) |

### 7.4 Why the 8-config matrix (§1–§5) was correctly left as-is

Confirmed by reading the fix diffs directly and the v4 runner: **R1 is inert unless
memory is ON**, and the matrix ran with `WREN_MEMORY_STORE=none` throughout (by
design, for a fair grounding ablation) — so R1 could not have altered any matrix
number. **R2's `_view_items` is purely additive** (no-op when `manifest.views` is
empty), and `run_eval_v4.py`'s onboard/enrich flow (`manual_enrich`,
`copilot_enrich_pass` / `COPILOT_ENRICH_MESSAGE`) never authors a view — only
definitions, synonyms, metrics, rollups, and calendar mappings. So no manifest in
the 8×30×3 matrix ever contained a view, and R2 could not have altered a matrix
number either. **The §1–§5 scoreboard and headline findings stand unchanged** — the
re-run was correctly scoped to the two feature probes only, not the full matrix.

The §2 headline finding — enrichment not reaching the retrieved layer — is therefore
still open. R2's fix addresses one *channel* into that gap (authored views can now
occasionally surface) but the matrix's own enrichment path (definitions/synonyms/
metrics) was never broken in the same way and still underperforms raw context. A
future v5 matrix run that has the enrich flow also author views, post-fix, is the
natural next step to see whether §2's −12 gap narrows in practice.

**Mandatory restore applied:** memory was toggled `none` → run E13/E14 → `lancedb`
(and the agent container recreated each time) → run E16 in the correct regime. The
stack is left with `WREN_MEMORY_STORE=lancedb` / `WREN_MEMORY_LEARNING_ENABLED=true`,
its standard operating state.
