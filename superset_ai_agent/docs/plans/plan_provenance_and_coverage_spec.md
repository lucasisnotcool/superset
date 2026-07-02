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

# Feature Spec: MDL Provenance Completeness & Background Coverage

**Scope:** `superset_ai_agent/` (FastAPI backend) + `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/` (React).
**Status:** Draft for review.
**Related docs:** `wren_mdl_copilot.md`, `uploaded_documents_rag_and_crud.md`, `plan_unified_attach_ingestion_spec.md`.

This spec covers two related upgrades to the MDL (semantic-layer) provenance experience:

- **Feature A — Provenance completeness:** record agent/Copilot edits and enrichment passes (with the documents used) in the provenance timeline, and coalesce contiguous user-edit runs into a single timeline entry.
- **Feature B — Background coverage:** move coverage analysis from a synchronous, per-document, on-demand action into an asynchronous, whole-directory background job that auto-runs on MDL change, supersedes stale runs, persists results, and surfaces them inside the provenance dialog.

---

## 1. Current state (as-built findings)

### 1.1 Provenance pipeline

| Concern | Where | Behaviour |
| --- | --- | --- |
| Event store (append-only) | `persistence/models.py` `AiAgentEvent` (`ai_agent_events`); `sqlalchemy_store.py::append_event/list_project_events` | Full `SemanticLayerEvent` persisted as JSON, ordered by `created_at`. |
| Event → timeline projection | `schemas.py::provenance_from_event`, `_PROVENANCE_KIND_BY_EVENT`, `PROVENANCE_EVENT_TYPES` | Maps a subset of event types to `ProvenanceKind`; non-provenance events return `None`. |
| Read API | `app.py::get_project_provenance` → `GET /agent/semantic-layer/projects/{id}/provenance` | Projects events, sorts newest-first, caps at `PROVENANCE_HISTORY_CAP` (500). |
| Manual-CRUD emit | `app.py::_emit_mdl_provenance` (called from `POST/PATCH/DELETE .../mdl-files`) | Emits `mdl_created` / `mdl_updated` / `mdl_activated` / `mdl_deleted` with `detail.actor = owner_id`, `detail.source_type`, `path`, `file_id`, `document_id`. |
| Onboarding emit | `app.py` onboarding job | Emits `onboarding_started/completed/failed` with `mode`, `dataset_ids`, `model_count`, `warnings`. |
| UI | `SemanticLayerEditor/MdlProvenanceDialog.tsx`, mounted from `index.tsx` (history button) | Vertical timeline reusing the AI-Explain shell; renders `ProvenanceEntry { id, kind, status, summary, created_at, actor, detail }`. |

### 1.2 Confirmed gaps

