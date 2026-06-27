# Plan — Onboarding picker hardening (R1, R2, virtualization, R3)

**Status:** IMPLEMENTED (2026-06-26). All four items landed in sequence; tests green.
**Scope:** Frontend-only. No backend/MDL contract change. Builds on
`plan_onboarding_dataset_registration_ux.md` (already implemented).

## As-built (2026-06-26)

`npx jest src/SqlLab/components/AiAgentPanel` → **24 suites / 200 passed** (was 190).
`tsc --noEmit` clean; prettier applied. `oxlint` not run locally (native binding absent; CI lints).

- **R3 — done.** `api.ts` unchanged (owner already set by backend). Picker shows a
  `picker-register-hint` line ("Registered with default columns and you as owner; refine later in
  the dataset editor."). Prior plan doc R3 note corrected. +1 picker test.
- **R2 — done.** `getDatasetWritePermission()` (`/api/v1/dataset/_info?q=(keys:!(permissions))` →
  `can_write`). Picker fetches once per open into `canRegister` (null = unknown); permissive on
  failure. `allowRegister = canWrite && canRegister !== false` gates the unregistered section. +2
  api tests, +2 picker tests (dataset-write absent → disabled; `_info` reject → still enabled).
- **R1 — done.** `listAllRegisteredTableNames()` (columns-projected `id,table_name`, paged at 1000,
  `REGISTERED_NAME_SCAN_CAP = 5000`, returns `{names, truncated}`). Picker loads it on open/focus
  into `registeredNamesAll`; `unregistered` classifies against it (∪ loaded rows as safety net),
  gated on `registeredNamesLoaded`; banner uses the authoritative count; `picker-scan-truncated`
  warning when capped. Removed the now-dead `datasetsLoaded` flag. +3 api tests, +1 picker test
  (authoritative beats unloaded display page).
- **V — done.** `react-window` `VariableSizeList` (fixed `LIST_HEIGHT=320`, `ROW_HEIGHT=36`,
  taller header). Flattened `listItems` (reg → header → unreg → loading); paging moved from
  `onScroll` to `onItemsRendered` (`PREFETCH_ROWS=10`). **Critical detail:** the row component
  (`PickerRow`) is defined at **module scope** and reads dynamic values from `itemData` — a
  closure-capturing row recreated per render makes react-window treat it as a new component type
  and remount all rows (broke selection + tests). `itemKey` keeps DOM nodes stable. Chose explicit
  `height` over `AutoSizer` for jsdom-friendliness (plan V.5 option a). +1 picker test asserting a
  2000-row unregistered set mounts <60 DOM nodes.
**Primary file:** `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx`
**API file:** `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts`
**Tests:** `OnboardingTablePicker.test.tsx`, `api.test.ts`

---

## Source-backed facts (verified)

- **Dataset list column subsetting:** `GET /api/v1/dataset/?q=(columns:!(id,table_name),...)` —
  FAB honors `API_SELECT_COLUMNS_RIS_KEY = "columns"` (`superset/datasets/api.py:29`,
  `:1272-1278`). `list_columns` includes `table_name`/`id` (`:116-143`).
- **Max page size 1000:** `SQLALCHEMY_DAO_MAX_PAGE_SIZE = 1000` (`superset/config.py:188`).
- **`_info` permissions:** `GET /api/v1/dataset/_info` returns a `permissions` array
  (`can_read`/`can_write`/…). FAB `info_headless` wrapped at `superset/views/base_api.py:485-497`.
  Frontend precedent: `src/pages/DatasetList/DatasetList.permissions.test.tsx` +
  `DatasetList.testHelpers.tsx:319,335-341` (`setupApiPermissions([...])`), and DatasetList shows
  the "Create Dataset" button only when `can_write` is present.
- **Auto-owner on create:** `CreateDatasetCommand` → `populate_owners(None)` →
  `populate_owner_list(None, default_to_user=True)` returns `[g.user]`
  (`superset/commands/dataset/create.py:114-115`, `superset/commands/base.py:46-58`,
  `superset/commands/utils.py:44-71`). **The creating user becomes owner automatically.**
- **Virtualization stack present:** `react-window@^1.8.10` + `react-virtualized-auto-sizer`
  (`superset-frontend/package.json:225-226`). Fixed-row precedent:
  `src/components/Chart/DrillBy/DrillBySubmenu.tsx:47` (`FixedSizeList`, `height=200`,
  `itemSize=35`, `overscanCount=20`); `src/dashboard/components/SliceAdder.tsx:440-453`
  (`AutoSizer` + `List` + `itemKey`).

---

## Issue R3 — Default dataset config on inline register  *(do first; trivial, independent)*

**Reality:** owners ARE set (current user). Columns/metrics come from default introspection —
identical to the Add Dataset flow, which is acceptable.

**Requirements**
- Correct the earlier risk note: registered datasets get the current user as owner + default
  column/metric introspection (same as Add Dataset).
- Set the user's expectation in-UI that an inline-registered dataset is "basic" and refinable.

**Touchpoints**
- `OnboardingTablePicker.tsx`: under the "Not registered (N)" section header, add a one-line
  secondary helper: *"Registered with default columns; refine later in the dataset editor."*
  (Only render when `unregistered.length > 0 && canRegister`.)
- `plan_onboarding_dataset_registration_ux.md` "As-built": fix the R3 line (owner IS set).

**Checklist**
- [ ] R3.1 Add the helper line to the unregistered section header.
- [ ] R3.2 Correct the R3 note in the prior plan doc.
- [ ] R3.3 Test: helper text present when unregistered rows shown (extend an existing picker test;
      no new network).

**Blockers/deps:** none.

---

## Issue R2 — Real dataset-write permission (replace the `canWrite` proxy)  *(independent)*

**Requirements**
- Inline registration affordances gate on the user's actual **Dataset `can_write`**, not the
  project write flag.
- Must degrade safely: if `_info` fails, fall back to the current behavior (allow attempt; 403
  surfaces per-row) — never hard-block onboarding of already-registered datasets.
- No extra request when there are no unregistered tables to register (lazy/once-per-open is fine).

**Entrypoints / touchpoints**
- `api.ts`: new `getDatasetWritePermission(): Promise<boolean>` —
  `SupersetClient.get({ endpoint: '/api/v1/dataset/_info?q=(keys:!(permissions))' })`,
  return `(json.permissions ?? []).includes('can_write')`. Mirrors the existing
  `listPhysicalTables`/`createDataset` SupersetClient helpers.
- `OnboardingTablePicker.tsx`:
  - New state `canRegister: boolean | null` (null = unknown/not yet fetched).
  - Fetch once on open via `getDatasetWritePermission()`; on error set `true` (permissive
    fallback — the POST still enforces server-side).
  - Replace every `canWrite`-as-register-gate with `canRegister !== false`. Keep the `canWrite`
    prop ONLY for project-level concerns (it no longer gates registration). The unregistered
    checkboxes/rows disable when `canRegister === false`; header hint switches to
    *"— ask an admin to register these"*.
  - Keep `databaseId/schema/catalog` props; no new props required.

**Checklist**
- [ ] R2.1 Add `getDatasetWritePermission` to `api.ts`.
- [ ] R2.2 `api.test.ts`: returns true when `permissions` includes `can_write`; false otherwise;
      rejects → caller treats as permissive (test the helper returns the boolean; fallback logic is
      tested in the picker).
- [ ] R2.3 Wire `canRegister` state + once-per-open fetch in the picker; swap the register gate.
- [ ] R2.4 `OnboardingTablePicker.test.tsx`: (a) `can_write` present → unregistered rows enabled;
      (b) `can_write` absent → rows disabled + admin hint; (c) `_info` rejects → rows enabled
      (permissive). Mock `SupersetClient.get` URL-aware for `/_info` (extend `mockSupersetGet`).
- [ ] R2.5 `index.tsx`: no change needed (picker self-fetches), but confirm the existing
      `canWrite={canWrite}` prop still passes (project write still governs the Onboard CTA).

**Blockers/deps:** none. Note: the `unregistered tables are read-only without write permission`
existing test currently sets `canWrite={false}`; it must be updated to drive `canRegister` via the
`/_info` mock instead (the prop no longer gates registration).

---

## Issue R1 — Authoritative "not registered" classification  *(do before virtualization)*

**Problem:** `unregistered` is currently `physicalNames − registeredNames`, where `registeredNames`
is only the **loaded** dataset pages. A registered dataset on an unloaded page transiently shows as
unregistered.

**Fix:** classify against the **complete** registered-name set for the schema, fetched
independently of the paginated display list (cheap: `table_name`+`id` only, 1000/page).

**Requirements**
- `unregistered = physicalNames − registeredNamesAll`, where `registeredNamesAll` is every
  registered dataset name in the schema (not just loaded pages).
- Bounded: cap total names fetched (default cap, e.g. `REGISTERED_NAME_SCAN_CAP = 5000`). If the cap
  is hit, stop paging and `log`/note potential under-classification (a registered table beyond the
  cap could still show as unregistered → confirm-time dup-guard still prevents a bad create).
- Keep the existing paginated **display** list + load-on-scroll unchanged (this is purely the
  classification source).
- Keep the confirm-time dup-guard as defense-in-depth.
- Refresh `registeredNamesAll` on the same triggers as the list (open, window focus).

**Entrypoints / touchpoints**
- `api.ts`: new `listAllRegisteredTableNames(databaseId, schema, catalog?, cap?)` —
  loops `GET /api/v1/dataset/?q=(columns:!(id,table_name),filters:!(...),page:N,page_size:1000,
  order_column:table_name,order_direction:asc)` accumulating `table_name`s until
  `accumulated >= count` or `accumulated >= cap`. Reuse the same filter triplet the picker builds
  (`database rel_o_m`, `schema eq`). Returns `string[]`.
- `OnboardingTablePicker.tsx`:
  - New state `registeredNamesAll: Set<string>` (+ `registeredScanTruncated: boolean`).
  - Load it on open + focus (alongside `loadPhysical`); reset on open.
  - `unregistered` memo: use `registeredNamesAll` instead of `registeredNames` (loaded rows). Keep
    the `datasetsLoaded`-style gate: only classify once the authoritative set has resolved (new
    `registeredNamesLoaded` flag) to avoid the "everything looks unregistered" flash.
  - The gap banner's `registered` number should use `registeredNamesAll.size` (authoritative)
    rather than `totalCount` (the display `count`) — they should match, but the complete-set is
    the honest source. (Keep `totalCount` for the display list footer.)

**Checklist**
- [ ] R1.1 Add `listAllRegisteredTableNames` (with cap) to `api.ts`.
- [ ] R1.2 `api.test.ts`: single-page (count ≤ 1000) returns all names; multi-page accumulation
      (count > page_size) pages until complete; cap halts paging and the partial set is returned.
- [ ] R1.3 Picker: add `registeredNamesAll` + `registeredNamesLoaded` + `registeredScanTruncated`;
      load on open/focus; reset on open.
- [ ] R1.4 Repoint `unregistered` + the banner's registered count at the authoritative set; gate on
      `registeredNamesLoaded`.
- [ ] R1.5 `OnboardingTablePicker.test.tsx`: a dataset present in the authoritative set but NOT in
      the loaded display page is classified **registered** (no longer appears under
      "Not registered"). Update `mockSupersetGet` so the `columns`-projected list call returns the
      full registered set.
- [ ] R1.6 If `registeredScanTruncated`, surface a subtle note in the unregistered header
      (*"showing first N; some tables may be misclassified"*) and `log`.

**Blockers/deps:** none, but **must land before virtualization** so the windowed list consumes the
corrected `unregistered`. Interacts with R2's `mockSupersetGet` URL-awareness — coordinate the test
mock (one shared URL-aware `SupersetClient.get` mock now handles: dataset list, `columns`-projected
list, `/tables/`, and `/_info`).

---

## Issue V — DOM virtualization of the picker list  *(do last; largest)*

**Problem:** the unregistered section renders every matching physical name; a 10k-table schema with
no search mounts thousands of DOM rows. Registered rows already paginate on scroll, but they too
grow unbounded in the DOM as pages load.

**Fix:** render the combined list with `react-window` `FixedSizeList` (fixed-height rows) +
`react-virtualized-auto-sizer`, and move page-loading from the `<div onScroll>` handler to
react-window's `onItemsRendered`.

**Requirements**
- Single windowed list renders BOTH registered rows and the unregistered section (header + rows).
- Fixed row height (≈40px) — checkbox + name + optional tag fit one line; the section header and
  loading spinner are list items of the same height (or a known height).
- Preserve all existing behaviors: shift-range select over loaded registered rows, context-menu
  select-all/deselect, search, gap banner, empty state, register-on-confirm.
- Replace `onScroll` next-page trigger with `onItemsRendered({ visibleStopIndex })`: when
  `visibleStopIndex >= registeredRowCount - PREFETCH` and `hasMore && !loading`, call
  `fetchPage(page+1,…)`.
- Keep the modal layout stable: the list occupies a fixed-height region (existing 320px) wrapped in
  `AutoSizer` (or pass explicit height to avoid AutoSizer's zero-height-in-jsdom issue — see test
  note).

**Entrypoints / touchpoints**
- `OnboardingTablePicker.tsx`:
  - Build a flat `listItems` array (memo): `[{kind:'registered', row}…, {kind:'header'}?,
    {kind:'unregistered', name}…, {kind:'loading'}?]`.
  - Replace the `<div onScroll>{rows.map}{unregistered.map}</div>` block with
    `FixedSizeList` (`itemCount=listItems.length`, `itemSize=ROW_HEIGHT`, `overscanCount=8`,
    `itemKey` by stable key) and a `Row({index, style})` renderer that switches on `kind` and
    applies `style` (react-window requirement).
  - Keep the `Dropdown` context-menu wrapper around the list container.
  - shift-range: `toggleAt(index)` already indexes into `rows` (registered) — ensure the windowed
    registered index maps to the same `rows` index (registered items occupy the list head, so
    `listIndex === rowIndex` for registered rows; guard the header/unregistered offset).
- `package.json`: no change (deps already present).

**Checklist**
- [ ] V.1 Extract `ROW_HEIGHT` const + flat `listItems` memo.
- [ ] V.2 Swap the scroll `<div>` for `FixedSizeList` + `AutoSizer` (or fixed height); implement
      `Row` renderer with `style` applied; `itemKey` stable (`reg:<id>` / `new:<name>` / `header` /
      `loading`).
- [ ] V.3 Move page-loading to `onItemsRendered`; delete `onScroll`/`SCROLL_THRESHOLD`.
- [ ] V.4 Re-verify shift-range, context menu, search, banner, empty state still work.
- [ ] V.5 `OnboardingTablePicker.test.tsx`: all existing 11 tests pass unchanged. **jsdom caveat:**
      react-window needs a non-zero height; `AutoSizer` reports 0×0 in jsdom. Mitigation options
      (pick one, document in the test): (a) pass an explicit `height`/`width` to `FixedSizeList`
      instead of `AutoSizer` (simplest, test-friendly), or (b) mock `AutoSizer` to render children
      with fixed size (precedent: search `superset-frontend` for existing `react-virtualized-auto-sizer`
      jest mocks). Prefer (a): give the list a fixed height (the modal already fixes 320px) and only
      use `AutoSizer` for width, or hardcode both.
- [ ] V.6 Add a test asserting a large unregistered set (e.g. 2000 names) mounts only a bounded
      number of `picker-unregistered-row` nodes (virtualization proof), e.g.
      `expect(screen.getAllByTestId('picker-unregistered-row').length).toBeLessThan(60)`.

**Blockers/deps:** depends on **R1** (consumes corrected `unregistered`) and benefits from **R2**
(disabled rows). Do after both. The `onItemsRendered` refactor is the riskiest change — land it with
the full existing test suite as the regression gate.

---

## Global sequencing

1. **R3** (trivial, independent) →
2. **R2** (perm fetch; sets up `_info` mock) →
3. **R1** (authoritative classification; extends the shared URL-aware mock) →
4. **V** (virtualization; consumes R1 + R2).

After each: add/extend tests, run
`npx jest src/SqlLab/components/AiAgentPanel`, `tsc --noEmit`, prettier. Final: full suite green +
risk re-report. **Shared test scaffolding:** consolidate `SupersetClient.get` mocking into one
URL-aware switch handling `dataset list`, `columns`-projected list, `/tables/`, and `/_info`; add
`SupersetClient.post` for `createDataset` — do this in R2.1/R1.5 to avoid churn in V.

## Out of scope / explicitly deferred
- Server-side search of physical tables (the `/tables/` endpoint is un-paginated and un-filtered;
  search stays client-side over the fetched names — unchanged).
- Auto-refining inline-registered datasets (metrics/calculated columns) — admins use the dataset
  editor, as today.
