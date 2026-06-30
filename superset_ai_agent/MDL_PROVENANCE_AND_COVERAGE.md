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

# MDL Provenance & Background Coverage — Reference

**Status:** Implemented (Phases 1–3), tested, in the working tree.
**Last updated:** 2026-06-28.
**Purpose:** single context document for the MDL provenance-completeness and
background-coverage features. Read this first; the planning docs below have the
blow-by-blow rationale, decisions, and resumable checklists.

> ### ⚠️ Superseded in part (2026-06-30) — coverage was re-architected
> Three follow-on features changed the coverage model described below. **Where
> this doc and the specs disagree, the specs win:**
> - **Coverage is no longer a provenance event.** `coverage_completed` was
>   **removed** from `PROVENANCE_EVENT_TYPES` and `_PROVENANCE_KIND_BY_EVENT`, so
>   it no longer renders a `kind="coverage"` timeline row (statements at §2/§3/§7
>   about a coverage provenance entry are obsolete). Coverage now surfaces as a
>   **read-only version label** on the version-producing entries (keyed by
>   `mdl_checksum`). → `plan_coverage_labels_and_progress_spec.md`.
> - **Live progress + badge-as-viewer + labels** (Features A/B/C) shipped. New:
>   `coverage_progress` event, `progress` column (migration `0012`),
>   `…/coverage/scores-by-version`, `mdl_checksum` stamped into version-producing
>   event detail. → same spec, §12 "as shipped".
> - **Coverage recovery agent** shipped (flag-gated, off by default): auto-runs an
>   MDL-Copilot turn on a gappy report to propose gap-closing edits as a reviewable
>   changeset; migration `0013`, `recovery_*` columns, `…/coverage/runs/{id}/
>   recovery[/dismiss]`, `recovery_suggestions_ready` event. →
>   `plan_coverage_recovery_agent_spec.md` §11 "as shipped".

**Companion docs (same dir):**
- `plan_provenance_and_coverage_spec.md` — feature spec + rationale (the "why").
- `plan_provenance_and_coverage_impl.md` — Phase 1–2 build checklist + as-built log.
- `plan_provenance_and_coverage_followup_impl.md` — Phase 3 (gap closure) checklist + as-built log.

> Symbols below are stable; line numbers drift (the multi-schema feature and other
> work land continuously). Grep the symbol, not the line.

---

## 1. What these features do

The **MDL Copilot** lets users build a Wren-style semantic layer (MDL = a set of
JSON model/metric/relationship files) for a database schema, by onboarding,
hand-editing, and agentic editing/enrichment. Two cross-cutting concerns:

- **Provenance** — an append-only timeline of *who changed the MDL directory and
  how* (onboarding, user edits, agent/Copilot edits, enrichment passes, coverage
  runs), surfaced in a "Provenance" dialog. **Audit log, not version control.**
- **Coverage** — an advisory score + analysis of *how well the active MDL captures
  the information in the project's uploaded documents* (a reverse reconciliation:
  document claims → are they modeled?). Runs automatically in the background.

### The two problems this work solved
1. **Agent/enrichment edits were invisible.** The Copilot's changeset-apply path
   wrote directly to the store and never emitted provenance, so after the codebase
   moved onboarding/enrichment/modification onto the Copilot, the *primary* edit
   path produced no history. User manual edits emitted one noisy event per save.
2. **Coverage was synchronous, per-document, on-demand.** A user had to pick a
   document and wait. It was moved to an automatic, whole-directory background job
   surfaced in provenance, with supersession on MDL change.

---

## 2. Provenance (Feature A)

### 2.1 Data model
- Stored as append-only `SemanticLayerEvent` rows in table `ai_agent_events`
  (`persistence/models.py::AiAgentEvent`). The full event is JSON in `payload`.
- Read projection: `SemanticLayerEvent` → `ProvenanceEntry`
  (`semantic_layer/schemas.py::provenance_from_event`), a UI-ready row.

