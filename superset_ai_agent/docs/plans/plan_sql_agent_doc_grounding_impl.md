# Impl checklist: raw BI-doc grounding + retrieval upgrade for the AI SQL agent

Status: Implemented (all phases A/B/C, 2026-07-07). Spec:
`plan_sql_agent_doc_grounding_spec.md`. Live eval v5 validation pending
(user-driven).
Sequential checklist; mark items `[COMPLETE]` with evidence (file:line, test run).

## Re-anchor findings (2026-07-07, live tree at ce8423786c)

- `manifest_to_schema_items` already carries descriptions/displayName/synonyms
  (CR9) and view chunks — the A2 scope narrows to **metrics**, which are chunked
  nowhere (`schema_retriever.py` chunks models/columns/relationships/views only;
  `fetch_context` in `integrations/wren/llm_client.py:101-153` surfaces
  models/relationships/views only). This is exactly eval v4's Q27 gap.
- Document corpus is **project-scoped** in the store
  (`SemanticLayerStore.list_project_documents/list_project_chunks`), so DP2
  resolves to "the resolved semantic project's corpus" (projects are DB-tied via
  fingerprint; multi-schema by design).
- Explain UI is the typed-detail seam (`explain.py` `_DETAIL_HANDLERS`,
  `schemas.py` `AgentStepDetail` union + `KNOWN_AGENT_STEP_KINDS`); unknown steps
  degrade to bare summaries, and a drift-guard test asserts emitted step names.
- `ConversationGraph` mirrors the SQL graph nodes (no function-calling loop), so
  C1 is doc-channel **parity**, not a new tool loop.
- eval harness: `context_dump` = glossary prepended via `extra_context` in
  `eval_common.AgentClient.ask`; configs expand in `run_eval_v4.expand_configs`.

## Phase A

- [COMPLETE] **A1 — doc-RAG channel in `TextToSqlGraph`.** Evidence:
  `retrieve_document_context` + `DocumentContextStore` in
  `semantic_layer/document_retriever.py`; node `load_document_context` in
  `graph.py` (edges load_wren_context→load_document_context→draft_sql; uses
  only the access-checked `wren_context.project_id`); prompt payload key
  `document_context` in `_call_sql_model` (draft/repair/correct);
  `LoadDocumentContextDetail`/`DocumentPassage` + step kind in `schemas.py`,
  handler in `explain.py`; config `wren_sql_doc_context_enabled/_retrieve_k/
  _max_chars` + env overrides; wiring in `app.py` (both builders); prompt
  section in `prompts/text_to_sql.md`; FE `api.ts` union + `AgentStepDetail.tsx`
  `document_context` case + `ExplainDialog.tsx` label; `.env.example` updated
  (⚠️ needs sync + image rebuild on Windows). Tests: 12 new in
  `test_doc_context.py`; 57 passed (doc_context+graph+explain+schemas+config),
  39 passed (app+seam+document_retriever); FE `jest AiAgentPanel/` 377 passed.
  - `document_retriever.retrieve_document_context(...)`: rank the resolved
    project's chunks for the question via `DocumentChunkIndex` (embedding, else
    keyword), budget-trim to `wren_sql_doc_context_max_chars`, attach filenames.
  - New graph node `load_document_context` (after `load_wren_context`), state
    field + prompt payload section `document_context` (advisory), trace event.
  - Explain: `LoadDocumentContextDetail` + handler + union entry +
    `KNOWN_AGENT_STEP_KINDS`; `WrenContextArtifact.document_ids` stamped.
  - Config: `wren_sql_doc_context_enabled` (default True; inert without chunks),
    `wren_sql_doc_context_max_chars` (16000), `wren_sql_doc_retrieve_k` (6) +
    env overrides + `.env example`/`.env.example` (flag ON) — needs sync+rebuild.
  - Wire `semantic_layer_store` + `document_index` into both graph builders in
    `app.py`.
