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

# Implementation plan — MDL Copilot ↔ AI SQL parity (persistence + multi-turn)

**Status:** ready to build. Source-audited against the working tree on 2026-06-26.
**Spec:** `plan_copilot_parity_spec.md` (File 2). **Builds on:** `plan_onboarding_gating_user_flow.md` (File 1 — **landed**, see §1).
**As-built reference:** `wren_mdl_copilot.md` §AB / §AB.11.

This is both a technical specification and a sequential checklist. Each phase is
independently reviewable and leaves the tree green. Work top-to-bottom; later
phases assume earlier ones merged. File:line anchors are accurate as of the audit
date — re-confirm before editing (the files are under active change).

---

## 0. Goal

Bring the **MDL Copilot** to feature parity with the **AI SQL agent** on
*software capability* — **durable conversation persistence** and **multi-turn
memory** — and, per the user's two stated goals:

1. **Parity of features & layout.** Copilot gets the same thread lifecycle
   (persist / list / resume / rename / delete / new-chat) and the same panel shell
   as AI SQL. Justified deviations only (e.g. file attachments + changeset review
   are Copilot-specific; execution-mode is SQL-specific — see §6 deviation table).
2. **Reuse + formalize seams.** Reuse the existing conversation stack rather than
   forking it, and extract a small **agent-agnostic turn-orchestration seam** so
   the next agent we add reuses the same persistence + turn lifecycle while
   supplying its own "run a turn" function and artifact shape.

## 0.1 Locked decisions (this conversation)

| # | Decision | Rationale |
|---|---|---|
| Scope | **Option A — full parity** | User direction; matches goal 1. |
| Endpoints | **Parallel project-scoped routes** `…/projects/{id}/copilot/conversations*` | Clean separation; natural project binding + reuses existing write/readiness gating; keeps the SQL owner-history list uncontaminated. |
| Artifact model | **Generic payload + `type` discriminator** | Keeps `conversations/` agent-agnostic (goal 2); no cross-module type import; serves future agents. |
| Thread UI | **Full parity** (list/resume/rename/delete + new chat) | Goal 1: same features as AI SQL. |
| Shared store | **Reuse** `ConversationStore` + models, add `kind`/`project_id` discriminator | Spec §3; list/resume/delete come "for free". |

---

## 1. Source-audited current state (what exists vs what's missing)

### Reusable infrastructure (confirmed present)
- **Conversation store contract** — `conversations/store.py:41-111` (`ConversationStore`
  Protocol: create/list/get/update_scope/update_title/append/replace_artifact/delete).
- **In-memory impl** — `conversations/memory.py:36-179` (auto-title from first user msg,
  sequence = `len(messages)`).
- **Cross-worker impl** — `conversations/sqlalchemy_store.py:46-333` (soft-delete via
  `deleted_at`; `selectinload` eager messages+artifacts; owner-scoped on every query;
  `scope` stored as JSON, artifact stored as JSON `payload` + denormalized nullable `sql`).
- **Models (one `Base`, one DB)** — `persistence/models.py`: `AiAgentConversation:37-66`,
  `AiAgentMessage:68-98`, `AiAgentArtifact:100-123`. Migration head **`0007_document_chunks`**
  (`persistence/migrations/versions/`). Semantic-project and conversation tables share the
  same engine/Base, so a `project_id` column on conversations is a plain column (no new DB).
- **Store wiring** — `app.py:_create_conversation_store` (≈`:2655-2669`), selected by
  `AI_AGENT_CONVERSATION_STORE` (`memory`|`sqlalchemy`); `active_conversation_store` ≈`:278`.
- **SQL turn orchestration to mirror** — `ConversationGraph.run` (`conversation_graph.py:216-255`):
  `update_scope` → `append(user)` → `_invoke_graph` (history-aware) → `append(assistant)` →
  `ConversationTurnResponse`. Streaming variant `run_stream:479`. History windowing =
  `conversation.messages[-max_history_messages:]` (`conversation_graph.py:1802`,
  `_conversation_payload`).
- **Copilot loop** — `copilot/loop.py:71-211` (`run_copilot_loop`): builds
  `[system, user]` ChatMessages (`:103-106`), tool-call+correction loop, returns
  `Changeset`. **Never persists.** `copilot/service.py:100-142` (`run_copilot`) wraps it.
- **Copilot routes** — `app.py`: `/copilot` (`:1575`), `/copilot/stream` (`:1615`),
  `/copilot/apply` (`:1699`); readiness 409 via `_require_project_ready` on the two turn
  routes only (File 1 contract).
