# Plan — Onboarding picker: close the tables-vs-datasets expectation gap

**Status:** IMPLEMENTED (2026-06-26) — all three layers landed; tests green.
**Scope chosen by user:** All three layers.
**Owner agent context:** frontend-heavy; zero backend MDL contract change.

## As-built (2026-06-26)

All phases done. `npx jest src/SqlLab/components/AiAgentPanel` → **24 suites / 189 passed**
(was 173). `tsc --noEmit` clean; prettier applied. `oxlint` could not run locally (its
darwin native binding is not installed in this environment — CI will still lint).

- **api.ts:** `listPhysicalTables(databaseId, schema, catalog?)` and `createDataset({...})`
  added via `SupersetClient` (CSRF/session-safe). +4 api.test.ts tests.
- **CopilotPanel.tsx:** 3 onboarding copy blocks reworded "registered datasets". Tests assert
  on test-ids, unaffected.
- **OnboardingTablePicker.tsx:**
  - Gap banner (`picker-gap-banner`) when `physicalCount > totalCount`, with a `Register more →`
    deep link to `/dataset/add/` (new tab). Empty state now actionable (`picker-register-link-empty`).
  - `physicalCount` is derived from `physicalNames.length` (physical tables include registered
    ones; "unregistered" = set difference).
  - Window-`focus` refetch so returning from Add Dataset refreshes the list + count.
  - **Two transient-state gates** added to avoid flashes: the banner and the unregistered list
    only render once `datasetsLoaded` is true (before the first dataset page resolves,
    `registeredNames` is empty and everything would look unregistered / "0 of N").
  - Inline registration: unregistered physical tables render in a "Not registered (N)" section
    with checkboxes (`picker-unregistered-checkbox`), keyed on `table_name`. Confirm registers
    each via `createDataset` (sequential, dup-guarded against later-loaded pages), then onboards
    `[...registeredIds, ...createdIds]` (or `mode:'all'` which picks up the new datasets
    server-side). `canWrite=false` → rows disabled. Partial failure → STAY OPEN, surface
    `picker-register-error`, fold successes into the registered selection + refetch, keep failed
    rows checked for retry (deviation from plan's "proceed with successes" — chosen so the user
    sees what failed instead of the picker closing on them). +4 picker tests.
- **index.tsx:** passes `canWrite` to the picker. +1 end-to-end index test (register physical-only
  table → onboard by new id).

---

## 1. Problem (verified against source)

MDL onboarding consumes **Superset registered datasets (`SqlaTable`)**, never raw physical
tables. Verified:

- `get_full_schema` only calls `list_datasets` / `get_agent_context`
  (`superset_ai_agent/context/superset_metadata.py:79-110`).
- `list_datasets` is a literal `db.session.query(SqlaTable)` filter
  (`superset_ai_agent/integrations/superset/client.py:268-288`).
- Generated MDL embeds `superset_dataset_id` + dataset columns/metrics
  (`integrations/wren/mdl_exporter.py`).

But the **UI tells a different story**, creating a surprise:

- `CopilotPanel.tsx:688-693` (and the `failed`/retry copy) says onboarding *"reads this
  schema's permission-filtered **tables**"* → implies all physical tables.
- `OnboardingTablePicker.tsx:114-116` lists only registered datasets via `/api/v1/dataset/`.
- When nothing is registered, the empty state (`OnboardingTablePicker.tsx:317-325`) is a
  **dead end**: "Register tables as Superset datasets to onboard them" with no action.

The SQL Lab left tree, by contrast, shows **physical** tables via
`/api/v1/database/{id}/tables/` (`superset-frontend/src/hooks/apiResources/tables.ts:103-124`).
So a user sees N tables in SQL Lab, then fewer (or zero) in the picker, unexplained.

The backend is correct. **This is purely a UI honesty + smooth-flow fix.**

---

## 2. Reference facts (for the implementing agent)

- **Dataset create:** `POST /api/v1/dataset/` with body
  `{ database: <db id>, catalog, schema, table_name }`
  (precedent: `src/features/datasets/AddDataset/Footer/index.tsx:95-117`).
