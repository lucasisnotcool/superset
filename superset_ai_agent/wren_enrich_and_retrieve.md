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

# Wren Enrichment & Retrieval — Implementation Study & Plan

This document covers the **two runtime pipelines** the agent uses against the
semantic layer:

- **Enrichment** — a business document (or schema introspection) becomes an
  activatable MDL manifest.
- **Retrieval** — a natural-language question becomes grounded context and SQL.

It complements [`wren_full.md`](wren_full.md) (the native-manifest *authoring/
storage* rebuild, W1–W6). That rebuild fixed the MDL **shape**; this document
targets **pipeline behavior** — bringing enrichment and retrieval up to parity
with Wren AI's native pathway (wren-engine + Haystack-style indexing/retrieval/
generation pipelines over a persistent vector store).

The document has two sections:

- **[Section A — Implementation Study](#section-a--implementation-study)** is the
  source-backed account of the system **as built**: what is at parity, how the
  default configuration actually behaves, and the residual gaps per stage.
- **[Section B — Implementation Plan](#section-b--implementation-plan)** is the
  remaining, genuinely-open work to reach **full** parity, in dependency order.

Source references are relative to `superset_ai_agent/` (frontend paths via
`../superset-frontend/`) and are anchored to **symbol names** (functions, classes,
config fields) rather than line numbers, which drift.

Status legend: `[TODO]` not started · `[WIP]` in progress · `[DONE]`
source-backed and test-verified · `[BLOCKED]` waiting on a decision/dependency.

Test baseline at last audit: `pytest tests/unit_tests/superset_ai_agent` →
**419 passed, 4 env-gated skips**; `ruff` clean.

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

**Cross-cutting parity foundations** (the four pillars this work organizes around):

1. **Engine as the authority** — wren-core validation/planning grounds enrichment
   generation (E3) and validation (E5).
2. **One persistent vector store, many collections** — `db_schema`, `sql_pairs`,
   `instructions`; re-indexed on activation.
3. **Single semantic source** — converge the legacy heuristic document-overlay
   into MDL.
4. **Retrieval grounding everywhere** — retrieved schema feeds both enrichment
   (E2) and SQL generation (R2).

All four foundations are **built and on by default in their structural form**; the
deeper *engine-authoritative* and *vector-cache* behaviors ship as **opt-in flags**
(see [A.3](#a3--defaults--operating-modes)), preserving a zero-config,
dependency-light default path.

---

# Section A — Implementation Study

The as-built state of both pipelines, grounded in source. Subsections:
[A.0 scorecard](#a0--parity-scorecard) · [A.1 enrichment](#a1--enrichment-pipeline-e1e6)
· [A.2 retrieval](#a2--retrieval-pipeline-r1r6) ·
[A.3 defaults & modes](#a3--defaults--operating-modes) ·
[A.4 completed-work map](#a4--completed-work-map).

## A.0 — Parity scorecard

> ⚠️ **Superseded for enrichment — read [Section C](#section-c--enrichment-parity-remediation-field-test-findings) first.**
> This scorecard rates **capability existence in isolation** (each helper has a unit
> test). It does **not** exercise the real operator flow `onboard → review/activate →
> enrich → activate`. A field test (June 2026) against
> [`dev_fixtures/seagate_manufacturing/bi_glossary.md`](dev_fixtures/seagate_manufacturing/bi_glossary.md)
> proved the enrichment pipeline **degrades to a structure-less fallback blob** in the
> default flow. The E1–E6 "At parity" ratings below are therefore **overclaims** for
> the end-to-end path; Section C is the authoritative, source-backed remediation
> checklist and the corrected assessment lives in [C.1](#c1--corrected-parity-assessment).

Parity is rated against Wren native. "At parity (capability)" means the behavior
exists and is test-verified; where it is **opt-in**, the default path uses a
degrade-closed fallback (keyword/structural/heuristic), called out explicitly.

| Stage | Parity | Default behavior | Residual gap |
| --- | --- | --- | --- |
| **E1** Ingest/extract | At parity | Single-source (overlay off everywhere); section-aware retention to 200k | Persistent `documents` collection for SQL-time retrieval |
| **E2** Context assembly | At parity | Trimmed MDL reference + authoritative physical schema; live catalog **types** when available | Types absent on the names-only snapshot path |
| **E3** Generation | At parity (capability) | Physical+structural repair loop (1 retry); deep-engine validation **opt-in** | Modeling-prompt few-shot exemplars |
| **E4** Apply/merge | At parity | Column-level structure preservation | Cube measures/dimensions replace wholesale (agent does not author cubes) |
| **E5** Validate | At parity when engine gate on | Structural + physical (SchemaIndex); engine gate + deep validation **opt-in** | Engine validation not mandatory by default |
| **E6** Index/learn | At parity | Eager re-index on activation; single semantic source | — |
| **R1** Index build | At parity (infra) | Persistent LanceDB + embedding index **exist**; default is keyword / in-memory | Embedder as recommended prod default (guidance) |
| **R2** Retrieve/re-rank | At parity (capability) | One post-retrieval entrypoint, heuristic table-selection; LLM re-rank **opt-in** | Full *fetcher* collapse (sole reliance on the retriever); column-level selection |
| **R3** Few-shot/instructions | At parity (backend + UI) | Semantic recall (degrade-closed to keyword); instructions in SQL **and** enrichment prompts; authoring UI | — |
| **R4** Generation | At parity | Retrieved items + recalled examples + instructions | — |
| **R5** Correction loop | At parity | Engine-error SQL repair + dry-plan diagnostics | Dry-plan runs once (not per repair attempt) |
| **R6** Learning | At parity *when a durable memory store is configured* | **Off under the code default** (`WREN_MEMORY_STORE=none` ⇒ `NullMemory`); semantic recall closes the loop when `sqlalchemy`/`lancedb` | Cache compaction for stale rows |

> **Read R6 carefully.** The learning loop (store confirmed NL→SQL pair → semantic
> recall) is fully built, but the **code default** `wren_memory_store="none"` wires
> `NullMemory`, so by default nothing is stored or recalled. It is at parity only
> when a durable store is configured (the shipped `.env.example` sets `sqlalchemy`).
> See [A.3](#a3--defaults--operating-modes).

## A.1 — Enrichment pipeline (E1–E6)

### E1 — Ingest & extract — at parity
- **As built:** ingestion retains **whole sections** up to
  `wren_document_extract_char_limit` (default 200k) via
  `truncate_to_sections` ([`semantic_layer/document_chunks.py`](semantic_layer/document_chunks.py),
  used by [`semantic_layer/documents.py`](semantic_layer/documents.py)) — the prior
  blind 20k head-cut is gone. At enrichment time, `select_relevant_sections` assembles
  the sections most relevant to the project's table/column/model names (keyword
  overlap) within `wren_document_prompt_char_budget` (default 20k), re-joined in
  document order; degrade-closed (within-budget docs returned unchanged; no terms →
  head selection).
- **Single source:** the legacy heuristic document-overlay is off by default
  everywhere (see E6) — MDL/enrichment is the sole query-time semantic source.
- **Residual:** no persistent `documents` vector collection; section selection is
  keyword, not embedding. Low value until a doc collection feeds SQL-time retrieval.

### E2 — Context assembly (prompt payload) — at parity
- **As built:** the enrichment payload carries a **trimmed MDL reference**
  (`_mdl_reference`, [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py))
  in place of full re-emittable model bodies (resolves the historical `wren_full.md`
  W4 input-shape conflict), plus the **authoritative physical schema** (`physical_schema`
  — table→columns) so the model cannot reference a table/column that does not exist.
  On the **live** path catalog **types** also flow (`physical_schema_types`), letting
  the model type a brand-new column correctly. The enrich route builds the `SchemaIndex`
  and passes `schema`/`schema_types` into `propose_mdl_from_document`.
- **Residual:** the names-only snapshot fallback has no types, so on that path the
  model can *map* an existing catalog column but cannot reliably *emit* a new one.

### E3 — Generation — at parity (capability); deep validation opt-in
- **As built:** `_draft_with_correction` re-prompts on validation errors, bounded by
  `wren_modeling_max_correction_retries` (default 1), feeding field-anchored errors
  back into the prompt (`prompts/wren_enrichment.md`). Validation in the loop is
  **structural + physical** (via the `SchemaIndex`), so a hallucinated column/table —
  or, on the typed live path, a **cross-family type mismatch** — becomes an error the
  loop corrects. Structured output uses the json_schema→json_object→prompt fallback
  with a neutral `structured_response` label.
- **Deep-engine validation (opt-in):** when `wren_modeling_deep_validation` is on
  **and** wren-core is importable, a passing structural+physical draft is additionally
  compiled by wren-core. `_full_proposed_manifest` reconstructs the **whole** manifest
  (proposed models win by name; every other active model is carried over) so cross-file
  references resolve before `validate_with_wren_core`; engine errors fold into the same
  correction loop.
- **Residual:** modeling-prompt few-shot exemplars are not yet injected (the SQL prompt
  has them; the enrichment prompt does not). Deep validation is **model-complete but
  not relationship-complete** — `base_mdl` carries models only, so a proposed change
  that breaks a *pre-existing* relationship in an untouched file is still caught only at
  activation.

### E4 — Apply / merge — at parity
- **As built:** models merge **column-level** in both the in-place patch and the
  multi-file fallback — existing columns/types preserved, semantics overlaid, new
  columns appended (`_merge_model_preserving_structure`,
  `_merge_columns_preserving_structure`, `_reconcile_overlay_with_base`). A defensive
  cube entry-level guard (`_merge_cube_preserving_structure`) preserves a touched cube's
  `baseObject` and omitted measures/dimensions.
- **Residual:** the typed `AuthoredManifest` carries no cubes today, so the cube guard
  is forward-looking (it protects hand-edited MDL and a future cube-authoring schema),
  not reachable via the LLM overlay path.

### E5 — Validate — at parity when the engine gate is on
- **As built:** `validate_mdl` ([`semantic_layer/mdl_validator.py`](semantic_layer/mdl_validator.py))
  runs structural checks plus physical checks against the `SchemaIndex`
  (`unknown_column`, `column_without_type`, and the C3 `column_type_mismatch`). A
  dropped-column detector (`_dropped_columns`) surfaces structural regressions as a
  proposal warning. Engine compilation is available via the import-guarded
  `validate_with_wren_core`
  ([`semantic_layer/wren_core_validator.py`](semantic_layer/wren_core_validator.py)),
  and an activation gate (`wren_activation_requires_engine`) can 409 an activation when
  the engine is required but absent.
- **Residual:** engine validation is **optional at runtime** — both the activation gate
  and in-loop deep validation default off. True parity (engine compilation as the
  always-on authority) is a deployment choice, not the default.

### E6 — Index & learn — at parity
- **As built:** the index build is factored into `ensure_project_indexed` (shared
  lazy/eager path) with a best-effort `reindex_project_mdl`
  ([`semantic_layer/schema_retriever.py`](semantic_layer/schema_retriever.py)); the
  activation route eagerly re-indexes on `status="active"`. Retrieval was already
  self-refreshing lazily (content-checksum → rebuild on next query); this primes the
  persistent index at deploy. The legacy parallel doc-overlay query-time channel is
  retired by default (see A.3).

## A.2 — Retrieval pipeline (R1–R6)

### R1 — Index build — at parity (infra)
- **As built:** `KeywordRetriever`, `EmbeddingRetriever` (index-time item vectors,
  question-only embedding, embedder-signature index key), and `LanceDbRetriever`
  (persistent, ANN, cold-start rehydration, degrade-closed) all exist in
  `schema_retriever.py`; `create_retriever` wires `WREN_VECTOR_INDEX`. The `db_schema`
  embedding index is real. `sql_pairs` and `instructions` are served by the cached
  collections (A.4 / C0). **This infra was pre-existing — not built by this plan.**
- **Residual:** an embedder is not the *recommended* default in code (it is in
  `.env.example`); making embedding the documented prod default is guidance work.

### R2 — Retrieve & re-rank — at parity (capability)
- **As built:** one post-retrieval entrypoint, `build_unified_context`
  ([`semantic_layer/runtime.py`](semantic_layer/runtime.py)), unifies `fetch_context` +
  any enabled overlay + retriever chunks into one list, runs **table-selection over the
  unified set**, then dedups and caps. `canonical_model_name` resolves the model an item
  belongs to across heterogeneous shapes (retriever name-string vs. `fetch_context`
  dict-model) so selection prunes both. Retriever chunks lead the merged list so the
  relevance-ranked source wins the selection budget. The heuristic
  `select_relevant_models` is the default selector (bounded by
  `wren_table_selection_limit`, default 5; 0 = off).
- **LLM re-rank (opt-in):** when `wren_llm_table_selection` is on, `llm_select_models`
  ([`graph.py`](graph.py)) asks the model (`prompts/table_selection.md`) to pick the
  relevant model subset; output is validated against candidates (hallucinated names
  dropped), order-preserved, capped, and **degrades closed** to the heuristic on any
  failure.
- **Residual:** this is a *post-retrieval* collapse — `fetch_context` and the overlay
  still run as separate **fetchers** feeding the one pipeline. Full removal of
  `fetch_context` (sole reliance on the retriever) is deferred (it would regress
  File/Http wren-engine clients that return engine-ranked models). Selection is
  model-level, not column-level.

### R3 — Few-shot examples & instructions — at parity (backend + UI)
- **As built:** confirmed NL→SQL pairs are recalled by **semantic** similarity
  (`_semantic_rank`/`_recall_rank`, [`semantic_layer/memory_store.py`](semantic_layer/memory_store.py)),
  degrade-closed to keyword. The **instructions** subsystem — `Instruction` model +
  migration `0005_instructions`, stores in
  [`semantic_layer/instructions.py`](semantic_layer/instructions.py) (global-always +
  similarity recall), CRUD routes, and injection into **both** the SQL prompt
  (`prompts/text_to_sql.md`) and the enrichment prompt (`prompts/wren_enrichment.md`) —
  is complete, bounded by `wren_instruction_recall_k` (default 3). A React authoring
  surface (`InstructionsPanel.tsx`, mounted in the editor's "Instructions" tab) lists/
  creates/deletes instructions over the CRUD routes.
- **Scope correctness:** instructions are recalled at **schema** scope
  (`instruction_scope_hash`, [`semantic_layer/store.py`](semantic_layer/store.py)) —
  `dataset_ids` dropped — so an editor-authored instruction is recalled regardless of a
  chat query's selected datasets (memory recall intentionally stays dataset-scoped).
- **Durability caveat:** see R6 / A.3 — instruction durability is tied to
  `WREN_MEMORY_STORE` (default `none` ⇒ `InMemoryInstructionStore`, process-local).

### R4 — Generation — at parity
- **As built:** text-to-SQL consumes retrieved items + recalled examples + recalled
  instructions ([`graph.py`](graph.py), `_draft_sql`).

### R5 — Correction loop — at parity
- **As built:** `_repair_sql` ([`graph.py`](graph.py)) regenerates on engine errors,
  bounded by `repair_attempts`, folding validator errors, engine warnings, **and**
  Wren dry-plan diagnostics (`dry_plan_diagnostics`) into the repair prompt.
- **Residual:** the dry-plan runs once on the initial draft (not re-run per repair
  attempt), so diagnostics describe the first SQL — still useful guidance for every
  attempt; extraction is conservative (`error`/`errors` keys only).

### R6 — Learning — at parity *when a durable memory store is configured*
- **As built:** `store_confirmed` persists the NL→SQL pair; with R3's semantic recall
  those pairs are retrieved by embedding similarity (not recency), closing the loop. The
  cached `sql_pairs` collection (A.4 / C0.1) makes recall an ANN lookup rather than a
  per-query re-embed.
- **Default-behavior caveat:** under the **code default** `wren_memory_store="none"`,
  `create_memory` returns `NullMemory` — `store_confirmed` is a no-op and recall returns
  empty. The loop runs only with `WREN_MEMORY_STORE=sqlalchemy|lancedb` (the shipped
  `.env.example` sets `sqlalchemy`). See A.3.
- **Residual:** stale cache rows (eviction/delete remove the SQL row, not the cache row)
  accumulate inertly; a compaction pass is a follow-up.

## A.3 — Defaults & operating modes

The system has a deliberate split between **code defaults** (used when an env var is
unset — e.g. in tests and zero-config dev) and the **shipped `.env.example`** (the
template a deployment copies). They differ on two keys; both are intentional, but the
distinction is load-bearing and was a prior source of confusion.

| Flag | Code default (`config.py`) | `.env.example` | Effect of the default |
| --- | --- | --- | --- |
| `WREN_MEMORY_STORE` | `none` | `sqlalchemy` | **Code default disables the learning loop** (`NullMemory`); instructions fall to `InMemoryInstructionStore` (process-local, lost on restart). `.env.example` deployments get durable memory + instructions. |
| `WREN_RETRIEVER` | `keyword` | `embedding` | Code default is keyword ranking; `.env.example` deployments retrieve by embedding (needs an embedder configured). |
| `WREN_VECTOR_INDEX` | `memory` | `memory` | In-memory index; `lancedb` (persistent, multi-collection) is opt-in. |
| `WREN_SEMANTIC_OVERLAY_ENABLED` | `false` | `false` | Legacy doc-overlay off at query time **everywhere** — single-turn (`graph.py`) and multi-turn (`conversation_graph.py`). |
| `WREN_ACTIVATION_REQUIRES_ENGINE` | `false` | `false` | Activation does not require wren-core (no 409 gate). |
| `WREN_MODELING_DEEP_VALIDATION` | `false` | `false` | No wren-core compile in the enrichment loop (structural+physical only). |
| `WREN_LLM_TABLE_SELECTION` | `false` | `false` | Heuristic table-selection (no extra model call). |
| `WREN_TABLE_SELECTION_LIMIT` | `5` | `5` | Context pruned to ≤5 models when the unified set spans more. |
| `WREN_INSTRUCTION_RECALL_K` | `3` | `3` | ≤3 non-global instructions recalled per question (globals always apply). |
| `WREN_MODELING_MAX_CORRECTION_RETRIES` | `1` | `1` | One enrichment re-draft on an invalid first attempt. |
| `WREN_DOCUMENT_EXTRACT_CHAR_LIMIT` | `200000` | `200000` | Ingestion retains whole sections up to 200k. |
| `WREN_DOCUMENT_PROMPT_CHAR_BUDGET` | `20000` | `20000` | Enrichment prompt document budget. |

**Recommended prod topology** (`.env.example` documents this; no code default flip):
an embedder + `WREN_VECTOR_INDEX=lancedb` + `WREN_MEMORY_STORE=lancedb` = one
persistent store, many collections — closest to Wren native.

**Degrade-closed is the contract everywhere.** Embedding paths (retriever, memory,
instructions) fall back to keyword when no embedder is configured or on embedding
error; `reindex_project_mdl` swallows errors so activation never fails on an indexing
hiccup; grounding/instructions are omitted (no behavior change) when a schema or scope
is unavailable; the LLM table selector and deep validation degrade to the heuristic /
structural path. The cache is an accelerator, never the source of truth — a failed
cache write costs ranking quality, not data.

**Behavior changes worth a release note:**
- Multi-turn chat (`ConversationGraph`) with `WREN_SEMANTIC_OVERLAY_ENABLED` unset
  previously merged the legacy overlay **unconditionally** — it is now off by default,
  consistent with single-turn. Installs that relied on it must set the flag `true`.
- Semantic recall (examples + instructions) ranks by cosine when an embedder is
  configured — order changes from token-overlap for embedder-enabled installs.
- A cross-family wrong type on a physical column now fails validation / triggers a
  modeling retry on the live typed path where it previously passed.

## A.4 — Completed-work map

Symbol-anchored evidence for every shipped capability, with representative tests.

### Enrichment

| Capability | Source evidence | Tests |
| --- | --- | --- |
| E2 trimmed MDL reference | `_mdl_reference`, payload `current_mdl` (`integrations/wren/llm_client.py`) | `test_llm_wren_client.py::test_enrichment_prompt_sends_trimmed_reference_not_full_bodies` |
| E2 catalog grounding (`physical_schema`) | `propose_mdl_from_document(schema=…)`; enrich route builds `SchemaIndex` (`app.py`) | `::test_enrichment_prompt_includes_physical_schema` / `::_without_schema_does_not_send_physical_schema` |
| E3 authoring repair loop (physical-aware) | `_draft_with_correction`; `wren_modeling_max_correction_retries` | `::test_enrichment_retries_on_invalid_then_succeeds`, `::_no_retry_when_budget_is_zero`, `::_repairs_hallucinated_column_against_schema` |
| E3 deep-engine validation (opt-in) | `_deep_validate`, `_full_proposed_manifest`, `_deep_validation_enabled`; `wren_modeling_deep_validation` | `::test_full_proposed_manifest_unions_proposed_over_base`, `::_deep_validation_repairs_engine_error`, `::_skips_deep_validation_when_flag_off` |
| E4 column-level structure preservation | `_merge_model_preserving_structure`, `_merge_columns_preserving_structure`, `_reconcile_overlay_with_base` | `test_llm_wren_client.py::test_enrichment_preserves_omitted_column_*`, `::_appends_genuinely_new_column`, `::_fallback_preserves_columns_across_files` |
| E5 dropped-column detection | `_dropped_columns` | `::test_dropped_columns_helper_detects_a_real_drop` |
| E3 enrichment-prompt instructions | `propose_mdl_from_document(instructions=…)`; enrich route recalls scope instructions | `::test_enrichment_prompt_includes_instructions` / `::_omits_instructions_when_none`; `test_semantic_layer_api.py::test_enrich_injects_scope_instructions_into_prompt` |
| H5.1 cube entry-level guard (defensive) | `_merge_cube_preserving_structure` | `::test_cube_merge_preserves_omitted_measures` |
| F0.1 engine-as-validator gate (opt-in) | `wren_activation_requires_engine`; 409 in `_enforce_activation` (`app.py`) | `test_semantic_layer_api.py::test_activation_requires_engine_blocks_when_absent` / `::_passes_gate_when_present` |
| C3 type-aware grounding | `SchemaIndex.column_types`, `from_agent_context`, `column_type_mismatch`, `_type_family`; payload `physical_schema_types` | `test_mdl_validator.py::test_schema_index_from_agent_context_carries_types`, `::_type_mismatch_cross_family_is_error`, `::_type_check_skipped_for_names_only_snapshot` (+4); `test_llm_wren_client.py::test_enrichment_prompt_includes_physical_schema_types` |
| C4 document chunking + relevance selection | `chunk_sections`, `truncate_to_sections`, `select_relevant_sections` (`semantic_layer/document_chunks.py`); `_schema_terms` (`llm_client.py`) | `test_document_chunks.py` (10); `test_llm_wren_client.py::test_enrichment_selects_relevant_late_section_within_budget` / `::_small_document_sent_whole` |

### Retrieval

| Capability | Source evidence | Tests |
| --- | --- | --- |
| R2 unified entrypoint + heuristic selection | `build_unified_context`, `canonical_model_name`, `select_relevant_models`, `cap_context_items` (`semantic_layer/runtime.py`); `wren_table_selection_limit` | `test_semantic_layer_runtime.py::test_build_unified_context_merges_selects_and_caps`, `::test_canonical_model_name_across_shapes`, `::test_select_prunes_fetch_context_dict_models_too` (+) |
| R2 LLM table selection (opt-in) | `llm_select_models` (`graph.py`), `model_selector` seam (`runtime.py`), `prompts/table_selection.md`; `wren_llm_table_selection` | `test_graph.py::test_llm_select_models_returns_validated_subset` / `::_caps_to_limit_in_rank_order` / `::_bad_json_returns_none` (+); `test_semantic_layer_runtime.py::test_build_unified_context_uses_model_selector` (+2) |
| R3/R6 semantic example recall | `_semantic_rank`, `_recall_rank` (`semantic_layer/memory_store.py`) | `test_memory_store.py::test_semantic_recall_beats_keyword_overlap` (+3) |
| R3 instructions subsystem | `Instruction` model + `0005_instructions`; `semantic_layer/instructions.py` (`list_instructions`, `recall`); CRUD routes (`app.py`); SQL-prompt inject (`graph.py`); `wren_instruction_recall_k` | `test_instructions.py` (6); `test_graph.py::test_graph_injects_instructions_into_sql_prompt`; `test_semantic_layer_api.py::test_instructions_crud_roundtrip` / `::_rejects_empty` |
| R3 instruction scope correctness | `instruction_scope_hash` (`semantic_layer/store.py`); wired in graph recall, enrich recall, CRUD routes | `test_graph.py::test_instruction_scope_hash_ignores_dataset_selection`; `test_semantic_layer_api.py::test_instructions_listed_regardless_of_dataset_selection` |
| R3 instructions authoring UI | `InstructionsPanel.tsx`, `index.tsx` ("Instructions" tab), `api.ts` (`listInstructions`/`createInstruction`/`deleteInstruction`) | `InstructionsPanel.test.tsx` (8 RTL) |
| R5 dry-plan diagnostics | `dry_plan_diagnostics` (`graph.py`), folded into `_repair_sql` | `test_graph.py::test_dry_plan_diagnostics_extracts_error_and_errors`, `::_degrades_for_clean_or_missing_plan`, `::test_repair_sql_folds_dry_plan_diagnostics_into_prompt` |
| E6 eager deploy→reindex | `ensure_project_indexed`, `reindex_project_mdl` (`schema_retriever.py`); called on activation (`app.py`) | `test_schema_retriever.py::test_ensure_project_indexed_*` / `::test_reindex_project_mdl_*`; `test_semantic_layer_api.py::test_activation_eagerly_reindexes_retrieval` |
| E1/E6 single-source convergence (both flows) | `wren_semantic_overlay_enabled`; `merge_indexed_semantic_context(enabled=…)` defaults `False` (`runtime.py`); gated in `graph.py` **and** `conversation_graph.py` | `test_semantic_layer_runtime.py::test_merge_indexed_semantic_context_skips_overlay_when_disabled`; `test_graph.py::test_graph_skips_overlay_by_default_single_source`; `test_conversation_graph.py::test_conversation_graph_skips_overlay_by_default` / `::_merges_overlay_when_enabled` |

### Persistent multi-collection store (foundation §0.2)

| Capability | Source evidence | Tests |
| --- | --- | --- |
| Cached `sql_pairs` collection | `LanceVectorCache` (`semantic_layer/vector_cache.py`); `LanceDbMemory` + `_cache_id` + `load_candidates` (`memory_store.py`); `create_memory` wraps under `lancedb` + embedder | `test_vector_cache.py` (11); `test_memory_store.py::test_lancedb_memory_recall_is_semantic_via_cache` / `::_persists_cache_across_instances` / `::_recalls_uncached_pair_via_fill` (+2) |
| Cached `instructions` collection | `LanceDbInstructionStore` (`instructions.py`); `create_instruction_store` wraps under the same condition | `test_instructions.py::test_lancedb_instruction_recall_globals_plus_cache_ranked` / `::_wraps_with_cache` / `::_falls_back_without_embedder` |
| Prod-topology docs | `.env.example` `WREN_MEMORY_STORE` block | — |

### Pre-existing infra (NOT built by this plan — do not redo)

`LanceDbRetriever` (persistent, ANN, rehydration, degrade-closed) and
`EmbeddingRetriever` (index-time vectors, question-only embedding, embedder-signature
index key) already existed in `schema_retriever.py`; `create_retriever` wires
`WREN_VECTOR_INDEX=lancedb`. They are off by default (`wren_retriever="keyword"`,
`wren_vector_index="memory"`).

---

# Section B — Implementation Plan

Remaining, genuinely-open work to reach **full** parity. Everything in Section A is
shipped and test-verified; this section is the residual delta only. Each item:
**goal · files · depends-on · acceptance**. Severity: `P1` blocks a foundation ·
`P2` completes a stage · `P3` polish/cleanup.

## B.0 — Overview & critical path

```
overlay off-by-default everywhere (done) ──→ B.1 (overlay deprecation, product decision)
unified post-retrieval entrypoint (done) ──→ B.2 (full fetcher-collapse)
                                         └─→ B.7 (column-level selection)
engine deep-validation (opt-in, done)    ──→ B.4 (relationship-complete validation)
type grounding live path (done)          ──→ B.5 (precise type-equivalence map)
doc section selection (done)             ──→ B.6 (persistent documents collection)
cached collections (done)                ──→ B.8 (cache compaction)
instructions UI (done)                   ──→ B.3 (review/activate dialog polish)
```

No item blocks another foundation; all are stage-completion or polish. The highest-
value items are **B.1** (resolve the overlay's long-term fate) and **B.2** (the larger
retrieval refactor). The rest are parallelizable.

## B.1 — Overlay deprecation sequence (was "hard-remove `propose_updates`/`rebuild_index`") `P3` `[BLOCKED — product decision]`

- **Goal:** retire the legacy heuristic document-overlay subsystem. **This is a
  deprecation decision, not a mechanical cleanup** — the prior premise that the overlay
  was "fully dead behind the flag" was false: `propose_updates` and `rebuild_index`
  still power **live, authorized REST endpoints** independent of the query-time gate.
  - `propose_updates` ([`semantic_layer/review.py`](semantic_layer/review.py)) →
    document ingestion ([`semantic_layer/documents.py`](semantic_layer/documents.py),
    `proposed_updates`) → consumed by `POST /agent/semantic-layer/documents/{id}/review`
    (`apply_review`), and woven through the `SemanticDocument` schema + stores.
  - `rebuild_index` ([`semantic_layer/indexer.py`](semantic_layer/indexer.py)) →
    `POST /agent/semantic-layer/index/rebuild`.
  - Hard-removing them would break reachable endpoints and persisted schema.
- **Precondition (met):** the overlay query-time channel is now off-by-default
  **everywhere** (single-turn and multi-turn), so overlay data is created-but-inert by
  default.
- **Sequence:**
  1. Deprecate + announce the `/documents/{id}/review` and `/index/rebuild` endpoints
     and the `proposed_updates` document field.
  2. Confirm no client/UI depends on them (check the `SemanticLayerEditor` import/review
     flow).
  3. Remove the code **with a data migration** for persisted `proposed_updates` and
     semantic-layer versions.
- **Files:** `semantic_layer/review.py`, `semantic_layer/indexer.py`, `documents.py`,
  `app.py`, `graph.py`/`conversation_graph.py`, `config.py` (drop the flag),
  `runtime.py` (`enabled` param), a new migration; release note in
  `wren_full.md`/`UPDATING`.
- **Acceptance:** endpoints deprecated then removed on schedule; persisted overlay data
  migrated; no test references the flag; migration note landed.

## B.2 — Full fetcher-collapse (R2 remainder) `P2` `[TODO]`

- **Goal:** collapse the three context **fetchers** into one — sole reliance on the
  retriever — beyond the post-retrieval unify/select/cap already in place.
- **Files:** `graph.py`, `conversation_graph.py`, `semantic_layer/runtime.py`,
  the `WrenClient` impls.
- **Depends-on:** B.1 (overlay channel retired in code, not just gated).
- **Acceptance:** `fetch_context` no longer a separate source; File/Http wren-engine
  clients' engine-ranked models still reach the prompt via the retriever path; no
  regression in context tests.

## B.3 — MDL review/activate authoring polish (C5.2) `P3` `[TODO]`

- **Goal:** surface dropped-column warnings (`_dropped_columns`) and the engine gate
  (F0.1 409) cleanly in the import/review dialog; wire the deferred per-scope
  persistence warning (`semantic_layer_persistent` from `/health`).
- **Files:** `../superset-frontend/src/.../SemanticLayerImportDialog.tsx`,
  `InstructionsPanel.tsx`.
- **Depends-on:** existing C5.1 UI patterns.
- **Acceptance:** warnings/gate errors rendered with actionable copy; persistence
  warning shown per scope; RTL coverage.

## B.4 — Relationship-complete deep validation (C2.1 follow-up) `P2` `[TODO]`

- **Goal:** make in-loop deep validation relationship-complete — currently `base_mdl`
  carries models only, so a proposed change that breaks a **pre-existing** relationship
  in an untouched file is caught only at activation.
- **Files:** `integrations/wren/llm_client.py` (`_full_proposed_manifest`).
- **Depends-on:** deep validation (shipped, opt-in); wren-core reachable.
- **Acceptance:** active-file relationships collected into the reconstructed manifest; a
  proposed change that breaks an existing relationship fails in the modeling loop, not
  only at activation.

## B.5 — Precise type-equivalence map (C3 follow-up) `P3` `[TODO]`

- **Goal:** extend type validation beyond unambiguous cross-family mismatches to
  within-family precision/width (INT vs BIGINT, VARCHAR widths) without false positives.
- **Files:** `semantic_layer/mdl_validator.py` (`_type_family` → an equivalence map).
- **Acceptance:** within-family mismatches flagged where genuinely incompatible;
  catalog-vs-MDL vocabulary drift does not produce false positives; tests cover the
  boundary cases.

## B.6 — Persistent `documents` collection for SQL-time retrieval (C4 follow-up) `P3` `[TODO]`

- **Goal:** embed document chunks into the persistent vector store so document knowledge
  feeds **SQL-time** retrieval (not just one-shot enrichment selection).
- **Files:** `semantic_layer/document_chunks.py`, `semantic_layer/vector_cache.py`,
  `schema_retriever.py`.
- **Depends-on:** the C0 store spine.
- **Acceptance:** a `documents` collection indexed on ingest/deploy; SQL-time retrieval
  can surface relevant doc chunks; degrade-closed without an embedder.

## B.7 — Column-level LLM selection (C1.3 follow-up) `P3` `[TODO]`

- **Goal:** extend the LLM selector from model (table) granularity to column-level
  pruning within a model, matching Wren's finer selection.
- **Files:** `graph.py` (`llm_select_models`), `prompts/table_selection.md`,
  `semantic_layer/runtime.py`.
- **Acceptance:** the selector can prune columns within a kept model; degrade-closed to
  model-level selection; bounded and order-preserving.

## B.8 — Cache compaction for stale rows (C0 follow-up) `P3` `[TODO]`

- **Goal:** remove stale `sql_pairs`/`instructions` cache rows left by memory eviction
  and instruction delete (the SQL row is removed but not the per-scope cache row).
- **Files:** `semantic_layer/vector_cache.py`, `memory_store.py`, `instructions.py`.
- **Acceptance:** a compaction/rebuild pass reconciles cache rows against the SQL store;
  stale ANN hits no longer waste a top-k slot.

## B.9 — Embedder as recommended prod default (R1 follow-up) `P3` `[TODO]`

- **Goal:** document an embedder + persistent index as the recommended production
  configuration (guidance, **not** a code-default flip — preserve the zero-config dev
  path).
- **Files:** docs, `.env.example` (already partially done).
- **Acceptance:** prod guidance published; code defaults unchanged; degrade-closed
  contract intact.

## B.10 — Memory scope realignment to canonical project scope `P2` `[TODO]`

- **Goal:** apply the canonical scope rule (adopted in
  [`wren_graph_view.md`](wren_graph_view.md) §7.3, Option 2): durable agent
  knowledge is partitioned by the **project** (`database`+`catalog`+`schema`);
  `dataset_ids` is a *relevance signal*, never a partition key. Instructions
  (`instruction_scope_hash`) and MDL retrieval (project-keyed) already conform;
  **memory is the only knowledge still partitioned by `dataset_ids`** (full
  `scope_hash` in [`store.py`](semantic_layer/store.py) `scope_hash`, used by
  `memory_store` recall).
- **Change:** recall memory at the project scope (drop `dataset_ids` from the
  partition), and pass `dataset_ids` / caller-supplied focus tables as an
  **overlap boost** in `memory_store._semantic_rank` (relevance, not visibility).
- **Files:** `semantic_layer/memory_store.py` (recall + ranking), `graph.py`
  (`_request_scope_hash` use for memory), a re-key/dual-read **migration** of
  stored example rows; optionally fold `instruction_scope_hash` →
  `project_scope_hash` for one shared concept.
- **Depends-on / risk:** **backend-owner sign-off** — this is a behavior change
  (recall set widens) and touches `memory_store.py` (**C0-contended**); schedule
  with C0, not under the graph-view work. The current `scope_hash` "memory is
  legitimately dataset-scoped" comment must be revised with this change.
- **Note:** the graph **semantic query tool (X2)** does **not** depend on this —
  X2 passes a focus hint and never mutates `dataset_ids`, so it is unblocked
  regardless. This item only improves memory recall consistency.
- **Acceptance:** an example confirmed under one dataset selection is recalled for
  a same-project query with a different/empty selection; dataset overlap still
  boosts ranking; migration preserves existing examples; degrade-closed intact.

## B.11 — Risk register

- **Overlay removal (B.1) is destructive and product-facing** — it touches live,
  authorized endpoints (`/documents/{id}/review`, `/index/rebuild`) and persisted
  `proposed_updates` data. It requires a deprecation announcement and a data migration,
  not a code delete. Gate behind a release note; verify no install depends on
  `WREN_SEMANTIC_OVERLAY_ENABLED=true` first.
- **Degrade-closed is the contract** — every retrieval/validation/grounding path must
  keep its keyword/structural/names-only/heuristic fallback when the embedder, engine,
  or live context is absent. Preserve it across B.2/B.4/B.6/B.7.
- **No default behavior flips** — guidance ships (B.9); the zero-config keyword /
  in-memory dev path stays the code default. Note the `code-default` vs `.env.example`
  split documented in [A.3](#a3--defaults--operating-modes).
- **Type grounding trades snapshot uniformity for live types** — keep the names-only
  snapshot path working for callers without a live `AgentContext` (B.5).
- **Provider structured-output variance** — keep the deterministic json fallback; the
  enrichment repair loop and the LLM table selector must not mask it.
- **Durability is store-dependent** — learning (R6) and instruction persistence require
  `WREN_MEMORY_STORE=sqlalchemy|lancedb`; the code default `none` leaves memory off and
  instructions process-local. Surface this in operator docs (B.9).

---

# Section C — Enrichment Parity Remediation (field-test findings)

**Status: enrichment is NOT at end-to-end parity with Wren.** Section A rates each
helper in isolation; this section rates the **operator flow** the product actually
ships (`onboard → review/activate → enrich → activate`) and is the authoritative
remediation checklist. The goal of this section is unambiguous: **bring the
enrichment pipeline to full parity with Wren AI's modeling + semantics-generation +
relationship-recommendation pathway. Nothing less is accepted.** Items are `[TODO]`
until source-backed and test-verified.

Source references are relative to `superset_ai_agent/` and anchored to **symbol
names** (line numbers drift). Sub-sections:
[C.0 the failure](#c0--the-field-test-failure-observed) ·
[C.1 corrected assessment](#c1--corrected-parity-assessment) ·
[C.2 root causes](#c2--root-causes-source-backed) ·
[C.3 remediation checklist](#c3--remediation-checklist-the-implementation-plan) ·
[C.4 Wren parity findings (resolved)](#c4--wren-parity-findings-resolved) ·
[C.5 definition of done](#c5--parity-definition-of-done).

### Parity target — executive decision (there are two Wrens)

A consultant pass (sourced against Wren's repos, 2026-06) established that "Wren" is
now **two products**, and the distinction is load-bearing for every field shape below:

- **v1 — "Wren AI GenBI"** (`wren-ai-service` Haystack/Hamilton pipelines over
  **Qdrant**; `wren-ui` GraphQL authoring; Java/Rust `wren-engine`). This is the
  architecture that owns `semantics_description`, `relationship_recommendation`,
  `db_schema`/`table_descriptions`/`instructions` collections, and the native
  **camelCase MDL** our whole stack already targets. **Structure is engine-authoritative
  and v1 has *no* free-form document→MDL path** — business terms enter only through
  `properties`, **Instructions**, and **Question-SQL pairs**. Preserved on branch
  `legacy/v1` (tag `v1-final`), sunset 2026-05-07.
- **v2 — "Wren, the context layer"** (`pip install wrenai`; Rust `wren-core`; **YAML
  MDL** → `target/mdl.json`; **LanceDB** memory). v2 *does* ingest unstructured docs to
  MDL via agent skills (`/wren-enrich-context`).

**Decision (binding for Section C):** our product requirement is **doc → MDL**, which is
**v2's enrichment *intent***; but our entire codebase emits **v1's native camelCase MDL
field surface** and validates via `wren-core`. We therefore target a **hybrid that is
still strict parity**:

1. **Adopt v2's enrichment trigger** (a business document drives enrichment) — this is
   the feature's premise and is non-negotiable.
2. **Mirror v1's MDL field surface exactly** (`properties.description`/`displayName`/
   `alias`, `relationships[]`, calculated fields, `metrics[]`, views) — no invented
   fields.
3. **Keep v1's authority model**: **structure is introspection-authoritative and
   engine-compiled; the LLM never authors models, physical columns, or types.** The LLM
   may only author *semantics* (descriptions/aliases), *relationships* (proposals),
   *calculated fields* (`isCalculated`+`expression`), and *metric/measure expressions* —
   all preserved-merged onto the introspected structure and engine-validated.

This decision **overrides** any earlier checklist language that implied the LLM should
"model from the catalog." It must not. See [C.4](#c4--wren-parity-findings-resolved) for
the field-level evidence.

## C.0 — The field-test failure (observed)

**Setup:** the 7 `seagate_*` tables onboarded into the `seagate` example schema;
[`bi_glossary.md`](dev_fixtures/seagate_manufacturing/bi_glossary.md) uploaded and
enriched through `POST /agent/semantic-layer/projects/{id}/documents/{id}/enrich`.

**Expected (Wren parity):** 7 enriched models carrying the glossary's knowledge —
column **synonyms** (patty, griddle, 86'd, …), business **metrics** (Golden Yield,
True Pass Rate with their exclusion rules), and the **join relationships** from the
"how tables join" section.

**Observed:** a single model named after the **schema**, the document's first 500
characters as its `description`, and no columns/metrics/relationships:

```json
{ "models": [ { "name": "seagate",
  "description": "# Seagate Manufacturing — BI Glossary & Join Guide This is the internal BI wiki page for the `seagate_*` tables loaded into the `examples` database. Floor staff and the ERP export use diner slang for",
  "properties": { "database_label": "examples", "catalog_name": null,
    "schema_name": "seagate", "source_document_id": "…", "source_document": "bi_glossary.md" } } ] }
```

That payload is byte-for-byte the **deterministic fallback**, not enrichment —
`deterministic_mdl_proposal` ([`integrations/wren/client.py`](integrations/wren/client.py),
~L409–444): one model named `_safe_mdl_name(project.schema_name)`, `description =
document.summary or text.strip()[:500]`, and the tell-tale
`properties.source_document_id`/`source_document`. No enrichment prompt emits that
shape — it is the structure-only degrade path.

### Causal chain (source-backed)

1. **Onboarding writes drafts, never active.** `onboard_schema_project`
   ([`semantic_layer/onboarding.py`](semantic_layer/onboarding.py)) —
   *"Files are always written as drafts; activation remains a human decision."*
2. **Enrichment sees active files only.** `_active_mdl_json`
   ([`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)) skips
   `status != "active"`; identical filter in `_active_files_content`. With the
   onboarding drafts unactivated, `current_mdl` (the reference handed to the model)
   is **empty**.
3. **The prompt is biased to no-op.** [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md)
   frames the task as *"improving an existing … MDL,"* tells the model *"you only need
   to emit the models you change,"* and grants an explicit escape hatch — *"If the
   document does not apply to any current model, return an empty `files` array."* With
   an empty base, the model returns `{files: []}`.
4. **Empty files → silent blob.** `_draft_with_correction` returns `None` on the
   first empty response; `propose_mdl_from_document` then returns
   `deterministic_mdl_proposal` + `_PROVIDER_FALLBACK_WARNING`.

**Net effect:** every piece of business knowledge in the glossary is silently
dropped, and the call returns HTTP 200 with a proposal that *looks* successful.

> **Note on grounding:** the field test's `physical_schema` was probably non-empty
> (`wren_schema_table_candidate_limit=12` ≥ 7 tables fits the 6000-token budget), so
> the blob is caused by the **draft/active seam + no-op-biased prompt**, not by
> missing physical schema. The grounding path is nonetheless a **latent correctness
> bug** for larger schemas (RC3).

## C.1 — Corrected parity assessment

| Stage | A.0 claim | Field-test reality | Root cause |
| --- | --- | --- | --- |
| **E0** Structure precondition (`onboard → activate → enrich`) | *(unrated)* | **Broken** — onboarding output is invisible to enrichment until a separate manual activation; no guard, no auto-include | RC1, RC2 |
| **E2** Context assembly | At parity | **Partial** — grounded by a relevance-ranked retrieval over a *placeholder question*, can silently drop tables; never sees draft structure | RC3, RC1 |
| **E3** Generation | At parity (capability) | **Degrades to blob** on an empty/sparse base; no model-from-catalog mode; no modeling exemplars | RC4 |
| **E5** Validate | At parity *when engine on* | Engine compilation still opt-in & off by default → structure is LLM-asserted, not engine-authoritative | RC6 |
| **Relationships / metrics from doc** | implied by E3/E4 | **Absent** — deterministic seed emits no relationships; no relationship-recommendation pass; doc-defined metrics/calculated fields have no guided target | RC5 |

## C.2 — Root causes (source-backed)

- **RC1 — Enrichment is structurally blind to draft models.** `_active_mdl_json` and
  `_active_files_content` ([`llm_client.py`](integrations/wren/llm_client.py)) both
  `continue` on `status != "active"`; onboarding only writes drafts
  ([`onboarding.py`](semantic_layer/onboarding.py)). The onboarded structure is
  unreachable by enrichment until manual activation. **Dominant cause.**
- **RC2 — Silent degrade, no precondition guard.** `propose_mdl_from_document`
  ([`llm_client.py`](integrations/wren/llm_client.py)) returns the deterministic blob
  with only a soft warning; the enrich route (`enrich_project_document`,
  [`app.py`](app.py)) never checks whether the project has any models to enrich. A
  no-op enrichment is indistinguishable from success.
- **RC3 — Grounding is question-driven, not full-scope.** `_schema_index_for_project`
  ([`app.py`](app.py)) grounds enrichment by calling the text-to-SQL context provider
  with a literal placeholder `question="semantic layer validation"`; `get_context`
  ([`context/superset_metadata.py`](context/superset_metadata.py)) routes through
  `retrieve_schema_context` ([`semantic_layer/retrieval.py`](semantic_layer/retrieval.py)),
  which ranks by token overlap and caps at `wren_schema_table_candidate_limit` (12) +
  `wren_schema_context_token_budget` (6000). Enrichment must see the **whole** scope;
  relevance pruning against a meaningless question can silently drop tables. Onboarding
  has the identical defect (`onboard_semantic_project`, [`app.py`](app.py), uses
  `question="semantic layer onboarding"`).
- **RC4 — The enrichment prompt is biased to under-produce.**
  [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md) is an "improve existing"
  prompt with an empty-files escape hatch and **no few-shot exemplars** (the doc's own
  E3 residual). It has no "model from the catalog when the base is sparse" mode.
- **RC5 — Document business knowledge has no first-class modeling target.** The
  deterministic seed `model_from_dataset`
  ([`integrations/wren/mdl_exporter.py`](integrations/wren/mdl_exporter.py)) emits **no
  `relationships`**, and there is **no relationship-recommendation pass** anywhere
  (Wren ships a dedicated one). Doc-defined metrics with filters (Golden Yield excludes
  `SHORT_ORDER`; True Pass Rate drops garnish-only failures), region rollups (not a
  column), Diner-Week calendar, and shift remaps need metrics / calculated fields /
  views; the schema *supports* them (`AuthoredRelationship`, `AuthoredMetric`,
  `AuthoredColumn.is_calculated`/`expression` in
  [`semantic_layer/mdl_authoring.py`](semantic_layer/mdl_authoring.py)) but the prompt
  and pipeline give no guidance to produce them. Onboarding never ingests the document
  at all (`generate_base_model` sees only datasets).
- **RC6 — Structure is not engine-authoritative by default.** Deep wren-core
  validation (`_deep_validation_enabled`, [`llm_client.py`](integrations/wren/llm_client.py))
  and the activation engine-gate are both opt-in and **off** by default
  (`wren_modeling_deep_validation`, `wren_activation_requires_engine`,
  [`config.py`](config.py)). Wren grounds structure in the engine; here it is
  LLM-asserted and only structurally/physically checked.

## C.3 — Remediation checklist (the implementation plan)

Each item: **goal · problem/evidence · change · hard requirements (MUST) · files ·
acceptance · status**. Severity: `P0` blocks the seagate test from ever producing
real output · `P1` required for parity · `P2` completes/hardens parity.

### CR1 — Enrich the *modeled* MDL, not the *active* MDL `P0` `[DONE]`
- **Goal:** enrichment grounds on the project's current model set regardless of
  activation status, mirroring Wren (where the modeled MDL is always the input).
- **Evidence:** RC1 — `_active_mdl_json`/`_active_files_content` filter `status !=
  "active"`; onboarding writes drafts only.
- **Change:** introduce an "effective project MDL" accessor (per path: active file if
  present, else latest draft) and use it for the enrichment `current_mdl` reference and
  for `_patch_target`'s merge target.
- **Hard requirements:**
  - MUST overlay enrichment onto onboarding drafts when no active file exists.
  - MUST NOT drop or retype any base column/type (preserve the E4 structure contract).
  - MUST keep "active wins over draft" when both exist for a path.
- **Files:** [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)
  (`_active_mdl_json` → `_project_mdl_json(include_drafts=True)`, `_active_files_content`,
  `_patch_target`).
- **Acceptance:** with onboarding drafts present and unactivated, enriching
  `bi_glossary.md` yields ≥7 models (not the blob); a unit test asserts draft structure
  is the enrichment base.

### CR2 — Hard precondition guard; never emit the silent blob `P0` `[DONE]`
- **Goal:** an enrichment that cannot run returns an actionable error, not a fake
  success.
- **Evidence:** RC2 — blob returned with only a soft warning; no model-count check.
- **Change:** in `enrich_project_document`, when the project has zero models (active or
  draft), return a structured 409 with remediation copy ("Run onboarding and
  review/activate the base models before enriching"). In `propose_mdl_from_document`,
  when the provider returns empty `files` **and** a non-empty `physical_schema` was
  supplied, treat it as a hard failure surfaced to the UI — do not fall back to the
  schema-name blob.
- **Hard requirements:**
  - MUST distinguish "no base to enrich" (409) from "provider returned nothing" (explicit
    error, not a blob).
  - MUST surface `_PROVIDER_FALLBACK_WARNING` cases as warnings the UI renders, never
    swallow them.
- **Files:** [`app.py`](app.py) (`enrich_project_document`),
  [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)
  (`propose_mdl_from_document`).
- **Acceptance:** enriching an un-onboarded project returns 409 with copy; RTL/import
  dialog shows the message; no test path returns the schema-name blob when
  `physical_schema` is present.

### CR3 — Full-scope schema grounding `P1` `[DONE]`
- **Goal:** enrichment and onboarding ground on the **complete** scope schema, never a
  relevance-ranked top-k against a placeholder question.
- **Evidence:** RC3 — `_schema_index_for_project`/`onboard_semantic_project` reuse the
  retrieval path with a fake question; `retrieve_schema_context` caps at 12 + 6000
  tokens.
- **Change:** add a non-ranked introspection entrypoint
  (`get_full_schema(scope)` / direct `list_datasets(limit=wren_schema_table_scan_limit)`
  bypassing `retrieve_schema_context`) and use it for the modeling/validation
  `SchemaIndex` in both flows.
- **Hard requirements:**
  - MUST NOT depend on a question string for modeling-time grounding.
  - MUST return every dataset in the scope up to `wren_schema_table_scan_limit`;
    degrade-closed to the snapshot on a Superset outage (preserve the C3 contract).
- **Files:** [`context/superset_metadata.py`](context/superset_metadata.py),
  [`app.py`](app.py) (`_schema_index_for_project`, `onboard_semantic_project`).
- **Acceptance:** a >12-table schema grounds enrichment on all tables; a test asserts
  the full set reaches `physical_schema`.

### CR4 — Semantics-overlay enrichment prompt + correct synonym home `P1` `[DONE — prompt; LLM-behavior not unit-tested]`
- **Goal:** enrichment overlays *semantics* onto introspected structure (Wren v1's
  `semantics_description` authority model), never authors structure, never no-ops while
  real tables exist, and places synonyms where Wren actually keeps them.
- **Evidence:** RC4 + [C.4](#c4--wren-parity-findings-resolved) findings #1, #2, #4.
  **Correction vs. the original plan:** Wren's `semantics_description` pipeline emits
  only per-model/per-column `description`/`displayName` and has *no* embedder/document
  store — structure comes from introspection (`MDLBuilder`), not the LLM. And **MDL has
  no synonyms array**: a column carries a single `properties.alias`/`displayName` plus
  free-text `description`; multi-term colloquialisms belong in **Instructions**.
- **Change:** (a) reframe [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md) as
  a *semantics-overlay* prompt: it may add `description`, `properties.displayName`,
  `properties.alias`, `relationships`, calculated fields (`isCalculated`+`expression`),
  and metric/measure `expression`s — it **must not** add/rename/retype physical models
  or columns; (b) suppress the empty-files escape hatch whenever `physical_schema` is
  non-empty *and* a base model set exists; (c) route **multi-synonym** terms (patty/DU/
  drive-can) into the **Instructions** store (CR9 makes them retrievable), not a fake
  MDL field; (d) add few-shot exemplars: single alias → `properties.alias`; a
  document-defined **filtered measure** (`SUM(CASE WHEN status != 'SHORT_ORDER' …)`); a
  **calculated field** (region rollup / Diner-Week bucket).
- **Hard requirements:**
  - MUST emit native camelCase shape; `type` required on every column (unchanged).
  - MUST NOT author physical structure (models/physical columns/types) — only the
    semantic + calculated/relationship/metric layer (binding per the parity decision).
  - Single label → `properties.alias`/`displayName`; **multiple synonyms → Instructions**
    (no `properties.synonyms` — Wren has no such field).
  - MUST preserve every base column/type (E4 structure-preservation contract).
- **Files:** [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md);
  [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py) (payload mode +
  routing multi-synonyms to the instruction store).
- **Acceptance:** the seagate doc produces column `alias`/`displayName`,
  `golden_yield`/`true_pass_rate` measures with the correct in-expression filters, and
  region/shift calculated fields; colloquial synonyms land as Instructions; a golden-file
  test pins the enriched manifest; no test emits a non-Wren `synonyms` field.

### CR5 — Relationship recommendation `P1` `[PARTIAL — validation+prompt done; FK-seed not feasible]`
- **Goal:** join relationships are produced to Wren parity — FK-seeded from introspection
  and LLM-recommended (as *proposals*) from the document's join guide.
- **Evidence:** RC5 — `model_from_dataset` emits no `relationships`; no recommendation
  pass exists. [C.4](#c4--wren-parity-findings-resolved) #3 confirms Wren's
  `relationship_recommendation` is an **LLM** pipeline reasoning over schema (PK/name/
  type), surfaced for human accept/reject before entering the MDL.
- **Change:** (a) pre-seed relationships from Superset FK/PK metadata in
  `model_from_dataset` during onboarding (introspection-authoritative); (b) add a
  relationship-recommendation step emitting `AuthoredRelationship` proposals from the
  glossary join guide; (c) fold them into `_full_proposed_manifest` so deep validation
  resolves them; (d) keep them in the review/activate gate (proposals, not auto-applied).
- **Hard requirements:**
  - Each relationship MUST have exactly **2** `models`, a `joinType` ∈
    {`ONE_TO_ONE`,`ONE_TO_MANY`,`MANY_TO_ONE`,`MANY_TO_MANY`}, and a `condition` join
    expression (Wren native shape, [C.4](#c4--wren-parity-findings-resolved) #3/#4).
  - MUST reference only models/columns present in the manifest; relationship-querying
    requires a `primaryKey` on the referenced model — seed it.
  - MUST pass `wren-core` compilation when deep validation is on (CR7).
- **Files:** [`integrations/wren/mdl_exporter.py`](integrations/wren/mdl_exporter.py),
  [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md) (or a new
  `prompts/relationship_recommendation.md`),
  [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)
  (`_full_proposed_manifest`).
- **Acceptance:** the 7-table seagate join graph appears as `relationships` with valid
  `joinType`/`condition`, survives activation, and a query needing a 3-table join path
  resolves without being told the path.

### CR6 — Document-derived metrics & calculated fields (expression-level) `P1` `[DONE — gate+prompt]`
- **Goal:** the document's metrics, calendar, regions, and shift remaps become
  activatable MDL constructs, not prose lost in a description.
- **Evidence:** RC5 + [C.4](#c4--wren-parity-findings-resolved) #4. Schema supports
  `AuthoredMetric` (`expression`) and `AuthoredColumn.is_calculated`/`expression`
  ([`semantic_layer/mdl_authoring.py`](semantic_layer/mdl_authoring.py)). **Correction:**
  Wren has **no separate measure-filter field** — a row-level exclusion lives **inside**
  the measure/calculated `expression` as `CASE WHEN`/`FILTER (WHERE …)`; a cumulative
  metric is a **window function in an expression**, not a first-class type.
- **Change:** prompt + validation support for: filtered measure expressions (Golden
  Yield / True Pass Rate exclusions encoded *in-expression*), calculated fields (region
  rollup, Diner-Week bucket, shift-hour remap via `isCalculated`+`expression`), and a
  "metric undefined for slice X" guard (the Golden-Yield short-order trap).
- **Hard requirements:**
  - Exclusion rules MUST be encoded **inside** the measure/calculated `expression`, never
    as an invented filter field and never left to the SQL prompt to remember.
  - Cumulative/period metrics MUST be expression-level (window functions), not new fields.
  - Generated expressions MUST compile under `wren-core` (CR7) — no free-text that fails
    planning.
  - **Engine-build gate — RESOLVED (compile check, 2026-06).** Against the installed
    `wren_core` wheel (`venv/lib/python3.11/site-packages/wren_core`), `SessionContext`
    construction **accepts** top-level `metrics[]`, calculated fields, views, and column
    `properties` (alias/displayName/description). Negative controls prove the gate is real
    — a bad `joinType` variant, a column missing `type`, and a calculated field
    referencing an unknown column all **fail**. **Crucial nuance:** a metric with an
    unknown `baseObject` **passes** — i.e. `metrics[]` are *deserialized but not
    deeply planned*, whereas **calculated fields *are* planned** (their expressions are
    analyzed). → **Decision:** **prefer calculated fields** (engine-planned, fully
    validated — including aggregate expressions over a `relationship`) for derived and
    rollup values; emit top-level `metrics[]` only when the metric construct is genuinely
    required, and treat metric correctness as guaranteed by our structural validator + the
    SQL-time path, **not** by engine compile. Golden Yield / True Pass Rate are therefore
    authored as **aggregate calculated fields with in-expression `CASE WHEN` exclusions**.
- **Files:** [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md),
  [`semantic_layer/mdl_validator.py`](semantic_layer/mdl_validator.py),
  [`semantic_layer/mdl_authoring.py`](semantic_layer/mdl_authoring.py) (views support if
  a rollup is better expressed as a view).
- **Acceptance:** asking for "Golden Yield for the Tigerline region" produces SQL that
  applies the `STANDARD`-only filter and the region rollup; the short-order Golden Yield
  trap is refused, matching `test_queries.md` ground truth; emitted constructs compile
  against the target engine build.

### CR7 — Engine-authoritative validation in the parity path `P2` `[DONE — enabled in .env profile]`
- **Goal:** structure and expressions are validated by wren-core, not just
  structurally/physically — Wren's authority model.
- **Evidence:** RC6 — `wren_modeling_deep_validation` and
  `wren_activation_requires_engine` default off ([`config.py`](config.py)).
- **Change:** make deep validation + the activation engine-gate the **default for the
  parity profile** (keep the zero-dependency dev default documented as a reduced mode),
  and make the relationship-complete reconstruction (B.4) a prerequisite so pre-existing
  relationships are validated too.
- **Hard requirements:**
  - A hallucinated column/relationship/metric expression MUST fail in the modeling loop,
    not at activation.
  - MUST degrade-closed when wren-core is unavailable (documented reduced mode).
- **Files:** [`config.py`](config.py), `.env.example`,
  [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)
  (`_deep_validate`, `_full_proposed_manifest`).
- **Acceptance:** the parity profile rejects an invalid manifest in-loop; tests cover
  engine-on and engine-absent.

### CR8 — Confirm/​harden structured output `P2` `[DONE — diagnostics]`
- **Goal:** eliminate the provider-structured-output ambiguity behind the fallback.
- **Evidence:** RC6 / `_call_model` returns `None` on any `chat`/parse failure; the
  fallback masks whether the cause was empty `files` or invalid JSON.
- **Change:** capture and log the raw model response on fallback; verify the configured
  provider/model honors `format_schema` (`proposal_response_schema()`); keep the
  json_schema→json_object→prompt deterministic fallback.
- **Hard requirements:**
  - The fallback warning MUST carry enough diagnostics to distinguish empty-files vs
    parse-failure.
- **Files:** [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)
  (`_call_model`).
- **Acceptance:** a forced parse failure and a forced empty-files response produce
  distinct, logged warnings.

### CR9 — Bake semantics into the retrieval chunk (re-index coupling) `P0` `[DONE]`
- **Goal:** enriched semantics actually change NL→SQL retrieval — the whole point of the
  seagate before/after test.
- **Evidence:** [C.4](#c4--wren-parity-findings-resolved) #5 + a confirmed local gap:
  [`manifest_to_schema_items`](semantic_layer/schema_retriever.py) builds each chunk as
  names+types only (`text=f"{model}.{col} {type}"`; the model chunk is just column
  names; the relationship chunk omits the `condition`). It **never** includes column
  `description`/`displayName`/`alias` or model description. Wren's `db_schema` chunk is a
  DDL string annotating each column with `-- {"description":…,"alias":…}` and each FK with
  `-- {"condition":…,"joinType":…}` — that annotated text is what gets embedded. **As
  built, an enriched alias is invisible to retrieval**, so even CR4 alone would not move
  the field test.
- **Change:** extend `manifest_to_schema_items` so model/column chunks carry
  `description`, `displayName`, `alias` (from `properties`) and relationship chunks carry
  `condition`; ensure activation re-embeds (E6 `reindex_project_mdl` already runs — verify
  it fires on the enriched manifest and that the checksum changes when only `properties`
  change).
- **Hard requirements:**
  - Chunk content MUST include the semantic fields a synonym/alias lives in, or the
    synonym cannot influence retrieval (mirrors Wren's annotated DDL chunk).
  - Re-index MUST trigger on a `properties`-only change (checksum must cover `properties`).
  - Degrade-closed: no embedder ⇒ keyword rank over the richer text (unchanged contract).
- **Files:** [`semantic_layer/schema_retriever.py`](semantic_layer/schema_retriever.py)
  (`manifest_to_schema_items`, checksum), [`semantic_layer/instructions.py`](semantic_layer/instructions.py)
  (multi-synonym recall from CR4).
- **Acceptance:** after enrich+activate, a query using a colloquial term ("patty",
  "griddle") retrieves the correct model/column; a unit test asserts the alias/description
  is present in the indexed chunk text and that a `properties`-only edit re-indexes.

**Sequencing:** **CR1 + CR2 + CR9 (P0)** are the minimum to make the seagate test
produce *and surface* real output — CR1/CR2 stop the silent blob and ground enrichment on
the onboarded structure; **CR9 is co-P0** because without it enriched semantics never
reach retrieval, so a "successful" enrichment still wouldn't change answers. CR3 fixes
grounding correctness. CR4 + CR5 + CR6 (P1) are the semantic-richness payload that closes
parity (synonyms→alias/Instructions, relationships, expression-level metrics). CR7 + CR8
(P2) harden the engine-authority + provider layers.

## C.4 — Wren parity findings (resolved)

A consultant pass (sourced against Wren's repos, 2026-06) resolved the open unknowns.
Findings below are **accepted as the binding field-shape contract** for CR4/CR5/CR6/CR9;
the executive accept/reject and rationale follow each. The original research prompt is
retained at the end for provenance.

1. **Doc→MDL ingestion.** **v1 has none** — structure is built by introspection
   (`MDLBuilder`) and business terms enter only via `properties`, **Instructions**, and
   **Question-SQL pairs**. **v2** ingests docs→MDL via agent skills. → **Decision:** keep
   our doc→MDL trigger (v2 intent) but mirror v1's field surface and authority model (see
   the [parity decision](#parity-target--executive-decision-there-are-two-wrens)).
2. **`semantics_description` I/O.** A generation pipeline with **no embedder/document
   store**; input is the MDL + selected models; output is **per-model/per-column
   `description` + `displayName` only** — not synonyms, metrics, relationships, or
   calculated fields. → **Decision:** our enrichment may also emit relationships/
   calculated/metric layers (we collapse several Wren pipelines into one call), but it
   **must not author physical structure**. Drives CR4.
3. **Relationship recommendation.** **LLM-driven**, reasons over schema (PK/name/type),
   emits `relationships[]` of `{name, models:[2], joinType, condition, properties}` with
   `joinType ∈ {ONE_TO_ONE, ONE_TO_MANY, MANY_TO_ONE, MANY_TO_MANY}`; cardinality is an
   LLM judgment, surfaced for human accept/reject. → **Accepted** as the CR5 contract.
4. **MDL field reference (native camelCase) — accepted verbatim into CR4/CR6:**
   - **Synonym/alias:** *no synonyms array exists.* Column carries
     `properties.displayName` + `properties.alias` (single) + free-text
     `properties.description`. Multi-term colloquialisms → **Instructions**. → **Accepted**
     (reverses the original "`properties.synonyms`" plan).
   - **Calculated field:** `isCalculated: true` + `expression` (+ `relationship` key when
     the expression crosses a relationship).
   - **Metric:** top-level `metrics[]` = `{baseObject, dimension[], measure[],
     timeGrain[]}`; **row-level exclusion lives inside the measure `expression`**
     (`CASE WHEN …`/`FILTER (WHERE …)`) — there is **no separate filter field**.
   - **Cumulative metric:** **not a field** — a window function inside an expression.
   - **View:** `{name, statement, properties:{question, summary, displayName}}`.
   - **Engine-build caveat — RESOLVED:** a compile check against our installed
     `wren_core` shows `metrics[]`, calculated fields, and views all deserialize, but
     **metrics are not deeply planned** (a bad `baseObject` passes) while **calculated
     fields are planned**. → **Decision:** author derived/rollup/filtered values as
     **aggregate calculated fields** (engine-validated); reserve `metrics[]` for cases
     that truly need it (structurally-validated only). See the CR6 engine-build gate.
5. **Retrieval coupling.** A synonym influences NL→SQL **only** once it is in the indexed
   chunk content (Wren bakes `-- {"description":…,"alias":…}` into the `db_schema` DDL
   chunk) **or** in an indexed Instruction; an MDL edit is **inert until re-embed**. →
   **Accepted** — this is the evidence behind the new **CR9** (our chunk is names+types
   only) and the multi-synonym→Instructions routing in CR4.
6. **Validation authority.** `wren-core` compilation is the source of truth; it catches
   unknown column/model, unresolved relationships, invalid expressions, type problems;
   the SQL path adds dry-plan + a correction loop. → **Accepted** — strengthens CR7
   (engine compile is the gate; never accept un-round-tripped LLM structure).
7. **Instructions.** Authored as reusable rules; `instructions_indexing` embeds them
   (global-always + question-matched); `instructions_retrieval` injects them into the SQL
   prompt. v1 has **no separate modeling-time instruction channel** — modeling-time
   guidance is structural. → **Accepted** — our existing Instructions subsystem (R3) is
   the parity-correct home for colloquial synonyms and operator rules.

**Net executive position:** every consultant finding is **accepted**; the one *rejection*
is of my own earlier plan language — the LLM must **not** "model from the catalog," and
there is **no MDL `synonyms` field**. Both are corrected above. Nothing here relaxes the
full-parity bar; it sharpens the field shapes we must hit.

---

**Provenance — paste-ready research prompt used for the consultant pass:**

> You are a senior data-platform engineer who knows **Wren AI** (the open-source text-
> to-SQL semantic layer: `wren-engine`/`wren-core` + the Wren AI Service Haystack
> pipelines over Qdrant) in depth. I am bringing a separate product's "business document
> → semantic layer (MDL)" enrichment feature to **full parity** with Wren and need
> precise, version-aware specifics (cite Wren MDL spec / pipeline names where possible):
>
> 1. **Modeling vs. enrichment.** Walk through how MDL structure is created in Wren
>    (modeling against a connected data source via wren-engine) versus how the
>    *semantics-description-generation* pipeline adds semantics. Is structure always
>    engine-authoritative and created **before** any LLM semantics step? Does Wren ever
>    ingest a free-form business document/glossary to author or enrich MDL, or is that
>    outside Wren's scope?
> 2. **Semantics-description-generation pipeline.** Exact inputs and outputs. Does it
>    emit only model/column descriptions, or also synonyms, metrics, and relationships?
>    What grounds it (retrieved schema? sample rows?), and what is re-indexed afterward?
> 3. **Relationship recommendation.** Describe Wren's relationship-recommendation
>    capability: inputs (schema, FK metadata, sampled data, docs?), whether it is LLM- or
>    heuristic-driven, the relationship **MDL shape** it emits (name, models, join type,
>    condition expression), and how cardinality is decided.
> 4. **MDL field reference for the constructs I must represent.** Give the exact native
>    (camelCase) MDL JSON for: (a) a **column synonym/alias** — does the MDL carry it,
>    under which key, or is it only a vector-index artifact? (b) a **calculated field /
>    calculated column** (derived expression over other columns); (c) a **metric** with an
>    aggregation **and a row-level exclusion filter** (e.g. "sum X over rows where status
>    != 'SHORT_ORDER'"); (d) a **cumulative/time-based metric** if supported; (e) a
>    **view**. Note any fields wren-core requires vs tolerates.
> 5. **Retrieval coupling.** How are synonyms / business terms actually used at query
>    time — are they embedded into the `db_schema` collection chunks, stored as
>    `instructions`, or kept in the MDL and surfaced via table/column selection? What is
>    the minimal thing I must persist so a user-coined synonym ("patty" → a drive unit)
>    influences NL→SQL retrieval?
> 6. **Validation authority.** Confirm that wren-core compilation is the authority for
>    structure/relationships/expressions, and what classes of error it catches at compile
>    time (unknown column, type mismatch, unresolved relationship, invalid expression).
> 7. **Instructions / operator guidance.** How does operator guidance enter Wren, how is
>    it indexed, and how is it injected into generation — for both modeling-time and
>    query-time?
>
> For each answer, give the concrete data shape and cite the Wren component/pipeline/MDL
> field names so I can mirror them exactly. Flag anything that changed across Wren
> versions.

The answers are folded into CR4/CR5/CR6/CR9 above; the `[C.4]` pointers now resolve to
confirmed field shapes, not open questions.

## C.5 — Parity definition of done

Enrichment is at parity only when **all** hold, verified end-to-end (not unit-only):

- [ ] `onboard → enrich` produces enriched per-table models **without** a manual
  activation in between (CR1), or returns an actionable 409 when there is genuinely
  nothing to enrich (CR2) — **never** the schema-name blob.
- [ ] Grounding covers the **entire** scope schema, independent of any question (CR3).
- [ ] The seagate glossary yields, to ground-truth: column **`alias`/`displayName`** (single
  labels) with colloquial **synonyms routed to Instructions**, the **join relationships**
  (valid `joinType`/`condition`), the **filtered measures** (Golden Yield, True Pass Rate,
  exclusions encoded *in-expression*), and the **calculated fields** (regions, Diner Week,
  shift remaps) — **no invented `synonyms` field** (CR4–CR6).
- [ ] Enriched semantics (alias/description) and relationship conditions are **baked into
  the retrieval chunk** and re-indexed on activation, so colloquial queries retrieve the
  right model/column (CR9).
- [ ] Structure (introspection-authoritative, **never LLM-authored**), relationships, and
  metric/calculated expressions are **engine-validated** in the parity profile (CR7).
- [ ] The before/after `test_queries.md` run flips wrong/refused baseline answers into
  ground-truth-correct answers — including the **Golden-Yield short-order trap** being
  refused — matching the
  [`dev_fixtures/seagate_manufacturing/README.md`](dev_fixtures/seagate_manufacturing/README.md)
  acceptance.
- [ ] Field shapes for aliases / relationships / metrics / calculated fields / views
  **match Wren's native camelCase surface** as resolved in
  [C.4](#c4--wren-parity-findings-resolved) — verified against the target `wren-core`
  build (metrics engine-build gate).

## C.6 — Implementation pass 1 (status, tests, remaining risks)

First implementation pass landed the structural fixes. Test baseline after the pass:
`pytest tests/unit_tests/superset_ai_agent` → **428 passed, 4 env-gated skips**
(was 419); `ruff check` clean on all changed files. The compile gate (CR6) was run
against the installed `wren_core` wheel (see CR6 "Engine-build gate — RESOLVED").

### What shipped

| CR | Status | Source of change | Tests |
| --- | --- | --- | --- |
| **CR1** draft-aware enrichment base | `[DONE]` | `_effective_files_content`/`_effective_mdl_json`/`_supersedes` (active-else-latest-draft) in [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py); `_patch_target` patches the effective file | `test_llm_wren_client.py::test_enrichment_grounds_on_draft_base_before_activation` / `::_prefers_active_over_draft_for_same_path` |
| **CR2** precondition guard, no silent blob | `[DONE]` | route 409 via `_project_has_models` ([`app.py`](app.py)); client `_no_change_proposal` replaces the schema-name blob when a base/schema exists | `test_semantic_layer_api.py::test_enrich_without_base_models_returns_409`; `test_llm_wren_client.py::test_no_change_proposal_when_provider_empty_and_base_exists` / `::test_bare_project_still_degrades_to_deterministic_draft` |
| **CR3** full-scope grounding | `[DONE]` | `get_full_schema` (non-ranked) in [`context/superset_metadata.py`](context/superset_metadata.py); enrich + onboard routes use it (getattr-fallback for other providers) | `test_context_provider.py::test_get_full_schema_returns_whole_scope_ignoring_ranking` |
| **CR9** semantics in the retrieval chunk | `[DONE]` | `_semantic_terms` + enriched `manifest_to_schema_items` (description/displayName/alias/synonyms + relationship `condition`) in [`schema_retriever.py`](semantic_layer/schema_retriever.py); checksum already covers `properties` (`_content_checksum` hashes full content) | `test_schema_retriever.py::test_chunk_text_carries_enriched_semantics_and_join_condition` |
| **CR4** semantics-overlay prompt | `[DONE]` (prompt) | rewrote [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md): structure-not-authored, alias/displayName placement, multi-synonym→Instructions guidance, calc-field/filtered-measure exemplars | LLM-behavior; not unit-testable (see risks) |
| **CR5** relationship recommendation | `[PARTIAL]` | structural validation already present (`_validate_relationships`: joinType enum + exactly-2 arity); prompt now drives the shape | `test_mdl_validator.py` (pre-existing) |
| **CR6** metrics/calculated fields | `[DONE]` (gate + prompt) | compile gate resolved → **calculated fields preferred** (engine-planned); prompt emits in-expression `CASE WHEN` filters | engine compile check (manual, recorded) |
| **CR7** engine-authoritative validation | `[DONE]` (config) | `.env`/`.env.example` already set `WREN_MODELING_DEEP_VALIDATION=true` + `WREN_ACTIVATION_REQUIRES_ENGINE=true`; code default stays off for zero-config dev | `test_*` deep-validation (pre-existing) |
| **CR8** structured-output diagnostics **+ fix** | `[DONE]` | `_call_model` logs transport-fail vs parse-fail; **and** the `openai` client now implements the documented `json_schema → json_object → prompt_only` fallback it was missing ([`llm/openai_client.py`](llm/openai_client.py); `openai_structured_output` config) | `test_model_clients.py::test_openai_client_falls_back_to_json_object_on_schema_rejection` / `::_does_not_retry_non_schema_errors` |

### Field-test finding (pass 1, post-`make up-ai`) — RESOLVED

The CR8 diagnostics immediately earned their keep. A live enrich on the seagate glossary
hit the `_no_change_proposal` path, and the new log line revealed the true cause —
**not** an empty-files no-op but a hard provider rejection:

> `MDL model call (wren_enrichment) raised: OpenAI request failed for model
> 'gpt-4.1-mini': 400 — Invalid schema for response_format 'structured_response':
> 'required' … Extra required key 'properties' supplied.`

**Root cause:** the plain `openai` provider client
([`llm/openai_client.py`](llm/openai_client.py)) sent the MDL schema in **strict**
structured-output mode (`strict: true`) with **no fallback** — unlike the
`openai_compatible`/`azure` clients, which already degrade through
`json_schema → json_object → prompt_only`. OpenAI strict mode forbids open-ended
objects, and our MDL `properties` field is a free-form `dict[str, Any]`, so the schema
is *fundamentally* strict-incompatible (strict mode would also forbid emitting
`alias`/`displayName` into `properties` even if it parsed). **Fix:** ported the
fallback chain + an `openai_structured_output` config to the openai client; a
schema-rejection 400/422 now degrades to `json_object` (schema carried in the prompt),
while non-schema errors (auth/5xx) still surface immediately. Default stays `json_schema`
because the **SQL-generation** schema *is* strict-compatible and benefits from
enforcement; only the MDL path pays a one-time degraded retry.

**Residual:** strict `json_schema` enforcement is unavailable for MDL generation on any
provider (the free-form `properties` precludes it). If stricter MDL enforcement is ever
wanted, `properties` would need a closed sub-schema enumerating known keys
(`displayName`/`alias`/`synonyms`/`description`) with `additionalProperties:false` —
a schema redesign, deferred.

### Contract change (pass 3): onboarding auto-activates; new Reset action

Two operator-facing changes landed after the field tests; they update premises stated
earlier in this doc:

- **Onboarding now auto-activates** every base model that passes structural + physical
  validation (`onboard_schema_project(auto_activate=True)`,
  [`semantic_layer/onboarding.py`](semantic_layer/onboarding.py)), and the onboarding job
  re-indexes afterward. This **supersedes** the earlier "onboarding writes drafts;
  activation is a human decision" premise (C.0 causal chain, CR1/CR2 narrative): a freshly
  onboarded or reset project lands on a populated, queryable layer. Invalid models stay
  draft + warning. CR1 (enrich off drafts) still holds — enrichment counts drafts *and*
  active. **Caveat:** auto-activation uses structural + physical validation (catches
  hallucinated columns) but **bypasses the optional deep wren-core compile gate**
  (`WREN_ACTIVATION_REQUIRES_ENGINE`) that the manual activation route enforces; safe for
  introspected base models (plain tables, no relationships).
- **New `POST …/projects/{id}/reset`** soft-deletes all MDL (base + enrichment + hand-edits),
  **keeps documents**, then re-onboards (auto-activated). It replaces the post-onboarding
  Onboard button (which was a no-op: `create` skips existing paths) with a confirm-gated
  **Reset** in the editor. Tests: `test_semantic_layer_api.py::test_reset_deletes_all_mdl_then_reonboards`,
  `test_onboard_auto_activates_models_deterministic_fallback`; RTL
  `SemanticLayerEditor/index.test.tsx::"Reset button confirms before deleting and re-onboarding"`.

### Field-test finding (pass 2, first live query) — partly RESOLVED

Query `How many patties got 86'd on 2025-10-30?` produced the **correct** SQL
(`SUM(units_scrapped) WHERE event_date='2025-10-30'`) — the enrichment + CR9 retrieval
worked: `units_scrapped` carries `"86'd" patties count` and the synonym reached the
chunk. But the trace showed a wren-core warning and two issues surfaced:

- **Duplicate models in the compiled manifest → wren-core "table … already exists"
  (FIXED).** Onboarding writes **7 per-table files**; enrichment writes **1 file
  containing all 7 models** (the overlay spans >1 owner file, so `_patch_target` falls
  back to a single merged file). The compile/materialize merge concatenated without
  dedupe → **14 models**, double-registering every physical table, so wren-core planning
  failed and the **dry-plan/semantic-rewrite path silently degraded** (single-table Q1
  still ran via direct execution, but relationship-join queries — L2/L4 — would lose the
  engine's join expansion). **Fix:** last-wins dedupe by name in `compile_manifest`
  (`dedupe_named_entities`) and the materializer sidecar; files compile in path-sorted
  order so the enrichment file (later path) wins. Verified against the live stored files:
  7 models, wren-core plans cleanly. Tests:
  `test_mdl_compile.py::test_compile_manifest_dedupes_same_named_models_last_wins` /
  `::_dedupes_relationships_and_metrics_by_name`.
  - **Remediation for the running instance:** the warning comes from the **query-time**
    rewrite (`plan_semantic_sql_step` → `engine.compile(active_files)` →
    `compile_manifest`), which reads active files fresh from the DB **every query** and is
    not cached — so just **rebuild (`make up-ai`) and re-run the query**; the dedupe runs
    inline. No deactivate/reactivate is needed (activation only triggers `reindex`, not
    the engine compile). Caveat: the persistent **retriever** index (lancedb) is keyed by
    unchanged file-content checksum, so its duplicate chunks linger (low-harm) until file
    content changes (e.g. a re-enrich) or `.data/wren_lancedb` is cleared.
- **Enriched `metrics[]` are empty shells (`measure`/`expression` absent) — partly
  fixed.** The model emitted `golden_yield`/`true_pass_rate` with a `baseObject` but no
  measure — the CR6 LLM-behavior risk, surfaced (not silent) by the
  `metric_without_measure` warning. While here, fixed a **validator key bug**: the check
  looked at `measures` (plural) but Wren-native is singular `measure`, so a correct
  metric would be false-warned. Test:
  `test_mdl_validator.py::test_metric_with_native_singular_measure_not_flagged`.
  **Still open (CR6 follow-up):** strengthen the prompt so filtered ratios are authored
  as **aggregate calculated fields** (engine-validated) rather than metric stubs, and/or
  drop empty metrics at apply time. Tracked under the CR4/CR6 LLM-behavior risk below.

### Remaining risks & dev-intent / UX mismatches

1. **CR4/CR6 quality is LLM-behavior-dependent and not unit-tested.** The prompt now
   *instructs* the correct field shapes, but whether a given provider actually emits
   aliases, in-expression filters, and calculated fields to ground truth cannot be
   asserted deterministically. **Required next step:** a live seagate run with the
   configured provider, scored against
   [`dev_fixtures/seagate_manufacturing/test_queries.md`](dev_fixtures/seagate_manufacturing/test_queries.md)
   (this is exactly the C.5 end-to-end gate, still unchecked).
2. **Multi-synonym → Instructions is guidance, not automation.** CR4 tells the model to
   *recommend* an operator Instruction for colloquial synonyms (in `warnings`); the
   agent does **not** auto-create Instruction records from an enrichment. A coined synonym
   becomes retrievable only via (a) a single `alias`/`description` baked into the chunk
   (CR9 — works now) or (b) an operator manually adding the Instruction. **Gap vs Wren:**
   Wren's flow is equally human-in-the-loop here, so this is parity-acceptable, but the
   UI should surface the recommended-instruction warning prominently (see #6).
3. **CR5 relationship recommendation has no FK seed.** Superset `DatasetMetadata`
   ([`integrations/superset/client.py`](integrations/superset/client.py)) carries **no
   foreign-key/primary-key** information, so relationships cannot be seeded from
   introspection — they come **only** from the document-enrichment LLM pass. For a
   document without an explicit join guide, no relationships are produced. **This is a
   data-source limitation, not a code defect**; closing it needs a Superset-side FK
   source or operator authoring.
4. **`metrics[]` are structurally—not semantically—validated by `wren-core`** (compile
   gate finding: a bad `baseObject` passes). We mitigate by preferring calculated fields,
   but if the model emits a top-level metric the engine will not catch a bad reference —
   only our structural validator + SQL-time path will. Treat metric correctness as
   not-engine-guaranteed.
5. **CR3 full-scope grounding is bounded by `wren_schema_table_scan_limit` (100) and
   `max_context_datasets` for the base fetch.** A schema wider than the scan limit still
   truncates — acceptable for the seagate case (7 tables) but a ceiling for very wide
   schemas. The placeholder-question relevance drop is fixed; the absolute cap remains.
6. **UI / user-expectation mismatches to verify (not covered by these backend changes):**
   - The **409 "no base models"** response must render as actionable copy in
     `SemanticLayerImportDialog.tsx` (guide the user to onboard first), not a generic
     error toast. **Unverified** — needs an RTL/manual check (this is plan item **B.3**).
   - The **`_no_change_proposal`** path returns a valid proposal with warnings and an
     unchanged manifest; the review dialog should make "no changes were applied" obvious
     rather than showing an apparently-successful empty diff. **Unverified.**
   - The **recommended-instruction** warnings from CR4 have no dedicated UI affordance;
     today they appear only in the generic `warnings` list.
7. **Onboarding still writes drafts and is a separate manual step.** CR1 makes enrichment
   work off drafts, but the operator must still run onboarding before enriching (the 409
   enforces this). The `dev_fixtures/seagate_manufacturing/README.md` walkthrough still
   documents the **deprecated overlay flow** (`/index/rebuild`), not
   `onboard → enrich → activate`; it should be rewritten (tracked under B.1).
8. **`metrics`/cubes long-term stability.** Per C.4, these are the least stable Wren
   constructs across the v1→v2 boundary; the calculated-field-first decision insulates us,
   but any future move to a newer `wren-core` must re-run the CR6 compile gate.

### Upload-dialog UX fixes (pass 3) — DONE

Four issues found after a successful live enrich, fixed in
[`SemanticLayerImportDialog.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/SemanticLayerImportDialog.tsx)
(+ one backend status-code change). Resolves risk-item #6's first/second bullets in part.

| # | Symptom | Root cause | Fix | Tests |
| --- | --- | --- | --- | --- |
| 1 | Repeat **Save**/**Activate** presses → `MDL file already exists` + empty editor | Per-item `persistItem` had no in-flight guard (only `persistAll` set `isBusy`); a 2nd click before the refreshed `existingFiles` prop arrived re-`create`d | Synchronous `savingIdsRef` re-entry guard; `'saving'` status + button `loading`/disable; **optimistic** `sessionFilesRef` (path→id) so a repeat save `update`s instead of re-`create`s | `re-saving the same item updates instead of creating a second file` |
| 2 | `closeModal` React DOM warning on dialog open | `CustomModal` injects `closeModal` into a **function-component** footer ([`Modal.tsx`](../superset-frontend/packages/superset-ui-core/src/components/Modal/Modal.tsx)); our footer was `<Flex>`, which forwards the prop to a `<div>` | Pass footer as an **array** of buttons (not a valid element → no injection) | `no activate controls and uses Save / Save all labels` (renders dialog; warning gone) |
| 3 | Activate non-functional in the upload UI | Activation belongs in the main editor (`toggleFileStatus`/`setAllStatuses`), not the dialog | Removed per-item **Activate** + footer **Activate all**; relabel **Save draft → Save**, **Save all as draft → Save all**; dialog is draft-only | same test |
| 4 | Adding a duplicate path hard-errors (400) | `create` raised plain `ValueError` → 400 | New `MdlFileExistsError` → **409** in `create_mdl_file`; FE `uniqueMdlPath` auto-suffixes a genuinely-new colliding JSON upload (`_1`, `_2`); re-enrichment still updates its own path | `test_create_mdl_file_path_conflict_returns_409`; `a new JSON upload that collides … is auto-suffixed` |

**Residual risks / mismatches (pass 3):**
- **The "empty editor" symptom was not reproduced in a unit test** — it is attributed to the
  double-submit + refresh churn and should be gone once repeat-submits are blocked (Issue 1).
  If it recurs, trace the `refresh()` selection-sync in
  [`SemanticLayerEditor/index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)
  (`activeFileIdRef`/`setEditorValue`) — **needs a live confirmation**.
- **Auto-suffix only fires for new JSON uploads, by design.** A *re-enrichment* whose
  `proposed_path` matches an existing file still **updates** it (the approved Issue-4 rule).
  If a user actually wants a *parallel* enriched draft, there is no UI to force that — they
  would delete/rename first. Acceptable per the approved decision; revisit if requested.
- **`sessionFilesRef` is per-open-session.** It clears on dialog close; a stale create→409
  is still possible only if the same path is created by another client between sessions —
  the 409 (not 400) now lets the UI surface it cleanly, but the dialog does not yet
  auto-recover from a 409 (it shows the error on the item). A follow-up could catch 409 and
  re-suffix automatically.
- **Backend 409 mapping is on `create` only.** The upload route and path-rename on `update`
  still surface `MdlFileExistsError` as 400 (it remains a `ValueError`). Intentional —
  only the create path needed the distinct status for the dialog.
