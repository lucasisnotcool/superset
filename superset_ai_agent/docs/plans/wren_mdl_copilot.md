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

# Wren MDL Copilot — Implementation Plan & As-Built Log

> **This top section (§AB) is the authoritative as-built record** — the single
> source of truth for current behavior, files, endpoints, and gaps. Sections
> §0–§11 below are the **original plan (intent)**; consult them for rationale, but
> where they disagree with §AB, §AB wins.

---

# §AB. AS-BUILT IMPLEMENTATION LOG

**Status:** MDL Copilot (agentic CRUD + validation/correction loop) **and** the
Coverage Audit (md→MDL information-loss detection) are **implemented, wired UI→API,
and tested**. Verified green: **backend 557 unit tests** (`ruff`/`black`/`mypy`
clean on new code), **frontend 148 `AiAgentPanel` jest tests / 22 suites**
(`prettier` clean). Everything is gated behind `WREN_COPILOT_ENABLED` (404 when
off). The **unified onboarding/enrichment lifecycle** (readiness gate, `properties`
preservation, enrichment-via-Copilot, onboarding-skill parity) is **§AB.10**.

> **Coverage re-architected (2026-06-30) — this log predates it.** Coverage is now
> **background + decoupled**: it is a read-only *version label* (not a provenance
> event), has live stage progress, a recovery agent that proposes gap-closing edits,
> and the **on-demand "Coverage" button + `CoverageDialog` were removed** from the
> Copilot panel (the `run_coverage_audit` / `CoverageRequest` per-document path and
> `POST /copilot/coverage` linger as deprecated, unused). Canonical:
> `MDL_PROVENANCE_AND_COVERAGE.md` (top banner) →
> `plan_coverage_labels_and_progress_spec.md` §12 and
> `plan_coverage_recovery_agent_spec.md` §11 ("as shipped").

## AB.1 How to run / verify

```bash
# Backend (from repo root), venv has the deps incl. wren_core 0.7.1 wheel
source venv/bin/activate
python -m pytest tests/unit_tests/superset_ai_agent/            # full agent suite
python -m pytest tests/unit_tests/superset_ai_agent/test_copilot_*.py
ruff check superset_ai_agent/semantic_layer/copilot/
mypy superset_ai_agent/semantic_layer/copilot/<module>.py        # whole-repo mypy has 2k+ pre-existing errs; scope to files

# Frontend
cd superset-frontend
npx jest src/SqlLab/components/AiAgentPanel
npx prettier --write <files>     # eslint v9 CLI can't load the legacy config standalone; use prettier + project `npm run lint`/pre-commit
```

## AB.2 Backend file map (`superset_ai_agent/`)

- `llm/base.py` — `ModelClient.chat(..., tools=)` + `ModelResult.tool_calls`;
  `ToolSpec`/`ToolCall`; helpers `tools_to_openai`, `parse_openai_tool_calls`,
  `message_to_openai` (renders assistant tool-call **replay** + `role="tool"`
  results). `ChatMessage` gained `tool_call_id`, `name`, `tool_calls`.
- `llm/openai_client.py`, `openai_compatible.py`, `azure_openai.py`, `ollama.py`
  — all 4 providers thread `tools`, return `tool_calls`; structured-output
  (`format_schema`) callers unaffected. ollama parses object-args; others JSON-str.
- `semantic_layer/copilot/schemas.py` — `Changeset`/`ChangesetItem`,
  `ChangesetApplyRequest`, `WorkspaceNode`, `CopilotInspector`/`ToolDescriptor`/
  `SkillDescriptor`/`InstructionView`, `MessageAttachment`, `CopilotTurnRequest`,
  and coverage: `CoverageClaim`/`CoverageFinding`/`CoverageReport`/
  `OverreachFinding`/`CoverageRequest`.
- `semantic_layer/copilot/tools.py` — `MdlToolset`: in-memory **working-set**
  CRUD (never writes the store), tools `list/read/write/delete_mdl_file`,
  `validate_project`, `get_physical_schema`, plus RAG `list_documents`/
  `search_documents`/`find_duplicate_documents`; `build_changeset()` diffs working
  vs originals (JSON-normalized) → `Changeset`. `DocumentReader` protocol.
- `semantic_layer/copilot/loop.py` — `run_copilot_loop` (bounded tool-calling +
  engine-validation + correction loop; emits `AgentStep`s via `on_step`),
  `build_system_prompt` (base prompt + skills + project instructions).
- `semantic_layer/copilot/service.py` — `run_copilot`, `apply_changeset_items`
  (persists accepted items as **drafts** via existing CRUD, `source_type="copilot"`),
  `build_deploy_preview`, `build_inspector`. FastAPI-free (unit-testable).
- `semantic_layer/copilot/workspace.py` — `build_workspace_tree` (folders from
  path prefixes + virtual `instructions.md`/`queries.yml`/`raw/`/`target/mdl.json`/
  `.wren/memory`).
- `semantic_layer/copilot/coverage.py` — Coverage Audit: `extract_claims` (A),
  `build_mdl_facts` (B), `_FactRanker` (embedding cosine → keyword fallback),
  `judge_coverage(..., votes=N)` (C, multi-vote majority, conservative ties),
  `judge_overreach` (bidirectional), `aggregate_report` + `run_coverage_audit` (D),
  `InMemoryCoverageCache` + `audit_cache_key`.
- `semantic_layer/copilot/coverage_eval.py` — `score_coverage(predicted, gold)` →
  accuracy + per-status P/R/F1 (`GoldLabel`, `CoverageEvalMetrics`). Offline eval.
- `prompts/mdl_copilot.md`, `coverage_extract.md`, `coverage_judge.md`,
  `coverage_overreach.md` — loaded via `prompts/registry.get_prompt`.
- `app.py` — routes (see AB.4), nested in `create_app` closing over `active_*`
  deps (`active_model_client`, `active_mdl_file_store`, `active_semantic_layer_store`,
  `active_embedder`, `active_instruction_store`, `active_coverage_cache`,
  `_schema_index_for_project`, `_project_instruction_views`, `_attachments_text`,
  `authorize_semantic_project(request, pid, owner_id=, permission=)`).
- `config.py` — flags in AB.5. `schemas.py` — `MdlFileSourceType` gained
  `"copilot"`.

## AB.3 Frontend file map (`superset-frontend/src/SqlLab/components/AiAgentPanel/`)

- `api.ts` — clients: `getProjectWorkspace`, `runCopilot`, `streamCopilot` (SSE),
  `applyCopilotChangeset`, `getCopilotInspector`, `getCopilotDeployPreview`,
  `listProjectDocuments`, `runCoverage(projectId, docId, includeOverreach)`; types
  mirror backend (`Changeset`, `WorkspaceNode`, `CopilotInspector`,
  `CoverageReport`, `OverreachFinding`, …). SSE helpers `splitSseFrames`/
  `parseSseData` reused.
- `SemanticLayerEditor/index.tsx` — the editor: **split-pane** Files │ Editor │
  Copilot **rail** (toggle in header, `showCopilot`), `WorkspaceTree` browser,
  Ace editor with **inline gutter diagnostics** (`useJsonValidation` + stored
  validation line/col), **dirty-state** tag, **Validate** button. Tabs: Models /
  Instructions / Graph.
- `SemanticLayerEditor/WorkspaceTree.tsx` — antd `Tree`; `treeFromFiles(mdlFiles)`
  builds the tree client-side (works regardless of copilot flag); per-file activate
  `Switch` via `renderActions`.
