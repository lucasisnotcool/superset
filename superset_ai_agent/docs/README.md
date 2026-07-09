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

# superset_ai_agent — documentation index

> Curated map of every design doc, feature spec, implementation plan, audit,
> and reference in this package. One line per doc, grouped by area, with the
> doc's lifecycle status. Read this file first; do not grep 60 files.

Living orientation docs stay at the package root: [README.md](../README.md)
(setup, Windows), [MACOS.md](../MACOS.md) (macOS setup),
[ARCHITECTURE.md](../ARCHITECTURE.md) (file-by-file architecture map).

## Conventions

- **Layout.** `docs/plans/` — feature specs and implementation plans (paired
  `*_spec.md` + `*_impl.md` where both exist). `docs/reference/` — as-built
  references and audit snapshots describing what exists. `docs/archive/` —
  finished one-off agent briefs, kept as historical record (their internal
  paths may predate this layout). `../evaluation/` — eval specs, results, and
  the runner code (docs stay beside the code that consumes them).
- **Filenames are stable identifiers.** Code comments, test docstrings, and
  session memory reference these docs by exact filename (e.g.
  `wren_full.md Phase 1.3`, `plan_copilot_parity_impl.md §6`). **Never rename
  an existing doc.** New plan docs follow the established
  `plan_<topic>_spec.md` / `plan_<topic>_impl.md` pattern for grep-ability
  with the existing corpus (a deliberate deviation from kebab-case norms).
- **Status header.** Every doc carries a `Status:` line near the top
  (Proposed / Draft / In progress / Implemented / Shipped / Superseded) with a
  date. **Update it when the work ships** — several headers went stale and
  were corrected on 2026-07-03 against code evidence; the doc body's as-built
  sections and the tests are the ground truth when in doubt.
- **Shipped docs are historical records.** Append as-built notes and
  corrections; don't rewrite history.
- **Add a line here** in the same change that adds a new doc.
- **Runtime Markdown is not documentation.** `../prompts/*.md` and
  `../skills/*.md` are loaded by code at runtime; `../dev_fixtures/**/*.md`
  and `../evaluation/` fixtures are read by eval runners. Never move them.

## Foundations — Wren integration design docs

The founding design corpus; code comments cite these by name and section.

- [wren.md](plans/wren.md): original Wren-style conversational-analytics
  master plan (Historical — earliest mega-plan; superseded in detail by the
  docs below).
- [wren_model.md](plans/wren_model.md): Wren model integration checklist
  (Complete, incl. R11–R15 hardening).
- [wren_full.md](plans/wren_full.md): native-manifest rebuild plan — semantic
  engine seams, persistence baseline, retrieval (Implemented; heavily cited
  from code).
- [wren_enrich_and_retrieve.md](plans/wren_enrich_and_retrieve.md):
  enrichment & retrieval study/plan — chunking, table-selection prune,
  instructions (Implemented; config cites C1–C4/R2/R3/E3/F0.1).
- [wren_graph_view.md](plans/wren_graph_view.md): combined database + MDL
  graph visualization plan (see doc for per-item status).
- [wren_mdl_copilot.md](plans/wren_mdl_copilot.md): MDL Copilot — agentic
  CRUD editor over MDL projects — plan and as-built log (Shipped; §AB is the
  as-built reference).
- [plan_metric_semantic_translation_impl.md](plans/plan_metric_semantic_translation_impl.md):
  fix for MDL `metrics` throwing Oracle ORA-00904 — wren_core 0.7.1 drops the
  `metrics` key, so metric names reach the DB unresolved; Layers 1–3 = inline
  metric expressions + don't forward engine-rejected SQL + correct prompt guidance.

## MDL Lab & semantic projects

- [plan_mdl_lab_spec.md](plans/plan_mdl_lab_spec.md): MDL Lab — first-class
  semantic projects, Lab surface (Implemented P1–P5; as-built in
  [MDL_LAB.md](reference/MDL_LAB.md)).
- [MDL_LAB_GAP_CLOSURE.md](plans/MDL_LAB_GAP_CLOSURE.md): MDL Lab gap
  analysis & follow-up closure plan (Implemented FP1–FP5).
- [MDL_LAB_UI_REDESIGN.md](plans/MDL_LAB_UI_REDESIGN.md): MDL Lab UI
  reorganisation audit & redesign (Implemented UP1–UP5).
- [plan_multi_schema_mdl_spec.md](plans/plan_multi_schema_mdl_spec.md):
  multi-schema MDL semantic projects (Implemented Phases 1–3b; header still
  says Draft).
- [plan_relationships_only_activation_fix.md](plans/plan_relationships_only_activation_fix.md):
  let relationships-only MDL files activate (Implemented Phases 1–5).
