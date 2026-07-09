=# Conversation Management Spec — Edit & Resend, Regenerate, Fork (AI SQL agent + MDL Copilot)

Status: SHIPPED (2026-07-09) — all DPs taken at their recommended option; built per
[plan_conversation_management_impl.md](plan_conversation_management_impl.md) (incl.
Phase 2 revert/pager/feedback); residual gaps in that doc's Final report.
Scope: both agent surfaces (SqlLab `AiAgentPanel`, Semantic Layer `CopilotPanel`) and the shared
conversation store in `superset_ai_agent`.

This spec is source-backed on two sides:
- **Competitive research** (2025–2026 product behavior, web-verified 2026-07-08; each claim marked
  `[verified]` = 3-vote adversarial verification against primary sources, or `[reported]` =
  extracted from primary/official docs but single-source).
- **Codebase map** (file:line references against the live tree as of 2026-07-08).

---

## 1. Competitive analysis — what users expect today

### 1.1 The two industry models

Products have converged on **two distinct semantics**, chosen by whether the agent has
*workspace side effects*:

| Model | Products | Semantics |
|---|---|---|
| **A. Branch-in-place (tree)** | ChatGPT, Claude.ai, LibreChat, TypingMind | Edit/regenerate never destroys history. Each edit/retry creates a sibling version navigable with `< n/m >` arrows. Conversation is an N-ary tree rendered linearly. |
| **B. Rewind-and-rewrite (checkpointed linear log)** | VS Code Copilot Chat, Cursor, Claude Code, Windsurf Cascade | History is linear. Editing a prior message **truncates everything after it AND reverts workspace side effects** via automatic checkpoints. Branching exists only as an explicit "fork" escape hatch. |

Our agents sit in between: the chat itself is consumer-style, but the **MDL Copilot mutates
durable state** (applied drafts) like an IDE agent mutates files. The proposal below therefore
takes Model B's *checkpoint/warn/revert* discipline for side effects, with Model A's
*non-destructive fork* as the branching primitive — and keeps the thread itself linear
(soft-rewrite), which is what our append-only store supports cheaply.

### 1.2 Per-product behavior (evidence)

**ChatGPT**
- "Branch in new chat": hover message → ⋯ menu → new conversation carrying full context up to
  that point; original thread untouched; branches can be branched. Announced 2025-09-04, later on
  mobile. `[verified]` (openai.com/X announcement + multi-outlet corroboration)
- Edit-a-message historically creates in-place versions with `<1/2>` arrows (branch, not rewrite);
  a Feb-2026 web update regressed the arrows UI while branches remained server-side — evidence
  that storage and navigation affordance should be decoupled. `[reported]` (OpenAI community)
- Regenerate lives under a per-response "switch model" control — model-switch-on-retry is the
  primary retry path; attempts stack as navigable versions. `[reported]`

**Claude.ai**
- Regenerate keeps prior attempts behind a `1 of 2` pager; editing an earlier user message creates
  a new path with the original still navigable. Sources conflict on how complete this is (one
  2026-03 secondary source says edit/regenerate "wipes the subsequent thread") — i.e. even
  first-tier products are inconsistent here; the *expectation* is non-destructive. `[reported]`

**Gemini**
- Consumer Gemini: no edit-prior-message, no branching (branching code spotted in Android builds,
  2026-03). Google AI Studio: explicit "Branch from here" per-turn menu with branch switching.
  `[reported]`

**VS Code Copilot Chat** (checkpoints doc, 2026-07-01) `[verified]`
- Auto-snapshot of *affected files* before each chat request.
- **Edit a previous request ⇒ resend as new request + auto-revert file changes of that request and
  all subsequent ones.** Edit-and-resend is *coupled* to workspace rollback, not just transcript
  rewrite.
- Restore checkpoint ⇒ reverts files, undoes everything after, **removes the chat request from
  history** (rewrite, not branch) — but a **Redo** affordance makes restore itself reversible.
- Can fork a new independent conversation from a checkpoint.

**Claude Code** (checkpointing doc) `[verified]`
- Every user prompt auto-creates a checkpoint; persists across sessions; 30-day cleanup.
- `/rewind` menu **decouples the two axes**: restore *conversation only*, *code only*, or *both*.
- After restoring conversation, the original prompt is placed back in the input box for edit &
  resend (rewind-then-re-prompt, not in-place edit).