- `SemanticLayerEditor/CopilotPanel.tsx` — embedded chat: streaming live steps,
  per-file **diff Accept/Reject** (`react-diff-viewer-continued`), Apply, attachment
  composer (UTF-8 long-context), header buttons **Coverage** + **Inspector**.
- `SemanticLayerEditor/CopilotInspectorDialog.tsx` — read-only Prompt/Skills/Tools
  + Instructions (note: file is `...Dialog`, not `...Drawer`).
- `SemanticLayerEditor/CoverageReportModal.tsx` — exports `CoverageReportBody`
  (presentational: score, counts, per-claim findings, over-reach section) +
  `CoverageReportModal` wrapper.
- `SemanticLayerEditor/CoverageDialog.tsx` — document picker (`listProjectDocuments`)
  + over-reach checkbox + Run → `runCoverage` → `CoverageReportBody`.

## AB.4 HTTP endpoints (all under `/agent/semantic-layer/projects/{pid}`, copilot-gated)

| Method | Path | Purpose |
|---|---|---|
| GET | `/workspace` | Unified workspace tree (`WorkspaceNode`). |
| GET | `/copilot/inspector` | Prompt/skills/tools/instructions. |
| GET | `/copilot/deploy-preview` | Aggregate drafts-vs-active diff + manifest validation. |
| POST | `/copilot` | Run agentic edit turn → `Changeset` (sync). |
| POST | `/copilot/stream` | Same, SSE: `progress` steps then `complete` changeset (thread+queue over `on_step`). |
| POST | `/copilot/apply` | Persist accepted `ChangesetItem`s as drafts. |
| POST | `/copilot/coverage` | Coverage audit (`CoverageRequest{document_id, model?, include_overreach}`) → `CoverageReport`. |
| GET | `/documents` | List project source documents (added for the coverage picker). |
| GET | `/readiness` | Project readiness (`SemanticProjectReadiness{status,ready,…}`); **not** copilot-gated (read-only, drives the spinner). §AB.10. |

**Gate:** `POST /copilot` and `/copilot/stream` now also call `_require_project_ready`
→ **409** (`detail.status` ∈ `empty`/`indexing`/`failed`) until the base MDL layer is
onboarded and stable. §AB.10.

Reused existing: `mdl-files` CRUD+validate, `documents`/`documents/text`+`enrich`,
`onboard`/`reset`/`materialize`, `jobs/{id}`, `instructions`, `projects/resolve`,
conversations + `messages[/stream]`.

## AB.5 Config flags (`config.py`, `WREN_*` env)

- `wren_copilot_enabled` (default False) — gates all copilot routes (404 off).
- `wren_copilot_autopilot_enabled` (False) — reserved for auto-pilot (unbuilt).
- `wren_copilot_attachment_max_chars` (200_000) — inline attachment truncation.
- `wren_copilot_coverage_votes` (1) — coverage judge votes (majority).
- Reused: `wren_modeling_max_correction_retries`, `wren_modeling_deep_validation`,
  `wren_core_validation_enabled`, `wren_document_indexing_enabled`,
  `wren_document_vector_index`, `wren_instruction_recall_k`.

## AB.6 Key contracts, decisions & declared breaks

- **Tool-calling** added to `ModelClient` (the one foundational contract change;
  additive — structured-output path untouched). Loop degrades closed: a model that
  returns no tool calls yields an empty changeset + warning.
- **Propose, don't persist** — the copilot mutates a working-set copy and returns a
  `Changeset`; nothing hits the store until the user Accepts → `apply` writes
  **drafts**. Activation/Deploy stays a separate human action.
- **Skills are now active** — `skills/*.md` injected into the copilot system prompt
  (previously inert). Surfaced read-only in the inspector.
- **Authoring is JSON** (not Wren YAML); we mirror Wren's **folder organization**
  via `path` prefixes only (no storage change — `normalize_mdl_path` already allows
  subfolders).
- **`MdlFileSourceType` gained `"copilot"`** (provenance).
- **Coverage is advisory, degrade-closed** — extraction/judge failure → `missing`
  (loss-surfacing). Reads chunks, **falls back to `SemanticDocument.extracted_text`**
  when indexing is off.
- **Attachments**: inline long-context on the **copilot** request only; bypass the
  document/RAG pipeline by design. The legacy SqlLab chat does NOT take attachments
  (out of scope — separate agent).
- **`properties` is never silently dropped** by the Copilot write path (§AB.10.1):
  additive restore against the prior file version; the agent may add/change keys
  but not delete an existing block.
- **Readiness gate** (§AB.10.2): copilot edits are 409'd until the base MDL layer is
  onboarded and stable; readiness is *derived* (active files + running jobs), no new
  project column/migration.

## AB.7 Codebase facts / gotchas discovered (load-bearing for future work)

- `MdlFileStore` = whole-file `content` per row, **soft-delete**; `normalize_mdl_path`
  enforces `.json`, blocks `..`/absolute, **allows subfolders**.
- `SemanticProject.current_version_id` is a **dangling column — no version table**
  exists → snapshot/revert versioning is unbuilt (deferred, needs a migration).
- Document **chunks only exist when `wren_document_indexing_enabled`**; coverage
  falls back to `extracted_text` (a real `SemanticDocument` field) otherwise.
- wren-core validation = constructing `SessionContext(base64(manifest))` and
  catching the raise (`wren_core_validator.py`); `SessionContext.dry_run` exists but
  is unused. `validate_project_manifest(contents, schema_index, deep_validate,
  dedup_models)` is the manifest gate.
- `SchemaIndex` (`mdl_validator.py`) is the "never invent columns" physical index;
  `.to_tables()`, `.typed_tables()`, `.has_types()`.
- Icons that DON'T exist in `@superset-ui/core/components/Icons`: `PaperClipOutlined`,
  `RobotOutlined`, `AuditOutlined`, `FileSearchOutlined`. Used instead:
  `UploadOutlined`, `CommentOutlined`, `CheckSquareOutlined`. `Empty`/`Drawer`/
  `Tree`/`Checkbox`/`Tabs` are exported from `@superset-ui/core/components`.
- antd `Modal` uses `show`/`onHide` (not `open`/`onClose`) in this repo.
- jest env: `TextEncoder`/`ReadableStream` available; SSE component tests stub
  `global.fetch` with a `body.getReader()` chunk reader.

## AB.8 Test inventory (`tests/unit_tests/superset_ai_agent/`)

`test_model_client_tools.py` (tool-calling, 4 providers) · `test_copilot_tools.py`
(toolset/changeset) · `test_copilot_loop.py` (agentic loop, correction, degrade) ·
`test_copilot_service.py` (workspace tree, apply, deploy-preview, inspector) ·
`test_copilot_api.py` (routes: run/apply/workspace/inspector/stream/deploy-preview/
coverage + 404-gating + extracted-text fallback) · `test_copilot_coverage.py`
(A/B/C/D, embedder, caching, multi-vote, over-reach, eval). FE: `api.test.ts`,
`CopilotPanel.test.tsx`, `WorkspaceTree.test.tsx`, `CoverageReportModal.test.tsx`,
`CoverageDialog.test.tsx`, `SemanticLayerEditor/index.test.tsx`,
`SemanticLayerEditor/SemanticLayerImportDialog.test.tsx`. §AB.10 adds: properties
guard (`test_copilot_tools.py`), readiness gate (`test_copilot_api.py` +
`SemanticLayerEditor/index.test.tsx` + `api.test.ts`), onboarding skill
(`test_copilot_service.py`), enrichment deprecation notice
(`SemanticLayerImportDialog.test.tsx`).

