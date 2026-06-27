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

# Implementation Plan & Checklist: MDL Provenance Completeness & Background Coverage

**Companion to:** `plan_provenance_and_coverage_spec.md` (read it first for rationale).
**Scope:** `superset_ai_agent/` (FastAPI) + `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/`.
**Audience:** future agent sessions — this is a resumable, ordered checklist. Tick `[x]` as you complete each task; do not reorder. Each task lists **entrypoints/touchpoints (file:line)**, **requirements**, **acceptance**, and **depends-on**.

> All line numbers are anchors captured at authoring time against branch `master`. Re-grep the symbol if a line has drifted; symbols are stable, lines are not.

---

## How to use this checklist

- Work top-to-bottom. A task is **blocked** until its **depends-on** tasks are `[x]`.
- Phases are independently shippable: **Phase 1 (Feature A)** has no migration and can merge alone; **Phase 2 (Feature B)** depends on Phase 1's `actor_type` + apply-emit only for the coverage *trigger classification*, not structurally.
- After each task: run the named tests; run `pre-commit run` on staged files (mypy/ruff/prettier/eslint) per `CLAUDE.md`.
- Keep all provenance/coverage emission **best-effort** (try/except, log-and-continue) — never fail a write or an apply because telemetry failed (mirrors `_emit_mdl_provenance`, `app.py:997`).

---

## Pre-flight

- [ ] **P0. Confirm the dev/test loop runs.** Backend: `pytest tests/unit_tests/superset_ai_agent/ -q`. Frontend: `npm run test -- MdlProvenanceDialog`. If the backend can't collect, ask the user to set up the test env (per `CLAUDE.md`). **Depends-on:** none.
- [ ] **P1. Re-confirm the core gap still holds.** `grep -n "_emit_mdl_provenance" superset_ai_agent/app.py` shows calls only at ~1065/1296/1305/1345 (manual CRUD), and `apply_project_copilot` (`app.py:1997`) → `apply_changeset_items` (`copilot/service.py:173`) has **no** provenance emit. If this changed, revise the plan. **Depends-on:** none.

---

## Phase 1 — Provenance completeness (Feature A)

### 1A. Backend: `actor_type` + new `copilot_edit` kind

- [ ] **1A.1 Add `actor_type` to the projection + schema.**
  - **Touchpoints:** `semantic_layer/schemas.py` — `ProvenanceEntry` (line ~323, add `actor_type: Literal["user","agent","system"] = "system"`); `provenance_from_event` (line ~354, compute `actor_type` from `detail.source_type` / `kind`).
  - **Rule:** `source_type in {"manual","uploaded_mdl"} → "user"`; `source_type == "copilot"` or `kind == "enrichment"` or `kind == "copilot_edit"` → `"agent"`; `kind == "onboarding"` → `"system"`.
  - **Acceptance:** unit test asserts mapping for each `source_type`/`kind`.
  - **Depends-on:** P1.
- [ ] **1A.2 Register the `copilot_edit` kind end-to-end.**
  - **Touchpoints:** `schemas.py` — `ProvenanceKind` literal (~313), `SemanticLayerEventType` literal (~44, add `mdl_agent_edit` event type or reuse — see **D-IMPL-1**), `_PROVENANCE_KIND_BY_EVENT` (~339), `PROVENANCE_EVENT_TYPES` frozenset (~68).
  - **D-IMPL-1 (decision):** new event type `mdl_agent_edit` vs. reusing `mdl_updated` with `detail.source_type="copilot"`. **Recommendation: new event type `mdl_agent_edit`** → maps to kind `copilot_edit`; keeps the timeline label unambiguous and the trigger classification (Phase 2) trivial. `enrichment` keeps using the existing `document_enriched` event type.
  - **Acceptance:** `provenance_from_event` returns `copilot_edit`/`enrichment` for the new/existing event types.
  - **Depends-on:** 1A.1.

