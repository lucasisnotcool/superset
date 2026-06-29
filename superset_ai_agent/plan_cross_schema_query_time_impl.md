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

# Impl Plan & Checklist: Cross-Schema Query-Time Context (Fix C, expanded)

**Status:** proposed (no code changed) · **Date:** 2026-06-29
**Scope:** the **query-time** AI SQL agent path — schema inference, project
resolution, dataset/metadata context fetch, matched-models ranking, and SQL
execution — so a project that spans multiple schemas is grounded on **all** its
schemas, not just one.
Out of scope: onboarding/modeling-time paths (already cross-schema via
`_onboarding_context`/`_schema_index_for_project`), the Copilot editor, and the
frontend (inference is backend-only by prior decision).

> Anchors verified against the working tree on 2026-06-29. Re-grep before editing.
> Written to be resumed as a checklist; each task lists blockers/deps + exit
> criteria. Builds on `plan_cross_schema_context_ranking_impl.md` (Fix A/B, shipped).

---

## 0. CRITICAL re-anchor finding — the foundation is MISSING here (multi-machine drift)

The prior session's memory ([[ai-sql-schema-inference]]) says schema-inference
**SHIPPED**: a `resolve_effective_schema(...)` in `wren_runtime.py` returning
`(schema_name, schema_names)`, wired via `_with_inferred_schema` (query graph) and
`_with_inferred_scope` (conversation graph), with the materializer relaxed to
resolve schema from `project_id`.

**None of that implementation exists in this clone.** Verified:
- `grep -rn resolve_effective_schema superset_ai_agent/` → **0 hits** (only the
  test references it).
