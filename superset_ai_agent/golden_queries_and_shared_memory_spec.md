<!--
Feature spec — NL→SQL memory rescoping + access-aware recall + project-scoped golden queries.
Authored 2026-06-30. Source-backed; see inline file:line citations.
Status: DRAFT for review. No code written yet. Decision points flagged inline.
-->

# Shared NL→SQL Memory, Access-Aware Recall, and Project-Scoped Golden Queries

## 0. Guiding principle (user intent)

> Unless explicitly declared and discussed, **no Wren / memory / SQL knowledge is scoped by user.**
> The sharing boundary is the **database**: anyone who can use the AI SQL agent against a
> database may benefit from what was learned on that database — subject to per-request
> table/schema access checks so a user never sees or leverages a pair referencing data they
> cannot access.

This spec turns that principle into three features:

| # | Feature | Scope of the knowledge | Sharing |
|---|---------|------------------------|---------|
| **F1** | De-user-scope runtime memory → **database-level** shared corpus | database identity | all users with agent access to the DB |
| **F2** | **Access-aware recall** — RBAC hard-filter + relevance down-rank | per-request (user's accessible tables × active project) | enforced at recall |
| **F3** | **Project-scoped golden queries** (curated, verified, Cortex-VQR-style) | MDL project | all users with project access |

F1+F2 are coupled (F2 is the safety mechanism that makes F1 safe). F3 is additive and can ship independently but shares the recall path.

### Terminology — "runtime memory" vs "golden query" (read this first)

These are **two distinct stores**, not one. They only meet at recall.

| | **Runtime memory** (F1) | **Golden query** (F3) |
|---|---|---|
| What it is | auto-captured `question → SQL` pair | curated, **human-verified** `question → SQL` entry |
| Store | `AiAgentNlSqlExample` table | an entry in the project's **`queries.json`** MDL file (DP-5b) |
| Created by | the system, on every successful run | a person (promote / Copilot-propose+accept / hand-write) |
| Scope | **database** (shared across projects on the DB) | **project** (shared across users of the project) |
| Lifecycle | bounded by **capacity decay** (oldest aged out past `max_examples`) — unrelated to promotion | versioned + deployed with the MDL; never decayed |
| Trust | "executed OK" | "human-verified" (`verified_by` / `verified_at`) |
| SQL form | `native_sql` (physical) + `semantic_sql` | **`semantic_sql`** (logical/model names — Wren & Cortex rule) |
| Wren analogue | `query_history` | `queries.yml` |

**A golden query is its own construct — an entry in `queries.json` — not "the memory" and not "both."** It may be *drawn from* memory (the "Promote to golden" gesture copies a runtime pair into `queries.json` as a decoupled snapshot), or authored fresh by the Copilot or a user. Once created it is an independent, project-versioned artifact; editing or evicting the source memory row does not change it. **The two stores converge only at recall**, where both pools are retrieved and merged into the few-shot prompt (golden prioritized, deduped against memory). See §5.

> **Invariant — promotion is a COPY, never a MOVE.** Promoting a runtime pair to a golden query **does not remove it from memory.** The memory row stays in the **database-scoped** pool (it benefits every project on that DB); the golden copy serves the **project**. Removing it on promotion would trade DB-wide value for one project's — explicitly disallowed. **Dedup is a recall-time presentation choice** (golden supersedes its runtime twin in the few-shot prompt), **never a deletion.** Memory's capacity-decay and golden promotion are independent lifecycles: decay may later age out the runtime row, but the golden copy is permanent, so nothing is lost.

---

## 1. Verified current state (the starting point)

- **Memory is user-scoped today.** NL→SQL pairs key on `(owner_id, scope_hash)` for store, recall, dedup, eviction ([memory_store.py:256](semantic_layer/memory_store.py#L256)). `owner_id` is the Superset user (`superset:{user_id}`, [auth.py:276](auth.py#L276)). → two analysts with identical DB access share nothing.
- **`scope_hash`** = `database_id + catalog + schema(s) + dataset_ids` ([store.py:210](semantic_layer/store.py#L210)) — mixes DB, schema, and *table* granularity.
- **Datasets are table-level.** `DatasetSummary{id, table_name, schema_name, database_id}` ([client.py:69](integrations/superset/client.py#L69)). `dataset_ids` in a scope = a set of specific tables — the granularity auto-onboarding makes non-deterministic.
- **Projects are database-scoped, span schemas.** `WrenSemanticProject{default_database_id, schema_name, schema_names[]}` with per-model `tableReference.schema` ([schemas.py:219](semantic_layer/schemas.py#L219)).
- **Each pair stores both `semantic_sql` (model names → project-local) and `native_sql` (physical schema.table → DB-valid).** This split is the technical crux of every scoping decision below.
- **Access infra already returns the user's reachable tables.** `SemanticAccessService.require_schema_set_permission(...)` proves access per schema and returns the **union of accessible datasets**; "a schema that cannot be proven contributes nothing" ([access.py:129](semantic_layer/access.py#L129)). At recall, `state["context"].datasets` is exactly this set ([graph.py:399](graph.py#L399), used at [graph.py:600](graph.py#L600)).
- **`extract_referenced_tables(sql, dialect)`** already exists ([engine/base.py:110](semantic_layer/engine/base.py#L110)) — physical-table extraction is free.
- **Read-only UI surface exists.** Recalled pairs render in the explain trace as `RecalledExamples` (question + `native_sql`) ([AgentStepDetail.tsx:403](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/AgentStepDetail.tsx#L403)). No management/CRUD UI for memory.
- **Latest migration head:** `0014_schema_snapshot_by_schema` → new work starts at `0015`.

---

## 2. Industry grounding (what mature systems do)

Every mature text-to-SQL product converges on the **same two-tier split** this spec adopts — an auto-captured historical pool + a curated/verified pool — scoped at a **shared boundary, never per-user**, with a human promote-to-verified gesture. Sources are cited per row.

- **Snowflake Cortex Analyst — Verified Query Repository (VQR).** `verified_queries` live *inside the semantic model*; authoritative protobuf fields: `name, semantic_model_name, question, sql, verified_at, verified_by, use_as_onboarding_question`. Rule: *"Verified SQL queries must reference the logical table/column names in the semantic model, not the underlying dataset"* → verified queries are **project-scoped by construction** (the reason our `semantic_sql` is project-local). Promotion = a human **"Save as verified query"** button — *no* approval gate; `verified_by`/`verified_at` are just a human assertion. Retrieval = similarity; the answer surfaces **`confidence.verified_query_used`** so the user sees a verified query was used. `use_as_onboarding_question=true` queries are returned **deterministically** (not similarity-gated) as starter questions. ([VQR](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository), [proto](https://github.com/Snowflake-Labs/semantic-model-generator/blob/main/semantic_model_generator/protos/semantic_model.proto))
- **Databricks AI/BI Genie.** "Trusted assets" (parameterized example SQL + UC functions) are a **higher-trust tier** above generic example SQL; using one yields a **labeled "verified answer"**. Auto-discovered popular queries are **suggestions requiring human accept/reject** (a "Review Suggested Queries" dialog), gated by the curator's own query ACLs. Shared-assistant + **per-user data isolation**: runs on the author's compute creds but applies each end-user's data credentials, so *"any question about data they can't access generates an empty response."* **Notable gap:** Genie's docs state metadata/example *text* is shown to users lacking table SELECT (only *data results* are gated) — the exact leakage F2 closes. ([tune-quality](https://learn.microsoft.com/en-us/azure/databricks/genie/tune-quality), [set-up](https://learn.microsoft.com/en-us/azure/databricks/genie/set-up))
- **Vanna AI.** Two-tier: explicit `train(question, sql)` vs `auto_train=True` (embeds any pair whose SQL merely *executed* — "ran without error," weak governance). OSS enforces **no ACL on the pool**; v2.0 adds org/tenant isolation (`X-Organization-ID`) but gates *tools/conversations*, not the embedding store. → cautionary: "executed OK" ≠ "verified" (mirrors our F1 "confirmed = executed"). ([data-security](https://vanna.ai/data-security))
- **Wren AI.** Question-SQL pairs are kept **OUT of the MDL** in a separate **`queries.yml`** / `knowledge/sql/` store, derived into a LanceDB index (`query_history` + `schema_items` collections); project-scoped, Git-friendly. Cloud adds a **draft→publish** gate; *"Anyone on the project can curate it, and everyone benefits."* Our (DB row + LanceDB cache) architecture matches Wren's newer CLI/SDK generation. ([architecture](https://docs.getwren.ai/oss/reference/architecture), [question-sql-pairs](https://docs.getwren.ai/oss/guide/knowledge/question-sql-pairs))
- **dbt saved queries / LookML.** Both define curated queries **as code in project YAML/LookML, Git-versioned, per-project**, reviewed via PR — never per-user. LookML hides restricted fields from unauthorized users (no field-name leak); dbt's one documented gap is cached/exported results losing query-time security context. ([dbt](https://docs.getdbt.com/docs/build/saved-queries), [LookML access_grant](https://docs.cloud.google.com/looker/docs/reference/param-field-required-access-grants))
- **Academic basis.** Few-shot example *selection*: **DAIL-SQL** (arxiv 2308.15363) masks schema-specific tokens before similarity ranking (ranks by query-generation similarity, not surface/domain) — a future ranking enhancement for F2 Stage B. **Threat:** *Zero-Knowledge Schema Inference Attacks* (arxiv 2406.14545) — NL+SQL examples are a documented **schema-disclosure channel**, the citable basis for F2. **Pattern:** pre-filter ACL retrieval (Pinecone/Paragon) — resolve the user's authorized object IDs, then constrain the vector query with a metadata `$in` filter so *"the vector DB itself excludes unauthorized results."* F2 Stage A is exactly this (`referenced_tables` = the ACL metadata; `context.datasets` = authorized IDs).

**Takeaways adopted:** (a) curated/verified queries → project-scoped, model-name-based, with `verified_by`/`verified_at` provenance; (b) runtime pool → shared at the trust boundary (DB), never per-user; (c) human promote-to-verified gesture, optionally review-gated (we get Genie-style accept/reject *for free* via the changeset gate); (d) a "verified answer" signal when a golden query is used (Cortex `verified_query_used` / Genie label); (e) RBAC enforced — and **F2 goes one step further than Cortex and Genie**, which gate only *data execution*: we filter the *example pool itself* by accessible tables, closing the documented text-leakage gap.

---

## 3. F1 — Database-scoped shared memory

### Dev intent
Runtime memory is *shared knowledge about a database*, not a personal cache. Re-key it on database identity; drop `owner_id` from the key. Keep authorship as metadata (provenance), not as an isolation key.

### Spec
1. **New memory key = database identity**, replacing `(owner_id, scope_hash)`.
   - Define `memory_scope_key(scope)` = hash of `database_id` (+ `catalog_name` — see **DP-1**). **Drop** `schema_name`, `schema_names`, `dataset_ids`, **and** `owner_id`. Rationale: native-SQL validity is a property of the DB connection; cross-schema pairs have no single schema home; onboarding non-determinism makes any sub-DB key unstable; schema overlap across projects makes schema-keying produce incoherent partial sharing.
   - Cross-database isolation is **preserved** (different `database_id` = different physical tables = correctly not shared).
2. **`owner_id` becomes authorship metadata.** Keep the column (provenance: who first contributed the pair), but remove it from all WHERE clauses, dedup keys, and the LanceDB `scope_key`. Dedup identity stays `(_normalize(question), _normalize(native_sql))` but now within the DB pool.
3. **Store/recall signatures** drop `owner_id` as a *scoping* arg across `Memory` protocol, `SqlAlchemyMemory`, `LanceDbMemory`, `InMemoryMemory`, and the three call sites ([graph.py:862](graph.py#L862), [conversation_graph.py:1480](conversation_graph.py#L1480), [pipeline.py:189](semantic_layer/pipeline.py#L189)) + the two recall sites ([graph.py:603](graph.py#L603), [conversation_graph.py:1070](conversation_graph.py#L1070)).
4. **Migration `0015`:** re-key existing rows. Add `database_id` column (if not already derivable) and a backfill that recomputes the new key. **DP-2** below governs whether to dedup-merge legacy per-user duplicates.

### User flow / UI alignment
- The `RecalledExamples` trace panel needs **no structural change** — but copy must not imply the pool is personal. (Memory has no "personal" claim today; only *instructions* do — see §6.)
- New (small) honesty affordance: recalled examples may now originate from other users. No PII beyond question text + SQL is shown; the panel already shows only `question` + `native_sql`.

### Risks & mitigations
| Risk | Mitigation |
|---|---|
| **Question text leaks business context across users** on the same DB | Accepted by the stated trust model (same DB access ⇒ shared). F2 still hard-filters any pair referencing tables the recaller can't access. Document the trade-off in UPDATING.md. |
| Legacy rows keyed per-user create duplicate (question, SQL) across owners | Backfill dedups by the normalized identity, keeping most-recent (**DP-2**). |
| A pair contributed by user A references a table user B can't reach | **F2 is the gate** — F1 must not ship without F2. |
| LanceDB `sql_pairs` cache keyed on `{owner_id}:{scope_hash}` | Re-key cache to `db:{database_id}`; cold cache degrades closed to SQL ranking ([memory_store.py:404](semantic_layer/memory_store.py#L404)) — safe. |

---

## 4. F2 — Access-aware recall (the safety mechanism)

### Dev intent
Shared memory must be **safe by construction**: a recalled pair may influence a draft only if the requesting user can access every table it references; and pairs outside the active project's footprint should be down-weighted, not silently dominant. This is the industry-standard **pre-filter ACL retrieval** pattern (resolve authorized object IDs → constrain the candidate set before ranking, so unauthorized rows never reach the prompt), and it closes a documented leakage channel (*Zero-Knowledge Schema Inference*, arxiv 2406.14545) that even Cortex Analyst and Genie leave open (they gate *data execution* but surface example/instruction text regardless of per-table grants).

### Spec
1. **Persist provenance of references on each pair.** Add `referenced_tables: JSON` and `referenced_schemas: JSON` to `AiAgentNlSqlExample`. Compute at store time via `extract_referenced_tables(native_sql, dialect)` ([engine/base.py:110](semantic_layer/engine/base.py#L110)) — or reuse `plan.referenced_tables` already computed ([pipeline.py:88](semantic_layer/pipeline.py#L88)). Derive schemas from the physical refs. Backfill best-effort in migration `0015`.
2. **Recall pipeline becomes three-stage** (in `recall_examples`, after candidate load, before injection):
   - **Stage A — RBAC hard-filter (fail closed).** Build the accessible table set from `context.datasets` → `{(d.schema_name, d.table_name)}`. **Drop** any pair whose `referenced_tables` ⊄ accessible set. **If `referenced_tables` is null/empty/unparseable, drop the pair** (fail closed — never surface a pair we cannot prove is safe).
   - **Stage B — relevance down-rank.** For surviving pairs: **big** penalty if any referenced schema ∉ project `schema_names`; **small** penalty if a referenced table is accessible + in an in-scope schema but **not onboarded** into the active project's manifest. (Penalty applied to the similarity score, not a hard cut — keeps cross-schema hints alive.)
   - **Stage C — presentation.** For a surviving pair whose models/tables aren't all onboarded in the active project, inject its **`native_sql`** and **omit `semantic_sql`** (foreign model names would dangle). Fully-onboarded pairs inject both.
3. **Where it runs:** inside `recall_examples` (so all callers inherit it), with the accessible set + active project manifest passed in from the draft node, which already holds `state["context"]` and the Wren context.

### Decision points
- **DP-3 — accessible set definition.** *(a) Conservative (recommended for v1):* accessible = registered datasets in the request scope (`context.datasets`). Safe, already loaded, zero extra calls; may under-recall pairs referencing accessible-but-not-in-scope tables. *(b) Permissive:* accessible = the user's full Superset DB-role table grants. More complete recall, but requires querying Superset's permission model per table (cost + complexity) and risks over-sharing if the model is misread. **Recommend (a)**; revisit (b) only if under-recall proves material.
- **DP-4 — degrade-closed confirmation.** Confirm the fail-closed default for unparseable/legacy `referenced_tables`. (Recommended: yes — security beats recall.)

### Risks & mitigations
| Risk | Mitigation |
|---|---|
| SQL parse failure hides referenced tables → could leak | **Fail closed** (DP-4): unparseable ⇒ excluded from recall. |
| `referenced_tables` goes stale after a table rename | Native refs are physical; a renamed table simply stops matching the accessible set → pair drops out (safe degradation). Periodic re-derive optional. |
| Over-filtering (conservative DP-3) drops useful pairs | Acceptable for v1; down-rank (Stage B) already preserves cross-schema hints that survive the RBAC filter. |
| Performance: per-recall parse of N candidates | Parse once at *store* time and persist (Stage 1); recall is set-membership only. |

---

## 5. F3 — Project-scoped golden queries (Cortex-VQR analogue)

### Dev intent
A **governed, project-scoped, shared, human-verified** set of question→SQL pairs — the curated layer Wren ships as `queries.yml` and Cortex ships as the VQR. Distinct from F1's auto-captured runtime pool: golden queries are *trusted*, recalled with priority, and survive eviction.

### Spec
1. **Storage — DP-5: DECIDED → (b) `queries.json` project file** (Wren `queries.yml` parity; matches user expectation).
   - A golden query is **one entry in a `queries.json` MDL file**, a new `MdlFile` **kind sibling to the model files** — *out of* the MDL models, exactly as Wren keeps pairs in a separate `queries.yml`. It reuses `MdlFileStore`, the draft→activate lifecycle, the changeset review-gate, and provenance **wholesale** — no new store, no new CRUD surface, no new migration.
   - **Entry shape** (field names borrow Cortex VQR's authoritative proto): `{ name, question, semantic_sql, verified_by, verified_at, use_as_onboarding (bool), usage_guidance? }`. **SQL is authored in `semantic_sql` (logical / model-name) form** — the Wren *and* Cortex rule ("verified SQL must reference the semantic model's logical names, not the physical dataset"). This is safe precisely because the file is **project-scoped by construction** (it lives in the project, so its model names always resolve).
   - **Scope = the project** — inherent, because the file *is* part of the project. Never owner, never dataset.
   - **F2 reference set is manifest-derived, not parsed.** Because the SQL uses model names, a golden query's referenced **physical** tables (needed by F2's RBAC filter) are resolved via each referenced model's `tableReference.schema`+`table` in the active manifest — always available, no SQL parsing, no `native_sql` to persist. (Contrast F1, which parses `native_sql`.)
   - *Rejected alternative (a):* a `project_id`-keyed DB table `AiAgentGoldenQuery`. Viable and CRUD-simple, but not versioned/deployed with the MDL and diverges from Wren. Recorded only as the fallback if a non-file store is ever needed.
2. **Authoring paths (both human-gated):**
   - **Copilot-authored** via the existing **changeset review-gate**: add an `add_golden_query` Copilot tool + a `ToolActionKind` (extend `Literal[..., "curate"]` [copilot/schemas.py:54](semantic_layer/copilot/schemas.py#L54)); proposals land as `ChangesetItem`s, reviewed in `ChangesetReviewPanel`, persisted via `apply_changeset_items` as **drafts** ([copilot/service.py:186](semantic_layer/copilot/service.py#L186)). Activation stays a separate human action.
   - **Promote-from-runtime** (the Cortex "Save as verified query" gesture): a **"Promote to golden"** button on each `RecalledExamples` / answer card. One click **copies** a runtime pair into the project's golden set (draft → verify). **The source memory row is left in place** (it stays in the DB-scoped pool, serving every project on the DB) — promotion is a copy, never a move. Highest-leverage, lowest-friction UX; directly mirrors Cortex.
3. **Validation-on-verify.** A golden query is marked `verified` only after its SQL validates against the active manifest (and optionally executes read-only OK). Mirrors Cortex's "ensure the query actually answers the question."
4. **Recall merge (dedup is presentation-only, not deletion).** Golden queries recall **project-scoped, priority-ranked, decay-exempt** (like `is_global` instructions [instructions.py:117](semantic_layer/instructions.py#L117)), merged with F1's DB-pool runtime pairs. When a golden query and a memory pair share a normalized question, **golden supersedes the runtime twin *in the few-shot prompt only*** — both physical rows are retained; nothing is removed from either store. Golden carry `semantic_sql` safely because they are project-scoped by construction. F2's access filter **still applies** (a golden query referencing a table the user can't access is dropped).
5. **UI.** A **"Golden queries"** tab in the Semantic Layer Editor mirroring `InstructionsPanel` (add / list / delete; no edit → re-create), plus the promote button and a `verified` badge. Read-only mirror in `CopilotInspectorDialog`. **Verified-answer signal:** when a draft was generated using a golden query, badge the answer ("Verified" / show which golden query was used) — the Cortex `confidence.verified_query_used` / Genie "trusted asset" pattern; the explain trace already carries recall provenance to hang this on.
6. **Optional extension — onboarding/starter questions.** A `use_as_onboarding` golden query is surfaced **deterministically** (not similarity-gated) as a suggested starter question for the project — Cortex's `use_as_onboarding_question`, and a natural fit with the existing suggested-questions surface. Defer unless wanted; the field is cheap to reserve now.

### User flow / UI alignment
- **Author/curate:** analyst asks a question → agent answers → clicks **"Promote to golden"** → (optional) edits the question label → query enters the project's golden set as a draft → a project writer **verifies/activates**. Matches Cortex exactly.
- **Consume:** every user with project access gets the golden query as a high-priority few-shot, access-filtered by F2.
- **Govern:** golden queries are visible/diffable in the changeset review and the editor tab; provenance recorded via `apply_provenance_payload` ([copilot/service.py:273](semantic_layer/copilot/service.py#L273)).

### Risks & mitigations
| Risk | Mitigation |
|---|---|
| **Poisoning** — a wrong golden query becomes authoritative | Human review gate (changeset accept) + validation-on-verify + provenance (`verified_by` / `verified_at`). |
| Staleness after schema change | Validation-on-activate; surface stale golden queries in the coverage audit; prefer storing `semantic_sql` (model-name-based, survives physical renames). |
| Recall dilution (golden + runtime compete for k) | Reserve/prioritize golden slots; dedup by normalized question. |
| Scope creep vs. instructions | Golden queries = question→SQL exemplars; instructions = imperative guidance. Keep distinct stores. |

---

## 6. Companion decision — instructions are *deliberately* personal (DP-6)

Unlike memory, **instructions are explicitly user-scoped by product design.** The UI states it ([InstructionsPanel.tsx:157](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/InstructionsPanel.tsx#L157)):

> *"Your instructions are personal. They steer SQL generation for this schema for you only — other users with access to this database don't see or share them (unlike the project's documents and models)."*

This is the "explicitly declared and discussed" carve-out in your principle. So instructions are **out of scope for the auto-fix** — changing them would contradict a stated product promise and require UI/consent changes. **DP-6:** keep instructions personal (recommended — it's a deliberate, surfaced choice), or open a separate effort to add a shared/global tier (note: `is_global` already exists but is still owner-scoped, so "global" today means "global *for me*"). Decide separately; do not bundle into F1.

---

## 7. Consolidated decision points

| ID | Decision | Recommendation |
|----|----------|----------------|
| **DP-1** | Memory key = `database_id` only, or `(database_id, catalog)`? | `database_id` only (connection = validity boundary); revisit for multi-catalog (Trino/Snowflake) connections. |
| **DP-2** | Backfill: dedup-merge legacy per-user duplicates? | Yes — merge by normalized (question, native_sql), keep most-recent. |
| **DP-3** | F2 accessible set = request scope vs. full DB-role grants? | Request scope (`context.datasets`) for v1 — safe + free. |
| **DP-4** | Fail-closed on unparseable `referenced_tables`? | Yes — drop from recall. |
| **DP-5** | F3 storage: DB table vs. project file (`queries.json`)? | ✅ **DECIDED → project file** (`queries.json` MDL-file kind). Wren `queries.yml` parity; versions/deploys with the MDL; golden SQL in model-name form; F2 refs manifest-derived. |
| **DP-6** | Instructions: keep personal, or add shared tier? | Keep personal (deliberate, UI-declared); separate effort if shared wanted. |
| **DP-7** | Cube-backed answers as golden queries — store an optional structured `cube_query` field? | ⏸️ **DEFER to cube Track B** (see `plan_cubes_parity_spec.md` §6A / DP-C1). Until then, a cube-backed answer promotes as ordinary model-name `semantic_sql`; runtime memory keeps physical `native_sql` (RBAC-safe, DB-scoped — no cube-aware logic needed). When cube *consumption* ships, add an optional additive `cube_query` to the entry so recall can teach the agent to prefer the cube. No conflict with the DB-vs-project scoping model. |

---

## 8. Sequenced implementation checklist (for a future session)

**Phase 1 — F1+F2 foundation (ship together; F1 unsafe without F2).**
1. [ ] Add `referenced_tables`, `referenced_schemas` columns to `AiAgentNlSqlExample`; migration `0015` (+ best-effort backfill via `extract_referenced_tables`).
2. [ ] Populate refs at store time (all three store sites). Tests: refs persisted; multi-schema native SQL → multiple schemas.
3. [ ] Introduce `memory_scope_key(scope)` (DB identity, DP-1). Replace `scope_hash` in memory store/recall; keep `instruction_scope_hash` untouched.
4. [ ] Drop `owner_id` from memory keys (protocol + 3 impls + 5 call sites + LanceDB `scope_key`); keep `owner_id`/`created_by` as metadata. Tests: two owners share a pool; cross-DB isolation holds.
5. [ ] Re-key migration `0015` for existing rows (DP-2 dedup-merge).
6. [ ] F2 recall stages A/B/C in `recall_examples`; pass accessible set + active manifest from the draft node. Tests: inaccessible-table pair dropped; null refs dropped (DP-4); out-of-schema pair down-ranked; cross-project pair shows `native_sql` only.
7. [ ] UPDATING.md: document the trust trade-off (question text shared at DB level).

**Phase 2 — F3 golden queries (additive; DP-5 = `queries.json` MDL-file kind).**
8. [ ] Define the `queries.json` MDL-file kind + entry schema `{name, question, semantic_sql, verified_by, verified_at, use_as_onboarding, usage_guidance?}`; wire into `MdlFileStore`, validation, and the draft→activate lifecycle (sibling to model files, project-scoped by construction).
9. [ ] Copilot `add_golden_query` tool + `ToolActionKind` "curate"; route through changeset review + apply-as-draft (reuses the model/view authoring path).
10. [ ] "Promote to golden" button on `RecalledExamples` / answer cards → copies the runtime pair into `queries.json` as a decoupled draft entry (translate native→semantic SQL where needed).
11. [ ] Manifest-derive each entry's referenced physical tables (via referenced models' `tableReference`); validation-on-verify; recall merge (priority, eviction-exempt, dedup vs F1 memory); F2 filter applies to golden too.
12. [ ] "Golden queries" editor tab (mirror `InstructionsPanel`) + read-only inspector mirror; `verified` badge; verified-answer badge when a golden query was used.
13. [ ] Tests: project-scoped isolation; golden supersedes runtime on dup; access-filtered via manifest-derived refs; review-gate persists as draft; activation separate; round-trips through `MdlFileStore`.

**Out of scope (this spec):** instructions rescoping (DP-6), permissive DB-role accessible set (DP-3b).