### 1B. Backend: emit provenance from the Copilot apply path (core gap fix)

- [ ] **1B.1 Capture documents referenced during the turn.**
  - **Touchpoints:** `copilot/schemas.py` — `Changeset.steps: list[AgentStep]` (line 77) is the reliable source of `search_documents`/`list_documents` results (`MessageAttachment` at line 129 is ephemeral inline text with **no `document_id`**, so attachments yield only filenames). Add a helper `extracted_document_refs(changeset) -> list[{id, filename}]` that mines tool-result steps; fall back to attachment filenames (id `None`).
  - **Requirement:** label these "documents referenced," not "used" (R6 in spec).
  - **Acceptance:** unit test feeds a changeset with a `search_documents` step → returns the doc ids.
  - **Depends-on:** 1A.2.
- [ ] **1B.2 Emit one provenance event per applied changeset.**
  - **Touchpoints:** `app.py::apply_project_copilot` (line 1997), **after** the successful `apply_changeset_items` call (line 2010). Reuse `_append_semantic_event` (`app.py:3295`). Wrap in try/except (best-effort).
  - **Classification:** if `extracted_document_refs(...)` non-empty → event type `document_enriched` (kind `enrichment`); else → `mdl_agent_edit` (kind `copilot_edit`).
  - **`detail` payload (spec §A.3):** `{actor: owner_id, source_type:"copilot", conversation_id, summary: changeset.message, ops:{create,update,delete}, paths:[...], documents:[{id,filename}]}`. The endpoint must receive the applied `Changeset` (currently `apply_project_copilot` takes `ChangesetApplyRequest` with only `items` + `conversation_id`, `copilot/schemas.py:152`). **See D-IMPL-2.**
  - **D-IMPL-2 (decision):** the apply request carries only `items`, not the full `Changeset` (no `message`/`steps`). Options: (a) derive `summary`/`paths`/`ops` from `items` + look up the changeset artifact via `conversation_id` (artifacts persisted as `ConversationArtifact`, `copilot/service.py::changeset_to_artifact:160`); (b) extend `ChangesetApplyRequest` to include `message` + `document_refs`. **Recommendation: (a)** — re-read the persisted changeset artifact by `conversation_id` so the server is the source of truth and the client can't spoof provenance. Falls back to item-derived summary if no conversation_id.
  - **Acceptance:** unit test — apply with docs → one `document_enriched` event with `documents`; apply without docs → one `mdl_agent_edit`; emit failure does not fail apply (assert 200 + applied files even when store.append_event raises).
  - **Depends-on:** 1A.2, 1B.1.

### 1C. Backend: coalesce user-edit runs (read-time)

- [ ] **1C.1 Implement coalescing in the read endpoint.**
  - **Touchpoints:** `app.py::get_project_provenance` (line ~3074) — insert a `_coalesce_user_runs(entries)` step **after** the newest-first sort and **before** `[:PROVENANCE_HISTORY_CAP]`.
  - **Algorithm (spec §A.4):** walk newest→oldest; merge maximal contiguous runs where `actor_type == "user"`; a non-user entry closes the run. Each run → one entry: `created_at`=newest, `first_at`=oldest, `edit_count`=len, `summary`="Edited N times" when N>1, `detail.paths`=union, `id`=newest entry's id.
  - **Schema:** add `edit_count: int = 1`, `first_at: datetime | None = None` to `ProvenanceEntry` (`schemas.py:323`).
  - **Acceptance:** unit tests for the three spec sequences — (i) user,user (no gap) → 1 row @ latest; (ii) user, agent, user → 3 rows; (iii) single user edit → 1 row, `edit_count==1`. Plus: cap applied post-coalesce.
  - **Depends-on:** 1A.1 (needs `actor_type`).

### 1D. Frontend: render the richer timeline

