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

# Wren Model Integration — Implementation Checklist

This document is the **execution checklist** for delivering a fully functional
Wren-style semantic-layer modeling integration in `superset_ai_agent`. It is the
build companion to the design/audit doc [`wren.md`](wren.md); where the two
conflict, the governance invariants in `wren.md` win.

## Implementation Status — 2026-06-22

All five workstreams (A–E) plus the hardening risks **R1, R3, R8** and the
follow-up hardening **R11–R15** are **`[COMPLETE]`**, source-backed and
test-verified. Verification run on 2026-06-22:

- Backend: `pytest tests/unit_tests/superset_ai_agent/` → **183 passed,
  1 skipped** (the skipped test is the `wren-core`-installed deep-validation
  path, exercised only when the optional engine is present).
- Frontend: `jest` for `SemanticLayerEditor` + `api` → **13 passed**.
- Lint: `ruff check` clean on all new/changed agent files (json imports carry
  the established `# noqa: TID251` standalone-agent convention).
- DB: Alembic migration **`0002_schema_snapshots_and_jobs`** adds the schema
  snapshot and async job tables.

**R1/R3/R8 hardening (2026-06-22):** real schema-aware MDL validation
(`mdl_schema.py`, `mdl_validator.py`) with a code-enforced activation gate
(`mdl_files.py`, `app.py::_enforce_activation`); physical "no hallucinated
columns" enforcement via `SchemaIndex`; async onboarding jobs (`jobs.py`) with
progress events and a `proposal-review` / `onboarding-warnings` UI. See the
[Remaining Risks & Gaps](#remaining-risks--gaps) section for the per-risk
resolution detail and the new residual risks R11–R15.

| Workstream | Status | Key source | Tests |
| --- | --- | --- | --- |
| A. LLM modeling brain | `[COMPLETE]` | `integrations/wren/llm_client.py::LlmWrenClient`; `config.py` (`wren_adapter="llm"` default + `WrenAdapterMode`); `integrations/wren/factory.py`; `integrations/wren/client.py::generate_base_model`/`deterministic_base_model_proposals`; `integrations/wren/mdl_exporter.py` (snake_case); `prompts/wren_onboarding.md`, `prompts/wren_enrichment.md` | `test_llm_wren_client.py` (8) |
| B. Onboarding | `[COMPLETE]` | `semantic_layer/onboarding.py::onboard_schema_project`; `app.py` route `POST /agent/semantic-layer/projects/{id}/onboard`; `schemas.py::OnboardingResult`, `MdlFileSourceType="onboarding"` | `test_semantic_layer_api.py::test_onboard_*` (2) |
| C. Enrichment | `[COMPLETE]` | `LlmWrenClient.propose_mdl_from_document`; `app.py::_enrichment_proposal`; route `POST .../documents/text`; `schemas.py::SemanticDocumentTextRequest` | `test_semantic_layer_api.py::test_create_document_from_text_and_enrich` |
| D. Runtime context injection | `[COMPLETE]` | `LlmWrenClient.fetch_context` (emits `context_items`); `semantic_layer/runtime.py::merge_indexed_semantic_context` (appends, no clobber); `prompts/text_to_sql.md`, `prompts/conversation.md` | `test_graph.py::test_graph_injects_materialized_mdl_into_sql_prompt` |
| E. Frontend | `[COMPLETE]` | `AiAgentPanel/api.ts` (`onboardSemanticProject`, `createProjectDocumentFromText`, `OnboardingResult`); `SemanticLayerEditor/index.tsx` (Onboard button, paste-markdown + Enrich text) | `SemanticLayerEditor/index.test.tsx` (3) |

See [Remaining Risks & Gaps](#remaining-risks--gaps) at the end for known
limitations between dev expectation, actual implementation, and user intent.

## Goal: Three User Touchpoints

These are the acceptance anchors. Everything in this checklist exists to make
them work end to end.

1. **Onboarding** — open a database + schema, run onboarding to introspect the
   schema and generate a documented base model (draft MDL).
2. **Enrichment** — submit BI raw markdown text; an LLM enrichment process
   improves the base model (refined descriptions, synonyms, metrics,
   relationships) as a reviewable draft.
3. **Query** — ask the Superset AI agent a business question; the active
   semantic layer (MDL) is injected as authoritative context so the agent
   writes SQL with correct business meaning, joins, and metrics.

## Architecture Decision

Implement an **in-process LLM "modeling brain"** (`LlmWrenClient`) behind the
existing `WrenClient` protocol, driven by the agent's existing `model_client`
and prompt templates. The repo already provides the plumbing (project-per-schema
persistence, MDL file CRUD, materializer, document upload, enrich route, SQL Lab
editor UI); this work adds the missing intelligence and wires three gaps.

- The external `WrenHttpClient` and a future `wren-core` PyPI validator remain
  optional drop-ins behind the same protocol.
- One semantic project per `(database_id, catalog, schema)` (already modeled by
  `SemanticProject`).
- Generated MDL is **always draft** and requires explicit human activation.

## Governance Invariants (do not violate)

- [x] `SupersetClient.execute_sql` remains the only SQL execution boundary.
      (`LlmWrenClient` has no execution method — asserted by
      `test_llm_client_has_no_execution_methods`.)
- [x] No Wren execution method on any client; `WREN_EXECUTION_ENABLED=true`
      still fails startup (`factory.py`; `test_wren_factory_rejects_execution_enabled`).
- [x] Onboarding/enrichment output is written as `status="draft"`; never
      auto-activated, never auto-materialized (`onboarding.py`;
      `test_onboard_creates_draft_models_deterministic_fallback`).
- [x] Documents and MDL are context, not permission sources; Superset RBAC via
      `SemanticAccessService` stays authoritative (routes call
      `authorize_semantic_project`).
- [x] LLM prompts instruct: never invent columns/tables absent from the
      permission-filtered context or base model (`prompts/wren_onboarding.md`,
      `prompts/wren_enrichment.md`). **Prompt-enforced, not code-enforced — see
      risk R3.**
- [x] Generated SQL still passes `validate_read_only_sql` (unchanged graph path).

## Confirmed Baseline (verified in source)

| Area | File / symbol | State |
| --- | --- | --- |
| Wren client protocol + file/disabled/http impls | `integrations/wren/client.py`, `http_client.py` | read-only; `fetch_context` returns model **names** only |
| Superset→MDL exporter | `integrations/wren/mdl_exporter.py` | unwired; emits **camelCase** (`tableReference`), mismatched vs MDL spec |
| MDL file CRUD + draft/active/soft-delete | `semantic_layer/mdl_files.py` | complete; content is opaque YAML string |
| MDL validation | `semantic_layer/mdl_validation.py` | trivial (non-empty YAML/dict/list only) |
| Document upload + extract + deterministic proposals | `semantic_layer/documents.py`, `review.py` | complete but heuristic-only |
| Project materializer (active MDL → `mdl.json`) | `semantic_layer/wren_materializer.py`, `runtime.py` | complete |
| Graph context load + prompt injection | `graph.py::_load_wren_context`/`_call_sql_model`, `conversation_graph.py` | materializes path but **does not inject MDL content** |
| Superset schema introspection | `integrations/superset/client.py::list_datasets`, `get_agent_context`, `list_database_schemas` | available |
| SQL Lab editor UI | `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx`, `api.ts` | project resolve, MDL CRUD, doc upload, enrich, materialize wired |
| Onboarding route/flow | — | **missing** |

## The Three Gaps

- **Gap 1 (onboarding):** no schema-introspection → base-MDL route or flow.
- **Gap 2 (enrichment):** `enrich` uses `deterministic_mdl_proposal` (single
  stub model), no LLM, ignores existing base model.
- **Gap 3 (query):** `fetch_context` returns only model names; materialized MDL
  content (descriptions, relationships, metrics, examples) never reaches the
  prompt.

---

## Workstream A — LLM Modeling Brain (foundation)

### A1. Config

- [ ] In `config.py::AgentConfig`, extend `WrenAdapterMode` to include
      `"llm"` and add the env wiring:
  ```python
  wren_adapter: WrenAdapterMode = "llm"   # was "file"
  wren_onboarding_enabled: bool = True     # already present; default on
  wren_model_context_token_budget: int = 6000  # reuse wren_schema_context_token_budget if preferred
  ```
- [ ] `WREN_ADAPTER=llm` parsed in `AgentConfig.from_env`.

### A2. Prompts (register in `prompts/registry.py`)

- [ ] `prompts/wren_onboarding.md` — input: permission-filtered datasets
      (tables, columns + types, existing metrics, sample/description hints);
      output: strict MDL YAML, one `model` per dataset, with business
      `description`, per-column `properties.description`, inferred
      `relationships` (FK-name heuristics), and 2–3 example NL→SQL `queries`.
      Must instruct: never invent columns/tables not in the input.
- [ ] `prompts/wren_enrichment.md` — input: current active MDL + raw document
      text; output: improved MDL YAML (refined descriptions, synonyms, metrics,
      relationships) justified by the document; never add columns absent from
      the base model.
- [ ] Update `prompts/text_to_sql.md` and `prompts/conversation.md` — add a
      "Semantic layer (authoritative business context)" section: honor model and
      column descriptions, use defined `relationships` for joins, prefer defined
      metrics, follow example queries.

### A3. New client `integrations/wren/llm_client.py`

- [ ] `class LlmWrenClient` implementing `WrenClient`, constructed with
      `(config: AgentConfig, model_client: ModelClient, mdl_file_store: MdlFileStore | None = None)`.
- [ ] `is_available()` → `True` when `model_client` is configured.
- [ ] `list_models()` → names from active MDL (reuse helpers in `client.py`).
- [ ] `fetch_context(*, question, superset_context, mdl_path=None)` →
      load materialized `mdl.json` (or active files), keyword pre-filter models
      relevant to `question`, return `WrenContextArtifact` with
      `context_items` carrying real semantics (model + column descriptions,
      relationships, metrics, example queries), trimmed to
      `wren_model_context_token_budget`. (Closes Gap 3 at the source.)
- [ ] `propose_mdl_from_document(*, project, document)` → read project active
      MDL via `mdl_file_store` + `document.extracted_text`, call
      `model_client.chat` with `wren_enrichment` prompt + structured schema,
      return `MdlEnrichmentProposal` (validated, with warnings). Fall back to
      `deterministic_mdl_proposal` on model/parse failure. (Closes Gap 2.)
- [ ] `generate_base_model(*, project, superset_context)` → **new protocol
      method**; call `model_client.chat` with `wren_onboarding` prompt, seeded by
      the deterministic exporter (A4); return `list[MdlEnrichmentProposal]`
      (one per model) or a single multi-model proposal. (Powers Gap 1.)
- [ ] `preview_document_updates`, `dry_plan`, `recall_examples`,
      `validate_mdl_project` → delegate to existing deterministic logic.

### A4. Protocol + exporter fixes

- [ ] Add `generate_base_model(...)` to the `WrenClient` Protocol in
      `integrations/wren/client.py`; provide safe defaults on
      `DisabledWrenClient`, `FileWrenClient` (deterministic), and
      `WrenHttpClient` (`/models/generate` endpoint, falling back to
      deterministic).
- [ ] Fix `integrations/wren/mdl_exporter.py` to emit the canonical snake_case
      MDL spec: `table_reference` (with `catalog`/`schema`/`table`), `columns`
      with `is_calculated`/`expression`/`not_null`/`properties.description`,
      `relationships` with `join_type`/`condition`. Keep `_drop_none`.

### A5. Factory + app wiring

- [ ] Update `integrations/wren/factory.py::create_wren_client` to accept
      optional `model_client` + `mdl_file_store` and return `LlmWrenClient` when
      `wren_adapter == "llm"`.
- [ ] In `app.py::create_app`, construct the client where `active_model_client`
      and `active_mdl_file_store` already exist; pass it to both
      `TextToSqlGraph` and `ConversationGraph` (replacing the current
      `create_wren_client(app_config)` call site).
- [ ] Keep `if config.wren_execution_enabled: raise ValueError(...)` guard.

---

## Workstream B — Onboarding (Touchpoint 1)

### B1. Backend flow `semantic_layer/onboarding.py`

- [ ] `onboard_schema_project(*, project, superset_client, wren_client, mdl_file_store, owner_id) -> list[MdlFile]`:
  1. Pull permission-filtered schema via
     `superset_client.list_datasets(database_id, catalog, schema)` /
     `get_agent_context`.
  2. Seed deterministically with fixed `mdl_exporter.model_from_dataset`.
  3. Call `wren_client.generate_base_model(project=..., superset_context=...)`.
  4. Validate each proposal (`validate_mdl_yaml`).
  5. Write each as a **draft** `MdlFile` via `mdl_file_store.create`
     (`source_type="onboarding"`).
  6. Emit a `SemanticLayerEvent` (`index_started`/`index_completed` or a new
     `model_onboarded` type).
- [ ] Add `"onboarding"` to `MdlFileSourceType` in `semantic_layer/schemas.py`.

### B2. Route

- [ ] `POST /agent/semantic-layer/projects/{project_id}/onboard` in `app.py`:
  - authorize via `authorize_semantic_project(..., permission="write")`;
  - resolve Superset context for the project's db/catalog/schema;
  - call `onboard_schema_project`; return `{files: list[MdlFile], validation_summary}`.
- [ ] Define response schema (e.g. `OnboardingResult`) in
      `semantic_layer/schemas.py`.

---

## Workstream C — Enrichment from Raw Markdown (Touchpoint 2)

### C1. LLM enrichment

- [ ] No route change needed for file-based enrich once `LlmWrenClient` is the
      active client (`_enrichment_proposal` already calls
      `wren_client.propose_mdl_from_document`).
- [ ] Confirm `_enrichment_proposal` in `app.py` passes the project so the LLM
      can read the base model.

### C2. Raw-text submission path

- [ ] `POST /agent/semantic-layer/projects/{project_id}/documents/text`
      accepting `{filename: str, text: str}`:
  - reuse `create_document` with `content=text.encode("utf-8")`,
    `content_type="text/markdown"`;
  - return the created `SemanticDocument`.
- [ ] Frontend then calls the existing enrich endpoint on the returned document.

---

## Workstream D — Runtime Context Injection (Touchpoint 3)

- [ ] In `graph.py::_load_wren_context` and
      `conversation_graph.py::_load_wren_context`, ensure
      `wren_context.context_items` is populated by `LlmWrenClient.fetch_context`
      (real MDL semantics, token-capped). No new node required.
- [ ] Verify `_call_sql_model` payload (`graph.py:553`) serializes
      `wren_context` including `context_items` into the user message (it already
      `model_dump()`s the artifact).
- [ ] Confirm prompt updates from A2 instruct the model to treat the semantic
      layer as authoritative.
- [ ] No change to validation/execution path.

---

## Workstream E — Frontend (SQL Lab Semantic Layer Editor)

File: `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx`
and `../api.ts`.

- [ ] `api.ts`: add `onboardSemanticProject(projectId)` →
      `POST .../projects/{id}/onboard`; add
      `enrichProjectText(projectId, filename, text)` →
      `POST .../documents/text` then `enrichProjectDocument`.
- [ ] Add **"Onboard"** button (near "Materialize"); on success `refresh()` to
      surface draft files; disable while loading / without write permission.
- [ ] Add a **paste-markdown** textarea + **"Enrich text"** action calling
      `enrichProjectText`, then load the returned proposal into the editor
      (reuse existing `proposal` state + "Save as draft"/"Activate").
- [ ] No change needed to AiAgentPanel query path (scope already sent);
      touchpoint 3 improves once backend injects context.

---

## Optional / Stretch (cut first if time-constrained)

- [ ] `wren-core` PyPI dependency for real MDL schema validation, replacing
      `validate_mdl_yaml` (keep trivial validator as fallback).
- [ ] Real `wren-core` dry-plan / SQL rewriting (execution still via Superset).
- [ ] Relationship-inference quality pass; cube/metric modeling.
- [ ] Async (Celery) onboarding/enrichment; version-history rows.
- [ ] `WrenHttpClient` parity for `generate_base_model`.

---

## Tests

- [ ] `tests/unit_tests/superset_ai_agent/test_llm_wren_client.py` — mock
      `model_client`: proposal/onboarding/context shapes; no execution method;
      fallback on model failure; token budget respected.
- [ ] `test_onboarding.py` — seed → draft files written; never activated;
      respects permission-filtered datasets.
- [ ] Extend `test_semantic_layer_api.py` — `onboard` and `documents/text`
      routes (authz, 404s, draft status).
- [ ] Extend `test_graph.py` / `test_conversation_graph.py` — `context_items`
      reaches the prompt payload.
- [ ] `mdl_exporter` snake_case output assertion.
- [ ] Frontend: `SemanticLayerEditor` Onboard + Enrich-text actions
      (Jest + RTL).

## Manual E2E (acceptance)

- [ ] Open schema → **Onboard** → draft models appear with descriptions →
      **Activate** → **Materialize**.
- [ ] Paste BI markdown → **Enrich text** → review proposal → **Activate**.
- [ ] Ask the agent a business question → generated SQL reflects descriptions,
      defined relationships (joins), and metrics.

## Pre-flight

- [ ] `pre-commit run --all-files` (mypy, ruff, pylint, eslint, prettier) green.
- [ ] All new Python files carry the ASF license header and type hints.

---

## EOD Sequencing

1. **A** (brain + prompts + factory + exporter fix) — unblocks all. ~2h
2. **D** (context injection) — fastest demoable win (touchpoint 3). ~1h
3. **B** (onboarding) — touchpoint 1. ~1.5h
4. **C** (raw-text enrichment) — touchpoint 2. ~45m
5. **E** (frontend buttons) — ~1h
6. **Tests + pre-commit** — ~1h

**Cut-line to protect the demo:** keep A, D, B, C, and the Onboard button. Defer
everything under "Optional / Stretch."

---

## Remaining Risks & Gaps

Known gaps between dev expectation, actual implementation, and user intent as of
2026-06-22. None block the three touchpoints; all are follow-ups.

### R1. MDL validation + activation gate — `[COMPLETE]` (2026-06-22)
**Resolved.** Replaced the shallow validator with a typed, schema-aware one and
added a hard activation gate so structurally/physically invalid MDL can no
longer be activated or materialized.

Source:
- `semantic_layer/mdl_schema.py` — canonical typed MDL spec (`MdlModel`,
  `MdlColumn`, `MdlRelationship`, `MdlView`, `JOIN_TYPES`).
- `semantic_layer/mdl_validator.py::validate_mdl` / `validate_project_manifest`
  — structural checks (required fields, duplicate names, `join_type` enum,
  relationship resolution, calculated-column expressions) accumulating
  `MdlValidationMessage`s; `mdl_validation.py` now re-exports these so all
  ~6 call sites are upgraded.
- Store gate: `semantic_layer/mdl_files.py::_assert_activatable` +
  `MdlFileValidationError` block `draft→active` for invalid content in both
  in-memory and SQLAlchemy stores. A relationships-only file passes this per-file
  gate (relationships count toward `empty_root`); unresolved endpoints are warnings
  here and become errors only on the merged manifest.
- Route gate: `app.py::_enforce_activation` re-validates the merged project
  manifest (plus physical schema) on activation → `422`. This is where a
  relationship's endpoints are resolved strictly and wren-core deep validation runs.
- Materialization guard: `wren_materializer.py` validates the merged active
  manifest and returns `WrenMaterializationResult.warnings`.

Tests: `test_mdl_validator.py` (9), `test_semantic_layer_mdl_files.py`
(`test_cannot_activate_structurally_invalid_mdl_file`),
`test_semantic_layer_api.py::test_activation_blocked_for_hallucinated_column`.

**Residual:** structural validation is hand-rolled, not `wren-core`; deep
planner-grade validation (SQL-level) remains the optional Phase-2 `wren-core`
dependency. See new R11.

### R2. Onboarding only sees registered Superset *datasets*, not raw tables
`onboard_schema_project` pulls `superset_client.get_agent_context`, which returns
permission-filtered **datasets**. Tables in the schema with no Superset dataset
are invisible to onboarding. **Impact:** medium — users may expect "explore the
whole schema." This is the correct governed scope (we never bypass Superset),
but the UI does not yet communicate it. **Fix:** surface a "documents N
registered datasets; M physical tables not registered" note, or add a governed
table-introspection path.

### R3. Code-enforce "never invent columns" — `[COMPLETE]` (2026-06-22)
**Resolved.** Hallucinated tables/columns are now detected by physical
validation against a `SchemaIndex` built from permission-filtered Superset
metadata, and the activation gate blocks them.

Source:
- `semantic_layer/mdl_validator.py::SchemaIndex.from_agent_context` +
  physical checks in `validate_mdl` (`unknown_table`, `unknown_column`;
  calculated columns exempt).
- Generation-time visibility: `onboarding.py` validates each proposal against
  the schema index and writes invalid ones as draft with a "cannot be
  activated" warning; the enrich route (`app.py`) re-validates the proposal
  with `_schema_index_for_project` and surfaces `unknown_column` errors.
- Enforcement: the R1 activation gate (`_enforce_activation`) re-checks physical
  validity, so a hallucinated draft can never be activated.

Tests: `test_mdl_validator.py::test_physical_validation_flags_hallucinated_column`
/ `_allows_calculated_column`; `test_semantic_layer_api.py::
test_onboard_flags_hallucinated_columns_as_non_activatable`,
`test_enrich_flags_hallucinated_columns`,
`test_activation_blocked_for_hallucinated_column`.

**Residual:** physical enforcement degrades to structural-only if Superset
metadata can't be fetched at activation time (Superset outage); see new R12.

### R4. `fetch_context` retrieval is keyword overlap, not embeddings
`LlmWrenClient.fetch_context` ranks models by token overlap with the question
(plus a dataset-name boost) and trims by a char/4 token estimate. For wide
schemas this is coarse vs. Wren's embedding retrieval. **Impact:** low–medium on
large schemas (relevant models may be trimmed). **Fix:** embedding retrieval over
`schema_items` (listed under Optional / Stretch).

### R5. Enrichment reads only *active* MDL as the base
`propose_mdl_from_document` and onboarding's base read use active MDL files. If a
user onboards (drafts) but does not activate, then enriches, the enrichment sees
an empty base model. **Impact:** low, but a likely user-flow surprise.
**Fix:** include drafts as base context, or guide users to activate before
enriching.

### R6. LLM output contract depends on the provider honoring structured JSON
`LlmWrenClient._call_model` requests `format_schema` and parses JSON; on any
failure it falls back to the deterministic proposal. Weak local models may
frequently fall back to non-LLM scaffolds, so enrichment/onboarding "quality"
silently degrades to deterministic. **Impact:** medium for self-hosted models.
**Fix:** surface in the UI whether a proposal was LLM- or fallback-generated
(the warnings already differ — wire a badge).

### R7. fetch_context re-reads `mdl.json` from disk every query
Materialization writes `mdl.json`; `fetch_context` reads it per request. No
caching/invalidation beyond the materialized checksum. **Impact:** low (small
files). **Fix:** cache by `materialized_checksum`.

### R8. UI gaps vs. user intent — `[COMPLETE]` (2026-06-22)
**Resolved (all three sub-gaps).**

- **Async onboarding + progress:** onboarding now returns `202` with a
  `SemanticJob`; the slow LLM work runs off-request via a job runner, and
  `onboarding_started`/`completed`/`failed` events are emitted.
  Source: `semantic_layer/jobs.py` (`InMemoryJobStore`, `ThreadJobRunner`,
  `InlineJobRunner`), `app.py` onboard route + `GET .../jobs/{job_id}`,
  `schemas.py::SemanticJob`. Frontend polls via `api.ts::runOnboarding`.
  Tests: `test_semantic_layer_api.py::test_onboard_*` (job shape + pollable);
  `SemanticLayerEditor/index.test.tsx` (async onboard).
- **Warnings surfaced:** `OnboardingResult.warnings`,
  `MdlEnrichmentProposal.validation`/`warnings`, and
  `WrenMaterializationResult.warnings` now render in `SemanticLayerEditor`
  (`onboarding-warnings`, `proposal-review` alerts).
  Test: `SemanticLayerEditor/index.test.tsx` asserts `onboarding-warnings`.
- **Enrichment review:** proposals render a `proposal-review` panel with
  validation status, error messages, and a collapsible "Previous content"
  before/after (`proposalBefore`). Full inline diff highlighting is still a
  nice-to-have; see new R13.

**Residual:** the job store + thread runner are process-local (single worker);
see new R14.

### R9. `mdl_exporter` envelope still mixes casing
Model/column bodies are now snake_case (spec-aligned), but the manifest envelope
in `export_agent_context_to_mdl` still emits `dataSource`/`semanticOverlay`
(camelCase), matching `wren_materializer`. Harmless for the current LLM-context
use, but must be reconciled before adopting `wren-core`, which is strict about
the manifest shape.

### R10. mypy not verified locally
`mypy` is not installed in the working venv, so type-checking was not run.
Types were written to be annotation-complete, but CI `pre-commit run mypy` is the
authoritative gate and was **not** executed here. **Action:** run it before push.

## R11–R15 Hardening — `[COMPLETE]` (2026-06-22)

Second hardening pass. Verification: backend **183 passed, 1 skipped**
(`wren-core` deep-path test skipped when the optional engine is absent);
frontend **13 passed**; `ruff` clean on new/changed files. Migration **`0002`**
adds the snapshot + jobs tables.

### R11. wren-core deep validation — `[COMPLETE]`
Optional `wren-core` engine augments (never replaces) the always-on
structural/physical validator.
- Source: `semantic_layer/wren_core_validator.py` (import-guarded `wren_core`;
  `validate_with_wren_core`; `to_wren_core_manifest` snake→camelCase mapping);
  `mdl_validator.validate_project_manifest(deep_validate=...)` merges findings;
  activation gate passes `deep_validate=config.wren_core_validation_enabled`;
  config flag `wren_core_validation_enabled` (+ `WREN_CORE_VALIDATION_ENABLED`);
  `requirements-ai-agent.txt` carries the commented opt-in dep.
- Tests: `test_wren_core_validator.py` (mapping, unavailable no-op, deep flag
  safe without the engine; engine-present test `skipif` not installed).
- **Residual → R16.**

### R12. Outage-resilient physical validation — `[COMPLETE]`
A successful schema fetch is snapshotted per project; on a Superset outage the
snapshot is used so hallucinated columns are still caught at activation.
- Source: `persistence/models.py::AiAgentSchemaSnapshot` (+ migration `0002`);
  `semantic_layer/schema_snapshot.py` (in-memory + SQLAlchemy stores);
  `SchemaIndex.from_snapshot`/`to_tables`; `app.py::_schema_index_for_project`
  (upsert on success, load snapshot on failure); strict mode
  `semantic_activation_requires_live_schema` → `409` when no schema available.
- Tests: `test_schema_snapshot.py`;
  `test_mdl_validator.py::test_schema_index_from_snapshot_validates_like_live`;
  `test_semantic_layer_api.py::test_activation_uses_schema_snapshot_during_outage`.
- **Residual → R17.**

### R13. Real diff viewer — `[COMPLETE]`
The enrichment `proposal-review` panel renders a line-level split diff
(`react-diff-viewer-continued`, already a repo dependency) of current vs proposed
MDL.
- Source: `SemanticLayerEditor/index.tsx` (`proposal-diff` block).
- Test: `SemanticLayerEditor/index.test.tsx` (renders diff when a proposal
  replaces an existing file).

### R14. DB-backed jobs (cross-worker) — `[COMPLETE]` (Phase 1)
- Source: `persistence/models.py::AiAgentJob` (+ migration `0002`);
  `semantic_layer/jobs.py::SqlAlchemyJobStore`; `app.py::_create_job_store`
  (selected by persistence mode).
- Tests: `test_job_store.py` (in-memory lifecycle; **cross-instance visibility**
  proving a job created by one "worker" is pollable by another; failure path).
- **Residual → R18** (Phase 2 Celery for execution durability).

### R15. Persisted physical validation — `[COMPLETE]`
A draft's stored `MdlFile.validation` now reflects schema-aware findings.
- Source: `mdl_files.py` `create`/`update`/`_new_file` accept a `validation`
  override; `onboarding.py` and the `POST`/`PATCH` mdl-file routes compute
  schema-aware validation (cheap via the R12 snapshot) and pass it in.
- Tests: `test_semantic_layer_mdl_files.py::test_create_persists_validation_override`;
  `test_semantic_layer_api.py::test_create_persists_physical_validation`.

## New Residual Risks introduced by R11–R15 (2026-06-22)

### R16. wren-core manifest shape is unverified against a live engine
`to_wren_core_manifest` targets the `wren-core-base` camelCase serde shape but
was **not** validated against an installed `wren-core` (the package is not in the
working venv; the engine-present test is `skipif`-skipped). Field-name drift
across wren-core versions could make deep validation reject valid manifests.
**Impact:** medium **when the flag is enabled**; zero by default (flag off /
engine absent → no-op). **Fix:** a CI job that installs `wren-core` and runs the
skipped test against a golden manifest; reconcile R9 envelope casing.

### R17. Schema snapshots can be stale
The snapshot is refreshed only on a successful live fetch. If a table/column is
dropped or renamed in Superset during an outage, validation against the stale
snapshot can over- or under-flag until the next successful fetch. **Impact:**
low. **Fix:** TTL the snapshot, or fall to strict mode
(`semantic_activation_requires_live_schema`) for high-governance deployments.

### R18. Job execution is still in-process (Phase-1)
`SqlAlchemyJobStore` makes jobs **visible** across workers, but `ThreadJobRunner`
still executes on the submitting worker; a worker dying mid-onboarding leaves an
orphaned `running` job (no result). **Impact:** medium for multi-worker prod.
**Fix:** Phase-2 `CeleryJobRunner` (deferred) plus a stale-`running` sweep with a
`started_at` heartbeat.

### R19. Per-save schema fetch cost
R15 computes schema-aware validation on every MDL create/update, adding a
Superset metadata fetch per save (snapshot only short-circuits the *outage*
path, not the happy path). **Impact:** low–medium (extra round-trip on an
interactive save). **Fix:** cache the live `SchemaIndex` per request/short TTL,
or reuse the snapshot within a freshness window.