1. **Agent/Copilot edits are invisible.** `POST /agent/semantic-layer/projects/{id}/copilot/apply` (`app.py::apply_project_copilot`, line ~1997) calls `apply_changeset_items()` (`copilot/service.py:173`), which writes **directly** via `store.create/update/delete` and **never calls `_emit_mdl_provenance`**. Since commit `0510be36ab` routed onboarding, enrichment, and modification through the Copilot, the changeset-apply path is now the *primary* way MDL changes — and **none of it is logged to provenance.**
2. **Enrichment provenance is thin / absent.** The legacy `document_enriched` → `enrichment` mapping exists in `_PROVENANCE_KIND_BY_EVENT`, but Copilot-driven enrichment lands through `apply` (gap #1), so it is not emitted at all. Even the legacy path does not durably record *which documents* fed the enrichment or *what* it changed.
3. **User edits are noisy, not coalesced.** Manual edits (`saveFile` → `updateMdlFile`/`createMdlFile`, `index.tsx:492+`) *do* emit one `mdl_updated`/`mdl_created` per save. There is no grouping, so a single editing session produces many adjacent entries.

### 1.3 Coverage pipeline

| Concern | Where | Behaviour |
| --- | --- | --- |
| Audit engine | `copilot/coverage.py::run_coverage_audit` | 4 stages: extract claims → build MDL facts → judge coverage (+ optional overreach) → aggregate. Already staged (cancellation-friendly). |
| Report schema | `copilot/schemas.py::CoverageReport` (`score`, `findings`, `total/covered/partial/missing`, `overreach`, `unsupported`, `warnings`) | Advisory score = `(covered + 0.5·partial)/total`. |
| Trigger | `app.py::run_project_coverage` → `POST .../copilot/coverage` | **Synchronous, on-demand, single-document.** Request = `CoverageRequest { document_id, model, include_overreach }`. |
| Cache | `InMemoryCoverageCache` (`app.py:341`) | Per-worker, keyed on (doc + manifest + model + votes + overreach). |
| UI | `CoverageDialog.tsx` + `CoverageReportModal.tsx` (`CoverageReportBody`) | Document dropdown + "Run audit" button; renders score, finding tags, overreach. |

### 1.4 Async infra already present (reusable for Feature B)

- `semantic_layer/jobs.py`: `JobRunner` (`ThreadJobRunner` / `InlineJobRunner`), `JobStore` (`InMemoryJobStore` / `SqlAlchemyJobStore`), wired as `active_job_runner` (`app.py:311`). Used today for onboarding (`active_job_runner.submit(_run_onboarding)`) and document extraction.
- `SemanticJob { id, kind, status: running|completed|failed, project_id, result: OnboardingResult|None, error }` + `SemanticProjectReadiness { status: empty|indexing|ready|failed, running_job_id }`.
- Materialization checksum: `wren_materializer.materialize_wren_project()` returns a deterministic checksum over the **active** file set — a natural "MDL directory version" key.

---

## 2. Feature A — Provenance completeness

### A.1 Goals

- Every MDL mutation appears in provenance: **user edits, Copilot/agent edits, enrichment passes, onboarding, activation, deletion.**
- Enrichment entries capture **which documents were used** and a human summary of **what changed**.
- Contiguous runs of **user** edits collapse into a **single** entry stamped at the run's **latest** timestamp; any non-user entry (agent/enrichment/onboarding) **breaks the run** and stands alone.

### A.2 Actor-type model (the cross-cutting primitive)

The timeline must distinguish *who* made a change. `detail.actor` is always `owner_id` (the human is the owner even when driving the Copilot), so it cannot be the discriminator. Introduce a derived **`actor_type`**:

| `actor_type` | Source signal | Examples |
| --- | --- | --- |
| `user` | `detail.source_type == "manual"` (or `uploaded_mdl`) | hand edits in the editor |
| `agent` | `detail.source_type == "copilot"`, or `kind ∈ {enrichment}` | Copilot apply, enrichment pass |
| `system` | `kind ∈ {onboarding}` | bulk schema onboarding |

`actor_type` is computed in `provenance_from_event` and added to `ProvenanceEntry`. The UI renders an icon/label per type. **Only `actor_type == "user"` entries coalesce.**

### A.3 Emit provenance from the Copilot apply path (gap #1, #2)

In `apply_project_copilot` (after a successful `apply_changeset_items`), emit **one** provenance event per applied changeset (not per file — see Decision D1), classified as enrichment vs. generic agent edit:

- **Classification:** if the originating turn referenced any documents (message `attachments`, or the agent invoked `search_documents`/`list_documents` during the turn), kind = `enrichment`; else kind = a **new** `copilot_edit`.
- **Detail payload:**
  ```jsonc
  {
    "actor": owner_id,
    "source_type": "copilot",
    "conversation_id": "...",          // for "view conversation" deep-link
    "summary": changeset.message,       // agent's own description of the change
    "ops": { "create": n, "update": n, "delete": n },
    "paths": ["models/orders.json", ...],
    "documents": [                      // enrichment only
      { "id": "...", "filename": "glossary.md" }
    ]
  }
  ```
- **Document capture:** thread document references from the turn into apply. The apply request already carries `conversation_id`; resolve the turn's attachments and any document-tool results from the persisted conversation transcript. MVP: capture message attachments + a `used_document_search: bool`. Richer (Phase 2): capture exact `document_id`s returned by `search_documents`.
- Keep emission **best-effort** (wrap in try/except, mirroring `_emit_mdl_provenance`): a provenance failure must never fail the apply.

> **Note / Decision D1:** one event per *changeset* (recommended) vs. one per *file*. Per-changeset matches how users think ("the agent did X"), keeps enrichment's document list in one place, and avoids flooding the timeline. Per-file would duplicate the document list N times and fragment the story. **Recommendation: per-changeset.** Trade-off: file-level granularity is lost from the timeline, but `detail.paths` preserves the list, and the conversation deep-link gives full detail.

New `ProvenanceKind` values: add `copilot_edit`. (`enrichment` already exists.) Update `_PROVENANCE_KIND_BY_EVENT`, `PROVENANCE_EVENT_TYPES`, the frontend `ProvenanceKind` union, and `KIND_LABELS` in `MdlProvenanceDialog.tsx`.

### A.4 Coalesce user-edit runs (gap #3)

**Where:** read-time projection inside `get_project_provenance`, *after* sort, *before* cap. Rationale (matches Google Docs version-history grouping, and keeps the event log append-only / fully auditable):

- Write-time merging would mutate/destroy granular events, is racy under concurrent writes, and breaks audit integrity.
- Read-time coalescing is a pure view concern and reuses the existing projection step.

**Algorithm** (events sorted **newest-first**):
```
walk entries newest→oldest
  start/extend a "user run" while consecutive entries have actor_type == "user"
  a non-user entry closes the current run and is emitted as-is
emit each user run as ONE entry:
  created_at  = latest (newest) timestamp in the run   // shown time
  first_at    = earliest timestamp in the run          // for "edited N times since…"
  edit_count  = len(run)
  summary     = "Edited N times" (N>1) | single summary (N==1)
  detail.paths = union of paths touched in the run
  id          = id of the newest entry in the run (stable for React keys)
```

**Worked example (matches the requested behaviour):**
- User edits 14:00, user edits next-day 17:00, nothing between → **1 entry @ 17:00** ("Edited 2 times since yesterday 14:00").
- User 14:00 → **agent** → user 17:00 → **3 entries**: `[user @14:00]`, `[agent]`, `[user @17:00]`. The agent entry sits between the two user edits, so they fall in different runs.

**Cap interaction:** coalesce *before* applying `PROVENANCE_HISTORY_CAP` so the cap counts displayed rows, not raw events. (Acceptable: a pathological single run of >500 raw user edits is still one row; the cap protects against many distinct entries.)

`ProvenanceEntry` gains optional `actor_type`, `edit_count`, `first_at`.

### A.5 UI changes (`MdlProvenanceDialog.tsx`)

- Render `actor_type` as an icon + label (user / agent / system).
- For coalesced user runs (`edit_count > 1`), show "Edited N times" with a range (`first_at`–`created_at`).
- For `enrichment`, render the document chips from `detail.documents` and a "View conversation" link (deep-link via `conversation_id` to the Copilot thread).
- For `copilot_edit`, show `detail.summary` + ops counts + "View conversation".

---

## 3. Feature B — Background, directory-level coverage

### B.1 Goals

- Coverage runs **asynchronously** as a background job over the **latest active MDL directory**, aggregated across the project's documents (not a single doc picked by the user).
- Each **successful, complete** run persists its **score + analysis**; users open it from the **provenance dialog**.
- When the MDL directory changes mid-run, **cancel the stale run and immediately start a fresh one** on the latest version (single-flight + supersession + debounce — the standard "cancel stale work" pattern).

### B.2 What "directory coverage" audits

The existing engine audits one document's claims against the MDL (+ optional overreach). Directory coverage = run the audit for the **active MDL set** against the **union of the project's documents**, then aggregate into one `CoverageReport` (sum totals, weighted score, concatenated findings tagged by `document_id`/`filename`; overreach computed once against the union). If the project has **no documents**, skip (record a "no documents" no-op, not a failure).

> **Decision D2 — aggregation scope.** (a) Union over all project documents vs. (b) MDL-only self-consistency. The engine is document-grounded, and the user's value is "did we lose anything from the docs we ingested." **Recommendation: (a) union over all project documents.**

### B.3 Trigger & "latest version"

- The MDL **directory version** = the materialization checksum over the **active** file set (`wren_materializer`). Drafts do not change the directory; only active-set changes matter.
- **Triggering events:** activation (`mdl_activated`), deletion of an active file, onboarding completion, reset. (A draft create/update does **not** trigger.)
- On each trigger: compute the new active checksum; if it differs from the last *completed* run's checksum, (re)schedule coverage.

### B.4 Supersession, debounce, and cancellation

- **State row per project** (DB-backed, so it works across workers): a `CoverageRun` with `status ∈ {pending, running, complete, failed, superseded}` and the `mdl_checksum` it targets.
- **Debounce:** coalesce triggers within a short window (default 3–5 s) so a multi-file deploy spawns one run, not many. (Confirmed industry pattern: debounce the *trigger*, then run.)
- **Supersession:** when a new trigger arrives with a different checksum, mark the in-flight run `superseded` and start a new one targeting the latest checksum. The newest checksum always wins.
- **Cooperative cancellation:** `ThreadJobRunner` cannot kill threads, so add an optional `should_cancel: Callable[[], bool]` to `run_coverage_audit`, checked **between stages and between per-claim judgements**. On cancel, return early without persisting. (A single in-flight LLM call cannot be interrupted; supersession takes effect at the next stage boundary — acceptable latency.)
- **Idempotency / cost guard:** before running, if a `complete` run already exists for the exact `(mdl_checksum, docs_checksum, model, votes)`, reuse it instead of re-running (persistent extension of the existing `InMemoryCoverageCache`).

### B.5 Persistence

New table `ai_agent_coverage_runs` (Alembic migration `0009_*`, continuing the linear history after `0008_conversation_kind_project`):

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String(36) PK | |
| `project_id` | String(36), indexed | |
| `owner_id` | String(255), indexed | captured at trigger time (background auth) |
| `mdl_checksum` | String(128), indexed | active-set version this run targets |
| `docs_checksum` | String(128) | union-of-documents version (idempotency) |
| `status` | String(32), indexed | pending / running / complete / failed / superseded |
| `score` | Float, nullable | denormalized for cheap "latest score" badge |
| `report` | JSON, nullable | full `CoverageReport.model_dump()` |
| `error` | Text, nullable | |
| `created_at` / `updated_at` | DateTime(tz), indexed | |

Rationale for a dedicated table over reusing `SemanticJob`: `SemanticJob.result` is typed to `OnboardingResult`; reports can be large; and we want a queryable history keyed by checksum (latest report, idempotency, "report for this version"). It doubles as the supersession state row (B.4).

On `complete`, also emit a lightweight provenance event `coverage_completed` (kind `coverage`) with `detail = { run_id, score, total, covered, partial, missing, unsupported, mdl_checksum }`. This is what makes coverage appear in the timeline; the heavy report stays in `ai_agent_coverage_runs`.

### B.6 API

- `GET /agent/semantic-layer/projects/{id}/coverage/latest` → latest `complete` `CoverageRun` (score + report) or `null`.
- `GET /agent/semantic-layer/projects/{id}/coverage/runs/{run_id}` → full stored report (used by the provenance dialog drill-in).
- `GET /agent/semantic-layer/projects/{id}/coverage/status` → `{ status, mdl_checksum, running }` for a live "analysing…" badge.
- `POST /agent/semantic-layer/projects/{id}/coverage/refresh` → manual re-trigger (force a run on current active set), for an explicit "Re-run" button.
- **Deprecate** the synchronous `POST .../copilot/coverage` once the background path ships (keep one release for the per-document deep-dive, see D3).

### B.7 UI

- Provenance timeline shows a `coverage` entry per completed run: score + tag counts; clicking opens `CoverageReportBody` (reuse existing component) inside the dialog.
- A persistent **coverage badge** in the editor header (latest score, "analysing…" while a run is in flight, "stale" if active checksum ≠ latest completed run's checksum) with a "Re-run" action calling `/coverage/refresh`.
- `CoverageDialog.tsx`'s manual per-document trigger is repurposed: either removed, or kept as an optional "deep-dive on one document" drill-down (D3).

> **Decision D3 — keep or drop per-document coverage.** **Recommendation:** make directory-level the default surfaced in provenance; keep per-document coverage as an optional manual drill-down for one release, then remove if unused.

---

## 4. Risks & mitigations

| # | Risk | Mitigation |
| --- | --- | --- |
| R1 | **LLM cost / thrash** from auto-running coverage on every change. | Debounce triggers; supersede stale runs; persistent idempotency cache keyed on `(mdl_checksum, docs_checksum, model, votes)`; only trigger on active-set changes, never on drafts. |
| R2 | **Multi-worker supersession.** `ThreadJobRunner` is per-process; two workers could both run coverage. | DB-backed `CoverageRun` state with a claim/lease (compare-and-set `status` to `running` with the target checksum); losers no-op. Cross-worker correctness lives in the DB row, not process memory. |
| R3 | **Threads can't be force-killed.** | Cooperative `should_cancel` between stages/claims; worst case one extra in-flight LLM call before the stale run yields. |
| R4 | **Coalescing hides edits / breaks audit.** | Coalesce at **read** time only; raw events remain append-only and fully recoverable; `edit_count`/`first_at` expose the collapsed range; optional "expand" affordance can list raw events (Phase 2). |
| R5 | **Provenance emit failure blocks a write.** | Best-effort emit (try/except), identical to `_emit_mdl_provenance`. |
| R6 | **Document attribution for enrichment is fuzzy** (agent may read docs it doesn't use). | MVP: record attachments + `used_document_search` flag; Phase 2: capture exact doc ids from `search_documents` tool results. Label as "documents referenced," not "documents used." |
| R7 | **Coverage score misread as a gate.** | Keep `CoverageReport`'s "advisory, not a gate" framing in UI copy; show confidence/votes; never block deploy on score. |
| R8 | **Cap interacts with coalescing.** | Coalesce before cap so the cap bounds displayed rows. |
| R9 | **Background job auth/identity.** | Capture `owner_id` at trigger time and run under it (same pattern as onboarding/extraction background jobs). |

---

## 5. Decision points (recommendations in **bold**)

- **D1** — Copilot provenance granularity: per-changeset vs per-file. **Per-changeset.** (§A.3)
- **D2** — Directory-coverage scope: union of all docs vs MDL-only. **Union of all project documents.** (§B.2)
- **D3** — Keep per-document coverage as a drill-down? **Default to directory-level; keep per-doc one release, then remove if unused.** (§B.7)
- **D4** — New kind `copilot_edit` vs overloading `mdl_updated` + `actor_type`. **Add `copilot_edit`** for clear labelling; `actor_type` still drives coalescing. (§A.3)
- **D5** — Coverage trigger surface: emit from `_emit_mdl_provenance`/apply paths vs a dedicated "active-set changed" hook. **Dedicated hook** invoked wherever the active set changes (activation, delete-active, onboarding-complete, reset), so triggering is centralized and testable. (§B.3)
- **D6** — Debounce window default. **3–5 s** (tune from telemetry). (§B.4)

---

## 6. Phased rollout

1. **Phase 1 — Provenance completeness (Feature A).**
   - Add `actor_type` to projection; emit `copilot_edit`/`enrichment` from apply (best-effort) with document refs; read-time user-run coalescing; UI: actor icons, "edited N times", document chips, conversation deep-link.
   - No migration required (reuses `ai_agent_events`).
2. **Phase 2 — Background coverage (Feature B).**
   - Migration `0009_coverage_runs`; directory-level aggregate audit; `should_cancel` in `run_coverage_audit`; trigger hook + debounce + DB-backed supersession; new GET/refresh endpoints; provenance `coverage_completed` event; UI badge + report drill-in.
   - Deprecate sync `/copilot/coverage` after one release.
3. **Phase 3 — Polish.**
   - Exact document-id attribution from `search_documents`; expandable raw-event view under a coalesced run; coverage trend (score over versions) in the dialog.

---

## 7. Testing

- **Backend unit (`tests/unit_tests/superset_ai_agent/`):**
  - apply emits exactly one `copilot_edit`/`enrichment` event with correct `detail` (docs, ops, conversation_id); emit failure does not fail apply.
  - `provenance_from_event` sets `actor_type` correctly per `source_type`/`kind`.
  - coalescing: the three worked-example sequences in §A.4 (pure-user run; user→agent→user; single edit) produce the expected row counts/timestamps; cap applied post-coalesce.
  - coverage: aggregate over multiple docs; empty-docs no-op; `should_cancel` aborts mid-run without persisting; idempotency cache reuse; supersession marks the stale run `superseded`; checksum mismatch ⇒ "stale".
- **Frontend (Jest + RTL):** `MdlProvenanceDialog` renders actor types, coalesced "edited N times", document chips, and opens `CoverageReportBody` from a `coverage` entry; badge states (score/analysing/stale).
- **Migration:** `0009` upgrade/downgrade round-trips; linear-history check after `0008_conversation_kind_project`.

---

## 8. Out of scope

- Diff/restore of MDL versions from the timeline (provenance is an audit log, not version control).
- Real-time push of coverage status (polling is sufficient; SSE is a later option, reusing the project events stream).
- Coverage as a deploy gate.