- [ ] **1D.1 Mirror schema additions in the API client.**
  - **Touchpoints:** `AiAgentPanel/api.ts` — `ProvenanceKind` union (line 1683, add `'copilot_edit'`), `ProvenanceEntry` interface (line 1691, add `actor_type?`, `edit_count?`, `first_at?`). `getMdlProvenance` (line 1705) unchanged.
  - **Acceptance:** typecheck passes (`tsc`/eslint).
  - **Depends-on:** 1A.1, 1A.2, 1C.1.
- [ ] **1D.2 Render actor type, coalesced runs, document chips, deep-link.**
  - **Touchpoints:** `SemanticLayerEditor/MdlProvenanceDialog.tsx` — `KIND_LABELS` (~line 82, add `copilot_edit: t('Agent edit')`); the secondary-detail extractor (~lines 97–113); the row render (~lines 176–193).
  - **Requirements:** actor-type icon/label (user/agent/system); when `edit_count>1` show "Edited N times" + range `first_at`–`created_at`; for `enrichment` render `detail.documents` as chips + a "View conversation" link using `detail.conversation_id`; for `copilot_edit` show `detail.summary` + ops.
  - **Acceptance:** Jest/RTL — renders each actor type; renders "Edited 3 times"; renders doc chips; clicking deep-link fires the thread-open handler.
  - **Depends-on:** 1D.1.

- [ ] **1D.3 (deferred to Phase 3 — track here)** Expandable raw-event view under a coalesced run. **Depends-on:** 1C.1, 1D.2.

### Phase 1 exit criteria
- [ ] Copilot apply, enrichment, manual edits, onboarding all appear in the timeline with correct `actor_type`.
- [ ] Consecutive user edits collapse per the worked examples; agent/enrichment break runs.
- [ ] Enrichment entries show referenced documents + conversation deep-link.
- [ ] `pytest tests/unit_tests/superset_ai_agent/` + `npm run test -- MdlProvenanceDialog` green; `pre-commit run` clean.

---

## Phase 2 — Background, directory-level coverage (Feature B)

> **Blocker B-DEP-1:** Phase 2 reuses the `actor_type`/event-projection plumbing from 1A for its own `coverage_completed` event. Land 1A before 2D. The rest of Phase 2 is independent of Phase 1.

### 2A. Backend: persistence (migration + model + store)

- [ ] **2A.1 Add the `ai_agent_coverage_runs` model.**
  - **Touchpoints:** `persistence/models.py` (alongside `AiAgentEvent`, line ~197). Columns per spec §B.5: `id` PK, `project_id`(ix), `owner_id`(ix), `mdl_checksum`(ix), `docs_checksum`, `status`(ix), `score` Float, `report` JSON, `error` Text, `created_at`/`updated_at`(ix).
  - **Acceptance:** model imports; `Base.metadata` includes the table.
  - **Depends-on:** none.
- [ ] **2A.2 Alembic migration `0009_coverage_runs.py`.**
  - **Touchpoints:** `persistence/migrations/versions/` (latest is `0008_conversation_kind_project.py`; set `down_revision="0008_conversation_kind_project"`, keep linear history). Use `superset.migrations.shared.utils` helpers where applicable; create table + indexes; implement `downgrade` (drop table).
  - **Acceptance:** upgrade/downgrade round-trips locally; alembic history linear.
  - **Depends-on:** 2A.1.
- [ ] **2A.3 `CoverageRunStore` (protocol + InMemory + SqlAlchemy).**
  - **Touchpoints:** new `semantic_layer/coverage_store.py`, modelled on `semantic_layer/jobs.py` (`JobStore` Protocol line 45, `InMemoryJobStore` 59, `SqlAlchemyJobStore` 118). Methods: `create(project_id, owner_id, mdl_checksum, docs_checksum) -> CoverageRun`; `claim(run_id) -> bool` (compare-and-set `pending→running`, the cross-worker lease, spec R2); `complete(run_id, report)`; `fail(run_id, error)`; `supersede(project_id, except_run_id)`; `latest_complete(project_id) -> CoverageRun|None`; `get(run_id)`; `find_complete(mdl_checksum, docs_checksum, model, votes)` (idempotency, spec B.4).
  - **Schema:** add `CoverageRun` pydantic model to `schemas.py` (or `copilot/schemas.py`) + `CoverageRunStatus = Literal["pending","running","complete","failed","superseded"]`.
  - **Acceptance:** unit tests for `claim` CAS semantics and `supersede`.
  - **Depends-on:** 2A.1.
