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

# MDL Lab — Gap Analysis & Follow-up Closure Plan

**Status:** FP1–FP5 IMPLEMENTED + tested (backend 906 passed/11 skipped; touched frontend suites green —
copilot-tools 21, provenance-schemas 13, coverage 20, InstructionsPanel 9, MdlProvenanceDialog 7,
SemanticLayerEditor/index 16, ProjectBrowser 9, TableExploreTree + TabbedSqlEditors 23, api.ts client 30).
The **second round** closed the deferred FP4 remainder + FP5:
- **FP4-route** — a first-class, DB-scoped **MDL Lab** destination: an "Open MDL Lab" toolbar action on the SQL
  Lab database tree opens a browse-first semantic tab (no schema) keyed by `buildMdlLabId`; the editor accepts an
  optional `projectId` for deep-linked entry. (`actions/sqlLab.ts::openMdlLab`, `types.ts`,
  `TabbedSqlEditors`, `TableExploreTree`, `SemanticLayerEditor/index.tsx`.)
- **FP4-deeplink** — the schema-tree action is relabelled "Open in MDL Lab" (deep-links a schema's project).
- **FP4-perf** — `ProjectBrowser` paginates (50/page + "Show more"), bounding the DOM.
- **FP5/DP6** — `include_documents` duplication: `duplicate_documents` in both stores (project-scoped reads,
  fresh ids) + endpoint re-embed under the clone's vector scope (best-effort) + a duplicate confirm modal with an
  "Also copy uploaded documents" toggle.

**Still needs live QA (cannot run the app here):** the MDL Lab tab visuals/navigation and the duplicate modal
flow are covered by unit/integration tests with mocked network, not observed running. **Deferred non-goals:** a
top-nav route *outside* SQL Lab (the destination lives in SQL Lab, which matches the DB-scoped model), full
react-arborist virtualization (pagination suffices at realistic scale), and an async job for very large
include-documents re-embeds (synchronous best-effort today). See §9 of `MDL_LAB.md`.

**Method:** every claim below was verified against source (grep + read), not trusted from
`MDL_LAB.md`. File:line anchors captured against the working tree; symbols are stable, lines drift.

**Companion docs:** `plan_mdl_lab_spec.md` (intent/decisions), `MDL_LAB.md` (as-built),
`MDL_PROVENANCE_AND_COVERAGE.md`, `plan_multi_schema_mdl_spec.md`.

---

## 1. Verification result — what is genuinely DONE (source-confirmed)

These spec claims are MATCHED in source (no action needed):

- **P1 identity** — `slug` column + partial unique index `uq_ai_agent_semantic_project_slug_active`
  on `(database_uri_fingerprint, catalog_name, slug) WHERE deleted_at IS NULL`
  (`persistence/models.py` ~L227-246); old `(…, schema_name, deleted_at)` constraint gone.
  Migration `0011_project_slug_identity.py` (down_revision `0010_…`) does the expand/contract; single head.
- **P2 access** — `access.py::_project_with_permission` (~L283-304) FULL→write / else read, `identity` unused
  (no admin tier); `projects.py::_with_permission`/`_is_visible` (~L816-832) `del owner_id`;
  `rename`/`update`/`delete` raise on `read`.
- **P2 owner→project re-scope** — `sqlalchemy_store.py`: `list_project_events`, `list_project_documents`,
  `list_project_chunks`, `find_document_by_checksum`, `delete_project_events` all `del owner_id` and filter by
  `project_id` only. `coverage_store.py::find_complete` keys on `(project_id, mdl_checksum, docs_checksum)` —
  already project-scoped (spec §5.6.3 satisfied; the as-built note is correct, the spec over-stated the work).
- **P3 CRUD** — `app.py` POST `/projects` (write + `enrich_request` + `create`), POST `/{id}/duplicate`
  (compensating-delete on file-copy failure ~L1058-1079), PATCH `/{id}` (write), DELETE `/{id}` (write, not admin),
  GET `/projects` (DB-scoped). `_emit_project_created_provenance` (~L1173-1199) emits `mdl_project_created`
  with `detail.duplicated_from`. `mdl_files.py::duplicate_files`/`_clone_file` in both stores.
- **P5 ungate** — `app.py::_require_project_ready` blocks only `indexing` (~L1846-1861); old all-but-ready 409 gone.
  `copilot/tools.py::propose_onboard_table` + R1 rejection for tables outside the accessible schema set (~L386-393).
- **DP10 (partial)** — `app.py::get_project_provenance` sets `is_self = entry.actor == identity.owner_id`
  (~L3678); `schemas.py::coalesce_user_runs` splits on actor change (~L589); `MdlProvenanceDialog.tsx` renders
  "You" only when `is_self`.
- **Frontend** — `ProjectBrowser.tsx` mounted in `index.tsx` BrowserPane (~L1155); `openProject` switches project
  via `selectedProjectIdRef`; CopilotPanel gates on `isBootstrapping === (readinessStatus==='indexing')`;
  all five `*SemanticProject` api.ts clients present; `SemanticProject.slug?`, `ProvenanceKind +project_created`,
  `ProvenanceEntry.is_self?`.

---

## 2. Confirmed GAPS (spec/intent vs actual source)

| # | Gap | Spec ref | Source state (verified) | Severity |
|---|---|---|---|---|
| **G1** | **No first-class MDL Lab surface.** ProjectBrowser lives only inside the schema-opened editor's BrowserPane; the editor entry contract is still `(databaseId, catalogName, schemaName)` + `resolveSemanticProject`, with project switching layered on via `selectedProjectIdRef`. No dedicated route, no nav entry, no schema-tree "Open in MDL Lab" deep-link. | F1, §5.2, DP4, R6 | `index.tsx` `SemanticLayerEditorProps` = `{databaseId, catalogName, schemaName, schemaNames?}` (~L266-273); resolve at ~L416-422; `TabbedSqlEditors/index.tsx` mounts by `(db,catalog,schema)` (~L273-278). No projectId entry path. | **High (headline F1 unmet)** |
| **G2** | **F4 relationship enrichment has no tool.** Only singular `propose_onboard_table` exists. No `propose_relationships`, no plural `propose_onboard_tables`. The worked example ("onboards named tables per schema **+ adds relationships**") can only add relationships by hand-authoring JSON through `write_mdl_file`. | F4, §5.5, §11-P5 | `copilot/tools.py` tool list (~L126-261): 10 tools, none for relationships or multi-table onboard. | **High (F4 half-met)** |
| **G3** | **No actor display-name resolution.** Shared-project provenance shows the raw `owner_id` (or a generic "Teammate"), not a human name. `is_self` works; the *other* actor's label is their id. | §5.6.2, DP10, R10 | `app.py::get_project_provenance` stamps `is_self` only, no name (~L3678); `MdlProvenanceDialog.tsx` falls back to `entry.actor ?? 'Teammate'` (~L308). No `actor_name` on `ProvenanceEntry`. | Medium |
| **G4** | **Instructions NOT re-scoped (spec/impl divergence).** Spec §5.4/DP3 lists instructions among the owner→project re-scopes; source keeps them `owner_id`-scoped *by design* (docstring: "context, never permission sources"). Net effect: a shared project's Copilot instructions are personal per-user, not shared — not a leak (no widening), but a sharing inconsistency vs the documented intent. | §5.4, DP3 | `instructions.py::list_instructions` filters `owner_id == owner_id AND scope_hash` in all three stores (~L186, L246-253, L333). | Medium (needs decision) |
| **G5** | **ProjectBrowser is unvirtualized + list API unpaginated.** Plain `map()` over all groups/rows; `listSemanticProjects` fetches every project in one call. Fine at small N, degrades at scale; R7 anticipated virtualization (react-arborist) + pagination. | §5.2, R7 | `ProjectBrowser.tsx` renders `filteredGroups.map(...project.map(Row))` (~L264-336); `api.ts::listSemanticProjects` no paging (~L1063-1079). | Low/Medium |
| **G6** | **Duplication is structure-only with no "include documents" path and is non-transactional.** No DP6 opt-in to copy + re-embed documents; cross-store copy (project → files → provenance) is compensating-delete, not atomic. | §5.3, DP6, R4 | `app.py` duplicate handler (~L1029-1083) copies project + files only; try/except compensating-delete. No re-embed job. | Low (accepted trade-off) |
| **G7** | **Multi-schema coverage test missing.** `_active_mdl_checksum`/`_coverage_documents` are schema-agnostic but untested for the routine multi-schema case. | §5.6.7, R-cov | No `test_multi_schema_coverage.py`. | Low |
| **G8** | **No manual/visual QA.** Everything is unit/integration with mocked network; never observed running. | §9.5 | — | Low (process) |

**Non-gaps worth recording:** the document-retrieval tool call sites still *pass* `owner_id`
(`tools.py::_list_documents`/`_search_documents` pass `owner_id=self._owner_id`) but the store methods
`del owner_id`, so behaviour is correctly project-wide — it's a harmless dead argument, not a scoping gap.
The `0012` list-index migration the spec named is unnecessary: the partial unique index leads with
`database_uri_fingerprint`, covering the list query — no action.

---

## 3. User-flow vs actual-UI gaps (intent ↔ UX)

| Intended flow (spec §8) | Actual UI | Gap |
|---|---|---|
| "Browse my MDL projects as first-class objects; open one to see its structure." | The browser is only reachable *after* right-clicking a schema to open the MDL editor; there is no Lab destination. You must already be inside one project to discover the rest. | **G1** — entry/discoverability inverted. F2 (browse/open/CRUD) works once you're in; F1 (a first-class home) is absent. |
| "Upload a BI doc on cross-schema joins; the Copilot onboards those tables from each schema **and enriches relationships** — I approve." | Copilot can `search_documents` + `propose_onboard_table` (one table/call) and write relationship JSON by hand. No single reviewable changeset that adds schemas + tables + relationships together. | **G2** — onboarding half works per-table; the "+ relationships" half has no first-class tool, so the diff is piecemeal, not the promised one-shot reviewable changeset. |
| "Shared provenance shows who did what." | A teammate's edit renders as a raw user id / "Teammate". | **G3** — correct *attribution boundary* (You vs not-you) but no readable name. |
| "Any DB-authorized user sees the project's full context." | True for docs/events/coverage/RAG. Copilot **instructions** are still personal. | **G4** — context-sharing is incomplete vs DP3 wording. |

---

## 4. Residual risks (source-anchored)

| Risk | Where (entry point) | Status / mitigation |
|---|---|---|
| **R-G1 IA churn**: changing the editor entry from `(db,catalog,schema)` → `projectId` touches the hot hubs `index.tsx`, `TabbedSqlEditors/index.tsx`, `actions/sqlLab.ts` and the 168-test suite. | `index.tsx` (~L266-273, L405-471), `TabbedSqlEditors/index.tsx` (~L273-278), `actions/sqlLab.ts::buildSemanticLayerEditorId` | Keep `(db,catalog,schema)` as an optional bootstrap that *resolves then redirects* to a `projectId` tab; add the projectId path additively; one writer per hot file. Manual QA required. |
| **R-G2 unproven-schema onboard via relationships**: a `propose_relationships`/plural-onboard tool must route every schema-add through the R1 access proof or it bypasses §5.5's invariant. | `copilot/tools.py` (new handlers), `mdl_validator.py::SchemaIndex.has_table` | Reuse the `propose_onboard_table` R1 guard (`has_table(table, schema)` reject); validate the assembled relationship model through the existing `validate_project` path before it becomes a changeset. Test: relationship referencing an unproven schema is rejected pre-apply (mirrors R3). |
| **R-G4 instructions divergence**: shared project, two users → inconsistent Copilot guidance; or, if "fixed" naively by dropping `owner_id`, a behavioural change that could surprise users who authored personal instructions. | `instructions.py::list_instructions` (3 stores) | **Decision required (DP-NEW below)** before any code change — this is product behaviour, not a bug. Recommendation: align the spec to the code (instructions stay personal, like NL→SQL memory) OR add a separate project-scoped instruction tier; do **not** silently re-scope existing rows. |
| **R-G3 mis-attribution**: without name resolution, audit reads are ambiguous (which teammate?). | `app.py::get_project_provenance`, `schemas.py::ProvenanceEntry` | Add `actor_name` resolved from the AppBuilder user store in the projection; "You" stays gated on `is_self`. Falls back to id when unresolved. |
| **R-G5 list perf** at many DBs/projects (N+1 readiness/coverage badges). | `ProjectBrowser.tsx`, `api.ts::listSemanticProjects` | Lazy/batch badges per row; paginate the list endpoint; virtualize only if row counts justify it. Low until scale shows up. |
| **R-G6 orphan clone** on a crash between clone and compensating-delete. | `app.py` duplicate handler (~L1058-1079) | Cheap to detect/delete; acceptable. A true two-phase/transactional clone is the only full fix (deferred). |
| **R2 migration on the live volume** (persistent `ai_agent.db`) — unchanged standing risk. | `0011_project_slug_identity.py` | Test `alembic upgrade head` against a copy of a real volume before deploy; single head must hold. |

---

## 5. Decision point — RESOLVED

**DP-NEW — Copilot instruction scope under sharing. → RESOLVED: (a) keep personal, with a UI explanation.**
Instructions stay `owner_id`-scoped (like NL→SQL memory). The spec §5.4/DP3 is corrected to mark instructions
personal-by-design. **Added requirement:** the instructions UI must clearly tell the user their instructions are
**personal to them** (not shared with other DB-authorized users of the project), so the divergence from the
otherwise project-shared context is explicit, not surprising. Implemented in FP3a.

Rejected alternatives: (b) drop `owner_id` from `list_instructions` (would change existing behaviour + need a
per-user-row migration); (c) add a separate project-scoped instruction tier (larger build, not wanted now).

---

## 6. Follow-up plan (phased, dependency-ordered)

Each phase ends green per `CLAUDE.md` (pytest + touched Jest + `pre-commit run`), single alembic head.
Phases are ordered by value/risk; FP1–FP3 are low-risk backend/leaf work, FP4 is the large IA change.

### FP1 — Complete F4: relationship + multi-table onboarding tools (backend, isolated)
**Why first:** highest value, lowest blast radius (one file + tests), unblocks the F4 worked example.
- **Entry points:** `copilot/tools.py` — add `propose_relationships(models[], joins[])` and `propose_onboard_tables(schema, tables[])` (plural) as changeset-emitting tools next to `propose_onboard_table`; reuse `_safe_model_name`, the typed `SchemaIndex`, and the `write_mdl_file` staging path. Register specs (~L126-261) + dispatch (~L273) + handlers.
- **R1 mitigation (R-G2):** every table/schema referenced must pass `SchemaIndex.has_table(table, schema)`; assembled relationships validated via the existing `validate_project` before becoming a changeset. Reject + explain on any unproven schema.
- **Provenance:** agent-applied output already surfaces as `enrichment`/`copilot_edit` (§5.6.4) — no new kind.
- **Tests (`test_copilot_tools.py`):** plural onboard builds N valid base models; `propose_relationships` builds a join model; both reject an unproven schema/table; doc-grounded path tags `enrichment`.

### FP2 — DP10 attribution: actor name resolution (backend projection + thin FE)
- **Entry points:** `schemas.py::ProvenanceEntry` add `actor_name: str | None`; `app.py::get_project_provenance` resolve `entry.actor` → display name via the AppBuilder user store (batch the lookups), set alongside `is_self`; `api.ts::ProvenanceEntry` add `actor_name?`; `MdlProvenanceDialog.tsx` render `actor_name ?? actor ?? 'Teammate'` when `!is_self`.
- **Mitigation (R-G3):** "You" stays gated on `is_self`; fall back to id when a name can't be resolved (deleted user). No N+1 — resolve the distinct actor set once per request.
- **Tests:** projection resolves a known user to its name; unknown actor falls back to id; second user's edit never renders "You" (extend `test_provenance_schemas.py` + a `MdlProvenanceDialog.test.tsx` case).

### FP3 — instructions personal-scope UI explanation (DP-NEW=a) + multi-schema coverage test
- **FP3a (instructions stay personal + explain in UI):** no scope change to `instructions.py`. Update
  `plan_mdl_lab_spec.md` §5.4/DP3 to mark instructions personal-by-design. Add a clear UI note wherever
  instructions are authored/shown (the Copilot/instructions surface) stating they are **personal to you**, not
  shared with other DB-authorized users of the project. Test the note renders.
- **FP3b (G7):** add `test_multi_schema_coverage.py` — a project spanning ≥2 schemas; assert
  `_coverage_documents`/`_active_mdl_checksum` audit across schemas and produce one project-scoped run (covers §5.6.7).

### FP4 — F1 first-class MDL Lab surface (frontend IA — the big one) — PARTIAL (entry contract DONE; route/deep-link/perf DEFERRED)
**DONE:** the **projectId entry contract** (`SemanticLayerEditor` accepts an optional `projectId`; when set it
loads-by-id via `getSemanticProject` + the tested `openProject`, and the scope-resolve effect is guarded off so
the two paths never race). Additive — legacy schema-tree entry is byte-for-byte unchanged when `projectId` is
absent. Test: `index.test.tsx` "opens by projectId without resolving by schema". This is the architectural unlock
a Lab route consumes.
**DEFERRED (needs app-shell routing + live QA, not shipped):** the standalone top-nav destination, the
schema-tree "Open in MDL Lab" deep-link, and list virtualization/pagination. These require React-Router/menu
registration in the SQL Lab shell and manual QA in a running app; shipping them unverified would violate the
report-faithfully rule. Entry points retained below for the next pass.
**Why last:** largest blast radius, touches hot hubs + the 168-test suite, needs manual QA.
- **Entry points & sequence (serial spine, one writer per hot file):**
  1. **Editor entry contract** — `index.tsx` `SemanticLayerEditorProps` gains an optional `projectId`; when present, **load-by-id** instead of `resolveSemanticProject` (additive; legacy `(db,catalog,schema)` resolves then continues). New `api.ts` `getSemanticProject` already exists — wire the load path.
  2. **Lab destination** — a navigable surface (route/nav entry) rendering ProjectBrowser as master + the editor as detail, keyed by `projectId`; `actions/sqlLab.ts` + `TabbedSqlEditors/index.tsx` for project-keyed tabs.
  3. **Deep-link (R6)** — keep the schema-tree action, repoint it to "Open in MDL Lab" (resolve → projectId tab) so no existing path is removed.
  4. **G5 perf** — paginate `listSemanticProjects`; lazy/batch readiness+coverage badges per row; virtualize the list if row counts warrant.
- **Mitigations (R-G1):** additive projectId path (don't delete the resolve path); land item 1 + tests green before item 2; coordinate `index.tsx`/`CopilotPanel.tsx` single-writer; full manual QA pass (G8) at the end (open Lab → list → open project → see `models/`/`views/`/`raw/` tree → CRUD).
- **Tests:** `index.test.tsx` projectId-entry load; deep-link resolves to a project tab; ProjectBrowser pagination; existing suite stays green.

### FP5 — DP6 duplication depth (optional, defer unless requested)
- **Entry points:** `app.py` duplicate handler — add `include_documents` flag → async re-embed job (reuse the onboarding `JobRunner`) re-keying chunks under `doc:{newId}`; consider a two-phase clone to retire the compensating-delete (R-G6).
- Lowest priority; structure-only clone is the sensible default (DP6).

---

## 7. Recommended order & parallelism

- **Serial spine:** FP1 → FP2 → FP3 → FP4 (FP5 optional, anytime after FP1).
- **Safe to parallelize (write-disjoint):** FP1 (only `copilot/tools.py` + its test) and FP2 (`schemas.py`/`app.py` projection + dialog) touch different files and can run as two leaves; FP3's coverage test is a new file. FP4 must be serial (hot hubs).
- **Always-safe alongside:** a read-only security-review agent re-checking R-G2 (no unproven-schema onboard) and the R1/R9/R12 isolation invariants; a test-authoring agent on new `test_*` files.
- **Merge gate (unchanged):** green pytest + touched Jest + `pre-commit run`, single alembic head.

---

## 8. Bottom line

The shipped work is solid and the security-critical P2 re-scope is correctly done. The **substantive remaining
features** are **G1 (a real Lab surface / projectId entry)** and **G2 (relationship onboarding tool)** — the two
halves of F1 and F4 that the as-built doc under-states as "delivered". G3/G4/G5 are correctness/polish; G6/G7/G8
are completeness. FP1 (relationship tools) is the cheapest high-value next step; FP4 (Lab surface) is the largest
and should be sequenced last with manual QA. **DP-NEW (instruction scope)** needs a product decision before FP3.