- **Frontend (File 1 landed)** — `SemanticLayerEditor/index.tsx:904-918`: `CopilotPanel`
  always-mounted with `readinessStatus`/`readinessDetail`/`onOnboard` props + bootstrap view.
  `CopilotPanel.tsx:48-63` props; `:67-71` local `TranscriptEntry`; `:98` `transcript` is
  **local React state** (lost on reload); `:157` `handleSend` calls `streamCopilot` with no
  conversation id. AI SQL reference panel: `AiAgentPanel/index.tsx` (HistoryPanel, header
  new/history/delete actions, resume via `getConversation`). API client: `AiAgentPanel/api.ts`
  (`createConversation`/`listConversations`/`getConversation`/`updateConversationTitle`/
  `deleteConversation`/`streamConversationMessage`; copilot: `streamCopilot`/`applyCopilotChangeset`/`getCopilotInspector`).

### Partial wiring already present (use it)
- **`CopilotTurnRequest.conversation_id: str | None`** already exists
  (`copilot/schemas.py:147`) — the request contract is ready; the routes ignore it today.

### Missing (this plan adds)
- No persistence of Copilot transcript (local React state only).
- No history fed into `run_copilot_loop` (single `user_message`, `loop.py:103-106`).
- No `kind`/`project_id` discriminator on conversations → store `.list()` filters by
  `owner_id` only, so copilot + SQL threads would intermix.
- No Copilot thread routes, no FE thread UI.

---

## 2. Gaps between dev intent (spec) and actual code — RESOLVE THESE

These are real frictions discovered in the audit; each has a chosen resolution.

- **G1 — `ConversationArtifact` is hard-typed to SQL.** `type: Literal["sql"]` and
  **`sql: str` is required** (`conversations/schemas.py:62-81`). A `"changeset"` artifact
  has no SQL. **Resolution (chosen artifact model):** widen `type` to `str` (keep `"sql"`
  default), make `sql: str | None = None`, add generic `payload: dict[str, Any] | None = None`.
  The SQL agent keeps setting `type="sql"` + `sql=…`; the Copilot sets `type="changeset"` +
  `payload=changeset.model_dump(mode="json")`. Backward-compatible: existing rows validate
  (the DB `sql` column is already nullable, `models.py:108`).
- **G2 — `AiAgentConversation.database_id` is NOT NULL** (`models.py:46`); `ConversationScope.database_id`
  is required (`schemas.py:53`). A Copilot thread is project-scoped, not database-scoped.
  **Resolution:** derive `database_id` from `project.default_database_id` when creating the
  scope for a Copilot thread (the project always has one — `app.py:_onboarding_context` already
  relies on this, `:1758`). Persist `project_id` as the authoritative binding; keep `database_id`
  populated for model compatibility.
- **G3 — history shape differs.** SQL injects windowed prior turns *into the prompt*
  (`_conversation_payload`). The Copilot loop builds discrete ChatMessages. **Resolution:**
  feed Copilot history as prepended `ChatMessage`s (spec §3.2) — cleaner for a tool-calling
  loop and avoids touching `mdl_copilot.md` (spec §7: no prompt change). Window with a new
  config knob mirroring `max_history_messages`.
- **G4 — past changesets must re-render on resume, but only as *history*.** A resumed thread
  must show prior proposals/diffs read-only; it must **not** re-arm the Apply button for a
  stale changeset (drafts may already be applied/diverged). **Resolution:** persist the
  changeset artifact for display; on resume render past changesets as read-only history, and
  only the *live* turn's changeset is actionable (Accept/Reject/Apply). State this in the FE
  acceptance criteria (Phase 7).
- **G5 — onboarding is NOT a chat turn** (File 1 §1). Do not persist onboarding as conversation
  messages. The thread begins at the first real Copilot turn. (No work; a guard against scope creep.)

## 3. Gaps between user expectation (same UI as AI SQL) and Copilot reality

Captured so Phase 7 builds the right shell; deviations are justified, not accidental.