- [ ] **2A.4 Wire stores into the app.**
  - **Touchpoints:** `app.py` (~line 311–341 where `active_job_runner`, `active_coverage_cache` are constructed) — instantiate `active_coverage_run_store` (SqlAlchemy when a session factory exists, else InMemory; same pattern as `JobStore`).
  - **Depends-on:** 2A.3.

### 2B. Backend: directory-level aggregate audit + cancellation

- [ ] **2B.1 Add cooperative cancellation to the engine.**
  - **Touchpoints:** `copilot/coverage.py::run_coverage_audit` (line 577) — add optional `should_cancel: Callable[[], bool] | None = None`; check it at stage boundaries and inside the per-claim loop of `judge_coverage` (line 325). On cancel, return early (caller decides not to persist).
  - **Acceptance:** unit test — a `should_cancel` that flips True after stage A returns without judging; nothing persisted.
  - **Depends-on:** none.
- [ ] **2B.2 Directory aggregate runner.**
  - **Touchpoints:** new `run_directory_coverage(...)` (in `coverage.py` or a thin `app.py` helper). Gather **active** MDL files (`active_mdl_file_store.list`, status active) + **all** project documents (`list_project_documents`) + instructions; run `run_coverage_audit` per document; aggregate into one `CoverageReport` (sum totals, weighted score, findings tagged with `document_id`/`filename`; overreach once vs union). Empty docs → no-op record (spec §B.2, D2=union).
  - **Acceptance:** unit test — 2 docs aggregate into combined totals/score; 0 docs → no-op (no LLM call).
  - **Depends-on:** 2B.1.

### 2C. Backend: trigger, debounce, supersession orchestration

- [ ] **2C.1 Compute the MDL directory version (checksum).**
  - **Touchpoints:** reuse `wren_materializer.materialize_wren_project(...).checksum` over the **active** set (spec §B.3). Add a cheap helper that returns the active-set checksum without writing files if materialization is heavyweight.
  - **Depends-on:** none.
- [ ] **2C.2 Centralized "active-set changed" hook + debounced scheduler.**
  - **Touchpoints:** new `schedule_coverage(project_id, owner_id)` orchestrator. Call it from every active-set mutation:
    - `app.py:1296` (`mdl_activated` branch of PATCH mdl-files),
    - delete-of-active (`app.py` DELETE mdl-files, near `mdl_deleted` ~1345),
    - onboarding completion (`_run_onboarding`, `app.py:2214`),
    - reset (`reset_semantic_project`, `app.py:2271`),
    - copilot apply that activates (none today — apply lands drafts; safe to skip).
  - **Logic (spec §B.4):** compute current checksum; if a `complete` run already matches `(mdl_checksum, docs_checksum, model, votes)` → skip; else `supersede` in-flight runs for the project, `create` a `pending` run, and submit to `active_job_runner` after a debounce window (**D-IMPL-3** default 3–5 s) coalescing rapid triggers.
  - **D-IMPL-3 (decision):** debounce mechanism. Options: (a) timer thread per project; (b) a "latest trigger wins" check inside the job (job re-reads the project's newest pending run on start and no-ops if it's not the target). **Recommendation: (b)** — simpler, stateless, robust to multi-worker: the job, on start, calls `claim()`; if another newer run exists it loses the CAS and exits. Add a small `time.sleep(debounce)` before `claim()` to coalesce. Avoid per-project timer threads.
  - **Acceptance:** unit test — two rapid triggers with different checksums → first run ends `superseded`, second `complete`; identical checksum twice → second skipped via idempotency.
  - **Depends-on:** 2A.3, 2A.4, 2B.2, 2C.1.
