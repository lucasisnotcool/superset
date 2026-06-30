# Coverage as a Decoupled Version-Label + Live Progress — Feature Spec

Status: **Draft for review** · Scope: `superset_ai_agent` MDL Copilot / Semantic Layer Editor
Supersedes the click-to-rerun badge behaviour and the coverage-as-provenance-event coupling.

---

## 1. Intent (the one-paragraph version)

Coverage is a **read-only, second-class annotation over an MDL version** — not an
event in the MDL's history. It answers "how much of the source documentation does
*this* version of the semantic model capture?" We want three things:

1. **The coverage badge is a viewer, not a trigger.** Clicking it shows the
   current run's *progress* (if analysing) or the *report* (if one exists) — it
   never re-runs analysis. Re-run is a separate, explicitly-labelled control.
2. **Coverage scores annotate provenance versions as labels, side-by-side with
   history, without being history.** When Copilot edits an MDL at 88% coverage
   and the new version scores 60%, the provenance UI shows both `88%` and
   `60%` chips on their respective entries (with a `↓28%` delta) — but there is
   **no "Coverage" row** interleaved into the timeline.
3. **Live progress while a run is in flight** — stage, counts, and an honest
   indeterminate/determinate mix — so a 10–40s background run is legible.

This matches how mature tools (Codecov, dbt-coverage, GitHub Checks, DataHub,
Atlan) treat quality/coverage: a score lives in a **parallel metadata plane keyed
to a version/commit**, deltas are computed by comparing snapshots, and the asset's
own change history is never mutated. See §9 for citations.

---

## 2. What exists today (grounding)

**Backend**
- `AiAgentCoverageRun` table ([persistence/models.py:423](persistence/models.py)) already stores
  `id, project_id, owner_id, mdl_checksum, docs_checksum, status, score,
  report (JSON), error, created_at, updated_at`. **It is already keyed by
  `mdl_checksum`** — i.e. it is already a version-keyed result plane.
- Pipeline: `_schedule_coverage` → `_run_coverage_job` → `run_directory_coverage`
  (4 stages: extract claims per doc → build MDL facts → judge → aggregate), then
  `store.complete()` + `_append_semantic_event("coverage_completed")`
  ([app.py:1349–1463](app.py)). The audit already polls a `should_cancel` callback
  at **every stage boundary** ([copilot/coverage.py:719,733,755](semantic_layer/copilot/coverage.py)).
- Endpoints already present: `GET …/coverage/latest` (CoverageRun|null with full
  report), `GET …/coverage/runs/{run_id}` (one run + report),
  `GET …/coverage/status` (badge summary), `POST …/coverage/refresh` (re-run).
- `_active_mdl_checksum(project_id, owner_id)` ([app.py:1310](app.py)) hashes the
  sorted `(path, per-file checksum)` of the **active** files — deterministic, but
  computed on the fly and **not persisted onto provenance entries**.

**Provenance coupling (to be removed)**
- `coverage_completed` is classified as a provenance event
  (`PROVENANCE_EVENT_TYPES`, `_PROVENANCE_KIND_BY_EVENT["coverage_completed"] =
  "coverage"`, [schemas.py:493–505](semantic_layer/schemas.py)), so
  `provenance_from_event` projects it into a `ProvenanceEntry(kind="coverage")`
  and it renders as its own timeline row.
- `ProvenanceEntry` has **no `mdl_checksum`** field; version-producing events
  (`mdl_activated`, `mdl_updated`, `mdl_agent_edit`, `onboarding_completed`) do
  **not** stamp the resulting checksum into their `detail`.

**Frontend**
- `CoverageBadge.tsx` `onClick → refreshCoverage()` (re-runs). Badge state from
  `getCoverageStatus`; live via `useProjectEvents(COVERAGE_EVENT_TYPES)` + 30s poll.
- `CoverageReportBody` (in `CoverageReportModal.tsx`) is the shared report viewer
  (score, covered/partial/missing, findings, suggestions, overreach, warnings).
- `MdlProvenanceDialog.tsx` already drills a coverage timeline row into the report
  via `getCoverageRun(projectId, runId)`.

---

## 3. The model: coverage label plane keyed by `mdl_checksum`

The join key already exists. A "coverage label for a version" is simply: **the
latest `complete` `AiAgentCoverageRun` whose `mdl_checksum` == that version's
checksum.** No new table.

