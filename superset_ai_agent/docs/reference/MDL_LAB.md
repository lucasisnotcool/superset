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

# MDL Lab — First-Class Semantic Projects — As-Built Reference

**Status:** Implemented (P1–P5 + gap closure), tested, in the working tree (uncommitted).
**Read this first** for what exists; the spec has the rationale/decisions.

**Companion docs (same dir):**
- `plan_mdl_lab_spec.md` — feature spec: the "why", design options, decision points (DP1–DP11), risks.
- `plan_multi_schema_mdl_spec.md` — the multi-schema foundation this builds on.
- `MDL_PROVENANCE_AND_COVERAGE.md` — provenance/coverage reference (this work re-scoped both to project-level).

> Symbols are stable; line numbers drift — grep the symbol. All claims below are as-built and test-backed.

---

## 1. What this delivered (the five asks F1–F5)

| Ask | Outcome |
|---|---|
| **F1** MDL as a first-class type | Projects have stable identity (`id` + unique `slug`) independent of schema; named, creatable, duplicable. |
| **F2** Project browser | `ProjectBrowser.tsx` lists the database's projects (grouped by DB); opening one switches the editor to it. Mounted in the editor's left **BrowserPane** (not yet a standalone top-nav route — see §9). |
| **F3** Robust/performant/duplicable | Slug identity + partial unique index; structural duplication (project + schema set + MDL files), fresh history. |
| **F4** Copilot drives onboarding (HITL) | Copilot ungated pre-onboarding; `propose_onboard_table` tool + existing `get_physical_schema`/`write_mdl_file` let it onboard tables (incl. cross-schema from a BI doc) as a reviewable changeset. |
| **F5** DB-access scoping (no ownership) | Permission derived from proven DB access (FULL→write, PARTIAL→read); `owner_id`→audit only; documents/events/RAG/coverage re-scoped from owner to **project**. |

---

## 2. Data model & migration (P1)

- **`AiAgentSemanticProject.slug`** (`persistence/models.py`) — identity-safe handle, unique within `(database_uri_fingerprint, catalog_name)`.
- **Identity key swap**: replaced the old `UniqueConstraint(fingerprint, catalog, schema_name, deleted_at)` with a **partial unique index** `uq_ai_agent_semantic_project_slug_active` on `(fingerprint, catalog, slug)` **`WHERE deleted_at IS NULL`** (`sqlite_where`/`postgresql_where`). This genuinely enforces "one active project per slug" — the old `(…, deleted_at)` constraint never enforced active-row uniqueness (SQL treats NULL as distinct).
- **Migration `0011_project_slug_identity.py`** — expand/contract: add `slug` nullable → backfill a unique slug per `(db, catalog)` from each project's name (slugify inlined, deterministic by id) → make non-null + drop old constraint (batch mode) → create the partial unique index + a plain `slug` index. Downgrade reverses.
- **`slugify_project_name(name)`** (`semantic_layer/schemas.py`) — lowercase, hyphenate, collision-suffixed (`sales`, `sales-2`) by the store.
- `schema_name` remains the **primary schema** (wren-core namespace) but is no longer part of identity.

**Schemas added** (`semantic_layer/schemas.py`): `SemanticProject.slug`, `SemanticProjectResolveRequest.name`, `SemanticProjectRenameRequest`, `SemanticProjectDuplicateRequest`.

**Store** (`semantic_layer/projects.py`): `create()` (always-new named), `rename()` (re-slugs uniquely), `clone()` (`_clone_project`: copies identity + schema set, fresh id/slug/timestamps, no history), `_uniquify_slug`, `_taken_slugs`. Both InMemory + SQLAlchemy.

---

## 3. DB-access scoping (P2) — security-critical