- [ ] **2C.3 Background job body.**
  - **Touchpoints:** the `fn` submitted to `active_job_runner.submit(...)` (pattern: `_run_onboarding` at `app.py:2181`). Body: `sleep(debounce)` → `claim()` (exit if lost) → `run_directory_coverage(should_cancel=lambda: store.get(run_id).status=="superseded" or a newer pending run exists)` → `complete(run_id, report)` → emit `coverage_completed` provenance event (2D.1). On exception → `fail(run_id, error)`.
  - **Requirement:** capture `owner_id` at trigger time; run under it (spec R9).
  - **Acceptance:** integration-style unit test with `InlineJobRunner` end-to-end → a `complete` run + a `coverage_completed` event.
  - **Depends-on:** 2C.2, 2B.2.

### 2D. Backend: surface coverage in provenance + read APIs

- [ ] **2D.1 `coverage_completed` event + `coverage` kind.**
  - **Touchpoints:** `schemas.py` — add `coverage_completed` to `SemanticLayerEventType`, `coverage` to `ProvenanceKind`, map in `_PROVENANCE_KIND_BY_EVENT` + `PROVENANCE_EVENT_TYPES`; `actor_type="system"`. Emit from 2C.3 via `_append_semantic_event` with `detail={run_id, score, total, covered, partial, missing, unsupported, mdl_checksum}`.
  - **Acceptance:** completed run produces a `coverage` timeline entry carrying `run_id` + score.
  - **Depends-on:** 1A.1, 1A.2 (B-DEP-1), 2C.3.
- [ ] **2D.2 Read endpoints.**
  - **Touchpoints:** `app.py` (near other project routes) — `GET .../coverage/latest`, `GET .../coverage/runs/{run_id}`, `GET .../coverage/status`, `POST .../coverage/refresh` (manual re-trigger via `schedule_coverage`). All behind `authorize_semantic_project(..., permission="read"|"write")`.
  - **Acceptance:** endpoint tests for latest/by-id/status/refresh + authz.
  - **Depends-on:** 2A.3, 2C.2.
- [ ] **2D.3 Deprecate the sync per-document endpoint.**
  - **Touchpoints:** `app.py::run_project_coverage` (line 1605, `POST .../copilot/coverage`). Keep for one release (per **D3** in spec) for per-document drill-down; mark deprecated in docstring. Remove later.
  - **Depends-on:** 2D.2.

### 2E. Frontend: coverage in the dialog + header badge

- [ ] **2E.1 API client.**
  - **Touchpoints:** `AiAgentPanel/api.ts` — add `getLatestCoverage`, `getCoverageRun(runId)`, `getCoverageStatus`, `refreshCoverage`; reuse existing `CoverageReport` interface (line 1292).
  - **Depends-on:** 2D.2.
- [ ] **2E.2 Provenance drill-in.**
  - **Touchpoints:** `MdlProvenanceDialog.tsx` — for `kind === 'coverage'`, render score + tag counts; on click, fetch `getCoverageRun(detail.run_id)` and show `CoverageReportBody` (`CoverageReportModal.tsx:46`).
  - **Acceptance:** Jest — coverage entry renders score; clicking opens report body.
  - **Depends-on:** 2E.1, 1D.2.
- [ ] **2E.3 Header badge.**
  - **Touchpoints:** `SemanticLayerEditor/index.tsx` header (near the history button, ~lines 785–795). Show latest score / "Analysing…" (poll `getCoverageStatus`) / "Stale" (active checksum ≠ latest complete run) with a "Re-run" action → `refreshCoverage`.
  - **Acceptance:** Jest — three badge states render; "Re-run" calls the API.
  - **Depends-on:** 2E.1.
