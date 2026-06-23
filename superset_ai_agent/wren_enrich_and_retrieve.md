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

# Wren Enrichment & Retrieval — Parity Plan

This document covers the **two runtime pipelines** the agent uses against the
semantic layer:

- **Enrichment** — a business document (or schema introspection) becomes an
  activatable MDL manifest.
- **Retrieval** — a natural-language question becomes grounded context and SQL.

It complements [`wren_full.md`](wren_full.md) (the native-manifest *authoring/
storage* rebuild, W1–W6, `[DONE]`). That rebuild fixed the MDL **shape**; this
document targets the **pipeline behavior** — bringing enrichment and retrieval up
to parity with Wren AI's native pathway (wren-engine + Haystack-style indexing/
retrieval/generation pipelines over a persistent vector store).

**Part A** is the source-backed comparison (current vs. Wren, gap-first).
**Part B** is the implementation plan, **reordered into a dependency-ordered
checklist** so future passes can execute top-to-bottom.

Status legend: `[TODO]` not started · `[WIP]` in progress · `[DONE]`
source-backed and test-verified · `[BLOCKED]` waiting on a decision/dependency.

Source references are relative to `superset_ai_agent/` (frontend paths via
`../superset-frontend/`).

---

## 0. Grounding — Wren's native architecture (the target)

- **wren-engine (wren-core)** is the *source of truth*: it compiles MDL, resolves
  relationships / calculated fields / metrics, and does dialect-aware SQL
  planning. Validation and generation are grounded in the engine, not a
  reimplementation.
- **Wren AI Service** runs Haystack pipelines over a **persistent vector store**
  (Qdrant) with multiple indexed collections: `db_schema` (per-model/column
  chunks), `historical_question`, `sql_pairs` (few-shot NL→SQL), `instructions`,
  `table_description`. The store is **re-indexed on every MDL deploy**.
- **Enrichment analog** = the **semantics-description-generation** pipeline (plus
  relationship recommendation): structure is authoritative from the modeled/
  engine-compiled MDL; the LLM only generates descriptions/synonyms, grounded by
  retrieval, then re-indexed.
- **Retrieval** = embed-question → vector search → **LLM table/column selection
  (re-rank/prune)** → inject few-shot `sql_pairs` + `instructions` → generate →
  **engine-error-driven correction loop**.

**Cross-cutting parity foundations** (everything below depends on these):

1. **Engine as the authority** — wren-core validation/planning grounds E5 and E3.
2. **One persistent vector store, many collections** — `db_schema`, `sql_pairs`,
   `instructions`; re-indexed on activation.
3. **Single semantic source** — converge the heuristic document-overlay into MDL.
4. **Retrieval grounding everywhere** — retrieved schema feeds both enrichment
   (E2) and SQL generation (R2).

---

# Part A — Comparison (current vs. Wren, gaps first)

### Parity scorecard

| Stage | Parity | Severity | Lead gap |
| --- | --- | --- | --- |
| E1 Ingest/extract | None→**single-source done** | Capability | Overlay gated off (MDL sole source); chunking remains |
| E2 Context assembly | None→**grounded** | Capability | Trimmed reference + authoritative physical schema now in the prompt |
| E3 Generation | Partial→**physical repair loop** | Robustness | Repair loop now physical-aware; deep-engine + modeling few-shot remain |
| E4 Apply/merge | **At parity** | — | Column-level structure preservation landed |
| E5 Validate | Partial | Correctness | Engine validation optional at runtime |
| E6 Index/learn | Partial→**eager reindex done** | Infra | Activation now eagerly re-indexes; single-source convergence (E1) remains |
| R1 Index build | Partial→**done schema** | Infra | Persistent LanceDB + embedding schema index already exist (keyword default); multi-collection remains |
| R2 Retrieve/re-rank | Partial→**table-selection done** | Capability | Top-N model prune landed; full path-unification remains |
| R3 Few-shot/instructions | Partial→**recall + instructions (SQL+enrich) done** | Capability | Semantic recall + instructions in SQL *and* enrichment prompts; only a UI remains |
| R4 Generation | At parity | — | Mechanics in place |
| R5 Correction loop | At parity | — | Engine-error SQL repair exists |
| R6 Learning | **At parity** | — | Confirmed pairs now semantically recalled (cached collection optional) |

---

## A.1 Enrichment pipeline (document → activated MDL)

### E1 — Ingest & extract
- **Gap:** No chunking; whole doc hard-capped at 20k as one blob. A separate
  heuristic extractor (`propose_updates`) runs but its output never reaches
  generation — two disconnected semantic systems.
- **Current:** [`create_document`](semantic_layer/documents.py) extracts plain
  text and truncates to 20k; keyword `propose_updates`
  ([`semantic_layer/review.py`](semantic_layer/review.py)) builds review
  candidates enrichment ignores.
- **Wren:** Documents/instructions are chunked, embedded, and indexed; nothing is
  silently dropped, and indexed knowledge is retrieved at use time.

### E2 — Context assembly (prompt payload)
- **Gap:** The model gets the **full active MDL bodies** + the doc, but **not the
  authoritative physical schema** (catalog columns+types). It cannot ground
  "which columns exist," and large projects do not scale. This is also the
  unresolved **wren_full.md W4 conflict** (W4 claims trimmed reference context;
  code sends full bodies).
- **Current:** payload = `current_mdl` (full dicts via `_active_mdl_json`) +
  `document_text[:20_000]`
  ([`integrations/wren/llm_client.py`](integrations/wren/llm_client.py),
  `propose_mdl_from_document`); no `SchemaIndex` in the prompt.
- **Wren:** Generation is grounded on **retrieved** schema chunks selected for the
  task, not a raw full dump; structure comes from the engine-compiled MDL.

### E3 — Generation
- **Gap:** Single-shot; static file prompt; **no engine-error repair loop** for
  authoring (DF3-deferred); no few-shot exemplars or instructions injected.
- **Current:** one `_call_model` call (`integrations/wren/llm_client.py`);
  structured output json_schema→json_object→prompt fallback
  ([`llm/openai_compatible.py`](llm/openai_compatible.py)); schema label now
  neutral (`structured_response`).
- **Wren:** `semantics_description`/`sql_generation` consume retrieved
  `sql_pairs` + `instructions`; a correction loop folds **engine** errors back
  into the prompt.

### E4 — Apply / merge ✅ at parity
- **Gap:** *Closed.* Previously a touched model was replaced wholesale
  (column-drop possible).
- **Current:** Models merge **column-level** in both the in-place patch and
  multi-file fallback paths — existing columns/types preserved, semantics
  overlaid, new columns appended (`_merge_model_preserving_structure`,
  `_reconcile_overlay_with_base` in `integrations/wren/llm_client.py`).
- **Wren:** Same principle — structure is never overwritten by the LLM. *Residual:*
  cube measures/dimensions still replace wholesale (agent does not author cubes).

### E5 — Validate
- **Gap:** Default validator is a **Python reimplementation**; true engine
  validation (calculated-field/metric/relationship-expression compilation,
  dialect type-checks) only runs when wren-core is installed — **optional at
  runtime**, mandatory only in CI.
- **Current:** `validate_mdl` structural + `SchemaIndex` physical
  ([`semantic_layer/mdl_validator.py`](semantic_layer/mdl_validator.py)); optional
  engine pass import-guarded
  ([`semantic_layer/wren_core_validator.py`](semantic_layer/wren_core_validator.py));
  new dropped-column warning (`_dropped_columns`).
- **Wren:** Validation **is** engine compilation/planning — always authoritative.

### E6 — Index & learn
- **Gap:** An activated MDL file is **not** re-indexed into retrieval; the
  heuristic document-overlay (`rebuild_index`) is a separate version artifact
  merged at query time, so semantics live in two places.
- **Current:** [`semantic_layer/indexer.py`](semantic_layer/indexer.py)
  `rebuild_index` builds a version from approved *updates*; activation
  (`_enforce_activation`, [`app.py`](app.py)) does not trigger a retriever
  re-index.
- **Wren:** Every MDL deploy **re-indexes** the vector store from one source.

## A.2 Retrieval pipeline (question → context → SQL)

### R1 — Index build
- **Gap:** Default is **in-memory LRU + keyword** (embedder is `NullEmbedder`
  unless configured); only `schema_items` indexed — no `sql_pairs`/`instructions`
  collections; rebuilt lazily, not on deploy.
- **Current:** `KeywordRetriever`/`EmbeddingRetriever` over
  `manifest_to_schema_items`, LRU-cached
  ([`semantic_layer/schema_retriever.py`](semantic_layer/schema_retriever.py));
  defaults `wren_retriever="keyword"` ([`config.py`](config.py)); lancedb mode
  exists but opt-in.
- **Wren:** Persistent store; multiple collections; event-driven re-index on
  deploy.

### R2 — Retrieve & re-rank
- **Gap:** **Three overlapping context sources** merged with no unification and
  **no table-selection/re-rank** step; the primary `fetch_context` is keyword-only.
- **Current:** `fetch_context` (keyword `_rank_models`) + `retrieve_mdl_context`
  (embedding retriever) + `merge_indexed_semantic_context` (doc overlay), all in
  [`graph.py`](graph.py).
