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

# Feature Spec: AI SQL Agent — Semantic-Layer Project Selection & Transparency

Status: **Shipped (Tier 1 + Tier 2)** · Owner: AI agent · Related: [[multi-schema-mdl]], [[mdl-lab]], [[copilot-onboarding-spec]]

## As-built (Tier 1 + Tier 2)

Decisions D1–D4 implemented as recommended; Tier 3 (governed default) deferred per D2.

Backend
- `ConversationScope.project_id` + `AgentQueryRequest.project_id` added.
- `materialize_request_semantic_project(..., project_id=None)` selects the pin
  from the already access/schema-filtered `store.list` set (R1+R2+R3 in one
  check) and returns `(project, materialization, warnings)`; an unavailable pin
  degrades to the heuristic with a surfaced warning.
- `ConversationGraph._resolve_semantic_grounding` applies the order
  explicit→pinned→heuristic and pins the resolved id via the new
  `ConversationStore.update_project_id` (in-memory + SQLAlchemy). `TextToSqlGraph`
  honors `request.project_id` (no conversation to pin).

Frontend
- `ConversationScope.project_id` threaded through `buildConversationScope`/`currentScope`.
- ContextBar shows a project `Select` when >1 project covers the schema, else a
  `SemanticLayerStateBadge` reading `Semantic layer: <name>`. Selection defaults to
  the most-recent match, persists via the conversation pin, and is restored on
  reopen from `Conversation.project_id`.

Tests: backend `test_wren_runtime_project_selection.py` (5), pin/stability/override
in `test_conversation_graph.py` (3), `update_project_id` store test;
frontend transparency + picker tests in `index.test.tsx` (2). Full suites green
(985 backend / 285 AiAgentPanel).

### Follow-on (dataset selector removal + switch = new chat)

- **Removed the "All datasets in scope" `DatasetSelect`** from the panel (component +
  test deleted). The turn now sends `dataset_ids: []` — the selected semantic-layer
  project's MDL already scopes the tables, so the agent grounds on the whole project
  rather than a hand-picked dataset subset. No backend change (empty `dataset_ids`
  was already "all in scope").
- **Decision (revisits D1): switching the semantic-layer project forces a fresh chat.**
  Rationale: the project defines the agent's entire vocabulary (model names,
  relationships, metrics) and is injected into every turn; the transcript is also fed
  to the model, so carrying a thread written against project A while grounding on
  project B feeds stale/invalid references — the user-initiated form of the F2 drift.
  A semantic-layer switch is a context boundary like switching databases. So an
  explicit switch on a **non-empty** thread resets the transcript (`onSelectProject`),
  shows an info toast ("Switched semantic layer to ‹name› — started a new chat. Your
  previous chat is saved in history."), and the prior thread stays in history (already
  server-persisted). A switch on an empty thread retargets silently; programmatic
  default-selection and reopen-restore go through `setSelectedProjectId` directly and
  never reset. The picker still renders only when **>1** project covers the schema
  (when exactly one, the badge shows its name — nothing to switch to).
- Tests: `index.test.tsx` adds "switching the semantic layer mid-conversation starts a
  fresh chat" (transcript resets after a switch on a non-empty thread). Full panel
  suite green (292 tests).

## 1. Problem

The **AI SQL agent** (SQL Lab chat that authors SQL — distinct from the **MDL Copilot**
that edits the semantic layer) grounds its generation in an MDL/wren semantic-layer
**project**, but the user has no way to see or choose which project is used, and the
backend's choice is non-deterministic.

### What the code actually does (source-backed)

1. The AI SQL turn carries only a **schema scope**, never a project id.
   - `ConversationScope` = `{database_id, catalog_name, schema_name, schema_names?,
     dataset_ids, query_editor_id?, current_sql?, selected_text?}` — **no `project_id`**
     (`superset-frontend/.../AiAgentPanel/api.ts:295-305`).
   - Built from the active SQL Lab tab in `buildConversationScope()`
     (`AiAgentPanel/index.tsx:492-504`).

