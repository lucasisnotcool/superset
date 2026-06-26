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

# Spec — Selective-table onboarding & MDL provenance dialog

**Status:** proposal (not implemented). Source-audited against the working tree on
2026-06-26. Companions: `plan_onboarding_gating_user_flow.md` (File 1, onboarding
gate — landed), `plan_copilot_parity_spec.md`/`plan_copilot_parity_impl.md` (Copilot
persistence — landed). As-built record: `wren_mdl_copilot.md` §AB.

Two related but independent features over the semantic-layer (MDL) lifecycle:

- **Feature A — Selective onboarding.** Let the user choose *which* tables in a
  schema are onboarded, from a possibly huge schema, with bulk multi-select, and
  pass only the selected set to onboarding. The listing must stay **O(1) in user
  ops regardless of schema size** (paginated / load-on-scroll).
- **Feature B — MDL provenance dialog.** A time-ordered history of operations on
  the MDL directory (onboarding, enrichment, user CRUD), reusing the AI Explain
  dialog's design language, resetting when the MDL directory is reset.

This document is a spec + decision record, not an implementation. Each feature has:
current-state evidence → design → decision points (with evidence + recommendation)
→ touchpoints → risks/mitigations → phased checklist → tests.

---

# Feature A — Selective-table onboarding

## A0. Goal

On the empty/onboard rail, instead of onboarding the whole schema, the user picks
tables to onboard from a searchable, paginated, multi-selectable list, then
onboards only the selected set. User operations are O(1) in schema size.

## A1. Current state (source-backed)

**Onboarding is dataset-based, whole-schema, no selection.**
- Route: `POST /agent/semantic-layer/projects/{id}/onboard` takes **no body**
  (`app.py` `onboard_semantic_project`, ~`:2081-2104`); 202 + `SemanticJob`.
- It builds context via `_onboarding_context` (`app.py:~1982-2008`), which prefers
  `get_full_schema` over `get_context` and passes **no `dataset_ids`** —
  `AgentQueryRequest(question=…, database_id=…, catalog_name=…, schema_name=…)`.
- `get_full_schema` (`context/superset_metadata.py:~79-110`) calls
  `superset_client.list_datasets(... limit=wren_schema_table_scan_limit)` with **no
  `dataset_ids`** → the whole schema's Superset datasets.
- Onboarding writes **one MDL file per dataset**:
  `deterministic_base_model_proposals` iterates `superset_context.datasets`
  (`integrations/wren/client.py:~447-471`) → `model_from_dataset`
  (`integrations/wren/mdl_exporter.py:~78-96`); `onboard_schema_project`
  (`semantic_layer/onboarding.py:59-147`) creates each with `source_type="onboarding"`.

**The unit of onboarding is a Superset *dataset*, not a raw physical table.**
`model_from_dataset` needs `dataset.columns`/`dataset.metrics`, which only exist for
registered Superset datasets (`DatasetMetadata`, `integrations/superset/client.py:74-76`).

**Key enabling facts already present:**
- `AgentQueryRequest.dataset_ids: list[int]` exists (`schemas.py:50`).
- `SupersetClient.list_datasets(..., dataset_ids=...)` already filters
  `SqlaTable.id.in_(dataset_ids)` and **ignores the limit when ids are given**
  (`integrations/superset/client.py:284-288`). `get_agent_context` forwards
  `dataset_ids` (`:290-310`).
- Superset's **dataset REST API** `DatasetRestApi` (`superset/datasets/api.py:95`)
  has `search_columns`/`search_filters` (`:274-278`) and standard FAB rison
  pagination (`page`, `page_size`, `filters`, `order_column`) — i.e. server-side
  paginated + searchable listing of datasets by `database`, `schema`, `table_name`.

**The `/api/v1/database/{pk}/tables/` endpoint cannot paginate.** It returns ALL
tables at once (`superset/commands/database/tables.py` `TablesDatabaseCommand.run`,
`count = total`, no limit/offset); the FE hook flags this as a TODO
(`superset-frontend/src/hooks/apiResources/tables.ts:~109`). Underlying
`Database.get_all_table_names_in_schema` (`superset/models/core.py:~979`) returns the
full set from the SQLAlchemy inspector (cached). **It is fundamentally all-at-once.**