- **Explicit irreversibility boundary**: only edits via Claude's own file tools are tracked; bash
  side effects (`rm`/`mv`/`cp`), external and concurrent edits are NOT undoable — and the docs say
  so loudly. Branch-preserving alternative is a separate `--fork-session`.

**Cursor** `[verified]`
- Agent auto-checkpoints before significant changes (file-state snapshots, local, not git).
- Restore via chat-timeline click (with preview), a "Restore Checkpoint" button on prior requests,
  or hover `+`. Editing a prior message prompts whether to *also* revert code.
- Docs explicitly scope restore to files only — terminal side effects are not reverted.

**Windsurf/Devin Cascade** `[verified]`
- Hover a prior prompt → revert arrow undoes all code changes from that step onward; also
  user-named snapshots. **Reverts are documented as irreversible (no redo)** — the cautionary
  counter-example.

**LibreChat / TypingMind (power-user tier)** `[reported]` (official docs/discussions)
- LibreChat: every regenerate is implicitly a branch in an N-ary message tree; fork-from-message
  offers three scope options (visible path only / include related branches / all-to-target) +
  direction reversal. Its own users found the tree model confusing enough that the maintainer
  called it a power-user feature — **documented UX cost of exposing full tree semantics**.
- TypingMind: edit and regenerate both preserve the original as a separate thread; a "Chat Thread"
  view navigates versions; fork splits into a new chat.