To attach a label to a provenance entry we need the entry to know *which version it
produced*. So:

- **Stamp `mdl_checksum` into the `detail` of version-producing events** at
  emission time (the checksum is already computed nearby). Events:
  `onboarding_completed` ([app.py:3053](app.py)),
  `mdl_activated`/`mdl_updated` ([app.py:1765,1939,1949](app.py)),
  `mdl_created` ([app.py:1501](app.py)), and the Copilot `mdl_agent_edit` apply path.
- **Stamp `mdl_checksum` into `coverage_completed.detail`** too (currently only
  `run_id` + score breakdown), so the badge/labels can resolve without a drill-in.

Then a provenance entry's coverage label = `scoresByVersion[entry.detail.mdl_checksum]`.
Delta = this entry's score − the score of the nearest **older** entry that has one
(Codecov "compare to previous commit" semantics).

---

## 4. Feature A — Badge becomes a viewer

### 4.1 Behaviour
| State (`/coverage/status`) | Badge label | Click |
|---|---|---|
| `analysing` (running) | `Coverage: analysing — {stage detail}` (spinner) | open **Progress** view |
| `ready` | `Coverage: 88%` (green) | open **Report** view |
| `stale` | `Coverage: 88% (stale)` (amber) | open **Report** view (with "computed for a previous version" notice) |
| `none` (no run yet) | muted `Coverage: not run` or hidden | open **Empty** view with explicit "Run analysis" CTA |

- **Click never re-runs.** Re-run is an explicit button (`Re-run analysis`) inside
  the opened panel — separate affordance, per UX-affordance + Codecov/GitHub
  convention (badge = passive indicator linking to detail; actions are separate).
- Tooltip changes from "Re-run coverage analysis" → "View coverage".

### 4.2 The Coverage panel (one modal, three states)
- **Progress** (running): the §6 stepper + live detail + elapsed time + "You can
  keep editing while this runs." Auto-swaps to Report on completion.
- **Report** (ready/stale): existing `CoverageReportBody` from
  `getLatestCoverage()` (report already stored — **no rebuild needed**), with a
  header showing score, the run timestamp, the docs version it was computed
  against, a `stale` notice if applicable, and the `Re-run analysis` button.
- **Empty** (none): short explainer + `Run analysis` CTA (auto-runs only fire when
  the project has both active MDL and ≥1 source document; this CTA covers the
  "docs exist but no run yet / re-run wanted" case).

**"Is there a report, or must it be built?"** → `getLatestCoverage()` returns the
stored `CoverageRun` incl. `report` when a complete run exists for the project; the
panel shows it directly. Only `none` (null) or an explicit re-run schedules work
via `POST /coverage/refresh`. Stale = a report exists but for a prior `mdl_checksum`;
we show it and offer re-run, we do not silently rebuild.

---

## 5. Feature B — Coverage labels on the provenance timeline (decoupled)

### 5.1 Decouple coverage from provenance history
1. Remove `"coverage_completed"` from `PROVENANCE_EVENT_TYPES` and from
   `_PROVENANCE_KIND_BY_EVENT` ([schemas.py:493](semantic_layer/schemas.py)) →
   `provenance_from_event` returns `None` for it → **no coverage timeline rows**,
   retroactively (read-path filter, so **no data migration** for old rows).
2. Keep *emitting* `coverage_completed` (+ new `coverage_progress`, §6) as
   semantic events for SSE liveness — they are now non-provenance, "second-class".
   Drop the `"coverage"` member from `ProvenanceKind` once unused.

### 5.2 New read endpoint — scores by version
`GET /agent/semantic-layer/projects/{project_id}/coverage/scores-by-version`
→ `{ [mdl_checksum]: { score, run_id, status, computed_at, docs_checksum } }`,
the **latest complete run per checksum** for the project. New store method
`scores_by_checksum(project_id)` over the existing table (indexed on
`project_id`, `mdl_checksum`).

### 5.3 Provenance UI overlay
- `MdlProvenanceDialog` fetches `getMdlProvenance()` **and**
  `getCoverageScoresByVersion()`; for each entry with `detail.mdl_checksum`,
  render a right-aligned **coverage chip**:
  - has score → `Coverage 88%` (+ `↓28%` / `↑5%` delta vs nearest older scored
    entry; red on drop, green on rise, grey on first/none).
  - version changed but no run / no docs → muted `Coverage —` with tooltip
    ("not computed" / "no source documents") — the Codecov `ø` / GitHub `expected`
    pattern for "no data on a historical entry".
