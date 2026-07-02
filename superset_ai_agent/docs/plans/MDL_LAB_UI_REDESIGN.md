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

# MDL Lab — UI Reorganisation: Audit & Redesign Proposal

**Status:** IMPLEMENTED (UP1–UP5), tested, in the working tree (uncommitted) — pending the user's visual/manual QA.
Companion to `MDL_LAB.md` (as-built), `MDL_LAB_GAP_CLOSURE.md` (follow-ups), `plan_mdl_lab_spec.md` (rationale).

## As-built (UP1–UP5)
- **UP1** `CopilotPanel` header stacks vertically (title above wrapping action buttons) — no more one-char-per-line.
- **UP2** "New project" opens `NewProjectModal` (name + **schema multi-select**, first = primary); create proves
  access to every chosen schema. The browse-first "create needs a schema" guard is gone.
- **UP3** The per-schema tree action is removed (`TreeNodeRenderer` + `TableExploreTree`); the DB-level
  "Open MDL Lab" toolbar button + `projectId` deep-link are the only entries.
- **UP4** Structural inversion in `SemanticLayerEditor/index.tsx`: a top-level master–detail `EditorSplitter` —
  **`ProjectBrowser` is the left master**, the **selected project's workspace** is the right detail. The global
  `EditorHeader` is deleted; **Models/Instructions/Graph are sub-tabs of the workspace** (`ContentTabs` moved
  inside, no longer the parent of the browser). Relocated bits: schema set + add, provenance history, Copilot
  toggle, coverage badge, and the state/doc-count badge → a thin **workspace strip**; project name → removed
  (implied by the highlighted browser row). Schema-aware sub-views (instructions, graph, onboarding) derive scope
  from a new **`projectScope`** (the opened project), not the entry tab. Empty workspace state when no project.
  **Cross-schema onboarding**: `OnboardingTablePicker` gains a schema selector; registered-dataset selections
  (keyed by global id) **accumulate across schemas** for a single onboard pass.
- **UP5** Removed dead `openSemanticLayerEditor`/`buildSemanticLayerEditorId`; `SemanticLayerEditorTab.schemaName`
  is now vestigial (always unset). Tests repointed to `openMdlLab`.

**Tests:** all touched suites green (SemanticLayerEditor 23 suites, TableExploreTree, TabbedSqlEditors, api.ts —
232 passing); tsc + prettier clean. New tests: Copilot header, NewProjectModal (schema gating + multi), MDL Lab
toolbar-only entry, empty workspace, projectId/browse entry, cross-schema picker accumulation. The 2 pre-existing
`AiAgentPanel/index.test.tsx` + `ExplainDialog.test.tsx` SQL-render failures are unrelated and untouched.

## As-built — Onboarding tree + pipeline fixes (post-UP5 round)

This round answered four follow-ups: a nested onboarding tree, true cross-schema accumulation, a project
deselect/blank bug, and schema correctness for the agent/engine.

- **OT1 — Onboarding is now a collapsible schema→tables tree** (`OnboardingTablePicker.tsx`, full rewrite). The
  per-schema **dropdown selector is gone**; every project schema renders as a header with its tables indented
  underneath, **all expanded by default**, each collapsible (caret) like the SQL Lab DB browser. Still virtualized
  (`react-window`), so a 2000-row schema stays bounded. Per-schema header carries a tristate "select all in
  schema" checkbox + a `R of P registered` count; unregistered physical tables list per schema for inline
  registration (registered in **their own schema**, not the primary).
- **OT2 — Selections genuinely add up across schemas.** Selection is now an explicit `include` model: registered
  datasets keyed by **global id** (`included`), unregistered tables keyed by **schema+name** (`includedNew`). The
  footer count and "Onboard N table(s)" reflect the **cross-schema total**, and **Select all** spans every schema.
  The old Gmail-style `mode:'all'` (single-schema, exclude-set) is dropped from the picker — the backend's
  `include` path is id-driven and cross-schema native (`app.py:_onboarding_context`), so it onboards the chosen
  ids regardless of how many schemas they span. `loadSchema` renders registered rows first, then merges the
  advisory physical-tables scan, so a slow/failed physical lookup can never blank the rows.