- [ ] **2E.4 Repurpose `CoverageDialog.tsx`.** Per **D3**: keep as optional per-document drill-down for one release, or hide its entry point in favour of the badge + provenance. **Depends-on:** 2E.2, 2E.3.

### Phase 2 exit criteria
- [ ] Activating/deleting active MDL, onboarding, and reset each schedule a debounced directory coverage run.
- [ ] A change mid-run supersedes the stale run; the latest checksum always wins; identical checksum is skipped.
- [ ] Completed runs persist (score + report) and appear as `coverage` provenance entries openable in the dialog.
- [ ] Header badge reflects latest score / analysing / stale.
- [ ] All new unit tests + migration round-trip green; `pre-commit run` clean.

---

## Phase 3 — Polish (track, not blocking)

- [ ] **3.1** Exact document-id attribution from `search_documents` tool results (upgrade 1B.1 from filenames to ids).
- [ ] **3.2** Expandable raw-event list under a coalesced user run (1D.3).
- [ ] **3.3** Coverage trend (score across MDL versions) in the dialog, from `ai_agent_coverage_runs` history.
- [ ] **3.4** Consider SSE push for coverage status (reuse project events stream) instead of polling.

---

## Consolidated decision points

| ID | Decision | Recommendation | Where |
| --- | --- | --- | --- |
| D-IMPL-1 | New `mdl_agent_edit` event type vs reuse `mdl_updated`+source_type | **New event type** (unambiguous label + trivial Phase-2 classification) | 1A.2 |
| D-IMPL-2 | Apply provenance source: re-read persisted changeset artifact vs extend request | **Re-read artifact by `conversation_id`** (server-authoritative, unspoofable) | 1B.2 |
| D-IMPL-3 | Debounce mechanism: per-project timer vs claim-on-start CAS | **Claim-on-start CAS + small sleep** (stateless, multi-worker safe) | 2C.2 |
| (spec) D1 | Copilot provenance granularity | **Per-changeset** | 1B.2 |
| (spec) D2 | Coverage scope | **Union of all project documents** | 2B.2 |
| (spec) D3 | Keep per-document coverage | **Drill-down for one release, then remove** | 2D.3 / 2E.4 |

---

## Risks & mitigations (impl-specific)

| # | Risk | Mitigation | Task |
| --- | --- | --- | --- |
| R1 | LLM cost / thrash on every change | Debounce + supersede + idempotency cache on `(mdl_checksum,docs_checksum,model,votes)`; trigger only on active-set changes | 2C.2, 2A.3 |
| R2 | Multi-worker double-run | DB-backed `claim()` CAS lease; losers exit | 2A.3, 2C.3 |
| R3 | Threads not force-killable | Cooperative `should_cancel` at stage/claim boundaries | 2B.1, 2C.3 |
| R4 | Coalescing hides/garbles audit | Read-time only; raw events untouched; `edit_count`/`first_at` expose range; expand view in P3 | 1C.1, 3.2 |
| R5 | Provenance/coverage emit failure blocks a write | Best-effort try/except everywhere | 1B.2, 2C.3 |
| R6 | Fuzzy enrichment doc attribution (attachments lack ids) | MVP: filenames + flag; P3: ids from `search_documents` steps; label "referenced" | 1B.1, 3.1 |
| R7 | Score misread as a gate | "Advisory" copy; show votes; never gate deploy | 2E.2, 2E.3 |
| R8 | Cap vs coalescing | Coalesce before `PROVENANCE_HISTORY_CAP` | 1C.1 |
| R9 | Background job identity | Capture `owner_id` at trigger; run under it (onboarding pattern) | 2C.2, 2C.3 |

---

## Source references (entrypoints)