- Chip click → `getCoverageRun(projectId, run_id)` → opens that version's report
  in the same `CoverageReportBody` viewer (per-version drill-in, read-only).
- Refresh: on a `coverage_completed` SSE event the dialog re-fetches the **label
  map only** — it does not add or reorder timeline entries.

This yields exactly the requested outcome: `88%` and `60%` chips sitting on the
two edit entries, with a delta, and zero coverage rows mixed into history.

---

## 6. Feature C — Live progress (two-axis state)

Adopt the GitHub/Codecov **two-axis model**: a lifecycle `status`
(`pending → running → complete/failed/superseded`) plus a `score` *conclusion* that
exists only once complete. Post the "analysing" placeholder immediately; never
leave the surface blank.

### 6.1 Backend seam
- Add an optional `progress_cb: Callable[[ProgressEvent], None]` to
  `run_directory_coverage` / `run_coverage_audit`, called at the **existing
  `should_cancel` seams** — zero new call sites, no change to LLM logic. Events:
  - `extracting` — per document, `current=i, total=len(docs)`, `detail=filename`
  - `judging` — `detail="142 claims vs 38 facts"` (real denominator after extraction)
  - `checking_overreach`, `aggregating`
- Persist coarse progress on the run row: add nullable `progress` JSON column
  (`{stage, detail, current, total, phase_index, phase_total}`) via a migration
  alongside [0009_coverage_runs](persistence/migrations/versions/0009_coverage_runs.py);
  `store.report_progress(run_id, ev)` is a cheap `UPDATE`.
- Widen `GET /coverage/status` to return the progress fields.
- Emit `coverage_progress` SSE events **only on stage transitions** (≤4/run) to
  bound chatter; fine-grained per-doc updates land via the row + badge poll.

### 6.2 Progress UX (industry-aligned)
- **Stepper**, 4 short steps: `Extract → Build → Judge → Aggregate` with
  completed / current / pending states, current most prominent (USWDS).
- **Determinate** "doc 2/5" bar during extraction (countable); **indeterminate**
  spinner during Judge (a single batched LLM call — see Risk R6); a bar that only
  increases (Material). Counts shown once countable (NN/g ≥10s ⇒ percent/counts).
- Badge label mirrors the current stage: `Coverage: analysing — judging 142 claims…`.

---

## 7. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Existing `coverage_completed` rows already rendered as provenance entries | Read-path filter (`provenance_from_event → None`) hides them retroactively; **no data migration**. |
| R2 | Historical provenance entries lack `detail.mdl_checksum` → no labels for old versions | Graceful muted `—`; feature is forward-looking. Matches Codecov `ø` / GitHub `expected`. |
| R3 | Several entries map to the same `mdl_checksum` (no-op or coalesced edits) | Dedup labels by checksum; equal checksum ⇒ no delta (`ø`). |
| R4 | LLM judge variance: same checksum can score differently across runs | Idempotency already reuses the stored run for identical `(mdl_checksum, docs_checksum)`; `votes>1` majority reduces variance; label = latest complete run for that checksum. |
| R5 | A version's coverage depends on **docs** too, not just MDL | Label keys on `mdl_checksum` (the thing that changes across an edit) and takes the latest complete run; tooltip shows `docs_checksum`/timestamp so a docs-driven shift is explainable. See DP2. |
| R6 | Judge is one batched call ⇒ no intra-judge granularity | Honest indeterminate spinner for the Judge step; per-doc determinate only for Extract. Chunked judging deferred (Phase 3). |
| R7 | `coverage_progress` events bloating the shared SSE / event log | Emit only on stage transitions (≤4/run); persist fine progress to the row; never classify as provenance. |
| R8 | Removing rerun-on-click hurts discoverability of re-run | Explicit `Re-run analysis` button in the panel; updated tooltip; matches industry affordance norms. |
| R9 | `none`-state badge click with no docs can't produce a run | Empty-state panel explains the precondition (needs active MDL + ≥1 document) instead of a dead button. |

---

## 8. Decision points (with recommendations)

- **DP1 — Storage for version labels.** *Reuse `AiAgentCoverageRun`* (already
  version-keyed) vs a new `CoverageLabel` table. **Recommend: reuse** — the run
  table *is* the parallel metadata plane; a second table duplicates state.