Key types in `semantic_layer/schemas.py`:
- `SemanticLayerEventType` (raw event types). Provenance-relevant ones:
  `onboarding_started|completed|failed`, `mdl_created|updated|activated|deleted`,
  `document_enriched`, **`mdl_agent_edit`** (new), **`coverage_completed`** (new).
- `PROVENANCE_EVENT_TYPES` — the frozenset that the timeline includes and that
  **reset purges** (document upload/extract events are excluded so docs survive a reset).
- `ProvenanceKind` (UI label): `onboarding | enrichment | copilot_edit | coverage |
  mdl_created | mdl_updated | mdl_activated | mdl_deleted`.
- **`ActorType = "user" | "agent" | "system"`** — derived by `actor_type_for(kind,
  source_type)`. `actor` (owner id) can't discriminate because the human owns the
  project even when driving the Copilot; origin comes from the file `source_type`
  (`manual`/`uploaded_mdl` → user; `copilot`/`enriched_markdown` → agent;
  `onboarding` → system) and the kind.
- `ProvenanceEntry` fields: `id, kind, status, summary, created_at, actor,
  actor_type, edit_count, first_at, detail`. `PROVENANCE_HISTORY_CAP = 500`.

### 2.2 Where events are emitted (`app.py`)
- **Manual CRUD** (`POST/PATCH/DELETE .../mdl-files`) → `_emit_mdl_provenance`
  emits `mdl_created|updated|activated|deleted` with `detail.source_type`.
- **Onboarding job** emits `onboarding_started|completed|failed`.
- **Copilot apply** (`POST .../copilot/apply` → `apply_project_copilot`) →
  **`_emit_agent_apply_provenance`** (the core gap fix). It re-reads the
  server-authoritative changeset from the conversation
  (`copilot/service.py::changeset_from_conversation`), resolves the documents the
  agent used, and emits **one** event per applied changeset:
  - `document_enriched` (kind `enrichment`) if the turn was grounded on documents,
  - else `mdl_agent_edit` (kind `copilot_edit`).
  - Payload builder: `copilot/service.py::apply_provenance_payload` →
    `detail = {actor, source_type:"copilot", conversation_id, summary, ops:{create,
    update,delete}, paths, documents:[{id, filename}]}`.
- **Coverage job** emits `coverage_completed` (kind `coverage`) — see §3.
- All emission is **best-effort** (try/except, never fails the write).

### 2.3 Document attribution for enrichment (how "documents used" is captured)
- The agent's `search_documents` tool records pulled doc ids onto the changeset:
  `copilot/tools.py::MdlToolset._search_documents` → `_referenced_document_ids` →
  stamped in `build_changeset` as `Changeset.referenced_document_ids`.
- Inline **attachments** (ephemeral text, no id) are stamped as
  `Changeset.referenced_attachments` in the turn/stream handlers after
  `run_copilot` returns. Both feed `detail.documents` (attachments as
  `{id: None, filename}`) and both mark the apply as `enrichment`.
- `list_documents` enumeration is deliberately **not** an enrichment signal (too noisy).

### 2.4 Coalescing of user edits (read-time)
`semantic_layer/schemas.py::coalesce_user_runs` (called in `get_project_provenance`
after the newest-first sort, **before** the history cap). It collapses a maximal
contiguous run of **user `mdl_updated`** entries into one row stamped at the latest
timestamp (`edit_count`, `first_at`, summary "Edited N times", union of `paths`).
- Only `mdl_updated`-by-user coalesces — `create`/`activate`/`delete` are distinct
  lifecycle events shown individually (decision in the impl doc).
- Any non-user entry (agent/enrichment/onboarding/coverage) breaks the run.
- Worked example: user@2pm → agent → user@5pm = **3 rows**; user@2pm → user@5pm
  (no agent between) = **1 row @ 5pm**. Mirrors Google Docs version-history grouping.

