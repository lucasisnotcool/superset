<!--
v3 results — semantic VIEWS + golden QUERIES (and DB-scoped shared memory /
access-aware recall). Live run on the seagate_multi (multi-schema) fixture.
-->

# Eval v3 — Views & Queries: live results and next steps for Wren integration

**Run date:** 2026-06-30 · **Stack:** Docker (`make up-ai`, rebuilt from the working
tree — the Views/Queries code is uncommitted) · **LLM:** OpenAI `gpt-4.1-mini`
(unchanged from v2) · **Embedder:** OpenAI `text-embedding-3-small` · **Fixture:**
`seagate_multi` (3 schemas: `seagate_core` + `seagate_ops` + out-of-scope
`seagate_ref`; 7 relevant + 7 distractor tables) · **Memory regimes:** Views run
with `WREN_MEMORY_STORE=none` (clean grounding ablation, parity with v2); Queries
run with `WREN_MEMORY_STORE=lancedb` (the feature under test needs recall ON).
`.env` restored to `lancedb`/learning `true` after the run.

This pass tests the two features shipped after v2:

1. **Views** — semantic MDL views authored by the Copilot (Phase 1; native views
   not built). Specs: `plan_views_parity_spec.md` / `_impl.md`.
2. **Queries** — project-scoped **golden queries** (`queries.json`) + **DB-scoped
   shared memory** (F1) + **access-aware recall** (F2). Spec:
   `golden_queries_and_shared_memory_spec.md`.

Harness: `eval_v3.py` (+ `run_eval_v3.py`, `run_eval_v3_golden.py`); offline tests
`test_eval_v3.py` (13). Fixtures: `dev_fixtures/seagate_multi/views_addendum.md`,
`rawsql_addendum.md`.

---

## TL;DR (the three findings that should drive the roadmap)

1. **Views are plumbed end-to-end but invisible at query time.** A view
   authored → validated → activated → materialized is **queryable and correct**
   (engine inlines it; returns exact ground truth), but the agent **never selects
   from it on its own** — views are **not in the retrieval/context set**, so the
   "a view doubles as a recall example" promise is unrealized. When the agent is
   *explicitly* pointed at a view it gets the cross-schema answer **right**
   (Cobalt 1751 / Vantage 3017) where its from-scratch join is **wrong**. **The
   gap is surfacing, not execution.**

2. **Golden-query recall is silently broken for cross-schema queries** — the one
   case golden queries matter most. The F2 access filter builds its "accessible
   tables" set from the **request's single `schema_name`** (5 `seagate_core`
   datasets), so a golden query that references `seagate_ops` tables fails the
   **fail-closed Stage-A** check and is **dropped** (`recalled=0`). A
   *single-schema* golden recalls fine (`recalled` +1, "verified" signal fires) —
   proving the mechanism works and isolating the bug to **access scoping**.