- **Permission derivation** (`semantic_layer/access.py::_project_with_permission`): no owner/admin tier. `visibility=="db_access"` + FULL context (datasets visible) → **write**; else **read**. `semantic_full_access_grants_write` flag is now vestigial (FULL always grants write).
- **Store baseline** (`projects.py::_with_permission`/`_is_visible`): `db_access` → write (the access service proved DB access before the store is reached); visibility is purely DB-access (owner branch dropped). `update`/`delete` gate `read → raise` (was admin-only).
- **`owner_id`→`created_by` audit only.** Project access = proven access to its single database (`database_uri_fingerprint`).
- **Owner→project read re-scope** (the literal "project-level provenance/coverage/RAG" ask) — done at the **store layer** so every app.py call site is project-scoped with no app.py change (`del owner_id` keeps the signature):
  - `list_project_events` (provenance), `list_project_documents` (coverage doc set + RAG corpus), `list_project_chunks` (RAG corpus), `find_document_by_checksum` (dedup, DP11), `delete_project_events` (reset) — in **both** `sqlalchemy_store.py` and `memory.py`.
  - Coverage `find_complete` was already project-scoped (no change).
  - **RAG note**: the vector store was already keyed `document_scope_key(project_id)="doc:{project_id}"` (no owner) — only the candidate corpus was owner-filtered, so this is a pure read-filter change with **zero re-embedding**.
- **Safety invariant**: a project is bound to one database fingerprint, so project-scoping never widens beyond the DB boundary. NL→SQL **memory stays owner-scoped** (personal, not project context).

---

## 4. Duplication & CRUD (P3)

- **Endpoints** (`app.py`, under `/agent/semantic-layer/projects`):
  - `POST /{id}/duplicate` (body `{name?}`) — read access on source (creative op); `clone()` + `duplicate_files()`; **compensating-delete** of the clone if the file copy fails (no orphan); emits one `mdl_project_created` provenance entry with `detail.duplicated_from`.
  - `POST /` (create) — proves WRITE DB access via `require_schema_set_permission`; `enrich_request` (public access-service helper) + `create()`.
  - `PATCH /{id}` (rename) — WRITE; `rename()`.
  - `DELETE /{id}` — **changed `admin`→`write`** (admin tier no longer exists; was a P2 regression).
  - `GET /` (list) — already DB-scoped (database_id, optional schema).
- **File copy** (`mdl_files.py::duplicate_files` + `_clone_file`) — copies non-deleted files (preserves path/content/**status**/validation), new ids, re-parented, re-stamped.
- **DP6/DP8 (locked)**: duplication copies project + schema set + MDL files only — **not** documents/coverage/events/memory; the clone starts fresh with one `project_created` origin entry. No `0012` index migration needed (the partial unique index + fingerprint index cover list perf).
- **Provenance taxonomy** (`schemas.py`): new event type `mdl_project_created` + kind `project_created` (system actor) wired through `SemanticLayerEventType`, `PROVENANCE_EVENT_TYPES`, `ProvenanceKind`, `_PROVENANCE_KIND_BY_EVENT`, `actor_type_for`. Helper `_emit_project_created_provenance` (best-effort).

---

## 5. Copilot scope expansion (P5)

- **Backend ungate** (`app.py::_require_project_ready`): blocks **only** `indexing` (an in-flight onboarding job — editing would race file writes). `empty`/`failed`/`ready` all pass, so the Copilot is usable pre-onboarding. Readiness is advisory.
- **`propose_onboard_table` tool** (`copilot/tools.py`) — one-call onboarding: generates a base model from the **typed** `SchemaIndex` (`SchemaIndex.columns_for` + `column_type`) for a `(table, schema?)` and stages it via `write_mdl_file`. **R1-safe**: rejects a table absent from the project's accessible schemas (never invents). `_safe_model_name` helper. (Note: the live `from_agent_context` index has types; the names-only snapshot/outage path would produce type-less columns that fail validation — acceptable degradation.)
- **Frontend ungate** (`CopilotPanel.tsx`): gates on `isBootstrapping = readinessStatus==='indexing'` (not `isReady`). Empty/failed projects open the chat with a `copilot-onboard-banner` preserving the one-click whole-schema onboard; the agent can also onboard from a doc.
- **F4 cross-schema BI-doc flow works today**: add the schemas to the project (shipped `SchemaSetControl`) → upload BI doc → Copilot `search_documents` + `propose_onboard_table`/`write_mdl_file` onboards the named tables per schema + relationships → user accepts the changeset. R1 validation rejects models referencing unproven schemas.