- **OT3 — Onboarding no longer deselects/blanks the open project.** Root cause: in the Lab (project-keyed entry)
  the tab carries no schema, so `scope.schema_name` is empty; `runOnboard` and the completion poller called the
  scope-based `refresh()`, which at that guard did `setProject(null)` — wiping the workspace on onboard start and
  on completion. Fix: a new **`refreshOpenProject()`** reloads the currently-open project **by id** (identity
  stable, never nulls), and `runOnboard` + the poller use it; the scope `refresh` remains only for the legacy
  schema-tree entry. [`SemanticLayerEditor/index.tsx`]
- **OT4 — Multi-schema projects now validate/generate against their FULL schema set.** `_schema_index_for_project`
  fetched only the primary schema, so for a project spanning N schemas the R1 invariant wrongly rejected — and the
  Copilot was blind to — tables in the **secondary** schemas. It now **unions every member schema** (mirroring
  onboarding). Model *names* and per-model `tableReference {schema, table}` were already correct (D4 collision
  rename + per-dataset schema), so the compiled manifest the wren engine executes always carried the right schema;
  this fix aligns the **validation/Copilot index** with it. Snapshot-fallback (Superset outage) stays names-only
  by design, but now serializes the unioned set. [`app.py`, regression: `test_multi_schema_schema_index.py`]