3. **Authoring a *valid* semantic view from prose is unreliable; from correct SQL
   it is reliable.** Across 3 trials the Copilot proposed all 3 requested views
   but **0/3 activated** — it hallucinated a non-existent column
   (`seagate_shipments.units_shipped`) on the hardest view, and **atomic
   activation sank the whole changeset**. Given the *correct raw SQL* (the
   physical→model **name-substitution** task), **3/3 trials produced 2/2 valid,
   described, semantic views with zero physical-schema leakage** → **native views
   can be deferred** (resolves the spec's D6 / Step 6.5 gate).

**Unifying insight:** both features deliver *trusted SQL*, and in the **cross-schema**
case **neither reaches the model** — views aren't surfaced to retrieval, and golden
queries are access-filtered out by single-schema scoping. The common root is the
**query-time context/access path being single-schema while the manifest is
multi-schema** (the same class of gap tracked in the cross-schema query-time work).

---

## Part A — Views

### Claims under test (from the spec)
- **V1 Authoring:** the Copilot authors valid semantic views from a doc-grounded,
  reusable-pattern trigger (it previously dropped views).
- **V2 No false-green:** an invalid view (unknown column/model) fails the
  *activation gate*, not at query time (G2 deep validation).
- **V3 Query-time value:** a view improves text-to-SQL on view-shaped (multi-model
  / cross-schema) questions.
- **V4 Native-vs-semantic gate (D6):** does physical→model name-substitution yield
  acceptable semantic-view authoring? If yes, defer native views.

### E13 — View authoring from glossary + standard-reports addendum (memory OFF)

Deterministic onboard (12 models, both schemas) → Copilot turn authoring views from
the BI glossary **plus** a "standard reusable reports" addendum (3 cross-model
patterns). 3 trials.

| trial | proposed | activated | failure |
|------:|---------:|----------:|---------|
| 1 | 3 | **0** | `wren-core: No field named seagate_shipments.units_shipped` |
| 2 | 3 | **0** | `… No field named units_shipped` |
| 3 | 3 | **0** | `… No field named shipments.units_shipped` |

- The Copilot reliably **proposes** the right views (correct names:
  `warm_line_output_by_family`, `standard_golden_yield_by_family`,
  `region_channel_shipments`), each with a description.
- It **hallucinated** `units_shipped` on the shipments view (the real column is
  `qty_units`). **G2 deep validation correctly caught it** (V2 ✓ — no false-green).
- **But activation is atomic:** one bad view 422s the whole changeset, so the two
  *good* views never activate either. **0/3 usable.**

### E15 — Native-vs-semantic gate: author from correct raw physical SQL (memory OFF)

Same base; the addendum is the **correct legacy SQL over physical tables**; the only
burden is physical→model **name substitution**. 3 trials.

| trial | proposed | activated | semantic | native | desc-rate | physical-schema leak |
|------:|---------:|----------:|---------:|-------:|----------:|---------------------:|
| 1 | 2 | **2** | 2 | 0 | 1.0 | 0 |
| 2 | 2 | **2** | 2 | 0 | 1.0 | 0 |
| 3 | 2 | **2** | 2 | 0 | 1.0 | 0 |

- **6/6 views valid, semantic, described, no physical-schema leakage.** The
  name-substitution path is reliable.
- **Decision (D6 / Step 6.5): defer native views.** The model produces correct
  *semantic* views from raw SQL; the extra burden the native-view feature was meant
  to remove (semantic translation) is small and handled well. Reserve native views
  for genuinely *unmodeled/external* tables only.
- *Nuance:* "valid" = engine-valid (real columns), not provably *correct* — when the
  source SQL referenced a fuzzy concept the model coerced to a real column. Engine
  validation guarantees resolvability, not semantic correctness.

### E14 — Query-time value: do views lift cross-schema accuracy? (memory OFF)

Two **known-valid** hand-authored views (`warm_line_output_by_family`,
`standard_golden_yield_by_family`, real columns) activated on a fresh project; graded
the cross-schema questions Q16–Q18 with vs without the views (same models both times).

| condition | Q16 | Q17 | Q18 | agent used a view? |
|-----------|:---:|:---:|:---:|:------------------:|
| without views | wrong | wrong | wrong | — |
| **with views active** | wrong | wrong | wrong | **never (`used_views=[]`)** |

**Diagnosis (the important part):**
- The view is **absent from `wren_context`** — `matched_models` lists 8 models, **0
  views**; the view name never appears in the retrieved context. **Retrieval does
  not index/surface views.**
- The agent writes its own (wrong) join — e.g. for Q16 it joined
  `production_lines → production_events` on a **non-existent `line_id`**, skipping
  `work_orders`.
- **When explicitly told to use the view**, the agent selects from it and returns
  the **exact ground truth** (Cobalt 1751 / Vantage 3017 → `correct`); the engine
  inlines the view as a CTE. *(List-style phrasing is reliable; "how many…"
  sometimes collapses to a scalar — a phrasing artifact, not a view defect.)*

**Conclusion:** views work at the engine level but provide **zero automatic
query-time lift** because nothing makes the agent aware a relevant view exists. The
value is real (the view encodes the correct join the agent gets wrong) and is gated
entirely on **surfacing**.

---

## Part B — Queries (golden queries + shared memory + access-aware recall)

### Claims under test
- **Q1 Golden lift:** a verified golden query is recalled with priority and lifts
  accuracy on that question (and paraphrases).
- **Q2 Verified signal:** the answer is flagged as using a verified/golden query.
- **F1 Shared memory:** pairs are DB-scoped (owner dropped) — cross-user sharing.
- **F2 Access-aware recall:** a pair referencing an inaccessible table is dropped
  (fail-closed); out-of-scope pairs down-ranked; non-onboarded → `native_sql` only.

### E17 (offline) — F1/F2/F3 invariants are unit-verified ✓

`pytest tests/unit_tests/superset_ai_agent/{test_golden_queries,test_memory_store,
test_mdl_authoring_views,test_wren_core_validator,test_mdl_compile}.py` → **66
passed, 1 skipped**. This covers the security-critical invariants at the correct
layer: DB-scoped (not owner-scoped) recall, **Stage-A fail-closed** RBAC filter,
Stage-B down-rank, Stage-C `semantic_sql` stripping, golden kind-aware validation,
golden manifest-isolation, and the copy-not-move promote invariant.

### E16 (live) — golden-query recall lift

Memory ON (`lancedb`). Base project (deterministic onboard, 12 models). For each hard
cross-schema question: baseline (no golden) → promote + activate a **verified** golden
with correct semantic SQL → re-ask. 3 repeats each.

| case | baseline (q / paraphrase) | with golden | `recalled` | verified signal |
|------|---------------------------|-------------|:----------:|:---------------:|
| **Q16** warm-line by family (cross-schema) | wrong / wrong | wrong / wrong | **0,0,0** | no |
| **Q17** Golden Yield Vantage Q4 (cross-schema) | wrong / 1×correct | wrong / 2×correct | **0,0,0** | no |

**The golden was never recalled** (`recalled=0`) → no lift. **Root cause traced live:**

- The golden's physical refs resolve correctly:
  `{seagate_core.seagate_drive_skus, seagate_core.seagate_production_lines,
  seagate_ops.seagate_production_events, seagate_ops.seagate_work_orders}`.
- But the recall **access set** is built from `context.datasets`, and the query path
  logs **`Loaded 5 dataset(s)`** — only the request's primary schema
  (`seagate_core`). The `seagate_ops` tables are **absent** from the accessible set.
- `_pair_is_accessible` requires **every** referenced table to be accessible and
  **fails closed** otherwise → the cross-schema golden is **dropped**.

**Contrast that isolates the bug (live):**
- A **single-schema** golden (refs only `seagate_core.seagate_production_lines`):
  `recalled` goes **1 → 2** after promotion and the response carries the **"verified"
  signal**. Mechanism works *within the request schema*. ✓
- Passing `schema_names=[core,ops]` in the request **does not** expand the loaded
  set (still "5 datasets") — single-schema loading is wired into `_load_context`.
- Worse: a *topic-similar* single-schema golden ("lines per family") **is recalled
  for the cross-schema warm-line question and mis-leads it** (verified=true, answer
  still wrong) — because the *correct* cross-schema golden was dropped.

**Single-schema positive control:** on an easy single-schema question (avg capacity
by interface) the golden recalls (`recalled` 3/2/1) but baseline is already 3/3
correct, so no lift is *needed*. Net: in this fixture golden queries produced **no
measurable correctness lift** — the easy questions need no help and the hard
(cross-schema) ones are blocked by the access-scope bug. Golden recall is also a
**soft few-shot** (the agent reformulates rather than copies; `sql_matches_golden`
was False across runs), so even when recalled it nudges rather than overrides.

---

## Root causes & exact fix points (for the engineers)

| # | Finding | Where | Fix direction |
|---|---------|-------|---------------|
| **R1** | **Cross-schema golden queries never recalled.** Access set = request's single schema (`Loaded 5 dataset(s)`); cross-schema golden refs fail the fail-closed Stage-A filter. | `graph.py:608` `build_recall_access(context.datasets)`; `_load_context` loads one schema; `memory_store._pair_is_accessible` (fail-closed) | Build the recall accessible set from the **project's full `schema_names`** (or the materialized manifest's `tableReference` tables), not the request's primary schema. The manifest is already multi-schema; the access set must match it. Highest priority — it disables the headline feature exactly where it's valuable. |
| **R2** | **Views are not surfaced at query time.** Retrieval indexes models, not views; `wren_context` never contains a view. | retrieval/context node (`load_wren_context`); `matched_models` only | Index view name + `properties.description` as retrievable context items (the spec's "views become recall examples"), **or** auto-emit a golden query from an activated view so the recall path carries it. Without this, authored views are dead weight at query time. |
| **R3** | **Atomic activation sinks a whole view changeset on one bad view.** | bulk-status manifest validation | Surface per-view validation verdicts and allow partial activation (accept the valid views, reject the one bad view) — the review UI already supports per-item reject, but auto-activate is all-or-nothing. |
| **R4** | **Prose→semantic view authoring hallucinates columns.** | LLM authoring (`propose_mdl_from_document` / `write_mdl_file`) | Ground authoring in the **actual model columns** (pass the column list, as metrics do) and/or prefer the **name-substitution** path (E15) when source SQL exists. G2 already prevents the false-green; the gap is yield, not safety. |

---

## Recommended next steps for the Wren integration (priority order)

1. **Fix the recall access scope (R1).** One change unblocks the entire
   golden-query value proposition for multi-schema projects. Re-run E16 after the
   fix — expect cross-schema golden recall to fire and lift the hard questions.
2. **Surface views to the model (R2).** Either index view descriptions into
   retrieval, or bridge views → golden queries so the (post-R1) recall path carries
   them. This is what turns the working-but-invisible view feature into query-time
   accuracy.
3. **Defer native views (D6).** E15 shows physical→model name-substitution produces
   valid semantic views reliably; build native views only for unmodeled/external
   tables if a concrete need appears.
4. **Make view activation non-atomic / column-grounded (R3, R4)** to raise the
   authoring yield from the realistic prose path (0/3 today).
5. **Keep memory's fail-closed posture** (F2 is correct and unit-verified) — R1 is a
   *scope* fix, not a relaxation of the security filter.

### Methodology notes / threats to validity
- Single fixture, `gpt-4.1-mini`, low trial counts (3) for the live LLM-driven
  steps — directions are consistent and root-caused in code, but absolute rates are
  indicative, not precise.
- Views run with memory OFF (clean grounding ablation); Queries run with memory ON
  (required). The two regimes are not directly comparable on raw accuracy.
- Golden "lift" could not be cleanly demonstrated *because* the hard questions are
  cross-schema (blocked by R1) and the easy ones are already correct — itself the
  finding, not a measurement failure. After R1, the lift experiment becomes
  measurable.
- F1 cross-user sharing and the F2 RBAC drop are verified by the product unit suite
  (the correct layer for a security invariant); a live two-user RBAC probe was not
  run.