- **Wren:** **One** `db_schema_retrieval` pipeline → embed question → vector
  search → **LLM table/column selection** prunes before generation.

### R3 — Few-shot examples & instructions
- **Gap:** Recall is **recency-based**, not semantic; the Wren-client
  `recall_examples` is a stub returning `[]`; **no "instructions" concept**.
- **Current:** `memory.recall_examples` recalls confirmed NL→SQL pairs
  ([`graph.py`](graph.py)); `integrations/wren/llm_client.py` stub.
- **Wren:** `sql_pairs` retrieved by **semantic similarity**; user-authored
  **instructions** retrieved and injected.

### R4 — Generation ✅ at parity
- **Gap:** Minor — should consume unified/re-ranked context (R2) + instructions
  (R3).
- **Current:** text-to-SQL consumes retrieved items + recalled examples
  ([`graph.py`](graph.py)).
- **Wren:** `sql_generation` over retrieved schema + few-shot + instructions.

### R5 — Correction loop ✅ at parity
- **Gap:** None of substance; optionally feed engine dry-plan diagnostics.
- **Current:** `repair_sql` node with bounded `repair_attempts`, folding engine
  warnings into the repair prompt ([`graph.py`](graph.py)).
- **Wren:** `sql_correction` regenerates on engine errors — same mechanism.

### R6 — Learning
- **Gap:** Confirmed pairs are stored but **not embedded**, so R3 recall cannot be
  semantic.
- **Current:** `store_confirmed` persists NL→SQL pairs ([`graph.py`](graph.py)).
- **Wren:** Confirmed/curated pairs feed the `sql_pairs` index, closing the loop.

---

# Part B — Implementation plan (sequential checklist)

Reordered into **dependency order** — execute top-to-bottom. Each item lists
**files**, **depends-on**, and **acceptance**. Phases are sequential; items
within a phase may be parallelized unless a depends-on says otherwise.

## Phase 0 — Foundations `[WIP]`

These unblock the most downstream stages; do them first.

- [x] **F0.1 — Engine-as-validator, runtime-mandatory (flag-gated).** `[DONE]`
  Promote wren-core from "CI-only / optional" to a runtime activation gate.
  - Files: `config.py` (`wren_activation_requires_engine`, env
    `WREN_ACTIVATION_REQUIRES_ENGINE`, default false), `app.py`
    (`_enforce_activation` — 409 when engine required + absent; forces
    `deep_validate`), `.env.example`.
  - Depends-on: none.
  - Acceptance: **met** — `test_semantic_layer_api.py::test_activation_requires_engine_blocks_when_absent`
    (409 when absent) and `::test_activation_requires_engine_passes_gate_when_present`
    (gate satisfied when present).

- [~] **F0.2 — Persistent vector store + collection scaffolding.** `[WIP]`
  > **Correction (pass 2):** the persistent backend **already exists** — Part A
  > over-stated this gap. `LanceDbRetriever` (persistence, ANN search, cold-start
  > rehydration, degrade-closed) and `EmbeddingRetriever` (index-time item vectors,
  > question-only embedding, embedder-signature index key) are implemented in
  > `semantic_layer/schema_retriever.py`; `create_retriever` wires
  > `WREN_VECTOR_INDEX=lancedb`. It is simply **off by default** (`wren_retriever=
  > "keyword"`).
  - [x] Persistent `db_schema` vector index (LanceDB) with restart survival.
  - [ ] Named `sql_pairs` / `instructions` collections — `sql_pairs` is now served
    by the **memory store** (semantic recall, see R3/R6); a dedicated cached
    LanceDB `sql_pairs` collection and an `instructions` collection are still TODO.

## Phase 1 — Retrieval core `[WIP]`

- [~] **R1 — Index build: persistent, multi-collection, deploy-driven.** `[WIP]`
  - [x] `db_schema` embedding index — **already implemented**: schema items embed
    at index time, only the question embeds per query, index keyed by embedder
    signature (`schema_retriever.py`: `EmbeddingRetriever`, `retrieve_mdl_context`).
  - [ ] Multi-collection (`sql_pairs`/`instructions`) — `sql_pairs` semantic recall
    landed in the memory store (R3/R6); `instructions` collection still TODO.
  - [ ] Make an embedder the recommended prod default (docs/guidance).

- [~] **R2 — Unify retrieval + table-selection re-rank.** `[WIP]`
  - [x] **Table-selection prune** — `select_relevant_models` narrows the
    relevance-ranked retriever chunks to the top-N coherent models
    (`semantic_layer/runtime.py`), wired into the context node (`graph.py`),
    bounded by `wren_table_selection_limit` (`config.py`, env
    `WREN_TABLE_SELECTION_LIMIT`, default 5; 0 = off). Degrade-closed: no-op
    without a model signal. Tests:
    `test_semantic_layer_runtime.py::test_select_relevant_models_*` (4).
  - [ ] **Collapse the three paths into one entrypoint** — `fetch_context`
    (keyword over materialized MDL) and `merge_indexed_semantic_context` still run
    alongside `retrieve_mdl_context`; full unification (one schema-retrieval
    entrypoint) remains TODO. The heterogeneous item shapes (fetch_context's
    `model` is a dict; retriever's is a name string) are why selection currently
    operates on the homogeneous retriever output, not the merged list.

## Phase 2 — Grounding `[WIP]`

- [~] **E2 — Ground enrichment on retrieved/authoritative schema.** `[WIP]`
  - [x] **Trimmed MDL reference** — `_mdl_reference` replaces the full-body dump
    in the enrichment payload (`integrations/wren/llm_client.py`); prompt updated
    (`prompts/wren_enrichment.md`). **Resolves the W4 conflict** — `wren_full.md`
    W4 note updated to match. Test:
    `test_llm_wren_client.py::test_enrichment_prompt_sends_trimmed_reference_not_full_bodies`.
  - [x] **Authoritative catalog schema into the prompt.** The enrich route builds
    the `SchemaIndex` and passes `schema` (table→columns) into
    `propose_mdl_from_document` (protocol + all `WrenClient` impls gained the param;
    only `LlmWrenClient` uses it). The model receives `physical_schema` and the
    prompt forbids referencing tables/columns absent from it
    (`prompts/wren_enrichment.md`). Tests:
    `test_llm_wren_client.py::test_enrichment_prompt_includes_physical_schema` /
    `::test_enrichment_without_schema_does_not_send_physical_schema`.
    *Limitation:* the schema carries column **names** (the snapshot fallback has no
    types), so it grounds anti-hallucination and lets the model *map* an existing
    catalog column; emitting a brand-new column still needs a type from the model.

- [~] **R3 — Semantic few-shot (`sql_pairs`) + instructions.** `[WIP]`
  - [x] **Semantic example recall** — `_recall_rank`/`_semantic_rank` rank confirmed
    examples by embedding cosine to the question (`semantic_layer/memory_store.py`),
    degrade-closed to keyword; the embedder is shared from the app (`app.py`).
    Tests: `test_memory_store.py::test_semantic_recall_beats_keyword_overlap`,
    `::test_recall_degrades_to_keyword_when_embedder_unavailable`,
    `::test_recall_degrades_when_embedding_raises`,
    `::test_create_memory_passes_embedder_for_semantic_recall`.
  - [x] **Instructions store + retrieval (SQL prompt).** New `Instruction` model +
    migration `0005_instructions`; `semantic_layer/instructions.py` (InMemory +
    SqlAlchemy stores, global-always + similarity recall, degrade-closed);
    CRUD routes (`POST`/`GET`/`DELETE /agent/semantic-layer/instructions`); wired
    through `TextToSqlGraph._draft_sql` → SQL prompt (`prompts/text_to_sql.md`),
    bounded by `wren_instruction_recall_k`. Tests: `test_instructions.py` (6),
    `test_graph.py::test_graph_injects_instructions_into_sql_prompt`,
    `test_semantic_layer_api.py::test_instructions_crud_roundtrip` / `_rejects_empty`.
  - [x] **Instructions in the *enrichment* prompt.** `propose_mdl_from_document`
    gained an `instructions` param (protocol + all impls); the enrich route recalls
    scope instructions (global + document-relevant) and passes them; `LlmWrenClient`
    adds them to the payload and the prompt honors them (`prompts/wren_enrichment.md`).
    Tests: `test_llm_wren_client.py::test_enrichment_prompt_includes_instructions` /
    `::test_enrichment_omits_instructions_when_none`,
    `test_semantic_layer_api.py::test_enrich_injects_scope_instructions_into_prompt`.
  - [ ] **UI surface** for authoring instructions — still TODO (API + backend only).

- [x] **R6 — Confirmed pairs are semantically recalled.** `[DONE]`
  `store_confirmed` already persists the NL→SQL pair; with R3's semantic recall
  those pairs are now retrieved by embedding similarity (not recency), closing the
  learning loop. *Remaining optimization:* a cached LanceDB `sql_pairs` collection
  so recall doesn't re-embed candidates per query (vectors are currently computed
  on demand over the bounded candidate set).