## AB.9 Remaining / deferred (with rationale)

- **Onboarding UI gating & user flow** — planned in
  [`plan_onboarding_gating_user_flow.md`](plan_onboarding_gating_user_flow.md):
  remove auto-onboard, reset→delete-only, readiness-driven rail with a
  separate-process bootstrap view vs chat. Not yet implemented.
- **Copilot ↔ AI SQL agent parity** (conversation persistence + multi-turn) —
  spec in [`plan_copilot_parity_spec.md`](plan_copilot_parity_spec.md). The Copilot
  is currently stateless per request (no thread, no history); the SQL agent has a
  full conversation stack. Not yet implemented; build on the gating plan.
- **Snapshot/revert versioning** (`current_version_id`) — needs a new table +
  Alembic migration not verifiable here. Deploy-**preview** delivers the
  review-before-Deploy half.
- **Persistent (cross-worker) coverage cache** — current cache is per-worker
  in-memory (determinism holds within a worker, lost on restart).
- **Temp-0 / first-run determinism** — needs a `temperature` arg on
  `ModelClient.chat` (cross-provider change, not yet made). Multi-vote mitigates.
- **Live-model coverage eval** — `score_coverage` is tested; no committed gold
  md↔MDL fixtures or live-model runner, so real detector accuracy is unmeasured.
- **"Fix gap" action** — coverage suggestions are text; not yet a one-click
  pipe into the copilot changeset loop.
- **Auto-pilot mode** over `raw/` (background `JobStore` run) — unbuilt.
- **`ConversationTurnRequest.attachments`** on the SqlLab SQL chat — intentionally
  out of scope.
- Minor UI polish: deploy-preview has no editor button yet; coverage picker lists
  all documents regardless of extraction status; toggling the Copilot rail remounts
  (chat transcript not persisted — conversations store integration is the fix).

## AB.10 Unified onboarding/enrichment lifecycle (this pass)

Four changes that unify the schema-open → onboard → enrich → query flow and close
the highest-severity gaps found in the end-to-end UX investigation. All read-only
to existing contracts except where noted; no migration added.

**1. `properties` preservation guard (data-loss fix — was the top risk).**
The Copilot `write_mdl_file` path was a verbatim overwrite, so an LLM that
re-emitted a file could silently drop the Superset-extension `properties`
(`displayName`/`alias`/`synonyms`) that back governance + retrieval — and neither
structural nor wren-core validation catches it. Now `tools.py:_write_mdl_file`
runs `_preserve_superset_properties(prior, new)`: entities/columns matched by name,
**additively** restore any dropped `properties` key (new values still win on
collision), re-serialize only if a restore happened, and return a `note` so the
agent learns it. Mirrors the enrichment path's `_merge_column_preserving_structure`.
`prompts/mdl_copilot.md` gained a hard rule: never remove/empty an existing
`properties` block. Tests: `test_copilot_tools.py` (restore, additive-edit,
no-spurious-diff, no-invent-on-new-file).

**2. Readiness gate (Copilot only edits a stable layer).**
New `SemanticProjectReadiness` schema + `GET …/readiness` (read-only, not behind
the copilot flag) derive status from existing signals — active MDL files +
in-flight onboarding jobs — via `JobStore.list_for_project` (added to the protocol,
`InMemoryJobStore`, `SqlAlchemyJobStore`). States: `empty`/`indexing`/`ready`/
`failed`. `POST /copilot` and `/copilot/stream` call `_require_project_ready` →
**409** until `ready`. **No migration**: readiness is derived, not a new column
(`current_version_id` is still the dangling one). Frontend `SemanticLayerEditor`
gates the Copilot rail on a locally-derived `copilotReady = hasActiveModels &&
!isOnboarding` (single source of truth = `mdlFiles`, already reactive), showing a
spinner (`indexing`) or an **Onboard** prompt (`empty`) via `data-test=
copilot-not-ready`. Tests: `test_copilot_api.py` (readiness states, 409-until-ready,
seed-active helper), `SemanticLayerEditor/index.test.tsx` (blocked vs mounted),
`api.test.ts` (`getProjectReadiness`).

**3. Enrichment via Copilot as the primary stream.**
`prompts/mdl_copilot.md` now lists the document tools (`list_documents`/
`search_documents`/`find_duplicate_documents`) and adds an "Enriching from
documents" workflow (search → fold into descriptions/synonyms/metrics → reconcile
conflicts) — previously the tools were callable but undocumented, so the agent
rarely used them. The legacy `SemanticLayerImportDialog` shows a **deprecation
notice** steering document-enrichment to the Copilot (legacy markdown-enrich path
kept functional, not deleted). Tests: `test_copilot_api.py` (inspector advertises
document tools + prompt steers them), `SemanticLayerImportDialog.test.tsx` (notice).

**4. Onboarding-skill parity.**
`COPILOT_SKILLS` now loads `("onboarding", "generate-mdl", "enrich-context")` —
Wren v2's triad (the `onboarding` skill existed but was inert). `skills/onboarding.md`
gained the Wren workspace layout (`models/`, `relationships.json`, `views/`) and the
note that there is **no `mkdir`** — `write_mdl_file` creates folders implicitly from
the path. Test: `test_copilot_service.py` (inspector includes the `onboarding` skill).

**Wren-v2 reference (sources):** Wren authors a YAML directory (`models/<name>/
metadata.yml`, `relationships.yml`, `views/`, `cubes/`, `instructions.md`) compiled
to a single `target/mdl.json`; onboarding is introspection-driven (FKs → relationships)
with LLM enrichment on top; `properties` is the documented metadata/extension bag;
deploy passes an INDEXING gate before query. We mirror the *behavior* (JSON files,
introspection seed, properties bag, readiness gate) with our storage.

**Known gaps / risks after this pass:**
- Binary (PDF/DOCX) **attachments** to the Copilot are still read client-side as
  text (`file.text()`); for binaries, upload first then `search_documents`. Markdown/
  text attachments are fine.
- Readiness on the FE is **derived locally**, not polled from `/readiness`; the
  endpoint exists for the backend gate + external consumers. Multi-worker/stale
  cases are caught by the backend 409 (authority), not the FE derivation.