- Provenance emit: `superset_ai_agent/app.py::_emit_mdl_provenance:997`, `_append_semantic_event:3295`, `get_project_provenance:3074`.
- Copilot apply (the gap): `app.py::apply_project_copilot:1997` → `semantic_layer/copilot/service.py::apply_changeset_items:173`.
- Schemas: `semantic_layer/schemas.py` — `ProvenanceEntry:323`, `provenance_from_event:354`, `_PROVENANCE_KIND_BY_EVENT:339`, `PROVENANCE_EVENT_TYPES:68`, `SemanticLayerEventType:44`, `PROVENANCE_HISTORY_CAP`, `SemanticJob:413`.
- Changeset: `semantic_layer/copilot/schemas.py` — `ChangesetItem:52`, `Changeset:69` (`steps:77`), `ChangesetApplyRequest:152` (`conversation_id:159`), `MessageAttachment:129` (no doc id), `CoverageRequest:230`.
- Coverage engine: `semantic_layer/copilot/coverage.py::run_coverage_audit:577`, `judge_coverage:325`, `aggregate_report:550`; report schema `copilot/schemas.py::CoverageReport`.
- Async infra: `semantic_layer/jobs.py` — `JobStore:45`, `InMemoryJobStore:59`, `SqlAlchemyJobStore:118`; `app.py::active_job_runner:311`, `active_coverage_cache:341`, `_run_onboarding:2181`, onboarding emit `:2214`, reset `:2271`.
- Migrations: `persistence/migrations/versions/0008_conversation_kind_project.py` (latest; new = `0009_coverage_runs`).
- Frontend: `AiAgentPanel/api.ts` — `CoverageReport:1292`, `runCoverage:1306`, `ProvenanceKind:1683`, `ProvenanceEntry:1691`, `getMdlProvenance:1705`; `SemanticLayerEditor/MdlProvenanceDialog.tsx` (`KIND_LABELS:82`, render `:176`); `CoverageReportModal.tsx::CoverageReportBody:46`; `SemanticLayerEditor/index.tsx` (header `:785`, `saveFile:492`).

## Industry-pattern sources (rationale)
- Read-time grouping of consecutive same-author edits — Google Docs version history grouping: https://support.google.com/docs/answer/190843
- Debounce + cancel-stale-work in queueing/background systems — Inngest: https://www.inngest.com/blog/debouncing-in-queuing-systems-optimizing-efficiency-in-async-workflows
- Stale-request supersession (AbortController model) — pattern reference: https://medium.com/@velja/delaying-debouncing-and-cancelling-request-using-abortcontoller-in-react-d8e089bfce14

---

## Implementation status (delivered 2026-06-28)

All Phase 1 + Phase 2 items are implemented, unit-tested, and green
(backend: `tests/unit_tests/superset_ai_agent/` 833 passed / 11 skipped;
frontend: `MdlProvenanceDialog` + `CoverageBadge` suites 9 passed). Migration
`0009_coverage_runs` upgrade/downgrade round-trips on a single linear head.

### What shipped, by area

- **Provenance completeness (A).** `actor_type` + `copilot_edit`/`coverage` kinds
  + `mdl_agent_edit`/`coverage_completed` events (`schemas.py`); apply path now
  emits one server-authoritative provenance entry per changeset, classified as
  `enrichment` (documents pulled via `search_documents`) vs `copilot_edit`
  (`app.py::_emit_agent_apply_provenance`, `service.py::apply_provenance_payload`,
  `changeset_from_conversation`; toolset records `referenced_document_ids`).
  Read-time coalescing of consecutive user `mdl_updated` runs
  (`schemas.py::coalesce_user_runs`). UI: actor tags, "Edited N times" range,
  document chips, conversation deep-link.
