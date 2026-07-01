# Coverage Recovery Agent — Feature Spec

Status: **Draft for review** · Scope: `superset_ai_agent` MDL Copilot / Coverage
Builds on the coverage labels + progress work (`plan_coverage_labels_and_progress_spec.md`).

---

## 1. Intent (one paragraph)

When a background coverage run finds gaps (claims in the source documents the MDL
fails to capture), automatically run an **MDL-Copilot agent in "recovery" mode**:
seed it with the coverage report + recovery instructions as a *user message*, give
it the full Copilot toolset, and let it produce a **reviewable changeset** of
proposed MDL edits that close the gaps. The agent **never auto-applies** — it
stages suggestions the user reviews as a diff (per-item approve/reject) and applies
as drafts, exactly like a Copilot turn. The recovery agent's conversation is
**persisted with the coverage run** (same lifetime), and when suggestions are
ready a **persist-until-dismissed notification** plus an entrypoint on the coverage
report opens the review dialog. This is the industry-standard "found means fixed"
loop (GitHub Copilot Autofix, Sonar AI CodeFix, dbt Developer agent): auto-generate
a fix, **always gate on human review**, surface it asynchronously as a durable
artifact, never interrupt.

---

## 2. What exists today (grounding — all verified)

**The agent is a pure function.** `run_copilot(*, model_client, files,
schema_index, user_message, instructions, history, on_step, document_store,
document_index, project_id, owner_id, embedder, ...) -> Changeset`
([copilot/service.py](semantic_layer/copilot/service.py)) is FastAPI-free and
synchronous. It builds the full `MdlToolset` internally (all tools:
`write_mdl_file`, `patch_mdl_file`, `propose_*`, `run_coverage`, `search_documents`,
…) and returns a reviewable `Changeset`. **The recovery agent is just a
`run_copilot` call with the report as `user_message`.** No new agent infra.

**Conversations + changesets persist already.** `Conversation` has a `kind`
discriminator (`"sql"`/`"copilot"`) + `project_id`; a `Changeset` rides as a
`ConversationArtifact` of type `"changeset"` (`changeset_to_artifact`,
`changeset_from_conversation`). `ConversationTurnService.begin_turn/commit_turn`
seed and persist a thread. Models: `AiAgentConversation` / `AiAgentMessage` /
`AiAgentArtifact` ([persistence/models.py](persistence/models.py)).

**Apply path is reusable.** `apply_changeset_items(store, project_id, items,
owner_id)` persists accepted items as **drafts** (never activates); route
`POST …/copilot/apply` ([app.py](app.py)) + `_emit_agent_apply_provenance` records
an `mdl_agent_edit` event — which (post the labels spec) already stamps
`mdl_checksum`, so an applied-then-activated recovery edit gets a fresh coverage
label automatically.