## Phase 3 — Enrichment robustness `[WIP]`

- [~] **E3 — Authoring repair loop (DF3) + retrieval-grounded prompt.** `[WIP]`
  - [x] **Repair loop** — `_draft_with_correction` re-prompts with field-anchored
    structural errors, bounded by `wren_modeling_max_correction_retries`
    (`config.py`, env `WREN_MODELING_MAX_CORRECTION_RETRIES`, default 1); prompt
    consumes `previous_validation_errors` (`prompts/wren_enrichment.md`). Tests:
    `test_llm_wren_client.py::test_enrichment_retries_on_invalid_then_succeeds`,
    `::test_enrichment_no_retry_when_budget_is_zero`.
  - [ ] **Few-shot exemplars + instructions in the modeling prompt** — TODO. (SQL
    prompt has both; the enrichment prompt does not yet.)
  - [x] **Physical (catalog) errors in the loop.** With the `schema` now passed in
    (E2), `_draft_with_correction` validates with the `SchemaIndex`, so a
    hallucinated column/table becomes an error the loop **corrects** (not just
    structural issues). Test:
    `test_llm_wren_client.py::test_enrichment_repairs_hallucinated_column_against_schema`.
    *Remaining:* full wren-core *deep* engine validation inside the loop (the
    client does structural+physical; deep engine validation stays at activation).

- [~] **E1 — Document chunking + converge the heuristic overlay.** `[WIP]`
  - [x] **Single-source convergence (overlay gated off).** The legacy heuristic
    document-overlay is now **off by default** via `wren_semantic_overlay_enabled`
    (`config.py`, env `WREN_SEMANTIC_OVERLAY_ENABLED`); `merge_indexed_semantic_context`
    skips the channel when disabled (`semantic_layer/runtime.py`), wired from
    `graph.py`. By default MDL/enrichment is the **sole** query-time semantic
    source — this also satisfies E6's "retire `merge_indexed_semantic_context`"
    (reversibly, via the flag). Tests:
    `test_semantic_layer_runtime.py::test_merge_indexed_semantic_context_skips_overlay_when_disabled`,
    `test_graph.py::test_graph_skips_overlay_by_default_single_source` /
    `::test_graph_merges_indexed_semantic_context_when_overlay_enabled`.
  - [ ] **Document chunking** — replace the single 20k cut with section chunking
    and index chunks. Deferred: low value until a doc/`instructions` retrieval
    collection consumes chunks (the enrichment path already gets the 20k text).
  - [ ] **Hard removal of `propose_updates`/`rebuild_index`** — left in place
    behind the flag for backward compat rather than deleted; a future pass can
    remove them once no deployment depends on the overlay.

## Phase 4 — Indexing lifecycle `[WIP]`

- [~] **E6 — Re-index on activation; single semantic source.** `[WIP]`
  - [x] **Eager deploy→reindex** — the index-build is factored into
    `ensure_project_indexed` (shared lazy/eager path) with a best-effort
    `reindex_project_mdl`; the activation route calls it on `status="active"`
    (`semantic_layer/schema_retriever.py`, `app.py`). `create_app` gained an
    injectable `retriever` for testing. Tests:
    `test_schema_retriever.py::test_ensure_project_indexed_*` /
    `::test_reindex_project_mdl_*`,
    `test_semantic_layer_api.py::test_activation_eagerly_reindexes_retrieval`.
    *Note:* retrieval was already self-refreshing **lazily** (content-derived
    checksum → rebuild on next query); this moves the build off the first-query
    path and primes the persistent index at deploy.
  - [x] **Retire `merge_indexed_semantic_context`** (the parallel doc-overlay
    query-time channel) — done via the E1 `wren_semantic_overlay_enabled` flag
    (off by default); the channel no longer feeds the prompt by default.

## Phase 5 — Hardening & cleanup `[WIP]`

- [x] **H5.1 — Cube/metric entry-level guard.** `[DONE]`
  `_merge_cube_preserving_structure` preserves a touched cube's `baseObject` and
  any omitted measures/dimensions/timeDimensions; wired into
  `_merge_manifest_sections` (`integrations/wren/llm_client.py`). Test:
  `test_llm_wren_client.py::test_cube_merge_preserves_omitted_measures`.
  *Note:* defensive/forward-looking — the typed `AuthoredManifest` does not carry
  cubes today, so the LLM overlay cannot reach this path; it guards hand-edited
  MDL and a future cube-authoring schema.
- [x] **H5.2 — Doc/flags sync.** `[DONE]` `wren_full.md` W4 note updated to point
  at the implemented `_mdl_reference` trim; `.env.example` documents
  `WREN_ACTIVATION_REQUIRES_ENGINE` and `WREN_MODELING_MAX_CORRECTION_RETRIES`.
  *(Remaining: `wren_model.md` references — minor, when retrieval phases land.)*

- [ ] **H5.3 — Optional dry-plan in SQL repair.** Feed wren-core dry-plan
  diagnostics (not just validation errors) into `repair_sql`.
  - Files: `graph.py`.
  - Acceptance: engine dry-plan output present in repair context; no regression.

---

## Already landed (carried forward — do not redo)

- [x] **E4 — Column-level structure preservation** (`integrations/wren/llm_client.py`):
  `_merge_model_preserving_structure`, `_merge_columns_preserving_structure`,
  `_reconcile_overlay_with_base`. Tests in
  `../tests/unit_tests/superset_ai_agent/test_llm_wren_client.py`.
- [x] **E5 (partial) — Dropped-column detection** (`_dropped_columns`), surfaced as
  a proposal warning rendered by `SemanticLayerImportDialog`. (Engine-runtime
  mandate remains under **F0.1**.)
- [x] **E3 (cosmetic) — Structured-output schema label** `sql_draft` →
  `structured_response` across `llm/openai_compatible.py`, `llm/openai_client.py`,
  `llm/azure_openai.py`.
- [x] **R4 — SQL generation mechanics** (`graph.py`) — at parity.
- [x] **R5 — Engine-error SQL correction loop** (`graph.py`, `repair_sql`) — at
  parity.

---

## Sequencing rationale (why this order)

1. **Phase 0 first** — F0.1 (engine authority) and F0.2 (persistent store +
   collections) are prerequisites for nearly everything: E2/E3 ground on the
   engine; R1/R2/R3/R6/E1/E6 all need the vector store and collections.
2. **Phase 1 (retrieval core)** before grounding — E2 (enrichment grounding) and
   R3 (few-shot/instructions) both consume the unified retrieval path, so building
   R1→R2 once unblocks the most stages.
3. **Phase 2 (grounding)** — E2, R3, R6 turn the retrieval core into actual prompt
   grounding for both pipelines and close the learning loop.
4. **Phase 3 (enrichment robustness)** — E3 repair loop and E1 chunking depend on
   the engine (F0.1) and the store (F0.2) being in place.
5. **Phase 4 (lifecycle)** — E6 wires activation → re-index, which needs R1 and E1.
6. **Phase 5 (hardening)** — residuals and doc/flag sync once behavior is final.

## Risk register

- **Provider structured-output variance** — keep the deterministic fallback (F5,
  `wren_full.md`); the E3 repair loop must not mask it.
- **Embedder availability** — degrade-closed to keyword is the contract; preserve
  it across F0.2/R1.
- **wren-core runtime presence** — F0.1's strict mode must be flag-gated so
  no-engine deployments still function.
- **Migration of two semantic systems** — E1/E6 convergence must preserve existing
  approved semantics; stage behind a migration with a dry-run.
- **Doc drift** — H5.2 must land with the behavior change, not after.

---

## Audited implementation status (2026-06-23, post pass 8)

This section supersedes the per-pass logs (passes 1–8). Every "done" item below
was **grep-verified against source** at this audit. Test baseline: full agent suite
**356 passed, 4 env-gated skips** (`pytest tests/unit_tests/superset_ai_agent`);
ruff clean; edited files mypy-clean apart from the pervasive SQLAlchemy `Column[str]`
ORM-construction pattern shared with `memory_store`/`mdl_files`.

### Completed — enrichment pipeline