- `materialize_request_semantic_project` still hard-exits on no schema:
  `if ... schema_name is None: return None` ([wren_runtime.py:58](semantic_layer/wren_runtime.py#L58)).
- Its tests exist but **fail at collection** (ImportError):
  [test_wren_runtime_project_selection.py:38](../tests/unit_tests/superset_ai_agent/test_wren_runtime_project_selection.py#L38)
  imports `resolve_effective_schema`; `pytest` → "1 error during collection".

This is the Mac/Windows drift the memory flags ([[git-multi-machine-remotes]]):
shipped on another clone, absent here. **Phase 0 restores it** — and the orphaned
test file is the exact spec (it asserts the function returns the **full schema
set**, project-wins, DB-guarded, fall-through). So the foundation *already
intends* multi-schema; we restore it and then thread the full set through the
consumers that still use the scalar.

---

## 1. Current single-schema assumptions (ranked, source-anchored)

| # | Location | Assumption | Status |
|---|---|---|---|
| **A** | `wren_runtime.py:58` materializer `schema_name is None → return None`; `resolve_effective_schema` absent | No project grounds without a scalar schema; full set never inferred | **Open (foundation missing)** |
| **B** | `schemas.py` `AgentQueryRequest` has `schema_name` only (no `schema_names`) | One-shot query path can't carry multi-schema intent | **Open** |
| **C** | `conversation_graph.py:805` `_load_context` builds `AgentQueryRequest(schema_name=request.scope.schema_name)` | Drops `scope.schema_names` (the field exists!) → scalar only | **Open** |
| **D** | `context/superset_metadata.py:43-118` `get_context`/`get_full_schema` candidate scan uses single `request.schema_name` | Datasets fetched for one schema; secondary-schema tables never in context. (The `dataset_ids` branch IS cross-schema-correct.) | **Open (the "Fix C" root)** |
| **E** | `semantic_layer/retrieval.py` `retrieve_schema_context` ranks the pre-filtered single-schema candidates | Secondary-schema tables can't be ranked in | **Open (compounds D)** |
| **F** | `integrations/wren/client.py:319-356` `_matched_models` boosts models whose table is in `superset_context.datasets` (single schema) | Secondary-schema models lose the +3 "browsing" boost (Fix B parity boost still helps if the question names the table) | **Partially mitigated** |
| **G** | `graph.py:803` / client `execute_sql(schema_name=...)` sets the DB default schema/search_path | Unqualified secondary-schema tables fail at execution unless SQL is schema-qualified | **Open (execution)** |
| **H** | `semantic_layer/store.py` `scope_hash` keys on scalar `schema_name`; adds `schema_names` only when `effective_schema_names` > 1 | Memory/instruction recall is single-schema unless the multi-set reaches the scope | **Partially mitigated (depends on C)** |

**Already-correct foundations to reuse (do not rebuild):**
- `ConversationScope.schema_names` + `effective_schema_names`
  ([conversations/schemas.py](conversations/schemas.py)) — the scope is already
  multi-schema-capable; consumers just drop it.
- The **union pattern**: `_schema_index_for_project`
  ([app.py:1779-1792](app.py#L1779-L1792)) and `_onboarding_context`
  ([app.py:3300-3303](app.py#L3300-L3303)) already loop `project.schema_names` and
  union datasets — the **template** for the query-time fix.
- The `dataset_ids` path bypasses the schema filter in every adapter
  (local/rest/mcp) — cross-schema-correct by id.

---

## 2. Requirements (testable)

- **R1 (foundation restored).** `resolve_effective_schema` exists and satisfies its
  test ([test_wren_runtime_project_selection.py:179-258](../tests/unit_tests/superset_ai_agent/test_wren_runtime_project_selection.py#L179-L258)):
  returns `(primary, full_set)`, project-wins over tab schema, full set for
  multi-schema, DB-guarded, falls through to the passed schema.
- **R2 (full set carried).** When a multi-schema project grounds a request, the
  agent's dataset/metadata context contains datasets from **every** member schema
  the user can access — not just the primary.
- **R3 (no scalar regression).** Single-schema projects and schema-only (no
  project) requests behave **identically** to today.
- **R4 (ranking parity).** A model whose physical table is in a secondary schema
  can earn the same relevance treatment as a primary-schema model (it is now in
  `superset_context.datasets`, so the +3 boost applies).
- **R5 (execution correctness).** Generated SQL resolves tables across schemas —
  via schema-qualified references — independent of the single `schema_name`
  passed to `execute_sql`.
- **R6 (authz preserved, R1-from-multi-schema-spec).** Context is unioned only
  over schemas the user can access; inference selects *context*, never *access*
  (each per-schema context-load stays Superset-gated).
- **R7 (bounded).** The union scan is bounded so N schemas × per-schema scan can't
  blow the candidate count, the N+1 dataset fetch, or the prompt token budget.

---

## 3. Decision points (with recommendations)

- **DP1 — Carry multi-schema into the one-shot `AgentQueryRequest`.** (a) add
  `schema_names: list[str] | None` (parity with `ConversationScope`) [**recommended**];
  (b) infer-only inside the graph, never on the request. *Recommend (a)* — the
  conversation scope already has it; mirroring keeps the two graphs symmetric and
  lets `_with_inferred_schema` write the full set onto the request.
- **DP2 — How `get_context` becomes multi-schema.** (a) **union-scan** every
  member schema's candidates then rank across the union (mirrors
  `_schema_index_for_project`) [**recommended**]; (b) populate `dataset_ids` from
  project membership so the existing id-path (already cross-schema) is used.
  *Recommend (a)* — it preserves question-relevance ranking across the full set;
  (b) skips ranking and needs a separate membership fetch anyway. Keep (b)'s
  insight that the id-path is already correct as the fallback.
- **DP3 — Execution / schema qualification.** (a) rely on **schema-qualified SQL**
  (the MDL context already carries each model's `tableReference.schema`; the
  semantic engine rewrites model-SQL to native schema-qualified SQL) +
  instruct qualification in the prompt for the passthrough/LLM-direct path
  [**recommended**]; (b) change `execute_sql` to not set a default schema.
  *Recommend (a)* — qualified SQL is the only correct cross-schema form; don't
  depend on search_path. Verify the SQL prompt exposes table schemas (it does via
  context_items/tableReference once R2 lands).
- **DP4 — Union scan budget (R7).** Reuse `wren_schema_table_scan_limit` **per
  schema** but add a **total** cap across schemas (new
  `wren_schema_total_candidate_limit`, default = current single-schema candidate
  limit), ranked across the union, logging truncation (no silent cap). *Recommend*
  this over an unbounded union.
- **DP5 — Where inference writes the set.** Write `(schema_name, schema_names)`
  onto the request/scope at the graph chokepoints (`_with_inferred_schema` /
  `_with_inferred_scope`) so **all** downstream consumers (materialize, context,
  scope_hash, execution) see the full set from one place. *Recommended* (matches
  the shipped design the test encodes).

---

## 4. Entrypoints & touchpoints

| File : symbol (verified) | Change |
|---|---|
| `semantic_layer/wren_runtime.py :: resolve_effective_schema` (**restore — absent**) | New: `(schema_name, schema_names)` per the orphaned test; owner-filtered `store.get/list`, DB-guarded, project-wins, fall-through. |
| `semantic_layer/wren_runtime.py :: materialize_request_semantic_project` (35; guard 58) | Relax: when `schema_name` is None but `project_id` resolves, derive schema from the project; resolve across the member set, not just the scalar. |
| `schemas.py :: AgentQueryRequest` | Add `schema_names: list[str] | None` (DP1) + an `effective_schema_names` helper mirroring `ConversationScope`. |
| `graph.py :: TextToSqlGraph.run` (add `_with_inferred_schema`), `_load_context` (435), `_load_wren_context` (404/553) | Infer + write the full set onto the request; pass `schema_names` to the context provider; soften the `require_schema_scope` gate to "project or schema". |
| `conversation_graph.py :: _invoke_graph` (add `_with_inferred_scope`), `_load_context` (799-810), `_resolve_semantic_grounding` (858-866), `_load_wren_context` (882) | Carry `scope.effective_schema_names` into the `AgentQueryRequest`; pass the full set to materialize; soften the gate. |
| `context/superset_metadata.py :: get_context` (43-81), `get_full_schema` (83-118) | Union-scan `request.effective_schema_names` (DP2); rank across the union; bound by DP4. Single-schema path unchanged. |
| `semantic_layer/retrieval.py :: retrieve_schema_context` | Rank across the unioned candidate set (no change needed if the caller passes a unioned `context.datasets`). |
| `integrations/wren/client.py :: _matched_models` (319-356) | No change required once R2 lands (dataset context now spans schemas → boost applies); add a test. |
| `semantic_layer/store.py :: scope_hash / instruction_scope_hash` | Verify the full set keys the hash when present (already adds `schema_names` when >1); add a multi-schema test. |
| `config.py` | Add `wren_schema_total_candidate_limit` (DP4). |
| `tests/.../` | Un-orphan `test_wren_runtime_project_selection.py`; add union-context + cross-schema-ranking + execution-qualification tests. |

---

## 5. Sequential checklist

> Phases in order. Each ends green per `CLAUDE.md` (`pytest
> tests/unit_tests/superset_ai_agent/`, ruff, ruff-format, mypy-no-new-errors).
> **Blocker** = must precede; **Dep** = relies on.

### Phase 0 — Restore the inference foundation (unblocks everything)
- [ ] **0.1** Implement `resolve_effective_schema(...)` in `wren_runtime.py` to pass
      the existing [test_wren_runtime_project_selection.py](../tests/unit_tests/superset_ai_agent/test_wren_runtime_project_selection.py)
      (the contract): owner+DB-guarded `store.get(project_id)` (or `.list` filter),
      returns `(primary, full_set)`; project-wins; fall-through to passed schema.
      *Blocker for everything. Dep: none.*
- [ ] **0.2** Relax `materialize_request_semantic_project` (58): when
      `schema_name is None` but `project_id` is given, resolve via
      `resolve_effective_schema` before the `store.list` filter; keep R6 (access
      re-checked). Don't break the existing schema-present path.
- [ ] **0.3** Confirm the orphaned test now collects + passes; full suite green
      (this also clears the current collection error). *Exit: foundation restored.*

### Phase 1 — Carry the full schema set onto the request (DP1, DP5)
- [ ] **1.1** Add `schema_names` + `effective_schema_names` to `AgentQueryRequest`
      ([schemas.py](schemas.py)), mirroring `ConversationScope`.
- [ ] **1.2** Query graph: add `_with_inferred_schema` in `TextToSqlGraph.run` that
      calls `resolve_effective_schema` and writes `(schema_name, schema_names)` onto
      the request before the graph nodes run. *Dep: 0.1, 1.1.*
- [ ] **1.3** Conversation graph: add `_with_inferred_scope` in `_invoke_graph`
      (covers all entrypoints incl. streaming; falls back to the conversation pin);
      `_load_context` builds the `AgentQueryRequest` with
      `schema_names=scope.effective_schema_names`. *Dep: 0.1, 1.1.*
- [ ] **1.4** Soften the `wren_require_schema_scope` gates
      ([graph.py:404](graph.py#L404), [conversation_graph.py:882](conversation_graph.py#L882))
      to "project or schema". *Dep: 1.2/1.3.*
- [ ] **1.5** Tests: `test_graph_infers_schema_from_pinned_project`,
      `test_conversation_infers_schema_from_pinned_project` (multi-schema variants).
      *Exit: a pinned multi-schema project with no tab schema grounds with the full
      set on both paths.*

### Phase 2 — Multi-schema dataset/metadata context (DP2, DP4 — the core)
- [ ] **2.1** Add `wren_schema_total_candidate_limit` to `config.py` (DP4).
- [ ] **2.2** `get_context`/`get_full_schema`: when `effective_schema_names` has >1
      entry and no `dataset_ids`, **union-scan** each member schema's candidates
      (reuse the `_schema_index_for_project` loop pattern), dedup by dataset id,
      then rank across the union bounded by 2.1 (log truncation). Single-schema and
      `dataset_ids` paths unchanged (R3). *Dep: 1.x (request carries the set).*
- [ ] **2.3** Confirm `retrieve_schema_context` ranks the unioned candidates (it
      ranks `context.datasets`; once 2.2 unions them, no change — verify with a
      test). *Dep: 2.2.*
- [ ] **2.4** Tests: a 2-schema project, question naming a secondary-schema table →
      that table appears in `context.datasets` and is ranked in; single-schema
      no-op; budget-cap honored. *Exit: R2 + R4 + R7.*

### Phase 3 — Ranking + memory parity (verify, mostly free after Phase 2)
- [ ] **3.1** Test `_matched_models`: a secondary-schema model now earns the +3
      "browsing" boost because its table is in the unioned dataset context (R4).
- [ ] **3.2** Verify/lock `scope_hash` keys on the full set when present; test two
      requests differing only by member-schema set hash differently (H).

### Phase 4 — Execution / schema qualification (DP3, R5)
- [ ] **4.1** Confirm the semantic-engine (wren_core) path rewrites model-SQL to
      native **schema-qualified** SQL using each model's `tableReference.schema`
      (read `wren_materializer`/engine; add a cross-schema rewrite test).
- [ ] **4.2** For the passthrough/LLM-direct SQL path, ensure the SQL prompt
      surfaces each candidate table's schema (it does once context spans schemas)
      and instructs schema-qualified references; add an assertion/test that a
      cross-schema draft qualifies both tables. *Dep: Phase 2.*
- [ ] **4.3** Decide (DP3) whether `execute_sql`'s single `schema_name` is left as
      the default-schema hint (fine for qualified SQL) — document; no search_path
      reliance.

### Phase 5 — Verify & document
- [ ] **5.1** Full `pytest tests/unit_tests/superset_ai_agent/` green; ruff +
      ruff-format clean; mypy no new errors.
- [ ] **5.2** (If a multi-schema eval fixture exists) run a cross-schema join
      question end-to-end; confirm qualified SQL + correct execution.
- [ ] **5.3** Update this doc → IMPLEMENTED + as-built notes; update
      [[cross-schema-context-ranking]] / [[ai-sql-schema-inference]] memories.

---

## 6. Risks & mitigations

| ID | Risk | Mitigation |
|---|---|---|
| **R-DRIFT** | The foundation "shipped" on another clone; restoring here may collide on merge (multi-machine). | Restore to the **existing test's** contract exactly (it is the spec); coordinate via `origin/master` per [[git-multi-machine-remotes]]; if the other clone's version lands, prefer it and rebase this plan's later phases on top. |
| **R-PERF** | Union scan = N schemas × (dataset list + per-dataset N+1). | DP4 total cap; reuse the shipped dataset-endpoint ETag/eager-load perf work ([[dataset-endpoint-perf]]); only union when `effective_schema_names` > 1. |
| **R-AUTHZ** | Unioning context across schemas could surface a schema the user can't access. | R6: union only over schemas access is proven for; each per-schema context-load stays Superset-gated; inference selects context, not access. Add a cross-DB/forbidden-schema isolation test. |
| **R-BLOAT** | More candidates → larger prompt / token budget overflow. | Rank across the union, keep `wren_schema_table_candidate_limit` + token budget; the Fix-A `cap_context_items` still bounds final items. |
| **R-REGRESS** | Single-schema / no-project flows change. | R3 tests: scalar path byte-identical; union only triggers on a multi-member set. |
| **R-EXEC** | Qualified-SQL assumption wrong for some dialect. | Phase 4 engine test on a real cross-schema rewrite; the passthrough prompt instructs qualification; `execute_sql` schema stays a hint, not a hard search_path. |

---

## 7. Why this matches existing patterns

- The **union-over-`schema_names`** loop is already the codebase's modeling-time
  pattern (`_onboarding_context`, `_schema_index_for_project`) — Phase 2 applies the
  same shape at query time, not a new mechanism.
- **Inference selects context, not access** — the prior decision ([[ai-sql-schema-inference]])
  and the multi-schema spec's R1 invariant; per-schema Superset gating is retained.
- **Degrade-closed**: every step no-ops to the scalar path when there is no
  multi-schema project; bounded with logged truncation (house "no silent caps").

---

## 8. Quick status board

- [ ] Phase 0 — restore `resolve_effective_schema` + relax materializer (un-break the orphaned test)
- [ ] Phase 1 — carry full set onto request (both graphs)
- [ ] Phase 2 — union-scan multi-schema dataset context (the core)
- [ ] Phase 3 — ranking + memory parity (verify)
- [ ] Phase 4 — execution / schema qualification
- [ ] Phase 5 — verify + document

---

## 11. As-built status & residual notes (2026-06-30)

**IMPLEMENTED — all phases.**

- **P0** `resolve_effective_schema` added (`wren_runtime.py`, returns the FULL
  `(schema_name, schema_names)`, project-wins, DB-guarded); materializer relaxed to
  infer schema from `project_id` when `schema_name` is None. Orphaned test now
  collects + passes (12).
- **P1** `AgentQueryRequest.schema_names` + `effective_schema_names`;
  `normalize_schema_names` moved to base `schemas.py` (single source of truth,
  cycle-free); `_with_inferred_schema` (query graph `run`) + `_inferred_scope`
  (conversation graph `_load_context`, propagated on `state['request']`); gates
  softened to "project or schema". Parallel-written inference tests now pass.
- **P2** `get_context._candidate_datasets` unions every member schema (mirrors
  `_schema_index_for_project`), bounded by new `wren_schema_total_candidate_limit`
  (default 100), ranked across the union. Single-schema + `dataset_ids` paths
  unchanged.
- **P3** `_matched_models` +3 dataset boost now reaches secondary-schema models in
  the unioned context (verified by test); `scope_hash` already keyed on the full
  set and `_request_scope` now feeds it.
- **P4** text_to_sql prompt instructs schema-qualification when datasets span
  schemas; the wren_core path qualifies via the engine rewrite (`tableReference.schema`).

**Verification:** my directly-affected tests 65/65 green; full ai_agent suite
**1066 passed, 11 skipped, 1 failed**; ruff + ruff-format clean; my edits add no
new mypy errors.

**Residual / parallel-WIP interactions (NOT correctness regressions in this work):**
- `test_multi_schema_schema_index::test_bulk_activate_fetches_live_schema_once...`
  **fails on a fetch-COUNT assertion**. Root cause: this work makes the create-time
  and activate-time schema-index **cache keys consistent**, so activation correctly
  **reuses** the index `_create_model` cached <1s earlier (verified: create `SET`,
  activate `GET HIT` of a valid index; the TTL cache is explicitly designed for
  this). The in-flight test (parallel WIP, alongside an uncommitted `app.py`
  caching change) still asserts activation does one *new* fetch. The reused index
  is valid, so this is a perf-count drift to reconcile by the cache's author, not a
  regression. Left unmodified to avoid stepping on parallel work.
- `conversation_graph.py:1403` mypy `union-attr` is in parallel-WIP artifact code
  (outside this change's edit regions); not introduced here.

**UI-expectation gaps (backend scope; flagged):**
- **G-1 Project-wins is invisible.** When a pinned multi-schema project overrides
  the tab schema, nothing in the UI signals "grounded on project schemas a, b" vs
  the tab's schema. A scope chip / trace line would make the inference legible.
- **G-2 No multi-schema affordance in the picker.** The frontend still sets
  `schema_name` from the SQL Lab tab; it has no UI to send `schema_names`. The
  backend now infers the full set from the project, but a user without a pinned
  project still gets single-schema. Frontend could surface the project's schema set.
- **G-3 Union truncation is silent to the user.** `wren_schema_total_candidate_limit`
  truncation is logged server-side only; a very wide multi-schema project could
  drop candidate tables with no UI hint.