### 2.5 Frontend
- `SemanticLayerEditor/MdlProvenanceDialog.tsx` — the timeline. Renders actor tags
  (You/Agent/System), "Edited N times" ranges, enrichment **document chips**, a
  "View conversation" deep-link (`detail.conversation_id`), and a coverage
  **drill-in** ("View report" → fetches the run → `CoverageReportBody`).
- API client: `AiAgentPanel/api.ts` — `getMdlProvenance`, `ProvenanceEntry`,
  `ProvenanceKind`, `ProvenanceActorType`.

---

## 3. Background Coverage (Feature B)

### 3.1 Data model
- Table `ai_agent_coverage_runs` (`persistence/models.py::AiAgentCoverageRun`,
  migration `0009_coverage_runs.py`). Columns: `id, project_id, owner_id,
  mdl_checksum, docs_checksum, status, score, report (JSON), error, created_at,
  updated_at`. Doubles as the **supersession state row**.
- `CoverageRun` pydantic (`copilot/schemas.py`), `CoverageRunStatus = pending |
  running | complete | failed | superseded`.
- `CoverageReport` (`copilot/schemas.py`): `score`, `total/covered/partial/missing`,
  `findings: CoverageFinding[]`, `overreach`, `unsupported`, `warnings`.
- `CoverageFinding` now carries `document_id`/`document_filename` (directory runs
  tag each finding back to its source document).

### 3.2 Store
`semantic_layer/coverage_store.py` — `CoverageRunStore` protocol + `InMemory` +
`SqlAlchemy` impls (mirrors `jobs.py`). Key methods:
- `create / get / latest_complete(project) / active_run(project)`
- `claim(run_id) -> bool` — **compare-and-set** `pending→running` (the cross-worker
  lease so only one worker runs a given run).
- `supersede(project, except_run_id)` — mark in-flight runs `superseded`.
- `find_complete(project, mdl_checksum, docs_checksum)` — **idempotency** lookup.

### 3.3 Engine (`copilot/coverage.py`)
- `run_coverage_audit(...)` — single-document audit (extract claims → build MDL
  facts → judge → aggregate). Now takes `should_cancel: Callable[[],bool]`, polled
  at stage boundaries; raises `CoverageCancelledError` (caller must not persist).