- **Background coverage (B).** `ai_agent_coverage_runs` table + `CoverageRunStore`
  (claim-CAS lease + supersede + idempotency); `run_coverage_audit(should_cancel=)`
  + `run_directory_coverage` (union of docs); centralized `_schedule_coverage`
  hook on activation / active-file edit / active-file delete / onboarding-complete,
  with reset cancelling in-flight runs; debounced claim-on-start job; read
  endpoints `coverage/latest|runs/{id}|status|refresh`; provenance drill-in +
  header `CoverageBadge`. Sync per-document `/copilot/coverage` deprecated.

### Decisions confirmed during build (deltas from the plan)

- **Coalescing predicate narrowed to `mdl_updated` only** (not all user actions).
  `create`/`activate`/`delete` are distinct lifecycle events a user expects to see
  individually; only repeated content saves are the "noise" the user described
  ("edit at 2pm then 5pm = one change"). This also kept existing provenance API
  tests meaningful. (`schemas.py::_is_user_edit`)
- **`CoverageCancelled` → `CoverageCancelledError`** to satisfy the repo's N818.
- **Debounce default 0s** (`wren_coverage_debounce_seconds`): correctness is
  carried by claim-on-start supersession, so the wait is a pure cost optimization;
  0 keeps inline/test runs deterministic. Production sets a 3–5s window.

## Remaining risks & expectation/UI gaps (for the next session)

These are deliberate MVP boundaries, not defects — each has a clear follow-up.

1. **Enrichment document attribution is by retrieval, not "use" (R6).** We record
   document ids the agent pulled via `search_documents`. If the agent reasons from
   an inline message *attachment* (which has no `document_id`) or from a document it
   `list_documents`-ed but never searched, that apply is classified `copilot_edit`,
   not `enrichment`, and shows no document chips. *Gap vs. "capture the doc used
   for enrichment":* covered for the RAG/search path; not for inline attachments.
   Follow-up: thread attachment filenames + `list_documents` hits into the signal.
2. **Coverage findings are not tagged per source document.** The directory report
   aggregates the union of claims; the UI shows totals/score and a flat finding
   list, but a finding does not say which document it came from. *Gap vs. a user
   who expects per-document drill-down in the directory report.* Follow-up
   (Phase 3.x): carry `document_id` on each `CoverageFinding`.
3. **Overreach is union-level only.** `include_overreach` (off by default) flags MDL
   facts unsupported by *any* document; it cannot say "unsupported except by doc X".
   Acceptable for an advisory signal.
4. **Cooperative cancellation is stage-granular.** A superseded run still finishes
   its current in-flight LLM call (extract or judge) before yielding at the next
   `should_cancel` check. Worst case: one extra model call per superseded run. No
   way around this without killable workers.
5. **Single in-process scheduler, multi-worker correctness via DB lease.** Two
   workers can both schedule, but `claim()` (CAS `pending→running`) ensures one
   runs; the debounce is per-process. Cross-worker debounce coalescing is best-effort
   — acceptable because supersession already de-dupes on checksum.
6. **Badge polling, not push.** `CoverageBadge` polls `coverage/status` every 4s
   while `analysing`; a completed run can take up to one poll interval to surface.
   The provenance dialog does not live-update while open (re-open to refresh).
   *Minor gap vs. an expectation of instant update.* Follow-up: SSE on the existing
   project events stream (Phase 3.4).
7. **`refreshSignal={mdlFiles}` re-fetches the badge on any file-list change**,
   including draft edits that do not change the active set. Harmless (status is
   cheap + idempotent) but slightly chattier than necessary.
8. **ESLint could not be run in this checkout** (repo ships a legacy `.eslintrc`
   but the installed ESLint is v10, which requires flat config) — an environment
   issue, not these changes. Prettier + Jest + the enforced pre-commit ruff hook
   are clean; TypeScript types were authored to match existing `api.ts` patterns.
9. **`persistence/models.py` mypy noise** — the new `AiAgentCoverageRun` produces
   the same 2 `Column`-typing errors that all 13 pre-existing models already emit;
   the file is not mypy-clean at baseline. No new logic-file type errors.