2. The backend resolves the project from the scope **on every turn**, ignoring any
   project already pinned to the conversation.
   - `ConversationGraph._load_wren_context()` calls
     `materialize_request_semantic_project(...)` with only
     `(database_id, catalog_name, schema_name)` (`conversation_graph.py:865-873`);
     `TextToSqlGraph` does the same (`graph.py:428`).
   - That function does `projects = store.list(database_id, catalog_name, schema_name)`
     then **`project = projects[0]`** (`semantic_layer/wren_runtime.py:49-57`).
   - `store.list(...)` returns **all active projects** matching the scope, ordered
     **`updated_at DESC`** (`semantic_layer/projects.py:502-534`).

3. Multiple active projects can legitimately cover the same `(database, catalog,
   schema)`. The data model permits it by design: identity is
   `(database_uri_fingerprint, catalog_name, slug)` — "a database can hold **many named
   projects**" (`persistence/models.py:217-240`). There is **no "default/active/published"
   designation** per database — `status` is only `active`/`archived`.

4. Which project was used is computed but **not surfaced as a control**. The backend
   already returns `project_id` on `WrenContextArtifact` (`api.ts:113-135`) and persists
   `Conversation.project_id` (`api.ts:405-415`), but the UI shows only a document **count**
   via the non-interactive `SemanticLayerStateBadge` (`SemanticLayerStateBadge.tsx:49-51`).
   The frontend's own probe also blindly takes `projects[0]` (`index.tsx:758-764`).

### Why this is a genuine gap (two failures)

- **F1 — No control / no transparency.** When >1 project covers a schema, the user
  cannot choose which one grounds SQL, and cannot even tell which one was used. Industry
  norm is the opposite: the semantic model is an explicit, governed selection.
- **F2 — Non-deterministic resolution.** Because the project is re-resolved from scope
  **per turn** by `updated_at DESC`, editing *another* project that covers the same schema
  (e.g. via the MDL Copilot, or onboarding) silently re-points an in-flight SQL
  conversation at a different semantic layer between turns. The persisted
  `Conversation.project_id` is written but never read back for resolution.

Severity: **F2 is a correctness bug** (silent context switch → inconsistent/incorrect
SQL within one conversation). **F1 is a UX/governance gap.** Both are in scope here.

## 2. Industry context (what "good" looks like)