- **`run_directory_coverage(...)`** — the directory-level aggregate (decision D2 =
  **union of all project documents** vs the active MDL). Extracts claims per
  document, judges the union, tags each finding with its source document (guarded
  on equal length vs the judge's degrade paths). No documents → no-op (score 1.0).
- `CoverageDocument(document_id, filename, text)` — the runner's input unit.

### 3.4 Scheduling / supersession (`app.py`, all inside `create_app`)
- `_active_mdl_checksum(project, owner)` — sha256 over sorted `(path, checksum)` of
  **active** files = the "MDL directory version" (drafts don't change it).
- `_coverage_documents(project, owner)` — each doc's text (chunks else extracted_text).
- `_docs_checksum(documents)` — version key for the document set.
- **`_schedule_coverage(project, owner)`** — the centralized "active-set changed"
  hook. Idempotent (skip if a `complete` run matches the checksums), superseding
  (cancel in-flight, create a new `pending` run), no-op when there's nothing to
  audit (no active MDL or no documents). Called from: activation + active-file edit
  (PATCH mdl-files when `status=="active"`), delete-of-active, onboarding completion,
  manual refresh. **Reset** calls `supersede` so a stale run can't finish against
  deleted MDL.
- **`_run_coverage_job(run_id, project, owner)`** — submitted to `active_job_runner`
  (`ThreadJobRunner`). Body: `sleep(debounce)` → `claim()` (exit if lost) →
  `run_directory_coverage(should_cancel = run is superseded)` → `complete()` +
  emit `coverage_completed` provenance event, or `fail()`. On `CoverageCancelledError`
  it exits silently (the newer run reports).
- Config (`config.py`): `wren_coverage_auto_enabled` (default True),
  `wren_coverage_debounce_seconds` (default 0 — supersession carries correctness;
  prod sets 3–5s), `wren_coverage_include_overreach` (default False),
  `wren_copilot_coverage_votes`.

### 3.5 API (all under `/agent/semantic-layer/projects/{project_id}`)
- `GET /coverage/latest` → latest complete `CoverageRun` (score + report) or null.
- `GET /coverage/runs/{run_id}` → one stored run (the provenance drill-in).
- `GET /coverage/status` → `{status: analysing|stale|ready|none, running, stale,
  score, run_id}` for the badge.
- `POST /coverage/refresh` → manually (re)schedule a run on the current active MDL.
- `POST /copilot/coverage` → **deprecated** synchronous per-document audit (kept one
  release as an optional drill-down).
- `GET /events` (SSE, `text/event-stream`, named frames `event: <type>`) — carries
  `coverage_completed` and all semantic events; consumed by the frontend for live updates.

### 3.6 Frontend
- `SemanticLayerEditor/CoverageBadge.tsx` — header badge: latest score / "analysing…"
  / "stale" (active checksum ≠ last complete run), click to re-run
  (`POST /coverage/refresh`). Live via SSE + a 30s fallback poll.
- `SemanticLayerEditor/useProjectEvents.ts` — shared hook subscribing to the project
  SSE stream (named-frame aware: `addEventListener(type, …)`), + `COVERAGE_EVENT_TYPES`.
  Used by the badge and the open provenance dialog.
- Report rendering: `SemanticLayerEditor/CoverageReportModal.tsx::CoverageReportBody`
  (reused by the deprecated dialog and the provenance drill-in); shows per-finding
  source-document tags for directory runs.
- API client: `AiAgentPanel/api.ts` — `getLatestCoverage`, `getCoverageRun`,
  `getCoverageStatus`, `refreshCoverage`, `CoverageRun`, `CoverageStatusInfo`,
  `createProjectSemanticLayerEventSource`.

---

## 4. End-to-end flows

**User hand-edit:** PATCH mdl-files → `_emit_mdl_provenance(mdl_updated, source=manual)`
→ if active, `_schedule_coverage`. Timeline coalesces consecutive saves.

**Agent edit / enrichment:** Copilot turn (optionally with attachments / doc search)
→ changeset persisted on the conversation → user accepts → `POST /copilot/apply`
→ files written as drafts + `_emit_agent_apply_provenance` (enrichment if docs/
attachments used, else copilot_edit). Activation later triggers coverage.

**Coverage:** any active-set change → `_schedule_coverage` (debounce + supersede +
idempotency) → background job audits the active MDL against all docs → persists a
`CoverageRun` + emits `coverage_completed` → badge/dialog update live via SSE; user
opens the report from the provenance timeline.

---

## 5. Files touched / added (by area)

**Backend**
- `semantic_layer/schemas.py` — actor_type/kinds/events, `coalesce_user_runs`.
- `semantic_layer/copilot/schemas.py` — `Changeset.referenced_document_ids/_attachments`,
  `CoverageFinding.document_*`, `CoverageRun`, `CoverageRunStatus`.
- `semantic_layer/copilot/tools.py` — `search_documents` doc-id tracking.
- `semantic_layer/copilot/service.py` — `changeset_from_conversation`,
  `apply_provenance_payload`.
- `semantic_layer/copilot/coverage.py` — `should_cancel`, `CoverageCancelledError`,
  `CoverageDocument`, `run_directory_coverage`, per-finding tagging.
- `semantic_layer/coverage_store.py` — **new** (store).
- `persistence/models.py` — `AiAgentCoverageRun`; `persistence/migrations/versions/0009_coverage_runs.py` — **new**.
- `app.py` — apply provenance emit, coalescing wire-in, coverage store wiring,
  scheduler/job, coverage read endpoints, deprecation.
- `config.py` — coverage config flags.

**Frontend** (`superset-frontend/src/SqlLab/components/AiAgentPanel/`)
- `api.ts` — provenance + coverage types/clients, actor_type/coalesce fields.
- `SemanticLayerEditor/MdlProvenanceDialog.tsx` — rich timeline + coverage drill-in.
- `SemanticLayerEditor/CoverageBadge.tsx` — **new**.
- `SemanticLayerEditor/useProjectEvents.ts` — **new** (shared SSE hook).
- `SemanticLayerEditor/CoverageReportModal.tsx` — per-finding source tag.
- `SemanticLayerEditor/index.tsx` — badge mount (active-set `refreshSignal`).

**Tests:** `tests/unit_tests/superset_ai_agent/` — `test_provenance_schemas.py`,
`test_provenance_api.py`, `test_copilot_service.py`, `test_copilot_api.py`,
`test_copilot_coverage.py`, `test_coverage_store.py`; frontend
`MdlProvenanceDialog.test.tsx`, `CoverageBadge.test.tsx`,
`CoverageReportModal.test.tsx`, `useProjectEvents.test.ts`.

---

## 6. Key design decisions (with rationale)

| Decision | Choice | Why |
| --- | --- | --- |
| Distinguish user vs agent | derived `actor_type` from `source_type`/`kind` | `actor` is always the owner; can't discriminate |
| Agent provenance granularity | one event per **changeset** | matches how users think; one place for the doc list |
| Apply provenance source | re-read persisted changeset by `conversation_id` | server-authoritative, client can't spoof |
| Coalescing site | **read-time** projection | keeps the event log append-only/auditable |
| What coalesces | only user `mdl_updated` | create/activate/delete are distinct events |
| Coverage scope | **union of all project documents** | "did we capture what we ingested" |
| Supersession | DB `claim()` CAS + debounce | cross-worker safe; latest checksum wins |
| Cancellation | cooperative `should_cancel` at stage boundaries | threads aren't killable |
| Live status | SSE (existing stream) + 30s fallback poll | instant, with a safety net |
| Enrichment doc signal | `search_documents` + attachments (not `list_documents`) | avoid marking every turn "enrichment" |

---

## 7. Known gaps / non-goals (next session)

1. **oxlint** is the project linter (not ESLint); its native binary isn't installed
   in this checkout, so lint couldn't run locally (prettier + Jest are clean; CI lints).
2. Attachment chips are **filename-only** (no id/preview) — attachments are ephemeral.
3. Per-document finding tagging relies on the judge preserving finding order
   (length-guarded; degrades to untagged, never mis-attributed).
4. Directory report tags findings by document but doesn't **group/collapse** by document.
5. One SSE connection per surface (shared hook, not de-duped per project).
6. Provenance dialog live-refresh **pauses during a coverage drill-in** (by design).
7. **Coverage on multi-schema projects is untested** — multi-schema landed after
   this feature; `_active_mdl_checksum`/`_coverage_documents` are schema-agnostic so
   should be correct, but add a targeted test.
8. Accepted non-goals: stage-granular (not instant) cancellation; union-level (not
   per-document) overreach.

**Out of scope by design:** diff/restore of MDL versions (provenance is an audit
log); coverage as a deploy gate (advisory only).

---

## 8. Status & tests

- Backend `tests/unit_tests/superset_ai_agent/`: **867 passed, 11 skipped**.
- Feature frontend suites: **all passing** (provenance dialog, badge, report, hook).
- ruff (enforced pre-commit config) + prettier clean; changed logic files mypy-clean
  (`persistence/models.py` has the same `Column`-typing baseline noise as every model).
- **Pre-existing, unrelated failures:** `ExplainDialog.test.tsx` +
  `AiAgentPanel/index.test.tsx` fail on schema-qualified SQL rendering
  (`SELECT … FROM sales.orders`) — collateral from the multi-schema feature in the
  SQL-agent suites; reproduced with this work's changes stashed. Not owned here.
- All changes are in the **working tree** (Phase 1–2 were committed in
  `d83567ab0d "Improve provenance and fix onboard select all"`; Phase 3 is uncommitted).