| Item | Source evidence | Tests |
| --- | --- | --- |
| **E2 trimmed reference** (resolves wren_full.md W4) | `_mdl_reference` → `integrations/wren/llm_client.py:670`; payload `current_mdl` at `:188` | `test_llm_wren_client.py::test_enrichment_prompt_sends_trimmed_reference_not_full_bodies` |
| **E2 catalog grounding** (`physical_schema`) | `propose_mdl_from_document(schema=…)`; payload `physical_schema` at `llm_client.py:198`; enrich route builds `SchemaIndex.to_tables()` and passes it (`app.py` enrich route) | `::test_enrichment_prompt_includes_physical_schema` / `::_without_schema_does_not_send_physical_schema` |
| **E3 authoring repair loop** (+ physical-aware) | `_draft_with_correction` → `llm_client.py:237`; validates with `schema_index` (`:278`); bound `wren_modeling_max_correction_retries` (`config.py:150`) | `::test_enrichment_retries_on_invalid_then_succeeds`, `::_no_retry_when_budget_is_zero`, `::_repairs_hallucinated_column_against_schema` |
| **E4 column-level structure preservation** | `_merge_model_preserving_structure:611`, `_merge_columns_preserving_structure:703`, `_reconcile_overlay_with_base:777` | `::test_enrichment_preserves_omitted_column_*`, `::_does_not_retype_*`, `::_appends_genuinely_new_column`, `::_fallback_preserves_columns_across_files` |
| **E5 dropped-column detection** | `_dropped_columns` → `llm_client.py:809`; warning surfaced in proposal | `::test_dropped_columns_helper_detects_a_real_drop` |
| **E3 enrichment-prompt instructions** | `propose_mdl_from_document(instructions=…)`; payload at `llm_client.py:201`; enrich route recalls scope instructions (guarded on `default_database_id`) | `::test_enrichment_prompt_includes_instructions` / `::_omits_instructions_when_none`; `test_semantic_layer_api.py::test_enrich_injects_scope_instructions_into_prompt` |
| **H5.1 cube entry-level guard** | `_merge_cube_preserving_structure` → `llm_client.py:645` (defensive; `AuthoredManifest` has no cubes today) | `::test_cube_merge_preserves_omitted_measures` |
| **F0.1 engine-as-validator gate** | flag `wren_activation_requires_engine` (`config.py:144`); 409 when absent (`app.py:1090`) | `test_semantic_layer_api.py::test_activation_requires_engine_blocks_when_absent` / `::_passes_gate_when_present` |
| **E3 cosmetic** schema-name `sql_draft`→`structured_response` | `llm/openai_compatible.py`, `openai_client.py`, `azure_openai.py` | (covered by provider tests) |

### Completed — retrieval pipeline

| Item | Source evidence | Tests |
| --- | --- | --- |
| **R2 table-selection prune** | `select_relevant_models` → `semantic_layer/runtime.py:53`; wired `graph.py:386`; bound `wren_table_selection_limit` (`config.py:180`, default 5) | `test_semantic_layer_runtime.py::test_select_relevant_models_*` (4) |
| **R3/R6 semantic example recall** | `_semantic_rank:95` / `_recall_rank:124` in `memory_store.py`; embedder shared from `app.py` | `test_memory_store.py::test_semantic_recall_beats_keyword_overlap` (+3) |
| **R3 instructions subsystem** | model `AiAgentInstruction` + migration `0005_instructions`; `semantic_layer/instructions.py` (stores + `recall` global+similarity, method **`list_instructions`** to avoid the `list`-as-type mypy footgun); CRUD routes `app.py:1779/1806/1836`; SQL-prompt inject `graph.py:463/489/847`; bound `wren_instruction_recall_k` (`config.py:193`) | `test_instructions.py` (6), `test_graph.py::test_graph_injects_instructions_into_sql_prompt`, `test_semantic_layer_api.py::test_instructions_crud_roundtrip` / `::_rejects_empty` |
| **E6 eager deploy→reindex** | `ensure_project_indexed:546` + `reindex_project_mdl:583` (`schema_retriever.py`); called on activation `app.py:1173`; `create_app(retriever=…)` injectable | `test_schema_retriever.py::test_ensure_project_indexed_*` / `::test_reindex_project_mdl_*`; `test_semantic_layer_api.py::test_activation_eagerly_reindexes_retrieval` |
| **E1/E6 single-source convergence** | overlay gate `wren_semantic_overlay_enabled` (`config.py:186`, default **false**); `merge_indexed_semantic_context(enabled=…)` (`runtime.py:35`); wired `graph.py` | `test_semantic_layer_runtime.py::test_merge_indexed_semantic_context_skips_overlay_when_disabled`; `test_graph.py::test_graph_skips_overlay_by_default_single_source` / `::_when_overlay_enabled` |

### Pre-existing infra (NOT built by this plan — do not redo)

`LanceDbRetriever` (persistent, ANN, rehydration, degrade-closed) and
`EmbeddingRetriever` (index-time vectors, question-only embedding, embedder-signature
index key) already existed in `schema_retriever.py`; `create_retriever` wires
`WREN_VECTOR_INDEX=lancedb`. They are **off by default** (`wren_retriever="keyword"`).
Part A's F0.2/R1 "None/Partial-infra" rating over-stated this gap.

### Remaining work (not done)

- **Instructions / MDL authoring UI** — instructions are **API + backend only**; no
  frontend. This is now the highest *user-facing* gap and requires React work (a
  different stack from these backend passes).
- **Full R2 path-unification** — `fetch_context` (keyword over materialized MDL) and
  the overlay still exist as separate context sources beside `retrieve_mdl_context`;
  table-selection operates only on the homogeneous retriever output. Collapsing the
  three into one entrypoint is the larger refactor (blocked by heterogeneous item
  shapes: fetch_context `model` is a dict, retriever `model` is a name string).
- **Deep-engine validation inside the modeling repair loop** — the loop is
  structural+physical; wren-core *expression* errors (calculated fields, metrics)
  are caught only at activation. Needs the engine reachable in the client.
- **Type-aware schema grounding** — `physical_schema` is **names-only** (the snapshot
  fallback has no types), so the model can map an existing catalog column but cannot
  emit a brand-new column with a correct type. Needs the live `AgentContext` threaded
  through, sacrificing snapshot uniformity.
- **Document chunking (E1 remainder)** — the 20k single-cut extraction in
  `documents.py` is unchanged; low value until a doc/instructions retrieval
  collection consumes chunks.
- **Cached `sql_pairs`/instructions LanceDB collection (R6 optimization)** — semantic
  recall re-embeds candidates per query over the bounded set (≤200 examples / ≤500
  instructions). Correct + degrade-safe, but pays per-recall latency with a slow
  embedder.
- **Hard removal of `propose_updates`/`rebuild_index`** — left behind the overlay
  flag for backward compat, not deleted.

### Standing risks / behavior changes to remember

- **New non-zero defaults change behavior** (all reversible):
  - `WREN_MODELING_MAX_CORRECTION_RETRIES=1` — an invalid first enrichment draft now
    triggers a second model call (latency/cost); `0` restores single-shot.
  - `WREN_TABLE_SELECTION_LIMIT=5` — retrieval context pruned to ≤5 models; only bites
    when the top-`wren_context_limit` (8) chunks span >5 models; `0` disables.
  - `WREN_SEMANTIC_OVERLAY_ENABLED=false` — overlay-reliant installs lose the
    document-review overlay at query time; review/rebuild/state **endpoints unchanged**,
    only the query-time merge is gated; `true` restores.
  - Semantic recall (examples + instructions) ranks by cosine when an embedder is
    configured — order changes from token-overlap for embedder-enabled installs.
- **Durability** of instructions (and memory) is tied to `WREN_MEMORY_STORE`:
  sqlalchemy/lancedb → durable; `none` → process-local (lost on restart).
- **Degrade-closed everywhere**: embedding paths (retriever, memory, instructions)
  fall back to keyword when no embedder / on embedding error; `reindex_project_mdl`
  swallows errors so activation never fails on an indexing hiccup (logged, not
  surfaced); grounding/instructions are omitted (no behavior change) when a schema or
  scope is unavailable.
- **H5.1 cube guard is currently unreachable** via the typed LLM path
  (`AuthoredManifest` has no `cubes`) — forward-looking defense for hand-edited MDL.
- **`list`-as-type footgun**: store methods must not be named `list` (shadows the
  `list[...]` builtin in annotations under `from __future__ import annotations`,
  mypy `valid-type`). The instruction store uses `list_instructions`.

### New config flags added across passes (all in `.env.example`)

`WREN_ACTIVATION_REQUIRES_ENGINE` (false), `WREN_MODELING_MAX_CORRECTION_RETRIES` (1),
`WREN_TABLE_SELECTION_LIMIT` (5), `WREN_SEMANTIC_OVERLAY_ENABLED` (false),
`WREN_INSTRUCTION_RECALL_K` (3).

---

# Part C — Revised parity plan (remaining work to *full* parity)

Supersedes the forward-looking (`[ ]`) items in Part B. Everything Part B marks
`[DONE]`/`[x]` stays done — this is the residual delta between the current system
and Wren native, re-grouped by the **four cross-cutting foundations** (§0) and
ordered so each phase unblocks the next. Each item: **goal · files · depends-on ·
acceptance**. Re-audited against source on 2026-06-23 (anchors verified live).

Severity reflects distance-to-parity, not user pain:
`P1` blocks a foundation · `P2` completes a stage · `P3` polish/cleanup.

## Phase C0 — Persistent multi-collection store as the spine `[DONE]`

The single biggest structural gap vs. Wren is foundation §0.2: **one persistent
store, many named collections, re-indexed on deploy**. Before this pass `db_schema`
was the only real collection; `sql_pairs`/`instructions` re-embedded candidates per
query. C0 lands the cached `sql_pairs` and `instructions` collections — C1/C4 will
retrieve from this spine.