- **dbt Semantic Layer / Cube:** the agent queries **one governed semantic model** per
  data-source environment; selection is the *connection/project*, and the agent discovers
  metrics/dimensions *within* it (often over MCP). There is a single authoritative model,
  not a per-query race. (Cube, "Semantic Layer for AI Agents 2026"; dbt, "Semantic Layer
  as the Data Interface for LLMs".)
- **Wren AI (upstream of wren-core used here):** a **project = one data-source
  connection**; modeling happens once inside that project; "ask" operates within the
  selected project's models. Multiplicity-for-the-same-schema is not a normal state.
  (Wren AI docs: *Models*, *Asking Questions*.)

**Takeaway:** the cross-industry pattern is *explicit, singular, governed* semantic-model
selection. Superset's allowance of multiple active projects per schema is the anomaly that
manufactures this gap, so the fix must make selection **explicit and deterministic** while
preserving the multi-project capability the rest of the system already ships.

Sources:
- https://cube.dev/articles/semantic-layer-for-ai-agents-2026
- https://www.getdbt.com/blog/semantic-layer-as-the-data-interface-for-llms
- https://docs.getwren.ai/cp/guide/modeling/models
- https://docs.getwren.ai/cp/guide/home/ask
- https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026

## 3. Goals / Non-goals

**Goals**
- G1 Make the project that grounds AI SQL **visible** in the panel for every turn.
- G2 Let the user **explicitly choose** the project when more than one covers the scope.
- G3 Make resolution **deterministic and stable for the life of a conversation** (no
  silent per-turn switching).
- G4 Preserve **authz**: a client-supplied project id can never widen access.

**Non-goals**
- No change to how the MDL Copilot scopes projects (already explicit via `project_id`
  URLs; see R4 work in [[multi-schema-mdl]]).
- Not introducing an org-wide "default/published project" governance flag in this pass
  (tracked as Decision D2; deferrable).
- No change to wren-core / manifest compilation.

## 4. Design

Three tiers. **Recommendation: ship Tier 1 + Tier 2 together** (they share the same
plumbing); treat Tier 3 as a separate governance decision.

### Tier 1 — Deterministic resolution + transparency (must)

**Backend**
- Add an optional `project_id: str | None` to `ConversationScope` (schemas + Pydantic).
- Thread it into `materialize_request_semantic_project(..., project_id=None)`
  (`wren_runtime.py:35`). New resolution order:
  1. If `project_id` given → load that project, then **authz-check** it against the
     caller (reuse `SemanticAccessService` permission filter, `access.py:203-240`) and
     **verify it covers `schema_name`**. If it fails either check → fall back to (2) and
     attach a warning to `WrenContextArtifact.warnings`.
  2. Else if the **conversation already has `project_id`** (persisted at creation) → use
     it (subject to the same authz/coverage check). *This alone fixes F2.*
  3. Else current heuristic (`store.list(...)[0]`), and **persist the chosen
     `project_id` onto the conversation** so subsequent turns are stable.
- `ConversationGraph` / `TextToSqlGraph` pass `request.scope.project_id` through; for
  conversational turns, prefer the conversation's persisted id when scope omits one.

**Frontend**
- Make `SemanticLayerStateBadge` show the **resolved project name** (from
  `WrenContextArtifact.project_id` → project name) rather than only a doc count; render
  "No semantic layer" when none resolved. (`SemanticLayerStateBadge.tsx`.)
- Surface the grounding project on the generated-SQL artifact ("Grounded in *Project X*").

### Tier 2 — Explicit picker (should)

**Frontend**
- In the `ContextBar` (`index.tsx:1191-1247`), when the existing probe
  (`listSemanticProjects(db, catalog, schema)`, `index.tsx:749-789`) returns **>1**
  project, render a compact **project select** (default = backend's resolved pick).
  When exactly one, render it as a static chip (Tier 1 transparency). When zero, "No
  semantic layer".
- Persist the choice as **panel/conversation state** (see D3), inject into
  `buildConversationScope()` as `project_id`.
- Changing the project mid-conversation is treated like a scope change: **start a new
  conversation context** (consistent with how schema/catalog changes already reset
  grounding) — see D1.

**Backend** — already covered by Tier 1 (`project_id` honored + authz-checked).

### Tier 3 — Governed default (optional, deferrable — Decision D2)

- Add a per-`(database, catalog)` "default semantic project" designation (a nullable
  pointer or boolean flag on the project, owner/admin-settable). The Tier 1 heuristic
  step (3) prefers the default over `updated_at DESC`. Aligns with the dbt/Cube
  "one governed model" norm. Heavier: new field, migration, governance UI, RBAC for who
  may set it. **Defer** unless users ask for a sticky org-wide default.

## 5. Risks & mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|-----------|
| R1 | Client-supplied `project_id` targets a project the caller can't access → access widening | **High (security)** | Never trust client `project_id`; re-run `SemanticAccessService` permission filter (`access.py:203-240`) before use; on failure fall back + warn. Honors `visibility` (`private`/`db_access`/`custom`). |
| R2 | `project_id` doesn't cover the requested `schema_name` → grounding mismatch | Med | Verify schema membership (`AiAgentSemanticProjectSchema`, `models.py:264-288`) before use; else fall back + warn. |
| R3 | Pinned project archived/deleted between turns | Med | On load-miss, fall back to heuristic, re-persist, surface a non-blocking notice. |
| R4 | Backward compat: existing conversations have `project_id` but it was never used for resolution | Low | Tier 1 step (2) starts honoring it; pre-existing convos simply become deterministic. No migration needed. |
| R5 | Tier 3 only — who may set the default? | Med (if pursued) | Gate on project `admin` permission; out of scope unless D2 = yes. |
| R6 | Frontend probe and backend resolver diverge (both currently `[0]`) | Low | Single source of truth: frontend shows what the backend returns in `WrenContextArtifact.project_id`; the picker only *proposes*, backend *decides* + echoes back. |

## 6. Decision points

- **D1 — Pin vs per-turn switch.** *Recommend: pin per conversation.* Project is fixed at
  conversation creation (step 3 persists it); changing it in the picker starts a fresh
  conversation context, mirroring existing schema/catalog reset behavior. Rationale: kills
  F2, matches user mental model, avoids mid-thread semantic drift.
- **D2 — Introduce a governed default (Tier 3) now?** *Recommend: defer.* Tier 1+2 close
  the gap; revisit if a sticky org-wide default is requested. Low regret — Tier 1 step (3)
  is the natural insertion point later.
- **D3 — Where to persist the user's selection.** Options: (a) `Conversation.project_id`
  (already exists, server-side) — *recommended*, set on create / on explicit switch;
  (b) QueryEditor/SQL Lab tab state — rejected: project is an AI-agent concept and would
  pollute SQL Lab core. Use (a); panel keeps only the transient picker value until the
  next turn pins it.
- **D4 — Behavior when zero projects cover the scope.** Keep today's behavior (no wren
  context, SQL falls back to raw schema), but make the badge explicit ("No semantic
  layer") instead of silent.

## 7. Alignment (dev intent ↔ spec ↔ user flow)

- **Dev intent:** the grounding project must be *explicitly resolvable, authz-checked, and
  stable within a conversation*; the client may *propose* but the server *decides* and
  *echoes back* the project actually used.
- **Feature spec:** `project_id` becomes a first-class (optional) part of scope +
  conversation; resolution order = explicit → pinned → heuristic(+persist); every response
  reports the project used.
- **User flow:** User picks database/schema in SQL Lab → AI panel shows
  "Semantic layer: *Project X*" (a dropdown if several exist) → asks a question → generated
  SQL shows it was grounded in *Project X* → switching the dropdown starts a fresh
  grounded context. No invisible choices, no mid-thread drift.

## 8. Entry points & touchpoints (implementation map)

Backend
- `superset_ai_agent/semantic_layer/schemas.py` — add `project_id` to `ConversationScope`
  (and `AgentQueryRequest`).
- `superset_ai_agent/semantic_layer/wren_runtime.py:35-64` — accept + honor `project_id`;
  add authz + schema-coverage checks; fall-back-with-warning.
- `superset_ai_agent/semantic_layer/access.py:203-240` — reuse for the authz check.
- `superset_ai_agent/conversation_graph.py:865-873` & `graph.py:428` — pass
  `project_id` (prefer persisted conversation id for conversational turns); persist the
  resolved id on the conversation when first chosen.
- `superset_ai_agent/semantic_layer/projects.py:502-534` — (Tier 3 only) default-aware
  ordering.

Frontend
- `AiAgentPanel/api.ts:295-305` — add `project_id?` to `ConversationScope`.
- `AiAgentPanel/index.tsx:492-504, 749-789, 1191-1247` — picker in ContextBar, inject
  `project_id` into scope, persist selection (D3).
- `AiAgentPanel/SemanticLayerStateBadge.tsx:49-51` — show resolved project name.

## 9. Test plan

Backend (pytest)
- Explicit `project_id` honored when authorized + covers schema.
- Unauthorized `project_id` → fall back + warning (no access widening). **(R1 regression)**
- `project_id` not covering schema → fall back + warning. **(R2)**
- No `project_id`, conversation has pinned id → uses pinned, not `updated_at[0]`. **(F2)**
- No id anywhere → heuristic picks, then persists onto conversation; second turn reuses it.
- Archived pinned project → fall back + notice. **(R3)**

Frontend (Jest/RTL)
- >1 project → picker renders, defaults to resolved pick, switching injects `project_id`.
- exactly 1 → static chip with project name.
- 0 → "No semantic layer".
- Generated-SQL artifact shows grounding project name.

## 10. Recommendation

Ship **Tier 1 + Tier 2** as one change (shared `project_id` plumbing): it fixes the
correctness bug (F2) and the UX gap (F1), reuses fields that already exist
(`Conversation.project_id`, `WrenContextArtifact.project_id`), and needs no schema
migration. Defer **Tier 3** (governed default) to a follow-up pending the D2 decision.