| AI SQL UI element | Copilot first pass | Justification |
|---|---|---|
| Header: new / history / delete | **Yes — add all three** | Goal 1 parity. |
| HistoryPanel (list + resume + last-message preview) | **Yes** | Goal 1 parity. |
| Rename conversation | **Yes** (PATCH title) | Goal 1 parity. |
| Composer + Send/Stop | Yes (already) | — |
| **Execution-mode dropdown** (manual/read_only/auto) | **No** | Copilot proposes drafts; `apply` is the human gate. Not applicable (spec §2 row). |
| **File attachments** | **Keep** (Copilot-only) | Authoring needs source docs; SQL agent doesn't. Justified deviation. |
| **Changeset review (accept/reject/diff/apply)** | **Keep** (Copilot-only) | The Copilot's artifact *is* a reviewable changeset. Justified deviation. |
| ContextBar (db/schema/dataset chips) | Replaced by project/readiness context | Copilot is project-scoped; the editor shell already shows project context. |
| Markdown rendering of assistant text | Optional fast-follow | Parity-nice, not required for persistence. Note in Phase 7. |

---

## 4. Architecture — the seam to formalize (goal 2)

The conversation **store** is already a clean generic seam (reuse directly). The thing
that is *not* yet generic is **turn orchestration**: "load thread → window history → run
the agent → persist user + assistant (+ artifact) → assemble response." `ConversationGraph.run`
hard-codes that for SQL. Extract it.

**New module: `conversations/turns.py`** — an agent-agnostic `ConversationTurnService`:

```
class ConversationTurnService:
    def __init__(self, store: ConversationStore): ...
    def begin_turn(conversation_id, *, user_content, scope=None, owner_id) -> Conversation
        # update_scope (optional) + append(user message); returns the loaded thread
    def history_messages(conversation, *, max_messages) -> list[ChatMessage]
        # generic role/content windowing → ChatMessage[], for any tool-calling loop
    def commit_turn(conversation_id, *, assistant_content, artifacts, owner_id) -> Conversation
        # append(assistant message + artifacts)
```

- It owns *only* the store choreography — no SQL, no MDL, no graph. Both agents call it.
- The SQL `ConversationGraph` can adopt `begin_turn`/`commit_turn` opportunistically
  (low-risk refactor; **not required** for this plan — keep it additive to avoid
  destabilizing the SQL agent). The **must-do** is that the Copilot service uses it.
- Future agents: implement a `run(...)`-shaped callable returning `(assistant_text, artifacts)`
  and reuse `ConversationTurnService` + the store + the routes pattern verbatim.

This keeps "customise the exact graph construction (context, rag, etc.)" in each agent's
own loop while the persistence/turn lifecycle is shared source.

---

## 5. Phased checklist (sequential; each phase leaves the tree green)

> Convention: `[ ]` task · **Files** · **Acceptance**. Run `pre-commit run --all-files`
> and the named tests at the end of every phase. Backend tests: `pytest tests/unit_tests/superset_ai_agent/`.
> FE: `npm run test -- <file>`.

### Phase 1 — Schema: generic artifact + conversation discriminator
- [ ] **1.1** Widen `ConversationArtifact` (G1): `type: str = "sql"`; `sql: str | None = None`;
      add `payload: dict[str, Any] | None = None`. Keep all existing SQL fields.
      **File:** `conversations/schemas.py:62-81`.
- [ ] **1.2** Add discriminator to `Conversation` + `ConversationSummary`: `kind: str = "sql"`
      (default keeps existing SQL threads valid) and `project_id: str | None = None`.
      **File:** `conversations/schemas.py:94-117`.
- [ ] **1.3** Add `kind`/`project_id` to `ConversationSummary` and surface in list responses.
- [ ] **Acceptance:** existing SQL schema round-trips unchanged; a `changeset` artifact with
      `sql=None`+`payload={…}` validates. **Test:** `pytest -k conversation_schemas` (add if absent).

### Phase 2 — Persistence model + Alembic migration
- [ ] **2.1** Add columns to `AiAgentConversation`: `kind = Column(String(32), nullable=False,
      server_default="sql", index=True)`, `project_id = Column(String(36), nullable=True, index=True)`.
      **File:** `persistence/models.py:37-66`. (Plain column — no FK, conversations outlive projects.)
- [ ] **2.2** New migration `0008_conversation_kind_project.py` (down_revision `0007_document_chunks`):
      `op.add_column` both, create indexes, **backfill `kind='sql'` for existing rows**
      (server_default covers new + existing). Use `superset.migrations.shared.utils` helpers per CLAUDE.md
      (note: this module has its own alembic under `persistence/migrations/` — match the existing
      migration style there, e.g. `0007_document_chunks.py`). Clean `downgrade` dropping indexes+columns.
      **File:** `persistence/migrations/versions/0008_conversation_kind_project.py`.
