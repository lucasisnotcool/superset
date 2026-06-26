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

# Proposal & feature spec — MDL Copilot ↔ AI SQL agent parity

**Status:** proposal (not implemented). Companion: `plan_onboarding_gating_user_flow.md`
(File 1, build this on top of it). Authoritative as-built record is
`wren_mdl_copilot.md` §AB and the prompt network in §AB.11.

## 0. Goal (one sentence)

Make the **MDL Copilot** (authoring agent) reach the same *software capability* as
the **AI SQL agent** (query agent) — chiefly **conversation persistence** and
**multi-turn memory** — so a user's expectations carry across both agents.

## 1. The two agents (orientation)

- **AI SQL agent** = the *consume* side: `graph.py` (`TextToSqlGraph`, one-shot
  NL→SQL) and `conversation_graph.py` (`ConversationGraph`, multi-turn chat). Reads
  the MDL; never edits it. Prompts: `text_to_sql.md`, `table_selection.md`,
  `conversation.md`, `sql_reflection.md`.
- **MDL Copilot** = the *author* side: `semantic_layer/copilot/loop.py`
  (`run_copilot_loop`, tool-calling edit loop) + `service.py` (`run_copilot`).
  Edits the MDL; proposes a reviewable `Changeset`. Prompt: `mdl_copilot.md` +
  the three skills.

Same LLM transport (`llm/base.py`); otherwise independent (see §AB / §AB.11).

## 2. Feature comparison (software capability)

| Capability | AI SQL agent | MDL Copilot (today) | Source |
|---|---|---|---|
| Backend conversation store | ✅ `ConversationStore` + `InMemoryConversationStore` + `SqlAlchemyConversationStore` (cross-worker) | ❌ none | `conversations/store.py`, `memory.py`, `sqlalchemy_store.py:46` |
| Persisted thread (id / title / messages) | ✅ `AiAgentConversation`; `Conversation`/`ConversationMessage` | ❌ transcript is local React state, lost on remount | `persistence/models.py:37`; `conversations/schemas.py:84-106`; `CopilotPanel.tsx:83` |
| **Multi-turn context fed to the model** | ✅ carries prior turns + `sql_observations`/`reflection_feedback`; "use prior assistant SQL artifacts" | ❌ `run_copilot` takes a single `user_message`; system+user only, **no history** | `conversation_graph.py`; `prompts/conversation.md`; `copilot/service.py:100`, `copilot/loop.py:103-106` |
| List / resume / rename / delete threads | ✅ `GET/POST/PATCH/DELETE /agent/conversations…` | ❌ none | `app.py:585-782` |
| Start new chat | ✅ new conversation (`POST /agent/conversations`) | ❌ ephemeral clear only | `app.py:585` |
| Streaming | ✅ `/messages/stream` (SSE) | ✅ `/copilot/stream` (SSE) — **parity** | `app.py:702`; `copilot` stream route |
| Persisted artifacts | ✅ `ConversationArtifact` (SQL / charts) | ❌ `Changeset` returned, not stored | `conversations/schemas.py:62` |
| Scope binding | ✅ `ConversationScope` (db / catalog / schema) | project-scoped requests, but no thread | `conversations/schemas.py:50` |
| Execution modes (manual/read_only/auto) | ✅ | N/A (Copilot proposes drafts; `apply` is the human gate) | — |

**Headline gap:** the Copilot is effectively a series of **independent one-shot
runs** that share only a UI transcript. The SQL agent is a **persistent, multi-turn
thread**. "Persistence" and "multi-turn memory" are the two capabilities to add.

## 3. Proposal — reuse the conversation stack for the Copilot

The conversation primitives are generic (a thread of role/content messages +
artifacts + scope). Reuse them rather than inventing a parallel store, so list /
resume / rename / delete / persistence come "for free" and the two agents share one
mental model.

### 3.1 Data model
- **Reuse** `Conversation` / `ConversationMessage` / `ConversationArtifact`
  (`conversations/schemas.py`) and the `AiAgentConversation` table
  (`persistence/models.py:37`).
- Add a **discriminator** so Copilot threads are distinguishable from SQL threads
  (e.g. `kind: "sql" | "copilot"` on the conversation, or a dedicated
  `agent`/`surface` field). Bind a Copilot thread to its **semantic project**
  (project_id) in addition to scope.
- Represent the Copilot's output as a **`ConversationArtifact`** of a new kind
  (e.g. `"changeset"`) carrying the `Changeset` JSON, so a resumed thread can
  re-render past proposals/diffs.
