# Conversation Management — Implementation Checklist

Status: SHIPPED — all phases (1a–1d + Phase 2) implemented and verified 2026-07-09.
Residual risks and expectation gaps: see §Final report at the bottom.
Spec: [plan_conversation_management_spec.md](plan_conversation_management_spec.md) — all DPs
taken at their recommended option by user sign-off ("proceed with full implementation as
proposed; prefer Claude then ChatGPT implementations").

UX precedence order (user directive): Claude (Claude.ai / Claude Code) patterns first,
ChatGPT second. Concretely: rewind-then-re-prompt edit semantics with explicit
side-effect warnings (Claude Code), regenerate keeps prior attempts retrievable
(Claude.ai pager storage), fork named "Branch from here" (ChatGPT term is the one users
know; Claude has no equivalent surface).

## Design deltas vs spec (recorded, not drift)

- **D-1 Rewrite rides the turn request, not new turn endpoints.** `rewrite_from_message_id`
  is a new optional field on `ConversationTurnRequest` and `CopilotTurnRequest`; when set,
  the turn truncates from that (user) message before appending. This reuses both agents'
  existing non-stream + SSE routes unchanged (edit and regenerate = same primitive =
  Claude Code's rewind-then-re-prompt). Dedicated endpoints remain for preview
  (`GET .../rewrite-preview`), undo (`POST .../rewrites/{message_id}/undo`) and fork
  (`POST .../fork`).
- **D-2 Regenerate ≡ edit-with-unchanged-content.** The frontend resolves the preceding
  user message and sends its content + `rewrite_from_message_id`. No separate regenerate
  endpoint; the old assistant attempt is retained soft-deleted (pager storage from day 1).
- **D-3 One migration (0021)** carries all schema: message soft-delete/supersede,
  conversation fork back-links, NL→SQL example source provenance, message feedback table,
  MDL apply snapshot (before-image) table.
- **D-4 Truncated-batch marker**: every row soft-deleted by one rewrite gets
  `superseded_by_message_id = <anchor user message id>` (the anchor marks itself). Undo =
  soft-delete live rows with `sequence >= anchor.sequence`, restore the batch. Attempts
  pager = soft-deleted assistant rows whose marker is the live turn's user message id.

## Phase 1a — substrate

- [COMPLETE] 1a.1 Migration `0021_conversation_rewrites.py` (messages soft-delete +
      supersede marker; conversation fork back-links; NL→SQL source provenance;
      `ai_agent_message_feedback`; `ai_agent_mdl_apply_snapshots` incl. `message_id`
      attribution). Models updated. Evidence: migration walk green
      (`test_migration_version_table.py` + `test_persistence_database.py`, 9 passed).
- [COMPLETE] 1a.2 Store `truncate_from`/`undo_truncate`/`fork`/`list_attempts`, max+1
      sequence, live-row filters (`sqlalchemy_store.py`, `memory.py`, `store.py`);
      `Conversation.parent_conversation_id`/`forked_from_sequence`;
      `ConversationArtifact.inert`. Evidence: `test_conversation_rewrites.py` (31 passed,
      both stores parametrized; incl. R1 sequence-collision regression + undone-turn
      non-resurrection).
- [COMPLETE] 1a.3 Memory provenance + `delete_by_source`/`count_by_source` across
      Null/InMemory/SqlAlchemy/LanceDb (`memory_store.py`); dedup-refresh adopts the new
      source turn. Evidence: same suite.
- [COMPLETE] 1a.4 `conversations/rewrites.py` manifest + `GET .../rewrite-preview`
      (SQL + copilot twins in `app.py`); apply route records before-image snapshots with
      "Applied N drafts." message attribution (`copilot/service.py snapshot_sink`,
      `apply_snapshots.py`); legacy applies → `unknown_applies`. Evidence:
      `test_conversation_management_api.py` (10 passed) + preview unit tests.
- [COMPLETE] 1a.5 Backend tests: 41 passed (2026-07-09) —
      `test_conversation_rewrites.py` + `test_conversation_management_api.py`; prior
      suites (`test_conversation_store/sqlalchemy_store/turns/memory_store`, copilot
      service/api) still green (126 passed).

## Phase 1b — regenerate

- [COMPLETE] 1b.1 `rewrite_from_message_id`/`remove_learned_examples` on
      `ConversationTurnRequest`; `ConversationGraph.prepare_rewrite` (truncate + DP-2
      memory cleanup, fail-open) consumed by `run`/`run_stream` and pre-stream by the SSE
      route; `user_message_id` threaded into state; `store_confirmed` carries provenance.
      Evidence: `test_conversation_graph.py` +7 rewrite tests (31 passed) — truncate-
      then-rerun, regenerate-in-place, provenance, DP-2 delete + opt-out, stream path.
- [COMPLETE] 1b.2 `rewrite_from_message_id` on `CopilotTurnRequest`; truncation in
      `_copilot_thread_turn` preflight (404 unknown anchor / 409 assistant anchor).
      Evidence: `test_conversation_management_api.py` copilot rewrite tests (12 passed).
- [COMPLETE] 1b.3 In-flight guard recorded as client-side responsibility (abort before
      rewrite); server truncation is atomic per call. (Documented limitation, spec DP-4.)
- [COMPLETE] 1b.4 Frontend: `onRegenerate` now consults the rewrite preview and sends a
      truncating rewrite (replaces the append behavior); regenerate on last assistant
      message in BOTH panels (`AiAgentPanel/index.tsx`, `CopilotPanel.tsx`); api.ts
      clients + types for preview/undo/fork/feedback; `BranchesOutlined`/`UndoOutlined`
      added to the core icon set.
- [COMPLETE] 1b.5 Frontend tests: `index.test.tsx` 20 passed (incl. rewrite-anchored
      regenerate), `CopilotPanel.test.tsx` 32 passed (incl. rewrite regenerate + undo
      bar), `api.test.ts` green; `npm run type` → 0 errors (2026-07-09; required a
      `tsc -b packages/superset-ui-core` declaration rebuild + a pre-existing
      `AiAgentPrompts.test.tsx` fetch-mock option fix).

## Phase 1c — edit & resend

- [COMPLETE] 1c.1 Inline editor on user messages (pencil → textarea → Save & resend /
      Cancel, Enter submits, Esc cancels) in both panels.
- [COMPLETE] 1c.2 `RewriteConfirmModal.tsx` (shared): renders the manifest verbatim —
      applied items list, unknown-applies warning, "Remove learned examples" checkbox
      (default ON, SQL only), "Branch instead"; skipped entirely on an empty manifest.
- [COMPLETE] 1c.3 Undo: `POST .../rewrites/{message_id}/undo` (SQL + copilot twins) +
      persistent Undo bar until the next turn (both panels).
- [COMPLETE] 1c.4 Tests: undo endpoint (`test_conversation_management_api.py`), editor +
      dialog + undo jest (`index.test.tsx` "edits a user message…", "non-empty rewrite
      manifest…"; `CopilotPanel.test.tsx` "rewrite with applied drafts…").

## Phase 1d — fork

- [COMPLETE] 1d.1 `POST /agent/conversations/{id}/fork` + copilot twin (kind guards; the
      copilot twin 404s recovery threads). Copies live messages ≤ anchor + artifacts,
      scope, project pin; back-link columns; title "<title> (branch)".
- [COMPLETE] 1d.2 `changeset_from_conversation` skips `inert` artifacts + apply route
      409s a fork whose only changeset is a copy (double-apply guard). Evidence:
      `test_copilot_fork_marks_changeset_inert_and_blocks_apply`.
- [COMPLETE] 1d.3 Frontend: "Branch from here" per-message action (both panels, both
      roles); "Branched — open original" back-link (header/badge row); forked past
      changesets render read-only (existing read-only history path).
- [COMPLETE] 1d.4 Tests: backend fork tests (rewrites + API suites); jest branch tests in
      both panels.

## Phase 2

- [COMPLETE] 2.1 Apply snapshots recorded at apply time (`snapshot_sink` in
      `apply_changeset_items`; `apply_snapshots.py` store + `message_id` attribution);
      `GET .../applies` (grouped history) + `POST .../applies/{group}/revert`
      (`revert_apply_group` in `copilot/service.py`: drafts revert; activated /
      since-edited files excluded + named; provenance `mdl_agent_edit` event; "Reverted
      N applied drafts." turn; coverage reschedule; 409 on double-revert). UI: "Revert"
      on Applied turns + "also revert" checkbox in the rewrite dialog (CopilotPanel).
      Evidence: revert unit tests (update/activated/edited/delete cases), API round-trip
      test (`test_copilot_apply_records_snapshots_and_revert_restores`), jest revert test.
- [COMPLETE] 2.2 Attempt pager: `GET .../messages/{id}/attempts` + `< n/m >` pager on the
      last assistant turn in `AiAgentPanel` (view prior attempts read-only). Delta vs
      spec: pager UI is AiAgentPanel-only; the Copilot stores attempts (same substrate)
      but has no pager yet (recorded gap G-3).
- [COMPLETE] 2.3 Feedback persistence: `ai_agent_message_feedback` + upsert endpoint;
      the AiAgentPanel thumbs now persist (fire-and-forget). Copilot gains per-message
      copy (parity); thumbs remain SQL-panel-only (gap G-4).

## Final

- [COMPLETE] F.1 Verification (2026-07-09): backend
      `tests/unit_tests/superset_ai_agent/` → 1549 passed, 13 skipped, 1 failed —
      the failure (`test_bulk_activate_fetches_live_schema_once_and_deactivate_zero`)
      reproduces on clean HEAD 67a205e383 (pre-existing, introduced by the
      "Speed improvement" commit's caching; not this feature). Frontend: 39 suites /
      393 tests passed under `src/SqlLab/components/AiAgentPanel`; `npm run type` → 0
      errors. Pre-commit on touched files: all hooks pass except (a) gitleaks env
      install blocked by local SSL cert (skipped), (b) oxlint missing its darwin-arm64
      native binding (local npm optional-deps defect, not a finding); mypy is clean on
      all files this feature authored (remaining mypy noise is pre-existing package-wide:
      `models.py` declarative-Base pattern, `prompts/registry.py`, engine files).
- [COMPLETE] F.2 Report below.

## Final report — residual risks & gaps

Dev-intent vs implementation:
- G-1 **No server-side in-flight guard**: a rewrite arriving while a turn is mid-stream
  is not rejected server-side (DP-4 chose client-abort-first; the UI disables all
  rewrite affordances while `isLoading`/`isRunning`). A malicious/racing client could
  truncate under a running turn; the turn's commit then appends to the truncated thread.
- G-2 **Undo is transcript-only**: learned examples deleted by the rewrite are not
  restored on undo (they re-learn on the next confirmed run), and an undo after further
  turns discards those turns (UI only offers undo until the next turn, but the endpoint
  itself does not enforce it).
- G-3 **Attempt pager is SQL-panel-only** and only for the LAST turn; storage supports
  arbitrary depth (superseded rows are marker-linked) — UI extension is cheap later.
- G-4 **Thumbs feedback UI exists only in the SQL panel**; the backend endpoint is
  agent-agnostic.
- G-5 **Revert timestamps heuristic**: "edited after apply" uses a 5s updated_at
  tolerance rather than a content checksum of the applied state; a sub-5s manual edit
  after an apply would be silently reverted.
- G-6 **LanceDB memory cache rows** for deleted examples are left inert (by design,
  same contract as eviction) — recall degrades closed but the vectors persist.
- G-7 **Legacy applies** (before this feature) surface as `unknown_applies` in the
  preview and cannot be reverted (no before-images) — stated in the dialog.

User-expectation vs actual UI:
- E-1 No `< n/m >` version arrows on *historical* (non-last) turns — ChatGPT/Claude.ai
  keep every edit branch navigable; we keep linear + fork instead (spec DP-1, deliberate).
- E-2 Fork copies context but **artifact actions in a fork**: SQL artifacts stay
  executable (replay-safe reads), copied changesets are inert; a user may expect to
  re-apply a copied proposal — the apply route explains why not (409 message).
- E-3 The conversation-history list does not visually mark branches (no tree/indent);
  only the open thread shows the "Branched — open original" back-link.
- E-4 Editing a user message whose later turns executed SQL warns about counts only;
  it does not offer to re-execute automatically after the rewrite.
- E-5 Regenerate keeps the same model (no model-switch-on-retry) — deployment-config
  model, out of scope per spec §3.6.

Deployment notes (multi-machine):
- No new env vars / feature flags. Migration `0021_conversation_rewrites` is additive +
  nullable and runs automatically where `agent_run_migrations` is on; the Windows box
  needs the usual commit → pull → rebuild (image bakes source). Old backend + new
  frontend degrades: preview/fork/undo/applies calls 404/`{}` → UI hides or errors
  per-action (fail-open guards in `refreshApplies`/`refreshAttempts`).