- [COMPLETE] **A2 — surface metrics into retrieval.** Evidence: `_metric_items`
  in `semantic_layer/schema_retriever.py` (definition + synonyms in chunk text;
  `model=baseObject` so table-selection keeps metric with base); metrics
  section in `fetch_context` via generic `_rank_matching`
  (`integrations/wren/llm_client.py`). Tests: 2 new in
  `test_schema_retriever.py` (retrievable by name+synonym, minimal shapes),
  1 new in `test_llm_wren_client.py` (matching metric surfaces, unrelated
  doesn't). 107 passed, 2 skipped across retriever/llm_client/runtime/
  join_closure/mdl_compile.
- [COMPLETE] **A3 — trust-ladder prompt.** Evidence: `prompts/text_to_sql.md`
  restructured — "Grounding precedence" ladder (recalled → wren+instructions →
  document_context → datasets), explicit layer-wins conflict rule, new
  "Abstention" section (Q12 trap clawback: retrieved ≠ required, near-duplicate
  disambiguation), temporal rule (prefer structured date/calendar columns —
  Q22), metric-formula preference. Contract-guard tests: 5 new in
  `test_text_to_sql_prompt.py`; 48 passed with registry/graph/doc tests.
  ⚠️ Deployments with a DB prompt-registry `production` override of
  `text_to_sql` keep serving the old prompt until the override is updated.
- [COMPLETE] **A4 — eval v5 config.** Evidence: `run_eval_v5.py` (10-config
  matrix = v4's 8 + `wren_bi_rag·{manual,auto}`; per-question served-mode
  verification via the `load_document_context` trace step with loud
  `channel_mismatches`; SHIP-GATE delta `wren_bi_rag·auto −
  wren_bi_context·auto`); `eval_common.EXPERIMENTS` + docstring extended;
  `run_eval_v4.build_scoreboard` parameterized (`config_names`,
  `extra_deltas`, back-compat default). Tests: `test_eval_v5.py` 6 new;
  46 passed across eval v2/v4/v5 offline suites. Live 3-trial run is
  user-driven (needs the stack; WREN_SQL_DOC_CONTEXT_ENABLED=true and
  =false runs to fill both wren_bi and wren_bi_rag rows cleanly).

## Phase B

- [COMPLETE] **B1 — hybrid RRF retrieval.** Evidence: `HybridRetriever` +
  `rrf_fuse` in `schema_retriever.py` (`wren_retriever="hybrid"`, RRF k=60,
  2x over-fetch, degrades to keyword without vectors / to the persistent inner
  on a cold worker; `effective_vector_index` unwraps hybrid);
  `DocumentChunkIndex` `hybrid=` RRF fusion + `wren_document_hybrid_retrieval`
  config (default True) wired in `create_document_index`. `.env.example`:
  `WREN_RETRIEVER=hybrid`, `WREN_DOCUMENT_HYBRID_RETRIEVAL=true`. Tests: 5 new
  retriever + 2 new doc-index; 97 passed across the retrieval suites.
- [COMPLETE] **B2 — contextual chunk prefixes.** Evidence:
  `_index_document_chunks(contextual_prefix=...)` in
  `semantic_layer/documents.py` — vectors computed over `[filename] text`,
  persisted rows unchanged; `reindex_document` gains `config` (app.py route
  passes it); `wren_document_contextual_prefix` (default True) + env +
  `.env.example`. Tests: 2 new in `test_document_indexing.py`; 46 passed.
- [COMPLETE] **B3 — optional rerank stage.** Evidence: `llm/rerank.py`
  (`llm_rerank`, validated indices, degrade-closed) + `prompts/rerank.md`
  seed; `Reranker` seam + 2x over-fetch in `retrieve_document_context`
  (`reranked` stamped in details); `TextToSqlGraph._doc_reranker` gated by
  `wren_rerank_enabled` (default False; `.env.example` deliberately false —
  latency, documented deviation from flags-on-in-example rule). Tests: 8 new
  in `test_rerank.py`; 46 passed with graph/doc-context.
- [COMPLETE] **B4 — size-gating.** Evidence: `manifest_char_size` + dump gate
  in `runtime.build_unified_context` (`retrieval_mode="dump"`, no selection/
  count cap, fetch_context bundles ride along); graph passes
  `wren_context_dump_char_threshold` (default 40_000 chars ≈ 10k tokens; 0
  disables) + env + `.env.example`. Tests: 3 new in
  `test_semantic_layer_runtime.py`; full backend suite 1414 passed
  (1 pre-existing failure `test_bulk_activate_fetches_live_schema_once...`
  confirmed failing on clean HEAD in a worktree — not from this work).

## Phase C

- [COMPLETE] **C1 — ConversationGraph parity.** Evidence:
  `load_document_context` node + `_doc_reranker` in `conversation_graph.py`
  (edges load_wren_context→load_document_context→draft_response; access-checked
  project only), `document_context` in the conversation model payload,
  `prompts/conversation.md` doc section, app.py wiring (both builders; the
  service builder also gained the missing `instruction_store` for D1b parity).
  Tests: 3 new in `test_conversation_graph.py`; 25 passed.
- [COMPLETE] **C2 — dimension-value probe (gated).** Evidence:
  `semantic_layer/dimension_values.py` (quoted-literal extraction, string-column
  ranking, bounded governed probes, module-level 15-min TTL cache, SQL-escaped
  LIKE + LIMIT 5); graph node `probe_dimension_values` (inert without flag or
  literals), payload key `dimension_values`, prompt section (use stored values
  verbatim), `DimensionValuesDetail` + explain handler + FE type/label/case;
  `wren_dimension_value_probe_enabled=false` (+max_queries=3) + env +
  `.env.example`. Tests: 8 new in `test_dimension_values.py` (incl. graph
  gating + timeline); 46 passed with graph/explain/schemas.
- [COMPLETE] **C3 — dual-candidate generation (gated).** Evidence:
  `llm_select_candidate` + `_select_candidate` in `graph.py` (validity gate
  first — an invalid candidate never wins, no judge call needed; LLM pairwise
  judge on both-valid, semantic wins ties/failures), gated by
  `wren_dual_candidate_enabled=false`; `prompts/candidate_selection.md`;
  trace step `select_sql_candidate` + `CandidateSelectionDetail` + FE
  type/label/case; env + `.env.example`. Tests: 5 new in
  `test_candidate_selection.py`; 41 passed with graph/explain.
- [COMPLETE] **C4 — consistency linter.** Evidence:
  `semantic_layer/consistency.py` (`lint_project_consistency`:
  golden_unknown_reference, golden_conflicting_duplicates,
  duplicate_metric_conflict, instruction_unknown_identifier,
  golden_file_unparseable; all deterministic, degrade-closed); route
  `GET /agent/semantic-layer/projects/{id}/consistency` in `app.py`
  (authorize read, active files + project instructions). Tests: 6 unit in
  `test_consistency.py` + 1 API test in `test_semantic_layer_api.py`.

## Final

- [COMPLETE] Full backend suite: **1551 passed, 13 skipped** (1 deselected:
  `test_bulk_activate_fetches_live_schema_once_and_deactivate_zero` —
  pre-existing failure, confirmed failing on clean HEAD in a worktree).
  FE: `jest AiAgentPanel/` **377 passed / 39 suites**; `tsc --noEmit` clean on
  all touched files (1 pre-existing error in untouched
  `AiAgentPrompts.test.tsx`). Ruff: all touched files clean (check + format).
  ⚠️ `ruff format` also reformatted ~55 previously-unformatted, UNTOUCHED
  `.py` files (pure formatting churn; the file list is reproducible with
  `git diff --name-only` minus this plan's touched set) — commit separately or
  discard at the user's discretion. `.env.example` updated (9 new vars) —
  **needs sync + image rebuild on the Windows box**. Live eval v5 run is
  user-driven.
