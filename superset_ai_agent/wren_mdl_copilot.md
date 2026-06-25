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

# Wren MDL Copilot — Implementation Plan

> Status: **Backend + core frontend implemented & tested.** This document is the
> authoritative implementation plan and a checklist for future passes.
>
> Frontend done (130 AiAgentPanel jest tests pass, prettier clean):
> - `api.ts` copilot client (`getProjectWorkspace`, `runCopilot`,
>   `applyCopilotChangeset`, `getCopilotInspector`) + types (+ tests).
> - `CopilotPanel.tsx` — embedded chat, per-file **diff Accept/Reject**, Apply,
>   agent-step timeline, **attachment composer** (long-context). (+ tests)
> - `CopilotInspectorDrawer.tsx` — read-only Prompt/Skills/Tools + Instructions.
> - Editor (`SemanticLayerEditor/index.tsx`) — **Copilot tab**, **dirty-state
>   indicator**, **on-demand Validate** button.
>
> Also done since: **SSE streaming** — `POST /copilot/stream` (thread+queue over
> the loop's `on_step` sink) and `streamCopilot` in `api.ts`; CopilotPanel shows
> live agent steps. **Inline Ace gutter diagnostics** — `useJsonValidation`
> (live JSON syntax) merged with the stored file's line/column validation
> messages, rendered as editor annotations.
>
> Also done since: **workspace-tree folder browser** — `WorkspaceTree.tsx`
> (antd Tree, folder hierarchy from path prefixes, per-file activate Switch
> preserved, draft/active/invalid tags, selection → open file) replaces the flat
> file list in the editor. Built client-side via `treeFromFiles` so it works
> regardless of the copilot flag; backend `GET /workspace` returns the same shape
> (plus virtual sibling nodes) for other consumers.
>
> Also done since: **split-pane layout** — the editor's Models view is now
> Files │ Editor │ **Copilot rail** (toggleable via the header), the Cursor
> layout (replaced the Copilot tab). **Deploy preview** — `build_deploy_preview`
> + `GET /copilot/deploy-preview` + `getCopilotDeployPreview` return the aggregate
> drafts-vs-active diff with resulting manifest validation (Wren "Deploy" review).
>
> Deliberately deferred (with rationale):
> - **`ConversationTurnRequest.attachments`** on the legacy SqlLab *text-to-SQL*
>   chat — out of scope: attachments are fully delivered in the Copilot (MDL)
>   conversation; the SQL chat is a separate agent.
> - **Snapshot/revert versioning** behind `current_version_id` — requires a new
>   table + Alembic migration that cannot be fully exercised in this environment;
>   left for a migration-verified pass. The deploy-preview delivers the
>   review-before-Deploy half of Phase 7.
> - **Deploy-preview UI affordance** (a "Preview deploy" button/modal in the
>   editor) — backend + client are done and tested; the editor button is pending.
>
> Verified: backend **502 unit tests pass** (ruff/black clean); frontend
> **133 AiAgentPanel jest tests pass** (prettier clean).

## Implementation status (live)

**Done + tested (backend, 29 new unit tests, ruff/black/mypy clean on new code):**
- Phase 0.1/0.2 — tool-calling across the LLM contract and all four providers
  (`llm/base.py`, `openai_client.py`, `openai_compatible.py`, `azure_openai.py`,
  `ollama.py`), with assistant-tool-call replay; structured-output callers
  unaffected. Tests: `test_model_client_tools.py`.
- Phase 0.3 — `MdlToolset` working-set CRUD + validate + schema tools →
  reviewable changeset (`semantic_layer/copilot/tools.py`). Tests:
  `test_copilot_tools.py`.
- Phase 0.4 — schemas (`copilot/schemas.py`). Phase 0.5 — config flags
  (`config.py`: `wren_copilot_enabled`, `wren_copilot_autopilot_enabled`,
  `wren_copilot_attachment_max_chars`).
- Phase 1 (backend) — `build_workspace_tree` (`copilot/workspace.py`) +
  `GET .../workspace`. Tests: `test_copilot_service.py`, `test_copilot_api.py`.
- Phase 3 (backend) — agentic loop with engine validation + bounded correction
  (`copilot/loop.py`), service layer (`copilot/service.py`),
  `POST .../copilot` and `POST .../copilot/apply`. Tests: `test_copilot_loop.py`,
  `test_copilot_api.py`. **Note:** synchronous (returns full `Changeset` incl.
  `steps`); SSE streaming variant is deferred (see Remaining).
- Phase 5 (backend) — `build_inspector` + `GET .../copilot/inspector`, skills
  activated. Phase 6 (backend) — `attachments` accepted by the copilot request +
  truncation enforcement in the route.

**Remaining:**
- SSE streaming for `POST .../copilot/stream` (sync endpoint works; `on_step`
  sink already exists in the loop to feed it).
- Phase 2 & 4 & FE of 1/5/6 — all frontend (workspace tree, inline diagnostics,
  embedded Copilot chat, per-file diff Accept/Reject, inspector drawer,
  attachment composer). Not started.
- Phase 6 (conversation route attachments) — only the copilot request carries
  attachments today; extending `ConversationTurnRequest` is pending.
- Phase 7 — versioning/snapshot-before-deploy. Phase 8 — frontend tests + docs.

See the per-phase checklist in §8 for the authoritative item list.


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
| Activation gate | `app.py::_enforce_activation` (~1114); `mdl_files.py::_assert_activatable` (~53) | Manifest-level deep validation before draft→active. |
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