**Framework layer — LangGraph time-travel** `[reported]` (langchain docs)
- Canonical pattern: fork = new checkpoint branching from a prior one; `update_state` **never
  rolls back** a thread (branch, don't rewrite).
- **Replay re-executes nodes — LLM/tool calls fire again** and may differ; side-effecting tools
  need idempotency keys; propose-then-commit (approval before side effects) is the recommended
  guard. Four named strategies for input-during-run: enqueue / reject / interrupt / rollback.
- Design-pattern catalogs (Shape of AI "Branches") prescribe: keep source links between branches,
  make branching first-class at obvious touchpoints, let branches progress independently, avoid
  destructive merges. A minimum-viable mechanics set repeatedly cited: **regenerate last, edit last
  user message (discarding tail), and one of undo/branch**.

### 1.3 Norms distilled (what "industry standard" means for us)

1. **Editing a message never silently destroys downstream side effects.** IDE agents revert them
   automatically (files) and *warn about what they can't revert* (bash). We must do the analog for
   applied MDL changesets and memory writes.
2. **Regenerate replaces, in place, the last assistant turn** — never appends a duplicate turn
   (our current `onRegenerate` gets this wrong).
3. **Fork is non-destructive, message-anchored, and copies context up to the anchor** into a new
   conversation with a back-link. Original untouched. This is the cheapest feature to meet
   expectations on and the one every product has converged on.
4. **State restore should be recoverable** (VS Code's Redo) — soft-delete, don't hard-delete.
5. **Full in-thread version trees are a power-user luxury with real UX cost** (LibreChat evidence).
   Not required to be "in line with expectations"; ChatGPT-style fork + linear thread is.
6. **Irreversibility must be explicit** — say exactly what will and won't be undone (Claude Code's
   limitations section is the model).

---

## 2. Current architecture (what the features must fit into)

Facts verified against the live tree (see file:line):

- **Persistence** — `superset_ai_agent/persistence/models.py`: `AiAgentConversation` (L40-79;
  `kind` sql|copilot, `project_id` pin L58, `scope` JSON, soft-delete) and `AiAgentMessage`
  (L82-111; `role` user|assistant only, integer `sequence` L100, ordered by
  `(conversation_id, sequence)`). `AiAgentArtifact` (L114-136) hangs off messages
  (`sql` | `changeset`). **Strictly linear, append-only; no parent/branch/version fields; tool
  calls are not persisted as messages** (`conversations/turns.py:53-55`).
- **Store** — `conversations/sqlalchemy_store.py` / protocol `conversations/store.py:44-136`:
  `create/list/get/update_scope/update_title/update_project_id/append/replace_artifact/delete`.
  **No message-level update, delete, or truncate.** `sequence` is assigned as
  `len(conversation.messages)` on append (`sqlalchemy_store.py:201`) — a truncate feature must
  change this to `max(sequence)+1` over live rows or soft-deleted rows will collide.
- **Turn execution** — SQL agent: `ConversationGraph.run/run_stream`
  (`conversation_graph.py:320-359, 587+`) reloads full thread from store each turn
  (`_load_conversation` L776-793) and windows it (`max_history_messages`). Copilot: shared
  `ConversationTurnService` (`conversations/turns.py`) `begin_turn/history_messages/commit_turn`,
  wired at `app.py:3620-3660` → `run_copilot`. **Because every turn re-reads the store, rewriting
  the store IS rewriting the agent's context — no separate agent-state checkpoint to migrate.**
- **Routes** (`app.py`) — SQL: conversations CRUD L1023-1222, `POST .../messages` L1086,
  `.../messages/stream` L1142 (SSE), `execute-sql[/stream]` L1114/L1184. Copilot: CRUD
  L3479-3590, run L3662, stream L3735, **apply L3877-3940**.
- **Side effects, SQL agent** — user SQL is read-only enforced (`tools/sql_policy.py`). The one
  hidden durable write: learning-loop memory `store_confirmed(...)`
  (`conversation_graph.py:1727-1747`) → `AiAgentNlSqlExample` rows, **DB-fingerprint-scoped, not
  conversation-scoped, no linkage back to the originating message** — rewriting history cannot
  clean them up today.
- **Side effects, MDL Copilot** — tools mutate an in-memory working copy only ("propose, don't
  persist", `semantic_layer/copilot/tools.py:18-24`); each mutating call is recorded in a per-turn
  `ToolCallRecord` ledger (`_record_mutation` L640-675; `_MUTATING_ACTIONS` L1733-1742). Durable
  writes happen only at `POST .../apply` → `apply_changeset_items`
  (`semantic_layer/copilot/service.py:186-244`): **drafts only** (activation is a separate human
  action), plus provenance events, an "Applied N drafts" assistant turn (app.py:3928), and a
  coverage reschedule (L3939). `AiAgentSemanticMdlFile.update` **overwrites content with no
  before-image** (`models.py`, soft-delete only).
- **Frontend** — SQL: `superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx` (local
  `useState`, full-conversation reload per turn L922, backend message ids used L1215, stop/cancel
  via `AbortController` L1005). Copilot: `.../SemanticLayerEditor/CopilotPanel.tsx` (local state,
  changeset review via `ChangesetReviewPanel.tsx`, active thread id in localStorage L73/L249).
- **Existing partials** — `onRegenerate` (index.tsx:1200-1211) re-sends the last user message and
  **appends** (no truncation) — wrong per §1.3(2), to be fixed by this spec. Stop-generation and
  copy already exist. No edit or fork anywhere.
- **Conversation-linked satellite state** (fork/rewrite hazards): `project_id` pin +
  `scope` JSON (refreshed every turn), coverage runs' `recovery_conversation_id`
  (`models.py:478-510`), applied-draft provenance referencing conversation ids, localStorage
  active-thread keys.

---

## 3. Proposal

Three user-visible features + the safety substrate they share. Linear thread with
**soft-rewrite + explicit fork** (per §1.1 rationale); no in-thread version tree in v1 (DP-6).

### 3.0 Substrate: message soft-delete, turn ledger, and side-effect manifest

New schema (one migration):

- `ai_agent_messages`: add `deleted_at` (nullable DateTime) and `superseded_by_message_id`
  (nullable, self-FK). Soft-deleted rows are excluded from `get()`/history windows but retained —
  restore stays possible (§1.3 norm 4) and nothing is hard-deleted.
- `ai_agent_conversations`: add `parent_conversation_id` (nullable FK) and `forked_from_sequence`
  (nullable int) — the fork back-link (§1.3 norm 3).
- `ai_agent_nl_sql_examples` (`AiAgentNlSqlExample`): add nullable `source_conversation_id` and
  `source_message_id` provenance columns, populated by `store_confirmed`. Without this, edit /
  regenerate silently double-writes memory and can *persist a learning example for an answer the
  user is rewriting because it was wrong* — the exact class of hidden side effect §1.3 norm 1
  forbids.

New store methods (protocol + sqlalchemy impl + tests):

- `truncate_after(conversation_id, sequence, *, superseded_by=None)` — soft-delete all live
  messages with `sequence >= n` (artifacts stay attached to their soft-deleted messages).
- `fork(conversation_id, up_to_sequence) -> Conversation` — copy live messages (+ artifact rows)
  with fresh ids, copy `scope`/`project_id`/`database_*`, set back-link fields.
- Fix `append` sequence assignment to `max(live_and_deleted sequence)+1` (monotonic across
  soft-deletes; keeps `(conversation_id, sequence)` unique).

**Side-effect manifest** (the "what will this rewrite touch?" computation, used by both edit and
regenerate): given a conversation and a cut sequence, return

- `applied_changesets`: for `kind=copilot|recovery` — changeset artifacts at `sequence >= cut`
  whose items were applied (walk artifacts + provenance/"Applied N drafts" turns), with per-item
  action labels from the existing `ToolCallRecord` ledger;
- `memory_writes`: `AiAgentNlSqlExample` rows with `source_message_id` in the cut range (SQL
  agent);
- `executed_sql`: informational only (reads are replay-safe).

Endpoint: `GET /agent/conversations/{id}/rewrite-preview?from_message_id=...` returning the
manifest. The frontend confirm dialog renders it verbatim — the Claude Code pattern of naming
exactly what is and isn't undoable.

### 3.1 Feature 1 — Edit & resend a user message

**UX (both panels):** hover a user message → pencil icon → message becomes an inline editor with
*Save & resend* / *Cancel* (ChatGPT/Copilot-Chat convention). On save:

1. Frontend calls `rewrite-preview`. If the manifest is empty (the common SQL-agent case): proceed
   silently — matches consumer apps.
2. If non-empty: confirmation dialog —
   - *"Resending will remove N later messages from this conversation."*
   - Copilot, applied changesets downstream: list the applied items (create/update/delete + file
     names from `ToolCallRecord`), with a choice: **Keep applied drafts** (default; they remain in
     the project, the dialog says so explicitly) or **Revert applied drafts** (Phase 2, see 3.4;
     until then the dialog offers Keep only, worded as Claude Code words its bash limitation).
   - SQL agent, memory writes downstream: checkbox *"Also remove N learned example(s) from these
     turns"*, default ON (deletes the provenance-matched `AiAgentNlSqlExample` rows).
   - Escape hatch button: **Branch instead** — runs Feature 3 from the edited message and leaves
     this conversation untouched (the Cursor "edit prompts whether to also revert" analog).
3. Backend: `POST /agent/conversations/{id}/messages/{message_id}/edit` (and copilot twin):
   `truncate_after(seq(message))` → append edited user message (`superseded_by` back-link on the
   old row) → run the normal turn (stream variant too, reusing the existing SSE path). If a run is
   in-flight, the frontend aborts it first (existing `AbortController`); backend rejects edits on
   conversations with an active run (LangGraph "reject" strategy — simplest correct choice).

Because both graphs reload history from the store every turn (§2), the truncated thread is the
*only* context the resent turn sees — no agent-side checkpoint surgery needed.

**Restore:** v1 ships *Undo last edit* (restore soft-deleted range, remove the edited message) as
a store-level operation surfaced in the dialog's success toast ("Edited — Undo"), valid until the
next turn starts. Full multi-step rewind UI is out of scope (DP-6).

### 3.2 Feature 2 — Regenerate response

**UX:** refresh icon on the **last** assistant message only (v1; deeper regenerate = edit the
later user message instead, which is the same operation).

Backend: `POST .../messages/{assistant_message_id}/regenerate`: soft-delete that assistant message
(and its artifacts' visibility with it), re-run the turn from the preceding user message
**without re-appending it**. This replaces today's append-based `onRegenerate`
(index.tsx:1200-1211), which pollutes the thread and double-runs `store_confirmed`.

- Memory guard: a regenerated turn passes a `regenerated=True` flag → `store_confirmed` first
  deletes any prior example row with the same `source_message_id` (needs 3.0 provenance columns) —
  replace, never accumulate.
- Copilot: regenerating a turn whose changeset was **already applied** gets the same manifest
  dialog as edit (Keep/Branch; Revert in Phase 2). Unapplied changesets are simply discarded —
  that's the propose-don't-persist design paying off.
- **Prior-attempt navigation** (`< 1/2 >` pager à la Claude.ai): the soft-deleted attempt is
  retained and `superseded_by`-linked, so the API can expose `attempts` on a message cheaply. UI
  pager is **Phase 2** (DP-5) — storage supports it from day one so we never lose data waiting on
  UI.
- Model-switch-on-retry (ChatGPT's primary retry affordance): out of scope; our model is
  deployment-config, not per-user. Noted for the future in DP-7.

### 3.3 Feature 3 — Fork conversation ("Branch from here")

**UX:** ⋯ menu on any message (user or assistant) → **Branch from here** (ChatGPT's naming — the
one users already know). Creates a new conversation titled `"<original title> (branch)"`,
containing copies of all live messages up to and including the anchor; panel switches to it; a
small chip under the header links back to the parent ("Branched from *title*", click to open).
Also add **Branch** to the conversation-list row menu (= branch from last message ≈ "duplicate").

Backend: `POST /agent/conversations/{id}/fork {message_id?}` (+ copilot twin) → `store.fork(...)`.
Rules:

- Copies: messages, artifact rows (fresh ids), `scope`, `project_id` pin, database binding.
  Non-destructive; original untouched (§1.3 norm 3).
- **Does NOT fork durable project state**: applied drafts, memory, golden queries, docs are
  DB/project-scoped by directive ("nothing is user-scoped"; memory `golden-queries-shared-memory`)
  — both branches see the same project. The branch dialog states this in one line for copilot
  conversations ("Branches share the project's files and drafts").
- Changeset artifacts copied into a fork are marked non-actionable (historical) — their
  accept/apply buttons are disabled in the fork; only changesets produced *in* the fork are
  applicable. Prevents double-apply of the same items from two branches.
- `kind="recovery"` threads: fork disabled (they're machine-linked via
  `recovery_conversation_id`; a fork would dangle).
- Fork of a fork: allowed (parent chain via `parent_conversation_id`).

### 3.4 Phase 2 — Applied-changeset revert (the real "undo tool calls")

The competitive bar for side-effect undo is file-level checkpointing (VS Code/Cursor/Claude Code).
Our analog: **apply-time before-images**.

- At `apply_changeset_items`, snapshot each touched `AiAgentSemanticMdlFile`'s prior state
  (content or "did not exist") into a new `ai_agent_mdl_apply_snapshots` table keyed by
  (conversation_id, message_id, file_id).
- "Revert applied drafts" in the edit/regenerate dialog (and a standalone "Revert this apply"
  action on the "Applied N drafts" turn) then restores before-images: created→soft-delete,
  updated→restore content, deleted→undelete. Drafts only — if a file was **activated** after
  apply, it is excluded and named in the dialog as not auto-revertible (our "bash command"
  boundary, stated explicitly per §1.3 norm 6).
- Emits provenance events (`agent_apply_reverted`) and reschedules coverage, mirroring apply.

Phase 2 is separable: Phase 1 ships with warn+keep+branch, which already meets the "warn and
don't silently destroy" bar; revert upgrades us to the IDE-agent bar.

### 3.5 Adjacent gaps to close while we're here (small, high-expectation)

- **Persist feedback**: wire the thumbs stub (index.tsx:1213-1226) to a
  `POST .../messages/{id}/feedback` writing a row — regeneration analytics want it (which turns
  get regenerated = free eval signal for the golden-queries loop).
- **Copilot parity on existing affordances**: CopilotPanel lacks per-message copy and
  regenerate entirely — bring both panels to the same affordance set (copy, stop, edit,
  regenerate, branch).
- **Cancellation consistency**: on abort mid-turn we append a cancellation message
  (`conversation_graph.py:689-698`); edit/regenerate must treat that as a normal truncatable
  assistant turn (it already is one — just covered by tests).

### 3.6 Explicitly out of scope (v1)

- In-thread N-ary version tree + tree navigator (LibreChat evidence: high UX cost, not required
  to meet expectations — fork covers the job).
- Merging branches back (no mainstream product does it).
- Model-switch-on-retry; per-message model attribution.
- Forking across `kind` (sql↔copilot) or across projects/databases.
- Editing assistant messages (no mainstream product allows it).

---

## 4. Decision points (recommendations bolded)

- **DP-1 Edit semantics**: **soft-rewrite in place (truncate tail, restorable) with "Branch
  instead" escape hatch** vs always-branch-on-edit (ChatGPT-style tree). Rationale: linear store,
  IDE-agent precedent for side-effectful agents, tree UX cost.
- **DP-2 Memory cleanup on edit/regenerate**: **default-ON deletion of provenance-matched
  `AiAgentNlSqlExample` rows in the truncated range** vs leave-in-place. Requires the provenance
  columns either way.
- **DP-3 Copilot applied-changeset handling in v1**: **warn + keep + Branch-instead (Phase 1),
  before-image revert (Phase 2)** vs blocking edit when applies exist downstream vs shipping
  revert in v1.
- **DP-4 Concurrency**: **reject edit/regenerate/fork while a run is in-flight (client aborts
  first)** vs queue/interrupt semantics.
- **DP-5 Prior-attempt pager**: storage from day one, **UI in Phase 2** vs UI in v1.
- **DP-6 Undo-the-edit**: **single-step undo toast until next turn** vs full rewind menu
  (Claude-Code-style) vs none.
- **DP-7 Regenerate placement**: **last assistant message only** vs any assistant message
  (any-message regenerate ≡ edit-resend of the following user turn; offering both invites
  confusion).

## 5. Risks & mitigations

- **R1 Sequence collisions after truncate** — `append` uses `len(messages)`
  (`sqlalchemy_store.py:201`); with soft-deleted rows this collides on the unique index. Fix in
  the same change as `truncate_after` (max+1 over all rows); regression test.
- **R2 Double-apply from forks** — mitigated by marking copied changeset artifacts
  non-actionable (3.3); test that apply on a forked historical changeset 409s.
- **R3 History-window correctness** — `history_messages` (turns.py:86-109) and
  `_load_conversation` must filter `deleted_at IS NULL`; a missed filter silently feeds ghost
  turns to the model. Central filter in the store's row→schema mapping, not in callers.
- **R4 Recovery-thread invariants** — coverage runs point at conversations by id
  (`recovery_conversation_id`); edit/fork disabled on `kind="recovery"` (3.3) and truncation of a
  recovery thread's changeset turn re-derives `changeset_from_conversation` correctly (test).
- **R5 Cross-machine drift** — Windows box may lag this schema; migration is additive+nullable
  only, and the manifest endpoint degrades to "unknown side effects" if columns are missing.
  Standard `.env`/rebuild flow applies (no new env vars expected).
- **R6 SSE mid-stream edit races** — reject-while-running (DP-4) plus a store-level guard
  (truncate refuses if a turn holds the conversation lock).

## 6. Phasing (resumable checklist skeleton — becomes plan_conversation_management_impl.md on sign-off)

- **Phase 1a (substrate)**: migration (message soft-delete/supersede, conversation fork fields,
  memory provenance), store `truncate_after`/`fork`/sequence fix, live-row filters, manifest
  computation + `rewrite-preview` endpoint. Backend tests.
- **Phase 1b (regenerate)**: regenerate endpoints (both agents) + memory replace-guard; frontend
  fix of `onRegenerate` in AiAgentPanel; add regenerate to CopilotPanel. Tests.
- **Phase 1c (edit & resend)**: edit endpoints + inline editor + manifest confirm dialog + undo
  toast, both panels. Tests.
- **Phase 1d (fork)**: fork endpoints + "Branch from here" menu + back-link chip + non-actionable
  copied changesets + recovery-thread guard. Tests.
- **Phase 2**: apply-time before-images + revert path + revert provenance; prior-attempt pager UI;
  feedback persistence.

Per fork-workflow: every phase item lands with pytest (`tests/unit_tests/superset_ai_agent/`) +
jest + `npx tsc` on touched files before being marked `[COMPLETE]`, and this doc gains evidence
links as items complete.

---

## Appendix A — Research sources

Verified primary sources (3-vote adversarial verification, fetched live 2026-07-08):
- OpenAI branching announcement: x.com/OpenAI/status/1963697012014215181 (2025-09-04)
- VS Code Copilot checkpoints: code.visualstudio.com/docs/copilot/chat/chat-checkpoints
- Claude Code checkpointing: code.claude.com/docs/en/checkpointing
- Cursor checkpoints: cursor.com/docs/agent/chat/checkpoints
- Windsurf/Devin Cascade: docs.windsurf.com/windsurf/cascade/cascade (→ docs.devin.ai)

Reported (primary/official docs or credible secondary; single-source):
- LibreChat fork modes: librechat.ai/docs/features/fork; tree-UX discussion
  github.com/danny-avila/LibreChat/discussions/2908
- TypingMind fork/threads: docs.typingmind.com/feature-list
- LangGraph time-travel: docs.langchain.com/oss/python/langgraph/use-time-travel;
  langchain.com/blog/runtime-behind-production-deep-agents
- Shape of AI "Branches" pattern: shapeof.ai/patterns/branches
- ChatGPT edit-version arrows + 2026 regression: community.openai.com/t/1374666
- Claude.ai regenerate pager / edit branching: nodea.ai/blog/branching-ai-chat-guide;
  thepromptbench.com (conversation-mechanics analysis); PCWorld/pcworld.com Gemini branching
  coverage (2026-03)
- Google AI Studio "Branch from here": auto-post.io/blog/branch-conversations-with-gemini
- HITL propose/commit + idempotency: stackai.com human-in-the-loop design guide (2026-03)