- **DP2 — Label key.** `mdl_checksum` only vs `(mdl_checksum, docs_checksum)`.
  **Recommend: `mdl_checksum`, latest complete run**, with `docs_checksum`+timestamp
  surfaced in the chip tooltip. Rationale: the before/after story is *MDL* changing
  with docs held constant; keying on the pair fragments labels and hides the headline.
- **DP3 — Keep emitting `coverage_completed`?** Fully separate channel vs keep on the
  shared `/events` SSE but unclassified as provenance. **Recommend: keep on shared
  SSE, exclude from provenance** — minimal change, badge/labels already listen there.
- **DP4 — `none`-state badge click.** No-op vs open empty panel with CTA.
  **Recommend: open panel with `Run analysis` CTA** (covers docs-exist-but-not-run).
- **DP5 — Which entries get labels.** **Recommend: any entry whose
  `detail.mdl_checksum` differs from the prior entry's** (onboarding, activate,
  copilot edit, manual edit) — i.e. version-producing entries only.
- **DP6 — Progress granularity.** **Recommend: stage-level for v1**; chunked-judge
  "claim 80/142" only if directories routinely exceed a few hundred claims (Phase 3).
- **DP7 — Delta display.** **Recommend: show `↑/↓Δ` vs nearest older scored
  version**, red on drop / green on rise (Codecov per-commit delta convention).

---

## 9. Industry grounding (citations)

- **Score lives in a parallel plane keyed to a version; deltas compare snapshots,
  never mutate history** — dbt Explorer coverage (Discovery API, not source)
  <https://docs.getdbt.com/docs/explore/project-recommendations>; dbt-coverage
  report files + `compare` <https://github.com/slidoapp/dbt-coverage>; Codecov
  commit-status / per-commit delta
  <https://docs.codecov.com/docs/commit-status>,
  <https://docs.codecov.com/docs/comparing-commits>; DataHub health as assertion
  metadata <https://docs.datahub.com/docs/managed-datahub/observe/data-health-dashboard>;
  Atlan trust badges as metadata overlays
  <https://docs.atlan.com/product/capabilities/governance/data-quality/concepts/data-quality-studio>.
- **Badge = passive indicator that opens a detail view; actions are separate** —
  Codecov status badges link to the report dashboard
  <https://docs.codecov.com/docs/status-badges>; GitHub status checks "Details"
  link + separate re-run <https://docs.github.com/articles/about-status-checks>;
  Telerik/Carbon: badges are non-interactive status indicators
  <https://www.telerik.com/design-system/docs/components/badge/>; affordance/signifier
  match <https://www.uxpin.com/studio/blog/affordances-user-interaction/>.
- **Two-axis status+conclusion; pending placeholder up front; indeterminate→determinate;
  3+ step stepper; bar only increases** — GitHub Checks API
  <https://docs.github.com/en/rest/checks/runs>; Codecov "Expected — waiting"
  <https://docs.codecov.com/docs/commit-status>; NN/g progress indicators
  <https://www.nngroup.com/articles/progress-indicators/>; Material
  <https://m1.material.io/components/progress-activity.html>; Apple HIG
  (favour progress bars when quantifiable); USWDS step indicator
  <https://designsystem.digital.gov/components/step-indicator/>.
- **Per-historical-entry "no data" state** — Codecov `ø` (not affected) / GitHub
  `expected` & `stale` conclusions
  <https://docs.codecov.com/docs/coverage-percentages>,
  <https://docs.github.com/en/rest/checks/runs>.
- **Vocabulary** — "coverage" = ratio captured (dbt, dbt-coverage "hits/misses");
  distinct from "health/quality score" = ratio passing (Monte Carlo, Soda). Use
  **"coverage"** here (we measure how much of the docs the MDL captures).

---

## 10. Phasing

- **Phase 1 — Badge-as-viewer (Feature A).** Remove rerun-on-click; panel with
  Progress/Report/Empty states from existing endpoints. Self-contained, ships value.
- **Phase 2 — Live progress (Feature C).** `progress_cb` + `progress` column +
  widen `/coverage/status` + stepper + `coverage_progress` events.
- **Phase 3 — Version labels (Feature B).** Stamp `mdl_checksum` onto
  version-producing + coverage events; `scores-by-version` endpoint; provenance
  chip overlay + deltas; decouple `coverage_completed` from provenance.