- **OT5 — Cross-schema onboarding actually onboarded only ONE schema (fixed).** Root cause: the include-mode
  fetch passed a single `schema_name` (the primary) **alongside** the cross-schema `dataset_ids`, and **all three
  Superset providers** (`client.py` SQLAlchemy, `mcp.py`, `rest.py`) intersect `schema AND ids` — so datasets in
  the project's *secondary* schemas were silently dropped. This is the classic *silent-narrowing* anti-pattern: an
  orthogonal scope filter quietly shrinking a precise primary-key selection (cf. "fail fast / make invalid states
  unrepresentable", Ousterhout *A Philosophy of Software Design* ch. on errors; "Don't return partial results
  silently"). Three-part fix: (a) **`_onboarding_context`** fetches by ids alone (`schema_name=None`); (b) all
  three providers made **ids-authoritative** (an explicit id selection is never narrowed by `schema_name`; DB
  scope still bounds it — prevents cross-DB leak); (c) a **project-schema-set boundary guard**
  (`_enforce_onboarding_schema_boundary`, F5/R1) drops any dataset whose schema is outside the project's proven
  set even if its id was supplied, and **logs a reconciliation shortfall** so a silent drop surfaces (defense in
  depth — R1 also rejects at activation). [`app.py`, `integrations/superset/{client,mcp,rest}.py`]

**Tests (this round):** `OnboardingTablePicker.test.tsx` rewritten (18 tests: tree render, collapse/expand,
cross-schema accumulation + count, per-schema + global select-all, cross-schema inline registration,
virtualization, permissions); `index.test.tsx` onboarding tests green; `test_cross_schema_onboarding.py` (2: include
onboarding spans every selected schema + boundary guard drops out-of-set ids); `test_multi_schema_schema_index.py`
(2). Backend **912 passed** (+4 new across the round). tsc + prettier + jest + ruff/black (own files) clean.

**Residual UI notes (for QA):**
1. **Readiness + coverage badges live in the workspace strip**, not (yet) in the ProjectBrowser rows — the plan
   floated rows; the strip is a valid relocation and lower-risk. Moving them to rows is a follow-up.
2. **Copilot rail is scoped to the Models sub-tab** (editor-adjacent). The toggle is in the workspace strip, but
   the rail only renders on Models; on Instructions/Graph there is no rail. Acceptable per the "rail beside the
   editor" decision; a workspace-level rail is a larger follow-up.
3. **Doc count** is surfaced via the relocated state badge (`%s document(s)`) in the strip — not a per-Documents-
   group badge on the tree. Equivalent information, simpler placement.
4. **Cross-schema onboarding (OT1/OT2):** the picker is a collapsible schema→tables tree; registered + unregistered
   selections accumulate across **all** schemas and the count is the true total. The single-schema Gmail-style
   "all" mode is removed (the confirmed selection is always an explicit cross-schema `include` list). Whole-project
   onboarding (no explicit selection) remains available via the Onboard banner.
5. **No live/visual QA performed** — all covered by unit/integration tests with mocked network.
6. **File/symbol still named `SemanticLayerEditor`** internally; the user-facing surface is "MDL Lab". A rename is
   optional cosmetic cleanup.

> Anchors verified against the working tree; symbols are stable, lines drift.

The six requested changes all reduce to **one structural shift**: today a project is selected *inside* a
schema-keyed editor (the ProjectBrowser is buried in the Models tab's left pane, *under* the
Models/Instructions/Graph tabs). We want the **project to be the unit of navigation** — MDL Lab is a
master-detail surface where the ProjectBrowser is the master and a project's workspace
(Models/Instructions/Graph + Copilot) is the detail. This doc audits what exists and proposes the new layout.

---

## As-built — "UI autocloses / deselects project" fix (selection lifecycle)

**Symptom:** the workspace deselected the open project too often — after auto-onboard started, after
accepting a Copilot changeset, and on other mutations.

**Root cause (architectural).** Project selection had **two refresh paths** and the wrong one was the default:
- `refresh()` — *scope-based*: re-resolves a project from ambient `(database, schema)` and, when
  `scope.schema_name` is empty, runs `setProject(null)` + clears the workspace.
- `refreshOpenProject()` — *id-based*: reloads the open project by id, never nulls.

The MDL Lab entry tab carries **no schema** (`schemaName=''`), so every caller of the scope-based `refresh()`
hit the `!scope.schema_name` guard and **deselected** the project. `refreshOpenProject` was wired into only
`runOnboard` + the poller; **~10 other handlers still called `refresh()`** — including `onApplied={refresh}`
(accept changeset) and the auto-onboard `onConfirm`. So onboard *start*, changeset *apply*, file save/delete,
upload, and reset each blanked the workspace.

This is two anti-patterns at once: **dual source of truth** for "which project is open" (`project` state +
`selectedProjectIdRef`) and an **overloaded function** (`refresh` conflated *resolve-or-create by scope* with
*reload the open project*).

**Comparison with Superset patterns.** SQL Lab keys the editor off the **active tab / `queryEditor.id`** in
Redux and never re-derives the active editor from ambient db/schema; data is reloaded **by id**, not by
re-resolving context. Newer Superset data access uses **RTK Query hooks keyed by id** (e.g. `useSchemasQuery`,
already used by the schema pickers) that cache + refetch by key. The MDL Lab violated this by re-resolving the
project from ambient scope on every refresh.

**Fix applied (`SemanticLayerEditor/index.tsx`).** `refresh()` is now **id-aware and the single source of
truth**: if a project is explicitly open (`selectedProjectIdRef`), it reloads **by id** and never nulls the
selection; it falls back to scope resolve-or-create **only when nothing is selected yet** (the legacy
schema-tree entry's initial load). The id/scope bodies share an `applyProjectData()` helper. `refreshOpenProject`
is now a thin alias to `refresh`. Net effect: **every** existing `refresh()` caller (accept-changeset, auto-onboard,
save, delete, reset, upload) is correct in the Lab with one change, instead of auditing each call site.

Also fixed: the Superset `Modal` clones a **function-component** `footer` to inject `closeModal`
([Modal.tsx](../../superset-frontend/packages/superset-ui-core/src/components/Modal/Modal.tsx) `typeof footer.type === 'function'`),
so a `<Flex>`-root footer leaked `closeModal` onto a DOM node (React warning). `AutoOnboardModal` and
`OnboardingTablePicker` now use a host `<div>` footer root (type is a string → no injection).

**Recommended follow-ups (not yet done):**
1. **Lift selection to a tiny reducer/context** (or RTK Query keyed on `project.id`) so reloads are *declarative*
   (`useGetProjectQuery(id)`), eliminating the imperative `refresh()` sprinkled across ~10 handlers — the
   Superset-idiomatic end state.
2. The live **document-status poll** is gated on `scope.schema_name` so it is **inert in the Lab** (no schema);
   re-key it on `project?.id` like the rest of the workspace.
3. **`closeModal` leak** also affects any other `<Flex>`-root modal footer in the codebase — apply the host-root
   pattern or pass `footer` as an array.
4. The antd `Select` "setState during SelectTrigger render" warning in `NewProjectModal` is an rc-select internal
   (multiple-mode virtual list); benign and best left to an antd upgrade.
5. `GET/POST /projects/{id}/documents` returned **400** on a particular upload — surface the upload error to the
   user (toast) instead of swallowing; investigate the offending file (likely a `register_document` `ValueError`:
   unsupported type / size / extraction). Separate from the deselect.

**Tests:** new regression `index.test.tsx` — "Lab entry (no schema): a refresh-triggering action keeps the project
selected" (Reset in a `projectId` entry keeps `mdl-workspace`, never shows `mdl-empty`, never re-resolves by
schema). Full editor suite green for all touched files (index 20, OnboardingTablePicker 18, AutoOnboardModal).
Pre-existing flakiness in `CopilotPanel.test.tsx` (in-progress `CopilotPanel.tsx` edits, unrelated) is not from
this change.

## Step 1 — Audit (source-backed)

### 1.1 The two confusingly-similar surfaces (ask 4)

There is **one component**, `SemanticLayerEditor/index.tsx`, entered **two ways**:
- **Per-schema** (legacy): schema-tree row action → `openSemanticLayerEditor(db, catalog, schema)` →
  schema-bound tab (`buildSemanticLayerEditorId`). [`TreeNodeRenderer.tsx:268-275`,
  `TableExploreTree/index.tsx::handleOpenSemanticLayer`, `actions/sqlLab.ts:881-903`].
- **Browse-first MDL Lab** (new): DB-tree toolbar "Open MDL Lab" → `openMdlLab(db, catalog)` → schema-less tab
  (`buildMdlLabId`) [`TableExploreTree/index.tsx::handleOpenMdlLab`, `actions/sqlLab.ts::openMdlLab`].

Both render the same editor; the only difference is whether the tab carries a `schemaName`. This is the "similar
layout, slightly-off" confusion. **Deprecation = collapse to the browse-first entry only.**

Tabs live in redux `semanticLayerEditors[]` + `activeSemanticLayerEditorId`
(`types.ts:87-102`, reducer `OPEN/CLOSE/SET_ACTIVE_SEMANTIC_LAYER_EDITOR`). They are **not persisted**
(`persistSqlLabStateEnhancer` excludes them) — so no migration/cleanup of stored state is needed.

### 1.2 Current layout tree (`SemanticLayerEditor/index.tsx`)

```
SemanticLayerEditor (tab)
├── EditorHeader  ← the "top bar" (ask 5: remove)
│   ├── project.name                         (Typography.Title)        → remove (implied by browser)
│   ├── Provenance button (HistoryOutlined)  → setShowProvenance       → move
│   ├── CoverageBadge (projectId)            → move
│   ├── SemanticLayerStateBadge (state)      → move
│   ├── SchemaSetControl (schema tags + Add) → move
│   └── Copilot toggle button                → move (relocate, not delete)
└── ContentTabs  ← OUTER tabs (ask 6: must become INNER, per-project)
    ├── "Models"   → EditorSplitter[ BrowserPane | EditorPane | CopilotRail? ]
    │     • BrowserPane = ProjectBrowser  ← the MDL Lab list, buried here (ask 6 problem)
    │                   + Save/New + WorkspaceTree(files+docs) + Activate-all
    │                   + Upload document + Reset
    │     • EditorPane  = MDL JSON editor  OR  DocumentDetailPane (when a doc is selected)
    │     • CopilotRail = CopilotPanel (shown when showCopilot && project)
    ├── "Instructions" → InstructionsPanel (scope-keyed)
    └── "Graph"        → SchemaGraph (scope-keyed)
+ OnboardingTablePicker (modal), rename/duplicate/reset ConfirmModals, MdlProvenanceDialog
```

### 1.3 Scope dependency of each sub-component (the reroute crux, ask 4)

The editor builds `scope` from **tab props** (`databaseId, catalogName, schemaName`). After deprecation the tab is
browse-first (no schema), so anything reading the tab's schema must instead read the **opened project**
(`project.schema_name` / `project.schema_names`).

| Sub-component | Scope source today | Needs schema? | Reroute |
|---|---|---|---|
| `InstructionsPanel` | tab `scope` (gates on `scope.schema_name`) | **Yes** | derive scope from opened project |
| `SchemaGraph` | tab `schemaName` | **Yes** | use project's schema(s) |
| `OnboardingTablePicker` | tab `schema` (lists physical tables for one schema) | **Yes (singular)** | add a schema selector (project's `schema_names`, default primary) |
| `CopilotPanel` | `project.id` | No | none |
| `useDocumentIngestion` (upload) | `project.id` | No | none |
| `DocumentDetailPane` | selected doc | No | none |
| `CoverageBadge` | `project.id` | No | none |
| `MdlProvenanceDialog` | `project.id` | No | none |
| `SchemaSetControl` | `databaseId/catalog` + project schema set | partial | already project-driven |

**The only functional break** under browse-first is `OnboardingTablePicker`: it lists physical tables for a single
`schema`, so a multi-schema project opened with no schema can't onboard until a schema is chosen. The
`onboard`/`upload` **backend endpoints are project-keyed and schema-agnostic** — this is purely a UI input gap.

### 1.4 Feature inventory (everything that must survive the reroute)

Project CRUD (browser), open/switch project, MDL file CRUD (tree + editor: save draft/activate/validate/delete,
activate-all), document upload + viewer (`DocumentDetailPane`), Copilot chat + changeset apply + coverage +
inspector, onboarding (picker → job → poll), reset, provenance dialog, schema-set view/add, instructions CRUD,
schema graph. All are reachable today only *inside* the editor's tabs.

### 1.5 CopilotPanel header (ask 3)

`CopilotPanel.tsx:649-722` — header is a single `Flex justify="space-between" align="center"` with the
`"MDL Copilot"` title (left) and six action buttons New chat / History / Rename / Delete / Coverage / Inspector
(right). When the rail is narrow the title column is squeezed and wraps one char per line. Fix = stack vertically.

### 1.6 Create-project flow (ask 2)

`index.tsx::handleCreateProject` posts `createSemanticProject({database_id, catalog_name, schema_name,
schema_names})` using the **tab scope's** schema (guarded with a warning when browse-first has none). There is no
schema picker. `SchemaSetControl` already shows how to list a DB's schemas — `useSchemasQuery({dbId, catalog})`
(`src/hooks/apiResources/schemas`) — reusable for a create dialog.

### 1.7 Test surface a redesign disturbs

`TabbedSqlEditors.test.tsx` (semantic tab open/label/close), `SemanticLayerEditor/index.test.tsx` (resolve flow,
onboard banner, browse-first, projectId entry), `TableExploreTree.test.tsx` (per-schema action + MDL Lab toolbar),
`CopilotPanel.test.tsx` (header/actions), `InstructionsPanel.test.tsx` (scope gating), `ProjectBrowser.test.tsx`.

---

## Step 2 — Redesign

### 2.1 Target relationship (the core change)

```
MDL Lab  (single browse-first tab; entered only via "Open MDL Lab")
├── LEFT  — ProjectBrowser  (master; persistent, collapsible)
│     MDL Lab title · [+ New project (schema picker)] · search · grouped rows
│     each row: name · primary schema · schema-count · [readiness] · [coverage]  · ⋯actions
│     (readiness + coverage badges MOVE here from the old top bar — they are per-project)
└── RIGHT — Project Workspace  (detail; the selected project)
      • no project selected → empty state ("Select a project to open, or create one")
      • project selected →
        ┌ workspace context strip (compact, replaces the heavy top bar) ─────────────┐
        │ Schemas: [sales][crm] [+ add]      ⟳ History(provenance)      [Copilot ▸]    │
        └────────────────────────────────────────────────────────────────────────────┘
        └ sub-tabs (now CHILDREN of the project, ask 6):
            [ Models ] [ Instructions ] [ Graph ]
              Models       → file tree (+ doc count badge on Documents) | MDL editor / DocumentDetailPane
              Instructions → InstructionsPanel (scope from PROJECT)
              Graph        → SchemaGraph (scope from PROJECT)
        └ Copilot rail (collapsible, workspace-level; toggled by the strip's [Copilot ▸])
```

Mechanically: **lift `ProjectBrowser` out of the Models tab into the Lab's top-level left Splitter panel**, and
**move `ContentTabs` (Models/Instructions/Graph) into the right (workspace) panel** so they render the selected
project. The old `EditorHeader` is deleted; its contents relocate (below).

### 2.2 Where each removed top-bar item goes (ask 5)

| Item | Decision | New home |
|---|---|---|
| Project name | **Remove** | implied by the highlighted ProjectBrowser row |
| Provenance (history) icon | **Move** | workspace context strip (project-level action) |
| CoverageBadge | **Move** | ProjectBrowser row badge (per-project) |
| Readiness/state badge | **Move** | ProjectBrowser row badge (per-project) |
| Schema set + Add schema | **Move** | workspace context strip (manage project's schemas) |
| Copilot toggle | **Move** | workspace context strip ("Copilot" toggle for the rail) |
| Document count | **Move** (not elsewhere today) | badge on the Models tree "Documents" group (and/or the strip) |

Net: the heavy global header is gone; per-project status lives in the list rows, and the few project-level
*actions* live in a thin workspace strip — not a title bar.

### 2.3 Per-ask change list

- **Ask 1 — single entry.** Remove the per-schema `ActionButton` (`TreeNodeRenderer.tsx:268-275`) and
  `handleOpenSemanticLayer` wiring (`TableExploreTree/index.tsx`); keep the toolbar "Open MDL Lab" button.
- **Ask 2 — create with schemas.** "New project" opens a dialog: name + **multi-select of the DB's schemas**
  (`useSchemasQuery`), first selected = primary, ≥1 required → `createSemanticProject`. Removes the browse-first
  "create needs a schema" guard (the dialog always collects them).
- **Ask 3 — Copilot header.** Make the header a vertical stack: `"MDL Copilot"` title row, then the action-button
  row (wrapping). `CopilotPanel.tsx:649-722`.
- **Ask 4 — deprecate the editor / reroute.** One entry (browse-first). Build a `projectScope` from the **opened
  project** and feed it to `InstructionsPanel`, `SchemaGraph`, and the onboarding default. Retire
  `openSemanticLayerEditor`/`buildSemanticLayerEditorId` + the schema-bound tab branch (keep `projectId`
  deep-link). Internally the component may keep its name; user-facing concept is "MDL Lab".
- **Ask 5 — remove top bar.** Delete `EditorHeader`; relocate per §2.2.
- **Ask 6 — Models/Instructions/Graph as sub-components.** Move `ContentTabs` into the workspace (right) panel,
  rendering the selected project; `ProjectBrowser` becomes the Lab's top-level master pane (not a child of Models).

### 2.4 Onboarding under multi-schema — cross-schema picker (DECIDED)

`OnboardingTablePicker` is **expanded from single-schema to a hierarchical, cross-schema picker**: it lists the
project's **schemas as groups**, each expandable to its **tables**, with the same checkbox UI as today — so a user
can select tables across *multiple* schemas in one pass and onboard them together. Mechanically: iterate the
opened project's `schema_names`, call `listPhysicalTables(databaseId, schema, catalog)` per schema (lazy per group
is fine), render a two-level checkbox tree (schema → tables, with select-all per schema). The onboard selection
already carries dataset/table ids; the backend onboard is project-keyed and schema-agnostic, so no backend change
— the model's `tableReference.schema` is set per selected table. (This also matches the R1 invariant: every
selectable table is from a schema already in the project's proven set.)

---

## Step 3 — Phased implementation plan (for approval; not yet executed)

Each phase ends green (pytest n/a — frontend-only; jest + tsc + prettier) and is independently shippable.

**UP1 — Copilot header (ask 3).** Smallest, isolated. Stack the header vertically in `CopilotPanel.tsx`; update
`CopilotPanel.test.tsx`. *Risk: none.*

**UP2 — Create-project schema picker (ask 2).** New `NewProjectModal` (name + schema multi-select via
`useSchemasQuery`); wire `handleCreateProject` to it; drop the browse-first create guard. Tests: modal selects
schemas → `createSemanticProject` payload; ProjectBrowser `onCreate` opens the modal. *Risk: low (additive).*

**UP3 — Remove per-schema entry (ask 1).** Delete the `TreeNodeRenderer` schema action + `handleOpenSemanticLayer`;
keep the toolbar button. Update `TableExploreTree.test.tsx` (drop the per-schema-action test). *Risk: low.*

**UP4 — The structural reorg (asks 4/5/6) — the large one.** In `SemanticLayerEditor/index.tsx`:
  1. Build `projectScope` from the opened project; pass it to `InstructionsPanel` + `SchemaGraph` (replaces tab
     scope). Add the onboarding schema selector (§2.4).
  2. Delete `EditorHeader`; add a thin workspace context strip (schemas + add, provenance history, Copilot toggle).
  3. Lift `ProjectBrowser` to the Lab's top-level left Splitter panel; move `ContentTabs` into the right panel.
  4. Move readiness + coverage badges into `ProjectBrowser` rows (extend `ProjectBrowserProject` with
     `readiness`/`coverage`); move doc count onto the Models tree's Documents group.
  5. Empty state in the workspace when no project is selected.
  Tests: rewrite `index.test.tsx` layout assertions (browser is top-level; sub-tabs under workspace; no top bar);
  update `InstructionsPanel`/graph scope-from-project; onboarding picker schema select. *Risk: HIGH — touches the
  168-test editor suite + the hot `index.tsx`. Land UP1–UP3 first; do this as one focused pass with full
  manual QA.*

**UP5 — Code cleanup (ask 4 finish).** Remove now-dead `openSemanticLayerEditor`/`buildSemanticLayerEditorId` and
the schema-bound tab branch in `TabbedSqlEditors`; simplify `SemanticLayerEditorTab` (drop `schemaName`, keep
`projectId`). Optional file/symbol rename `SemanticLayerEditor` → `MdlLab`. *Risk: low once UP4 lands; do last.*

Suggested order: **UP1 → UP2 → UP3 → UP4 → UP5** (UP1–UP3 are quick wins and de-risk the big UP4).

### Risks & mitigations
- **R-UI1 (UP4 blast radius):** `index.tsx` is a hot hub with a large test suite. Mitigate: land the small phases
  first; keep `projectScope` derivation additive; one writer; full jest + manual QA before merge.
- **R-UI2 (onboarding multi-schema):** without §2.4 a multi-schema project can't onboard. Mitigate: ship the schema
  selector *in* UP4 (don't split it out).
- **R-UI3 (lost entry points):** removing the per-schema action could strand users who relied on it. Mitigate: the
  DB-tree "Open MDL Lab" button + deep-link entry remain; the project is then one click away in the browser.
- **R-UI4 (instructions scope):** instructions are personal *and* schema-scoped today; deriving scope from the
  project must keep the personal-scope note (DP-NEW) and the per-(owner,scope) keying intact.

### Resolved decisions (locked)
1. **Copilot placement** → collapsible right **rail** in the workspace, toggled from the context strip (chat beside
   the editor). Not a sub-tab.
2. **Documents** → count **badge on the Models tree's Documents group** (minimal; no separate Documents sub-tab).
3. **Onboarding** → **hierarchical cross-schema picker** (§2.4): list schemas → tables with checkboxes, select
   across multiple schemas in one onboard pass. (More than a single default-primary selector — folded into UP4.)