- [ ] **Acceptance:** `upgrade` then `downgrade` runs clean on sqlite; existing rows read back as
      `kind="sql"`. **Test:** migration round-trip (mirror any existing migration test, else manual via
      `run_migrations`).

### Phase 3 — Store: persist + filter by kind/project
- [ ] **3.1** Extend `ConversationStore.create` signature with `kind: str = "sql"`,
      `project_id: str | None = None`; extend `list` with optional `kind: str | None = None`,
      `project_id: str | None = None` filters. **File:** `conversations/store.py:44-57`.
- [ ] **3.2** `InMemoryConversationStore`: honor `kind`/`project_id` on create; filter in `list`;
      persist generic artifact `payload`. **File:** `conversations/memory.py`.
- [ ] **3.3** `SqlAlchemyConversationStore`: write `kind`/`project_id` in `create`; filter in `list`
      (`AiAgentConversation.kind == kind` / `.project_id == project_id` when provided); map
      `kind`/`project_id` in `_conversation_from_model`; persist+read generic artifact `payload`
      (it already stores the full artifact as JSON — confirm `payload`/`sql=None` round-trips).
      **File:** `conversations/sqlalchemy_store.py:52-75, 77-100, 155-197, 281-306`.
- [ ] **Acceptance:** create a `kind="copilot"`+`project_id` thread; `list(kind="copilot", project_id=…)`
      returns it and **excludes** SQL threads, and vice-versa; a `changeset` artifact round-trips
      cross-worker. **Test:** extend `tests/unit_tests/superset_ai_agent/` store tests (both impls).

### Phase 4 — Turn-orchestration seam
- [ ] **4.1** Add `conversations/turns.py` with `ConversationTurnService` (§4): `begin_turn`,
      `history_messages` (windowed role/content→`ChatMessage`), `commit_turn`. Pure store
      choreography; depends only on `conversations/` + `llm/base.ChatMessage`.
- [ ] **4.2** Add config knob `copilot_max_history_messages` (mirror `max_history_messages`)
      in `config.py`; default e.g. 8. (Open question §5.4 → mirror SQL default.)
- [ ] **Acceptance:** unit test `history_messages` windows correctly and emits `ChatMessage`
      role/content in order. **Test:** new `test_conversation_turns.py`.

### Phase 5 — Multi-turn into the Copilot loop
- [ ] **5.1** Add `history: list[ChatMessage] | None = None` to `run_copilot_loop`; prepend after
      system prompt, before the new user turn (`messages = [system, *history, user]`).
      **File:** `copilot/loop.py:71-106`.
- [ ] **5.2** Thread `history` through `run_copilot` (`copilot/service.py:100-142`).
- [ ] **5.3** Persist the changeset as an artifact: build `ConversationArtifact(type="changeset",
      payload=changeset.model_dump(mode="json"))` for `commit_turn`. (Keep `apply` separate — G4:
      persistence of the *conversation* is independent of persisting *drafts*.)
- [ ] **Acceptance:** `run_copilot_loop` with `history=[user A, assistant A]` includes both in the
      assembled `messages`; a follow-up turn can reference the prior turn. **Test:**
      `test_copilot_service.py` / `test_copilot_loop` (assert assembled messages contain history).

### Phase 6 — Routes: project-scoped Copilot conversations
- [ ] **6.1** Add parallel routes mirroring the SQL conversation surface, all behind
      `_require_copilot_enabled()` + `authorize_semantic_project(..., permission="write")`:
      - `POST   …/projects/{id}/copilot/conversations` → create (kind="copilot", project_id,
        scope derived from `project.default_database_id` per G2).
      - `GET    …/projects/{id}/copilot/conversations` → `list(kind="copilot", project_id=id)`.
      - `GET    …/projects/{id}/copilot/conversations/{cid}` → get.
      - `PATCH  …/projects/{id}/copilot/conversations/{cid}` → rename.
      - `DELETE …/projects/{id}/copilot/conversations/{cid}` → delete.
      **File:** `app.py` (near existing copilot routes `:1575-1723`). Reuse `ConversationNotFoundError`→404.
- [ ] **6.2** Wire `conversation_id` into `/copilot` and `/copilot/stream` (the field already exists,
      `copilot/schemas.py:147`): when present, `begin_turn` (append user), load history via
      `ConversationTurnService.history_messages`, pass to `run_copilot`, then `commit_turn`
      (append assistant + changeset artifact). When absent, behave as today (stateless) for
      backward compatibility. Keep `_require_project_ready` 409 gate (File 1). **File:** `app.py:1575-1693`.
