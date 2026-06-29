# Cross-Schema Tooling Audit — MDL Copilot & AI SQL Agent

**Created:** 2026-06-30 · **Scope:** every tool/step/prompt in `superset_ai_agent/` vs. the
cross-schema (multi-schema project) use case. **Method:** direct source reading + 3 parallel
sub-audits (AI-SQL pipeline, query-time execution, prompts), reconciled against the live tree
(some sub-audit verdicts were corrected — see §0.1).

This is a **state-of-the-world reference**, not an implementation plan. Each row cites
`file:line` and a verdict: ✅ cross-schema-correct · ⚠️ partial · ❌ single-schema / mis-scoped ·
➖ schema-independent (N/A).

---

## 0. Executive summary

A multi-schema MDL project (user-selected, must support cross-schema joins) splits cleanly into
**two paths**, and the cross-schema correctness is opposite on each:

- **MDL / semantic-engine / execution path = cross-schema CORRECT.** The compiled manifest keeps
  every model's `tableReference.schema`; wren-core rewrites model-SQL → schema-qualified native
  SQL; join-closure injects cross-schema partners; access, scope-hashing, and project resolution
  all honor the full `schema_names` set.
- **Physical-schema *surfacing* / *typing* / *context* path = single-schema / mis-scoped.** Every
  place that renders the physical catalog to an LLM (the copilot's `get_physical_schema`, the
  enrichment generator, the SQL agent's dataset context) reads a **flat, schema-blind** map that
  (a) drops schema qualification and (b) silently collides same-named tables across schemas.

The single shared root cause is **`SchemaIndex`'s dual representation** (§1). Most individual tool
"bugs" are this one cause surfacing in different consumers.

### 0.1 Corrections to the sub-audits (verified against the live tree)
- **`resolve_effective_schema` is NOT missing.** It exists ([wren_runtime.py:39](semantic_layer/wren_runtime.py)) and its
  test suite **passes (12 tests)** — the prior "missing/drift" note is stale (it shipped as
  "AI SQL schema inference"). **However**, its computed full schema set is **discarded** at the
  only call site (§4, F6).
- **`SchemaIndex` is NOT "cross-schema correct."** It is a **dual map**: schema-*aware* methods are
  correct, schema-*blind* methods collide (§1). "Graceful degrade" is the bug, not a feature.

---

## 1. Shared foundation — `SchemaIndex` (the root cause)

[`SchemaIndex`](semantic_layer/mdl_validator.py#L46-L191) carries **two** representations of the physical catalog:

| Field | Schema-qualified? | Carries | Cross-schema behavior |
|---|---|---|---|
| `tables`, `column_types` (flat) | ❌ | names **+ types** | same-named tables across schemas **overwrite** — [L73,80](semantic_layer/mdl_validator.py#L73): `tables[table]=…` keyed by bare name |
| `tables_by_schema` | ✅ | names **only — no types** ([L83](semantic_layer/mdl_validator.py#L83)) | both kept |

**Method-level verdict** (this determines every downstream tool):

| Method | Reads | Verdict |
|---|---|---|
| `has_table(t, schema)`, `has_column(t, c, schema)`, `columns_for(t, schema)`, `search(q, schema)` | `tables_by_schema` when `schema` passed | ✅ correct |
| `to_tables()`, `typed_tables()` | flat `tables`/`column_types` | ❌ no qualification + collision |
| `column_type(t, c)` | flat `column_types` (no `schema` param) | ❌ wrong type on collision |

Two structural facts that constrain any fix:
1. **No per-schema type map exists.** `tables_by_schema` is names-only; types live only in the
   collidable flat `column_types`. A correct cross-schema type lookup needs a *new* `types_by_schema`.
2. **The persisted snapshot is single-schema** ([SchemaSnapshot.schema_name: str](semantic_layer/schema_snapshot.py#L45), flat `tables`),
   and the live fetch is per-schema ([get_context/get_full_schema take one `schema_name`](context/superset_metadata.py#L43-L118)).
   The copilot index loops schemas itself ([app.py:1786](app.py#L1786)); on a Superset outage the multi-schema
   index degrades to schema-blind ([app.py:1797](app.py#L1797)).

**The access/scope layer is multi-schema-correct** and is *not* part of the gap: `schema_names`
list, per-schema permission proof, union context ([access.py:116-174](semantic_layer/access.py#L116)); scope-hash adds the set
([store.py:225](semantic_layer/store.py#L225)). The gap is purely physical-schema *surfacing/typing*, never authorization.

---

## 2. MDL Copilot tools (17)

The toolset ([copilot/tools.py](semantic_layer/copilot/tools.py)) is handed a `SchemaIndex` built multi-schema via
[`_schema_index_for_project`](app.py#L1779) (unions every `project.schema_names`). So `tables_by_schema` *is*
populated; the question is whether each tool reads the qualified or the flat map.

| Tool | Schema-dependent? | Verdict | Evidence |
|---|---|---|---|
| `list_mdl_files` | no | ➖ | returns paths+status |
| `read_mdl_file` | no | ➖ | returns stored JSON |
| `write_mdl_file` | yes (validation) | ⚠️ | validates via `validate_mdl`; existence checks schema-aware, type check blind (§3) |
| `patch_mdl_file` | yes (validation) | ⚠️ | same as write |
| `remove_mdl_entity` | no | ➖ | name-keyed |
| `delete_mdl_file` | no | ➖ | whole-file by path |
| `validate_project` | yes | ⚠️ | `has_table/has_column(…, schema)` ✅; `column_type` type-check ❌ (§3) |
| **`get_physical_schema`** | **yes (it *is* the schema)** | **❌ F1** | [tools.py:876-879](semantic_layer/copilot/tools.py#L876) returns flat `to_tables()`/`typed_tables()` — no schema qualification, same-name collision |
| `find_tables` | yes | ✅ (names) / ⚠️ (types) | [`_find_tables`](semantic_layer/copilot/tools.py) uses `search(q, schema)`, tags each candidate with `schema`; but its per-column `column_type` ([L907](semantic_layer/copilot/tools.py#L907)) is blind (F2) |
| `propose_onboard_table` | yes | ⚠️ | `has_table(t, schema)`/`columns_for(t, schema)` ✅; generated column **types** via `column_type` ([L1045](semantic_layer/copilot/tools.py#L1045)) ❌; relies on agent already knowing the schema |
| `propose_onboard_tables` | yes | ⚠️ | same, batched |
| `propose_relationships` | no | ➖ | model names only — cross-schema joins are just two model names |
| `list_documents` | no | ➖ | project-scoped docs |
| `search_documents` | no | ➖ | doc RAG |
| `read_document` | no | ➖ | doc text |
| `find_duplicate_documents` | no | ➖ | doc dedup |
| `run_coverage` | no | ➖ | docs vs MDL |

**The one tool the agent treats as authoritative — `get_physical_schema` — is the mis-scoped one.**
The prompt calls it "authoritative; never reference anything absent from it," yet for a cross-schema
project it (a) can't tell the agent *which schema* a table is in (so it can't author a correct
`tableReference.schema` for a new model) and (b) silently hides one of any same-named pair.
`find_tables` is the correct pattern and already does it right — it is the fix template for F1.

---

## 3. MDL generation / enrichment (non-agentic LLM authoring)

| Path | Verdict | Evidence |
|---|---|---|
| Validator existence checks | ✅ | `has_table(table, schema)` / `has_column(table, physical_name, schema)`, schema from the model's `tableReference` ([mdl_validator.py:519,557,642](semantic_layer/mdl_validator.py#L519); `_physical_schema_of_model` [L1050](semantic_layer/mdl_validator.py#L1050)) |
| Validator type-mismatch check | ❌ F2 | `_type_mismatch_message` → `column_type(table, lookup)` schema-blind ([mdl_validator.py:688,699](semantic_layer/mdl_validator.py#L688)) — false/missed `column_type_mismatch` on collision |
| Document-enrichment LLM grounding | ❌ F4 | [app.py:3779-3783](app.py#L3779) passes flat `to_tables()`/`typed_tables()` → wren LLM client builds `from_snapshot(schema, types)` ([llm_client.py:208](integrations/wren/llm_client.py#L208)) and sends `physical_schema` to the model — **schema-blind, no qualification** |
| Snapshot persistence | ❌ F3 | [app.py:1806](app.py#L1806) stores `index.to_tables()` (flat) → outage fallback rebuilds schema-blind |

---

## 4. AI SQL agent pipeline (LangGraph: `graph.py` one-shot, `conversation_graph.py` multi-turn)

| Node / step | Verdict | Evidence & note |
|---|---|---|
| **schema inference** (`resolve_effective_schema`) | ✅ shipped, ⚠️ lossy | [wren_runtime.py:39-83](semantic_layer/wren_runtime.py#L39) — project-wins primary inference, DB-guarded; test passes (12). **F6:** its full `schema_names` is **discarded** at [L117](semantic_layer/wren_runtime.py#L117): `schema_name, _ = resolve_effective_schema(...)` |
| **load_context** (physical datasets) | ❌ F5 | [conversation_graph.py:799-838](conversation_graph.py#L799), [graph.py:370-399](graph.py#L370) → `get_context(schema_name=scope.schema_name)` — **one schema**; secondary-schema *physical* tables never enter the candidate set |
| **load_wren_context** (MDL materialize) | ✅ | `materialize_wren_project(project=…)` gets full `project.schema_names`; manifest `semanticProject.schemas` = full set ([wren_materializer.py:79](semantic_layer/wren_materializer.py#L79)) |
| **apply_join_closure** | ✅ | [runtime.py:105-176](semantic_layer/runtime.py#L105) — relationship partners crossing the selected/unselected boundary are pulled in (cross-schema join survival); partial mitigation for F5 *for modeled relationship partners only* |
| **draft_sql** | ⚠️ | data is multi-schema (datasets ∪ MDL); prompt gives **no** multi-schema framing (§5) |
| **dry_plan / plan_semantic_sql** | ✅ | [engine/planning.py:76-99](semantic_layer/engine/planning.py#L76) compiles all active MDL files; validates against full manifest model set |
| **validate_sql** | ➖ | read-only policy + LIMIT; schema-agnostic |
| **repair_sql** | ✅ | feeds errors back; context stays multi-schema |
| **semantic→native rewrite** (wren-core) | ✅ | `plan_sql` rewrites model refs → **schema-qualified** native SQL using `tableReference.schema` ([wren_core_engine.py:82-123](semantic_layer/engine/wren_core_engine.py#L82)) |
| **_matched_models** (ranking) | ⚠️ | [wren/client.py:319-344](integrations/wren/client.py#L319) boosts models whose table is in the (single-schema) `context.datasets`; secondary-schema models lose the boost (code comment acknowledges) — mitigated by join-closure |
| **execute_sql** | ⚠️ F7 | [graph.py:799-804](graph.py#L799), [conversation_graph.py:1342-1346](conversation_graph.py#L1342) pass a single scalar `schema_name` (DB search_path hint). ✅ for engine-qualified SQL; ❌ for **unqualified LLM-direct/passthrough SQL** referencing a secondary schema |
| **build_artifacts** | ➖ | post-execution |
| scope-hash / memory | ✅ | [store.py:225](semantic_layer/store.py#L225) hashes the full set |
| access / permission | ✅ | [access.py:116-174](semantic_layer/access.py#L116) proves each schema, unions context |

**Net for the SQL agent:** the engine *can* execute cross-schema joins, but the LLM's **physical
grounding is single-schema** (F5) and the resolver's full set is **thrown away** (F6) — so a
secondary-schema table that isn't already an MDL relationship partner is invisible at draft time,
and a passthrough query against it fails at execution (F7).

---

## 5. Prompts & skills

| File | Verdict | Key line |
|---|---|---|
| `prompts/wren_onboarding.md` | ❌ single-schema-assumed | L11 "permission-filtered datasets of **one schema**" |
| `prompts/text_to_sql.md` | ⚠️ silent | L24 "use the relationships…to choose join keys" — correct abstraction, no cross-schema framing |
| `prompts/conversation.md` | ⚠️ silent | L26-29,43 relationships-for-joins; no cross-schema note |
| `prompts/sql_reflection.md` | ⚠️ silent | L24-34 error taxonomy has no "schema-qualification" category |
| `prompts/table_selection.md` | ⚠️ silent | candidate models by name; no schema disambiguation |
| `prompts/mdl_copilot.md` | ⚠️ field-aware, narrative-silent | respects `tableReference`; no "a project may span schemas" statement; no cross-schema example |
| `skills/generate-mdl.md` | ⚠️ field-aware | documents `tableReference {catalog?, schema, table}`; **all examples use `"public"`**, no cross-schema relationship example |
| `skills/onboarding.md` | ✅ tool-aware | L91 "`propose_onboard_tables` (cross-schema in one call)"; L65 "don't guess across schemas" is workflow discipline, not a limit |
| `skills/enrich-context.md` | ➖ | semantics-only; structure-preserving |
| `prompts/wren_enrichment.md`, `coverage_*.md` | ➖ | structure-preserving / audit-only ("this schema" phrasing is harmless) |
| `wren_upstream_skills/wren_langchain_prompt.py` | ✅ abstraction-correct | L43,64,177 "target MDL model names…engine translates to the dialect" — **why** schema-qualification is absent from agent output (engine-side) |
| `copilot/loop.py build_system_prompt` | ⚠️ | injects **no** cross-schema preamble even when the project spans schemas |

**Theme:** the *tools/structure* are cross-schema-capable; the *prose* never tells the model a
project can span schemas or that relationships/joins can cross them. The one actively-wrong line is
`wren_onboarding.md:11` ("one schema").

---

## 6. Consolidated findings (severity-ranked)

| # | Finding | Where | Severity | Status |
|---|---|---|---|---|
| **F1** | `get_physical_schema` is schema-blind (no qualification + same-name collision) — the agent's "authority" tool | [tools.py](semantic_layer/copilot/tools.py) `_get_physical_schema` | **High** (copilot authoring) | ✅ SHIPPED 2026-06-30 — emits `{schemas:{schema:{table:{columns,types}}}}` when multi-schema |
| **F5** | SQL-agent physical context fetches one schema — LLM blind to secondary-schema tables at draft | [superset_metadata.py](context/superset_metadata.py) + graph load_context | **High** (SQL agent) | ✅ SHIPPED (parallel — `plan_cross_schema_query_time_impl.md`, union scan) |
| **F6** | `resolve_effective_schema`'s full `schema_names` is computed then discarded | [wren_runtime.py:117](semantic_layer/wren_runtime.py#L117) | **High** (enables F5 fix) | ✅ SHIPPED (parallel — full set threaded) |
| **F2** | `column_type()` schema-blind → wrong onboarded types + false/missed type-mismatch on collision | [mdl_validator.py](semantic_layer/mdl_validator.py) `column_type`; [tools.py](semantic_layer/copilot/tools.py) find_tables/onboard | **Medium** | ✅ SHIPPED 2026-06-30 — `column_type(t,c,schema)` + `types_by_schema`; threaded into validator/onboard/find_tables |
| **F4** | Document-enrichment LLM grounded on flat schema | [app.py](app.py) `_enrichment_proposal` → [llm_client.py](integrations/wren/llm_client.py) | **Medium** | ✅ SHIPPED 2026-06-30 — validation via F2 + re-validation; `schema_by_schema` grounding param through WrenClient |
| **F7** | `execute_sql` pins single schema → passthrough/unqualified SQL to a secondary schema fails | [graph.py](graph.py), [conversation_graph.py](conversation_graph.py) | **Medium** (engine path safe) | ✅ SHIPPED (parallel — prompt schema-qualification) |
| **F3** | Snapshot is single-schema/flat → multi-schema degrades on outage | [schema_snapshot.py](semantic_layer/schema_snapshot.py), [app.py](app.py) | **Low** (outage only) | ✅ SHIPPED 2026-06-30 — `tables_by_schema` column (migration 0014) + outage-fallback qualified |
| **F8** | Prompts never frame cross-schema; `wren_onboarding.md:11` said "one schema" | §5 | **Low** (cognition) | ✅ SHIPPED — SQL prompts (parallel) + onboarding/mdl_copilot/generate-mdl (2026-06-30) |

**As-built (2026-06-30, this clone):** F1/F2/F2b/F3/F4/F8-copilot. Foundation = `SchemaIndex.types_by_schema`
(per-schema types) + qualified rendering methods (`schema_qualified_view`, `to_tables_by_schema`,
`typed_tables_by_schema`, `is_multi_schema`). Gating decision: the new qualified shapes activate **only
when `len(schemas) > 1`**, so single-schema scopes and their tests are untouched. Tests: SchemaIndex/validator
(test_multi_schema_validation), get_physical_schema (test_copilot_tools), snapshot round-trip + outage
(test_schema_snapshot), enrichment helper (test_llm_wren_client). Full suite 1077 pass / 1 known parallel-WIP
fail (bulk-activate cache count). F5/F6/F7/F8-sql shipped by the parallel query-time work.

**Not findings (verified correct):** access/permission, scope-hash/memory, MDL compile, semantic→native
rewrite, join-closure, project resolution, `resolve_effective_schema` logic itself.

---

## 7. Fix templates & existing plans

- **F1/F2/F3/F4** (physical-schema surfacing): the fix is to make `get_physical_schema` and the flat
  consumers schema-qualified, and add a `types_by_schema` map; `find_tables` is the in-repo template.
- **F5/F6/F7** (SQL-agent query-time): covered by `plan_cross_schema_query_time_impl.md` (5 phases) —
  thread the full set from `resolve_effective_schema` (F6) into a union `get_context` (F5) and
  schema-aware execution (F7). Related: `plan_cross_schema_context_ranking_impl.md` (`_matched_models`
  + join-closure, partly shipped), `plan_multi_schema_mdl_spec.md`.
- **F8** (prompts): mechanical prompt edits (the cheapest, highest-cognition-leverage change).

No code was modified by this audit.