- [x] **C0.1 — Cached `sql_pairs` collection** `P1` `[DONE]`
  - Built: `LanceVectorCache` (new `semantic_layer/vector_cache.py`) — persistent,
    **row-mutable** (`upsert`/`remove`/`search`), keyed by
    `(collection, scope, embedder signature)`, degrade-closed (`search`→`None` ⇒
    caller falls back). `LanceDbMemory` (`semantic_layer/memory_store.py`) wraps
    `SqlAlchemyMemory`: `store_confirmed` embeds the question **once** and upserts
    by dedup identity; `recall_examples` loads SQL candidates (no embed) + ANN id
    lookup, filling from the SQL window so recall never shrinks. `create_memory`
    wraps when `wren_memory_store="lancedb"` + an available embedder.
  - Evidence: `vector_cache.py:LanceVectorCache`,
    `memory_store.py:LanceDbMemory` + `_cache_id` + `load_candidates`.
  - Tests: `test_vector_cache.py` (11), `test_memory_store.py::`
    `test_lancedb_memory_recall_is_semantic_via_cache` (asserts recall embeds only
    the query — `embedded == [[_QUERY]]`), `::_persists_cache_across_instances`,
    `::_recalls_uncached_pair_via_fill`, `::test_create_memory_lancedb_wraps_with_cache`
    / `::_falls_back_without_embedder`.

- [x] **C0.2 — Cached `instructions` collection** `P1` `[DONE]`
  - Built: `LanceDbInstructionStore` (`semantic_layer/instructions.py`) wraps
    `SqlAlchemyInstructionStore`: `add` embeds each **non-global** instruction once
    (globals always apply, never embedded); `recall` = globals + ANN-ranked
    non-globals (fill from the SQL window). `create_instruction_store` wraps under
    the same `lancedb`-mode + embedder condition. `0005` schema unchanged (the
    vector is a derived cache; `ai_agent_instructions` stays the source of truth).
  - Evidence: `instructions.py:LanceDbInstructionStore`.
  - Tests: `test_instructions.py::test_lancedb_instruction_recall_globals_plus_cache_ranked`
    (globals + ANN, recall embeds only the query), `::_wraps_with_cache` /
    `::_falls_back_without_embedder`.

- [x] **C0.3 — Documented prod topology** `P2` `[DONE]`
  - `.env.example` `WREN_MEMORY_STORE` doc now describes `lancedb` mode and the
    recommended prod topology (embedder + `WREN_VECTOR_INDEX=lancedb` +
    `WREN_MEMORY_STORE=lancedb` = one persistent store, many collections). No
    default flip — `sqlalchemy` stays the default; degrade-closed contract intact.
  - Evidence: `.env.example` (`WREN_MEMORY_STORE` block).

## Phase C1 — One retrieval entrypoint + selection over the whole set `[DONE]`

Foundation §0.4 + R2 remainder. C1.1+C1.2 landed pass 10 (unified, normalized
context via one entrypoint); C1.3 (LLM selector) landed pass 14.

- [x] **C1.1 — Unify the model-name extraction across shapes** `P1` `[DONE]`
  - Built: `canonical_model_name(item)` (`semantic_layer/runtime.py`) resolves the
    model an item belongs to across the heterogeneous shapes — retriever chunks
    (`model` is a name string) **and** `fetch_context` model items
    (`{"type":"model","model":{...body...}}`). Non-destructive: it *reads* the name;
    item bodies (the fetch_context column dict the prompt needs) stay intact.
    `select_relevant_models` now uses it, so table-selection prunes `fetch_context`
    model items too — the blocker the audit named (dict-model vs name-string).
    *Chose extraction-over-mutation* rather than a `RetrievedModel` dataclass: the
    fetch_context item *is* the model body, so coercing `model` to a name would lose
    the columns; a read-only extractor unifies selection without a lossy rewrite.
  - Evidence: `runtime.py:canonical_model_name`, `:select_relevant_models`.
  - Tests: `test_semantic_layer_runtime.py::test_canonical_model_name_across_shapes`,
    `::test_select_prunes_fetch_context_dict_models_too`,
    `::test_select_relevant_models_noop_when_within_limit_mixed_shapes`.

