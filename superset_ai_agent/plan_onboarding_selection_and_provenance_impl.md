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

# Implementation plan — selective-table onboarding & MDL provenance dialog

**Status:** ready to build. Source-audited against the working tree on 2026-06-26.
**Spec:** `plan_onboarding_selection_and_provenance_spec.md` (decision record).
**Builds on:** File 1 onboarding gate (landed) + Copilot persistence (landed, `wren_mdl_copilot.md` §AB).

Both a technical spec and a sequential checklist. Work top-to-bottom; each phase
leaves the tree green (`pre-commit run --all-files`; backend
`pytest tests/unit_tests/superset_ai_agent/`; FE `npm run test -- <file>`). File:line
anchors are accurate as of the audit date — re-confirm before editing.

Two independent features over the MDL lifecycle:
- **A — Selective onboarding:** pick which (registered) tables to onboard from a
  huge schema, O(1) UI, pass only the selection to onboarding.
- **B — MDL provenance dialog:** time-ordered history of MDL operations, reusing the
  AI Explain dialog's design, deleted on MDL reset.

## 0. Locked decisions (from spec sign-off)

| # | Decision | Resolution | Evidence |
|---|---|---|---|
| A1 | Onboarding scope | **Registered Superset datasets only** (v1). Label UI "Tables". Raw/unregistered tables = costed follow-up. | onboarding models `DatasetMetadata` (needs columns/metrics); dataset API paginates, `/tables/` does not. |
| A2 | Table-list transport | **Call core `/api/v1/dataset/` directly via `SupersetClient`** (no proxy). | `DatasetSelect.tsx:90-92` already does exactly this in this panel. |
| A3 | "Select all" on huge schema | **Select-all-matching + `excluded` set** (Gmail-style); never enumerate all ids client-side. | O(1) requirement. |
| A4 | Onboard contract | **`OnboardingRequest` body**; empty body ≡ today's whole-schema onboard. | backward-compat. |
| A5 | Shift-range w/ pagination | **Range-select over loaded rows only**; bulk via select-all. | `FoldersEditor` range-select assumes materialized rows. |
| A6 | Picker UI | **Modal opened from the Onboard/Retry CTA** in `CopilotPanel`. | keeps File 1 rail intact. |
| B1 | Provenance source | **Extend the existing event log** (`ai_agent_events`). | files keep only latest state, not an edit sequence. |
| B2 | Reset semantics | **Delete-on-reset** of provenance-typed events for the project (document events untouched). **No epoch, no migration.** | user choice; `payload` JSON is schemaless. |
| B3 | History cap | **Render-all-in-scroll like `ExplainDialog`**; server caps at recent **500** newest-first. | `ExplainDialog` renders all steps; delete-on-reset bounds history per cycle. |
| B4 | Entry shape / UI | **New `ProvenanceEntry`** (don't overload `AgentStep`); **reuse the `ExplainDialog` shell**. | provenance is linear time, not SQL attempts. |
| B5 | Button placement | **Icon button in `EditorHeader` beside the project (MDL schema) name**, tooltip "Provenance"; not gated (empty state before history). | `index.tsx:642-658` renders the name there. |

---

# Feature A — Selective-table onboarding

## A-reuse (frontend building blocks, exact)
- **Paginated dataset fetch:** `DatasetSelect.tsx:69-104` `fetchDatasets(search,page,pageSize)`
  — rison `filters` `{database rel_o_m id}`, `{schema eq name}`, `{table_name ct search}`,
  `order_column:'table_name'`, `page`, `page_size`; `SupersetClient.get('/api/v1/dataset/?q=…')`;
  returns `{data:[{value:id,label:table_name}], totalCount}`. **Copy this fetch verbatim.**
- **Selection model:** `FoldersEditor/index.tsx:101-104` (`useState<Set<id>>` +
  `lastSelectedItemIdRef`), `:424-470` (`handleSelect` shift-range over loaded array),
  select-all/deselect-all (`:360,615`); checkbox row `TreeItem.tsx:274-288` (`e.shiftKey`).
- **Virtualization:** react-window (`FoldersEditor/VirtualizedTreeList.tsx`); for a flat
  infinite list use `FixedSizeList` + `onItemsRendered` to trigger the next page fetch.
- **Context menu:** antd `Dropdown` `trigger={['contextMenu']}` wrapping the list (select
  all / deselect all). Simpler than `ChartContextMenu`; no new infra.
- **Modal + Select:** `@superset-ui/core/components` `Modal`, `Button`, `Checkbox`, `Input`.

## A1 — Backend: `OnboardingRequest` schema
- [ ] Add to `semantic_layer/schemas.py`:
  ```python
  OnboardingMode = Literal["all", "include"]
  class OnboardingRequest(BaseModel):
      mode: OnboardingMode = "all"
      dataset_ids: list[int] = Field(default_factory=list)        # mode="include"
      exclude_dataset_ids: list[int] = Field(default_factory=list) # mode="all"
      search: str | None = None                                   # mode="all" name filter
  ```
- **Acceptance:** empty `OnboardingRequest()` validates and means "all, no excludes" (≡ today).
- **Test:** schema defaults round-trip.

## A2 — Backend: thread the selection into onboarding
- [ ] **Route** `onboard_semantic_project` (`app.py:~2081-2106`): accept
  `request: OnboardingRequest | None = None` (default → `OnboardingRequest()`), resolve
  `dataset_ids`, pass to `_onboarding_context`.
- [ ] **Resolver** `_resolve_onboarding_dataset_ids(project, request, fastapi_request) -> list[int] | None`:
  - `mode="include"` → `request.dataset_ids` (empty include → 400 "select at least one table").
  - `mode="all"` + no `search`/`exclude` → `None` (preserve current full-schema path).
  - `mode="all"` + `search`/`exclude` → list dataset ids for (db, schema[, search]) via the
    Superset client and subtract `exclude_dataset_ids`. (Server-side; bounded by
    `wren_schema_table_scan_limit`; page if needed.)
- [ ] **`_onboarding_context`** (`app.py:1984-2010`): add `dataset_ids: list[int] | None = None`.
  When ids present, build `AgentQueryRequest(..., dataset_ids=dataset_ids)` and use the
  **id-honoring** fetch — `get_full_schema` ignores ids (`context/superset_metadata.py`),
  so call the provider's `get_context`/the client's `get_agent_context(dataset_ids=…)`
  (which filters `SqlaTable.id.in_(dataset_ids)`, `integrations/superset/client.py:284-288`).
  When `dataset_ids is None`, keep the current `get_full_schema` path unchanged.
- [ ] **Capture selection for provenance (feeds B-4):** thread the resolved `dataset_ids`
  (or names) + `mode` into `_start_onboarding_job` so the onboarding-completed event can
  record them.
- **Acceptance:** `include` with 2 of N datasets onboards exactly those 2 files; `all` (empty
  body) onboards the whole schema as before; empty `include` → 400.
- **Tests** (`test_semantic_layer_api.py`/onboarding test): subset onboards only selected;
  empty body unchanged; empty-include 400.

## A3 — Frontend: API client
- [ ] In `api.ts`, extend `runOnboarding(projectId, selection?)` to POST the
  `OnboardingRequest` body (default `{}` ≡ whole-schema). Add a typed `OnboardingSelection`.
- [ ] Add a dataset-list fetch helper modeled on `DatasetSelect.fetchDatasets` (or import/
  share it): `fetchSchemaDatasets({databaseId, catalogName, schema, search, page, pageSize})`
  → `{rows:[{id,tableName}], totalCount}` via `SupersetClient` rison.
- **Acceptance/Test:** payload shape correct; rison filters include database/schema/search;
  `runOnboarding` defaults to whole-schema when no selection.

## A4 — Frontend: `OnboardingTablePicker` modal (net-new component)
- [ ] New `SemanticLayerEditor/OnboardingTablePicker.tsx`. Props: `{open, databaseId,
  catalogName, schema, onCancel, onConfirm(selection)}`.
- [ ] **List:** `FixedSizeList` (react-window) of dataset rows; `onItemsRendered` fetches the
  next page (reuse A3 fetch); search `Input` (debounced) re-queries server-side (resets paging).
- [ ] **Selection (`Set<number>` + `lastSelectedRef`)**, reusing `FoldersEditor` semantics:
  - checkbox click → toggle that id;
  - **Shift+click** → range over **loaded** rows (between last anchor and current);
  - **Cmd/Ctrl+click** → toggle single without clearing;
  - **right-click** (antd `Dropdown contextMenu`) → "Select all (matching)" / "Deselect all";
  - **select-all-matching** sets `mode:'all'` + clears `excluded`; subsequent unchecks add to
    `excluded` (Gmail-style) — keeps O(1).
- [ ] **Selected count** header: "N selected" (include) or "All N matching − M excluded" (all).
- [ ] **Footer:** "Onboard N table(s)" (disabled when 0) → `onConfirm(selection)`; Cancel.
- [ ] Build `selection` → `OnboardingRequest`: include-set → `{mode:'include',dataset_ids:[…]}`;
  all-mode → `{mode:'all',search,exclude_dataset_ids:[…]}`.
- **Acceptance (RTL):** toggle; shift-range over loaded rows; cmd-toggle; select-all-matching +
  exclude; count text; load-on-scroll fetches page 2; "Onboard N" emits the right payload.

## A5 — Frontend: wire CTA → modal → onboarding
- [ ] `CopilotPanel.tsx` Onboard/Retry CTA (`onOnboard`) → open the picker (lift state to
  `SemanticLayerEditor/index.tsx` or pass an `onRequestOnboard` prop). On confirm, call
  `runOnboard(projectId, selection)` (`index.tsx:528-560`) which already polls + toasts +
  `refresh()`.
- [ ] Guard: a `mode:'all'` with no datasets in the schema → friendly empty state ("No
  registered tables found in this schema. Register tables as Superset datasets first.").
- **Acceptance (RTL, `SemanticLayerEditor/index.test.tsx`):** clicking Onboard opens the
  picker (no immediate onboard POST); confirming dispatches onboarding with the selection;
  cancel onboards nothing; File-1 readiness gating unchanged.

---

# Feature B — MDL provenance dialog

## B-reuse (exact)
- **Backend log:** `AiAgentEvent` (`persistence/models.py:~197-212`, `payload` JSON),
  `SemanticLayerEvent` (`semantic_layer/schemas.py:~106-116`), `_append_semantic_event`
  (`app.py:~3060-3081`), `append_event`/`list_project_events`
  (`semantic_layer/sqlalchemy_store.py:316-378`, `memory.py:206-235`, protocol `store.py:144-168`).
- **FE shell:** `ExplainDialog.tsx:1-274` — antd `Modal`, vertical timeline (`StepRow`/`Dot`/
  `StepBody` styled), summary header, copy-JSON (`steps.map`, render-all-in-scroll). Fork this.
- **Header host:** `EditorHeader` (`SemanticLayerEditor/index.tsx:642-658`).

## B1 — Backend: event types + generic detail (no migration)
- [ ] Widen `SemanticLayerEventType` (`semantic_layer/schemas.py:~42-53`) with:
  `"mdl_created"`, `"mdl_updated"`, `"mdl_activated"`, `"mdl_deleted"`, `"document_enriched"`.
- [ ] Add `detail: dict[str, Any] | None = None` to `SemanticLayerEvent`. Round-trips via
  `payload` (`append_event` dumps the whole event) — **no DB change**.
- **Acceptance/Test:** an event with `detail` round-trips through the sqlalchemy store.

## B2 — Backend: `ProvenanceEntry` + mapping
- [ ] Add `ProvenanceEntry` (`semantic_layer/schemas.py`):
  ```python
  ProvenanceKind = Literal["onboarding","enrichment","mdl_created","mdl_updated",
                           "mdl_activated","mdl_deleted"]
  class ProvenanceEntry(BaseModel):
      id: str
      kind: ProvenanceKind
      status: Literal["ok","warning","error"] = "ok"
      summary: str
      created_at: datetime
      actor: str | None = None          # owner / "copilot" / "onboarding"
      detail: dict[str, Any] = Field(default_factory=dict)  # path/file_id/source_type/
                                        # dataset_ids/table_count/document_id/status_from/to
  ```
- [ ] `provenance_from_event(event) -> ProvenanceEntry | None` mapper: maps provenance-typed
  events → entries (kind from `type`, `summary` from `message`, `detail` passthrough, `actor`
  from `detail.actor`/owner); returns `None` for non-provenance types (e.g. `document_uploaded`).
- **Constant:** `PROVENANCE_EVENT_TYPES: frozenset[str]` = the onboarding/enrichment/mdl_* set
  (NOT document upload/extract — those survive reset and belong to the document suite).
- **Test:** mapping covers each kind; non-provenance types map to `None`.

## B3 — Backend: emit on MDL file CRUD (best-effort)
- [ ] Helper `_append_mdl_event(*, project, owner_id, event_type, file, message, detail)` →
  `_append_semantic_event(...)`, wrapped so a failure logs and never breaks the write
  (mirror the Copilot step-sink swallow pattern).
- [ ] Emit at the routes:
  - `create_mdl_file` (`app.py:~993-1022`) → `mdl_created` (detail: path, source_type, file_id).
  - `update_mdl_file` (`app.py:~1181-1245`) → `mdl_activated` when `request.status=="active"`
    (status_from→to), else `mdl_updated` (path, file_id).
  - `delete_mdl_file` (`app.py:~1250-1274`) → `mdl_deleted` (path, file_id).
- **Acceptance:** each route appends exactly one provenance entry; a forced emit failure does
  not fail the file op.
- **Tests** (`test_semantic_layer_api.py`): create/update/activate/delete each add one entry;
  emit-failure path (monkeypatch) still returns 200.

## B4 — Backend: onboarding + enrichment provenance detail
- [ ] **Onboarding:** in `_start_onboarding_job` completion (`app.py:~2065-2076`), pass
  `detail={mode, dataset_ids|count, model_count, activated_count, warnings, paths}` from A2 +
  `OnboardingResult` (`schemas.py:244-251`) on the `onboarding_completed` event (kept as a
  provenance entry of kind `onboarding`).
- [ ] **Enrichment:** when an enrichment-derived changeset item is applied, set the file's
  `source_type="enriched_markdown"` + `source_document_id` and emit `document_enriched`
  (detail: path, document_id, filename). *Note (R-B6):* the apply path
  (`copilot/service.py apply_changeset_items`) currently writes `source_type="copilot"` with no
  document link; threading `source_document_id` requires the `ChangesetItem` to carry it
  (small contract add) — **scope this sub-item explicitly**; if deferred, enrichment shows as a
  generic `mdl_created`/`mdl_updated` until the link lands.
- **Test:** onboarding entry carries `dataset_ids`/counts; (if done) enrichment entry carries
  `document_id`.

## B5 — Backend: delete-on-reset
- [ ] Add `delete_project_events(project_id, *, owner_id, types: set[str] | None = None) -> int`
  to the store protocol (`store.py`) + `InMemorySemanticLayerStore` + `SqlAlchemy…` (DELETE
  where `project_id`+`owner_id` [+ `type IN types`]).
- [ ] In `reset_semantic_project` (`app.py:2111-2137`), after deleting MDL files, call
  `delete_project_events(project_id, owner_id=…, types=PROVENANCE_EVENT_TYPES)`. **Document
  events are NOT in the set → they survive** (reset keeps documents).
- **Acceptance:** after reset, provenance is empty; `document_uploaded` events still listed by
  the document suite.
- **Tests:** reset purges provenance entries but not document events; both stores.

## B6 — Backend: read route
- [ ] `GET /agent/semantic-layer/projects/{project_id}/provenance` →
  `response_model=list[ProvenanceEntry]`. Authz `permission="read"`. Implementation: load
  `list_project_events(project_id, owner_id)`, map via `provenance_from_event`, drop `None`,
  **sort newest-first**, **cap 500**. (Add `PROVENANCE_HISTORY_CAP=500` constant.)
- **Acceptance/Test:** ordering newest-first; only provenance kinds; ≤ cap; 404/authz parity
  with sibling project routes.

## B7 — Frontend: dialog + header button
- [ ] **Client:** `getMdlProvenance(projectId): Promise<ProvenanceEntry[]>` (`api.ts`) +
  `ProvenanceEntry` TS type.
- [ ] **Component:** `SemanticLayerEditor/MdlProvenanceDialog.tsx`, forking `ExplainDialog`'s
  Modal + timeline shell (Dot/StepRow/StepBody/copy-JSON). Render `ProvenanceEntry[]` in time
  order (newest-first or oldest-first — pick one, default newest-first), status-colored dots,
  a per-`kind` detail line (path / table count / document filename / status_from→to), relative
  timestamps, empty state ("No history yet — onboard a schema to begin."). Load on open.
- [ ] **Button:** in `EditorHeader` (`index.tsx:642-658`), beside the project-name
  `Typography.Title`, an icon `Button` (`Icons.HistoryOutlined`) wrapped in `Tooltip`
  "Provenance"; `onClick` opens the dialog. Always visible (empty state pre-history).
- **Acceptance (RTL):** renders entries in time order; per-kind detail; empty state; open/close;
  copy-JSON present; button shows tooltip and opens the dialog.

---

# Tests matrix (consolidated)
- **A backend:** OnboardingRequest defaults; include-subset onboards only those; empty body
  unchanged; empty-include 400; resolver excludes work.
- **A frontend:** picker toggle / shift-range(loaded) / cmd-toggle / select-all-matching+exclude
  / count / load-on-scroll page 2 / payload; CTA opens picker; confirm dispatches; cancel no-op.
- **B backend:** detail round-trip; CRUD emits one entry each + emit-failure safe; onboarding
  detail; reset purges provenance not documents (both stores); read route order/cap/authz.
- **B frontend:** dialog renders ordered entries + per-kind detail + empty state; header button
  tooltip + open.

# Sequencing & cross-feature notes
- A and B are independent; **A-2 feeds B-4's onboarding detail** — do A before B-4 if serial.
- Both touch onboarding/reset in `app.py` (A-2 onboard, B-5 reset) — land carefully to avoid
  conflicts in the same region.
- **No migration** for either feature (A adds a request body; B reuses the schemaless
  `ai_agent_events.payload` + delete-on-reset).
- **No prompt-network change.** No change to Copilot persistence (§AB).

# Risks & mitigations (carried from spec, still open)
- **R-A1** datasets ≠ physical tables → empty-state copy + DP-A1 v1 scope; raw-table path is a
  costed follow-up (needs core `/tables/` pagination + per-table introspection).
- **R-A3/A5** select-all / shift-range at scale → select-all-matching+excludes; range over
  loaded rows only; both O(1).
- **R-B1** event write in CRUD hot path → best-effort emit (swallow+log).
- **R-B2** reset must not nuke document events → delete by `PROVENANCE_EVENT_TYPES` only.
- **R-B4** provenance volume within a cycle → server cap 500 + newest-first (matches Explain
  render-all); reset bounds per cycle.
- **R-B6** enrichment→file link retrofit (apply uses `source_type="copilot"`, no doc id) →
  explicit B-4 sub-item; degrade to generic mdl_* until the `ChangesetItem.source_document_id`
  contract add lands.

# Open items to confirm during build
1. Provider `get_context`/`get_agent_context` reliably forwards `dataset_ids` for onboarding
   (A-2) — else call the client `get_agent_context(dataset_ids=…)` directly.
2. Whether `react-window-infinite-loader` is already a dependency (A-4) — else hand-roll
   `onItemsRendered` paging.
3. Timeline direction in the dialog (newest-first vs oldest-first) — default newest-first.