- **Physical tables list:** `GET /api/v1/database/{id}/tables/?q=<rison>` where rison carries
  `force`, `schema_name`, optional `catalog_name`; response `{ count, result: [{ value, type }] }`
  (precedent: `src/hooks/apiResources/tables.ts:103-124`; backend
  `superset/commands/database/tables.py`). **Un-paginated** — one call returns the whole schema.
- **Add Dataset route (deep link):** `/dataset/add/` (`src/views/routes.tsx:305`).
- **Registered datasets list (already used by picker):** `GET /api/v1/dataset/?q=<rison>` with
  `filters:[{database rel_o_m},{schema eq},{table_name ct}]`, `page`, `page_size`, paginated.
- **Write gate:** the panel already receives `canWrite` (project-level). Dataset creation needs
  `can_write` on `Dataset`; we do not have that flag in-panel, so gate the inline-register
  affordance on `canWrite` as a proxy and surface a 403 from the POST as a friendly error +
  deep-link fallback (do NOT pre-disable on a guess).

---

## 3. Design

### Layer 1 — Honest, consistent language
Stop conflating "tables" with "datasets"; explain the concept once.

### Layer 2 — Gap visible + escapable
Picker fetches the schema's **physical table count** (one `/tables/` call) and compares with the
registered-dataset `count`. When physical > registered, show an info banner with the registered/
total split and a **Register more →** deep link (`/dataset/add/`, new tab). Re-fetch the dataset
list on window `focus` so returning from registration refreshes the picker. Empty state gets the
same actionable link.

### Layer 3 — Inline self-service registration
Merge the schema's physical tables into the picker list. Each row is tagged **(dataset)** or
**⚠ not registered**. Selecting unregistered rows is allowed; on confirm we **first** create the
missing datasets (`POST /api/v1/dataset/` per table, sequential, with progress + partial-failure
handling), map the returned ids back, then run onboarding over the full id set. No MDL backend
change — onboarding still receives `dataset_ids`.

Selection model change: the picker today keys selection on **dataset id** (a number). Physical-only
rows have **no dataset id yet**. Re-key the in-memory selection on **`table_name`** (stable, unique
within a schema), and resolve to dataset ids at confirm time (existing rows → their id; new rows →
id from the create response). `mode:'all'` + exclusions stays keyed on the registered set only;
"select all matching" never silently pulls in unregistered tables (they require an explicit,
visible register action — avoids surprise bulk dataset creation).

---

## 4. Sequential implementation checklist

### Phase 1 — API helpers (`AiAgentPanel/api.ts`)
- [ ] `listPhysicalTables(databaseId, schema, catalog?)` → `GET /api/v1/database/{id}/tables/?q=…`;
      return `{ count, names: string[] }` (filter `result` to `type==='table'|'view'|
      'materialized_view'`, map `value`). Uses `SupersetClient.get` (CSRF-safe).
- [ ] `createDataset({databaseId, schema, catalog, tableName})` → `POST /api/v1/dataset/`;
      return the new dataset id (`response.json.id`). Throw `AgentApiError`-style on failure so the
      panel can show status + 403 fallback.
- [ ] Unit tests in `api.test.ts` for both (success + error + rison shape).

### Phase 2 — Layer 1 copy (`CopilotPanel.tsx`)
- [ ] Rewrite the three bootstrap copy blocks (default `688-693`, `failed` `666-674`, and the
      `Onboard this schema` / `Retry onboarding` helper) to say "tables you've registered as
      datasets". Keep it one short sentence each.
- [ ] Update `CopilotPanel.test.tsx` text assertions.

### Phase 3 — Layer 2 banner + escape (`OnboardingTablePicker.tsx`)
- [ ] On open, fire `listPhysicalTables` once (store `physicalCount`, `physicalNames`).
- [ ] Subtitle helper line: "Only tables registered as Superset datasets can be onboarded."
- [ ] Info `Alert` (antd, no custom CSS) shown when `physicalCount > totalCount`:
      "{registered} of {physical} tables in `{schema}` are registered as datasets…" with a
      `Register more →` link to `/dataset/add/` (`target="_blank"`, `rel="noopener"`).
- [ ] Replace the dead-end empty state with the same link.
- [ ] `window` `focus` listener (added while `open`) re-runs `fetchPage(0, search, true)` and the
      physical count, so returning from the Add Dataset tab refreshes counts. Clean up on close.
