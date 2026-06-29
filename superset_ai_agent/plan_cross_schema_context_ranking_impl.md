<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Impl Plan & Checklist: Cross-Schema Context — Join-Closure + Unbiased Ranking

**Status:** proposed (no code changed) · **Date:** 2026-06-29
**Scope:** the AI SQL agent's MDL context **retrieval → selection → prompt** path.
Out of scope: onboarding seed building, the picker, the graph view, project
resolution (`wren_runtime`), and the request `schema_name` contract (that is the
heavier "Fix C" root change — see §8).

> Anchors verified against the working tree on 2026-06-29. Symbols are stable;
> re-grep line numbers before editing. This doc is written to be resumed as a
> checklist across sessions — each task lists its blockers/deps and exit criteria.

---

## 1. Problem (verified mechanism)

For a cross-schema project, a relevant join partner table can be **silently
dropped from the SQL prompt**, so the model is told a join exists but isn't given
the partner's columns/table → it cannot write the cross-schema join.

Two compounding causes, both confirmed in source:

- **Bias (Fix B).** `_matched_models` adds `+3` to a model whose name is in the
  *current request schema's* loaded datasets ([client.py:327-339](integrations/wren/client.py#L327-L339));
  `superset_context.datasets` is fetched for a **single** `schema_name`
  ([context/superset_metadata.py `get_context`](context/superset_metadata.py)).
  Cross-schema members (often renamed `{schema}_{table}`,
  [client.py:466-473](integrations/wren/client.py#L466-L473)) never earn the boost
  → rank lower. `matched_models` is then capped at `wren_context_limit` (default
  **8**, [config.py:120](config.py#L120)).
- **Truncation severs joins (Fix A).** `select_relevant_models` keeps the top
  `wren_table_selection_limit` (default **5**, [config.py:254](config.py#L254))
  model names by rank, prunes the rest's detail, but **always preserves
  relationship chunks** ([runtime.py:73-93](semantic_layer/runtime.py#L73-L93),
  `_filter_to_models` [runtime.py:61-70](semantic_layer/runtime.py#L61-L70)). So a
  low-ranked join partner's columns are pruned while the "A joins B" chunk
  survives — a **dangling relationship**.

**Practical trigger:** projects with **> `wren_table_selection_limit` (5)** models
where a join partner is not directly named in the question. ≤5-model projects are
unaffected (no truncation) — so all changes here must be **no-ops for the
single-schema / small-project case**.

**Mitigation already present (do not regress):** `build_unified_context` puts the
schema-agnostic retriever output *first* ([runtime.py:130](semantic_layer/runtime.py#L130)),
so question-relevant cross-schema models already survive. The gap is specifically
the **join partner that is relevant only via the relationship**, not via the
question text.

---

## 2. Requirements (testable)

- **R1 (join-closure).** If a relationship connects a selected model to a
  non-selected model, the non-selected partner's model+column items MUST be
  included in `context_items`, along with the relationship item. One hop minimum.
- **R2 (closure source completeness).** Closure MUST work even when the
  relationship *chunk* was not in the retriever's top-k — i.e. it consults the
  project's full relationship set, not only retrieved relationship items.
- **R3 (no single-schema regression).** For a project with ≤ `wren_table_selection_limit`
  models, or with no cross-set relationships, output is byte-identical to today.
- **R4 (bounded).** Closure additions are capped so a pathological star schema
  cannot blow the prompt past `wren_max_context_items` (default 40,
  [config.py:248](config.py#L248)).
- **R5 (unbiased ranking).** `_matched_models` MUST give a cross-schema model a
  competitive score when the question matches its table/semantics, independent of
  the request's single `schema_name`.
- **R6 (both selection paths).** Closure MUST apply to both the heuristic
  (`select_relevant_models`) and the opt-in LLM selector
  (`wren_llm_table_selection`, [config.py:259](config.py#L259)) paths — they share
  `build_unified_context` ([runtime.py:130-139](semantic_layer/runtime.py#L130-L139)).

---

## 3. Decision points (with recommendations)

- **DP1 — Closure source: retrieved relationship items vs. full-manifest
  relationships.** *(a)* use only relationship chunks that were retrieved (cheap,
  no new plumbing) — but misses joins whose chunk ranked out of top-k (violates
  R2); *(b)* pass the project's full relationship list (from the compiled
  manifest, already built in `_load_wren_context`) into closure.
  **Recommend (b).** It is the schema-linking standard (Wren's own table-selection
  pulls in related tables) and is the only option satisfying R2. Cost: thread a
  `relationships` adjacency into the selection step.
- **DP2 — Closure depth: one hop vs. transitive.** **Recommend one hop** (direct
  partners of selected models), bounded by DP4. Multi-hop (A→B→C) is rare for a
  single question and risks context bloat; revisit only if eval shows missed
  3-table chains.
- **DP3 — Do closure-added partners count against `wren_table_selection_limit`?**
  **Recommend NO** — exempt them, else closure just re-truncates and achieves
  nothing. Add a separate cap (DP4).
- **DP4 — Closure budget.** **Recommend** a new config
  `wren_join_closure_limit: int = 5` (max partner models pulled in per question),
  and keep the final `cap_context_items(max_context_items=40)` as the absolute
  backstop. Log when closure is truncated (no silent cap — house pattern).
- **DP5 — Fix B shape.** *(b1)* fold the model's `tableReference.table` +
  synonyms into the token score (schema-neutral, tiny, no contract change);
  *(b2)* widen `dataset_names` to the project's full schema union (needs a
  multi-schema dataset fetch). **Recommend (b1) now**; treat (b2) as part of the
  root Fix C (§8). (b1) alone satisfies R5.

---

## 4. Entrypoints & touchpoints

| File : symbol (verified line) | Role | Change |
|---|---|---|
| `semantic_layer/schema_retriever.py :: SchemaItem` (50-60) | retrievable chunk | **Add** `related_models: list[str] = []` (blocker for R1/R2). |
| `…schema_retriever.py :: manifest_to_schema_items` (117-181) | manifest → chunks | Populate `related_models` from `rel.get("models")` on relationship items (163-181). |
| `…schema_retriever.py :: retrieve_mdl_context` (676-735) | chunks → dicts | Add `"related_models": item.related_models` to the emitted dict (726-735). |
| `semantic_layer/runtime.py :: build_unified_context` (102-139) | merge→select→cap | Insert a **join-closure** step after `_select_models`, before `cap_context_items`; accept a `relationships` arg (DP1b). |
| `…runtime.py :: select_relevant_models / _filter_to_models / canonical_model_name` (20-93) | selection | Closure reuses `canonical_model_name`; add a `relationship_partners(items, relationships)` helper. |
| `graph.py :: _load_wren_context` (402-517) | wires retrieval+select | Thread the compiled manifest's `relationships` into `build_unified_context` (compiled at 470-477 / materialization). |
| `integrations/wren/client.py :: _matched_models` (319-348) | matched-models hint | Fix B (b1): add `tableReference`/synonym tokens to the scored `text`; keep the dataset boost as a bonus, not the only path. |
| `config.py` (≈248-259) | limits | Add `wren_join_closure_limit` (DP4); document interaction with `wren_table_selection_limit`. |
| `tests/unit_tests/superset_ai_agent/` | — | New `test_join_closure.py`; extend matched-models + cross-schema tests. |

Reference (read-only) — confirm shapes, don't edit:
`mdl_compile.compile_manifest` (all relationships, no schema filter),
`semantic_layer/schema_retriever.py` ranker `k=wren_context_limit` (719).

---

## 5. Sequential checklist

> Do phases in order. Each task: **[ ]** unchecked. "Blocker" = must be done
> first; "Dep" = relies on. Each phase ends green per `CLAUDE.md`
> (`pytest tests/unit_tests/superset_ai_agent/`, ruff, ruff-format; mypy no new
> errors — baseline noise in `persistence/models.py` / `wren_core_validator.py`).

### Phase 0 — Baseline & repro (no code)
- [ ] **0.1** Add a failing test `test_join_closure.py::test_cross_schema_partner_pruned_today`:
      build a manifest with 6+ models across 2 schemas + a relationship from a
      question-relevant model to a non-relevant partner; run the
      `build_unified_context` path with `wren_table_selection_limit=5`; assert the
      partner's columns are **absent** (documents current behavior).
      *Blocker for: proving the fix.* *Dep: none.*
- [ ] **0.2** Record baseline mypy error count on touched files (so "no new
      errors" is checkable later).

### Phase 1 — Structured relationship endpoints (foundation, Fix A blocker)
- [ ] **1.1** Add `related_models: list[str] = []` to `SchemaItem`
      ([schema_retriever.py:50-60](semantic_layer/schema_retriever.py#L50-L60)).
- [ ] **1.2** In `manifest_to_schema_items` relationship branch (163-181), set
      `related_models=[str(m) for m in (rel.get("models") or []) if m]`.
- [ ] **1.3** Emit `"related_models"` in the `retrieve_mdl_context` dict
      ([:726-735](semantic_layer/schema_retriever.py#L726-L735)).
- [ ] **1.4** Verify the retriever index tolerates the new field (it derives
      `SchemaItem`s at index time; vectors key on MDL checksum, not item shape).
      If any persisted index stores item schemas, bump its version.
      *Risk: stale index — see R-IDX.* *Dep: 1.1.*
- [ ] **1.5** Unit test: a relationship item round-trips `related_models` through
      `manifest_to_schema_items` and `retrieve_mdl_context`.
      *Exit: relationship dicts carry structured endpoints.*

### Phase 2 — Join-closure in selection (Fix A core, satisfies R1/R2/R6)
- [ ] **2.1** Add `wren_join_closure_limit: int = 5` to `config.py` + env wiring +
      docstring noting it is exempt from `wren_table_selection_limit` (DP3/DP4).
- [ ] **2.2** In `runtime.py`, add `relationship_partners(selected_names, relationships)`
      → the set of partner model names directly joined to any selected model (one
      hop, DP2), capped at `wren_join_closure_limit`, logging truncation (no silent
      cap). *Dep: DP1 resolved → relationships passed in.*
- [ ] **2.3** Change `build_unified_context` signature to accept
      `relationships: list[dict] | None = None`; after `_select_models`, compute the
      closure partner set and **union it into the allowed models** before the final
      filter, then `cap_context_items` as the backstop (R4).
      *Dep: 2.2.* *Blocker for 2.4.*
- [ ] **2.4** Thread relationships in `graph._load_wren_context` (402-517): pass the
      compiled manifest's `relationships` (DP1b) into `build_unified_context`
      (482-488). Confirm they're available from the same compile the retriever used.
      *Dep: 2.3.*
- [ ] **2.5** Make 0.1's test pass (partner columns now present) + add:
      `test_single_schema_no_op` (≤5 models → identical output, R3),
      `test_closure_respects_budget` (R4),
      `test_llm_selector_path_also_closes` (R6).
      *Exit: cross-schema join partners survive selection; small projects unchanged.*

### Phase 3 — Unbias matched_models (Fix B / R5)
- [ ] **3.1** In `_matched_models` ([client.py:319-348](integrations/wren/client.py#L319-L348)),
      include the model's `tableReference.table` and `properties` synonyms/displayName
      in the scored `text` (DP5-b1) so a cross-schema model scores on its own
      terms; keep the `+3` dataset boost as an additive bonus (not the sole path).
- [ ] **3.2** Test: a cross-schema model (table named in the question, physical
      table in a non-request schema) gets a competitive (non-zero, top-ranked)
      score; same-schema behavior unchanged. *Exit: R5.*

### Phase 4 — Verify & document
- [ ] **4.1** Full `pytest tests/unit_tests/superset_ai_agent/` green; ruff +
      ruff-format clean on touched files; mypy no new errors vs 0.2 baseline.
- [ ] **4.2** (If available) run an eval scenario with a cross-schema join question
      and confirm the generated SQL qualifies both tables with their schemas.
      *Dep: a multi-schema eval fixture; note as blocker if none exists.*
- [ ] **4.3** Update this doc's status to IMPLEMENTED + as-built notes; record any
      residual gaps (esp. the Fix C root item, §8).

---

## 6. Risks & mitigations

| ID | Risk | Mitigation |
|---|---|---|
| **R-IDX** | Adding `related_models` changes `SchemaItem` shape; a persisted/embedded index could serve stale items without it. | Index derives items at index time and keys on the MDL **checksum**, not item schema; re-index happens on any MDL change. Verify the in-process `LRUIndexCache` and any embedding store ignore unknown/missing fields; bump an index version constant if one exists. |
| **R-BLOAT** | Closure pulls many partners on a star/hub schema → oversized prompt. | `wren_join_closure_limit` (DP4) + the existing `cap_context_items(max_context_items=40)` backstop; `log()` truncation. |
| **R-REGRESS** | Small/single-schema projects change behavior. | R3 test (`≤ limit` models → identical output); closure only fires when a relationship crosses the selected/unselected boundary. |
| **R-NAMESPACE** | Closure matches partner names against the wrong name space. | Relationships reference **model names**; closure compares against `canonical_model_name` (same space). Add an assertion/test on a renamed (`{schema}_{table}`) collision model. |
| **R-OVERBOOST** | Fix B over-weights generic table tokens. | Tokenize `table` + synonyms only (not `schema`); keep weights modest; the dataset `+3` stays the strongest signal for actively-browsed tables. |
| **R-RELRANK** | If DP1a were chosen, a needed relationship chunk could rank out of top-k and closure never fires. | DP1b (full-manifest relationships) removes this dependency — that is *why* (b) is recommended. |

---

## 7. Why this matches existing patterns

- **Schema linking / related-table inclusion** is the documented text-to-SQL
  anti-hallucination practice and Wren's own table-selection behavior (pull in
  related tables, not an arbitrary count cut) — `select_relevant_models`'
  docstring already cites "narrowing to a coherent set of tables rather than an
  arbitrary count cut" ([runtime.py:76-86](semantic_layer/runtime.py#L76-L86));
  closure completes that intent for joins.
- **Degrade-closed**: every step no-ops to current behavior on missing data
  (no relationships, no project) — consistent with the retriever/runtime modules.
- **No silent caps**: closure logs truncation — the house rule used by coverage
  and context capping.

---

## 8. Deferred root cause (Fix C — separate change)

`get_context` fetches datasets for the request's single `schema_name`
([context/superset_metadata.py](context/superset_metadata.py)); the request
contract carries `schema_name: str | None` (singular,
[schemas.py](schemas.py) `AgentQueryRequest`). Widening query-time context to the
project's **full schema set** (mirroring onboarding's union loop,
[app.py:3049-3061](app.py#L3049-L3061)) would remove the bias at its source and
strengthen `matched_models` (DP5-b2). It touches the request contract and
project-resolution, so it is **out of scope here** and should be its own spec.
Fixes A+B make cross-schema joins correct for the **pinned-project** flow today;
Fix C improves auto-resolution and ranking quality. Blocker for Fix C: decide
whether `AgentQueryRequest` gains `schema_names: list[str]` or resolution becomes
schema-agnostic by `project_id`.

---

## 9. Quick status board (update as you go)

- [ ] Phase 0 — baseline repro test
- [ ] Phase 1 — structured relationship endpoints
- [ ] Phase 2 — join-closure (the load-bearing fix)
- [ ] Phase 3 — unbiased matched_models
- [ ] Phase 4 — verify + document
- [ ] (deferred) Fix C — multi-schema query context