- **Phase 4 (optional).** Chunked judging for intra-judge progress; richer delta
  visualisation (sparkline of coverage over versions).

## 11. Out of scope
- Changing the coverage *scoring algorithm* (extract/judge/aggregate) or weights.
- Persisting an explicit MDL "version" entity (the `mdl_checksum` is sufficient as
  the version key; no new versioning model is introduced).
- Notifications/email on coverage drops (possible later, off the same events).

---

## 12. Implementation status — as shipped (2026-06-30)

**All three phases SHIPPED + tested** (frontend coverage suite green; tsc clean).
Decisions DP1–DP7 implemented as recommended. Key deltas, files, and gotchas:

### Backend
- **Migration `0012_coverage_run_progress`** — nullable `progress` JSON column on
  `ai_agent_coverage_runs`. `CoverageProgress` pydantic model
  ([copilot/schemas.py](semantic_layer/copilot/schemas.py)); persisted while
  `running`, **cleared on terminal transition** (complete/fail) in both store
  impls' `_update`.
- **`progress_cb`** (NamedTuple `CoverageProgress` + `_report_progress`) threaded
  through `run_directory_coverage` on the **existing `should_cancel` stage seams**
  ([copilot/coverage.py](semantic_layer/copilot/coverage.py)); advisory — a
  throwing callback never breaks the audit. Stages: `building_facts` → `extracting`
  (per-doc, countable) → `judging` (detail = "N claims vs M facts") →
  `aggregating`/`checking_overreach`.
- **`_run_coverage_job`** wires `progress_cb` → `store.report_progress` + emits a
  throttled non-provenance `coverage_progress` event **only on stage transitions**
  (≤4/run). `_run_coverage_job` carries `# noqa: C901`.
- **`/coverage/status`** widened: `progress` (live), plus (Feature B)
  `scores-by-version` via new store method `scores_by_checksum(project_id)`
  (latest complete run per `mdl_checksum`). A `latest_complete_bulk` store method
  also exists (project-list badge enrichment).
- **Decouple (Feature B):** `coverage_completed` **removed from**
  `PROVENANCE_EVENT_TYPES` **and** `_PROVENANCE_KIND_BY_EVENT`
  ([semantic_layer/schemas.py](semantic_layer/schemas.py)) → `provenance_from_event`
  returns `None` for it (read-path filter, **no data migration**). `mdl_checksum`
  stamped into version-producing event `detail` at the two central seams
  (`_emit_mdl_provenance`, `_emit_agent_apply_provenance`) + onboarding.
- New event types added to the `SemanticLayerEventType` literal: `coverage_progress`,
  (later) `recovery_suggestions_ready` — both **non-provenance**.

### Frontend
- **`CoverageBadge`** click → opens `CoveragePanel` (viewer), **never re-runs**;
  re-run is an explicit button inside the panel. Analysing label mirrors live
  stage detail.
- **New components:** `CoveragePanel` (progress/report/empty/stale states),
  `CoverageProgress` (4-step stepper: Extract→Build→Judge→Aggregate; determinate
  bar only when countable, indeterminate for the batched Judge).
- **`MdlProvenanceDialog`** renders a `CoverageChip` per version-producing entry
  (joins `detail.mdl_checksum` → `scores-by-version`), with a Codecov-style delta
  (`↓28%`) vs the nearest older *different*-checksum scored entry; chip opens that
  version's report. Coverage rows no longer appear in the timeline.

### Gotchas / non-obvious
- **`t('… %s%', n)` sprintf chokes on a trailing literal `%`** — render the percent
  outside `t()` (e.g. `{t('Coverage')} {pct}%`).
- The events `/events` SSE is snapshot-style (finite generator); the browser
  `EventSource` auto-reconnects to re-fetch — that's the "live" mechanism. `coverage_progress`
  is throttled to stage transitions to avoid bloating that re-streamed list.
- **Side-effect of decoupling:** `coverage_completed`/`coverage_progress` are no
  longer purged on MDL reset (they're non-provenance). Harmless/non-rendering, but
  they linger in the event store — candidate for a future cleanup.

### Known gaps (deferred)
- Label key is `mdl_checksum` only (DP2) — a score shift driven purely by *document*
  changes at a constant MDL version isn't distinguished in the chip (docs version is
  in the run; surface in a tooltip later).
- Judge is one batched LLM call → no intra-judge "claim 80/142" granularity (Phase 4).
</content>