- [ ] **6.3** Keep `/copilot/apply` unchanged (drafts gate). Optionally stamp applied state back
      onto the stored changeset artifact via `replace_artifact` (nice-to-have; not required).
- [ ] **Acceptance:** a turn with `conversation_id` appends user+assistant+changeset artifact to the
      thread; "new chat" = new thread; resume returns full transcript incl. past changeset; list is
      project+owner scoped; missing thread → 404; premature turn → 409. **Test:** `test_copilot_api.py`.

### Phase 7 — Frontend: thread-backed CopilotPanel (full parity)
- [ ] **7.1** API client: add `createCopilotConversation(projectId, scope?)`,
      `listCopilotConversations(projectId)`, `getCopilotConversation(projectId, cid)`,
      `updateCopilotConversationTitle(projectId, cid, title)`, `deleteCopilotConversation(projectId, cid)`;
      add `conversation_id` to the `streamCopilot`/`runCopilot` payloads. Mirror the SQL client
      shapes. **File:** `AiAgentPanel/api.ts` (+ `api.test.ts`).
- [ ] **7.2** `CopilotPanel`: replace local `TranscriptEntry[]` (`CopilotPanel.tsx:67-71,98`) with a
      thread-backed model: hold `conversationId` + load messages on mount/resume; on send, ensure a
      conversation (create if none) then stream with `conversation_id`; on complete, the assistant
      message + changeset are already persisted server-side (re-fetch or append locally).
- [ ] **7.3** Header parity: add **New chat** (create thread + clear), **History** toggle, **Delete**
      (mirror `AiAgentPanel/index.tsx` header). Add a **HistoryPanel** listing
      `listCopilotConversations` with resume on click + rename. Keep Coverage/Inspector buttons
      (gated by `readinessStatus==='ready'`, File 1 F4).
- [ ] **7.4** Resume rendering (G4): render past changesets **read-only** (diffs visible, no
      Accept/Reject/Apply); only the live turn's changeset is actionable.
- [ ] **7.5** Keep file-attachments + changeset review (justified deviations, §3). Do **not** add an
      execution-mode dropdown.
- [ ] **Acceptance (RTL, `CopilotPanel.test.tsx` + `index.test.tsx`):** transcript survives reload
      (loaded from API); "New chat" yields empty thread + new id; resume re-renders prior messages +
      last changeset (read-only); rename/delete work; history list is project-scoped; bootstrap/readiness
      gating (File 1) still holds.

### Phase 8 — Docs + as-built
- [ ] **8.1** Update `wren_mdl_copilot.md` §AB (and §AB.11 note: prompts unchanged, history is additive
      context). Record the new routes, the `kind`/`project_id` columns, and the `ConversationTurnService` seam.
- [ ] **8.2** Note the migration in this module's migration log if one exists; mention any
      `WREN_COPILOT_*` / `copilot_max_history_messages` config in `.env.example`.

---

## 6. Test matrix (consolidated)

- **Store** (Phase 3): kind/project filtering isolates SQL vs Copilot; changeset artifact
  round-trips in both `InMemory` and `SqlAlchemy`; soft-delete still works.
- **Turns** (Phase 4): `history_messages` windows + orders correctly.
- **Loop** (Phase 5): history present in assembled messages; follow-up references prior turn.
- **API** (Phase 6): conversation_id turn appends 3 records; new-chat distinct thread; 404/409 paths;
  list project+owner scoped.
- **Migration** (Phase 2): upgrade/downgrade clean; existing rows backfill `kind="sql"`.
- **FE** (Phase 7): reload-survival; new-chat; resume (incl. read-only past changeset); rename; delete;
  readiness gating intact.

## 7. Risks & sequencing notes
- **Shared stack — don't break SQL.** `kind`/`project_id` default to `"sql"`/`NULL`; all new store
  params are keyword-optional. SQL routes call `list`/`create` with no kind → must keep returning SQL
  threads. Add a regression test asserting SQL `list()` excludes copilot threads (Phase 3).
- **Refactor restraint.** Adopting `ConversationTurnService` inside `ConversationGraph` is optional and
  should land as a *separate* follow-up PR if at all — keep this plan additive to the SQL agent.
- **Build order.** Phases 1→2→3 are backend-foundational and must merge before 5/6. Phase 4 can land
  with 3. Phase 7 needs 6. Do not start FE before the routes exist.
- **No prompt changes** (spec §7): verify `mdl_copilot.md` still reads correctly with prior turns
  present, but do not edit it.