- **Migration:** adding a `kind`/`project_id` column or an artifact type → an
  Alembic migration under `superset/migrations/versions/` (use
  `superset.migrations.shared.utils`).

### 3.2 Multi-turn into the loop
- Add a `history: list[ChatMessage]` (or prior-turns) parameter to `run_copilot`
  (`service.py:100`) and `run_copilot_loop` (`loop.py:71`); prepend it after the
  system prompt and before the new user turn (`loop.py:103-106`).
- On each turn: load the thread's prior messages, run the loop with history,
  **persist** the new user + assistant messages and the resulting `Changeset`
  artifact. (Keep `apply` as the separate human gate — persistence of the
  *conversation* is independent of persisting *drafts*.)

### 3.3 Endpoints
Two options — pick in §5:
- **Reuse** `/agent/conversations*` with `kind="copilot"` + project binding (fewer
  routes; shared FE conversation client), or
- **Parallel** `/agent/semantic-layer/projects/{id}/copilot/conversations*` mirroring
  the conversation surface (clean separation; more routes). Either way the existing
  `/copilot` and `/copilot/stream` turn routes gain a `conversation_id`.

### 3.4 Frontend
- `CopilotPanel` becomes **thread-backed**: load messages on mount, persist on each
  turn (builds on File 1's always-mounted panel).
- **"Start new chat"** = create a new Copilot thread (not just clear local state).
- Transcript then survives **reset** (File 1) *and* **page reload** (this file).
- Optional: a thread list / resume / rename / delete UI mirroring the SQL chat
  (can be a fast-follow; persistence + new-chat is the must-have).

## 4. Scope options (the decision)
- **Option A — full parity (recommended).** Reuse the conversation stack: persisted
  threads + multi-turn history + list/resume/delete. Cost: store wiring + loop
  history param + migration + FE thread UI. Outcome: the two agents are genuinely
  equal.
- **Option B — lightweight.** Lift the transcript to durable client/project-scoped
  state (survives reset + reload) + "Start new chat", but **no** multi-turn memory
  and **no** cross-device persistence. Cost: small; closes the "lost on reset/reload"
  complaint only.

User direction in-conversation: **leaning A** ("ensure MDL copilot has persistence…
same parity"). Confirm before building.

## 5. Open questions (resolve before implementation)
1. **Reuse `Conversation*` vs new `CopilotConversation*`?** Reuse is less code and
   gives shared UX; risk is `ConversationMessage`/`ConversationArtifact` are
   somewhat SQL-shaped (artifacts assume SQL/charts). A `kind` discriminator +
   a `"changeset"` artifact type is the lightest reconciliation.
2. **Endpoints:** reuse `/agent/conversations` (kind-tagged) vs parallel
   project-scoped Copilot routes (§3.3).
3. **How much thread UI now?** Persistence + "start new chat" only, or also
   list/resume/rename/delete in the first pass?
4. **History budget:** how many prior turns to feed `run_copilot_loop` (token cost
   vs continuity) — mirror the SQL agent's windowing if any.

## 6. Tests (outline)
- Store: a Copilot thread persists messages + a `changeset` artifact; list/get/delete
  round-trip; cross-worker via `SqlAlchemyConversationStore`.
- Loop: `run_copilot_loop` with `history` includes prior turns in the assembled
  messages; a follow-up ("now also add a synonym") references the prior turn.
- API: a turn with `conversation_id` appends to the thread; "start new chat" creates
  a distinct thread.
- FE: transcript survives reload; "Start new chat" yields an empty thread; resume
  re-renders prior messages + the last changeset.
- Migration: upgrade/downgrade clean.

## 7. Dependencies & sequencing
- **Build on File 1.** File 1 makes `CopilotPanel` always-mounted + readiness-driven
  and gives an in-session transcript. File 2 replaces that in-session transcript with
  a persisted thread and adds multi-turn + real "start new chat".
- Touches the shared conversation stack — coordinate with any AI SQL agent changes
  (the stores/schemas are shared; a `kind` discriminator must not break existing SQL
  conversations: default existing rows to `kind="sql"`).
- No change to the prompt network (§AB.11): this is software/persistence, not prompts.
  `mdl_copilot.md` already assumes a draft-review loop; multi-turn history is additive
  context, not a prompt contract change (verify the base prompt still reads correctly
  with prior turns present).