- [plan_ai_sql_project_selection_spec.md](plans/plan_ai_sql_project_selection_spec.md):
  AI SQL agent MDL-project picker + per-conversation pin (Shipped Tier 1+2).
- [plan_conversation_management_spec.md](plans/plan_conversation_management_spec.md)
  / [plan_conversation_management_impl.md](plans/plan_conversation_management_impl.md):
  edit & resend, regenerate, fork conversations for both agents — competitive
  analysis + soft-rewrite/fork design with side-effect manifest, apply
  before-image revert, attempt pager, persisted feedback (Shipped 2026-07-09;
  residual gaps in the impl doc's Final report).

## MDL Copilot authoring

- [plan_copilot_parity_spec.md](plans/plan_copilot_parity_spec.md) /
  [plan_copilot_parity_impl.md](plans/plan_copilot_parity_impl.md): Copilot ↔
  AI SQL parity — persistence + multi-turn (Implemented; as-built in
  wren_mdl_copilot.md §AB).
- [plan_mdl_copilot_patch_tools_impl.md](plans/plan_mdl_copilot_patch_tools_impl.md):
  sparse patch-write + read-truncation fix (Shipped Phases 0–5, 7; Phase 6
  deferred).
- [plan_mdl_copilot_followups_impl.md](plans/plan_mdl_copilot_followups_impl.md):
  token telemetry, targeted read, removal, input levers (Item D shipped;
  C/E/B2 deferred).
- [plan_copilot_enrichment_assertiveness.md](plans/plan_copilot_enrichment_assertiveness.md):
  make enrichment propose relationships + metrics, not just descriptions
  (Proposed).
- [plan_enrichment_relationship_model_fix.md](plans/plan_enrichment_relationship_model_fix.md):
  fix relationship-as-model enrichment failures + accept-button UX (Proposal).
- Views: [plan_views_parity_spec.md](plans/plan_views_parity_spec.md) (spec) ·
  [plan_views_parity_impl.md](plans/plan_views_parity_impl.md) (Phases 0–1
  complete — semantic views shipped) ·
  [plan_views_surfacing_impl.md](plans/plan_views_surfacing_impl.md)
  (query-time surfacing; Phases A+B implemented) ·
  [plan_views_explain_ui_spec.md](plans/plan_views_explain_ui_spec.md)
  (Explain UI view provenance; Implemented).
- Cubes: [plan_cubes_parity_spec.md](plans/plan_cubes_parity_spec.md) /
  [plan_cubes_parity_impl.md](plans/plan_cubes_parity_impl.md): Wren-parity
  cube authoring, Track A/B split (Build pending).

## Onboarding

- [plan_copilot_onboarding_spec.md](plans/plan_copilot_onboarding_spec.md) /
  [plan_copilot_onboarding_impl.md](plans/plan_copilot_onboarding_impl.md):
  Copilot-driven onboarding replacing the manual flow (Phases 1–5 built;
  Phase 6 security deferred).
- [plan_onboarding_gating_user_flow.md](plans/plan_onboarding_gating_user_flow.md):
  onboarding UI gating & user flow (Implemented).
- [plan_onboarding_dataset_registration_ux.md](plans/plan_onboarding_dataset_registration_ux.md):
  close the tables-vs-datasets expectation gap in the picker (Implemented).
- [plan_live_schema_introspection_spec.md](plans/plan_live_schema_introspection_spec.md):
  source the physical catalog from live DB introspection (Superset `/tables/` +
  `/table_metadata/`) when no datasets are registered, so BYO-connection projects
  can onboard with an empty `tables` catalog (Implemented, default-on).
- [plan_onboarding_picker_hardening.md](plans/plan_onboarding_picker_hardening.md):
  picker hardening — R1/R2/virtualization/R3 (Implemented).
- [plan_onboarding_seed_robustness_spec.md](plans/plan_onboarding_seed_robustness_spec.md):
  seed robustness — column identity + types (Implemented I1–I6).
- [plan_onboarding_selection_and_provenance_spec.md](plans/plan_onboarding_selection_and_provenance_spec.md)
  / [plan_onboarding_selection_and_provenance_impl.md](plans/plan_onboarding_selection_and_provenance_impl.md):
  selective-table onboarding & MDL provenance dialog (Implemented).
- [plan_onboarding_background_task_spec.md](plans/plan_onboarding_background_task_spec.md):
  onboarding as a first-class background task — durability, concurrency,
  notification (Proposed, not built).

## Provenance & coverage

- [plan_provenance_and_coverage_spec.md](plans/plan_provenance_and_coverage_spec.md)
  / [plan_provenance_and_coverage_impl.md](plans/plan_provenance_and_coverage_impl.md)
  / [plan_provenance_and_coverage_followup_impl.md](plans/plan_provenance_and_coverage_followup_impl.md):
  MDL provenance completeness & background coverage, plus Phase-3 gap closure
  (Implemented; as-built in
  [MDL_PROVENANCE_AND_COVERAGE.md](reference/MDL_PROVENANCE_AND_COVERAGE.md)).
- [plan_tool_call_provenance_spec.md](plans/plan_tool_call_provenance_spec.md):
  tool-call-level MDL provenance (Implemented P1–P3; §12 as-built).
- [plan_coverage_labels_and_progress_spec.md](plans/plan_coverage_labels_and_progress_spec.md):
  coverage as a decoupled version-label + live progress (Shipped, all 3
  phases).
- [plan_coverage_recovery_agent_spec.md](plans/plan_coverage_recovery_agent_spec.md):
  auto-run recovery agent proposing changesets to close coverage gaps
  (Shipped, all 3 phases + follow-ups; flag-gated off by default).

## AI SQL query agent

- [ai_agent_explain_and_audit.md](plans/ai_agent_explain_and_audit.md):
  Explain & Audit UI — agent timeline contract (Implemented; cited from
  `explain.py` / `schemas.py`).
- [plan_semantic_mode_badge_spec.md](plans/plan_semantic_mode_badge_spec.md) /
  [plan_semantic_mode_badge_impl.md](plans/plan_semantic_mode_badge_impl.md):
  semantic-mode badge for the AI SQL agent (Implemented Phases 1–6).
- [plan_cross_schema_context_ranking_impl.md](plans/plan_cross_schema_context_ranking_impl.md):
  cross-schema context — join-closure + unbiased ranking (Shipped).
- [plan_cross_schema_query_time_impl.md](plans/plan_cross_schema_query_time_impl.md):
  cross-schema query-time context, Fix C (Proposed; blocked — the
  `resolve_effective_schema` foundation is absent on this clone).
- [golden_queries_and_shared_memory_spec.md](plans/golden_queries_and_shared_memory_spec.md)
  / [plan_golden_queries_impl.md](plans/plan_golden_queries_impl.md): shared
  NL→SQL memory, access-aware recall, project-scoped golden queries
  (Implemented).
- [plan_recall_access_scope_fix_impl.md](plans/plan_recall_access_scope_fix_impl.md):
  fix R1 — mis-scoped access set dropped cross-schema recall (Implemented).
- [plan_recalled_provenance_impl.md](plans/plan_recalled_provenance_impl.md):
  Explain UI — recalled-query provenance markers (Implemented).
- [plan_oracle_semantic_transpile_impl.md](plans/plan_oracle_semantic_transpile_impl.md):
  Oracle semantic mode via sqlglot dialect finalization (Implemented, all 6
  phases).
- [plan_sql_agent_doc_grounding_spec.md](plans/plan_sql_agent_doc_grounding_spec.md)
  / [plan_sql_agent_doc_grounding_impl.md](plans/plan_sql_agent_doc_grounding_impl.md):
  raw BI-doc RAG channel in the SQL agent, metric surfacing, trust-ladder
  prompt, hybrid retrieval, dual-candidate (In progress; grounded in eval v4).

## Documents & RAG

- [uploaded_documents_rag_and_crud.md](plans/uploaded_documents_rag_and_crud.md):
  uploaded documents — RAG, viewer & agentic CRUD (Implemented Phases 0–4).
- [document_format_support_study.md](plans/document_format_support_study.md):
  format-support feasibility study (Study; Tier 1 implemented).
- [document_format_tier1_plan.md](plans/document_format_tier1_plan.md): Tier 1
  format expansion — xlsx/pptx/CSV→Markdown (Implemented, all 11 steps; Part F
  as-built).
- [plan_attach_dialog_impl.md](plans/plan_attach_dialog_impl.md): Copilot
  attach dialog — pick existing docs + drag-drop upload (Implemented).
- [plan_document_upload_ux_gaps.md](plans/plan_document_upload_ux_gaps.md):
  close the 4 document-upload UX gaps (Proposal).
- [plan_document_upload_residual_gaps.md](plans/plan_document_upload_residual_gaps.md):
  residual document-upload gaps post Phase 1–3 (Proposal).
- [plan_unified_attach_ingestion_spec.md](plans/plan_unified_attach_ingestion_spec.md):
  attach & upload share one ingestion pipeline (Proposal).
- [plan_attach_grounding_ux_followups.md](plans/plan_attach_grounding_ux_followups.md):
  attach grounding & status UX follow-ups (Proposal).
- [plan_attach_poll_consolidation_followups.md](plans/plan_attach_poll_consolidation_followups.md):
  poll consolidation, pending-only fetch, MDL sniff (Proposal).
- [plan_attach_tree_gate_json_followups.md](plans/plan_attach_tree_gate_json_followups.md):
  live tree, give-up cue, precise JSON notice (Proposal).

## Platform, security & operations

- [plan_postgres_only_persistence_spec.md](plans/plan_postgres_only_persistence_spec.md)
  / [plan_postgres_only_persistence_impl.md](plans/plan_postgres_only_persistence_impl.md):
  all state in one external Postgres — pgvector, blob storage, pg-only mode
  (Shipped as the DEFAULT topology; `migrate_to_postgres` script carries
  legacy SQLite/local-document data over).
- [plan_self_service_connections_spec.md](plans/plan_self_service_connections_spec.md)
  / [plan_self_service_connections_impl.md](plans/plan_self_service_connections_impl.md):
  BYO-credential connections + Builder role, DB-fingerprint authz (Built
  WS1–WS3; Phase 4 hardening open; as-built §10 of the impl).
- [plan_db_access_grants_impl.md](plans/plan_db_access_grants_impl.md): admin
  pre-approved per-DB access grants (Shipped P0/S1–S9).
- [plan_llm_call_logging_impl.md](plans/plan_llm_call_logging_impl.md): count
  + time every chat LLM call, admin menu surface (Shipped, all 6 phases).
- [plan_dataset_endpoint_perf_impl.md](plans/plan_dataset_endpoint_perf_impl.md):
  Superset-core dataset endpoint performance — eager-load + ETag, picker
  storm (Implemented Tracks 0/A/B/C; scope is Superset core, filed here with
  the fork's planning corpus).
- [plan_testing_platform_impl.md](plans/plan_testing_platform_impl.md):
  testing & evaluation platform — benchmarks, run job, prompt registry, judge,
  Scientist (**Active** — P0+P1 shipped, P2/P3 in progress; spec at
  [../evaluation/TESTING_PLATFORM_SPEC.md](../evaluation/TESTING_PLATFORM_SPEC.md)).

## Reference (as-built & audits)

- [MDL_LAB.md](reference/MDL_LAB.md): MDL Lab as-built reference — what
  exists, symbols, tests.
- [MDL_PROVENANCE_AND_COVERAGE.md](reference/MDL_PROVENANCE_AND_COVERAGE.md):
  provenance & background coverage as-built reference.
- [cross_schema_tooling_audit.md](reference/cross_schema_tooling_audit.md):
  per-tool/per-node/per-prompt cross-schema audit of both agents (snapshot
  2026-06-30).
- [wren_upstream_skills/](reference/wren_upstream_skills/): captured upstream
  WrenAI skill files, kept verbatim for parity comparison (see its README for
  provenance).

## Evaluation (`../evaluation/`)

Docs live beside the eval runner code that consumes them — do not move.

- [TESTING_PLATFORM_SPEC.md](../evaluation/TESTING_PLATFORM_SPEC.md): testing
  & evaluation platform spec, Revision 2 (DP-1..DP-21 accepted; Active).
- [EVAL_V2_SPEC.md](../evaluation/EVAL_V2_SPEC.md) ·
  [EVAL_V4_SPEC.md](../evaluation/EVAL_V4_SPEC.md): eval redesign specs
  (multi-schema/distractor; onboard×grounding benchmark).
- [RESULTS.md](../evaluation/RESULTS.md) ·
  [RESULTS_v2.md](../evaluation/RESULTS_v2.md) ·
  [RESULTS_v3.md](../evaluation/RESULTS_v3.md) ·
  [RESULTS_v4.md](../evaluation/RESULTS_v4.md): eval run results, v1–v4.
- [README.md](../evaluation/README.md) ·
  [CONSULTANT_BRIEF.md](../evaluation/CONSULTANT_BRIEF.md): harness overview
  and external-reviewer brief.

## Archive

One-off agent task briefs, finished; internal paths may predate this layout.

- [codebase_prompt_for_agents_skill_maintenance.md](archive/codebase_prompt_for_agents_skill_maintenance.md)
  and responses in
  [codebase_response_for_agents_skill_maintenance/](archive/codebase_response_for_agents_skill_maintenance/):
  skill-maintenance brief + per-skill audit responses.
- [codebase_prompt_for_agent_query_maintenance.md](archive/codebase_prompt_for_agent_query_maintenance.md):
  query-agent prompt-maintenance brief.
- [codebase_prompt_for_agent_mdl_prompt_integration.md](archive/codebase_prompt_for_agent_mdl_prompt_integration.md):
  MDL prompt-integration brief.
