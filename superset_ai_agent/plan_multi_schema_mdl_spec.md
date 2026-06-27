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

# Feature Spec: Multi-Schema MDL Semantic Projects

**Status:** Draft for review
**Scope:** `superset_ai_agent/` (FastAPI / semantic layer) + `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/`
**Author intent:** Lift the "one MDL project = one physical schema" constraint so a single semantic project can model tables drawn from **multiple schemas** of the same database, with correct authoring, validation, onboarding, access control, and UI.

> Line numbers are anchors captured against branch `master` at authoring time. Symbols are stable; re-grep if a line has drifted.

---

## 1. Problem statement

MDL (**Modeling Definition** — wren-core's native semantic manifest) projects are currently hard-scoped to a single `(database, catalog, schema)` tuple. A user who wants to model `sales.orders` joined to `crm.customers` cannot do it in one project: the project, onboarding, validation, and UI all assume one schema. Real warehouses spread a single business domain across schemas (staging vs marts, per-team schemas, source vs derived), so the one-schema boundary forces either (a) duplicating/denormalizing tables into one schema, or (b) splitting one logical model across disconnected projects that cannot express cross-schema joins.

### 1.1 The pivotal finding — the engine is *already* multi-schema

The single-schema limit is **an organizational boundary the fork added on top of wren-core, not an engine constraint.** Two independent confirmations:

- **Code.** Every `MdlModel` already carries its own physical location via `MdlTableReference { catalog, schema, table }` ([mdl_schema.py:81-102](superset_ai_agent/semantic_layer/mdl_schema.py#L81-L102)). The manifest's *root* `catalog`/`schema` ([mdl_schema.py:162-163](superset_ai_agent/semantic_layer/mdl_schema.py#L162-L163), [mdl_compile.py:58-59](superset_ai_agent/semantic_layer/mdl_compile.py#L58-L59)) is wren-core's **logical namespace**, not a filter on which physical tables models may reference. `compile_manifest` merges per-model bodies through unchanged ([mdl_compile.py:98-127](superset_ai_agent/semantic_layer/mdl_compile.py#L98-L127)) — nothing collapses or rewrites `tableReference.schema`.
- **WrenAI docs.** *"Catalog and schema … define the Wren Engine namespace — they have nothing to do with your database's catalog or schema. The actual database location of each table is specified per-model in the table_reference section. This architecture allows you to define multiple models across different database schemas while maintaining a unified semantic layer."* ([What is MDL](https://docs.getwren.ai/oss/concepts/what_is_mdl))

**Implication:** we are not changing the engine contract. A manifest with `model A → tableReference.schema=sales` and `model B → tableReference.schema=crm` already compiles and rewrites SQL today. The work is to stop *forbidding* it in the layers wrapped around the engine.

### 1.2 Where the single-schema assumption actually lives

| Layer | Assumption | Evidence |
|---|---|---|
| **DB constraint** | Unique `(database_uri_fingerprint, catalog_name, schema_name, deleted_at)` → one active project per schema | `persistence/models.py:216-228` |
| **Project model** | `schema_name: str` (required, scalar) | `semantic_layer/schemas.py` `SemanticProject` |
| **Project resolution** | `resolve(...)` matches exactly one `schema_name` | `semantic_layer/projects.py` (in-mem + SQLAlchemy) |
| **Onboarding** | Introspects datasets of one schema only | `semantic_layer/onboarding.py:59-147` |
| **Validation index** | `SchemaIndex` built from one schema's tables; "table does not exist in the schema" | `semantic_layer/mdl_validator.py` (`SchemaIndex`, ~L376) |
| **Materializer** | Writes a single `dataSource.schema_name` / `semanticProject.schema` | `wren_materializer.py:58-117` |
| **Access scope** | `ConversationScope.schema_name: str`; resolve proves one schema | `conversations/schemas.py`, `access.py:120-129` |
| **Frontend** | `SemanticLayerEditorProps.schemaName: string`; no schema selector | `SemanticLayerEditor/index.tsx:243-247` |

Note one layer **already anticipated** multi-schema: `SemanticAccessProof.schema_names: list[str]` ([access.py:60](superset_ai_agent/semantic_layer/access.py#L60)). The proof is plural; only the project and scope are scalar.

---

## 2. Goals / non-goals

**Goals**
- One semantic project can reference tables from **N schemas within the same database/catalog**.
- Onboarding can select tables across multiple schemas in one flow.
- Validation recognizes tables from **any** project schema and reports schema-qualified errors.
- Access control proves the user can read **every** schema the project touches.
- The UI lets the user see and manage the project's schema set; no silent single-schema fallback.
- **Backward compatible**: every existing single-schema project keeps working with zero user action.

**Non-goals (this iteration)**
- **Cross-database** projects (tables from two different physical databases / connection URIs). Out of scope — keeps the URI-fingerprint identity model intact. Revisit later via wren-core `dataSource` per model.
- **Cross-catalog** projects. The catalog stays a single value per project (most backends collapse catalog to the connection). Schema is the axis we open.
- Changing the wren-core manifest contract or the native MDL vocabulary.
- Multi-tenant sharing/visibility model changes beyond what multi-schema access proof requires.

---

## 3. Design options & recommendation

### Option A — One project spans a **set of schemas** (RECOMMENDED)

Replace the scalar `schema_name` scope with a **schema set**: the project is identified by `(database_uri_fingerprint, catalog_name)` plus an associated, mutable set of schemas it is scoped to. Models within it freely reference any in-set schema via their existing `tableReference.schema`. The wren-core root `schema` becomes a fixed logical namespace (e.g. the project id or `"wren"`), decoupled from physical schemas — exactly as the WrenAI docs prescribe.

- **Pros:** Matches the engine's actual model and every industry peer (dbt sources, Cube `schema.table`, LookML `sql_table_name`). Enables cross-schema joins. Minimal engine risk. Natural, single-project UX.
- **Cons:** DB migration (drop/replace unique constraint). Access control must prove a *set* of schemas. Project identity/dedupe semantics change.

### Option B — Multiple single-schema projects + a "project group" overlay

Keep one project per schema; add a grouping entity that compiles several projects into one manifest for query time.

- **Pros:** No change to the per-project invariant; additive.
- **Cons:** Cross-schema joins/relationships have no home (relationships live in a manifest, not across projects). Doubles the surface (group CRUD, group coverage, group provenance). Onboarding/validation/UX still single-schema per unit. Higher total complexity for a worse UX. **Rejected.**

### Option C — Single project, `schema_name` becomes "default schema", models opt out per-table

Leave the DB/project model scalar; treat `schema_name` as a default and let validation/onboarding accept any schema via per-model `tableReference`.

- **Pros:** Smallest backend diff; no migration.
- **Cons:** Access control gap — a model could reference a schema the user was never proven to access (the resolve step only proves the default schema). This is a **security boundary** issue (see §6 R1). Onboarding still can't pick cross-schema tables cleanly. The "default schema" is a leaky abstraction users won't understand. **Rejected for the authz gap**, though its "default schema" idea is folded into Option A as the wren-core namespace.

**Recommendation: Option A.** It is the only option that (a) matches the engine and industry norm, (b) closes the authorization boundary cleanly, and (c) gives a coherent one-project UX. The rest of this spec details Option A.

---

## 4. Feature specification (Option A)

### 4.1 Data model

Introduce an explicit **schema set** owned by the project. Two viable shapes — see **D1**.

- **Project identity** changes from `(fingerprint, catalog, schema)` to `(fingerprint, catalog)` + a name/slug discriminator (since two projects could legitimately model the same database differently). See **D2** for the new uniqueness key.
- **`SemanticProject.schema_names: list[str]`** (ordered, de-duplicated, non-empty) replaces `schema_name: str`. Keep a derived `primary_schema` (first element) for display and for the wren-core namespace default.
- New association table `ai_agent_semantic_project_schemas(project_id, schema_name)` with unique `(project_id, schema_name)` — normalized, queryable, and migration-friendly (vs. a JSON column; see **D1**).
- `AiAgentSemanticProject.schema_name` column is **retained but deprecated** during migration as the "primary schema" mirror to keep old reads working; the association table is authoritative.

### 4.2 Project resolution & lifecycle

- `SemanticProjectResolveRequest` gains `schema_names: list[str]` (back-compat: accept scalar `schema_name` and coerce to a one-element list).
- `resolve(...)` matches on `(fingerprint, catalog)` + project discriminator; **reconciles** the schema set: schemas in the request not yet associated are added (after access proof, §4.4); existing schemas are retained. Removing a schema is an explicit, separate operation (see **D3** — never silently drop a schema that models still reference).
- Adding a schema to a project triggers an **incremental onboarding opportunity** (offer, don't auto-run) for that schema's tables.

### 4.3 Onboarding (multi-schema)

- `onboard_schema_project` generalizes to accept an `OnboardingSelection` whose table entries are **schema-qualified** (`{schema, table}` pairs), spanning any subset of the project's schema set ([onboarding.py:59-147](superset_ai_agent/semantic_layer/onboarding.py#L59-L147)).
- `SchemaIndex` is built from the **union** of datasets/tables across all project schemas, keyed by `(schema, table)` rather than bare `table` (see **D4** for the collision rule).
- Generated base models set `tableReference.schema` to the table's real schema (not the project default), so cross-schema is correct from first onboarding.

### 4.4 Access control

- `ConversationScope.schema_name: str` → `schema_names: list[str]` (back-compat coercion). `scope_hash` ([store.py](superset_ai_agent/semantic_layer/store.py)) incorporates the **sorted** schema set so cache identity is stable regardless of order.
- `resolve_project` / `_require_project_permission` must prove access to **every** schema in the set, not just one ([access.py:120-129, 205-214](superset_ai_agent/semantic_layer/access.py#L120-L214)). `SemanticAccessProof.schema_names` is already plural — wire it through.
- **Authorization invariant (must-hold):** a model whose `tableReference.schema` is **not** in the project's proven schema set is a validation **error** and is excluded from materialization. This is the gate that makes Option A safe where Option C was not (§6 R1).

### 4.5 Validation

- `validate_project_manifest` / `SchemaIndex` resolve a model's table against the **schema named in its `tableReference`** ([mdl_validator.py](superset_ai_agent/semantic_layer/mdl_validator.py)). Error text becomes schema-qualified: `Model X references table 'crm.customers' that does not exist in any project schema.`
- New validation rule: `tableReference.schema` must be a member of the project schema set (ties to §4.4 invariant).
- Relationship/join conditions referencing cross-schema models are valid as long as both models resolve — no special-casing; wren-core handles the SQL.

### 4.6 Materialization & compilation

- `materialize_wren_project` keeps writing **one** manifest. Root `catalog`/`schema` become the fixed logical namespace (project id-derived), **not** a physical schema ([wren_materializer.py:58-117](superset_ai_agent/semantic_layer/wren_materializer.py#L58-L117)). Per-model `tableReference.schema` already carries physical location, so the compiled manifest is unchanged in shape — only the root namespace semantics are clarified.
- `dataSource.properties.schema_name` (single) is replaced by `schema_names` (list) for provenance; this is metadata wren-core tolerates (`extra="allow"`).
- `compile_manifest`'s `schema=` parameter becomes the logical namespace, defaulted, not the physical schema.

### 4.7 Frontend / UX

- `SemanticLayerEditorProps.schemaName: string` → `schemaNames: string[]` (accept scalar for back-compat at the call site) ([index.tsx:243-247](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L243-L247)).
- **Header schema chips + "Add schema" control:** the editor header shows the project's schema set as chips; an "Add schema" affordance opens a schema multi-select (scoped to the current database/catalog) → proves access → reconciles the set → offers onboarding for the new schema.
- **Onboarding picker** (`OnboardingTablePicker.tsx`) gains a schema dimension: a schema selector/grouping above the table list; the `schema` filter becomes `in (schemaSet)` and rows show their schema. Selected tables carry their schema.
- **Schema graph** (`SchemaGraph/SchemaGraph.tsx`) filters physical tables by `schema ∈ schemaSet` and visually groups/colors nodes by schema so cross-schema joins are legible.
- **Default new-model template** prefills `tableReference.schema` from the active schema chip rather than leaving it blank ([index.tsx:223-233](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L223-L233)).
- Coverage/provenance are already project-level — no schema coupling, no change needed.

### 4.8 Copilot

- `MdlToolset` receives the unioned multi-schema `SchemaIndex`; `get_schema` retrieval ranks across all schemas ([copilot/tools.py](superset_ai_agent/semantic_layer/copilot/tools.py), [schema_retriever.py](superset_ai_agent/semantic_layer/schema_retriever.py)). `SchemaItem` gains a `schema` qualifier so retrieved tables are unambiguous and the agent emits correct `tableReference.schema`.

---

## 5. Decision points

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | How to store the schema set | (a) JSON/array column on project; (b) **normalized association table** | **(b)** association table `ai_agent_semantic_project_schemas`. Queryable for resolve/list, enforces uniqueness, indexes cleanly, avoids JSON-in-SQL filtering. JSON column would push set logic into app code and break server-side resolution filters. |
| **D2** | New project uniqueness key (schema removed from it) | (a) `(fingerprint, catalog)` — one project per database; (b) `(fingerprint, catalog, name_slug)` — many named projects per database | **(b)**. (a) would forbid two legitimately different models of one DB and is a one-way door. (b) preserves today's "resolve creates if missing" by using a stable default slug, and unlocks named projects later. Migration maps each existing project to a slug derived from its current `schema_name`. |
| **D3** | Removing a schema from a project | (a) block if any active model references it; (b) cascade-delete those models; (c) soft "detach" leaving orphan models that fail validation | **(a)**. Safest, reversible, no surprise data loss. Surface the blocking models in the error so the user can delete them first. |
| **D4** | Table name collisions across schemas (`sales.orders` vs `archive.orders`) | (a) key `SchemaIndex` by `(schema, table)`, require model names unique; (b) auto-prefix model names by schema | **(a)**. Keep the index `(schema, table)`-keyed so both physical tables are addressable; model **logical** names must still be unique within the manifest (wren-core requirement — see dedupe note [mdl_compile.py:154-162](superset_ai_agent/semantic_layer/mdl_compile.py#L154-L162)). When onboarding would create two models named `orders`, disambiguate the *logical* name (e.g. `sales_orders`) while `tableReference` stays exact. Don't force prefixes when there's no collision. |
| **D5** | wren-core root `schema` value once decoupled | (a) keep `"public"`/first schema; (b) project-derived logical namespace | **(b)** a fixed, project-stable namespace (e.g. `"wren"` or project slug). Using a real physical schema as the root is the leaky abstraction that caused the confusion; make the decoupling explicit. |
| **D6** | Migration default for existing projects | (a) one-element schema set = current `schema_name`; (b) auto-expand to all sibling schemas | **(a)**. Never widen a user's data scope automatically — that would be a silent access expansion. Existing projects stay exactly as scoped; users opt into more schemas explicitly. |
| **D7** | API back-compat for `schema_name` | (a) hard cut to `schema_names`; (b) accept both, coerce scalar → `[scalar]`, echo both for a deprecation window | **(b)**. Frontend, stored conversations, and any external callers send scalar today; dual-accept avoids a flag-day break. |

---

## 6. Risks & mitigations

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| **R1** | **Authorization bypass** — a model references a schema the user wasn't proven to access (the Option C gap). | **High (security)** | The §4.4 invariant: materialization/validation **reject** any `tableReference.schema ∉ proven schema set`. Access proof must cover every set member before resolve returns. Add a unit test that a model pointing at an unproven schema is excluded and errors. Map to `SECURITY.md`: principal = `sql_lab`/Gamma with schema-scoped DB access; the matrix row is "data/table access requires proven access to that schema." |
| **R2** | **DB migration on the unique constraint** is irreversible-ish and runs against live `ai_agent.db` volumes (see memory: legacy rows in persistent volume). | **High** | Ship as expand/contract: (1) add association table + backfill from `schema_name`; (2) switch reads to the table; (3) replace the unique index; (4) keep `schema_name` column as primary mirror. Each step independently deployable & reversible. Use `superset.migrations.shared.utils` helpers per `CLAUDE.md`. Test against a copy of a real volume. |
| **R3** | **Cross-worker store drift** — in-memory vs SQLAlchemy project stores must agree on set reconciliation semantics. | Medium | Define reconciliation once in a shared helper; both `InMemorySemanticProjectStore` and `SqlAlchemySemanticProjectStore` call it. Parity test asserts identical resolve behavior for a multi-schema request. |
| **R4** | **Model logical-name collisions** across schemas double-register a physical table and wren-core rejects the manifest (`table … already exists`). | Medium | D4: `(schema,table)`-keyed index + onboarding disambiguates logical names. Validation surfaces duplicate logical names pre-materialization. Existing last-wins dedupe ([mdl_compile.py:154-176](superset_ai_agent/semantic_layer/mdl_compile.py#L154-L176)) is a backstop, not the primary guard. |
| **R5** | **Scope cache poisoning** — `scope_hash`/retriever checksum keyed by scalar schema would alias different schema sets. | Medium | Incorporate the **sorted** schema set into `scope_hash` and retriever `scope_key`. Re-index on set change (already keyed by manifest checksum, which changes when models change). |
| **R6** | **Performance** — onboarding/validation now union N schemas' datasets; large multi-schema DBs inflate the `SchemaIndex` and retrieval corpus. | Low/Medium | Build `SchemaIndex` lazily per resolve; cap onboarding introspection to selected tables (already table-scoped); paginate the multi-schema picker (existing pagination). Log if a project's schema set exceeds a soft cap rather than silently truncating. |
| **R7** | **UX confusion** — users equating the wren-core root `schema` with a physical schema. | Low | D5 fixes the root to a logical namespace; the UI never shows it. UI talks only about the *physical schema set* (chips), matching the user's mental model. |
| **R8** | **Stale single-schema conversations** referencing a project whose set later grew. | Low | Conversations store scope, not project internals; D7 coercion keeps old scalar scopes valid (they prove a subset). No migration of conversation rows needed. |

---

## 7. Intent alignment (dev ↔ spec ↔ user)

| Layer | Stated intent | Spec realization | Verification |
|---|---|---|---|
| **Engine/dev** | "One semantic layer over a business domain, physical location is per-table." (wren-core design) | Root namespace decoupled (D5); per-model `tableReference.schema` is the source of truth; manifest shape unchanged. | Native-manifest contract test still green; new test: 2-schema manifest compiles + rewrites a cross-schema join. |
| **Backend/dev** | "A project is the unit of authoring, access, coverage." | Project keeps being the unit; only its *scope* widens from one schema to a set, gated by per-schema access proof. | Resolve/list/access parity tests (in-mem ↔ SQLAlchemy); authz test for R1. |
| **User intent** | "Model my orders-and-customers domain that lives in `sales` and `crm` together." | Add both schemas to one project; onboard tables from both; join across them; one coverage/provenance timeline. | E2E: create project, add 2nd schema, onboard a table from each, author a cross-schema relationship, validate green. |
| **User flow ↔ UI** | "I should see and control which schemas my project covers." | Header schema chips + Add-schema; multi-schema onboarding picker; schema-grouped graph. No hidden default-schema behavior. | UI test: chips reflect set; adding a schema proves access then offers onboarding; removing a referenced schema is blocked with a clear message (D3). |

---

## 8. Phasing (independently shippable)

1. **Phase 0 — Decouple root namespace (no scope change).** Make wren-core root `schema` a fixed logical namespace (D5); confirm existing single-schema projects unaffected. Pure refactor, no migration. De-risks the engine assumption before touching scope.
2. **Phase 1 — Data model + access (backend).** Association table + backfill (R2 expand/contract); `schema_names` on project/scope/resolve with scalar coercion (D7); access proof over the set (R1). Reads still single-schema in practice.
3. **Phase 2 — Onboarding + validation multi-schema.** Schema-qualified `SchemaIndex` (D4), multi-schema onboarding, schema-qualified validation errors, R1 invariant enforced.
4. **Phase 3 — Frontend.** `schemaNames` prop, header chips/Add-schema, multi-schema picker, schema-grouped graph, copilot `SchemaItem.schema`.
5. **Phase 4 — Contract.** Replace the unique index (R2 step 3), retire the deprecated scalar echo after the window (D7).

Each phase ends with `pytest tests/unit_tests/superset_ai_agent/ -q`, the named UI tests, and `pre-commit run` on staged files, per `CLAUDE.md`.

---

## 9. Open questions for the user

- **Named projects (D2b):** do you want the door open for *multiple* projects per database now, or is "one project per database, multi-schema" sufficient for the first release? (D2b keeps it open cheaply; confirm before committing the uniqueness key.)
- **Cross-database** modeling — confirmed out of scope this round? (Non-goal §2.)
- **Schema set soft cap** (R6) — is there an expected upper bound (e.g. ≤ 10 schemas/project) we should design the picker UX around?

---

### Sources
- [What is Modeling Definition Language (MDL)? — Wren AI](https://docs.getwren.ai/oss/concepts/what_is_mdl)
- [Wren AI architecture reference](https://docs.getwren.ai/oss/reference/architecture)
- [Cube — fully-qualified table references / dbt integration](https://cube.dev/docs/guides/dbt)
- [dbt — building semantic models](https://docs.getdbt.com/best-practices/how-we-build-our-metrics/semantic-layer-3-build-semantic-models)
- [Semantic Layer Showdown: Cube vs dbt Semantic Layer vs LookML](https://pipecode.ai/blogs/semantic-layer-cube-dbt-semantic-layer-lookml)