- [x] **C1.2 — Collapse merge/select/cap into one entrypoint** `P2` `[DONE]`
  - Built: `build_unified_context(...)` (`semantic_layer/runtime.py`) replaces the
    inline merge→select→cap block in the graph context node
    ([graph.py](graph.py)): it unifies `fetch_context` + overlay + retriever chunks
    into one list, runs table-selection over the **unified** set, then dedups+caps,
    and stamps `retrieval_mode`/`retrieved_item_count` from the surviving retriever
    chunks. **Retriever chunks lead the merged list** so the relevance-ranked source
    wins the table-selection budget ahead of the legacy keyword `fetch_context`
    models (consistent with `cap_context_items`' retriever-priority) — avoids
    regressing the better-ranked source when both are active.
  - Evidence: `runtime.py:build_unified_context`; graph context node now calls it.
  - Tests: `test_semantic_layer_runtime.py::test_build_unified_context_merges_selects_and_caps`,
    `::test_build_unified_context_overlay_only_passthrough`; existing graph
    overlay/MDL tests still green.
  - *Not done (full collapse):* `fetch_context` and the overlay still run as separate
    *fetchers* feeding this one pipeline; ripping `fetch_context` out entirely (sole
    reliance on the retriever) is a larger, riskier step deferred — it would regress
    File/Http wren-engine clients that return engine-ranked models.

- [x] **C1.3 — LLM table/column selection (true re-rank)** `P2` `[DONE]` (pass 14)
  - Built: `build_unified_context` gained a `model_selector` seam
    (`semantic_layer/runtime.py`, type `ModelSelector = Callable[[list[str]],
    list[str] | None]`); `_select_models` applies it over the unified candidate model
    names and **degrades closed** to the heuristic `select_relevant_models` on
    `None`/empty. Extracted `candidate_model_names` + `_filter_to_models` (shared by
    both paths). `llm_select_models` (`graph.py`) calls the model with a new
    `prompts/table_selection.md`, validates the returned names against the candidates
    (hallucinated names dropped), preserves retriever-rank order, and caps to the
    limit; returns `None` on missing prompt / provider error / unparseable / empty.
    Wired flag-gated via `TextToSqlGraph._model_selector` (off → `None` → heuristic).
  - Config: `wren_llm_table_selection` (env `WREN_LLM_TABLE_SELECTION`, default
    **false** — opt-in: one model call per retrieval). `.env.example` documented.
  - Tests: `test_semantic_layer_runtime.py::test_build_unified_context_uses_model_selector`
    / `::_selector_none_falls_back_to_heuristic` / `::_selector_empty_falls_back`;
    `test_graph.py::test_llm_select_models_returns_validated_subset` /
    `::_caps_to_limit_in_rank_order` / `::_bad_json_returns_none` /
    `::_empty_candidates_skips_call` / `::test_model_selector_is_none_when_flag_off` /
    `::_built_when_flag_on`.

## Phase C2 — Engine as the always-authoritative validator `[DONE]`

Foundation §0.1 + E5/E3 remainder. Landed pass 11 (both items behind/with their
flags, degrade-closed).

- [x] **C2.1 — Deep-engine validation in the modeling loop** `P1` `[DONE]`
  - Built: `_draft_with_correction` now, when structural+physical validation passes
    **and** `wren_modeling_deep_validation` is on **and** wren-core is importable,
    runs `_deep_validate` and folds engine errors into the same correction loop.
    `_full_proposed_manifest(base_mdl, proposed)` reconstructs the **whole** manifest
    (proposed models win by name; every other active model is carried over) before
    calling `validate_with_wren_core`, so cross-file references resolve and don't
    false-positive — the partial-overlay problem the design note flagged.
    `integrations/wren/llm_client.py`: `_full_proposed_manifest`,
    `_deep_validate`, `_deep_validation_enabled`. New flag
    `wren_modeling_deep_validation` (`config.py`, env `WREN_MODELING_DEEP_VALIDATION`,
    **default false** — opt-in: an engine compile per draft + can surface failures
    earlier). No-op when wren-core absent (`validate_with_wren_core` is import-guarded
    *and* the gate checks `wren_core_available()`).
  - Tests: `test_llm_wren_client.py::test_full_proposed_manifest_unions_proposed_over_base`,
    `::test_enrichment_deep_validation_repairs_engine_error` (engine error forces a
    retry, folded into `previous_validation_errors`),
    `::test_enrichment_skips_deep_validation_when_flag_off`.
  - *Known gap (documented):* `base_mdl` (`_active_mdl_json`) carries **models only**,
    so the in-loop deep validation includes *proposed* relationships but **not
    pre-existing relationships from untouched files**. It therefore catches proposed
    expression/relationship/calculated-field errors against the full model set, but a
    proposed change that *breaks an existing* relationship is still only caught at
    activation (the full gate). Closing this needs collecting active-file
    relationships into the merge — a follow-up.

- [x] **C2.2 — Engine dry-plan diagnostics into SQL repair (was H5.3)** `P3` `[DONE]`
  - Built: `dry_plan_diagnostics(dry_plan)` (`graph.py`) extracts `error`/`errors`
    strings (deduped, degrade-closed for a clean/missing/odd-shaped plan); `_repair_sql`
    folds them into `repair_errors` alongside the validator + engine warnings, and
    surfaces them in the repair trace.
  - Tests: `test_graph.py::test_dry_plan_diagnostics_extracts_error_and_errors`,
    `::_degrades_for_clean_or_missing_plan`,
    `::test_repair_sql_folds_dry_plan_diagnostics_into_prompt`.
  - *Limitation (documented):* the dry-plan node runs **once on the initial draft**
    (not re-run inside the repair loop), so diagnostics describe the first SQL — still
    useful for every repair attempt; extraction is conservative (`error`/`errors`
    only), so an engine using other keys yields `[]` (no regression).

## Phase C3 — Type-aware grounding `[DONE]`

E2 remainder. `physical_schema` was **names-only**; types now flow on the live path.

- [x] **C3.1 — Thread live catalog types into enrichment** `P2` `[DONE]` (pass 12)
  - Built:
    - `SchemaIndex` gained `column_types` (`semantic_layer/mdl_validator.py`):
      `from_agent_context` populates it (live), `from_snapshot(tables, types=None)`
      stays names-only unless types are explicitly threaded; helpers `column_type`,
      `typed_tables`, `has_types`.
    - **Cross-family type-mismatch error** (`column_type_mismatch`) via
      `_type_family` + `_type_mismatch_message`: fires only when the catalog type and
      the proposed type resolve to a *known, different* family (string/numeric/
      temporal/boolean). Conservative by design — unknown types, calculated/
      relationship columns, parameterized types (`VARCHAR(255)`), and `BIT` never
      flag. Integrates with the correction loop (it's an error → triggers a retry).
    - `schema_types` param threaded through `propose_mdl_from_document` (protocol +
      all impls), `_enrichment_proposal`, and the enrich route — passing
      `schema_index.typed_tables()` **only when `has_types()`** (live path), `None`
      on the names-only snapshot (degrades to E2 grounding). `LlmWrenClient` adds
      `physical_schema_types` to the prompt payload and builds a typed loop index
      (`SchemaIndex.from_snapshot(schema, schema_types)`). Prompt rule added
      (`prompts/wren_enrichment.md`).
  - Tests: `test_mdl_validator.py::test_schema_index_from_agent_context_carries_types`,
    `::test_type_mismatch_cross_family_is_error`, `::test_type_match_same_family_passes`,
    `::test_type_mismatch_ignored_for_unknown_catalog_type`,
    `::test_type_check_skipped_for_names_only_snapshot`,
    `::test_from_snapshot_with_types_enables_type_check`,
    `::test_type_mismatch_skipped_for_calculated_column`;
    `test_llm_wren_client.py::test_enrichment_prompt_includes_physical_schema_types` /
    `::_omits_physical_schema_types_when_absent`.
  - *Conservatism note:* the validator rejects only **unambiguous cross-family**
    mismatches, not within-family precision/width differences (e.g. INT vs BIGINT,
    VARCHAR(50) vs VARCHAR(255)) — catalog and MDL type vocabularies differ too much
    to flag those without false positives. A precise type-equivalence map is a
    follow-up.

## Phase C4 — Document chunking + relevance selection `[DONE]`

E1 remainder. The blind 20k head-cut is replaced by section-aware retention +
schema-relevance selection (pass 13).

- [x] **C4.1 — Section chunking + relevance-aware selection** `P3` `[DONE]`
  - Built: new `semantic_layer/document_chunks.py` —
    - `chunk_sections` (blank-line blocks, oversized blocks hard-split on whitespace);
    - `truncate_to_sections(text, limit)` — **ingestion** retains whole sections up to
      `wren_document_extract_char_limit` (default 200k) instead of a mid-section cut
      (`semantic_layer/documents.py` now uses it; the prior hard 20k head-cut is gone);
    - `select_relevant_sections(text, *, terms, budget)` — **enrichment** assembles the
      sections most relevant to the project's table/column/model names (keyword overlap)
      within `wren_document_prompt_char_budget` (default 20k), re-joined in document
      order. Degrades closed: within-budget docs returned unchanged; no terms → head
      selection. `integrations/wren/llm_client.py` builds the term set via `_schema_terms`
      (physical schema + active MDL) and uses it for the prompt's `document_text`.
  - Config: `wren_document_extract_char_limit` (env `WREN_DOCUMENT_EXTRACT_CHAR_LIMIT`),
    `wren_document_prompt_char_budget` (env `WREN_DOCUMENT_PROMPT_CHAR_BUDGET`),
    both in `.env.example`.
  - Tests: `test_document_chunks.py` (10 — chunk/truncate/select incl. relevant-late-
    section, order preservation, head degrade, zero-limit); `test_llm_wren_client.py::`
    `test_enrichment_selects_relevant_late_section_within_budget` /
    `::test_enrichment_small_document_sent_whole`.
  - *Scope note:* this is the **behavior** win (late content survives, relevance-budgeted)
    without a persistent `documents` LanceDB collection — enrichment is one-shot and the
    relevance signal (schema names) is keyword-cheap, so embedding chunks into the C0
    vector store for cross-pass reuse was not warranted here. A persistent doc collection
    feeding *SQL-time* retrieval remains a possible follow-up (not required by this gap).

## Phase C5 — Authoring UI `[TODO]`

R3 UI remainder — the highest **user-facing** gap. Instructions and MDL authoring
are API+backend only.

- [x] **C5.1 — Instructions authoring UI** `P2` `[DONE]` (2026-06-24)
  - Goal: React surface to list/create/delete instructions (global + scoped) over
    the existing CRUD routes (`app.py:1779/1806/1836`).
  - Implemented:
    - `api.ts` — `Instruction` type + `listInstructions`/`createInstruction`/
      `deleteInstruction` (reuse `requestJson` + `semanticScopeParams`).
    - `SemanticLayerEditor/InstructionsPanel.tsx` — new self-contained panel
      (props `{ scope, canWrite }`): list, add form (TextArea + "Always apply"
      switch), per-row `Popconfirm` delete; empty-schema guard; degrade on error
      via danger toast. `@superset-ui/core` components only, no `any`.
    - `SemanticLayerEditor/index.tsx` — mounted under a new `ContentTabs`
      ("Models" / "Instructions"); reuses the editor's existing `scope` + `canWrite`.
    - `InstructionsPanel.test.tsx` — 8 RTL tests (list, empty, create+payload,
      `is_global`, delete-confirm, read-only, no-schema guard, load-error).
  - Verification: `jest src/SqlLab/components/AiAgentPanel` **46 passed**
    (was 38; +8); `npm run type` clean (0 errors); prettier clean. oxlint not
    runnable locally (native-binding error — CI covers; same limitation noted in
    `wren_full.md §10.3`).
  - **Deferred from plan (residual):** the per-`scope` persistence warning
    (mitigation #5 — `semantic_layer_persistent` from `/health`) was *not* wired,
    to keep the panel self-contained; tracked below.
  - **Key residual risk — instruction scope vs. query scope — RESOLVED (pass 15).**
    The editor authors at schema scope (`dataset_ids: []`), but SQL-time recall
    hashed the *query* scope including `dataset_ids`, so an editor-authored
    instruction was silently **not** recalled when a chat query had datasets selected
    (and `is_global` did not escape it — recall filters by `scope_hash` before the
    global split). **Fix:** new `instruction_scope_hash(scope)` (`semantic_layer/
    store.py`) hashes a schema-level scope (`dataset_ids` dropped); wired into **all
    four** instruction touchpoints — graph SQL-time recall (`graph.py`
    `_instruction_scope_hash`), enrich-time recall, and the create/list routes
    (`app.py`). Memory (NL→SQL) recall intentionally stays dataset-scoped via the
    original `scope_hash`. Tests: `test_graph.py::test_instruction_scope_hash_ignores_dataset_selection`
    + the rewritten `::test_graph_injects_instructions_into_sql_prompt` (authors at
    schema scope, queries with datasets → recalled);
    `test_semantic_layer_api.py::test_instructions_listed_regardless_of_dataset_selection`.

- [ ] **C5.2 — MDL review/activate authoring polish** `P3`
  - Goal: surface dropped-column warnings (`_dropped_columns`) and the engine gate
    (F0.1 409) cleanly in the import/review dialog.
  - Files: `../superset-frontend/src/.../SemanticLayerImportDialog.tsx`.
  - Depends-on: C5.1 patterns.
  - Acceptance: warnings/gate errors rendered with actionable copy; RTL coverage.

## Phase C6 — Cleanup `[TODO]`

- [ ] **C6.1 — Hard-remove `propose_updates` / `rebuild_index`** `P3`
  - Goal: delete the legacy heuristic overlay once C1 makes it fully dead and no
    deployment relies on `WREN_SEMANTIC_OVERLAY_ENABLED=true`.
  - Files: `semantic_layer/review.py`, `semantic_layer/indexer.py`, `graph.py`,
    `config.py` (drop the flag), `runtime.py` (`enabled` param).
  - Depends-on: C1.2 (overlay channel retired in code, not just gated).
  - Acceptance: overlay code paths removed; no test references the flag; migration
    note in `wren_full.md`/`UPDATING`.

## Revised sequencing & critical path

```
C0 (store spine) ─┬─→ C1 (unify+select) ─→ C6 (remove overlay)
                  └─→ C4 (doc chunking)
C2 (engine authority) ── independent, gated on engine presence
C3 (type grounding)  ── independent
C5 (UI)              ── independent (frontend stack)
```

**Critical path to "full parity":** C0 → C1 → C2 → C3 (the four foundations).
C4/C5/C6 are parallelizable polish. Recommended next pass: **C0.1** (cached
`sql_pairs`) — it is self-contained, reuses existing LanceDB infra, removes the
per-query re-embed, and is the literal first brick of the persistent
multi-collection spine.

## Carry-forward risks (still apply to Part C)

- **Degrade-closed is the contract** — every C0/C1/C2/C3 path must keep its keyword/
  structural/names-only fallback when the embedder/engine/live-context is absent.
- **No default behavior flips** — C0.3 ships *guidance*; the zero-config keyword/
  in-memory dev path stays the default.
- **Type grounding (C3) trades snapshot uniformity** for live types — keep the
  snapshot path working for callers that have no live `AgentContext`.
- **Overlay removal (C6) is destructive** — gate behind a release note; verify no
  install depends on `WREN_SEMANTIC_OVERLAY_ENABLED=true` first.

---

## Pass 9 status (2026-06-23) — Phase C0 landed; C1–C6 remain

**Done this pass:** Phase **C0** (the persistent multi-collection store spine) —
C0.1 cached `sql_pairs`, C0.2 cached `instructions`, C0.3 prod-topology docs. New
module `semantic_layer/vector_cache.py`; wrappers `LanceDbMemory` /
`LanceDbInstructionStore`; both factories branch on `wren_memory_store="lancedb"`
+ an available embedder, degrading to the SQL store otherwise.

**Test baseline:** `pytest tests/unit_tests/superset_ai_agent` → **375 passed, 4
skipped** (was 356/4; +19: 11 vector-cache, 5 memory, 3 instructions). Real
LanceDB exercised via `pytest.importorskip("lancedb")` round-trips (lancedb 0.33
installed). `ruff` clean; `vector_cache.py` mypy-clean. The `Column[str]`
ORM-construction mypy noise in `memory_store.py`/`instructions.py` is the
pre-existing baseline pattern (renumbered by the `load_candidates` refactor), not
new.

**Remaining Phase C (next passes, dependency order):**
- **C1** — unify retrieval to one entrypoint + LLM table/column selection (C1.1
  normalize item shape → C1.2 collapse the three context sources → C1.3 LLM
  selector). The largest refactor; touches `graph.py`/`runtime.py`.
- **C2** — engine-as-authority: **C2.1 deep-engine validation inside the modeling
  repair loop** + **C2.2 dry-plan diagnostics into `repair_sql`**. *Design note
  found this pass:* C2.1 is **not** a drop-in — `_draft_with_correction` validates
  a **partial overlay** (only touched models), but wren-core compiles a **whole**
  manifest (relationships/calculated fields resolve across files). Doing it right
  means merging the overlay against the full active MDL set, then validating the
  merged manifest — which is exactly what activation already does. So C2.1 needs an
  in-loop "merge-then-deep-validate" step (reuse the activation merge), not just a
  `validate_with_wren_core` call. Plan B updated accordingly.
- **C3** — type-aware grounding (thread live `AgentContext` types into enrichment).
- **C4** — document chunking into a doc collection (depends on C0's spine).
- **C5** — instructions/MDL authoring **UI** (frontend stack; API already done).
- **C6** — hard-remove `propose_updates`/`rebuild_index` (after C1 kills the overlay).

**New standing risks introduced by C0 (all bounded / degrade-safe):**
- **Stale cache rows.** Memory eviction (`_evict_old`) and instruction `delete`
  remove the SQL row but **not** the cache row (delete has no `scope_hash` to target
  the per-scope table). Stale rows are inert — they map to nothing on recall and are
  back-filled from the SQL window — but accumulate, and a stale ANN hit can waste a
  top-k slot (back-filled in recency, not similarity, order). Volumes are small
  (≤200 pairs / ≤500 instructions). *Future:* a cache-compaction/rebuild pass.
- **Embedder-signature change goes cold, not wrong.** Rows are keyed by signature,
  so changing the embedder model leaves a cold table → `search`→`None` → recall
  falls back to the inner per-query path until rows are re-written. Correct, but the
  cache does not auto-backfill existing rows under the new signature.
- **First-insert does write→delete→write** in `upsert` (create-table-with-row, then
  delete-then-add for idempotency). Correct, minor redundant I/O; avoids relying on
  a `merge_insert` API not present in older lancedb.
- **Cache is an accelerator, never source of truth** — every failure path keeps the
  SQL store authoritative; a lost/failed cache write only costs ranking quality, not
  data. This is the explicit C0 contract and is unit-tested (connect failure,
  embedding raises, cold scope, no embedder).

---

## Pass 10 status (2026-06-24) — Phase C1.1+C1.2 landed; C1.3 + C2–C6 remain

**Done this pass:** **C1.1** (unified `canonical_model_name` extraction across the
fetch_context dict-model and retriever name-string shapes) + **C1.2** (one
`build_unified_context` entrypoint that merges fetch_context + overlay + retriever
chunks, runs table-selection over the *unified* set, then dedups+caps). The graph
context node's inline merge/select/cap block is replaced by the single call.

**Test baseline:** **379 passed, 4 skipped** (was 375/4; +4 net new runtime tests).
`ruff` clean; `runtime.py`/`graph.py` mypy-clean (the repo-wide `Column[...]` ORM
noise is pre-existing baseline in other files).

**Behavior changes this pass (intended, bounded):**
- **Table-selection now prunes `fetch_context` model items too**, not just the
  retriever output (the C1.1 win). Bites only when the unified set spans more than
  `WREN_TABLE_SELECTION_LIMIT` (default 5) models; degrade-closed (never empties).
- **Merged-context ordering is now retriever-first.** Previously fetch_context items
  led and retriever chunks were appended; now retriever chunks lead so the
  relevance-ranked source wins the selection budget. Prompt item order changes for
  installs where both sources are active (no test pinned the old order; overlay/MDL
  graph tests are order-independent or have no retriever items).
- **`retrieved_item_count`** now counts retriever chunks **surviving** select+cap
  (was the pre-cap `len`). More accurate; no test asserted the old value.

**Design choices worth remembering:**
- **Extraction over a `RetrievedModel` rewrite.** The plan floated normalizing every
  item into one dataclass; rejected because a `fetch_context` item *is* the model
  body (columns), so coercing `model` to a name string would drop what the prompt
  needs. A read-only `canonical_model_name` unifies *selection* without a lossy
  shape rewrite — smaller blast radius, same parity outcome.
- **C1.2 is a *post-retrieval* collapse, not a fetcher collapse.** fetch_context and
  the overlay still run as separate fetchers feeding one unify/select/cap pipeline.
  Full removal of `fetch_context` (sole reliance on the retriever) is deferred — it
  would regress File/Http wren-engine clients that return engine-ranked models.

**Remaining Phase C (dependency order):** C1.3 (LLM selector — plugs into the
`build_unified_context` selection seam) · C2 (deep-engine validation in the modeling
loop — needs the overlay→full-manifest merge, see C2.1 design note · + dry-plan in
SQL repair) · C3 (type-aware grounding) · C4 (doc chunking) · C5 (authoring UI —
frontend) · C6 (hard-remove `propose_updates`/`rebuild_index`).

---

## Pass 11 status (2026-06-24) — Phase C2 landed; C1.3 + C3–C6 remain

**Done this pass:** **C2.1** (deep-engine validation inside the enrichment correction
loop, with `_full_proposed_manifest` solving the partial-overlay→whole-manifest
problem from the pass-9 design note) + **C2.2** (Wren dry-plan diagnostics folded into
`_repair_sql`). New flag `WREN_MODELING_DEEP_VALIDATION` (default false).

**Test baseline:** **385 passed, 4 skipped** (was 379/4; +6: 3 deep-validation,
3 dry-plan). `ruff` clean; `graph.py`/`llm_client.py`/`config.py` mypy-clean (repo-wide
`Column[...]` ORM noise unchanged).

**Decisions / deviations worth remembering:**
- **C2.1 defaults OFF, not "on when engine present."** A literal reading of the
  acceptance was "deep-validate when wren-core is installed." Chose an opt-in flag
  instead (matches F0.1 / overlay flag discipline): default-on would add an engine
  compile per draft *and* surface validation failures earlier than today, changing
  behavior for every wren-core install silently. Operators opt in via
  `WREN_MODELING_DEEP_VALIDATION=true`. **Flag me if you'd prefer default-on.**
- **C2.1 in-loop deep validation is model-complete but not relationship-complete.**
  `base_mdl` carries models only, so the reconstructed manifest includes proposed
  relationships + all models, but not pre-existing relationships from untouched files.
  Catches proposed expression/relationship errors; a proposed change that *breaks an
  existing* relationship is still caught only at activation. Closing it = collect
  active-file relationships into `_full_proposed_manifest` (follow-up).
- **C2.2 diagnostics are from the initial draft.** The dry-plan node runs once before
  the repair loop; not re-run per repair. Diagnostics still guide every attempt.
- **Ordering:** deep validation runs only *after* structural+physical passes (cheap
  checks first); a structurally-invalid draft never pays the engine-compile cost.

**Remaining Phase C (dependency order):** C1.3 (LLM selector) · C3 (type-aware
grounding) · C4 (doc chunking) · C5 (authoring UI — frontend) · C6 (hard-remove
`propose_updates`/`rebuild_index`).

---

## Pass 12 status (2026-06-24) — Phase C3 landed; C1.3 · C4 · C6 remain

**Done this pass:** **C3.1** — type-aware grounding. `SchemaIndex` now carries
catalog `column_types` on the live `from_agent_context` path; `schema_types` flows
through `propose_mdl_from_document` → `physical_schema_types` in the prompt; and a
conservative cross-family `column_type_mismatch` validator error (integrated with the
correction loop). Names-only snapshot path degrades exactly as before.

**Test baseline:** **394 passed, 4 skipped** (was 385/4; +9: 7 validator, 2 prompt).
`ruff` clean (extracted `_validate_column_semantics` to stay under the C901 limit);
`mdl_validator.py`/`llm_client.py`/`app.py` mypy-clean (repo `Column[...]` noise
unchanged).

**Decisions / risks worth remembering:**
- **Type-mismatch is a hard error, NOT flag-gated** — unlike C2.1, because it sits in
  the *same place* as the existing `unknown_column`/`column_without_type` physical
  errors (also un-gated, also only active when a `SchemaIndex` is present). Consistent
  with E2/E5 physical validation; only fires on the live typed path.
- **Conservative on purpose** — cross-family only (string/numeric/temporal/boolean),
  both types recognized. Within-family precision/width (INT vs BIGINT, VARCHAR widths)
  is *not* flagged; `BIT` excluded (boolean-vs-numeric ambiguity); calculated/
  relationship columns skipped. This keeps false positives near zero given catalog-vs-
  MDL type-vocabulary drift. A precise equivalence map is a follow-up.
- **Snapshot uniformity preserved** — `from_snapshot` is still names-only by default;
  types only ground/validate when the live Superset fetch supplied them. On a Superset
  outage the snapshot path silently degrades to the prior names-only behavior (the
  documented trade-off). `from_snapshot(tables, types=…)` exists so a future typed
  snapshot can opt in, but the snapshot store schema is unchanged this pass.
- **Behavior change for live installs:** an enrichment that declares a cross-family
  wrong type on a physical column now fails validation / triggers a modeling retry
  where before it passed. Intended (that's the parity goal); bounded by the
  conservative rule.

**Remaining Phase C (dependency order):** C1.3 (LLM selector — has its seam in
`build_unified_context`) · C4 (doc chunking — depends on C0 spine) · C6 (hard-remove
`propose_updates`/`rebuild_index`). *(C5 authoring UI tracked separately as a frontend
unit.)*

---

## Pass 13 status (2026-06-24) — Phase C4 landed; C1.3 · C6 remain

**Done this pass:** **C4.1** — document chunking + relevance selection. New
`semantic_layer/document_chunks.py`; ingestion retains whole sections up to a 200k cap
(was a hard 20k head-cut), and enrichment selects the schema-relevant sections within a
20k prompt budget. Two new config flags.

**Test baseline:** **406 passed, 4 skipped** (was 394/4; +12: 10 chunking module, 2
enrichment). `ruff` clean; `document_chunks.py`/`documents.py`/`llm_client.py`/`config.py`
mypy-clean.

**Decisions / risks worth remembering:**
- **Two limits, not one.** Ingestion retention (`EXTRACT_CHAR_LIMIT`, 200k) ≫ prompt
  budget (`PROMPT_CHAR_BUDGET`, 20k) on purpose: late content must survive *ingestion*
  (where there is no relevance signal) so it can be relevance-selected at *enrichment*
  (where the schema is known). The old code lost it at ingestion.
- **Storage change.** `extracted_text` in the DB can now be up to 200k chars (was 20k).
  Bounded and operator-tunable (`WREN_DOCUMENT_EXTRACT_CHAR_LIMIT`), docs already capped
  by `WREN_MAX_DOCUMENT_BYTES` (2MB). Set the limit to 20000 to restore the old footprint.
- **Keyword relevance, not embedding.** Section ranking is keyword overlap with
  table/column/model names — cheap, dependency-free, degrade-closed, and a good fit since
  the signal is literal identifiers. No embedder plumbed into the wren client.
- **No persistent `documents` collection.** The plan floated embedding chunks into the C0
  vector store; skipped — enrichment is one-shot and keyword selection suffices. A
  persistent doc collection feeding *SQL-time* retrieval is a possible follow-up, not part
  of this gap.
- **Behavior change:** a previously head-truncated large document now contributes its
  schema-relevant late sections to the enrichment prompt (and retains far more text in the
  DB). Intended; bounded by the two limits.

**Remaining Phase C (dependency order):** C1.3 (LLM table/column selector — plugs into
`build_unified_context`) · C6 (hard-remove `propose_updates`/`rebuild_index`). *(C5
authoring UI tracked separately as a frontend unit.)*

---

## Pass 14 status (2026-06-24) — Phase C1 complete; only C6 remains

**Done this pass:** **C1.3** — LLM table/column selection. `build_unified_context`
gained a `model_selector` seam; `llm_select_models` (flag-gated, opt-in) picks the
relevant model subset and **degrades closed** to the heuristic on any failure. New
`prompts/table_selection.md`, config flag `WREN_LLM_TABLE_SELECTION` (default false).

**Test baseline:** **415 passed, 4 skipped** (was 406/4; +9: 3 runtime selector,
6 graph selector). `ruff` clean; `runtime.py`/`graph.py`/`config.py` mypy-clean.

**Decisions / risks worth remembering:**
- **Default OFF, opt-in.** The selector adds one model call to the previously
  model-free retrieval node; consistent with the other opt-in flags (C2.1, C1 overlay).
  The heuristic `select_relevant_models` remains the default and the fallback.
- **Degrade-closed at three layers:** `llm_select_models` returns `None` on missing
  prompt / provider error / unparseable / empty; `_select_models` falls back to the
  heuristic on `None`/empty; hallucinated names are dropped (validated against
  candidates). So a misbehaving selector can never empty the context or inject a
  non-existent model.
- **Bounded + order-preserving.** Chosen names are capped to `wren_table_selection_limit`
  and re-ordered to the retriever's rank, so the LLM can prune but not reshuffle or
  exceed the budget.
- **Column-level selection deferred.** The seam selects *models* (tables), matching the
  heuristic's granularity; Wren's finer column-level pruning within a model is a
  follow-up (the prompt/selector already has the shape to extend to columns later).

**Phase C status:** C0 ✅ · C1 ✅ (C1.1/C1.2/C1.3) · C2 ✅ · C3 ✅ · C4 ✅ · C5.1 ✅
(separate frontend unit). **Remaining:** **C6** — hard-remove the legacy
`propose_updates`/`rebuild_index` overlay (now fully dead behind
`WREN_SEMANTIC_OVERLAY_ENABLED=false`); a destructive cleanup to gate behind a release
note.

---

## Pass 15 status (2026-06-24) — C5.1 audit + scope-mismatch fix; ready for C6

**Goal:** verify the C5.1 instructions UI (built during C0 by another agent) is fully
wired + functional before C6.

**FE audit — wiring is correct, no changes needed:**
- `api.ts` `listInstructions`/`createInstruction`/`deleteInstruction` hit the right
  routes with `semanticScopeParams`.
- `InstructionsPanel.tsx` — `canWrite`-gated add/delete, schema-scope guard, error
  toasts, empty state; `@superset-ui/core` components.
- `index.tsx` — builds `scope` with `dataset_ids: []`, derives `canWrite` from the
  project permission, mounts the panel in the "Instructions" tab.

**Prerequisite gap found + fixed (backend):** instruction recall used the **query**
scope hash (incl. `dataset_ids`) while authoring used **schema** scope (`dataset_ids:
[]`) — so an authored instruction was silently dropped whenever a chat query selected
datasets, and `is_global` did not help (recall filters by scope hash before the global
split). Added `instruction_scope_hash` (schema-level) and wired it into all four
touchpoints (graph recall, enrich recall, create, list); memory recall stays
dataset-scoped. This makes the C5.1 feature actually functional end-to-end.

**Test baseline:** **417 passed, 4 skipped** (was 415/4; +2: 1 unit scope-hash, 1 API
cross-dataset listing; the masking graph test was rewritten to the real scenario).
`ruff` clean (also added the missing `# noqa: TID251` on `store.py`'s standalone-agent
`json` import); `store.py`/`graph.py`/`app.py` mypy-clean. No FE code changed (FE was
already correct); FE jest/type unaffected.

**Residual (tracked, not blocking C6):** C5.2 (MDL review/activate polish) and the
per-scope persistence warning (mitigation #5) remain deferred frontend items.

**Now ready for C6** — hard-remove `propose_updates`/`rebuild_index`.
