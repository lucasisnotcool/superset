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

# Feature Spec: MDL Lab — First-Class Semantic Projects

**Status:** Draft for review
**Scope:** `superset_ai_agent/` (FastAPI) + `superset-frontend/src/SqlLab/...` (and a new MDL Lab surface)
**Builds on:** `plan_multi_schema_mdl_spec.md` (multi-schema projects, shipped). This spec promotes the multi-schema project from a schema-scoped editor panel into a **first-class object with its own Lab surface**, opens the Copilot's scope to drive onboarding, and replaces ownership with database-access scoping.

> Line anchors captured against `master` at authoring time; symbols are stable, lines drift.

---

## 1. Context & problem

Today the semantic layer ("MDL") is a **second-class** capability bolted onto SQL Lab's schema tree:

- It is reached only by right-clicking a **schema** in the SQL Lab left tree, which opens a tab keyed by `(databaseId, catalogName, schemaName)` ([TabbedSqlEditors/index.tsx:260-281](superset-frontend/src/SqlLab/components/TabbedSqlEditors/index.tsx#L260-L281), [actions/sqlLab.ts:881-903](superset-frontend/src/SqlLab/actions/sqlLab.ts#L881-L903)). There is **no way to browse projects** as objects — `listSemanticProjects` is only used to look up the one project for the current schema ([AiAgentPanel/index.tsx:758-763](superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx#L758-L763)).
- The **Copilot is hard-gated behind onboarding**: `isReady = readinessStatus === 'ready'` hides the entire chat until active models exist ([CopilotPanel.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx), backend 409 gate [app.py:1705-1718](superset_ai_agent/app.py#L1705-L1718)). The agent cannot help you *get* onboarded; you must onboard a whole schema manually first.
- **Access is ownership-based**: the project creator becomes `admin`; everyone else with DB access gets `read` (or `write` behind a flag) ([projects.py](superset_ai_agent/semantic_layer/projects.py) `_with_permission`/`_is_visible`, [access.py](superset_ai_agent/semantic_layer/access.py) `_project_with_permission`). This conflicts with the intended model where a project belongs to a **database**, not a person.
- **Project identity is `(fingerprint, catalog, schema, deleted_at)` unique** ([persistence/models.py:216-228](superset_ai_agent/persistence/models.py#L216-L228)), so a database can hold exactly one project per schema and a project **cannot be duplicated** within the same scope.

The five asks below turn MDL into a first-class, governable, agent-operable workspace.

---

## 2. Goals (the five asks) / non-goals

**Goals**
- **F1 — First-class type + MDL Lab.** MDL projects are a top-level object with a dedicated **MDL Lab** surface (browse, open, create, duplicate, delete), not a hidden schema action.
- **F2 — Project browser.** A left-side panel lists the user's accessible projects; opening one reveals its subdirectory/file structure (the workspace tree).
- **F3 — Robust, performant, duplicable projects.** Projects are structurally first-class (stable identity, named), list/open fast at scale, and support **duplication**.
- **F4 — Copilot drives onboarding (human-in-the-loop).** Remove the post-onboarding gate. The Copilot can decide to onboard **individual tables across schemas** as reviewable proposals — e.g. ingest a BI doc describing cross-schema joins → onboard the named tables from each schema → enrich with the relationships.
- **F5 — Database-scoped access (no ownership).** A project covers **one database** (already true; may span its schemas). Access is granted by **valid Superset access to that database** — users do not own projects; anyone with the right DB access sees/edits the projects registered under it.

**Non-goals (this iteration)**
- Cross-**database** projects (still one DB per project — F5 reaffirms the multi-schema spec's non-goal).
- A project marketplace / cross-tenant sharing beyond DB-access scoping.
- Replacing SQL Lab; MDL Lab is a sibling surface, and the schema-tree shortcut into a project is kept.
- Real-time multi-user collaborative editing (concurrent edit conflict resolution is out of scope; last-write-wins per file stays).

---

## 3. Dependency note — the deferred Phase 4 is now load-bearing

`plan_multi_schema_mdl_spec.md` §7a deferred the **unique-index swap / named-projects key (D2b)** as "not needed for multi-schema." **F1/F3/F5 now require it:**

- **Duplication** (F3) cannot create a second project on the same `(fingerprint, catalog, schema)` while the source is active — the unique constraint forbids it.
- **First-class named projects** (F1) need identity independent of schema (a DB can hold many projects; a project may not even map 1:1 to a schema after the Copilot onboards a custom slice).
- **DB-access scoping** (F5) wants identity = `(database)` + a name/slug, with ownership removed from the key.

So this spec **subsumes and executes** the deferred contract change as its foundation (§5.1).

---

## 4. Design options & recommendations (per area)

### F1/F2 — Surface & navigation

| Option | Description | Verdict |
|---|---|---|
| **A. Dedicated MDL Lab route + project browser (RECOMMENDED)** | A first-class surface (e.g. `/sqllab/mdl` or a top-nav "MDL Lab") with a left **project browser** and the existing 3-pane editor as the detail view. Tabs keyed by **`projectId`**. Keep a "Open in MDL Lab" shortcut on the schema tree. | Matches dbt Cloud IDE / Cube Cloud / LookML project surfaces; makes projects browsable and linkable; reuses the editor. |
| B. Stay in SQL Lab, add a "Projects" section to the existing left bar | Cheaper, but conflates SQL editing with modeling and can't grow (no project-level routes/links). | Rejected — keeps MDL second-class. |
| C. Standalone CRUD list page only (no integrated editor) | A FAB-style list page; opening bounces to the schema-keyed editor. | Rejected — doesn't give project-keyed editing or a real Lab. |

**Recommendation: A.** A `projectId`-keyed editor + a project browser panel. The editor already resolves a project and renders a workspace tree ([WorkspaceTree.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/WorkspaceTree.tsx)); we change its **entry contract** from `(db,catalog,schema)` to `projectId` and add the browser + project CRUD around it.

### F4 — Copilot scope

| Option | Description | Verdict |
|---|---|---|
| **A. Onboarding-as-a-Copilot-tool, human-in-the-loop (RECOMMENDED)** | Drop the readiness gate. The Copilot is always available. Onboarding individual tables becomes a **tool** the agent calls, producing a reviewable **changeset** (the existing propose→apply contract) the user accepts. Cross-schema enrichment uses the same loop. | Matches WrenAI's `wren-onboarding` agent skill + HITL; reuses the existing changeset/apply machinery and the R1 access invariant. |
| B. Keep the gate but add a "guided onboarding" wizard | Less disruptive but doesn't satisfy "onboarding is a Copilot decision"; the agent still can't act on a BI doc. | Rejected — doesn't meet F4. |

**Recommendation: A.** Reuse the existing changeset review/apply path so every agent-driven onboard/enrich stays human-approved and access-checked.

### F5 — Access model

| Option | Description | Verdict |
|---|---|---|
| **A. Pure DB-access scoping; `owner_id` → `created_by` audit only (RECOMMENDED)** | Permission is derived from proven Superset access to the project's database: FULL DB access → write; PARTIAL → read; no per-user admin. Destructive ops (delete/duplicate) require write-level DB access. Optional grant table retained for future fine-grained overrides but unused by default. | Matches the industry "governance derived from the data platform" norm; matches the user's explicit intent. |
| B. Keep ownership, add DB-access as a parallel grant | The current hybrid; simplest but contradicts F5. | Rejected. |

**Recommendation: A**, with the security guardrail that data sub-objects (documents, events, instructions) become **project-scoped** (§5.4) so any DB-authorized user sees the project's full context — never wider than the DB boundary.

---

## 5. Feature specification

### 5.1 Project identity & data model (foundation — executes deferred Phase 4)

- **Identity:** a project is `id` (UUID, already the PK) + `(database_uri_fingerprint, catalog_name)` + a **`slug`** (kebab of the name, unique within the DB/catalog). Replace the unique constraint `(fingerprint, catalog, schema_name, deleted_at)` → **`(fingerprint, catalog, slug, deleted_at)`** (DP1).
- `schema_name` stays as the **primary schema** (back-compat + wren-core namespace); it leaves the identity key. The schema set already lives in `ai_agent_semantic_project_schemas`.
- **Name/slug:** `name` becomes user-editable and defaults to the DB/schema-derived label; `slug` is derived and collision-suffixed (`sales`, `sales-2`). Migration backfills `slug` from existing names.
- **`created_by`** replaces `owner_id`'s *meaning* (keep the column, stop using it for authz — §5.4). `visibility` collapses to a single effective mode (`db_access`); `private`/`custom` retained as read-compat enum values only.
- **Resolve vs. create split:** the schema-tree shortcut keeps "resolve-or-create the default project for this schema"; the Lab adds explicit **create** (named, empty or onboarded) and **duplicate**. Resolve now matches by slug/membership, not by a schema-unique row.

### 5.2 MDL Lab surface (F1) & project browser (F2)

- **New surface** reachable as a first-class destination (route + entry points): a left **ProjectBrowser** panel + the existing editor as detail. Tabs key on `projectId`.
- **ProjectBrowser**: lists projects the user can access (§5.4), grouped by database → (optionally) catalog. Each row: name, primary schema + schema-count chip, readiness badge, coverage badge, last-updated. Actions: Open, Duplicate, Rename, Delete, New project. Mirror the existing virtualized tree pattern ([TableExploreTree](superset-frontend/src/SqlLab/components/TableExploreTree/index.tsx), `react-arborist`) for performance.
- **Open project → structure view:** the workspace tree (`treeFromFiles`) already renders the subdir structure (`models/`, `views/`, `raw/` documents, etc.) — it becomes the project's detail tree, now reached by `projectId` rather than schema.
- **Editor entry contract change:** `SemanticLayerEditorProps` gains a `projectId` path; `(db,catalog,schema)` becomes optional bootstrap for the legacy shortcut. Resolve is replaced by **load-by-id** when `projectId` is present ([index.tsx:367-372](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L367-L372)).
- **Listing API:** `GET /agent/semantic-layer/projects` already exists (schema-filtered); add an unfiltered, **DB-access-scoped** list (all projects under databases the user can access), paginated and indexed.

### 5.3 Robustness, performance, duplication (F3)

- **Duplication endpoint:** `POST /agent/semantic-layer/projects/{id}/duplicate` → new project (new UUID + slug). **Deep-copies** the structural core in one transaction: project row, schema memberships (`AiAgentSemanticProjectSchema`), and all **MDL files** (`AiAgentSemanticMdlFile`, new file ids, same paths/content/status). **Does not copy** by default: documents+chunks+vectors, coverage runs, events, NL→SQL memory, schema snapshot, jobs (DP6) — the clone starts with a fresh history and recomputes coverage/snapshot. Offer an opt-in **"include documents"** that re-keys documents+chunks and **re-embeds** (vectors are scope/project-keyed).
- **Performance:** index the project-list query (`database_uri_fingerprint`, `slug`); paginate the browser; keep duplication **transactional** for the structural copy and push the optional document re-embed to an **async job** (reuse the onboarding job runner) so large clones don't block the request.
- **Structural robustness:** adopt the unused `current_version_id` as an optional **named-snapshot** anchor later (out of scope to fully build now, but the duplication transaction is the seam). Bulk file operations (copy-all) get a store-level helper rather than N round-trips.

### 5.4 Database-scoped access (F5)

- **Permission derivation:** drop `owner_id`-grants-admin. In `_project_with_permission`/`_with_permission`, permission = function of the **proven DB access level** for the project's database: `FULL → write`, `PARTIAL → read`; no `admin` tier (DP2). `update`/`delete`/`duplicate` require `write`.
- **Visibility:** every project under a database is visible to anyone who proves access to that database. `_is_visible` drops the `owner_id == owner_id` branch; visibility is purely DB-access. (The R1 multi-schema access proof already establishes per-schema DB access — reuse it.)
- **Re-scope owner-filtered sub-objects to the project (security-critical, DP3):** documents, document chunks, events, and instructions are currently filtered by `owner_id` ([sqlalchemy_store.py], [instructions.py]). For a shared project these must be filtered by **`project_id`** so every DB-authorized user sees the project's full context. The project is DB-fingerprint-bound, so project-scoping never widens beyond the DB boundary. **NL→SQL memory stays `owner_id`-scoped** (personal learning, not project context) — DP3.
- **Authorization stays at the project boundary:** all sub-object access continues to require first proving project (→ DB) access; only the *intra-project* filter changes from owner to project.

### 5.5 Copilot scope expansion (F4)

- **Remove the readiness gate** in the UI (`isReady` no longer hides the chat) and the backend 409 ([app.py:1705-1718](superset_ai_agent/app.py#L1705-L1718)). An empty project opens straight into a usable Copilot with onboarding affordances.
- **Onboarding becomes Copilot tools**, producing reviewable changesets through the existing propose→apply path:
  - `propose_onboard_tables(schema, tables[])` — generate base models for specific tables in a (proven) schema; for tables in a schema not yet in the project's set, the proposal **includes the schema-add** (which re-proves access — R1).
  - `propose_relationships(...)` — enrich onboarded models with cross-schema joins.
- **BI-doc flow (the worked example):** user uploads a BI doc → Copilot `search_documents` extracts the named tables + joins → emits a changeset that (1) adds the needed schemas, (2) onboards exactly those tables from each schema, (3) adds the relationships — all as **one reviewable diff** the user accepts. Every generated model still passes the **R1 schema-in-set + schema-aware validation** invariants from the multi-schema spec, so the agent cannot onboard a table in a schema the user can't access.
- **Readiness becomes advisory** (a badge), not a gate: it still distinguishes empty/indexing/ready for display and for the materialization pipeline, but no longer blocks the agent.

---

### 5.6 Provenance & coverage (must stay within existing patterns)

Provenance and coverage already exist and are well-established (`MDL_PROVENANCE_AND_COVERAGE.md`): provenance is an append-only `SemanticLayerEvent` timeline projected to `ProvenanceEntry`; coverage is a background `CoverageRun` auditing the **union of the project's documents** against the active MDL. Both are already **project-keyed** (`project_id`). The MDL Lab changes do not redesign them — they must (a) make them correctly **project-level under sharing**, and (b) cover the new flows. Requirement restated: **provenance applies at the project level; coverage refers to the BI docs stored within that project.**

**Verified gaps the new model must close:**
- `list_project_events` filters by **`owner_id` AND `project_id`** (`sqlalchemy_store.py`), so a second DB-authorized user opening a shared project would see a **partial/empty** timeline.
- `list_project_documents` filters by **`owner_id` AND `project_id`**, so coverage's `_coverage_documents(project, owner)` would audit **only the current user's** docs, not all the project's BI docs.
- Coverage **run reads** (`latest_complete`/`active_run`) are already project-only ✓, but the idempotency key `find_complete(..., owner_id, ...)` includes `owner_id`, so two users could trigger **duplicate background runs** for the same active set.

**Specification (folds into F5/DP3 re-scoping):**
1. **Provenance is project-scoped (F5).** Drop the `owner_id` filter from `list_project_events`/`get_project_provenance`; read by `project_id` only. Any user with the project's DB access sees the **whole** timeline. The per-event `actor` (the user who made the change) is retained on the row, so a shared project's history correctly shows *who* did *what*. (Same DB-boundary safety as DP3: events carry `project_id`, and the project is bound to one `database_uri_fingerprint`.)
2. **Multi-user attribution display (CONFIRMED).** Resolve each entry's `actor` (`owner_id`) to a display name; render **"You" only when the actor is the viewer**, otherwise the other user's name. `actor_type` (user/agent/system) stays as the secondary tag. This needs an actor-identity resolution step (owner_id → name) in the provenance projection/API — today's bare "You" tag would mislabel another user's edit. *(Frontend `MdlProvenanceDialog.tsx` + a name on `ProvenanceEntry`.)*
3. **Coverage refers to the project's BI docs (F5).** Drop the `owner_id` filter from `list_project_documents` so `_coverage_documents` audits **every** BI doc in the project regardless of uploader (the literal ask). Make the coverage **idempotency key project-scoped** (drop `owner_id` from `find_complete`/the run's dedup) so one active-set version yields one background run, not one-per-user.
4. **Agent-driven onboarding provenance (F4, CONFIRMED).** Onboarding tables from a BI doc via the Copilot flows through the **existing** `_emit_agent_apply_provenance` path: doc-grounded → `enrichment` (actor=agent, with the BI-doc chips already rendered); non-doc agent edits → `copilot_edit`. The **system `onboarding`** kind is retained for the whole-schema onboarding *job*. No new provenance taxonomy — reuses `apply_provenance_payload` and the changeset's `referenced_document_ids`. Activation of the agent-created models still triggers `_schedule_coverage`, so coverage picks up the BI doc automatically.
5. **Duplication & history (F3/DP6, CONFIRMED).** A duplicated project starts with a **fresh** provenance timeline and **no** coverage runs (recomputed once its MDL is activated). Emit exactly **one** system provenance entry on the clone — `project_created` with `detail = {duplicated_from: <source_project_id>, source_name}` — so lineage is recorded without falsely importing the source's audit log. Coverage recomputes from scratch (checksums differ after file re-key); the optional "include documents" re-embed (DP6) makes those docs available to the first coverage run.
6. **No structural redesign.** The event table, `CoverageRun` store, supersession/`claim()` CAS, SSE stream, badge, and provenance dialog are unchanged in shape — only the **read-scope filters** (owner→project) and two additive surfaces (actor name, `project_created` entry) change. This keeps the feature within existing dev expectations and the `MDL_PROVENANCE_AND_COVERAGE.md` contract.
7. **Close the known multi-schema coverage gap.** `MDL_PROVENANCE_AND_COVERAGE.md` §7.7 flags multi-schema coverage as untested; `_active_mdl_checksum`/`_coverage_documents` are schema-agnostic, but add a targeted multi-schema coverage test now that projects routinely span schemas.

### 5.7 BI-document RAG (must stay project-level)

Uploaded BI documents are ingested through a unified pipeline (persist → dedup → extract → vectorize), specified in `plan_unified_attach_ingestion_spec.md` / `plan_attach_tree_gate_json_followups.md` (both **implemented**). The Copilot retrieves over them via `search_documents`. Requirement: **RAG applies at the project level** — every user with the project's DB access retrieves over the project's full BI-doc corpus.

**Source-verified current scoping (claims checked against code, not the docs):**
- **Vector store is already project-scoped, no owner in the key:** `document_scope_key(project_id) → "doc:{project_id}"` ([document_retriever.py:59-71](superset_ai_agent/semantic_layer/document_retriever.py#L59-L71)). Vectors for every uploader's chunks live under the one project key. ✅
- **But the retrieval *corpus* is owner-filtered:** the toolset feeds `retrieve()` the candidate set `list_project_chunks(project_id, owner_id=self._owner_id)` ([copilot/tools.py:374](superset_ai_agent/semantic_layer/copilot/tools.py#L374)), and `retrieve()` **intersects** vector hits back to that set (`ordered = [by_id[id] for id in ids if id in by_id]`, [document_retriever.py:157-158](superset_ai_agent/semantic_layer/document_retriever.py#L157-L158)). So a project-wide vector hit on **user A's** doc is dropped when **user B** searches. The keyword fallback (`keyword_rank_chunks(query, chunks, k)`) ranks the same owner-filtered corpus. **Net: retrieval is effectively owner-scoped today.**
- **`list_project_documents`** (the agent's `list_documents` tool and coverage's doc set) is owner-filtered too ([sqlalchemy_store.py](superset_ai_agent/semantic_layer/sqlalchemy_store.py)).
- **Dedup is per-project *and* owner-isolated:** `find_document_by_checksum(project_id, checksum, owner_id)` — verified, matching the ingestion spec's BE-2 claim.

**Specification (folds into the F5/DP3 owner→project re-scope — no new mechanism):**
1. **Project-level retrieval corpus.** In the Copilot toolset, query the document/chunk corpus **project-scoped** (drop the `owner_id` filter on `list_project_chunks`/`list_project_documents` for retrieval). Because the vector cache is *already* keyed `doc:{project_id}`, this is a **pure read-filter change with zero re-embedding** — existing vectors become retrievable project-wide immediately. (Same change class as provenance/coverage §5.6.)
2. **Project-scoped dedup (CONFIRMED).** Make `find_document_by_checksum` project-scoped (drop `owner_id`). Re-uploading bytes already in the project — by *any* user — dedups to the one existing document/chunks/vectors and surfaces the existing "reusing `<file>`" notice. One copy of each BI doc's vectors per project; the deduped row keeps its original `created_by`. Keeps the corpus a clean project-level set and avoids double embedding.
3. **Duplication (F3/DP6) → re-embed.** Because the vector key is `doc:{project_id}`, a cloned project (new id → new `doc:{newId}` key) needs its copied chunks **re-embedded** under the new scope key — already specified as the async step of the "include documents" opt-in. Structure-only clones carry no docs/vectors (fresh).
4. **No structural redesign.** The ingestion pipeline, `DocumentChunkIndex`/LanceDB cache, `scope_key` scheme, status polling, and dedup choke-point (`register_document`) are unchanged in shape — only the **corpus read-scope** (owner→project) and the **dedup key** (owner→project) change. RAG then sits exactly within the project-level pattern the rest of the system now uses.
5. **DB-boundary safety (R1-consistent).** The project key is bound to one `database_uri_fingerprint`; project-scoping the corpus never widens retrieval beyond that database. Personal NL→SQL memory stays owner-scoped (it is not document RAG).

## 6. Decision points

> **Confirmed with the requester (locked):** **DP2** → two-tier read/write derived from DB access, no ownership-admin. **DP1** → user-named projects with server-derived unique slugs (rename supported from v1). **DP4** → dedicated MDL Lab surface (own route/nav) + retain the schema-tree entry as a deep-link shortcut. **Provenance & coverage (§5.6):** **DP8** duplication → fresh history + one `project_created`/`duplicated_from` origin entry; **DP9** agent onboarding → reuse `enrichment`/`copilot_edit` (no new kind); **DP10** multi-user attribution → resolve actor identity, "You" only for self. **BI-doc RAG (§5.7): DP11** → project-scoped dedup (drop `owner_id`) + project-scoped retrieval corpus, reusing the same owner→project re-scope (vectors already keyed `doc:{project_id}` → zero re-embed). DP3/DP5/DP6/DP7 stand at their recommended values unless revisited.


| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **DP1** | Project identity key (replacing the schema-unique constraint) | (a) `(fingerprint, catalog, slug)`; (b) pure UUID, no natural-key uniqueness; (c) keep schema in key | **(a)**. A human-meaningful unique slug per DB/catalog enables named + duplicate projects, keeps resolve deterministic, and matches dbt/LookML project naming. Pure UUID (b) loses "no two projects named X in this DB"; (c) blocks duplication. |
| **DP2** | Permission tiers under DB-access scoping | (a) two tiers: read (PARTIAL) / write (FULL); (b) keep a per-project admin via grants | **(a)** as default; retain the existing `AiAgentSemanticProjectGrant` table dormant for a future fine-grained override. No ownership-admin — matches F5. Destructive ops need write. |
| **DP3** | Re-scoping owner-filtered sub-objects | (a) documents+events+instructions → project-scoped, memory stays owner-scoped; (b) everything project-scoped; (c) leave owner-scoped | **(a)**. Project context (docs/events/instructions) must be shared across DB-authorized users; personal NL→SQL memory should not leak between users sharing a project. (c) breaks F5 (a second user sees an empty project). |
| **DP4** | MDL Lab placement | (a) dedicated surface/route + browser; (b) section in SQL Lab left bar; (c) modal list | **(a)**. First-class requires a first-class home; keep the schema-tree shortcut as a convenience entry. |
| **DP5** | Copilot ungating mechanism | (a) onboarding-as-tool + HITL changeset; (b) guided wizard behind a softened gate | **(a)**. Reuses the changeset/apply + R1 access invariants; satisfies "onboarding is a Copilot decision." |
| **DP6** | Duplication depth | (a) structural (project+memberships+MDL files), docs/coverage/events fresh; (b) full deep copy incl. docs+vectors; (c) shallow (reference shared files) | **(a)** default + opt-in "include documents" (async re-embed). (b) is expensive and usually unwanted; (c) is unsafe (shared mutable files across projects). |
| **DP7** | Empty-project default on create | (a) empty + Copilot-driven onboarding; (b) auto-onboard primary schema | **(a)**. F4 makes onboarding a deliberate, reviewable Copilot action; auto-onboard would pre-empt the agent and re-introduce the "whole schema" assumption. |

---

## 7. Risks & mitigations

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| **R1** | **Cross-boundary data exposure** from relaxing `owner_id` filters (DP3): a second user could see another user's documents/instructions, or data could leak across DBs. | **High (security)** | Re-scope to `project_id`, and a project is bound to one `database_uri_fingerprint`; access requires proving DB access first. Add tests: (i) two users with the same DB access see the same project docs; (ii) a user with access to DB-A sees **no** project/doc bound to DB-B; (iii) NL→SQL memory remains per-user. Map to `SECURITY.md`: principal = any role with that DB's access; the matrix row is "data/asset access requires proven access to the underlying database." |
| **R2** | **Migration risk** — identity-key swap + slug backfill + sub-object re-scoping run against the persistent `ai_agent.db` volume (see memory: legacy rows have already broken readiness once). | **High** | Expand/contract: (1) add `slug` (nullable) + backfill; (2) build the new unique index `CONCURRENTLY`/guarded, drop the old; (3) switch reads. Sub-object re-scoping is a **read-path filter change**, not a data migration (rows already carry `project_id`), so it's reversible by config. Test against a copy of a real volume. |
| **R3** | **Copilot ungating lets the agent author models referencing unproven schemas/tables.** | Medium | The R1 invariant from the multi-schema spec already **rejects** any model whose `tableReference.schema` ∉ the project's proven set, and validation rejects unknown tables. Onboarding-as-tool routes every schema-add through the access proof. Add a test: a copilot onboard proposal for an unproven schema is rejected pre-apply. |
| **R4** | **Duplication of large projects** (many files, big document corpus) blocks the request or partially copies on failure. | Medium | Structural copy in one DB transaction (atomic); optional document re-embed as an async job with progress (reuse onboarding job runner). Cap/stream file copy; `log()` if truncated. |
| **R5** | **Slug collisions / rename races** create two "active" projects competing for a slug. | Medium | Unique `(fingerprint, catalog, slug, deleted_at)` enforces it at the DB; generate collision-suffixed slugs server-side; resolve rename via the same uniqueness check with a clear 409. |
| **R6** | **IA disruption** — users accustomed to the schema-tree entry can't find MDL. | Low | Keep the schema-tree "Open in MDL Lab" shortcut (now deep-links to the project in the Lab); add a discoverable Lab entry point. No removal of the existing path. |
| **R7** | **Project-list performance** at scale (many DBs/projects) and N+1 readiness/coverage badge loads. | Low/Medium | Index `(fingerprint, catalog, slug)`; paginate; lazy/batch the badges (readiness+coverage fetched per-row on demand, not eagerly for the whole list). |
| **R8** | **Permission downgrade surprises** — a former "owner" (admin) becomes "write" or "read" under DB-access derivation and loses delete rights. | Low | Document the model; map delete/duplicate to write-level DB access (most former owners have FULL access → write). Provide the grant table as the escape hatch if a stricter control is later needed. |
| **R9** | **Provenance/coverage stay owner-filtered** — if the §5.6 owner→project filter change is missed, a shared project shows a second user a half-empty timeline and coverage that ignores other users' BI docs (silent under-reporting). | Medium | §5.6.1/§5.6.3 are part of the P2 (DB-access) re-scope, not separate; add tests: two DB-authorized users see the **same** provenance timeline and the **same** coverage doc set. Make the coverage idempotency key project-scoped (one run per active-set, not per user). |
| **R10** | **Mis-attribution in shared provenance** — the "You" tag labels another user's edit as the viewer's (R8-adjacent trust/audit issue). | Low/Medium | DP10: resolve `actor`→identity; "You" only when actor == viewer. Test that a second user's edit renders as that user's name, not "You". |
| **R11** | **Cloned project inherits stale coverage/provenance** — copying runs/events would falsely transfer audit history. | Low | DP8: duplication copies **no** events/runs; emits one `project_created` origin entry; coverage recomputes (checksums differ post re-key). Test: a fresh clone's timeline has exactly the origin entry and no coverage until activation. |
| **R12** | **RAG stays owner-scoped** — if the §5.7 corpus re-scope is missed, a shared project's Copilot retrieves only the *viewer's* uploads; user A's BI doc is invisible to user B even though its vectors exist under `doc:{project}` (silent under-grounding; the agent appears to "forget" team docs). | Medium | §5.7.1 is part of the P2 owner→project re-scope. Test: user A uploads a BI doc, user B's `search_documents` retrieves its chunks (project corpus, not owner). Verify keyword-fallback path too. |
| **R13** | **Duplicate vectors / cross-user re-embed** if dedup stays owner-isolated under sharing. | Low | DP11: project-scoped `find_document_by_checksum`. Test: two users upload identical bytes to one project → one document row, one chunk set, one vector set; second upload reports "reusing". |

---

## 8. Intent alignment (dev ↔ spec ↔ user)

| Layer | Stated intent | Spec realization | Verification |
|---|---|---|---|
| **F1/F2 user** | "Browse my MDL projects as first-class objects; open one to see its structure." | MDL Lab surface + ProjectBrowser; project-keyed editor; workspace tree as the structure view. | E2E: open Lab → see project list → open a project → see `models/`/`views/`/`raw/` tree. |
| **F3 dev** | "Projects are robust, performant, duplicable." | UUID+slug identity; indexed/paginated list; transactional structural duplication + async doc re-embed. | Unit: duplicate copies files+memberships, not history; load test the list; duplicate of an N-file project is atomic. |
| **F4 user** | "Upload a BI doc on cross-schema joins; the Copilot onboards those tables from each schema and enriches relationships — I approve." | Ungated Copilot; onboarding/relationship **tools** → one reviewable changeset that adds schemas, onboards named tables, adds joins; R1-validated. | E2E: doc upload → changeset proposes schema-adds + per-table models + relationships → accept → models active, cross-schema join validates. |
| **F5 dev/user** | "Projects are owned by DB access, not users; correct DB creds ⇒ access." | `owner_id`→`created_by`; permission derived from proven DB access; sub-objects project-scoped within the DB boundary. | Unit/security: two users, same DB access → same project + docs; DB-A user cannot see DB-B project; memory stays per-user (R1 tests §7). |
| **P&C user** | "Provenance applies at the project level; coverage refers to the BI docs stored within that project." | §5.6: provenance read project-scoped (owner filter dropped); coverage audits the union of *all* project documents (owner filter dropped); idempotency project-scoped; no structural redesign. | Two-user parity: same timeline, same coverage doc set (R9); clone has only its origin entry (R11); agent BI-doc onboarding shows as `enrichment` with doc chips. |
| **RAG user** | "BI-doc RAG applies at the project level." | §5.7: retrieval corpus project-scoped (owner filter dropped) — vectors already `doc:{project_id}` so zero re-embed; dedup project-scoped; re-embed on duplicate. No structural redesign. | Two-user parity: user A's uploaded BI doc is retrievable by user B's Copilot (R12); identical bytes from two users → one doc/vector set (R13); clone re-embeds under its own key. |

---

## 9. Phasing (independently shippable; each ends green per `CLAUDE.md`)

1. **P1 — Identity foundation (executes deferred Phase 4).** Add `slug`, swap the unique constraint, split resolve/create, keep everything else behaving the same. No UX change yet. *(Backend + migration; expand/contract per R2.)*
2. **P2 — DB-access scoping (F5) + provenance/coverage/RAG re-scope (§5.6, §5.7).** Derive permission from DB access; re-scope documents/events/instructions to `project_id`; `owner_id`→audit. **Provenance: drop owner filter (§5.6.1); coverage: drop owner filter on docs + project-scope the idempotency key (§5.6.3); RAG: project-scope the retrieval corpus + dedup key (§5.7.1–2) — vectors already `doc:{project_id}`, zero re-embed.** Heavily tested for R1/R9/R12/R13 cross-boundary isolation + two-user parity. *(Backend; read-path filter change.)*
3. **P3 — Duplication + project CRUD API (F3).** `duplicate` (fresh history + `project_created`/`duplicated_from` origin entry, §5.6.5), `create` (named/empty), `rename`, `delete`; unfiltered DB-scoped list; indexes. *(Backend.)*
4. **P4 — MDL Lab surface + ProjectBrowser (F1/F2) + provenance attribution (§5.6.2).** New surface, project-keyed editor entry, browser panel, schema-tree deep-link shortcut; actor-identity resolution + "You"-for-self in the provenance dialog. *(Frontend.)*
5. **P5 — Copilot scope expansion (F4).** Remove gate; onboarding/relationship tools; BI-doc → changeset flow (agent onboarding surfaces as `enrichment`/`copilot_edit`, §5.6.4); readiness becomes advisory. Add the multi-schema coverage test (§5.6.7). *(Backend tools + frontend ungate.)*

P1→P2 are foundational and ship first (they are also the highest-risk migrations). P4/P5 are the visible payoff and depend on P1–P3.

---

## 10. Open questions for the user

- **Slug & rename UX (DP1):** are user-chosen project **names** (with server-derived slugs) wanted now, or is an auto-name (`<db>.<schema>`) sufficient for v1 with rename later?
- **Permission tiers (DP2):** is two-tier read/write (no admin) acceptable, or do you want a per-project "owner/maintainer" override via the existing grant table from day one?
- **Duplicate scope (DP6):** should "Duplicate" default to **structure-only** (recommended) or always offer the include-documents toggle prominently?
- **MDL Lab entry point (DP4):** dedicated top-nav destination, or a mode toggle inside SQL Lab? (Affects routing + breadcrumb design.)
- **Legacy schema-tree entry:** keep it as a deep-link into the Lab (recommended), or retire it once the Lab ships?

---

## 11. File touchpoints (per phase)

> Symbols are stable; line numbers drift (re-grep). "★ hot" = a high-contention shared file (see §12). New files are isolation-safe.

### P1 — Identity foundation
| File:symbol | Change |
|---|---|
| `persistence/models.py::AiAgentSemanticProject` ★ | Add `slug` column; swap `__table_args__` unique `(fingerprint, catalog, schema_name, deleted_at)` → `(fingerprint, catalog, slug, deleted_at)`. |
| `persistence/migrations/versions/0011_project_slug_identity.py` (**new**) | Add `slug` (nullable) + backfill from name; build new unique index; drop old. Expand/contract (R2). |
| `semantic_layer/schemas.py::SemanticProject / SemanticProjectResolveRequest` ★ | Add `slug`; `name` user-editable; resolve/create request carries optional `name`. |
| `semantic_layer/projects.py::_project_from_request, resolve, _slugify (new), create (new), rename (new)` ★ | Slug gen + collision suffix; resolve matches by slug/membership not schema-unique. |
| `tests/unit_tests/superset_ai_agent/test_project_identity.py` (**new**) | Slug uniqueness/suffix; resolve-by-slug; migration backfill. |

### P2 — DB-access scoping + provenance/coverage/RAG re-scope
| File:symbol | Change |
|---|---|
| `semantic_layer/access.py::_project_with_permission` ★ | Drop owner→admin; permission = f(DB access level): FULL→write, PARTIAL→read. |
| `semantic_layer/projects.py::_with_permission, _is_visible, update, delete` ★ | Drop owner branches; update/delete gate on `write` not `admin`. |
| `semantic_layer/sqlalchemy_store.py` + `semantic_layer/memory.py` ★ | Drop `owner_id` filter on `list_project_events` (§5.6.1), `list_project_documents` (§5.6.3 + §5.7.1), `list_project_chunks` (§5.7.1); `find_document_by_checksum` project-scoped (§5.7.2). |
| `semantic_layer/coverage_store.py::find_complete` | Drop `owner_id` from the idempotency key (§5.6.3). |
| `semantic_layer/copilot/tools.py::_search_documents, _list_documents` ★ | Corpus calls project-scoped (drop owner) (§5.7.1). |
| `app.py::_coverage_documents, get_project_provenance, permission wiring` ★ | Read paths project-scoped; map `owner_id`→`created_by` audit. |
| `tests/.../test_db_access_scoping.py`, `test_two_user_parity.py` (**new**) | R1/R9/R12/R13: same timeline, same coverage docs, same RAG corpus; cross-DB isolation; project-scoped dedup. |

### P3 — Duplication + project CRUD
| File:symbol | Change |
|---|---|
| `semantic_layer/projects.py::clone (new), create, rename, delete` ★ | Transactional structural clone (project+memberships); write-gated. |
| `semantic_layer/mdl_files.py::duplicate_files (new)` | Bulk copy MDL files src→dst (new ids, same paths/content). |
| `app.py` ★ | `POST .../projects/{id}/duplicate`, `POST .../projects`, `PATCH/DELETE .../projects/{id}`, unfiltered DB-scoped `GET .../projects`; emit `project_created`/`duplicated_from` (§5.6.5); optional include-docs async re-embed job. |
| `persistence/migrations/versions/0012_project_list_indexes.py` (**new**) | Index `(database_uri_fingerprint, slug)` for list perf (R7). |
| `tests/.../test_project_duplication.py` (**new**) | Copies files+memberships, not history; origin entry only; atomicity. |

### P4 — MDL Lab surface + ProjectBrowser + attribution
| File:symbol | Change |
|---|---|
| `SemanticLayerEditor/ProjectBrowser.tsx` (+ `.test.tsx`) (**new**) | Virtualized project list (react-arborist), grouped by DB; Open/Duplicate/Rename/Delete/New. |
| `AiAgentPanel/api.ts` ★ | `duplicateProject, createProject, renameProject, deleteProject`, unfiltered `listProjects`, `ProvenanceEntry.actor_name`. |
| `SemanticLayerEditor/index.tsx::SemanticLayerEditorProps, refresh` ★ | Accept `projectId` entry → load-by-id path (replaces resolve when present). |
| `actions/sqlLab.ts`, `TabbedSqlEditors/index.tsx`, `TableExploreTree/index.tsx` ★ | Project-keyed tabs; schema-tree "Open in MDL Lab" deep-link; Lab route/nav entry. |
| `SemanticLayerEditor/MdlProvenanceDialog.tsx` | Actor-identity rendering; "You" only when actor==viewer (§5.6.2). |
| `semantic_layer/schemas.py::provenance_from_event`, `app.py` ★ | Add `actor_name` (resolve `owner_id`→display name) to the projection. |

### P5 — Copilot scope expansion
| File:symbol | Change |
|---|---|
| `semantic_layer/copilot/tools.py::propose_onboard_tables (new), propose_relationships (new)` ★ | Onboarding/enrichment as reviewable changeset tools; schema-add routes through access proof (R3). |
| `semantic_layer/copilot/service.py::apply_provenance_payload` | Agent onboarding → `enrichment`/`copilot_edit` (§5.6.4). |
| `app.py` (remove 409 gate ~`:1705-1718`) ★ | Readiness advisory, not a gate. |
| `SemanticLayerEditor/CopilotPanel.tsx::isReady`, `index.tsx` ★ | Remove the chat gate; readiness → badge. |
| `tests/.../test_copilot_onboarding.py`, `test_multi_schema_coverage.py` (**new**) | Agent onboards cross-schema from a BI doc, R1-validated; multi-schema coverage (§5.6.7). |

---

## 12. Parallel execution & multi-agent feasibility

### 12.1 Feasibility verdict (evidence-based)

**Realtime multi-agent editing of the *same* files is NOT safe here; structured parallelism along module/new-file seams IS.** Evidence:

- **`app.py` is a 3,846-line, ~55-endpoint hub** edited by P2, P3, P5 *and* the provenance/coverage/RAG re-scope. Concurrent edits to one 3.8k-line file collide on nearly every hunk. Same hazard, smaller scale, for **`api.ts` (1,892)** and **`index.tsx` (1,287)** on the frontend.
- **Migrations are a strictly linear alembic chain** (0001→0010, each `down_revision` → the single prior). Two agents creating a migration both set `down_revision="0010…"` → **multiple heads → alembic errors at upgrade**. Migrations must be single-owner, serialized.
- **`isolation: "worktree"` does not make same-file edits free** — it defers the conflict to an (expensive) merge. For 3.8k-line hubs, that merge is the bottleneck, not a saving.

So the right model is **"serial spine, parallel leaves"** with a single integration owner — not N agents on the hubs in realtime.

### 12.2 Dependency DAG (what must be sequential)

```
P1 identity ──► P2 access/re-scope ──► P3 CRUD/dup ──► P4 Lab UI
                       │                                  ▲
                       └──────────────► P5 Copilot ───────┘ (P4∥P5 share index.tsx/CopilotPanel)
```
- **P1 → P2** hard: P2's permission/visibility builds on slug identity + the resolve split.
- **P2 → {P3, P4, P5}**: the owner→project re-scope underpins sharing correctness; building UI/CRUD/Copilot on owner-scoped reads would bake in R9/R12 bugs.
- **P3 → P4**: the browser needs the list/CRUD/duplicate APIs.
- **P4 ∥ P5** are *mostly* independent (different concerns) but **both edit `index.tsx` + `CopilotPanel.tsx`** → coordinate or serialize those two files.

### 12.3 Recommended execution model

1. **Phases run in dependency order**, one **phase-owner agent per phase**, each in its **own git worktree** (`isolation: "worktree"`), with an **integration checkpoint** (merge + full test run) between phases. This is the safe default.
2. **Within a phase, fan out the leaves** that touch *only new files*:
   - P4: a **ProjectBrowser agent** (new `ProjectBrowser.tsx`+test) runs parallel to the **wiring owner** (api.ts/index.tsx/routes). They integrate via a thin agreed prop contract.
   - P5: a **tools agent** (new `propose_*` functions) parallel to the **ungate owner** (gate removal in app.py/CopilotPanel).
3. **Always-parallel, write-disjoint agents** (safe to run *realtime* alongside any implementer):
   - a **security-review agent** auditing the R1/R9/R12 isolation invariants (read-only);
   - a **test-authoring agent** writing the two-user-parity / cross-DB-isolation suites (new `test_*` files only);
   - a **doc/verification agent** keeping touchpoint anchors fresh (read-only + this spec).
4. **One migration owner across the whole effort** holds the "migration token": all of `0011`, `0012`, … are authored by it, serially. No other agent creates an alembic file.

### 12.4 Hot-file contention list (never edit concurrently)

`app.py` · `semantic_layer/projects.py` · `semantic_layer/schemas.py` · `persistence/models.py` · any `persistence/migrations/versions/*` · `semantic_layer/copilot/tools.py` · `semantic_layer/sqlalchemy_store.py` · `AiAgentPanel/api.ts` · `SemanticLayerEditor/index.tsx` · `SemanticLayerEditor/CopilotPanel.tsx`.
**Rule:** each hot file has exactly one writer at a time (the phase-owner or integration owner). Leaf agents touch only **new** files.

### 12.5 Task split + go/no-go safety boundaries

| Agent | Owns (writes) | Runs parallel with | **GO** | **NO-GO (hard stop)** |
|---|---|---|---|---|
| **Migration owner** | all `migrations/versions/*` | anyone (it only adds new migration files) | Author 0011/0012 serially; run `alembic upgrade head` on a copy of a real `ai_agent.db` | Any *other* agent creating a migration; >1 alembic head; upgrade fails on the volume copy |
| **P1 identity owner** | models.py, schemas.py, projects.py (identity), 0011 | migration owner (same agent), security/test agents | Slug + key swap behind expand/contract; existing tests stay green | Editing access/permission logic (P2's lane); touching api.ts/index.tsx |
| **P2 access/re-scope owner** | access.py, projects.py (perms), stores, coverage_store, copilot/tools (corpus), app.py (reads) | security-review, test-authoring | Owner→project read-filter change; two-user parity + cross-DB isolation tests green | Merging if any isolation test (R1/R9/R12/R13) fails; widening any read beyond `database_uri_fingerprint` |
| **P3 CRUD/dup owner** | projects.py (clone/CRUD), mdl_files.py, app.py (endpoints), 0012 | P4 ProjectBrowser agent (contract-only) | Transactional structural clone; origin-entry-only history | Copying events/coverage into a clone (DP8); same-scope clone without distinct slug (R5) |
| **P4 wiring owner** | api.ts, index.tsx, sqlLab actions/tabs/tree, MdlProvenanceDialog, schemas.py (`actor_name`) | **ProjectBrowser agent** (new files) | Project-keyed entry + browser mount + attribution UI | Two agents editing index.tsx; removing the schema-tree shortcut (R6) |
| **P4 ProjectBrowser agent** | `ProjectBrowser.tsx`(+test) **only** | P4 wiring owner | New component against the agreed props; its own tests | Editing index.tsx/api.ts (wiring owner's lane) |
| **P5 Copilot owner** | copilot/tools.py, copilot/service.py, app.py (gate), CopilotPanel.tsx, index.tsx (badge) | **tools agent**, test agent | Onboarding-as-tool + ungate; R1 validation still rejects unproven schemas | Letting an onboard proposal bypass the access proof (R3); editing index.tsx concurrently with P4 |
| **Security-review agent** | *nothing* (read-only) | everyone | Audit R1/R9/R12 isolation, the access-proof invariant, `SECURITY.md` mapping | Any source edit |
| **Test-authoring agent** | new `test_*` files **only** | everyone | Two-user parity, cross-DB isolation, dedup, clone-history suites | Editing non-test source |

### 12.6 Global merge gates (no-go to advance)

A phase may merge / the next phase may start **only when**: (1) `pytest tests/unit_tests/superset_ai_agent/` green; (2) touched-suite Jest green; (3) `pre-commit run` (ruff/prettier/mypy) clean on staged files; (4) **single alembic head** (`alembic heads` returns one); (5) the **two-user parity + cross-DB isolation tests pass** (the security acceptance gate for P2 and anything built on it). Any red = no-go; fix in the owning worktree before integrating.

**Bottom line:** parallelism here is real but bounded — best as *dependency-ordered phase-owners in worktrees* with *read-disjoint review/test agents running realtime alongside*, a *single migration owner*, and *one writer per hot file*. The free lunch is the leaves (new components/modules/tests) and the always-safe read-only review/test agents; the hubs (`app.py`, `api.ts`, `index.tsx`, migrations) stay serial.

---

### Sources
- [WrenAI — project artifacts, agent skills (`wren-onboarding`, `wren-generate-mdl`), human-in-the-loop](https://github.com/Canner/WrenAI)
- [WrenAI 2025 review — HITL + knowledge/feedback loops](https://medium.com/wrenai/wren-ai-2025-year-in-review-from-open-source-to-agentic-bi-in-production-0d1a974d95c7)
- [Opening up the Looker semantic layer — platform-governed access](https://cloud.google.com/blog/products/business-intelligence/opening-up-the-looker-semantic-layer)
- [Cube — universal semantic layer governance applied centrally](https://cube.dev/blog/how-cubes-universal-semantic-layer-and-google-cloud-platform-gcp-work)
- [Semantic Layers 2025 playbook — governance enforced at the layer](https://coalesce.io/data-insights/semantic-layers-2025-catalog-owner-data-leader-playbook/)