**Coverage completion hook.** `_run_coverage_job` calls
`active_coverage_run_store.complete(run_id, report, score=…)` ([app.py:1495](app.py#L1495)),
then emits `coverage_completed`. This is the trigger point. `AiAgentCoverageRun`
([persistence/models.py](persistence/models.py)) is the run row;
`coverage_store.py` owns its lifecycle (claim lease, supersession).

**Frontend review UI exists.** `CopilotPanel.tsx` renders a `Changeset` as
per-item diffs (`ReactDiffViewer`) with accept/reject `decisions` state and an
apply button (`applyCopilotChangeset`). This is extractable into a shared
`ChangesetReviewPanel`.

**Gap: no persistent notification.** Toasts (`addSuccessToast` etc.) are
ephemeral (~5s, Redux). Nothing "persists until dismissed". Must be built — and
research says toasts are the *wrong* tool for this (NN/g: important info in a
fading toast gets missed).

---

## 3. Industry grounding (shapes every decision below)

- **Auto-fix agents never auto-apply — human review is universal.** Copilot
  Autofix, Sonar AI CodeFix, Codacy, Snyk, Cursor, dbt Developer agent all stage
  a fix for explicit approval; none touch mainline.
  <https://docs.github.com/en/code-security/responsible-use/responsible-use-autofix-code-scanning>,
  <https://docs.sonarsource.com/sonarqube-cloud/managing-your-projects/issues/fixing/>,
  <https://docs.getdbt.com/docs/dbt-ai/developer-agent>
- **Show rationale + gate on confidence; suppress low-confidence.** Autofix shows
  a natural-language explanation and *hides* suggestions that fail internal tests.
  <https://github.blog/news-insights/product-news/found-means-fixed-introducing-code-scanning-autofix-powered-by-github-copilot-and-codeql/>
- **Avoid overload.** Group, cap, threshold, draft-by-default, dashboard the rest
  (Dependabot/Renovate `groups`, `prConcurrentLimit`, draft PRs).
  <https://docs.renovatebot.com/configuration-options/>
- **Deliver async results passively, tied to a durable artifact** (a check run, a
  PR, an annotation) + an inbox you triage on your own schedule (Done/Save/Snooze)
  — never a blocking prompt.
  <https://docs.github.com/en/developers/apps/creating-ci-tests-with-the-checks-api>,
  <https://docs.gitlab.com/user/todos/>
- **Persistent notification = banner / inline, not toast.** Material banner is
  "persistent and nonmodal… remains until the user dismisses it"; Carbon inline
  notifications "do not dismiss automatically". Deep-link to the exact view.
  <https://m2.material.io/components/banners>,
  <https://carbondesignsystem.com/components/notification/usage/>,
  <https://www.nngroup.com/articles/indicators-validations-notifications/>
- **HITL governance:** pause-for-approval, per-item approve/edit/reject, show what
  changed + why, undo; irreversible/destructive actions need explicit confirm;
  rate tools by risk (read-only vs write, reversibility).
  <https://docs.langchain.com/oss/python/langchain/human-in-the-loop>,
  <https://developers.openai.com/api/docs/guides/agents/guardrails-approvals>,
  <https://www.anthropic.com/engineering/building-effective-agents>
- **Vocabulary:** "autofix"/"suggested fix" is the code-scanning register;
  "remediation" is the security register; dbt (our closest analogue) uses
  **"suggestions"** + **"approve or reject"**. → UI term: **"Coverage
  suggestions"**; actions **Apply / Dismiss**; the agent is the *coverage recovery
  agent* internally.

---

## 4. Backend design

### 4.1 Recovery run = a separate chained background job
After `store.complete(...)` in `_run_coverage_job`, if the report has gaps and the
feature is enabled, submit a **second** background job (`active_job_runner.submit`)
— do **not** run inline. Rationale: coverage results/labels must land fast
("ack-fast-then-process"); the recovery loop is another multi-step LLM run.

```
coverage complete → store.complete → coverage_completed event   (fast, unchanged)
                  └→ submit _run_recovery_job(run_id)            (chained, async)
```

`_run_recovery_job(run_id)`:
1. Re-read the run; **bail if superseded** (`should_cancel` parity) or already has
   a `recovery_conversation_id` (idempotent / claim-lease like coverage).
2. Build the recovery **user message** from the report (§4.3) + recovery
   `instructions`. Create a `kind="recovery"` conversation; `begin_turn` with the
   user message.
3. Call `run_copilot(model_client=…, files=active files, schema_index=…,
   user_message=report_text, instructions=recovery_instructions,
   document_store/index/embedder=…, project_id, owner_id, on_step=sink)`.
4. `commit_turn` the resulting `Changeset` as a `"changeset"` artifact; store
   `recovery_conversation_id` + `recovery_status` on the run row.
5. Emit a non-provenance `recovery_suggestions_ready` event
   (`{run_id, recovery_conversation_id, suggestion_count, mdl_checksum}`) — the
   badge/banner's wake signal. Only if the changeset has ≥1 item.

### 4.2 Run-row + conversation schema (migration `0013`)
- `AiAgentCoverageRun` gains: `recovery_conversation_id` (String, nullable),
  `recovery_status` (`none|pending|running|ready|failed|empty`),
  `recovery_dismissed_at` (DateTime, nullable — server-side dismissal, §4.5).
- Conversation gets a new `kind="recovery"` value (no schema change — `kind` is a
  free String). Recovery threads are excluded from the user's Copilot thread list.
- **Lifecycle:** the recovery conversation lives exactly as long as the run row
  (the run is now a durable version label, labels spec). Coverage reset / project
  delete cascades to recovery conversations (extend the existing
  `delete_project_events`/reset cleanup to also soft-delete `kind="recovery"`
  conversations for the project).

### 4.3 What the agent receives (the "user message")
A compact, structured serialization of the report focused on the **gaps**:
- Per missing/partial finding: `claim.subject`, `claim.statement`, `status`, and
  the judge's `suggestion` (the report already carries remediation hints), tagged
  with its source document filename.
- A recovery **instruction block** (system-prompt-grounded): *"Propose the minimal
  set of MDL edits that capture these missing/partial claims. Only add semantics
  the source documents support — descriptions, synonyms/aliases, metrics,
  relationships. Do not invent data. **Removals are allowed** — you may propose
  deleting or rewriting existing MDL (drop a whole file via a delete, or remove a
  model/column/metric by rewriting its file) when the documents contradict it or it
  is redundant/unsupported — but every removal must cite the claim or contradiction
  that justifies it. Cite the claim each edit closes."* Covered findings are omitted.
  - Removal mechanics (verified): the agent removes a **whole MDL file** with
    `delete_mdl_file` (→ `op="delete"`), and removes an **element inside a file**
    (a model/column/metric/relationship) with `write_mdl_file`/`patch_mdl_file`
    (→ `op="update"` with that element dropped). Both are first-class changeset
    ops the apply path already handles.

### 4.4 Apply (unchanged path, reused)
The review dialog posts accepted items + the `recovery_conversation_id` to the
existing `…/copilot/apply`. Items land as **drafts**; `_emit_agent_apply_provenance`
records an `mdl_agent_edit` (already checksum-stamped → labels). Activation stays a
separate human action → triggers a fresh coverage run → new label closes the loop.

### 4.5 Notification state (server-side, durable)
Dismissal is a **server flag** (`recovery_dismissed_at` on the run), *not*
localStorage — it must persist across reloads/devices and is per-run. A
`GET …/coverage/runs/{id}/recovery` endpoint returns
`{status, conversation_id, suggestion_count, changeset, dismissed, stale}` where
`stale = run.mdl_checksum != current active checksum`.

---

## 5. Frontend design

### 5.1 Reusable `ChangesetReviewPanel`
Extract the diff+decisions+apply block from `CopilotPanel.tsx` into a shared
component (`changeset`, `decisions`, `onDecisionsChange`, `onApply`, `actionable`,
`isApplying`). Both CopilotPanel and the recovery dialog render it. Each recovery
item additionally shows **which coverage claim it closes** + the agent rationale
(the "explain the fix" pattern).

### 5.2 `RecoverySuggestionsDialog`
A modal opened from the notification or the coverage report. Header: "N coverage
suggestions" + a **stale** notice if the MDL moved on (offer "re-run recovery").
Body: `ChangesetReviewPanel` (per-item approve/reject, validation status, diff).
Footer: **Apply selected** (drafts) / **Dismiss all**. **Removals are first-class,
proposable suggestions** — the agent may propose deleting a file or stripping a
model/column/metric, and the user approves or rejects it like any other item.
Default decision follows the existing Copilot rule **op-agnostically**: an item is
pre-accepted unless it *fails validation* (then pre-rejected) — deletes are **not**
singled out for pre-rejection. Removals stay **conspicuous** (the existing red
"Delete" op tag; for an intra-file removal, the diff shows the dropped element), so
a destructive change is obvious without being suppressed. Because every applied
item lands as a reversible **draft** (not an activation), a wrongly-approved removal
is recoverable before deploy.

### 5.3 Persistent notification (the "alert")
A **persistent, nonmodal, dismissible banner** (Material banner / Carbon inline
register — *not* a toast) rendered at the top of the Semantic Layer editor when the
latest run has `recovery_status="ready"` and `!dismissed`:

> ✨ **3 coverage suggestions ready** — the recovery agent proposed edits to close
> documentation gaps.  **[Review]**  **[Dismiss]**

- `[Review]` → `RecoverySuggestionsDialog`. `[Dismiss]` → server flag, banner gone.
- Driven by a new `recovery_suggestions_ready` entry in `COVERAGE_EVENT_TYPES`
  ([useProjectEvents.ts](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/useProjectEvents.ts))
  + the existing status poll. One banner per run; never re-notifies on reload
  (server dismissal).
- **Second entrypoint:** the coverage report viewer (`CoveragePanel`, labels spec)
  shows a "Coverage suggestions ready — Review" button when the run has
  undismissed suggestions, so the entrypoint survives a dismissed banner.

### 5.4 In-flight affordance
While `recovery_status="running"`, the coverage badge tooltip / report panel shows
"Preparing suggestions…" (reuses the progress vocabulary). Full recovery progress
streaming is deferred (the terminal "ready" signal is the industry norm — a
conditional "fix available" affordance, not a live feed).

---

## 6. User flow

1. Coverage run finishes at 60% with gaps → badge shows `Coverage 60%`.
2. Recovery job auto-runs on the report → proposes e.g. *add description to
   `orders.id`*, *add synonym "drive unit" = "patty" on `product`*, *add `revenue`
   metric*. Each tied to the claim it closes.
3. `recovery_suggestions_ready` → persistent banner: "✨ 3 coverage suggestions
   ready — [Review] [Dismiss]". The report panel also shows "Review suggestions".
4. **Review** → dialog: per-suggestion diff + "Closes: *'a drive unit is a patty'*
   (was missing)" + validation. User approves 2, rejects 1.
5. **Apply selected** → 2 drafts created (provenance `mdl_agent_edit`) → banner
   dismissed automatically.
6. User activates the drafts (separate, deliberate) → fresh coverage run → label
   rises to e.g. 85% → loop converges.
7. If the user **Dismisses** without applying, the banner is gone for good (server
   flag); suggestions remain reachable from the report panel until the run is
   superseded.

This maps the ask one-to-one: persistent dismissible notification (§5.3),
entrypoint via notification **or** report (§5.2–5.3), diff dialog of all suggested
changes (§5.2), recovery conversation captured with each report (§4.2), agent has
all Copilot tools (§4.1), report+instructions as the user message (§4.3).

---

## 7. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Agent proposes hallucinated / unjustified edits (incl. removals) | **Never auto-applies** (human review, per-item); each item carries `MdlValidationResult`; **only invalid items pre-reject (op-agnostic)** — removals are legitimate, reviewable proposals, not pre-blocked; they stay conspicuous (red delete tag / diff shows dropped element); the system prompt requires every removal to cite the contradiction/claim that justifies it; applied items are reversible **drafts**, so nothing is destroyed until a separate human activation. |
| R2 | Cost/latency: every gappy run spawns another LLM loop | Gate on `missing+partial > 0` **and** a `wren_coverage_recovery_enabled` flag; run as a **separate** job; cap `max_steps`; only the latest run; superseded runs skip. |
| R3 | Stale suggestions — MDL moved on since recovery ran | Tag recovery with the run's `mdl_checksum`; dialog shows a *stale* notice + "re-run" when current active checksum differs; supersession cancels in-flight recovery. |
| R4 | Notification fatigue | One banner per run, only when gaps exist; **server-side** dismissal (no re-notify on reload); only-core-purpose alert (NN/g). |
| R5 | Double-run / races | Claim-lease on the recovery job like coverage; `recovery_conversation_id` set once; idempotent on re-entry. |
| R6 | Apply→activate→re-audit→recover **loop** | Drafts don't trigger coverage; only human activation does. Converges as gaps close. Future guard: skip recovery if the prior recovery for the same docs was applied and gaps are unchanged. |
| R7 | Recovery-conversation bloat | Tie lifetime to the run row; cascade cleanup on reset/delete; bounded by the run-retention policy. |
| R8 | Authz | The job runs as the run's `owner_id` and **writes nothing** (produces a changeset only); apply remains user-gated through the normal `…/copilot/apply` authz (`write`). |
| R9 | edit-then-apply re-validation (LangChain caveat) | v1 is **approve/reject only** (no inline edit), matching Copilot; inline editing deferred. |

---

## 8. Decision points (with recommendations)

- **DP1 — Inline vs chained job.** **Recommend: separate chained job** — keeps
  coverage labels fast; recovery streams in after.
- **DP2 — Conversation kind.** New `kind="recovery"` vs reuse `"copilot"`.
  **Recommend: `"recovery"`** — keeps recovery threads out of the user's Copilot
  history and gives a clean cleanup scope.
- **DP3 — Dismissal persistence.** Server flag on the run vs localStorage.
  **Recommend: server-side `recovery_dismissed_at`** — durable, per-run,
  cross-device; localStorage is per-browser and violates "persists until
  dismissed".
- **DP4 — Notification surface.** Persistent banner vs pill-next-to-badge vs full
  inbox. **Recommend: persistent dismissible banner** (matches the "persistent
  notification alert" ask; Material/Carbon register) **plus** the report-panel
  entrypoint. A full notification-center is overkill for one entity per project.
- **DP5 — Auto-run gating.** Always vs only-when-gaps + flag. **Recommend:
  only-when-gaps + `wren_coverage_recovery_enabled`** (off by default until proven).
- **DP6 — Review granularity.** Per-item approve/reject (reuse Copilot) vs
  inline-editable. **Recommend: per-item approve/reject for v1**; inline edit later.
- **DP7 — Apply target.** Drafts (Copilot parity) vs direct activate. **Recommend:
  drafts** — activation stays a deliberate human step; preserves the review→activate
  governance and the coverage loop.
- **DP8 — Recovery progress UX.** Terminal "ready" only vs live stepper.
  **Recommend: terminal event + a "Preparing suggestions…" state**; live streaming
  deferred (industry treats this as a conditional "fix available" affordance).

---

## 9. Phasing
- **Phase 1 — Backend recovery job:** flag + gating, `_run_recovery_job`,
  `run_copilot` invocation, conversation persistence, run-row columns + migration
  `0013`, `recovery_suggestions_ready` event, `…/recovery` read endpoint. Tests.
- **Phase 2 — Review dialog:** extract `ChangesetReviewPanel`; build
  `RecoverySuggestionsDialog`; wire apply + per-item rationale/claim. Tests.
- **Phase 3 — Notification:** persistent banner + report-panel entrypoint +
  server-side dismissal + stale handling. Tests.
- **Phase 4 (optional):** inline-edit of suggestions; recovery progress streaming;
  re-run-recovery; "skip if unchanged" loop guard.

## 10. Out of scope
- Auto-applying or auto-activating any edit (always human-gated).
- Changing the coverage scoring algorithm.
- A general notification-center/inbox (single-entity banner suffices now).
- Inline editing of proposed content (Phase 4).

---

## 11. Implementation status — as shipped (2026-06-30)

**Phases 1–3 SHIPPED + follow-ups + tested** (backend recovery suite green;
frontend 64 green; tsc + ruff clean). **Off by default** — gated behind
`wren_coverage_recovery_enabled` (env `WREN_COVERAGE_RECOVERY_ENABLED`), itself
under `wren_copilot_enabled`. Decisions DP1–DP8 implemented as recommended.

### Backend
- **`_run_recovery_job(run_id, project, owner_id)`** ([app.py](app.py)) — a
  **separate chained job** submitted from `_run_coverage_job` after
  `store.complete(...)` when gaps exist + flag on (`# noqa: C901`). Bails if
  superseded / not `complete` / already has a `recovery_conversation_id` /
  no gaps (sets `empty`). Sets `recovery_status="running"`, seeds a
  **`kind="recovery"`** conversation with `_build_recovery_message(report)`, calls
  the pure **`run_copilot(...)`** (full `MdlToolset`), commits the `Changeset` as a
  `"changeset"` artifact, sets `ready`/`empty` + emits non-provenance
  `recovery_suggestions_ready` (only when ≥1 item).
- **`_build_recovery_message`** (module-level, unit-tested) serializes
  missing/partial findings + the judge's hint + source doc, and instructs minimal,
  source-grounded edits — **removals explicitly allowed when justified**.
- **Migration `0013_coverage_recovery`** — `recovery_conversation_id`,
  `recovery_status`, `recovery_dismissed_at` on `ai_agent_coverage_runs` (all
  nullable). `CoverageRecoveryStatus` literal. Store gains `set_recovery` +
  `dismiss_recovery` (both impls + Protocol); `_from_model` maps NULL →
  `"none"` (**back-compat: pre-feature runs read as `none`**).
- **Endpoints:** `GET …/coverage/runs/{id}/recovery` (returns
  `{status, conversation_id, suggestion_count, changeset, dismissed, stale}` —
  changeset read back via `changeset_from_conversation`; `stale` = run checksum ≠
  current active) and `POST …/recovery/dismiss` (durable `recovery_dismissed_at`).
  `/coverage/status` widened with `recovery_status`/`recovery_run_id`/
  `recovery_dismissed`.
- **Back-fill (the "pick up existing active projects" fix):** `_schedule_coverage`
  short-circuits on the idempotent `find_complete` path, so a pre-feature / failed
  run was never recovered. Now it **schedules recovery there** when
  `recovery_status in ("none","failed")` + gaps. So any trigger — incl. the manual
  **Re-run** — back-fills recovery for the current active version without a fresh
  audit. (Still **no proactive sweep** for fully-static projects; coverage is
  event-driven only — activate/update/delete/onboard/refresh.)
- **Background + UI-independent:** prod uses `ThreadJobRunner` (daemon thread);
  scheduling is from backend mutation routes, not from any UI poll. Badge/banner
  polling is display-only.

### Frontend
- **`ChangesetReviewPanel`** (new, reusable, self-contained decisions) — per-item
  diff + approve/reject + apply. **Removals are first-class**: pre-accept unless
  *invalid* (op-agnostic); deletes only made conspicuous (red tag), never
  pre-rejected.
- **`RecoverySuggestionsDialog`** — loads recovery, shows stale notice, applies
  accepted items as **drafts** (`applyCopilotChangeset`) then dismisses.
- **`RecoveryBanner`** — persistent, dismissible banner (in `index.tsx`, top of
  editor) on `recovery_status==="ready" && !dismissed`; durable server-side
  dismissal; opens the dialog. Second entrypoint: **`CoveragePanel`** report shows
  Review (ready) + **preparing (running/pending)** + **failed** recovery states.
- `recovery_suggestions_ready` added to `COVERAGE_EVENT_TYPES`.
- **Removed the on-demand "Coverage" button** from `CopilotPanel`; **deleted**
  `CoverageDialog.tsx`/`.test` (coverage is background-only now).

### Gotchas / non-obvious
- **`Alert` imports from `@apache-superset/core/components`, NOT
  `@superset-ui/core/components`** — wrong path → `undefined` element at runtime
  (cost a debugging cycle on the banner).
- **`run_copilot_loop` swallows chat errors → an empty changeset** (`recovery_status
  "empty"`), so `failed` is reached only by non-LLM exceptions. Back-fill therefore
  retries `("none","failed")` but **not `empty`** (an empty run won't differ on retry).
- Background job has **no live request** → recovery uses **`_cached_schema_index`**
  (warm cache or `None`) → structural-only validation at suggest time; the **apply
  route re-validates** against a live schema before persisting.
- `recovery` conversations use `kind="recovery"` so they stay out of the user's
  Copilot thread list.

### Known gaps / risks (deferred)
- `CopilotPanel` not yet refactored to consume `ChangesetReviewPanel` (duplicate
  render logic remains).
- Recovery conversations **not cascade-purged** on coverage reset (tied to the
  durable run row; accumulate).
- `recovery_status` `pending→running` transitions are **not pushed via SSE**
  (only the terminal `ready` event is), so the "preparing" state can lag up to the
  30s fallback poll.
- Recovery job has **no claim-lease** (minor double-run race under concurrent
  triggers; mitigated by the `set_recovery("pending")` soft guard + the job's
  `recovery_conversation_id` idempotency check).
- Deprecated `runCoverage` API client + `POST /copilot/coverage` route left in
  place (unused, harmless).

### Tests
- Backend: `test_copilot_api.py` (auto-run+surface, dismissal durable, flag-off
  no-run, **back-fill** of an unrecovered run, `_build_recovery_message`),
  `test_coverage_store.py` (`set_recovery`/`dismiss_recovery`).
- Frontend: `ChangesetReviewPanel`, `RecoverySuggestionsDialog`, `RecoveryBanner`,
  `CoveragePanel` (preparing/failed/ready/none states) test files.

---

## §12 — Post-ship fixes (2026-07-01): forced re-run, flag visibility, decoupled sweep

Three gaps surfaced once the feature ran on a real dev stack (recovery flag was
off, so the UI looked "missing"; the re-run button no-op'd; existing projects
were never picked up autonomously). All three are now addressed.

### Fix 1 — "Re-run analysis" forces a fresh audit
`_schedule_coverage` is idempotent by `(mdl_checksum, docs_checksum)`, so a manual
refresh on an unchanged MDL silently reused the stored run and the button did
nothing. Added `force: bool` to `_schedule_coverage` (bypasses the `find_complete`
short-circuit; still supersedes in-flight + creates a fresh run). `supersede()`
only touches `pending/running`, so the prior **completed** score label survives
until the new run finishes — no mid-flight wipe. `POST …/coverage/refresh` now
defaults `force=True` (the only caller is the explicit button); pass `?force=false`
for the old idempotent/back-fill behaviour. `_schedule_coverage` now returns
`bool` (created a run?) so the sweep can count work under a synchronous runner.

### Fix 2 — flag visibility (why the UI looked missing)
`wren_coverage_recovery_enabled` defaults **False** and was absent from
`.env.example`, so the dev stack never set it → no recovery jobs → banner/dialog
never rendered (working as coded, but invisible). `.env.example` now documents
`WREN_COVERAGE_AUTO_ENABLED`, `WREN_COVERAGE_RECOVERY_ENABLED=true`, and
`WREN_COVERAGE_SWEEP_INTERVAL_SECONDS`. Operators sync via `superset_ai_agent/
sync_env.ps1` (Policy 2 copies new example vars into the live `.env`), then
rebuild the ai-agent image.

### Fix 3 — autonomous, decoupled coverage + recovery sweep
New config `wren_coverage_sweep_interval_seconds` (default `0` = off, env
`WREN_COVERAGE_SWEEP_INTERVAL_SECONDS`). When > 0 a daemon thread (`coverage-
sweep`) runs one tick shortly after startup and every interval. `_run_coverage_
sweep()` runs **two independent passes**:

1. **Coverage** (gated by `wren_coverage_auto_enabled`): enumerates all projects
   via the storage layer (`project_store.list()` — db-access visibility is
   owner-agnostic, `owner_id` is audit) and calls `_schedule_coverage(…,
   recover_backfill=False)`. Idempotent guards mean only versions lacking a
   completed report get audited; recovery back-fill is suppressed here.
2. **Recovery** (gated by `wren_coverage_recovery_enabled`): new store method
   `CoverageRunStore.iter_recoverable()` returns the latest **completed** run per
   project that still has gaps, `recovery_status in (none, failed)`, and is not
   dismissed (helper `_needs_recovery`). Each run carries its own `owner_id`, so
   the project reloads without a request identity. Schedules `_run_recovery_job`
   via `functools.partial` (correct loop-variable binding).

**Decoupling:** the recovery pass reads existing completed reports directly — it
never requires the coverage pass (or any fresh run) to have produced them. The
inline coverage→recovery chain in `_run_coverage_job` is kept as a low-latency
fast-path for fresh edits; the sweep is the durable guarantee that already-active
/ legacy projects get picked up. The two passes are mutually independent and the
sweep's coverage pass does not trigger recovery.

The tick is exposed as `api.state.run_coverage_sweep()` so tests drive a single
deterministic tick (no wall-clock wait). The daemon thread is off in tests
(interval 0).

### Tests
- `test_coverage_store.py`: `iter_recoverable` selection matrix (eligible vs
  no-gap/ready/empty/dismissed/in-flight) + "latest run per project wins", both
  backends.
- `test_copilot_api.py`: `test_manual_refresh_forces_a_fresh_run`,
  `test_sweep_recovery_pass_is_decoupled_from_coverage` (auto-coverage off, yet
  recovery still fires), `test_sweep_coverage_pass_audits_an_uncovered_version`,
  `test_sweep_is_noop_when_both_passes_disabled`. Existing idempotency + back-fill
  tests updated to `?force=false` (back-fill is now the non-forced path).

### Known gaps / risks
- The sweep's recovery scheduling has no claim-lease, so a tick that overlaps the
  inline chain could double-submit; `_run_recovery_job` is idempotent on
  `recovery_conversation_id` (second submit returns early), but a tight race could
  still create two conversations. Low risk at the recommended 15-min cadence;
  a CAS lease on `recovery_status` pending→running would close it fully.
- Coverage pass enumerates **db-access-visible** projects only; a non-`db_access`
  project (not the norm in this model) would be skipped.
- Sweep interval is fixed; no jitter/backoff. Each tick re-audits nothing already
  covered (idempotent), so cost is bounded by genuinely uncovered/un-recovered
  projects.

---

## §13 — Apply-flow fix + dialog UX (2026-07-01)

Reported: clicking **Apply suggestions** errored with "Conversation not found", the
report then showed **Stale** with no re-analysis, and the suggestions opened as a
second stacked dialog with a redundant **Dismiss** button.

### Backend
- **"Conversation not found" (root cause).** `/copilot/apply` recorded the apply as
  an assistant turn via `_require_copilot_conversation`, which asserted
  `kind == "copilot"`. The recovery changeset lives on a `kind="recovery"` thread,
  so the check 404'd. `apply_changeset_items` runs *before* that check, so the
  drafts were actually created — the user saw an error for an apply that partly
  succeeded. Fix: `_require_copilot_conversation` gained a `kinds` whitelist
  (default `("copilot",)`); the apply route passes `("copilot", "recovery")`.
- **Stale with no re-analysis (root cause).** `store.update`/`delete` preserve a
  file's `active` status (status only changes when explicitly set), so applying an
  update/delete of an active file moves the active-set checksum → the report goes
  stale. But `/copilot/apply` never re-scheduled coverage (unlike the direct
  mdl-file endpoints). Fix: the apply route now calls `_schedule_coverage(project,
  owner)` after a successful apply. Idempotent — a no-op when only drafts were
  created (active unchanged), a fresh audit when the active MDL moved. This is the
  "visibility of system status" feedback: the badge re-analyses instead of
  silently going stale.
- Tests: `test_apply_recovery_changeset_accepts_the_recovery_conversation`,
  `test_apply_reschedules_coverage_when_active_mdl_changes`.

### Frontend
- **No more double-dialog.** The suggestions were a nested `RecoverySuggestionsDialog`
  Modal rendered *inside* the CoveragePanel Modal. Extracted the modal-free body
  into `RecoverySuggestionsContent`. CoveragePanel now renders it as a **second
  pane** inline (widens to 960px + re-centers via `centered`, vertical `Divider`
  between report and suggestions, a "Hide" control to collapse). The standalone
  `RecoverySuggestionsDialog` (banner entrypoint, when the report dialog is not
  open) reuses the same content.
- **Close only.** Removed the redundant **Dismiss** from the suggestions footer
  (dismissal remains on the banner and is implicit on apply).
- **Feedback (NN/g visibility of system status + form-error guidelines).** Apply
  now resolves to an explicit **success Alert** ("Applied N suggestion(s)… coverage
  re-analyses automatically") instead of a silent close, and failures show a
  **human-readable** message ("Could not apply the suggestions. Please try again.")
  with the raw detail in parentheses — never a bare backend string. `onApplied`
  bubbles to the badge's `poll` so re-analysis shows immediately.
- `canWrite` threaded editor → CoverageBadge → CoveragePanel → content so apply is
  gated in the UI (backend still authoritative).
- Tests: CoveragePanel "extends the same dialog into a second pane" (asserts one
  modal, report + suggestions coexist); RecoverySuggestionsDialog success-confirm,
  friendly-error, and "Close only (no Dismiss)".

Sources for the feedback design: NN/g *Visibility of System Status*
(nngroup.com/articles/visibility-system-status), NN/g *Error-Message Guidelines*
(nngroup.com/articles/errors-forms-design-guidelines).