**Frontend onboarding trigger.** `runOnboard(projectId)` in
`SemanticLayerEditor/index.tsx:~528-560` calls `runOnboarding(projectId)`
(`api.ts`), polls the job, toasts, `refresh()`. The Onboard CTA lives in
`CopilotPanel.tsx` bootstrap view (`onOnboard` prop, ~`:636-705`), wired from
`index.tsx:~908` as `onOnboard={() => runOnboard(project.id)}`.

**Reusable multi-select precedent.** `FoldersEditor`
(`superset-frontend/src/components/Datasource/FoldersEditor/`):
- `useState<Set<string>>` selection + `lastSelectedItemIdRef` (`index.tsx:~101-104`).
- Shift-click **range select** (`handleSelect`, `index.tsx:~424-470`).
- Checkbox row toggling with `e.shiftKey` (`TreeItem.tsx:~274-288`).
- Select-all/deselect-all toolbar (`index.tsx:~360,615`).
- Virtualization via `VirtualizedTreeList` (react-window `VariableSizeList`,
  `FoldersEditor/VirtualizedTreeList.tsx`).
Right-click context menu precedent: `Chart/ChartContextMenu/useContextMenu.tsx`
(net-new for table selection).

## A2. Decision points (evidence + recommendation)

### DP-A1 — What is the selectable unit: Superset **datasets** or raw DB **tables**? ★ pivotal
- **Evidence:** onboarding consumes `DatasetMetadata` (columns/metrics) and models
  *datasets*; raw tables without a registered dataset cannot be modeled by
  `model_from_dataset`. The dataset API paginates server-side; the `/tables/`
  endpoint does not (and can't without backend work in core Superset).
- **Recommendation: datasets.** List **Superset datasets** for the (database,
  schema). This (1) maps 1:1 to what onboarding can actually model, (2) gets
  server-side pagination + search **for free** from `/api/v1/dataset/`, satisfying
  the O(1) requirement, and (3) needs only a tiny agent backend change
  (thread `dataset_ids`). Label the UI "Tables" (datasets are 1 table each here) to
  match user language; optionally show a hint that only registered tables appear.
- **Rejected: raw DB tables.** Would require net-new server-side pagination on the
  core `/tables/` endpoint (schema + command + model + engine-spec changes — see the
  pagination audit), AND a way to model unregistered tables (no columns/metrics
  available) — a much larger, cross-cutting lift. Defer as a costed follow-up only
  if product requires onboarding tables that aren't Superset datasets.

### DP-A2 — Listing transport: call Superset's dataset API directly, or proxy via the agent?
- **Evidence:** the panel is a Superset-frontend component with session creds; the
  dataset API is first-party and rison-paginated. The agent has no list endpoint and
  no pagination infra.
- **Recommendation: call `/api/v1/dataset/` directly from the FE** (rison `filters`:
  `database`=id, `schema`=name, `table_name` `ct` search; `page`/`page_size`;
  `order_column=table_name`). Avoids a pass-through endpoint and reuses Superset's
  caching/permissions. The agent only consumes the resulting `dataset_ids`.
- **Alternative:** a thin agent proxy `GET …/projects/{id}/onboarding-candidates`
  if cross-origin/CSRF to the core API from the agent panel is a problem in the
  deployment. Decide from how the panel already calls core APIs (it already uses
  `SupersetClient`/relative `/api/v1/...` for other data — confirm at build time).

### DP-A3 — "Select all" semantics on a huge schema (O(1)). ★
- **Evidence:** a 10k-table schema cannot be enumerated client-side to "select all"
  without defeating pagination. Onboarding's *default* (no `dataset_ids`) already
  means "all in schema."
- **Recommendation: dual-mode selection model.**
  - `mode="include"`: an explicit `Set<datasetId>` of chosen rows (the common case).
  - `mode="all"`: "all matching the current schema (+ optional search)", carried as a
    boolean + an `excluded: Set<datasetId>` (Gmail-style), so deselecting a few from
    "select all" stays O(1).
  The **onboard request** encodes this (see DP-A4). This keeps both the UI and the
  payload O(1) regardless of schema size.

### DP-A4 — Onboard request contract.
- **Recommendation:** add an `OnboardingRequest` body (backward-compatible: empty
  body ≡ today's whole-schema onboard):
  ```
  class OnboardingRequest(BaseModel):
      mode: Literal["all", "include"] = "all"
      dataset_ids: list[int] = []          # mode="include": exactly these
      exclude_dataset_ids: list[int] = []  # mode="all": all-in-schema minus these
      search: str | None = None            # mode="all": narrow "all" by name filter
  ```
  Backend resolution:
  - `include` → `_onboarding_context(..., dataset_ids=request.dataset_ids)` →
    `get_agent_context`/`get_full_schema` forwarding `dataset_ids` →
    `list_datasets` filters by id (already supported).
  - `all` (+search/excludes) → resolve the dataset_ids server-side by querying
    datasets for (db, schema[, search]) and removing excludes, then proceed as
    `include`. (Reuses `list_datasets`; for very large "all", resolve in pages.)
  - empty/absent body → `mode="all"`, no excludes → **exact current behavior**.

### DP-A5 — Shift-range select across a paginated/virtualized list.
- **Evidence:** `FoldersEditor` range-select operates over a fully materialized
  `flattenedItems` array; with server pagination, rows between two anchors may be
  unloaded.
- **Recommendation:** range-select only over **currently loaded** rows (the
  contiguous loaded window), and document it. True "select range across unloaded
  rows" would require fetching the id range — out of scope for v1; "Select all
  (matching)" (DP-A3) covers the bulk case. Cmd/Ctrl-click = toggle a single row
  without clearing others (natural with the `Set` model). Plain click on a checkbox =
  toggle that row.

### DP-A6 — Where the selection UI lives.
- **Recommendation:** a **modal** ("Select tables to onboard") opened from the
  existing Onboard/Retry CTA in `CopilotPanel.tsx`, returning the selection to
  `runOnboard`. Keeps the bootstrap rail unchanged (File 1) and avoids embedding a
  heavy virtualized list in the always-mounted panel. The modal owns the list +
  selection; confirming dispatches onboarding with the encoded selection.

## A3. Design (recommended path)

1. **List** datasets for (database_id, schema) via `/api/v1/dataset/` with rison
   pagination + `table_name` search. Render rows in a virtualized list
   (react-window) with a checkbox per row, reusing `FoldersEditor`'s `Set`-based
   selection + shift-range (over loaded rows) + select-all/deselect-all + a
   right-click context menu (select all / deselect all). Show a live **selected
   count** ("N selected" / "All N matching"). Load-on-scroll via react-window
   `onItemsRendered` → fetch next page (or `react-window-infinite-loader` if already
   a dependency; else AsyncSelect-style paginated loader).
2. **Confirm** → `runOnboard(projectId, selection)` builds the `OnboardingRequest`
   and POSTs it.
3. **Backend** threads `dataset_ids` through `_onboarding_context` → context provider
   → `list_datasets`; only selected datasets become MDL files. Job/polling/events
   unchanged.

## A4. Touchpoints
- **BE schema:** new `OnboardingRequest` (`semantic_layer/schemas.py`).
- **BE route:** `onboard_semantic_project` accepts the body; pass selection to
  `_onboarding_context` (`app.py`).
- **BE context:** `_onboarding_context` forwards `dataset_ids`; ensure
  `get_full_schema`/`get_context` honor `request.dataset_ids` (today `get_full_schema`
  ignores them — `context/superset_metadata.py`). Simplest: when ids present, use the
  id-forwarding path (`get_agent_context`/`list_datasets(dataset_ids=…)`).
- **BE "all" resolution:** helper to resolve `mode="all"`+search+excludes →
  dataset_ids (reuses `list_datasets`).
- **FE api client:** `runOnboarding(projectId, selection?)` (`api.ts`); a dataset-list
  fetch helper hitting `/api/v1/dataset/` (or reuse an existing list hook).
- **FE UI:** new `OnboardingTablePicker` modal; wire from `CopilotPanel` Onboard CTA;
  thread selection to `index.tsx` `runOnboard`.
- **Onboarding metadata for provenance (Feature B):** record which dataset_ids/paths
  were onboarded (feeds B's onboarding entry).

## A5. Risks & mitigations
- **R-A1 Datasets ≠ physical tables.** Tables not registered as datasets won't
  appear/onboard. *Mitigation:* clear empty-state copy ("Only tables registered as
  datasets can be onboarded"); product decision DP-A1; costed raw-table follow-up.
- **R-A2 Cross-API auth/CSRF** calling core `/api/v1/dataset/` from the agent panel.
  *Mitigation:* confirm the panel already calls core APIs; else add the thin proxy
  (DP-A2 alternative).
- **R-A3 "Select all" on 10k tables.** *Mitigation:* DP-A3 select-all-matching +
  excludes; backend resolves in pages; never materialize all ids client-side.
- **R-A4 Range-select across unloaded rows.** *Mitigation:* DP-A5 (loaded-window
  only) + select-all for bulk.
- **R-A5 Stale dataset list** (datasets added after the page cached). *Mitigation:*
  a refresh affordance; the dataset API isn't the `/tables/` cache.
- **R-A6 Onboarding cost still O(schema) server-side** when "all". *Mitigation:* this
  is inherent to onboarding work (one file per table); the **UI/user-ops** stay O(1)
  as required; existing job/async + `wren_schema_table_scan_limit` bound it.

## A6. Phased checklist (Feature A)
- [ ] **A-1 BE schema:** add `OnboardingRequest` (mode/dataset_ids/exclude/search).
- [ ] **A-2 BE route + context:** accept body; forward `dataset_ids`; resolve
      `mode="all"`; empty body ≡ current behavior. Tests: include-subset onboards only
      those; all-minus-excludes; empty body unchanged.
- [ ] **A-3 FE client:** dataset-list fetch (paginated/search) + `runOnboarding`
      selection param. Tests: rison params correct; pagination advances.
- [ ] **A-4 FE picker modal:** virtualized list + checkbox + shift-range (loaded) +
      Cmd/Ctrl toggle + select-all/deselect-all + right-click menu + selected count +
      load-on-scroll. Tests (RTL): toggle, shift-range over loaded rows, select-all
      matching, count, "Onboard N" dispatches correct payload.
- [ ] **A-5 Wire** CopilotPanel CTA → modal → `runOnboard(selection)`; empty selection
      guard. Tests: confirm passes selection; cancel onboards nothing.
- [ ] **A-6 Provenance hook:** onboarding records onboarded dataset_ids/paths (feeds B).

---

# Feature B — MDL provenance dialog

## B0. Goal

A dialog that, on open, shows the **editing history of the MDL directory** as a
time-ordered, sequential timeline (no content diffs). Captures: **Onboarding**
(which tables, when, metadata), **Enrichment** (which document, when), **User CRUD**
(which MDL file, when). **Resets when the MDL directory is reset.** Reuses the AI
Explain dialog's design language for a consistent, intuitive sequential UI.

## B1. Current state (source-backed)

**Event persistence exists.**
- `AiAgentEvent` table (`persistence/models.py:~197-212`): `id`, `project_id`,
  `owner_id`, `scope` JSON, `type`, `payload` JSON, `created_at` (indexed).
- `SemanticLayerEvent` schema + `SemanticLayerEventType`
  (`semantic_layer/schemas.py:~42-116`): today's types are document
  (`document_uploaded`/`document_extracted`/`index_failed`) and onboarding
  (`onboarding_started`/`onboarding_completed`/`onboarding_failed`).
- Emit helper `_append_semantic_event` (`app.py:~3060-3081`).
- Read routes: `GET /agent/semantic-layer/projects/{id}/events` (and scope variant)
  stream stored events as SSE via `to_sse` (`app.py:~2849-2870`,
  `semantic_layer/events.py:25-31`). FE: `createProjectSemanticLayerEventSource`.

**Already emitted:** onboarding start/complete/fail (with `model_count`,
`activated_count` in payload, `app.py:~2023-2074`); document upload/extract
(`app.py:~844-869`).

**MDL file metadata carries provenance fields** (`semantic_layer/schemas.py:186-204`;
table `persistence/models.py:316-344`): `source_type` (`onboarding|manual|
enriched_markdown|copilot|uploaded_mdl`), `source_document_id`, `created_by`,
`updated_by`, `created_at`, `updated_at`, `deleted_at`, `checksum`, `status`.

**MISSING for a full provenance timeline:**
- **No events on MDL file CRUD.** `create_mdl_file` (`app.py:~993-1022`),
  `update_mdl_file` (`~1181-1245`, incl. draft→active activation),
  `delete_mdl_file` (`~1250-1274`) emit **no events**. The store stamps
  `created_by/updated_by/updated_at` but keeps no append-only log — only the latest
  state. So "the sequence of user edits" is not recoverable from files alone.
- **Enrichment → file link is weak.** Enrichment returns a *proposal*
  (`enrich_project_document`, `app.py:~2319-2415`); the Copilot applies it as files
  with `source_type="copilot"` (`copilot/service.py:~187-200`) **not**
  `enriched_markdown`, and **without** `source_document_id`. So "which document
  enriched which file" isn't captured at apply time.
- **Reset does NOT reset provenance.** `reset_semantic_project` (`app.py:~2106-2135`)
  soft-deletes MDL files only; **events persist**. Per the goal, the provenance view
  must reset on MDL reset — but blanket-deleting project events is wrong because
  **documents survive reset** (reset deletes MDL, not documents), so their
  `document_uploaded` events should not vanish.

## B2. Decision points (evidence + recommendation)

### DP-B1 — Provenance source: a typed event log (recommended) vs. reconstruct from files.
- **Evidence:** files keep only latest `updated_at` (no edit sequence); events are an
  append-only log already wired (table + emit helper + read route).
- **Recommendation: extend the event log.** Add MDL-CRUD event types and emit them at
  the three routes. The provenance dialog reads the log. Reconstruction-from-files
  can't show the *sequence* of edits the goal asks for.

### DP-B2 — How "reset on MDL reset" works without nuking document events. ★
- **Evidence:** reset deletes MDL files only; documents + their events persist;
  events are shared across the document-RAG suite and onboarding.
- **Recommendation: a provenance epoch.** Add `provenance_epoch` (int, default 0) to
  the semantic project; `reset_semantic_project` increments it. Stamp each
  provenance-relevant event with the current epoch (in `payload` or a column). The
  provenance dialog shows only events at the **current** epoch. This (a) resets the
  *view* on reset as required, (b) preserves the audit row history, (c) leaves
  document events alone (they simply predate the new epoch / are filtered by type).
- **Rejected: delete events on reset.** Loses audit; risks deleting still-relevant
  document events; harder to get right than an epoch filter.
- **Simpler alt (if no audit retention needed):** delete only **MDL-CRUD + onboarding**
  events for the project on reset (leave document events). Acceptable but less clean
  than an epoch; choose epoch unless a migration is undesirable.

### DP-B3 — Read API: reuse the SSE stream or add a JSON list endpoint?
- **Evidence:** the events route streams SSE (good for live), but a modal wants a
  finite ordered list; huge histories need bounding.
- **Recommendation: a JSON `GET …/projects/{id}/provenance`** returning an ordered
  `ProvenanceEntry[]` for the current epoch, newest-or-oldest-first (UI choice),
  with a sane cap/pagination. Keeps the dialog simple and bounded; the SSE stream
  stays for live editor updates. (Alternatively reuse the existing events SSE and
  collect to a list client-side — fine for small histories, but a JSON list is
  cleaner and boundable.)

### DP-B4 — Provenance entry shape (reuse Explain's design, not necessarily its type).
- **Evidence:** Explain dialog (`ExplainDialog.tsx:1-274`) renders `AgentStep[]` as a
  vertical, status-dotted, grouped timeline in an antd `Modal`, with a per-step
  detail renderer and a copy-JSON affordance; backend `AgentStep`
  (`schemas.py:388-400`) has `kind/status/summary/started_at/duration_ms/detail`.
- **Recommendation:** introduce a `ProvenanceEntry` (don't overload `AgentStep`,
  which is SQL-graph-shaped):
  ```
  ProvenanceEntry:
    id: str
    kind: Literal["onboarding","enrichment","mdl_created","mdl_updated",
                  "mdl_activated","mdl_deleted","reset"]
    status: Literal["ok","warning","error"] = "ok"
    summary: str                      # e.g. "Onboarded 12 tables", "Edited models/orders.json"
    created_at: datetime
    actor: str | None                 # owner_id / "copilot" / "onboarding"
    detail: dict                      # kind-specific: table_count, dataset_ids, path,
                                      # file_id, source_type, document_id/filename, status_from/to
  ```
  **Reuse the Explain UI shell** (Modal, vertical timeline, status dots, summary
  header, copy-JSON) for visual/design consistency; render `ProvenanceEntry.detail`
  with a small typed switch mirroring `AgentStepDetail`'s pattern. No grouping by
  `attempt_index` (provenance is linear time) — group by day or show a continuous
  timeline.

### DP-B5 — What "Onboarding metadata" includes.
- **Recommendation:** capture `table_count`, the onboarded `dataset_ids` (or names),
  `activated_count`, `warnings`, and `mode` (all vs selected) from Feature A's
  result — onboarding already returns `OnboardingResult{model_count, activated_count,
  warnings}` (`semantic_layer/schemas.py:244-251`); thread the selected dataset
  identifiers in.

## B3. Design (recommended path)
1. **Emit provenance events** at the MDL-CRUD routes (`create`/`update`/`delete`,
   incl. activation as `mdl_activated`) and enrich the onboarding/enrichment emits
   with structured detail; stamp the current `provenance_epoch`.
2. **Epoch reset:** `reset` increments the project's `provenance_epoch` and emits a
   `reset` entry at the new epoch (a clean "history starts here" marker).
3. **Read endpoint:** `GET …/projects/{id}/provenance` → ordered `ProvenanceEntry[]`
   for the current epoch (bounded).
4. **Dialog:** `MdlProvenanceDialog`, opened from a header button in the editor /
   CopilotPanel, reusing the `ExplainDialog` shell (Modal + timeline + dots + detail
   switch + copy-JSON). Intuitive, sequential, time-ordered.

## B4. Touchpoints
- **BE model + migration:** `provenance_epoch` on the semantic project (Alembic
  under `persistence/migrations/`, next after `0008_conversation_kind_project`).
- **BE schema:** `ProvenanceEntry` + new event types/detail
  (`semantic_layer/schemas.py`).
- **BE emits:** MDL CRUD routes (`app.py` create/update/delete), onboarding emits
  (structured detail + epoch), enrichment apply (set `source_type="enriched_markdown"`
  + `source_document_id`, emit) — note the apply path is in `copilot/service.py`.
- **BE reset:** increment epoch + emit `reset` (`app.py reset_semantic_project`).
- **BE read route:** `GET …/projects/{id}/provenance` (+ epoch filter), reusing the
  store; add `list_provenance` to the semantic-layer store.
- **FE client:** `getMdlProvenance(projectId)` (`api.ts`).
- **FE UI:** `MdlProvenanceDialog` (fork `ExplainDialog` shell) + a "Provenance"/
  "History" button (header of editor or CopilotPanel, gated to `ready`).

## B5. Risks & mitigations
- **R-B1 Event write coupling / failure** in CRUD hot paths. *Mitigation:* emit
  best-effort (swallow + log, like the Copilot step sink) so provenance never blocks
  a file write.
- **R-B2 Reset semantics vs document events.** *Mitigation:* DP-B2 epoch (don't
  delete events); provenance filters by type + epoch.
- **R-B3 Migration for `provenance_epoch`.** *Mitigation:* `server_default=0`,
  backfills existing projects to epoch 0 (mirrors `0008`'s pattern).
- **R-B4 Volume** (thousands of edits). *Mitigation:* bound/paginate the read route;
  the dialog shows recent N with "load more".
- **R-B5 Actor fidelity** ("user" vs "copilot" vs "onboarding"). *Mitigation:* derive
  from `owner_id` + `source_type` already on the file/op; store `actor` in detail.
- **R-B6 Enrichment link retrofit** (apply path uses `source_type="copilot"`).
  *Mitigation:* when applying an enrichment-derived changeset item, set
  `source_type="enriched_markdown"` + `source_document_id`; if the changeset can't
  carry that provenance, thread it through the Copilot apply (small contract add).
- **R-B7 Design drift from Explain.** *Mitigation:* reuse the `ExplainDialog`
  styled shell/components directly; only swap the entry/detail renderer.

## B6. Phased checklist (Feature B)
- [ ] **B-1 Model + migration:** `provenance_epoch` on the project; Alembic
      (down_revision `0008_conversation_kind_project`); backfill 0. Test: up/down clean.
- [ ] **B-2 Schema:** `ProvenanceEntry` + event types/detail. Test: round-trip.
- [ ] **B-3 Emit on MDL CRUD:** create/update/delete/activate emit best-effort events
      with detail + epoch. Tests: each route appends one entry; failure to emit
      doesn't fail the write.
- [ ] **B-4 Onboarding/enrichment detail:** structured onboarding detail (tables,
      counts, mode) + enrichment apply sets `enriched_markdown`+`source_document_id`
      and emits. Tests: entries carry the metadata.
- [ ] **B-5 Reset epoch:** reset increments epoch + emits `reset`; provenance after
      reset shows only the new epoch. Test: pre-reset entries hidden; documents'
      events unaffected.
- [ ] **B-6 Read route + store:** `GET …/provenance` ordered, epoch-filtered, bounded.
      Tests: ordering, epoch filter, cap.
- [ ] **B-7 Dialog:** `MdlProvenanceDialog` reusing the Explain shell; header button
      gated to `ready`. Tests (RTL): renders entries in time order; detail per kind;
      empty state; opens/closes; copy-JSON.

---

# Cross-cutting decisions summary (for sign-off)

| # | Decision | Recommendation | Why |
|---|---|---|---|
| DP-A1 | Selectable unit | **Datasets** (label "Tables") | Matches what onboarding models; gets paginated/search free; tiny BE change. |
| DP-A2 | Listing transport | **Direct `/api/v1/dataset/`** (proxy fallback) | First-party paginated API; no new infra. |
| DP-A3 | "Select all" on huge schema | **Select-all-matching + excludes** | O(1) UI + payload. |
| DP-A4 | Onboard contract | **`OnboardingRequest` (mode/ids/excludes/search)**, empty ≡ today | Backward-compatible, expressive. |
| DP-A5 | Shift-range w/ pagination | **Loaded-window range; select-all for bulk** | Avoids fetching unloaded ranges. |
| DP-A6 | Picker UI location | **Modal from Onboard CTA** | Keeps File 1 rail intact; heavy list off the always-mounted panel. |
| DP-B1 | Provenance source | **Typed event log** (extend events) | Files lack edit sequence. |
| DP-B2 | Reset semantics | **Provenance epoch** (not delete) | Resets view, preserves audit, spares document events. |
| DP-B3 | Read API | **JSON `…/provenance`** (bounded) | Simple, boundable modal data. |
| DP-B4 | Entry shape/UI | **New `ProvenanceEntry`, reuse Explain shell** | Linear time ≠ SQL-graph step; consistent design. |

# Dependencies & sequencing
- Feature A and Feature B are independent and parallelizable; A-6 feeds richer
  onboarding metadata into B-4 (do A first if sequencing).
- Both touch the onboarding/reset routes — land A's route change and B's reset-epoch
  change with care to avoid conflicts (same `app.py` regions).
- No prompt-network changes. No change to the Copilot persistence work (landed §AB).

# Open questions — RESOLVED (see `plan_onboarding_selection_and_provenance_impl.md`)
1. **DP-A1** → **Registered datasets only (v1).** A Superset "dataset" is a
   table-level registered object (not schema-level); raw/unregistered tables are a
   costed follow-up.
2. **DP-A2** → **Direct `/api/v1/dataset/` via `SupersetClient`** (no proxy):
   precedent `DatasetSelect.tsx:90-92` in this panel.
3. **DP-B2** → **Delete-on-reset** of provenance-typed events only (document events
   survive). No epoch, no migration.
4. **DP-B3** → **Render-all-in-scroll like `ExplainDialog`**; server caps at 500
   newest-first (history is bounded per cycle by delete-on-reset).
5. **Provenance button** → icon button in `EditorHeader` beside the project (MDL
   schema) name (`index.tsx:642-658`), tooltip "Provenance"; not gated (empty state
   before history).

The full, sequenced, source-backed build lives in
`plan_onboarding_selection_and_provenance_impl.md`.