- [ ] Tests: banner appears with correct split; hidden when physical==registered; empty state link
      present; focus refetch fires.

### Phase 4 — Layer 3 inline registration (`OnboardingTablePicker.tsx`)
- [ ] Re-key selection (`included`/`excluded`/range) from dataset id → `table_name`. `isSelected`,
      `setSelected`, `toggleAt`, `selectedCount` updated. Keep a `Map<table_name, datasetId|null>`
      derived from the merged list.
- [ ] Merge list: registered datasets (from `/dataset/`) ∪ physical-only names (in
      `physicalNames` but not in the loaded dataset set), each row tagged `(dataset)` or
      `⚠ not registered`. Note pagination caveat: dataset list is paged, physical list is whole —
      mark "not registered" only for names not present in **any** loaded page; re-evaluate as pages
      load. (Acceptable: an as-yet-unloaded registered table may briefly show unregistered; the
      confirm-time create is idempotent-guarded — see next item.)
- [ ] Confirm flow: split selection into `existingIds` and `newTableNames`. For each new name,
      `createDataset(...)`; **guard** each call by first checking it didn't appear as a dataset on a
      later page (avoid duplicate-create 422 — Superset rejects dup table_name). Collect created
      ids. Show inline progress ("Registering 3 of 7…") and a per-row error tag on failure;
      continue with the successes (partial success allowed), then `onConfirm({mode:'include',
      datasetIds:[...existingIds, ...createdIds]})`.
- [ ] Gate the register path on `canWrite`; on POST 403, surface "You don't have permission to
      register datasets — ask an admin or use Add Dataset" with the deep link.
- [ ] `OnboardingTablePicker.test.tsx`: register-and-onboard happy path (2 existing + 2 new →
      2 POSTs → onConfirm with 4 ids); partial failure (1 POST 500 → onConfirm with 3 ids + error);
      403 → friendly message + no onConfirm; dup-guard (name appears on later page → no POST).

### Phase 5 — Wire-through + integration (`SemanticLayerEditor/index.tsx`)
- [ ] Pass `canWrite` to `OnboardingTablePicker` (currently it only gets db/catalog/schema).
- [ ] `index.test.tsx`: extend the existing "onboard through picker" test to cover a physical-only
      table being registered then onboarded end-to-end (mock `/tables/`, `/dataset/` GET+POST).

### Phase 6 — Verify
- [ ] `npm run test -- AiAgentPanel` green; `tsc --noEmit` clean; `pre-commit run` (prettier/eslint).
- [ ] Manual: schema with 0 registered → banner + register link works; mixed → inline register;
      huge schema → list still loads on scroll, single `/tables/` call (O(1) preserved for the
      banner; the physical-name set is the one unavoidable whole-schema read, matching SQL Lab).

---

## 5. Risks / caveats to report after build

- **R1 (pagination vs whole-schema diff):** dataset list is paged but physical list is whole, so the
  "not registered" tag is eventually-consistent as pages load. Mitigated by the confirm-time dup
  guard; worst case is a redundant create attempt that 422s and is skipped.
- **R2 (no dataset-write flag in panel):** we proxy on `canWrite` and handle 403 at POST time rather
  than pre-disabling. A user with project write but not dataset write sees a friendly fallback, not a
  broken button.
- **R3 (default dataset config) — CORRECTED:** `POST /api/v1/dataset/` with only
  `{database,schema,table_name}` creates a dataset with default column/metric introspection — same
  as the Add Dataset flow. The earlier claim "no owners are set" is **wrong**: the backend assigns
  the **current user as owner** automatically (`CreateDatasetCommand` →
  `populate_owner_list(default_to_user=True)` → `[g.user]`, `superset/commands/utils.py:59`). The
  picker now states this inline. See `plan_onboarding_picker_hardening.md` R3.
- **R4 (`/tables/` cost on giant schemas):** one un-paginated call (cached server-side via
  `table_cache`). This is the same call SQL Lab's tree makes, so parity, not regression. If it proves
  heavy we can gate the banner behind a "Show unregistered" toggle later.
- **R5 (catalog dbs):** pass `catalog` through to both `/tables/` and create; covered by the existing
  `catalogName` prop.
