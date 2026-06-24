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
