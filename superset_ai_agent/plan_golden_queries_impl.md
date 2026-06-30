<!--
Implementation plan — shared NL→SQL memory (DB-scoped) + access-aware recall + project-scoped golden queries.
Derived from golden_queries_and_shared_memory_spec.md. Source-backed; file:line cited.
Sequential checklist for future agent sessions. Status: NOT STARTED. Decisions DP-1..DP-6 in the spec
plus DP-7..DP-9 below are pending unless marked DECIDED.
-->

# Implementation Plan — Shared Memory · Access-Aware Recall · Golden Queries

## How to use this checklist
- Work top-to-bottom. Each task lists **entrypoints** (file:line), **what to do**, **requirements**, and **blockers/deps**.
- **Phase 1 (F1+F2) ships as one unit** — F1 (sharing) is unsafe without F2 (access filter). Do not merge F1 alone.
- **Phase 2 (F3)** is additive; depends on F2's recall plumbing existing.
- Run `pre-commit run --all-files` before every push (CLAUDE.md mandate). Tests after each task.
- Decisions: DP-1…DP-6 live in `golden_queries_and_shared_memory_spec.md §7`; DP-5 is DECIDED (project file). DP-7…DP-9 are new, defined at the end here.

## Pre-flight (read before starting)
- **Migration head is `0014_schema_snapshot_by_schema`** → new migration = `0015`. Only **Phase 1** needs a migration; **F3 needs none** (reuses the MDL-file table).
- The memory store is **off by default** (`wren_memory_store="none"`, [config.py:291](config.py#L291)); these changes are inert until a deployment enables it. Good — lets us land plumbing safely.
- Trust model (CLAUDE.md / SECURITY.md): sharing boundary is the **database**; per-request table access is enforced by F2. Memory is *context, not permission*.

---

# PHASE 1 — F1 (DB-scoped memory) + F2 (access-aware recall)

### Task 1A — Schema-aware physical-table extractor  ⟶ *blocker for 1C, 1E*
- **Entrypoint:** [engine/base.py:110](semantic_layer/engine/base.py#L110) `extract_referenced_tables(sql, *, dialect)` — returns `table.name` **only** (drops schema).
- **Do:** add a sibling `extract_qualified_tables(sql, *, dialect) -> list[tuple[str|None, str]]` returning `(table.db or None, table.name)` from sqlglot `exp.Table` (`table.db` = schema, `table.name` = table). Keep the existing helper untouched (other callers depend on names-only).
- **Requirement:** parse failure ⇒ return `[]` (same best-effort contract). This `[]` is what makes F2 **fail closed** (1E Stage A).
- **Test:** multi-schema SQL → multiple `(schema, table)`; unqualified table → `(None, table)`; junk SQL → `[]`.

### Task 1B — Persist reference + DB columns on the memory row  ⟶ *migration 0015*
- **Entrypoints:** model [persistence/models.py:394](persistence/models.py#L394) `AiAgentNlSqlExample`; new migration `persistence/migrations/versions/0015_*.py` (`down_revision="0014_schema_snapshot_by_schema"`).
- **Do:** add nullable columns: `database_id` (Integer, index), `referenced_tables` (JSON), `referenced_schemas` (JSON). Keep `owner_id` (becomes authorship metadata, no longer a key — see 1D).
- **Requirement:** all nullable (legacy rows backfill to NULL). Use `superset.migrations.shared.utils` helpers per CLAUDE.md.
- **Blocker/decision (DP-7):** legacy rows have an **opaque `scope_hash`** and **no `database_id`** → cannot be deterministically re-keyed to a DB pool. Recommendation: **accept a memory reset** — legacy rows stay NULL and are excluded from recall by 1E's fail-closed filter (memory re-accumulates within normal usage; it's a cache, not source-of-truth). Optional partial backfill via `project_id`→`default_database_id` for rows that have a project_id (the text-to-SQL path sets none, so most won't). Document the reset in UPDATING.md.

### Task 1C — Compute + store references and database_id at store time  ⟶ *deps: 1A, 1B*
- **Entrypoints (3 store sites):** [graph.py:862](graph.py#L862), [conversation_graph.py:1480](conversation_graph.py#L1480), [pipeline.py:221](semantic_layer/pipeline.py#L221) — all call `memory.store_confirmed(...)`.
- **Do:** before each call, compute `referenced = extract_qualified_tables(native_sql, dialect)`; derive `referenced_schemas = {s for s,_ in referenced}`; pass `database_id`, `referenced_tables`, `referenced_schemas` through `store_confirmed`.
- **Entrypoint:** [memory_store.py:158](semantic_layer/memory_store.py#L158) `Memory.store_confirmed` protocol + 3 impls ([InMemoryMemory:201](semantic_layer/memory_store.py#L201), [SqlAlchemyMemory:280](semantic_layer/memory_store.py#L280), [LanceDbMemory:369](semantic_layer/memory_store.py#L369)) + `NlSqlPair` model ([:48](semantic_layer/memory_store.py#L48)) → add the three fields.
- **Requirement:** reuse `plan.referenced_tables` ([pipeline.py:88](semantic_layer/pipeline.py#L88)) where already computed, but note it's names-only; the **schema** must come from `extract_qualified_tables`. Best-effort — store failures already swallowed.
- **Test:** a stored pair persists `database_id` + `(schema,table)` refs; multi-schema native SQL → both schemas captured.

### Task 1D — Re-key memory on database identity (drop owner_id)  ⟶ *deps: 1B; pairs with 1E*
- **Entrypoint (new key fn):** [store.py:210](semantic_layer/store.py#L210) — add `memory_scope_key(scope) -> str` = hash of `database_id` only (**DP-1**; add `catalog_name` only if DP-1 says so). Leave `scope_hash`/`instruction_scope_hash` untouched (instructions stay personal — DP-6).
- **Entrypoints (drop owner_id from key, keep as metadata):**
  - Protocol [memory_store.py:152](semantic_layer/memory_store.py#L152): `recall_examples(question, *, scope_key, k)` and `store_confirmed(..., scope_key, database_id, ...)` — remove `owner_id` from signature *as a key* (may keep an optional `created_by` for authorship).
  - `InMemoryMemory._pairs` ([:191](semantic_layer/memory_store.py#L191)) keyed by `scope_key` (not the `(owner_id, scope_hash)` tuple).
  - `SqlAlchemyMemory` `load_candidates`/`recall_examples`/`store_confirmed`/`_evict_old` WHERE clauses ([:256](semantic_layer/memory_store.py#L256), [:298](semantic_layer/memory_store.py#L298), [:335](semantic_layer/memory_store.py#L335)) → filter by `database_id` (or the new key column), **drop `owner_id`**.
  - `LanceDbMemory._scope_key` ([:366](semantic_layer/memory_store.py#L366)) → `f"db:{database_id}"` (drop owner_id).
- **Entrypoints (5 call sites pass the new key, drop owner_id):** recall [graph.py:603](graph.py#L603), [conversation_graph.py:1070](conversation_graph.py#L1070); store [graph.py:862](graph.py#L862), [conversation_graph.py:1480](conversation_graph.py#L1480), [pipeline.py:221](semantic_layer/pipeline.py#L221). Also `create_memory` wiring at [app.py:360](app.py#L360) (likely unchanged).
- **Requirement:** dedup identity stays `(_normalize(question), _normalize(native_sql))` but now within the DB pool. Cross-database isolation preserved (key = database_id).
- **Test:** two distinct `owner_id`s sharing a `database_id` recall each other's pairs; two different `database_id`s do **not** share. **DP-2:** backfill/dedup-merge duplicates by normalized identity, keep most-recent.

### Task 1E — Access-aware recall: Stage A filter + B down-rank + C presentation  ⟶ *deps: 1A, 1C, 1D; THE safety gate*
- **Entrypoint:** [memory_store.py:274](semantic_layer/memory_store.py#L274) `recall_examples` (all impls) + the ranking helpers `_recall_rank`/`_rank`/`_semantic_rank` ([:141](semantic_layer/memory_store.py#L141)). Add a new stage wrapper, e.g. `_access_filter_and_rank(question, pairs, k, *, accessible, project_schemas, onboarded, embedder)`.
- **Do:**
  - **Stage A (RBAC hard-filter, FAIL CLOSED):** keep a pair only if `set(pair.referenced_tables) ⊆ accessible`. If `referenced_tables` is null/empty/`[]` (legacy or unparseable) → **drop** (never surface what we can't prove safe). `accessible` = `{(d.schema_name, d.table_name) for d in context.datasets}`.
  - **Stage B (relevance down-rank):** subtract a **big** penalty if any `referenced_schemas ⊄ project_schemas`; a **small** penalty if a referenced table is accessible + in an in-scope schema but ∉ `onboarded`. Apply to the similarity score (soft, not a cut).
  - **Stage C (presentation):** for a surviving pair whose tables ⊄ `onboarded`, inject `native_sql` only and **strip `semantic_sql`** (foreign model names dangle). Fully-onboarded pairs keep both.
- **Requirement:** Stage A is the *pre-filter ACL* pattern (spec §2). Fail-closed is **DP-4 = yes**. Penalties are tunable constants; document defaults.
- **Test:** pair referencing an inaccessible table → dropped; null-refs pair → dropped; out-of-project-schema pair → present but down-ranked; non-onboarded pair → `native_sql` only.

### Task 1F — Feed the accessible set + project context into recall  ⟶ *deps: 1E*
- **Entrypoints:** draft nodes [graph.py:598](graph.py#L598) `_draft_sql` and [conversation_graph.py:1068](conversation_graph.py#L1068) `_draft_response`.
- **Do:** build `accessible` from `state["context"].datasets` (the access-proven set — [access.py:129](semantic_layer/access.py#L129)); derive `project_schemas` from `wren_context`/project `schema_names`; derive `onboarded` `(schema,table)` from the compiled manifest's models (`_table_schema`/`_table_name`, [mdl_validator.py:1129](semantic_layer/mdl_validator.py#L1129)). Pass all three into `recall_examples`.
- **Requirement (DP-3):** v1 accessible set = the request scope's `context.datasets` (safe, already loaded). Note the permissive full-DB-role option as deferred.
- **Test:** integration — draft node drops an inaccessible recalled pair end-to-end.

### Task 1G — Phase-1 tests + lint
- Mirror [tests/.../test_memory_store.py](../tests/unit_tests/superset_ai_agent/test_memory_store.py). Cover: DB-pool sharing, cross-DB isolation, store-time refs, Stage A/B/C, fail-closed, eviction still bounded.
- `pre-commit run --all-files`; resolve mypy (memory_store is typed).

### Task 1H — Docs + config
- **UPDATING.md:** document (a) memory is now DB-shared (question text visible to all users with DB access — the accepted trade-off), (b) legacy memory reset (DP-7).
- Confirm no new config flag needed; `wren_memory_*` semantics unchanged except keying.

---

# PHASE 2 — F3 Project-scoped golden queries (`queries.json`)  ⟶ *deps: Phase 1 recall plumbing*

> Storage DP-5 = **DECIDED → `queries.json` as an MDL-file kind**, reusing `AiAgentSemanticMdlFile`/`MdlFileStore`. **No new table, no migration.** The manifest assembler already ignores the `queries` top-level key ([mdl_merge.py:44 MERGE_SECTIONS](semantic_layer/mdl_merge.py#L44), [mdl_compile.py:98](semantic_layer/mdl_compile.py#L98)), so it never reaches wren-core. The real touchpoint is **validation** (2A).

### Task 2A — `queries.json` file kind + kind-aware validation  ⟶ *BLOCKER B4*
- **Entrypoints:** activation gate [mdl_files.py:54](semantic_layer/mdl_files.py#L54) `_assert_activatable` (calls `validate_mdl(content)`); `MdlFileStore.validate`/`create`/`update` ([mdl_files.py:357/411/471](semantic_layer/mdl_files.py#L357)); kind enum [schemas.py:183](semantic_layer/schemas.py#L183) `MdlFileSourceType`.
- **Do:**
  - Add a **reserved-path kind discriminator** — constant `GOLDEN_QUERIES_PATH = "queries.json"` + `is_golden_queries_file(path)` helper. (Recommended over overloading `source_type`, which is provenance — **DP-8**.)
  - Branch validation: if `is_golden_queries_file(path)` → `validate_golden_queries(content, *, manifest=None)` instead of `validate_mdl`. On **create/draft**: structural (each entry has `name`, `question`, `semantic_sql`). On **activate**: also resolve every `semantic_sql` model name against the active manifest (+ optional read-only execute) — the validation-on-verify gate.
- **Requirement:** `validate_mdl` must **never** run on a queries file (it would reject it for having no models). Confirm `build_deploy_preview` ([copilot/service.py:333](semantic_layer/copilot/service.py#L333)) excludes the queries file from *manifest* validation while still showing its draft→active diff.
- **Test:** a `queries.json` draft activates without MDL errors; a queries entry referencing an unknown model fails activation; a model file still validates as before.

### Task 2B — Golden-query entry schema + serde  ⟶ *deps: 2A*
- **Entrypoint:** new `semantic_layer/golden_queries.py`.
- **Do:** pydantic `GoldenQuery{name, question, semantic_sql, verified_by: str|None, verified_at: int|None, use_as_onboarding: bool=False, usage_guidance: str|None}` and a file wrapper `GoldenQueriesFile{queries: list[GoldenQuery]}`; `parse(content)`/`dump(file)` helpers. Field names mirror Cortex VQR proto (spec §2).
- **Requirement:** SQL stored as **`semantic_sql` (model names)** only — no `native_sql` (manifest rewrites at execution; refs are manifest-derived in 2C).
- **Test:** round-trip parse/dump; reject entry missing `question`/`semantic_sql`.

### Task 2C — Golden-query recall (merge + manifest-derived refs + F2)  ⟶ *deps: 2B, 1E, 1F*
- **Entrypoints:** draft nodes [graph.py:598](graph.py#L598) / [conversation_graph.py:1068](conversation_graph.py#L1068) (where memory recall happens); model→table resolver [mdl_validator.py:1129](semantic_layer/mdl_validator.py#L1129).
- **Do:**
  - Load active `queries.json` for the project (via `MdlFileStore.list` filtered to the reserved path); parse entries.
  - Rank entries by similarity to the question (reuse the embedder/keyword path from [memory_store.py:141](semantic_layer/memory_store.py#L141), or instructions' `_recall` pattern — **DP-9**: in-process ranking for v1, like instructions; LanceDB cache later).
  - **Derive each entry's referenced physical tables** from `extract_qualified_tables(semantic_sql)` → model names → `_table_schema`/`_table_name` per the manifest → `(schema, table)`. Apply **F2 Stage A** to golden too (drop if a referenced table is inaccessible).
  - **Merge** golden (priority, decay-exempt — like `is_global` instructions [instructions.py:117](semantic_layer/instructions.py#L117)) with memory candidates; **dedup by normalized question** — golden supersedes its runtime twin **in the prompt only** (no deletion; spec invariant).
- **Requirement:** golden is the *higher-trust tier*; reserve/prioritize its slots within `k`.
- **Test:** golden entry recalled with priority; golden referencing inaccessible table dropped; golden supersedes a duplicate memory pair without deleting it; project-scoped (no cross-project leakage).

### Task 2D — Copilot `add_golden_query` tool (changeset-gated authoring)  ⟶ *deps: 2A/2B*
- **Entrypoints:** tool registry [copilot/tools.py](semantic_layer/copilot/tools.py); `ToolActionKind` [copilot/schemas.py:54](semantic_layer/copilot/schemas.py#L54); apply path [copilot/service.py:186](semantic_layer/copilot/service.py#L186) `apply_changeset_items`.
- **Do:** add `ToolActionKind` value `"curate"`; add `add_golden_query` tool that **merges a new entry into the existing `queries.json` content** and emits a `ChangesetItem{op: create|update, path: "queries.json", proposed_content}`. Reviewed in `ChangesetReviewPanel`, persisted as a **draft** by the existing apply path (no new persistence code).
- **Requirement:** activation stays a separate human action (drafts by default). Provenance via `apply_provenance_payload` ([copilot/service.py:273](semantic_layer/copilot/service.py#L273)).
- **Test:** tool emits a valid changeset item; apply lands a draft queries.json; review diff renders.

### Task 2E — "Promote to golden" (promote-from-runtime)  ⟶ *deps: 2A/2B; spec invariant = COPY not move*
- **Entrypoints:** new route mirroring the instruction create route [app.py:4180](app.py#L4180) → `POST /agent/semantic-layer/projects/{project_id}/golden-queries/promote`; authz via `authorize_semantic_project(..., permission="write")`.
- **Do:** accept `{question, semantic_sql|native_sql}` from a recalled pair; if only `native_sql` exists, translate to model-name form against the manifest (or store as-is with a flag); upsert into the project's `queries.json` **draft** entry. **Leave the source memory row untouched** (copy, never move — spec invariant).
- **Requirement:** idempotent on normalized question (re-promote refreshes, no dup).
- **Test:** promote creates a draft golden entry; the memory row still exists afterward; re-promote dedups.

### Task 2F — UI: promote button, Golden-queries tab, verified badge  ⟶ *deps: 2C/2E*
- **Entrypoints (frontend):** `RecalledExamples` [AgentStepDetail.tsx:403](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/AgentStepDetail.tsx#L403); instructions panel template `InstructionsPanel.tsx`; changeset review `ChangesetReviewPanel.tsx`; workspace tree [workspace.py:84](semantic_layer/copilot/workspace.py#L84) (the existing **virtual `queries.yml` node** — reconcile: point it at the real `queries.json` MDL file — **blocker B5**).
- **Do:** (a) "Promote to golden" button on recalled-example / answer cards → calls 2E; (b) a "Golden queries" tab in the Semantic Layer Editor mirroring `InstructionsPanel` (list/add/delete; edit = re-create); (c) "Verified" badge on an answer whose draft used a golden query (recall provenance is already in the explain trace — Cortex `verified_query_used` / Genie "trusted asset" pattern).
- **Requirement:** copy follows the Wren/Cortex framing ("verified", project-shared) — NOT the "personal" copy used for instructions.
- **Test:** RTL — button posts; tab CRUD; badge renders when a golden query was used.

### Task 2G — Phase-2 tests + lint
- Mirror [test_semantic_layer_mdl_files.py](../tests/unit_tests/superset_ai_agent/test_semantic_layer_mdl_files.py) (kind-aware validation, activation) and [test_copilot_service.py](../tests/unit_tests/superset_ai_agent/test_copilot_service.py) (changeset apply for queries.json). Confirm queries.json is **excluded from the compiled manifest** (assembly test). `pre-commit run --all-files`.

---

# Blockers & dependency graph
- **B1:** F1 must ship **with** F2 (sharing unsafe without the access filter). → 1D gated by 1E/1F in the same PR.
- **B2:** 1E/1F depend on the schema-aware extractor (1A) + persisted refs (1B,1C).
- **B3 (DP-7):** legacy memory rows can't be re-keyed (no `database_id`) → reset; fail-closed (1E) makes them inert. Accept + document.
- **B4:** F3 activation needs a kind-aware validation branch (2A) — `validate_mdl` must not run on `queries.json`. Highest-risk F3 task; do first in Phase 2.
- **B5:** reconcile the existing **virtual `queries.yml`** workspace node ([workspace.py:84](semantic_layer/copilot/workspace.py#L84)) with the new stored `queries.json` MDL file (2F).
- **Dep:** Phase 2 recall (2C) depends on Phase 1's `recall_examples` access plumbing (1E/1F) existing.

# Risks & mitigations (consolidated)
| Risk | Mitigation | Task |
|---|---|---|
| Sharing leaks question text across DB users | Accepted by trust model; F2 still hard-filters inaccessible-table pairs; document in UPDATING.md | 1E, 1H |
| Unparseable refs could leak | **Fail closed** — drop from recall | 1E (DP-4) |
| Legacy memory un-rekeyable | Accept reset; pool rebuilds; legacy rows inert | 1B/1D (DP-7) |
| `validate_mdl` rejects `queries.json` | Kind-aware validation branch | 2A (B4) |
| Poisoned golden query becomes authoritative | Review-gate (changeset accept) + validate-on-activate + `verified_by`/`verified_at` | 2A/2D |
| Golden staleness after schema change | Validate-on-activate; semantic_sql survives physical renames; coverage audit later | 2A/2C |
| Recall dilution (golden + memory compete for k) | Priority slots + dedup by normalized question (presentation-only) | 2C |

# Decision points
- **DP-1…DP-6:** see spec §7 (DP-5 DECIDED = project file; DP-6 = instructions stay personal/out of scope). DP-1..DP-4 carry recommendations; confirm before 1B/1D/1E.
- **DP-7 (new):** legacy memory — **accept reset** (recommended) vs partial backfill via `project_id`. Gates 1B.
- **DP-8 (new):** golden-query kind discriminator — **reserved path `queries.json`** (recommended; keeps `source_type` as provenance) vs a new `source_type`/`content_type`. Gates 2A.
- **DP-9 (new):** golden-query recall ranking — **in-process embed/keyword for v1** (recommended; mirrors instructions) vs reuse the LanceDB `sql_pairs` cache now. Gates 2C.