- The `properties` guard cannot *delete* a property by design (governance fields
  shouldn't be silently droppable); intentional removal needs a manual edit.
- Onboarding itself is still the deterministic introspection path (Copilot is gated
  *after* it); "Copilot drives onboarding" (item 1's stretch) remains the
  deterministic-fallback design, now correctly gated.
- Coverage/`deploy-preview`/`inspector` are **not** readiness-gated (read-only views
  the editor needs while loading); only the editing turns are.

## AB.11 Prompt network (authoritative map)

Every prompt that drives an agent, plus the meta-prompts that drive agents to
maintain those prompts. "Loaded by" = the exact runtime call site. All runtime
prompt/skill files are loaded through `get_prompt`/`get_skill`, which **strip the
leading ASF license header / frontmatter at load** (`prompts/registry.py`
`strip_leading_metadata`) so no boilerplate reaches the model.

### 1. MDL Copilot — the authoring agent (`semantic_layer/copilot/`)
The effective system prompt is assembled per run by `loop.py:build_system_prompt`
= **base + `## Skills` (all three, always-on) + `## Operator instructions`**.

| Role | File | Loaded by |
|---|---|---|
| Base system prompt | `prompts/mdl_copilot.md` | `loop.py` `build_system_prompt` → `get_prompt("mdl_copilot")` |
| Skill (always-on) | `skills/onboarding.md` | `service.py` `COPILOT_SKILLS` → `_skill_texts` → `get_skill` |
| Skill (always-on) | `skills/generate-mdl.md` | same |
| Skill (always-on) | `skills/enrich-context.md` | same |

Skills are **injected context, not tool-called and not on-demand**. The Copilot's
actions are tools (`tools.py`), not skills.

### 2. Coverage Audit — Copilot sub-feature (`semantic_layer/copilot/coverage.py`)
| File | Loaded by |
|---|---|
| `prompts/coverage_extract.md` | `coverage.py` `extract_claims` |
| `prompts/coverage_judge.md` | `coverage.py` `judge_coverage` |
| `prompts/coverage_overreach.md` | `coverage.py` `judge_overreach` |

### 3. Deterministic onboarding / enrichment — non-Copilot (`integrations/wren/llm_client.py`)
The base-model generation + document-enrichment path (structure from the catalog,
semantics from the model). Distinct from the Copilot.

| File | Loaded by |
|---|---|
| `prompts/wren_onboarding.md` | `llm_client.py` `_call_model("wren_onboarding")` (`generate_base_model`) |
| `prompts/wren_enrichment.md` | `llm_client.py` `_call_model("wren_enrichment")` (`propose_mdl_from_document`) |

### 4. AI SQL agent — the query/consume agents (`graph.py`, `conversation_graph.py`)
Two LangGraph **structured-output** pipelines (no tools, no skills).

| File | Loaded by | Node |
|---|---|---|
| `prompts/table_selection.md` | `graph.py` `get_prompt("table_selection")` | model selection |
| `prompts/text_to_sql.md` | `graph.py` `get_prompt("text_to_sql")` | one-shot SQL draft (`SqlDraft`) |
| `prompts/conversation.md` | `conversation_graph.py` (`get_prompt("conversation")`, 2 sites) | chat draft |
| `prompts/sql_reflection.md` | `conversation_graph.py` `get_prompt("sql_reflection")` | reflect / review |

### 5. Loaders & assembly (plumbing)
- `prompts/registry.py` — `get_prompt`, `strip_leading_metadata` (header strip), lru-cached.
- `skills/__init__.py` — `get_skill`, `list_skills` (same header strip).
- `semantic_layer/copilot/loop.py` — `build_system_prompt` (base + skills + instructions).
- `semantic_layer/copilot/service.py` — `COPILOT_SKILLS`, `_skill_texts`, `build_inspector`.

### 6. Maintenance prompts (meta — agents that maintain the prompts above)
Located at `superset_ai_agent/`. These are run by a human handing each to an agent
instance; they are **not** loaded at runtime.

| Meta-prompt | Drives | Targets |
|---|---|---|
| `codebase_prompt_for_agents_skill_maintenance.md` | 3 skill agents (onboarding / generate-mdl / enrich-context) | `skills/*.md` (+ `prompts/wren_onboarding.md`, `prompts/wren_enrichment.md`); copy-first from upstream baseline, tailor in place |
| `codebase_prompt_for_agent_mdl_prompt_integration.md` | 1 integrator agent | sole editor of `prompts/mdl_copilot.md`; reconciles base↔skill layering |
| `codebase_prompt_for_agent_query_maintenance.md` | 1 query agent | `prompts/{text_to_sql,conversation,sql_reflection,table_selection}.md`; evolve in place |

Agent reports: `codebase_response_for_agents_skill_maintenance/{onboarding,generate-mdl,enrich-context,mdl_copilot_integration,query_agent}.md`.

### 7. Upstream baselines (reference-only — NEVER loaded at runtime)
`wren_upstream_skills/` holds verbatim Canner/WrenAI sources used by the
maintenance agents as tailoring baselines (provenance headers; third-party — see
its `README.md`). Files: `onboarding.SKILL.md`, `generate-mdl.SKILL.md`,
`enrich-context.SKILL.md`, `enrich-context.references.{gap_catalog,cube_proposals}.md`,
`AGENTS.md` (base-prompt structural baseline), `usage.SKILL.md` +
`wren_langchain_prompt.py` (query baselines). Not removed during cleanup.

### Cleanup performed this pass
`skills/usage.md` was **removed** — it was inert (no skill-loading exists in the
query graphs; the Copilot loads only the three `COPILOT_SKILLS`), an untailored
paraphrase, and its genuine content is preserved in
`wren_upstream_skills/usage.SKILL.md`. All other prompts have a live consumer
(verified by grepping every `get_prompt(...)` / `get_skill(...)` call site).


## 0. Summary & intent

**MDL Copilot** turns the existing `SemanticLayerEditor` into a *Cursor-for-MDL*
surface: a workspace file browser, an editor with real agentic editing, an
embedded conversational agent that performs **CRUD on MDL files** and runs an
**engine validation → correction loop**, plus an **agent inspector** and
**conversation file upload**.

The feature's two hard goals (from the originating brief):

1. **Agentic CRUD MDL** — the agent can autonomously create, read, update, and
   delete any MDL file within a given schema (= a semantic *project*).
2. **Validation + correction loop** — run wren-core engine validation, capture
   structured errors, and self-correct before surfacing proposals.

### Guiding constraints (do not violate)

- **C1 — Wren parity is the priority.** Do not simplify or weaken functionality
  for convenience. Where Wren has a capability, we match its *behavior*, even if
  our storage/representation differs.
- **C2 — Declare every broken contract.** Any deviation from an existing pattern,
  schema, or invariant is called out explicitly in §7.
- **C3 — Reuse Superset/agent natives.** Prefer existing stores, endpoints,
  components, and the LangGraph/SSE/jobs plumbing over new infrastructure.

### Design decisions already locked (from product review)

| Decision | Choice |
| --- | --- |
| Chat placement | **Embedded** inside `SemanticLayerEditor` (not the SqlLab right-rail panel). |
| Edit application model | **Propose diff → Accept/Reject** (Cursor-style); nothing persists until accept. |
| Accept granularity | **Per-file** (whole-file diff, reusing `react-diff-viewer-continued`). |
| Authoring format | **JSON** (not Wren's YAML); we mirror Wren's *folder organization*, not its YAML authoring. |
| Attachment model | **Long-context inline** in the user message; no RAG, no persistence (MVP). |

---

## 1. Wren parity reference (the bar)

Sources: [Wren quickstart](https://docs.getwren.ai/oss/engine/get_started/quickstart),
[What is MDL](https://docs.getwren.ai/oss/concepts/what_is_mdl),
[Wren architecture](https://docs.getwren.ai/oss/reference/architecture),
[WrenAI README](https://github.com/Canner/WrenAI),
[Wren engine API](https://docs.getwren.ai/oss/wren_engine_api).

### 1.1 Native workspace layout

```
<project>/
  wren_project.yml          # project config (datasource, catalog/schema)
  models/                   # one folder per model (metadata.yml)
  views/                    # saved view definitions
  relationships.yml         # relationships across models
  instructions.md           # business / operational rules for the agent
  queries.yml               # reviewed NL->SQL examples (seed memory)
  raw/                      # source docs the auto-pilot agent reads
  .wren/memory/             # LanceDB retrieval index (auto-managed)
  target/mdl.json           # compiled, engine-ready manifest
```

Source authored as YAML, **compiled to a camelCase JSON manifest**.

### 1.2 Agentic modeling behavior to match

- The agent **reads and writes MDL directly** so "one definition lives in MDL
  instead of agents hallucinating joins."
- **Skills** are Markdown workflows that orchestrate primitives in the right order
  (e.g. "build MDL before querying", "fetch context before SQL").
- **Two modes:** *grill* (one question at a time) and *auto-pilot* (agent reads
  `raw/` and proposes). Both write to MDL + instructions + queries + memory, **all
  reviewable**.
- **Correctness primitives:** rich schema retrieval, **dry-plan validation**,
  **structured errors with hints**, value profiling, eval runner.
- **Correction loop:** run → capture structured error → re-prompt → loop until
  valid.
- **Deploy:** changes are reviewed as a **diff**, then a **Deploy** action
  synchronizes the contract.

---

## 2. Current-state map (what already exists)

This feature is mostly **assembly of existing parts**. The map below is the reuse
surface; entrypoints are file paths + symbol names (line numbers are approximate
and may drift — anchor on symbols).

### 2.1 Backend (`superset_ai_agent/`)

| Concern | Entrypoint | Notes |
| --- | --- | --- |
| FastAPI routes | `app.py` | All agent + semantic-layer routes; route builders `build_conversation_graph`, `build_text_to_sql_graph`. |
| Conversation turn | `app.py::send_conversation_message` (~611), `::stream_conversation_message` (~664) | SSE + REST; both call `ConversationGraph.run`. |
| Conversation request schema | `conversations/schemas.py::ConversationTurnRequest` (~131), `::ConversationMessage` (~84) | **Extend with `attachments` (§6).** |
| Agent graph | `conversation_graph.py::ConversationGraph`, `ConversationState` (~146), `_compile_graph` (~592) | LangGraph; reflection/retry loop is the template for the copilot loop. |
| SQL validate→repair loop | `graph.py::_compile_graph` (~332) | Canonical `validate → repair → re-plan` template. |
| LLM-driven MDL authoring + **correction loop** | `integrations/wren/llm_client.py::_draft_with_correction` (~289), `::_call_model` (~570), `::propose_mdl_from_document` (~164), `::generate_base_model` (~406), `::_patch_target` | **Primary reuse target** for the copilot loop. |
| MDL file store (CRUD) | `semantic_layer/mdl_files.py::MdlFileStore` (~75), `InMemoryMdlFileStore`, `SqlAlchemyMdlFileStore`, `normalize_mdl_path` (~467) | Per-file rows, whole-file `content`, soft-delete. **`normalize_mdl_path` already allows subfolders** (blocks `..`/absolute, enforces `.json`). |
| Structural + physical validation | `semantic_layer/mdl_validator.py::validate_mdl` (~163), `::validate_project_manifest` (~225), `SchemaIndex` (~45) | Manifest-level merge + "never invent columns" via `SchemaIndex`. |
| Deep wren-core validation | `semantic_layer/wren_core_validator.py::validate_engine_manifest` (~75), `::_friendly_engine_error` (~109) | Validation via `SessionContext(base64(manifest))` construction; Rust errors → friendly `MdlValidationMessage`. |
| Engine seam | `semantic_layer/engine/wren_core_engine.py::WrenCoreEngine` (`validate`, `plan_sql`); `engine/base.py::SemanticEngine` (~80) | In-process `wren_core` 0.7.1. **`SessionContext.dry_run` exists but is unused** (wire for dry-plan parity). |
| Compile / materialize | `semantic_layer/mdl_compile.py::compile_manifest`, `CompiledManifest.to_engine_manifest`, `to_base64_json`; `semantic_layer/wren_materializer.py` | Merge files → engine manifest (`target/mdl.json`). |
| Instructions (`instructions.md`) | `semantic_layer/instructions.py::Instruction`, store; routes `app.py` (`list/create/delete instructions`) | Project-scoped; `is_global` always applies; already injected into enrichment payload (`llm_client.py` ~213). |
| NL→SQL examples (`queries.yml`) | `persistence/migrations/versions/0003_nl_sql_examples.py` table | owner/project/scope + question/semantic_sql/native_sql. |
| Documents (`raw/`) | `app.py` (`upload_project_source_document`, `create_project_document_from_text`, `enrich_project_document` ~1609); `semantic_layer/store.py`, `file_storage.py` | Persistent corpus + enrichment pipeline (RAG-ish `select_relevant_sections`). |
| Async jobs | `semantic_layer/jobs.py::JobStore`, `ThreadJobRunner`; `app.py::_start_onboarding_job` (~1318) | Background runner + `GET .../jobs/{id}` polling + SSE events (`_append_semantic_event` ~2136). |
| LLM abstraction | `llm/base.py::ModelClient.chat` (~45), `ChatMessage`, `ModelResult`; `llm/factory.py` | **Structured-output only today; extend for tool-calling (§Phase 0).** Providers: ollama, openai, openai_compatible, azure. |
| Prompts | `prompts/registry.py::get_prompt`; `prompts/wren_enrichment.md`, `prompts/wren_onboarding.md` | File-backed prompt loader. |
| Skills | `skills/__init__.py::list_skills`, `get_skill`; `skills/*.md` | **Inert today** (nothing dispatches them). Activate for the copilot + inspector. |
| Authoring contract | `semantic_layer/mdl_authoring.py::AuthoredManifest`, `proposal_response_schema`, `serialize_manifest` | Typed LLM authoring schema (camelCase). |
| Activation gate | `app.py::_enforce_activation` (~1114); `mdl_files.py::_assert_activatable` (~53) | Manifest-level deep validation before draft→active. A relationships-only file is a valid fragment (counts toward `empty_root`); its endpoints resolve on the merged manifest at the route gate, mirroring Wren's top-level `relationships.yml`. |
| Persistence models | `persistence/models.py::AiAgentSemanticMdlFile` (~283), `AiAgentInstruction`, `SemanticProject` (`current_version_id` ~209, **dangling — no version table**). |
| Config flags | `config.py` (`wren_*`, `WREN_*` env) | `wren_modeling_max_correction_retries`, `wren_modeling_deep_validation`, `wren_core_validation_enabled`, etc. |

### 2.2 Frontend (`superset-frontend/src/SqlLab/components/AiAgentPanel/`)

| Concern | Entrypoint | Notes |
| --- | --- | --- |
| MDL editor (file list + Ace + CRUD + activate + reset) | `SemanticLayerEditor/index.tsx` | Mounted as a SqlLab **editor tab** via `actions/sqlLab.ts::openSemanticLayerEditor` (~881) + `TabbedSqlEditors/index.tsx`; opened from `TableExploreTree/index.tsx` (~287). Redux holds tab existence (`types.ts::semanticLayerEditors`); file contents are **local state**. |
| Instructions panel | `SemanticLayerEditor/InstructionsPanel.tsx` | List/add/delete; "Always apply" toggle. |
| Import + diff | `SemanticLayerEditor/SemanticLayerImportDialog.tsx` | Uses **`react-diff-viewer-continued`** (antd-themed). Reuse for changeset diffs. |
| Schema graph | `SemanticLayerEditor/SchemaGraph/*` (ECharts; `ids.ts`, `validationOverlay.ts`, `mdlOverlay.ts`) | 3 layers; read-only. Add click→open-file. |
| Chat panel (SSE) | `AiAgentPanel/index.tsx`; SSE helpers in `api.ts` (`consumeConversationStream`, `splitSseFrames`) | Streaming chat, execution modes, regenerate, explain. |
| Agent trace | `AiAgentPanel/ExplainDialog.tsx`, `AgentStepDetail.tsx` | Step timeline; discriminated union on `detail.kind`. Add an `mdl_edit` kind. |
| API client + types | `AiAgentPanel/api.ts` | Full endpoint map + `MdlFile`, `MdlValidationMessage` types. Base URL `/ai-agent`. |
| Editor widget | `src/core/editors/EditorHost.tsx` (`AceEditorProvider`, react-ace); `useJsonValidation` from `@superset-ui/core/components/AsyncAceEditor` | Canonical editor. **No Monaco in repo.** `useJsonValidation` → gutter annotations. |

---

## 3. Architecture overview

Four pillars, all inside the `SemanticLayerEditor` surface:

```
┌─ Workspace ─┬──────────── Editor / Diff ────────────┬──── Copilot ─────┐
│ models/     │  models/orders.json     ⌘S  Validate   │ [chat]           │
│  orders ●   │  {                                      │ [inspector ▾]    │
│  items      │    "models": [ … ]        ⚠ line 12     │  prompt | instr  │
│ relationships.json                                    │  skills | tools  │
│ instructions.md   ── agent proposes ──                │                  │
│ queries.yml │  ◀ Current     Proposed ▶               │ ▸ planning…      │
│ raw/        │  + "metrics":[{ revenue … }]            │ ▸ validating…    │
│ target/     │  [✓ Accept]          [✗ Reject]          │ ✓ 3 edits ready  │
│  mdl.json🔒 │                                          │ 📎 attach        │
└─────────────┴────────────────────────────────────────┴──[ Ask… ]───────┘
```

### 3.1 The agentic edit loop (server-side)

```
user turn (+ attachments)
  → load workspace context (MDL files, SchemaIndex, project instructions, skills)
  → [LLM tool-calling loop, bounded by max_steps / correction_retries]
        plan → call CRUD tool(s) → validate_project_manifest (+ deep wren-core)
                                       │ valid?
                          ┌── no ──────┘
                          ▼
                     feed structured errors back → re-plan
        └── yes ──► assemble Changeset (proposed file contents, pre-validated)
  → stream agent_step progress over SSE
  → return Changeset (NOT persisted)
client: per-file diff → Accept → existing create/update/deleteMdlFile (draft)
      → Deploy (activate) = existing activation gate
```

Key intent: **propose, don't persist.** The loop reuses the
`_draft_with_correction` discipline (feed `previous_validation_errors` back), but
generalized from "one document → one file" to "multi-file changeset over the whole
project," and made *agentic* via tool-calling (the model chooses which CRUD op).

---

## 4. Requirements

### 4.1 Functional requirements

- **FR1** — Browse the full workspace tree (models, views, relationships,
  instructions, queries, raw, target, memory) with folder hierarchy.
- **FR2** — Manual MDL CRUD with folders, dirty-state, inline diagnostics
  (gutter), on-demand validate.
- **FR3** — Conversational agent that performs CRUD across all MDL files in the
  project and returns a reviewable **Changeset**.
- **FR4** — Engine validation + bounded correction loop before a Changeset is
  surfaced; correction steps are visible in the trace.
- **FR5** — Per-file Accept/Reject of proposed changes; accepted edits land as
  **drafts**; **Deploy** activates with a manifest-level validation gate.
- **FR6** — Agent Inspector: read-only **Prompt**, read-only **Skills**,
  read-only **Tools**, and **editable project-scoped Instructions**.
- **FR7** — Conversation attachments: upload UTF-8 text files fed inline into the
  user message (long-context, no RAG, no persistence).

### 4.2 Non-functional / parity requirements

- **NFR1** — Degrade-closed: missing `wren_core` → deep validation no-op (existing
  behavior), copilot still produces structurally-validated proposals.
- **NFR2** — Authorization unchanged: project `permission`/`canWrite` gates every
  mutation; copilot endpoints reuse the existing project auth path.
- **NFR3** — No new heavy infra: reuse `JobStore`/SSE/Ace/diff-viewer.
- **NFR4** — All agent writes are reviewable before they hit storage (FR5).

### 4.3 Out of scope (MVP)

- Per-entity (sub-file) accept granularity.
- RAG over attachments; attachment persistence.
- YAML authoring; auto-pilot over `raw/` as a background job (grill/interactive
  first; auto-pilot is a fast-follow that reuses `JobStore`).
- Editing `queries.yml`/memory beyond surfacing (read + delete only for MVP).

---

## 5. Contracts & data shapes

New transient/display contracts (mirror the existing `MdlEnrichmentProposal`
"reviewable artifact, not stored" pattern). Define in `schemas.py` /
`conversations/schemas.py` and the TS mirror in `api.ts`.

```python
# Changeset (returned by the copilot; NOT persisted)
class ChangesetItem(BaseModel):
    op: Literal["create", "update", "delete"]
    path: str
    file_id: str | None = None            # for update/delete
    current_content: str | None = None    # diff base; None for create
    proposed_content: str | None = None   # None for delete
    validation: MdlValidationResult       # pre-validated proposal (file-level)
    summary: str                          # human label, e.g. "Add revenue metric"

class Changeset(BaseModel):
    items: list[ChangesetItem]
    manifest_validation: MdlValidationResult   # whole-project after applying items
    warnings: list[str] = []
    steps: list[AgentStep] = []                # reuse the explain timeline
```

```python
# Workspace tree (GET .../workspace) — unifies stores for display
class WorkspaceNode(BaseModel):
    path: str
    kind: Literal["folder", "mdl", "instructions", "queries",
                  "document", "compiled", "memory", "config"]
    editable: bool
    status: str | None = None             # draft|active for MDL
    validation: MdlValidationResult | None = None
    children: list["WorkspaceNode"] = []
```

```python
# Inspector (GET .../copilot/inspector) — display of effective agent context
class CopilotInspector(BaseModel):
    system_prompt: str                    # get_prompt("mdl_copilot")
    skills: list[dict]                    # [{name, text}] from get_skill()
    tools: list[dict]                     # [{name, description}] from registry
    instructions: list[Instruction]       # project-scoped, editable elsewhere
```

```python
# Attachment (added to ConversationTurnRequest)
class MessageAttachment(BaseModel):
    filename: str
    content_type: str = "text/plain"
    text: str = Field(max_length=ATTACHMENT_MAX_CHARS)   # UTF-8 only
```

```python
# Tool-calling extension to the LLM contract (llm/base.py)
# chat(..., tools: list[ToolSpec] | None = None) -> ModelResult
#   ModelResult.tool_calls: list[ToolCall] | None
# ToolSpec  = {name, description, parameters(JSON Schema)}
# ToolCall  = {id, name, arguments(dict)}
```

---

## 6. New backend surface (small)

| Endpoint | Purpose | Reuses |
| --- | --- | --- |
| `GET /agent/semantic-layer/projects/{pid}/workspace` | Unified workspace tree (FR1) | mdl_files, instructions, nl_sql_examples, documents, compile_manifest |
| `POST /agent/semantic-layer/projects/{pid}/copilot/stream` (SSE) | Agentic edit loop → Changeset (FR3/FR4) | ConversationGraph pattern, `_draft_with_correction`, jobs/SSE |
| `GET /agent/semantic-layer/projects/{pid}/copilot/inspector` | Prompt/skills/tools/instructions (FR6) | `get_prompt`, `list_skills`/`get_skill`, tool registry, instructions store |
| (extend) `POST .../conversations/{id}/messages[/stream]` | `+ attachments[]` (FR7) | existing routes/schema |

Apply-on-accept reuses **existing** `createMdlFile` / `updateMdlFile` /
`deleteMdlFile`. (Optional fast-follow: a batch `apply` endpoint for atomic
multi-file writes.)

---

## 7. Declared contract breaks (per C2)

1. **`ModelClient.chat` gains tool-calling** (`llm/base.py`). Additive (`tools=`
   param, `tool_calls` on `ModelResult`); existing `format_schema` callers
   unaffected. *Fallback if a provider lacks tool-calls:* structured "edit-plan"
   output — implement and comment, so degrade-closed holds (NFR1).
2. **Skills become active.** `skills/*.md` are inert today; they will be injected
   into the copilot system prompt and surfaced read-only in the inspector. Fix the
   stale `skills/generate-mdl.md` line that says "author YAML" (we author JSON).
3. **Changeset is a transient artifact** — not persisted until accept. No storage
   contract change (reuses the `MdlEnrichmentProposal` precedent).
4. **Workspace tree surfaces non-MDL stores** (instructions/queries/raw/target/
   memory) as virtual nodes. Presentation-layer unification only; each store keeps
   its own persistence.
5. **Conversation `attachments`** extend the message schema and **bypass** the
   document/enrichment (RAG) pipeline. Intentional MVP lane, distinct from
   persistent `raw/` documents.
6. **`current_version_id` becomes real** (Phase 7): implement at least
   snapshot-before-deploy so Deploy is revertible. Today it is a dangling column.
7. **Folder convention** (`models/`, `views/`, `relationships.json`): new files
   adopt it; onboarding starts emitting it. Existing flat-path files remain valid
   (no migration).

---

## 8. Implementation sequencing (checklist)

Phases are ordered for **incremental, shippable value** with explicit
dependencies. Each task names the file/symbol to touch. `[ ]` = todo.

### Phase 0 — Foundations & contracts (unblocks parallel work)
- [ ] Add tool-calling to the LLM contract: `llm/base.py` (`ModelClient.chat` +
      `ToolSpec`/`ToolCall`/`ModelResult.tool_calls`).
- [ ] Implement tool-calls per provider in `llm/factory.py` clients
      (openai, azure, openai_compatible, ollama) + structured-output fallback.
- [ ] Add a `ToolRegistry` (new `semantic_layer/copilot/tools.py`): typed wrappers
      over `MdlFileStore` CRUD + `validate_project_manifest` + dry-plan +
      `SchemaIndex` retrieval + instructions recall.
- [ ] Define schemas: `Changeset`/`ChangesetItem`, `WorkspaceNode`,
      `CopilotInspector`, `MessageAttachment` (`schemas.py`,
      `conversations/schemas.py`); mirror types in `api.ts`.
- [ ] Add config flags in `config.py`: `wren_copilot_enabled`,
      `wren_copilot_autopilot_enabled` (reuse `wren_modeling_*` retries/validation).
- [ ] Unit tests: provider tool-call round-trips; schema (de)serialization.

### Phase 1 — Workspace browser (FR1)
- [ ] Backend `GET .../workspace`: aggregate stores into `WorkspaceNode` tree
      (`app.py` route + new `semantic_layer/copilot/workspace.py`). Reuse
      `mdl_files.list`, instructions store, nl_sql_examples query, documents list,
      `compile_manifest` (for `target/mdl.json`).
- [ ] Adopt folder convention in onboarding writers (`semantic_layer/onboarding.py`,
      `llm_client.generate_base_model`): emit `models/<name>.json`,
      `relationships.json`.
- [ ] Frontend: replace left `ScrollList` with a `@superset-ui/core` **Tree** in
      `SemanticLayerEditor/index.tsx`; folder nodes, draft/active badges, dirty (●)
      and agent-touched (✎) markers; read-only styling for `target/`+`.wren/`.
- [ ] `api.ts`: `getWorkspace(pid)`.
- [ ] Tests: tree assembly (backend unit); tree render + selection (Jest/RTL).

### Phase 2 — Editor agentic upgrades (FR2) — pure frontend, low risk
- [ ] Inline diagnostics: wire `validation.line/column` → Ace gutter via
      `useJsonValidation` in `SemanticLayerEditor/index.tsx`.
- [ ] Dirty-state tracking + ⌘S + unsaved-changes guard.
- [ ] On-demand **Validate** button → existing `validateMdlFile` (`api.ts`).
- [ ] Graph→file navigation: click node in `SchemaGraph` → open file (use
      `SchemaGraph/ids.ts`).
- [ ] Tests: dirty-state transitions; gutter annotation mapping.

### Phase 3 — Copilot backend loop (FR3/FR4)
- [ ] New `semantic_layer/copilot/graph.py`: LangGraph mirroring
      `conversation_graph.py` (`load_context → plan → call_tool → validate →
      correct → finalize`), bounded by `max_steps`/`wren_modeling_max_correction_retries`.
- [ ] Generalize the correction discipline from
      `llm_client._draft_with_correction` to multi-file (extract shared helper).
- [ ] Wire dry-plan: call `SessionContext.dry_run` via
      `engine/wren_core_engine.py` for a representative view/metric (parity gap).
- [ ] `POST .../copilot/stream` route (`app.py`): SSE `agent_step` progress +
      final `Changeset`. Reuse `_append_semantic_event` / SSE framing.
- [ ] Assemble effective system prompt: `prompts/mdl_copilot.md` (new) +
      active skill text (`get_skill`) + project instructions + tool schemas.
- [ ] Tests: loop converges to valid manifest within retry budget (seeded schema +
      doc); degrade-closed without `wren_core` (NFR1).

### Phase 4 — Embedded Copilot chat + changeset review (FR3/FR5) — frontend
- [ ] `SemanticLayerEditor/CopilotPanel.tsx`: project-scoped chat; reuse SSE
      helpers (`consumeConversationStream`) + trace primitives (`ExplainDialog`,
      `AgentStepDetail` with new `mdl_edit` kind).
- [ ] `SemanticLayerEditor/ChangesetReview.tsx`: per-file diff cards
      (`react-diff-viewer-continued`) with Accept/Reject + Accept-all/Reject-all.
- [ ] Apply-on-accept: call existing `createMdlFile`/`updateMdlFile`/`deleteMdlFile`
      (drafts); optimistic update + rollback on failure; refresh workspace tree.
- [ ] `api.ts`: `streamCopilot(pid, request)`.
- [ ] Tests: accept writes draft + refreshes; reject discards; SSE fallback.

### Phase 5 — Agent inspector (FR6)
- [ ] Backend `GET .../copilot/inspector` (`app.py` + `workspace.py` helper).
- [ ] Frontend `SemanticLayerEditor/AgentInspector.tsx`: tabs Prompt (read-only),
      Skills (read-only `SafeMarkdown`), Tools (read-only list), Instructions
      (editable — reuse `InstructionsPanel` + instructions endpoints, project scope).
- [ ] `api.ts`: `getCopilotInspector(pid)`.
- [ ] Tests: instructions add/delete reflected in effective prompt payload.

### Phase 6 — Conversation attachments (FR7)
- [ ] Backend: add `attachments: list[MessageAttachment]` to
      `ConversationTurnRequest` (and copilot request); wrap text into the LLM user
      message in the graph context builder; enforce `ATTACHMENT_MAX_CHARS`
      (truncate + warn).
- [ ] Frontend: paperclip in the Copilot composer; read file as UTF-8 text;
      attachment chips (name/size/remove); include in request payload.
- [ ] Tests: oversize truncation/warning; attachment reaches the prompt.

### Phase 7 — Deploy parity & versioning (FR5, C2.6)
- [ ] Deploy/activate flow: aggregate **pre-deploy diff** (all drafts vs active) +
      manifest validation gate (reuse `_enforce_activation`).
- [ ] Snapshot-before-deploy: implement a version/snapshot row behind
      `SemanticProject.current_version_id` (`persistence/models.py` + migration in
      `persistence/migrations/versions/`); enable revert.
- [ ] Correction-loop trace surfacing in the chat (`validate ⚠ → correct → ✓`).
- [ ] Tests: deploy gate blocks invalid manifest; revert restores prior version.

### Phase 8 — Hardening, polish, docs
- [ ] Permission gating audit (`canWrite`) on every new mutation (NFR2).
- [ ] Empty/loading/error states for tree, changeset, inspector.
- [ ] `pre-commit run --all-files` (black/ruff/mypy/prettier/eslint) — gate per
      CLAUDE.md.
- [ ] Update `superset_ai_agent/ARCHITECTURE.md` + this file's status.
- [ ] (Fast-follow) auto-pilot mode over `raw/` as a `JobStore` background run.

---

## 9. Testing strategy

- **Backend unit** (`tests/unit_tests/` patterns; pytest): tool registry ops,
  copilot loop convergence + degrade-closed, workspace assembly, changeset
  validation, attachment truncation, deploy gate + revert.
- **Frontend** (Jest + RTL, per CLAUDE.md "prefer unit tests"; `test()` not
  `describe()`): tree render/selection, dirty-state, gutter mapping, changeset
  accept/reject + apply, inspector instruction CRUD, attachment chips.
- **Engine fidelity:** assert `_friendly_engine_error` translations remain stable
  across the copilot loop (seeded malformed manifest → expected `code`).
- **No new E2E** for MVP; if added later use Playwright (Cypress deprecated).

---

## 10. Risks & open questions

- **R1 — Tool-calling variance across providers.** Mitigation: structured
  edit-plan fallback (Phase 0), keep ollama path tested.
- **R2 — Whole-file replace vs concurrent edits.** A copilot Changeset is built
  against a snapshot; if the user edits between propose and accept, detect via
  `checksum` mismatch and re-base or warn.
- **R3 — Long-context attachment limits.** Enforce `ATTACHMENT_MAX_CHARS`; surface
  truncation; this is the explicit MVP trade-off vs RAG.
- **R4 — Folder convention drift.** Existing flat files must keep working; the tree
  builder groups by `path` prefix regardless.
- **OQ1** — Should the embedded copilot reuse the `conversations` store
  (persisted history) or a lightweight ephemeral session? *Default:* reuse
  `conversations` for parity (history is reviewable), scoped to the project.
- **OQ2** — Batch `apply` endpoint for atomic multi-file accept now or fast-follow?
  *Default:* per-file reuse for MVP; batch in Phase 7 with versioning.

---

## 11. Appendix — endpoint map (target state)

Existing (reuse): `mdl-files` CRUD + `validate` + `upload`, `documents` +
`enrich`, `onboard`/`reset`/`materialize`, `jobs/{id}`, `instructions` CRUD,
`conversations` + `messages[/stream]`, `projects/resolve`.

New:
- `GET  .../projects/{pid}/workspace`
- `POST .../projects/{pid}/copilot/stream` (SSE)
- `GET  .../projects/{pid}/copilot/inspector`
- (extend) `POST .../conversations/{id}/messages[/stream]` with `attachments[]`

## AB. As-built — Copilot conversation persistence & multi-turn (parity)

Implements `plan_copilot_parity_impl.md` (companion `plan_copilot_parity_spec.md`,
File 2; builds on File 1's always-mounted readiness rail). Brings the MDL Copilot
to AI SQL parity on **durable persistence** and **multi-turn memory**, reusing the
shared conversation stack rather than forking it.

**Shared conversation stack (reused, made agent-agnostic).**
- `ConversationArtifact` (`conversations/schemas.py`) is now generic: `type: str`
  (was `Literal["sql"]`), `sql` optional, plus an opaque `payload: dict`. The
  Copilot persists its `Changeset` as `type="changeset"` + `payload=changeset
  .model_dump()` — no typed import into `conversations/`.
- `Conversation`/`ConversationSummary` gained `kind` (`"sql"` default | `"copilot"`)
  and `project_id`. Migration `0008_conversation_kind_project` adds the columns
  (`server_default="sql"` backfills existing SQL threads; `project_id` is a plain
  indexed column, not a FK).
- `ConversationStore.create/list` take `kind`/`project_id`; `list(kind=…)` filters
  so the AI SQL history (`GET /agent/conversations`, now `kind="sql"`) and the
  project-scoped Copilot history stay separate. Both `InMemory` and `SqlAlchemy`
  stores implement it.

**The reusable seam (goal 2).** `conversations/turns.py::ConversationTurnService`
owns the agent-agnostic turn choreography — `begin_turn` (append user + scope),
`history_messages` (window prior turns → `ChatMessage[]`), `commit_turn` (append
assistant + artifacts). A future agent reuses persistence + the turn lifecycle by
supplying its own loop and artifact shape; it customizes only its graph/RAG.

**Multi-turn into the loop.** `run_copilot_loop`/`run_copilot` take
`history: list[ChatMessage]`, prepended after the system prompt and before the new
user turn (no prompt-network change, per spec §7). Windowed by
`WREN_COPILOT_MAX_HISTORY_MESSAGES` (default 12; mirrors `AI_AGENT_MAX_HISTORY_MESSAGES`).

**Routes (project-scoped, parallel surface).**
- `POST/GET .../projects/{pid}/copilot/conversations` (create / list)
- `GET/PATCH/DELETE .../projects/{pid}/copilot/conversations/{cid}` (get / rename / delete)
- `.../copilot` and `.../copilot/stream` now honor `conversation_id`: append the
  user turn, feed history, persist the assistant turn + `changeset` artifact. Absent
  `conversation_id` → stateless one-shot (backward compatible). Readiness 409 +
  write-authz gates unchanged.

**Frontend.** `CopilotPanel` is thread-backed: it creates/loads threads via the
client, persists each turn server-side, and resumes the active thread across page
reload (localStorage `sqllab:mdl-copilot:conversation:{projectId}`). Header gains
New chat / History / Rename / Delete (AI SQL parity). Past changesets re-render
**read-only** on resume; only the live turn's changeset is actionable (no stale
Apply). Justified deviations from the SQL panel: keeps file attachments + changeset
review (authoring-specific); omits the execution-mode dropdown (Copilot proposes
drafts; Apply is the human gate).

**Transcript-consistency hardening (gap closure).** The stored transcript never
ends on a dangling user turn: the turn helper's `commit(content, changeset=None)`
always pairs an assistant turn — the changeset summary + artifact on success, a
failure note on error (non-streaming 502 and streaming `error` event), and
`"Generation cancelled."` on client disconnect (`GeneratorExit`), mirroring the AI
SQL stream contract. Apply is recorded too: `ChangesetApplyRequest.conversation_id`
makes the apply route append an `"Applied N draft(s)."` assistant turn, so a resumed
thread shows the proposal *was* applied (parity with the SQL agent's execute-sql
turn). The FE client raises a typed `AgentApiError` carrying the HTTP status; a
resumed thread that 404s (deleted elsewhere) is silently forgotten — stale
localStorage cleared, fresh chat — instead of surfacing an error banner.