---

## 6. Provenance attribution under sharing (DP10)

- **`ProvenanceEntry.is_self`** (`schemas.py`) — computed in `get_project_provenance` (the endpoint knows the requester: `is_self = entry.actor == identity.owner_id`).
- **`coalesce_user_runs`** now **splits on actor change** — two users' contiguous edits no longer merge into one mis-attributed "Edited N times".
- **Frontend** (`MdlProvenanceDialog.tsx`): renders "You" only when `is_self`; otherwise the actor's id (no display-name service — see §9). `project_created` kind labelled "Created project".

---

## 7. Frontend (P4 mount)

- **`ProjectBrowser.tsx`** (+ `.test.tsx`, 8 tests) — pure presentational: groups by database, search, per-row Open + actions menu (Duplicate/Rename/Delete; Rename/Delete disabled for `read`), New button. **Bug fixed**: Row *and* RowBody both bound `onClick` → a click fired `onOpen` twice (double-load); now bound once on RowBody.
- **Mount** (`SemanticLayerEditor/index.tsx`): rendered in the `BrowserPane`. Additive, low-risk integration — the scope-resolve `refresh` is **untouched**; project switching uses `openProject(target)` + `selectedProjectIdRef`. State: `projects` (tolerant `listSemanticProjects` load, re-armed by `projectsReloadSignal`), `browserProjects` mapping, handlers `handleCreateProject`/`handleDuplicateProject`/`handleDeleteProject`/`handleRenameSubmit` + a rename `ConfirmModal` + `Input`.
- **API clients** (`AiAgentPanel/api.ts`): `getSemanticProject`, `createSemanticProject`, `renameSemanticProject`, `duplicateSemanticProject`, `deleteSemanticProject`; types `SemanticProject.slug`, `ProvenanceKind` `+project_created`, `ProvenanceEntry.is_self`.

---

## 8. Tests (all green: backend 896 passed/11 skipped, single alembic head `0011`; frontend 168 SemanticLayerEditor)

**Backend** (`tests/unit_tests/superset_ai_agent/`):
- `test_project_identity.py` — slugify, slug uniqueness/suffix, create/rename, per-catalog uniqueness.
- `test_project_slug_migration.py` — 0011 backfill, partial-unique enforcement, downgrade.
- `test_db_access_scoping.py` — two-user parity (provenance/coverage-docs/RAG corpus same across users), cross-project/DB isolation, project-scoped dedup.
- `test_project_duplication.py` — clone copies files+schema set not history; preserves status; independence.
- `test_copilot_api.py` — `test_copilot_runs_on_empty_project`/`…_stream…`, `test_copilot_blocked_while_onboarding_is_indexing` (no-op JobRunner).
- `test_copilot_tools.py` — `propose_onboard_table` generates a valid base model; rejects table outside the schema set.
- `test_provenance_schemas.py` — `test_coalesce_splits_user_runs_by_actor`.
- Updated for the new model: `test_semantic_layer_access.py`, `test_semantic_layer_projects.py`, `test_document_chunk_store.py`, `test_copilot_document_tools.py`, `test_purge_legacy_mdl.py`.

**Frontend** (`SemanticLayerEditor/`): `ProjectBrowser.test.tsx`; `index.test.tsx` (`browses the database projects and opens a second one`, + updated empty→chat+banner tests); `CopilotPanel.test.tsx` (empty→chat+banner); `MdlProvenanceDialog.test.tsx`.

---

## 9. Residual gaps / non-goals (honest)

> Follow-up round (FP1–FP4) closed several of the originally-listed gaps. See
> `MDL_LAB_GAP_CLOSURE.md` for the full plan + per-item status. Current state:

**Closed in the FP1–FP4 round:**
- **F4 relationship enrichment** — `propose_relationships` + plural `propose_onboard_tables` added to the Copilot
  toolset (R1-safe: unknown tables/models rejected per-item); the cross-schema BI-doc worked example now lands as
  one reviewable changeset. (`copilot/tools.py`; `test_copilot_tools.py`.)
- **Actor display name** — `ProvenanceEntry.actor_name` captured at write time (`_emit_mdl_provenance` stamps
  `identity.username/email`); the dialog shows the author's name for a teammate's edit, "You" only when `is_self`.
  Historical/system entries fall back to the id. (`app.py`, `schemas.py`, `MdlProvenanceDialog.tsx`.)
- **Instructions personal-scope made explicit** — DP-NEW resolved: instructions stay personal (like NL→SQL
  memory); `InstructionsPanel` now shows a clear info note that they are personal and not shared with other
  DB-authorized users. (`InstructionsPanel.tsx`; spec §5.4a.)
- **Multi-schema coverage test** — `build_mdl_facts` proven to audit models/columns/relationships across ≥2
  schemas (`test_copilot_coverage.py`).
- **First-class entry contract** — `SemanticLayerEditor` accepts an optional `projectId` and loads-by-id (the Lab
  route's unlock); additive, legacy entry unchanged. (`SemanticLayerEditor/index.tsx`; `index.test.tsx`.)

**Closed in the second follow-up round (FP4-remainder + FP5/DP6):**
- **First-class MDL Lab destination** — an "Open MDL Lab" action on the SQL Lab database tree toolbar opens a
  browse-first, DB-scoped semantic tab (no schema needed); `openMdlLab`/`buildMdlLabId`,
  `SemanticLayerEditorTab.schemaName` now optional + `projectId` for deep-linked entry. The schema-tree action is
  relabelled "Open in MDL Lab" (deep-links a schema's project). Create requires a schema, so it's guarded with a
  clear message in browse-first mode (open/duplicate/rename/delete are DB-wide).
- **ProjectBrowser pagination** — 50/page + "Show more" (bounds the DOM).
- **DP6 include-documents duplication** — `duplicate_documents` (both stores) + endpoint re-embed + a duplicate
  confirm modal with an "Also copy uploaded documents" toggle.

**Still open (deferred, with entry points in `MDL_LAB_GAP_CLOSURE.md`):**
1. **Manual/visual QA** — the MDL Lab tab + duplicate modal are unit/integration-tested with mocked network, not
   observed in a running app.
2. **Top-nav route outside SQL Lab** — the destination lives inside SQL Lab (matches the DB-scoped model); a
   separate global app route is a non-goal for now.
3. **react-arborist virtualization** — pagination suffices at realistic project counts.
4. **Async re-embed for very large include-documents clones** — synchronous best-effort today (vectors are an
   accelerator; failure degrades to keyword recall, never fails the clone).
5. **Duplication is compensating-delete, not transactional** — safe against orphans; a two-phase clone is the
   only full fix.
6. **`propose_onboard_table(s)` on the names-only snapshot path** produces type-less columns that fail validation
   (live path has types) — acceptable degradation, surfaced as a validation error.

---

## 10. Ops / migration notes

- **`0011` runs against the persistent `ai_agent.db` volume** (see `ai-agent-docker-deploy-gotchas`). Expand/contract per step; test against a copy of a real volume before deploy. Single alembic head must hold (`alembic heads` → `0011_project_slug_identity`).
- The P2 owner→project re-scope is a **read-path filter change** (rows already carry `project_id`), reversible by reverting the store methods — no data migration.
- Acceptance gate for the F5/provenance/coverage/RAG re-scope: **two DB-authorized users see the same timeline, the same coverage doc set, and retrieve over the same BI-doc corpus** (covered by `test_db_access_scoping.py`).
- Pre-existing, not owned here: `app.py` `max_steps` line E501; some `test_provenance_schemas.py` PT018/E501 predating this work; two `AiAgentPanel` SQL-render Jest failures from the multi-schema feature.
