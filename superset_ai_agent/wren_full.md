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

# Wren Full-Parity Implementation Plan

This is the **execution checklist** for bringing full Wren feature parity (or
governed equivalents) to `superset_ai_agent`, structured so any later session
can pick it up as a checklist. It is the successor to [`wren.md`](wren.md)
(design/governance) and [`wren_model.md`](wren_model.md) (the first modeling
increment). Where they conflict, the **governance invariants below win**, then
this document, then the earlier two.

Status legend: `[TODO]` not started · `[WIP]` in progress · `[COMPLETE]`
source-backed and test-verified · `[BLOCKED]` waiting on a decision/dependency.

---

## Progress Summary (audited 2026-06-23)

Audit method: every "Completed" row below was re-verified against the working
tree on this date — file/symbol existence, the cited test, and the suites:
backend `pytest tests/unit_tests/superset_ai_agent` → **281 passed, 3 skipped**
(the 3rd skip is the LanceDB `importorskip` round-trip); frontend
`jest src/SqlLab/components/AiAgentPanel` → **36 passed** (incl. 6 `AuditInfoPanel`
tests); `ruff` + `prettier` clean on changed files. (`oxlint` and
project `tsc` could not run in this environment — native-binding fault / full-repo
cost — they run in CI.) Paths are relative to `superset_ai_agent/`; tests live
under `../tests/unit_tests/superset_ai_agent/`. The detailed per-phase write-ups
and residual-risk IDs (`R**`) referenced here are unchanged below.

### ✅ Completed (source-backed)

| # | Item | Evidence (source) | Evidence (test) |
| --- | --- | --- | --- |
| 1 | Durable semantic persistence baseline; parity features fail closed on `memory` (0.0) | [`config.py`](config.py) `semantic_layer_store`/`conversation_store="sqlalchemy"` (L86–87); [`app.py`](app.py) `_validate_semantic_persistence_config` | `test_persistence_baseline.py` |
| 2 | MDL compile canonicalization — single camelCase manifest source (0.3) | [`mdl_compile.py`](semantic_layer/mdl_compile.py) `compile_manifest` | `test_mdl_compile.py` |
| 3 | SemanticEngine seam + `WrenCoreEngine` rewrite; degrades closed (1.1) | [`semantic_layer/engine/`](semantic_layer/engine/) (`base.py`, `passthrough.py`, `wren_core_engine.py`, `factory.py`) | `test_semantic_engine.py` (incl. live rewrite, skipif-gated) |
| 4 | Engine wired into both graphs' execution path + audit provenance (1.2/1.5) | [`graph.py`](graph.py) / [`conversation_graph.py`](conversation_graph.py) `plan_semantic_sql`; [`schemas.py`](schemas.py) `AuditInfo.{semantic_sql,native_sql,engine}` | `test_graph_semantic_engine.py` |
| 5 | Semantic-SQL prompt mode (flag-gated) (1.3) | [`config.py`](config.py) `wren_semantic_sql_enabled`; `_SEMANTIC_SQL_GUIDANCE` in both graphs | `test_graph_semantic_engine.py::test_semantic_sql_mode_*` |
| 6 | Embedder + Retriever seams (keyword default, embedding optional) (2.1/2.2) | [`llm/embeddings.py`](llm/embeddings.py); [`semantic_layer/schema_retriever.py`](semantic_layer/schema_retriever.py) | `test_schema_retriever.py` |
| 7 | Memory learning loop (durable NL→SQL examples) (2.3) | [`semantic_layer/memory_store.py`](semantic_layer/memory_store.py); migration [`0003_nl_sql_examples`](persistence/migrations/versions/0003_nl_sql_examples.py) | `test_memory_store.py` |
| 8 | **Retriever seam consumed by both graphs (RV2)** | `retrieve_mdl_context` in [`schema_retriever.py`](semantic_layer/schema_retriever.py); called in both graphs' `_load_wren_context`; `WrenContextArtifact.retrieval_mode` | `test_seam_wiring.py::test_retrieve_mdl_context_*` |
| 9 | **Conversation-graph memory write-back + recall (RV4)** | [`conversation_graph.py`](conversation_graph.py) `_execute_sql` (store) + `_draft_response` (recall) | `test_seam_wiring.py::test_conversation_memory_*` |
| 10 | Cubes/metrics first-class in schema + compile (Phase 3) | [`mdl_schema.py`](semantic_layer/mdl_schema.py) `MdlMetric`/`MdlCube`; [`mdl_compile.py`](semantic_layer/mdl_compile.py) | `test_mdl_compile.py::test_compile_manifest_maps_metrics_and_cubes` |
| 11 | **Metric/cube structural validation (RM1)** | [`mdl_validator.py`](semantic_layer/mdl_validator.py) `_validate_metrics`/`_validate_cubes` | `test_mdl_validator.py` (7 new) |
| 12 | **wren-core CI engine job + multi-model-join golden (RE1)** | [`.github/workflows/superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml) | `test_semantic_engine.py::test_wren_core_rewrites_multi_model_join` |
| 13 | Intent classification + Skills (Phase 4) | [`intent.py`](intent.py); [`skills/`](skills/) | `test_intent_and_skills.py` |
| 14 | **Intent classifier wired into conversation graph as a gated hint (RO1)** | [`conversation_graph.py`](conversation_graph.py) `_classify_intent` node; [`config.py`](config.py) `wren_intent_classification_enabled` (default off) | `test_seam_wiring.py::test_intent_classification_*` |
| 15 | **SemanticPipeline facade — deterministic plan-and-execute core (4.1)** | [`semantic_layer/pipeline.py`](semantic_layer/pipeline.py) | `test_semantic_pipeline.py` (3) |
| 16 | Dependencies: `wren-core-py` hard dep; `psycopg` documented for prod | [`requirements-ai-agent.txt`](../requirements-ai-agent.txt) (L36, L47) | n/a (CI installs wheel — RE1) |
| 17 | **Cube dimension/time-dimension/hierarchy structural validation (RM1a)** | [`mdl_validator.py`](semantic_layer/mdl_validator.py) `_validate_named_entries` | `test_mdl_validator.py::test_cube_dimension_*` (3) |
| 18 | **Gated engine-feedback correction loop, one-shot graph (1.4)** | [`graph.py`](graph.py) `_correct_semantic_sql` + `correct_semantic_sql` node; [`config.py`](config.py) `wren_engine_max_correction_retries` (default 0); [`planning.py`](semantic_layer/engine/planning.py) `correctable_warnings` | `test_graph_semantic_engine.py::test_engine_correction_*` (2) |
| 19 | **Intent routing short-circuit, conversation graph (RO1a)** | [`conversation_graph.py`](conversation_graph.py) `_answer_directly` + `_route_after_intent`; [`config.py`](config.py) `wren_intent_routing_enabled` (default 0) | `test_seam_wiring.py::test_intent_routing_*` (2) |
| 20 | **Frontend surfacing: engine + semantic/native SQL + retrieval mode (RV3)** | [`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx) (friendly labels + engine/retrieval badges); [`api.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts) (`AuditInfo.engine/semantic_sql/native_sql`, `WrenContextArtifact.retrieval_mode`) | `AuditInfoPanel.test.tsx` |
| 21 | **Memory-reuse signal end-to-end + "Reused N learned example(s)" badge (RV3)** | `WrenContextArtifact.recalled_example_count` stamped in both graphs' draft nodes ([`graph.py`](graph.py) / [`conversation_graph.py`](conversation_graph.py)); badge in [`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx) | `test_graph_semantic_engine.py` (count) + `AuditInfoPanel.test.tsx` (2) |
| 22 | **Memory write-back dedup (RV4a)** | [`memory_store.py`](semantic_layer/memory_store.py) `_dedup_key`; `InMemoryMemory`/`SqlAlchemyMemory` refresh-in-place on repeat | `test_memory_store.py::test_*_dedup*` (3) |
| 23 | **Ephemeral-store warning: "models not persisted" UI banner (RV3)** | [`schemas.py`](schemas.py) + [`app.py`](app.py) `HealthResponse.semantic_layer_persistent`; warning `Alert` in [`index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx) | `test_app.py::test_health_*` |
| 24 | **Memory decay: per-scope example cap (evict oldest) (RV4a)** | [`memory_store.py`](semantic_layer/memory_store.py) `max_examples` + `_evict_old`; [`config.py`](config.py) `wren_memory_max_examples` (default 200) | `test_memory_store.py::test_*_decay_*` (2) |
| 25 | **Deeper cube semantics: granularity + hierarchy-level resolution (RM1a)** | [`mdl_validator.py`](semantic_layer/mdl_validator.py) `_validate_cube_semantics` (warning-only) | `test_mdl_validator.py::test_cube_*granularity*` / `*hierarchy*` (4) |
| 26 | **Engine correction loop in the conversation graph (1.4, symmetric)** | [`conversation_graph.py`](conversation_graph.py) `_correct_semantic_sql` + `correct_semantic_sql` node; gated by `wren_engine_max_correction_retries` (default 0) | `test_seam_wiring.py::test_conversation_engine_correction_*` (2) |

### ⏳ Pending / deferred (with tracking ID)

These four remaining items are **larger/structural or stretch** and warrant a
scoping decision before being picked up (the functional parity surface is
complete; these are refactors, optional installs, or experiments):

| # | Item | Status | Tracking / note |
| --- | --- | --- | --- |
| A | Seams package consolidation + `SeamBundle` factory (0.1–0.2, 0.4) | `[TODO]` | **Structural/refactor, zero behavior change.** The six bindings exist and work under `engine/`, `schema_retriever.py`, `memory_store.py`, `llm/embeddings.py`; consolidating into `semantic_layer/seams/` is cosmetic and risks churn for no functional gain. Lowest value. |
| D | Full graph→pipeline **drafting inversion** (graphs become thin callers) | `[TODO]` | **Large refactor, real drift risk.** RO2a — the `SemanticPipeline` facade exists (#15) but takes semantic SQL as input; inverting the LangGraph drafting loop into the pipeline is a multi-day rework of the keystone graphs. |
| E | LangChain / Pydantic-AI framework adapters (4.4) | `[TODO]` | **Stretch / optional install.** RO2b — thin tool wrappers around the pipeline; out of the default install. Value depends on whether external framework embedding is a real requirement. |
| I | Throwaway A/B spike vs. upstream Wren mesh | `[TODO]` | **Experiment, not shippable code.** De-risking exercise; a one-off comparison, not an implementation task. |

The **MDL retrieval & embedding system** reached full parity (R1–R4) on
2026-06-23: items are embedded once per manifest checksum (warm queries embed only
the question), an optional LanceDB persistent index degrades closed,
`embedder_dimensions` is sent + validated, a model change forces a reindex, and the
badge reflects the effective retriever + chunk count. See
[Phase 2.4 — MDL Retrieval & Embedding Full Parity](#phase-24--mdl-retrieval--embedding-full-parity-plan-added-2026-06-23)
for the gap closure table and residual risks (chiefly **R-RET-A**: the LanceDB
native path is unverified locally and must be checked in CI, and **R-RET-B**:
LanceDB persists vectors but does not yet do native ANN search). This subsumes the
old RV2a "LanceDB vector index" TODO.

### ⚠️ Known limitation (new finding)

- **RE1b** — wren-core 0.7.1 in embedded mode (no registered data source) raises
  an internal `CSV error: No such file or directory` on **relationship-traversal**
  auto-joins (a calculated column like `customer.region` the LLM references
  without writing the join). Explicit multi-model joins **do** rewrite (golden
  test #12); the engine degrades closed on the error. Re-verify on a wren-core
  upgrade. Source: [`semantic_layer/engine/wren_core_engine.py`](semantic_layer/engine/wren_core_engine.py)
  `_degraded`; characterized in `test_semantic_engine.py`.

### Open decision

- **OQ1** — semantic-SQL authoring rollout (engine-on by default vs. per-project
  opt-in). Recommendation stands: ship behind `wren_engine`, flip per-project
  after the golden tests + a real-schema A/B. See [Phase 1](#phase-1--semanticengine-the-keystone).

---

## How to use this document

1. Work **top-down by phase**. Phase 0 unblocks everything; do not start a later
   phase before its predecessor's acceptance criteria pass.
2. Each workstream has: **Parity target** (what Wren does), **Requirements**,
   **Design spec** (protocols + files), **Config**, **Tests**, **Acceptance**.
3. Tick the checklist boxes as you land each item. Update the Status table at the
   top of each workstream and the roll-up table in
   [Implementation Status](#implementation-status).
4. Keep every new Python file ASF-licensed and type-hinted; run
   `pre-commit run --all-files` before pushing (mypy/ruff/pylint/eslint/prettier).

---

## Locked Decisions (do not relitigate without sign-off)

These were decided with the product owner and frame the entire plan:

1. **Execution boundary = Superset-only.** wren-core *rewrites* semantic SQL into
   native SQL; **Superset SQL Lab REST remains the only executor**, behind
   `validate_read_only_sql` and Superset RBAC/RLS/audit. We do **not** adopt
   `ibis-server` or Wren connectors.
2. **Coupling = embed wren-core, reimplement the rest behind seams.** We depend
   on `wren-core` (PyPI) for the semantic engine — the one cleanly separable,
   high-value Wren component — and reimplement retrieval/memory/modeler/
   orchestrator over our own seams, borrowing **building-block libraries**
   (LanceDB for vectors/memory) rather than wrapping Wren's bundled
   `/v1/asks` pipeline or the `wrenai` SDK (which assume Wren-owned execution and
   would re-introduce the governance tension).

Rationale and the full Wren analysis live in the conversation that produced this
plan; the short version: Wren only exposes **one** cleanly-wrappable API
(`wren-core`); everything else exists only inside a bundled pipeline that owns
execution. So we borrow the engine and own the glue.

## Key Deliverables — `[COMPLETE]` (2026-06-22)

1. **Wren fully enabled & ready.** `wren-core-py` 0.7.1 is a hard dependency
   ([`requirements-ai-agent.txt`](../requirements-ai-agent.txt)); the API was
   verified against the live engine and corrected (`SessionContext(mdl_base64,
   data_source=<dialect>)` loads the manifest directly — the prior
   `to_manifest()`/two-arg usage was wrong). `wren_engine="wren_core"` and
   `wren_core_validation_enabled=True` are the **defaults**. Verified: model→
   physical-table rewrite, calculated-column generation, and deep-validation
   rejection of a typeless column. Degrades to passthrough only when a query
   backend has no wren-core dialect (e.g. sqlite) or no MDL exists.
2. **Persistence fully enabled across restarts.** `conversation_store` and
   `semantic_layer_store` default to `"sqlalchemy"` ([`config.py`](config.py)),
   so MDL, conversations, and the materialized manifest survive restarts out of
   the box (sqlite at `./.data/ai_agent.db`, auto-migrated). Verified by the
   restart-survival test (0.0) and the parity-enforcement pairing.

Tests/dev opt into `memory` + `passthrough` explicitly. Full suite: **234 passed,
2 skipped**. **This resolves RE1** (the engine is installed, verified, and on).

---

## Governance Invariants (carry-over + new)

Carried from [`wren.md`](wren.md) and [`wren_model.md`](wren_model.md):

- [ ] `SupersetClient.execute_sql` is the **only** SQL execution boundary used by
      the agent graphs. No seam may execute SQL.
- [ ] No Wren execution method on any client; `WREN_EXECUTION_ENABLED=true` still
      fails startup ([`integrations/wren/factory.py`](integrations/wren/factory.py)).
- [ ] Generated/onboarded/enriched MDL is written `status="draft"`; never
      auto-activated, never auto-materialized.
- [ ] Documents, MDL, retrieval results, and memory examples are **context, not
      permission sources**; Superset RBAC via `SemanticAccessService` stays
      authoritative.
- [ ] All generated SQL — semantic or native — still passes
      `validate_read_only_sql` before execution.

New invariants introduced by this plan:

- [ ] **The engine rewrites; Superset executes.** `SemanticEngine.plan_sql`
      output (native SQL) is the *only* thing handed to the executor; the raw
      LLM "semantic SQL" is never executed directly when the engine is active.
- [ ] **Every seam degrades closed.** Absent `wren-core` → passthrough engine +
      structural validation only. Absent embedder/LanceDB → keyword retrieval.
      Absent memory store → no few-shot, never an error.
- [ ] **Physical references must resolve in Superset.** A manifest
      `tableReference` must map to objects the requesting user can see in
      Superset; the engine rewrite is validated against the permission-filtered
      `SchemaIndex` before execution.
- [ ] **Audit records both SQLs.** `AuditInfo` carries `semantic_sql`,
      `native_sql`, and `engine` so every executed query is traceable to the
      model that wrote it and the engine that rewrote it.

---

## Target Architecture: Six Seams

Decompose today's coarse `WrenClient` protocol
([`integrations/wren/client.py`](integrations/wren/client.py)) into six narrow,
independently-swappable seams that mirror Wren's real component boundaries. Each
seam has a zero-dependency default binding (so the service always starts) and a
parity binding.

```
   NL question
        │
        ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │  Orchestrator  (graph.py today → SemanticPipeline + Skills)       │
 │   intent → retrieve → context → draft semantic SQL → plan → exec  │
 └─────────────────────────────────────────────────────────────────┘
     │          │            │             │           │          │
 ┌───▼───┐ ┌────▼────┐ ┌─────▼─────┐ ┌─────▼────┐ ┌────▼────┐ ┌───▼─────┐
 │Retriev│ │Modeler  │ │Semantic   │ │Executor  │ │Memory   │ │Embedder │
 │er     │ │(LLM MDL)│ │Engine     │ │(Superset)│ │(history)│ │(vectors)│
 │       │ │         │ │(wren-core)│ │          │ │         │ │         │
 │keyword│ │LlmModel-│ │WrenCore / │ │Superset- │ │Sqla /   │ │OpenAI / │
 │/embed │ │er       │ │passthrough│ │RestClient│ │LanceDB  │ │none     │
 └───────┘ └─────────┘ └───────────┘ └──────────┘ └─────────┘ └─────────┘
```

| Seam | Parity target (Wren) | Default binding | Parity binding | New? |
| --- | --- | --- | --- | --- |
| **SemanticEngine** | `wren-core.transform_sql` | `PassthroughEngine` | `WrenCoreEngine` | mostly new |
| **Retriever** | LanceDB `schema_items` embedding search | `KeywordRetriever` (exists) | `EmbeddingRetriever` | extend |
| **Memory** | LanceDB `query_history` learning loop | `NullMemory` | `SqlAlchemyMemory` / `LanceDbMemory` | new |
| **Modeler** | `generate-mdl` + `semantics-description` | `DeterministicModeler` | `LlmModeler` (exists) | refactor |
| **Executor** | ibis-server connectors | `SupersetRestClient` (exists) | same | formalize |
| **Embedder** | provider embedders | `NullEmbedder` | `OpenAi/Azure/OllamaEmbedder` | new |

---

## Current-State Baseline (verified 2026-06-22)

What exists today, per seam, so later sessions know what they are refactoring vs.
building.

| Seam | Today | Source |
| --- | --- | --- |
| Engine | wren-core used for **validation only**; import-guarded, off by default; snake→camelCase mapping exists | [`semantic_layer/wren_core_validator.py`](semantic_layer/wren_core_validator.py) |
| Engine (manifest) | hand-rolled snake_case MDL spec + structural/physical validator | [`semantic_layer/mdl_schema.py`](semantic_layer/mdl_schema.py), [`semantic_layer/mdl_validator.py`](semantic_layer/mdl_validator.py) |
| Retriever | keyword token-overlap scoring + token-budget trim | [`semantic_layer/retrieval.py`](semantic_layer/retrieval.py), [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py)`::fetch_context` |
| Memory | read-only `recall_examples` over a static `memory.json`; **no write-back** | [`integrations/wren/client.py`](integrations/wren/client.py)`::FileWrenClient.recall_examples` |
| Modeler | LLM onboarding + doc enrichment, deterministic fallback | [`integrations/wren/llm_client.py`](integrations/wren/llm_client.py) |
| Executor | Superset SQL Lab REST, read-only validated | [`integrations/superset/rest.py`](integrations/superset/rest.py), [`tools/sql.py`](tools/sql.py) |
| Embedder | **none** — `ModelClient` exposes `chat` only | [`llm/base.py`](llm/base.py) |
| Orchestrator | LangGraph one-shot + conversation graphs; injects MDL context into the SQL prompt | [`graph.py`](graph.py), [`conversation_graph.py`](conversation_graph.py) |
| Materialize | merge active YAML → project `mdl.json` (camelCase envelope) | [`semantic_layer/wren_materializer.py`](semantic_layer/wren_materializer.py) |
| Client selection | adapter factory (`llm`/`http`/`file`/disabled) | [`integrations/wren/factory.py`](integrations/wren/factory.py) |

**Key constraints discovered:**

- `ModelClient` has **no embedding method** → an `Embedder` seam must be added and
  retrieval must degrade to keyword when none is configured.
- MDL is **snake_case internally** but the engine needs **camelCase**; this is the
  R9/R16 seam. The plan canonicalizes by treating YAML as authoring source and
  compiling to a camelCase `mdl.json` manifest (exactly Wren's model).
- The LLM currently writes **raw physical SQL**; the engine needs **semantic SQL**
  written against MDL logical models. This is the one behavioral change with a
  real fallback story (see Workstream 1, OQ1).

### Supporting Infrastructure Readiness (verified 2026-06-22)

Non-Wren, Wren-*supporting* infrastructure — checked before building so parity
work doesn't sit on ephemeral foundations.

| Area | State | Detail |
| --- | --- | --- |
| MDL DB persistence | **Present but OFF by default** | `AiAgentSemanticMdlFile` table + `SqlAlchemyMdlFileStore` exist; default `semantic_layer_store="memory"` ([`config.py`](config.py)) means MDL is **in-memory, lost on restart**. |
| Project/doc/version/snapshot persistence | Present but OFF by default | One flag governs **all** semantic stores (`_create_semantic_layer_store` / `_create_semantic_project_store` / `_create_mdl_file_store` / `_create_schema_snapshot_store` in [`app.py`](app.py) all branch on `semantic_layer_store`). |
| ORM models | **Complete** | [`persistence/models.py`](persistence/models.py): conversations, messages, artifacts, documents, updates, versions, wren-context cache, events, projects, grants, access proofs, schema snapshots, jobs, **MDL files**. |
| Migrations | **Complete to date** | `0001_initial_agent_tables`, `0002_schema_snapshots_and_jobs` ([`persistence/migrations/versions/`](persistence/migrations/versions/)). |
| DB engine/session/bootstrap | Present | `agent_database_url` (default `sqlite:///./.data/ai_agent.db`), `agent_run_migrations=True`, `_requires_agent_database` builds the engine only in `sqlalchemy` mode. |
| Identity guard | Present | `_validate_identity_persistence_config` rejects DB persistence with static identity unless explicitly overridden — flipping to `sqlalchemy` requires a real identity provider. |
| Requirements (DB) | Present | `SQLAlchemy`, `alembic`, `boto3` (S3 docs) in [`requirements-ai-agent.txt`](../requirements-ai-agent.txt). **Missing for prod Postgres:** a driver (`psycopg`). |
| Requirements (parity deps) | Missing | `wren-core` (commented), `lancedb`, embedder provider — added per phase below. |
| UI | **Substantial, modeling-ready** | `SemanticLayerEditor/index.tsx` (~46KB), `api.ts` (~27KB), `SemanticLayerImportDialog`, onboarding/enrich/materialize wired ([`SemanticLayerEditor/`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/)). **Missing:** engine status, semantic-vs-native SQL, retrieval-mode + memory surfacing (Phase 4 follow-on). |

**Conclusion:** the DB layer is built and tested; the gap is purely that
persistence is **opt-in and off by default**. Parity features depend on durable
MDL (engine cache, retrieval index, and memory loop all key off the materialized
manifest), so **Phase 0 makes DB-backed semantic persistence the baseline**
(0.0 below) before any seam work.

---

## Implementation Status

| Phase | Workstream | Status |
| --- | --- | --- |
| 0 | Durable semantic persistence baseline (MDL in DB) | `[COMPLETE]` (2026-06-22) |
| 0 | MDL compile canonicalization (0.3) | `[COMPLETE]` (2026-06-22) |
| 0 | Seam refactor + default bindings (0.1–0.2, 0.4) | `[TODO]` |
| 1 | SemanticEngine seam (protocol + passthrough + wren-core scaffold) | `[COMPLETE]` (2026-06-22) |
| 1 | SemanticEngine graph wiring + audit (both graphs) | `[COMPLETE]` (2026-06-22) |
| 1 | Semantic-SQL prompt mode (1.3) | `[COMPLETE]` (2026-06-22) |
| 1 | Engine-feedback correction loop, both graphs (1.4) | `[COMPLETE]` (2026-06-23, gated, default off) |
| 2 | Embedder + Retriever seam (keyword default, embedding optional) | `[COMPLETE]` (2026-06-22) |
| 2 | Retriever seam consumed by both graphs (RV2) | `[COMPLETE]` (2026-06-23) |
| 2 | Memory learning loop (NL→SQL examples) | `[COMPLETE]` (2026-06-22) |
| 2 | Conversation-graph memory write-back + recall (RV4) | `[COMPLETE]` (2026-06-23) |
| 2.4 | MDL retrieval & embedding full parity (R1–R4: index lifecycle, LanceDB, embedder hardening, observability) | `[COMPLETE]` (2026-06-23) |
| 3 | MDL completeness (cubes/metrics) | `[COMPLETE]` (2026-06-22) |
| 3 | Metric/cube structural validation (RM1) | `[COMPLETE]` (2026-06-23) |
| 3 | Cube dimension/hierarchy shape + granularity/level semantics (RM1a) | `[COMPLETE]` (2026-06-23) |
| 3 | wren-core deep-validation CI job (RE1) | `[COMPLETE]` (2026-06-23) |
| 4 | Intent classification + Skills | `[COMPLETE]` (2026-06-22) |
| 4 | Intent classifier wired into conversation graph (RO1, gated hint) | `[COMPLETE]` (2026-06-23) |
| 4 | Intent routing short-circuit, conversation graph (RO1a, gated) | `[COMPLETE]` (2026-06-23) |
| 4 | SemanticPipeline facade (4.1, deterministic core) | `[COMPLETE]` (2026-06-23) |
| 4 | Framework adapters (4.4, LangChain/Pydantic-AI) | `[TODO]` (deferred) |
| 2 | Memory dedup + decay (RV4a) | `[COMPLETE]` (2026-06-23) |
| Cross | Frontend surfacing: engine + semantic/native SQL + retrieval mode + memory badge + persistence warning (RV3) | `[COMPLETE]` (2026-06-23) |

---

## Phase 0 — Seam Refactor + MDL Compile Canonicalization

**Goal:** make semantic state durable, then introduce the six protocols and
rebind current behavior to them with **zero behavior change**, and split MDL into
*authoring YAML* (snake_case, human) vs *compiled manifest* (camelCase,
engine-ready). Unblocks every later phase.

### 0.0 Durable semantic persistence baseline (do first) — `[COMPLETE]` (2026-06-22)

**Resolved.** Parity seams now fail closed unless durable persistence is on, and
MDL is proven to survive a restart. Source:
[`config.py`](config.py) (`wren_engine` / `wren_retriever` / `wren_memory_store` /
`wren_memory_learning_enabled` + env wiring `WREN_ENGINE` / `WREN_RETRIEVER` /
`WREN_MEMORY_STORE` / `WREN_MEMORY_LEARNING_ENABLED`);
[`app.py`](app.py) (`_parity_features_enabled`, `_validate_semantic_persistence_config`
called in `create_app`, `_requires_agent_database` now also triggers on
`wren_memory_store="sqlalchemy"`, plus a startup `logger.info` of the effective
persistence mode); [`.env.example`](.env.example) (new keys + embedder block).
Tests: [`test_persistence_baseline.py`](../tests/unit_tests/superset_ai_agent/test_persistence_baseline.py)
(7) — defaults legal; each parity feature rejects `memory`; allowed with
`sqlalchemy`; memory-store triggers the DB; **MDL create→activate→reopen-DB
survives restart**. Suite: **190 passed, 1 skipped**; `ruff` clean.

**Residual risk RP1:** enforcement is **config-based**, not instance-based —
`_validate_semantic_persistence_config` keys off `semantic_layer_store`, so a
caller that injects a durable store object into `create_app(...)` while leaving
`semantic_layer_store="memory"` would still be rejected (and vice-versa, an
injected in-memory store with `=sqlalchemy` would pass). This is intentional
(config is the operator contract) but is a dev-expectation gap worth a docstring
note when the seam factory lands in 0.4.

**Why first:** MDL is in-memory by default (`semantic_layer_store="memory"`), so
models vanish on restart. Every parity seam keys off a **durable, materialized
manifest** — the engine's compiled-`SessionContext` cache (1.1), the LanceDB
retrieval index (2.2), and the memory learning loop (2.3) all become meaningless
if MDL is ephemeral. The DB layer already exists; this workstream makes it the
baseline. No new tables for *existing* state — wiring + defaults only.

- [ ] Default `semantic_layer_store` to `sqlalchemy` for any deployment using
      Wren parity features. Options (pick per `app.py`/config review): (a) flip
      the default to `sqlalchemy`; or (b) keep `memory` as the dev default but
      **hard-require** `sqlalchemy` whenever `wren_engine != passthrough` or
      `wren_memory_learning_enabled` — fail startup with a clear message
      otherwise. Recommendation: (b) — explicit, no silent ephemerality.
- [ ] Confirm the single `semantic_layer_store` flag covers **all** semantic
      stores (it does today: layer/project/mdl-file/snapshot). If any parity seam
      adds state (memory, retrieval-index metadata), route it through the same
      flag + `session_factory` so one switch governs durability.
- [ ] Verify migrations cover the MDL-file + project + snapshot tables end-to-end
      against a fresh `sqlite` and (in CI) a Postgres DB; add the `psycopg`
      driver to a deployment requirements file for Postgres (see Dependencies).
- [ ] Respect the identity guard: DB persistence + static identity is rejected by
      `_validate_identity_persistence_config`. Document that enabling persistence
      requires `superset_session` or `signed_header` identity (already enforced).
- [ ] Add a startup log line stating the effective persistence mode + DB URL so
      operators can see whether MDL is durable.
- [ ] **MDL durability test:** create → activate → materialize an MDL file with
      `semantic_layer_store=sqlalchemy`, restart the app (new process / new store
      instance on the same DB), and assert the file + project + materialized
      manifest survive.

### 0.1 Seam protocols

- [ ] Create `superset_ai_agent/semantic_layer/seams/__init__.py` exporting the
      six protocols. Suggested module layout:
  ```text
  semantic_layer/seams/engine.py      # SemanticEngine, CompiledManifest, PlannedSql
  semantic_layer/seams/retriever.py   # Retriever, SchemaItem
  semantic_layer/seams/memory.py      # Memory, NlSqlPair
  semantic_layer/seams/modeler.py     # Modeler
  semantic_layer/seams/executor.py    # Executor (thin alias over SupersetClient)
  llm/embeddings.py                   # Embedder, NullEmbedder
  ```
- [ ] Define protocols (signatures are the contract; keep them minimal):
  ```python
  class SemanticEngine(Protocol):
      def compile(self, mdl_files: list[MdlFile]) -> CompiledManifest: ...
      def validate(self, manifest: CompiledManifest, *, deep: bool = False,
                   schema_index: SchemaIndex | None = None) -> MdlValidationResult: ...
      def plan_sql(self, semantic_sql: str, manifest: CompiledManifest, *,
                   dialect: str) -> PlannedSql: ...        # native_sql + referenced_tables

  class Retriever(Protocol):
      def index(self, manifest: CompiledManifest, *, scope_key: str) -> None: ...
      def retrieve(self, question: str, *, scope_key: str,
                   k: int) -> list[SchemaItem]: ...

  class Memory(Protocol):
      def recall_examples(self, question: str, *, scope_key: str,
                          owner_id: str, k: int) -> list[NlSqlPair]: ...
      def store_confirmed(self, *, question: str, semantic_sql: str,
                          native_sql: str, scope_key: str, owner_id: str,
                          result_meta: dict[str, Any]) -> None: ...

  class Modeler(Protocol):
      def generate_base_model(self, *, project, superset_context
                              ) -> list[MdlEnrichmentProposal]: ...
      def propose_mdl_from_document(self, *, project, document
                              ) -> MdlEnrichmentProposal: ...

  class Embedder(Protocol):
      def embed(self, texts: list[str]) -> list[list[float]]: ...
      def dimensions(self) -> int: ...
  ```
- [ ] `CompiledManifest`, `PlannedSql`, `SchemaItem`, `NlSqlPair` as Pydantic
      models in the seam modules.
- [ ] **Back-compat:** keep `WrenClient` as a deprecated facade that delegates to
      the new seams (so `graph.py`/`app.py` keep working during migration); mark
      with a `# DEPRECATED: decomposed into seams` comment and a follow-up ticket
      to remove. Do **not** delete `integrations/wren/` in Phase 0.

### 0.2 Default bindings (no behavior change)

- [ ] `PassthroughEngine` (`seams/engine.py`): `compile` = call the existing
      materializer/compile step; `validate` = delegate to
      `mdl_validator.validate_project_manifest`; `plan_sql` = return
      `PlannedSql(native_sql=semantic_sql, referenced_tables=...)` **unchanged**
      with an `info` warning "semantic rewrite skipped (engine=passthrough)".
- [ ] `KeywordRetriever` (`seams/retriever.py`): wrap the logic in
      [`retrieval.py`](semantic_layer/retrieval.py) + `llm_client._rank_models`.
- [ ] `NullMemory`, `NullEmbedder`: no-ops returning `[]` / raising on `embed`.
- [ ] `DeterministicModeler` / `LlmModeler`: lift the methods out of
      `FileWrenClient`/`LlmWrenClient` unchanged.
- [ ] `SupersetExecutor`: thin Protocol alias documenting that execution stays in
      `SupersetClient.execute_sql`.

### 0.3 MDL compile canonicalization (closes R9/R16) — `[COMPLETE]` (2026-06-22)

**Resolved.** Authoring YAML stays snake_case; a single compile step produces the
canonical camelCase engine manifest, and the deep-validation mapping now shares
that exact code path so they cannot drift.

- [x] MDL files remain **authoring YAML** (snake_case, unchanged on disk).
- [x] Added [`semantic_layer/mdl_compile.py`](semantic_layer/mdl_compile.py)
      `compile_manifest(...) -> CompiledManifest` — the **single source of
      camelCase truth** (`tableReference`, `joinType`, `isCalculated`, `refSql`,
      `primaryKey`, plus `views`/`metrics`/`dataSource`). `CompiledManifest`
      carries `to_engine_manifest()` and `to_base64_json()` (exactly wren-core's
      `to_manifest` input — the Phase-1 seam consumes this directly).
- [x] Reconciled [`wren_core_validator.py`](semantic_layer/wren_core_validator.py)
      `to_wren_core_manifest` to **delegate** to `mdl_compile`'s shared mappers
      (`model_to_camel` / `relationship_to_camel`), removing the duplicate
      mapping (R9/R16).
- [x] [`wren_materializer.py`](semantic_layer/wren_materializer.py) now also
      writes the canonical `manifest.json` (camelCase, via `compile_manifest`)
      **additively** beside the readable `mdl.json`, mirroring Wren's YAML→
      compiled-`mdl.json` model. The existing `mdl.json` (consumed by
      `fetch_context`) is intentionally left as the readable merged view so the
      LLM-context shape and the materializer test are unchanged.

Tests: [`test_mdl_compile.py`](../tests/unit_tests/superset_ai_agent/test_mdl_compile.py)
(5) — snake→camel mapping, multi-file merge order, base64 round-trip, malformed
YAML skipped, and **delegation parity** (validator output == compile output).
Suite: **195 passed, 1 skipped**; `ruff` clean on changed files.

**Residual risk RC1:** `mdl_exporter.py` still emits its own envelope and was
**not** rerouted through `compile_manifest` (it is unwired today — only used by
the deterministic onboarding fallback). Reroute it when the Modeler seam lands
(Phase 4) to fully retire ad-hoc casing. Low impact: its model bodies are
already snake_case and pass through the same validator.

**Residual risk RC2:** `compile_manifest` merge is **append-only** (later files
add models; no override/dedupe). Two active files defining the same model name
yield duplicate models in the manifest — the structural validator's
duplicate-name check (R1) catches this at activation, but the compile step itself
does not dedupe. Acceptable; revisit if multi-file projects become common.

### 0.4 Factory + wiring

- [ ] New `semantic_layer/seams/factory.py::build_seams(config, *, model_client,
      embedder, session_factory, mdl_file_store) -> SeamBundle` returning all six
      bindings chosen by config, degrading closed.
- [ ] `app.py::create_app` constructs the `SeamBundle` once and injects it into
      `TextToSqlGraph` and `ConversationGraph` (replacing the
      `create_wren_client(...)` call site).

**Config (0):** `wren_engine: Literal["passthrough","wren_core"] = "passthrough"`;
`wren_retriever: Literal["keyword","embedding"] = "keyword"`;
`wren_memory_store: Literal["none","sqlalchemy","lancedb"] = "none"`.

**Tests (0):** seam protocol conformance tests; `compile_manifest` golden
(snake→camel) test; assert graphs produce identical output to pre-refactor on a
fixture (snapshot). **Acceptance:** full existing suite green, no behavior change.

---

## Phase 1 — SemanticEngine (the keystone)

**Parity target:** `wren-core` parses the MDL manifest and `transform_sql`
expands models into CTEs, resolves calculated columns, and turns relationships
into real joins — producing native SQL the source DB runs. This is the single
highest-value piece of Wren and the line between *Wren-shaped* and *Wren-grade*.

### 1.1 `WrenCoreEngine` — `[COMPLETE]` (2026-06-22)

**Resolved (seam + scaffold).** The `SemanticEngine` seam exists with both
bindings; the wren-core rewrite path is implemented and degrades closed when the
optional engine is absent. Graph wiring (1.2) and prompt/correction (1.3–1.5)
remain `[TODO]`.

Source:
- [`semantic_layer/engine/base.py`](semantic_layer/engine/base.py) —
  `SemanticEngine` Protocol, `PlannedSql`, `BACKEND_TO_WREN_DIALECT` +
  `resolve_dialect`, `extract_referenced_tables` (sqlglot-based, best-effort, for
  the physical-resolution gate).
- [`semantic_layer/engine/passthrough.py`](semantic_layer/engine/passthrough.py)
  — default binding: `compile` → `compile_manifest`; `plan_sql` returns SQL
  unchanged + warning; no deep validation.
- [`semantic_layer/engine/wren_core_engine.py`](semantic_layer/engine/wren_core_engine.py)
  — `plan_sql` resolves dialect → injects `dataSource.type` → `to_manifest(
  manifest.to_base64_json())` → `SessionContext.transform_sql`; **degrades to the
  input SQL with a warning** when wren-core is absent, dialect is unknown, or the
  rewrite raises. `validate(deep=True)` uses the new
  `wren_core_validator.validate_engine_manifest` (operates on the already-compiled
  camelCase manifest — fixes a snake/camel double-mapping bug).
- [`semantic_layer/engine/factory.py`](semantic_layer/engine/factory.py) —
  `create_semantic_engine(config)` selects by `wren_engine`.

Tests: [`test_semantic_engine.py`](../tests/unit_tests/superset_ai_agent/test_semantic_engine.py)
(8 + 1 skipif) — dialect map, table extraction (joins + bad SQL), factory
defaults, passthrough no-rewrite, **wren-core absent-degrade**, unknown-dialect
degrade; the real `transform_sql` rewrite is `skipif`-gated until wren-core is
installed (1.2 CI job). Suite: **203 passed, 2 skipped**; `ruff` clean.

**~~Residual risk RE1~~ — RESOLVED (2026-06-22):** wren-core-py 0.7.1 is
installed and the rewrite path is verified end-to-end (model→physical rewrite,
calculated columns, deep-validation rejection). The API was corrected:
`SessionContext(mdl_base64, data_source=<dialect>)` — the manifest loads directly
and the dialect is the constructor arg (not a manifest field). `wren_engine=
wren_core` is now the default. **New finding RE1a:** wren-core requires a `type`
on every column and supports a fixed dialect set (postgres/bigquery/snowflake/
mysql/duckdb/clickhouse/trino/mssql/redshift/databricks/oracle/athena/spark/
datafusion/…); **sqlite is NOT supported**, so sqlite-backed query sources
degrade to passthrough (no rewrite). Re-verify `BACKEND_TO_WREN_DIALECT` on a
wren-core upgrade.

**Residual risk RE2:** `extract_referenced_tables` is sqlglot best-effort; CTE
aliases and quoted identifiers may under/over-report. It feeds a *gate* (fail
toward structural-only on parse failure), not execution, so it cannot cause wrong
SQL — only an over-strict or skipped physical check.

The detailed sub-tasks below remain the spec for the graph-wiring work (1.2+):

#### Original 1.1 spec (retained for the wiring work)

- [x] `semantic_layer/engine/wren_core_engine.py::WrenCoreEngine(SemanticEngine)`:
  ```python
  manifest = to_manifest(base64(json(compiled_manifest)))   # already proven in wren_core_validator
  ctx = SessionContext(manifest, [])
  native_sql = ctx.transform_sql(semantic_sql)              # ← the parity unlock
  ```
- [ ] `validate` delegates to the existing deep path
      ([`wren_core_validator.validate_with_wren_core`](semantic_layer/wren_core_validator.py))
      **merged with** the always-on structural/physical validator.
- [ ] `plan_sql` returns `PlannedSql(native_sql, referenced_tables, warnings)`;
      extract `referenced_tables` for the physical-resolution check (invariant).
- [ ] Cache compiled `SessionContext` per `(project_id, materialized_checksum)`
      to avoid recompiling per request (addresses R7-style cost).
- [ ] **Dialect:** map `project.database_backend` → wren-core source/dialect and
      thread it into the manifest `dataSource` so `transform_sql` targets the
      right native dialect (Postgres/BigQuery/Snowflake/…). Maintain an explicit
      `BACKEND_TO_WREN_DIALECT` table; unknown backend → passthrough + warning.

### 1.2 Graph wiring (engine in the execution path) — `[COMPLETE]` (2026-06-22)

**Resolved (both graphs).** The engine is wired into the live query path of the
one-shot and conversation graphs; passthrough (default) is a verified no-op, and
a fake rewriting engine proves native SQL reaches Superset execution + audit.

Source:
- [`semantic_layer/engine/planning.py`](semantic_layer/engine/planning.py) —
  shared `plan_semantic_sql_step` (rewrite + soft hallucination gate) and
  `with_engine_provenance` (audit stamping), so both graphs use one tested path.
- [`graph.py`](graph.py) — `plan_semantic_sql` node between `dry_plan_with_wren`
  and `validate_sql`; `repair_sql → plan_semantic_sql` re-plans repaired drafts;
  `semantic_engine` ctor arg (defaults from `create_semantic_engine(config)`);
  audit provenance in `_build_artifacts`.
- [`conversation_graph.py`](conversation_graph.py) — same node/edges; rewrites
  `draft.sql` in place; audit provenance applied to both the artifact copy and
  state.
- [`schemas.py`](schemas.py) — `AuditInfo.semantic_sql` / `native_sql` / `engine`.

Tests:
[`test_graph_semantic_engine.py`](../tests/unit_tests/superset_ai_agent/test_graph_semantic_engine.py)
(2 — rewrite reaches execution + audit; passthrough no-op) and
[`test_conversation_graph.py`](../tests/unit_tests/superset_ai_agent/test_conversation_graph.py)::
`test_conversation_graph_engine_rewrite_reaches_execution_and_audit`. Suite:
**206 passed, 2 skipped**; `ruff` clean.

**Residual risk RG1 (gap vs. plan intent):** the physical-resolution gate is a
**soft warning, not a hard pre-execution block.** The plan called for failing
before execution on an unknown table; because wren-core's rewritten SQL is full
of CTE names (false positives) and the engine isn't live-verified, the gate runs
on the **semantic** SQL against known model + dataset names and only *warns*.
Hardening to a block is deferred until the wren-core CI job (RE1) lands and we
can distinguish physical tables from expanded CTEs.

**Residual risk RG2 (dev-expectation gap):** the engine rewrite is only
meaningful once the LLM writes **semantic** SQL against MDL models — that prompt
change is **1.3 (still TODO)**. With the default passthrough engine and today's
physical-SQL prompt, this wiring is inert by design (zero behavior change). The
rewrite path is proven only via a fake engine + skipif'd wren-core test.

#### Original 1.2 spec (retained)

- [x] Add a node `plan_semantic_sql` between `draft_sql` and `validate_sql` in
      [`graph.py`](graph.py)`::_compile_graph` (and the conversation graph):
      `semantic_sql → engine.plan_sql → native_sql`.
- [ ] Feed `native_sql` into the **existing** `validate_read_only_sql` then the
      Superset executor — **unchanged**. (Invariant: engine rewrites, Superset
      executes.)
- [ ] Validate `PlannedSql.referenced_tables` against the request's
      `SchemaIndex` (reuse `SchemaIndex.from_agent_context` /
      `from_snapshot`); a reference Superset can't see → fail before execution
      with a clear error (not a Superset 500).
- [ ] On engine error, route to the correction loop (1.4); if `engine ==
      passthrough`, behave exactly as today.

### 1.3 / 1.4 Semantic-SQL prompt + correction loop — `[COMPLETE]` (2026-06-22)

**Resolved.** When `wren_semantic_sql_enabled` is on **and** the engine is not
passthrough, both graphs inject `_SEMANTIC_SQL_GUIDANCE` into the model payload
(write SQL against MDL models; the engine rewrites to native). Engine warnings
from the plan step are folded into the repair prompt (1.4).

Source: [`config.py`](config.py) (`wren_semantic_sql_enabled` +
`WREN_SEMANTIC_SQL_ENABLED`, `wren_memory_recall_k`); [`graph.py`](graph.py) /
[`conversation_graph.py`](conversation_graph.py) (`_SEMANTIC_SQL_GUIDANCE`,
`semantic_sql_mode` in `_call_sql_model` / `_call_conversation_model`,
`engine_warnings` state folded into `_repair_sql`); [`.env.example`](.env.example).
Tests: [`test_graph_semantic_engine.py`](../tests/unit_tests/superset_ai_agent/test_graph_semantic_engine.py)
(`test_semantic_sql_mode_injects_authoring_guidance`, `_off_by_default`). Suite:
**208 passed, 2 skipped**; `ruff` clean.

**Residual risk RG3 (dev expectation):** the prompt guidance is **prose only** —
the dedicated `prompts/text_to_sql.md` / `conversation.md` files were not split
into a separate semantic-SQL variant; the directive rides the user payload
instead. Functionally equivalent and simpler, but a reviewer expecting a prompt
file diff won't find one. Effective only with `wren_engine=wren_core` (RE1), so
unverified end-to-end against a live model writing real semantic SQL.

**~~Residual risk RG4~~ — ADDRESSED for the one-shot graph (2026-06-23):** a
gated engine-feedback correction loop now exists. `plan_semantic_sql_step` splits
the **correctable** hallucination-gate warning (unknown model/table) from
non-correctable degrade reasons (`PlanStepResult.correctable_warnings`); when
`wren_engine_max_correction_retries > 0`, [`graph.py`](graph.py)'s
`_route_after_validation` routes a *valid* draft that references a hallucinated
table to `_correct_semantic_sql` (bounded re-draft, per-attempt trace event)
before executing. **Dev-intent divergences:** (a) default is **0 (off)**, not the
plan's 2 — the gate is best-effort (RE2 over/under-reporting), so opt-in avoids
spurious re-drafts; (b) implemented in the **one-shot graph only** — the
conversation graph keeps its result-driven reflection loop; (c) it never fires
without a materialized MDL (empty manifest → no gate). Tests:
`test_graph_semantic_engine.py::test_engine_correction_*`.

#### Original 1.3 spec (retained)

- [ ] Update [`prompts/text_to_sql.md`](prompts/text_to_sql.md) and
      `prompts/conversation.md`: instruct the model to write SQL **against MDL
      logical models/columns** (referencing relationship-qualified and calculated
      columns by their MDL names), not physical tables, when the engine is
      active. Provide the model/column catalog from the retriever.
- [ ] Provide a flag-driven prompt variant: when `engine == passthrough`, keep the
      current physical-SQL instructions (models map 1:1 to tables, so logical ≈
      physical and nothing breaks).

### 1.4 SQL correction loop (parity with Wren's dry-run → correction)

- [ ] On `plan_sql` / `validate` failure, feed the engine error back to the model
      (bounded retries, reuse the conversation graph's reflection machinery) to
      regenerate semantic SQL. Mirrors Wren's `sql_correction` pipeline.
- [ ] Cap retries via existing retry config; emit `TraceEvent`s for each attempt.

### 1.5 Audit — `[COMPLETE]` (2026-06-22)

- [x] Extended `AuditInfo` ([`schemas.py`](schemas.py)) with `semantic_sql`,
      `native_sql`, `engine`; populated via `with_engine_provenance` in both
      graphs' `_build_artifacts`. (Stamped at artifact-build time, not
      `_execute_sql`, so the provenance rides the same record the UI reads.)

**Config (1):** `wren_engine="wren_core"` to enable; `wren_semantic_sql_enabled`
(prompt mode); `wren_engine_max_correction_retries: int = 2`. Add `wren-core` to
[`requirements-ai-agent.txt`](../requirements-ai-agent.txt) (currently commented).

**Tests (1):**
- [ ] Golden manifest: two models + a `MANY_TO_ONE` relationship + a calculated
      metric → assert `transform_sql` output **contains the join** the LLM never
      wrote. (`skipif` wren-core absent.)
- [ ] **CI job that installs `wren-core`** and runs the engine-present tests
      against a golden manifest (closes R16 — the unverified-manifest risk).
- [ ] Passthrough fallback parity (engine off → identical to Phase 0).
- [ ] `referenced_tables` physical-resolution failure → pre-execution error.
- [ ] Correction loop repairs an intentionally-bad first draft.

**Acceptance (parity litmus):** a question requiring a cross-model join + a
calculated metric produces an **executed** native SQL whose joins were generated
by the engine, not the LLM — verified end to end through Superset.

> **OQ1 (open):** semantic-SQL authoring is the one behavioral change. Decide the
> rollout: (a) engine-on by default once parity tests pass, or (b) per-project
> opt-in while models are still mostly 1:1 table mappings. Recommendation: ship
> behind `wren_engine` flag, default `passthrough`, flip per-project after the
> golden tests + a real-schema A/B.

---

## Phase 2 — Retrieval (embeddings) + Memory (learning loop)

### Phase 2 status — `[COMPLETE]` (2026-06-22)

**Embedder + Retriever seams + Memory learning loop landed.** Default bindings
(keyword retrieval, null/in-memory) keep the service unchanged; the embedding
and durable-memory paths are config-gated and degrade closed.

Source:
- [`llm/embeddings.py`](llm/embeddings.py) — `Embedder` protocol, `NullEmbedder`,
  `OpenAiEmbedder` (reuses `OPENAI_*`), `create_embedder`.
- [`semantic_layer/schema_retriever.py`](semantic_layer/schema_retriever.py) —
  `Retriever` protocol, `manifest_to_schema_items` (model/column/relationship
  chunks ≈ Wren `schema_items`), `KeywordRetriever` (default), `EmbeddingRetriever`
  (in-memory cosine over the embedder, degrades to keyword), `create_retriever`.
- [`semantic_layer/memory_store.py`](semantic_layer/memory_store.py) — `Memory`
  protocol, `NlSqlPair`, `NullMemory`/`InMemoryMemory`/`SqlAlchemyMemory`,
  `create_memory`; owner+scope isolation; recall = token-overlap rank.
- [`persistence/models.py`](persistence/models.py)::`AiAgentNlSqlExample` +
  migration [`0003_nl_sql_examples`](persistence/migrations/versions/0003_nl_sql_examples.py).
- Graph wiring: [`graph.py`](graph.py) recalls examples into the prompt
  (`_draft_sql`/`_call_sql_model`) and writes back confirmed pairs on successful
  execution (`_execute_sql`); [`app.py`](app.py) builds the memory store via
  `create_memory(..., session_factory)` and injects it.
- Config/env: `wren_retriever`, `embedder_*` (`AI_AGENT_EMBEDDER_*`),
  `wren_memory_store`, `wren_memory_learning_enabled`, `wren_memory_recall_k`,
  `wren_lancedb_path`; [`.env.example`](.env.example); commented `lancedb` dep.

Tests: [`test_schema_retriever.py`](../tests/unit_tests/superset_ai_agent/test_schema_retriever.py)
(9 — chunking, keyword/embedding ranking, degrade-closed, factories),
[`test_memory_store.py`](../tests/unit_tests/superset_ai_agent/test_memory_store.py)
(6 — store/recall, **owner+scope isolation**, **cross-instance persistence via
migration**, factory gating),
[`test_graph_semantic_engine.py`](../tests/unit_tests/superset_ai_agent/test_graph_semantic_engine.py)::
`test_memory_writeback_and_recall_round_trip` (execute → store → recall into the
next prompt). Suite: **224 passed, 2 skipped**; `ruff` clean.

**Residual risk RV1 (dev expectation vs impl):** the plan specified **LanceDB**
for both retrieval and memory; the implementation uses **in-memory cosine** for
embedding retrieval and the **SQLAlchemy** table for durable memory. Functionally
equivalent at small/medium scale and dependency-free, but: (a) embedding
retrieval recomputes embeddings per request (no persistent vector index — RV1a),
and (b) memory recall is **token-overlap**, not vector similarity (RV1b). LanceDB
is the deferred optimization for wide schemas / semantic recall; the seams accept
it as a drop-in.

**~~Residual risk RV2~~ — RESOLVED (2026-06-23):** the `Retriever` seam is now
consumed by **both graphs**. `retrieve_mdl_context`
([`schema_retriever.py`](semantic_layer/schema_retriever.py); named to avoid
collision with the legacy dataset-ranking `retrieval.retrieve_schema_context`)
compiles the project's active MDL → `SchemaItem` chunks → ranks with the
configured retriever and appends the top-k to `WrenContextArtifact.context_items`
(plus a `retrieval_mode` stamp) in `_load_wren_context` of [`graph.py`](graph.py)
and [`conversation_graph.py`](conversation_graph.py). Degrades closed (no project /
no store / no active MDL / any error → `[]`), so the legacy `retrieval.py`
keyword context path is untouched when the retriever has nothing to add. The
older `retrieval.py` overlay still also runs; the two are additive.
Tests: [`test_seam_wiring.py`](../tests/unit_tests/superset_ai_agent/test_seam_wiring.py)
(`test_retrieve_mdl_context_*`). **Residual RV2a:** the embedding path still
recomputes embeddings per request (no persistent LanceDB index — see RV1a).

**~~Residual risk RV3~~ — partially RESOLVED (2026-06-23):** the AI panel audit
collapsible now surfaces an `Engine: …` badge, friendly `Semantic SQL`/`Native
SQL` labels, and a `Retrieval: …` badge (from `WrenContextArtifact.retrieval_mode`)
— [`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx),
typed in [`api.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts),
test `AuditInfoPanel.test.tsx`. **Still open:** a "answered from a learned
example" memory badge — the backend exposes no per-response recall-reuse flag, so
there is nothing for the UI to bind to yet.

**~~Residual risk RV4~~ — RESOLVED for conversation-graph write-back
(2026-06-23):** memory write-back is now wired into the **conversation graph**
([`conversation_graph.py`](conversation_graph.py)`::_execute_sql`) and recall
into the draft prompt (`_draft_response` → `recalled_examples` in the payload),
mirroring the one-shot graph. Approved-SQL turns are excluded from write-back
(their message is not a natural-language question). Tests:
[`test_seam_wiring.py`](../tests/unit_tests/superset_ai_agent/test_seam_wiring.py)
(`test_conversation_memory_writeback_and_recall_round_trip`). **~~Residual RV4a~~
— dedup RESOLVED (2026-06-23):** write-back now dedups on the normalized
(question, native_sql) key — a repeat refreshes the existing example in place
rather than accumulating (`memory_store.py::_dedup_key`; both stores; +3 tests).
**Decay (2026-06-23):** a per-scope **count cap** now evicts the oldest examples
past `wren_memory_max_examples` (default 200) in both stores (`max_examples` +
`_evict_old`; +2 tests). **Still open:** (a) cap is count-based, not age/TTL-based;
(b) successful auto-execute turns still auto-confirm (a noisier signal than Wren's
explicit confirmation); (c) the SqlAlchemy dedup/evict scans recent rows per write
(bounded, single-worker-scale per R-C).

### 2.1 Embedder seam

**Parity target:** Wren embeds schema chunks and questions with a configurable
embedder.

- [ ] `llm/embeddings.py::Embedder` (Protocol) + provider impls mirroring the
      existing LLM providers ([`llm/`](llm/)): `OpenAiEmbedder`, `AzureEmbedder`,
      `OllamaEmbedder`, `NullEmbedder`.
- [ ] `llm/factory.py` (or a sibling) builds the embedder from config; missing
      config → `NullEmbedder` and the retriever factory falls back to keyword.
- [ ] Validate `embedder_model` ↔ `embedder_dimensions` consistency at startup;
      `dimensions` is baked into the LanceDB table at index creation, so a
      mismatch (or a model change) must trigger a **reindex**, not a silent error.

**Config / env vars (assume OpenAI).** Naming follows the existing convention:
vendor creds use bare `OPENAI_*` (reused), agent-owned config uses `AI_AGENT_*`,
toggles use `WREN_*`. Minimal OpenAI setup is three vars
(`WREN_RETRIEVER=embedding`, `AI_AGENT_EMBEDDER_PROVIDER=openai`,
`OPENAI_API_KEY=…`); everything else defaults.

_Required to enable embedding retrieval:_

| Env var | Config field | Default | Purpose |
| --- | --- | --- | --- |
| `WREN_RETRIEVER` | `wren_retriever` | `keyword` | Set `embedding` to activate the embedder + LanceDB. |
| `AI_AGENT_EMBEDDER_PROVIDER` | `embedder_provider` | _unset_ | Set `openai`. Unset → `NullEmbedder` → auto-fallback to keyword. |
| `OPENAI_API_KEY` | `openai_api_key` (reused) | _unset_ | **Required.** Embedder falls back to this when `AI_AGENT_EMBEDDER_API_KEY` is unset. |

_Embedder tuning (optional, sane defaults):_

| Env var | Config field | Default | Purpose |
| --- | --- | --- | --- |
| `AI_AGENT_EMBEDDER_MODEL` | `embedder_model` | `text-embedding-3-small` | OpenAI embedding model. |
| `AI_AGENT_EMBEDDER_DIMENSIONS` | `embedder_dimensions` | `1536` | Vector size — **must match the model + LanceDB table**. `3-small`=1536, `3-large`=3072. |
| `AI_AGENT_EMBEDDER_API_KEY` | `embedder_api_key` | falls back to `OPENAI_API_KEY` | Override only if embeddings use a different key. |
| `AI_AGENT_EMBEDDER_BASE_URL` | `embedder_base_url` | falls back to `OPENAI_BASE_URL` | Override for Azure/proxy/self-hosted gateways. |
| `AI_AGENT_EMBEDDER_BATCH_SIZE` | `embedder_batch_size` | `128` | Batch size when indexing the manifest. |

_Supporting (index storage — needed when the retriever is on):_

| Env var | Config field | Default | Purpose |
| --- | --- | --- | --- |
| `WREN_LANCEDB_PATH` | `wren_lancedb_path` | `{AI_AGENT_STORAGE_DIR}/lancedb` | Where the vector index lives. |

- [ ] **Update `.env` examples:** add all of the above to
      [`superset_ai_agent/.env.example`](.env.example) (and any deployment
      `.envrc`/compose env templates) with the OpenAI defaults commented, so the
      embedding path is discoverable without reading this plan. This is a
      required sub-task of the Embedder seam, not a follow-on.

### 2.2 EmbeddingRetriever (LanceDB)

**Parity target:** LanceDB `schema_items` collection; embed question → top-k
relevant models/columns/relationships.

- [ ] `semantic_layer/retrieval/lancedb_retriever.py::EmbeddingRetriever(Retriever)`.
- [ ] `index(manifest, scope_key)`: chunk the **compiled manifest** into
      `SchemaItem`s (one per model, per column, per relationship — mirrors Wren's
      chunking), embed, upsert into a LanceDB table at
      `{agent_storage_dir}/lancedb/{scope_key}`.
- [ ] `retrieve(question, scope_key, k)`: embed question, vector-search, return
      `SchemaItem`s; feed into `WrenContextArtifact.context_items` (same shape the
      prompt already consumes — see `merge_indexed_semantic_context` in
      [`runtime.py`](semantic_layer/runtime.py)).
- [ ] **Index lifecycle:** rebuild on materialization, keyed by
      `materialized_checksum`; wire into the existing
      [`indexer.py`](semantic_layer/indexer.py)`::rebuild_index` + materializer so
      activation refreshes the index. Stale-checksum → lazy reindex.
- [ ] Keep `KeywordRetriever` as the default and the automatic fallback.

**Config:** `wren_retriever="embedding"`; `wren_lancedb_path` (default
`{agent_storage_dir}/lancedb`); reuse `wren_context_limit` /
`wren_schema_context_token_budget`.

**Tests:** chunking shape; index+retrieve round-trip (fake embedder with
deterministic vectors); fallback to keyword when embedder is `Null`; checksum
reindex.

### 2.3 Memory learning loop

**Parity target:** Wren's `query_history` collection — confirmed NL→SQL pairs
recalled as few-shot, improving over time.

- [ ] New table `ai_agent_nl_sql_examples` in
      [`persistence/models.py`](persistence/models.py) (owner-scoped, project/
      scope-scoped; columns: id, owner_id, project_id, scope_hash, question,
      semantic_sql, native_sql, result_meta JSON, created_at). Migration
      **`0003_nl_sql_examples`**.
- [ ] `SqlAlchemyMemory(Memory)` (+ `InMemoryMemory` for tests); optional
      `LanceDbMemory` for **semantic** recall (embed question, vector-search the
      examples) — gated on the embedder, else fall back to keyword recall.
- [ ] **Write-back hook:** after a successful, user-confirmed execution in
      `graph.py` / `conversation_graph.py`, call `memory.store_confirmed(...)`.
      Gate on execution success (and, for conversation mode, user acceptance);
      never store failed or unconfirmed SQL.
- [ ] **Recall:** inject top-k pairs as few-shot into the SQL prompt; replaces the
      vestigial `recall_examples`/`memory.json` path.

**Config:** `wren_memory_store="sqlalchemy"|"lancedb"`;
`wren_memory_learning_enabled: bool = True`; `wren_memory_recall_k: int = 3`.

**Governance:** examples are owner+scope scoped, are **context not permission**,
and must be excluded from cross-owner recall (filter by `owner_id` + `scope_hash`
like the existing stores).

**Tests:** store→recall round-trip; cross-owner isolation; learning improves a
fixture question's few-shot; write-back fires only on confirmed success.

---

## Phase 2.4 — MDL Retrieval & Embedding Full Parity (plan added 2026-06-23)

The seams (Embedder + Retriever) ship and are consumed by the graphs, but the
embedding path is **functional, not Wren-grade**: it re-embeds the whole schema
on every request and has no persistent index. This workstream closes the gap to
true parity. It supersedes the older [2.2](#22-embeddingretriever-lancedb) sketch.

### Status — `[COMPLETE]` R1–R4 (2026-06-23)

All four phases below landed. Source:
[`schema_retriever.py`](semantic_layer/schema_retriever.py) (index-lifecycle
`Retriever` protocol — `has_index`/`index`/`retrieve`/`effective_name`;
`_IndexEntry` cache; `EmbeddingRetriever` embeds items once per
`(scope_key, checksum#embedder-signature)`, query-only on warm path;
`LanceDbRetriever` import-guarded, degrades to in-process; `_content_checksum`);
[`llm/embeddings.py`](llm/embeddings.py) (`signature()`, `dimensions` passthrough
for `text-embedding-3-*`, `OllamaEmbedder`, soft dimension-consistency warning);
[`config.py`](config.py) (`wren_vector_index`); graphs stamp `retrieval_mode`
(effective) + `retrieved_item_count`; UI badge in
[`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx).
Tests: [`test_schema_retriever.py`](../tests/unit_tests/superset_ai_agent/test_schema_retriever.py)
(20 + 1 LanceDB `importorskip`) + `AuditInfoPanel.test.tsx` (6). Suite: **281
passed, 3 skipped**; `ruff`/`prettier` clean.

| Gap | Resolution | Status |
| --- | --- | --- |
| G1 (re-embed per request) | items embedded once per checksum; warm queries embed **only the question** (verified by call-count test) | **Closed** |
| G2 (no index lifecycle) | `has_index`/`index`/`retrieve` keyed by `(scope_key, checksum)`; reindex on change | **Closed** |
| G3 (per-request recompile) | `has_index` skips recompile + chunking on a warm checksum | **Closed** |
| G4 (`dimensions` inert) | sent to the API for `3-*` models; soft startup consistency warning | **Closed** |
| G6 (OpenAI-only) | `OllamaEmbedder` added (Azure via `base_url`) | **Closed** |
| G8 (no observability) | `retrieved_item_count` + effective `retrieval_mode` stamped + UI badge | **Closed** |
| G8a (badge can lie) | badge stamps the **effective** retriever (`keyword` on silent fallback) | **Closed** |
| R-RET4 (model-change corruption) | embedder `signature()` folded into the index key → forces reindex | **Closed** |
| G5 (two retrieval systems) | documented as an **interim additive contract** (dataset-level `WrenRetrievalArtifact` + MDL-level `context_items`); unify is a follow-up | **Documented, not unified** |
| G7 (embedder per request) | service-level graph caches across requests; per-request graph-build path is still cold (R2/LanceDB or an app singleton fixes) | **Partial** |

**Residual risks after R1–R4:**
- **~~R-RET-A — LanceDB native path unverified locally~~ — VERIFIED (2026-06-23).**
  `lancedb>=0.13` is now a hard dependency and installed; the round-trip test
  **runs and passes** against the real API. Verifying it immediately caught **two
  real bugs** the degrade-on-error wrapping had hidden — an invalid table name
  (`#`/`:` from the embedder signature) and `to_pandas()` needing `pylance` — both
  fixed (hash the table id; read via `to_arrow()`). See C1 below. *Residual:*
  confirm the wheel installs in CI, and add the loud-fallback `/health` signal so a
  future install/connect failure is visible rather than a silent in-memory no-op.
- **R-RET-B — LanceDB used as durable vector storage, not native ANN search.**
  `retrieve` rehydrates all rows and ranks with in-process cosine (reuses tested
  logic), so it avoids re-embedding after restart but does **not** realize
  ANN-at-scale; very wide schemas still load all vectors into RAM. A follow-up can
  switch cold retrieval to `table.search(qvec)`.
- **R-RET-C — embedding API round-trips unverified.** The OpenAI `dimensions`
  passthrough and Ollama path are tested via an injected fake client / construction
  only (no network). Live behavior runs in deployments with creds.
- **R-RET-D — in-process index is unbounded across scopes + cold per worker.** One
  entry per `(scope_key)` (latest checksum), never evicted; multi-worker prod
  re-embeds once per worker until LanceDB. Acceptable for single-worker; revisit
  with an LRU + app-level singleton.

**Dev-intent vs. actual-impl (resolved + residual):** the `Retriever` protocol now
matches the index-lifecycle spec (was inline-only); `embedder_dimensions` is now
load-bearing (was inert). Residual: "LanceDB" persists vectors but does not do
native ANN search (R-RET-B).

**User-intent vs. actual-UI:** the badge now reflects the **effective** retriever
and the chunk count, so it no longer misreports a silent keyword fallback (G8a).
Residual: no per-query embedding **cost** figure (only item count); index
freshness is not surfaced because retrieval lazily reindexes to current MDL before
every query, so a stale-index state is not user-visible by construction.

### Risk & Gap Closure Plan (R1–R4 follow-up, planned 2026-06-23)

Closes the residual risks above. **Ordering matters:** C1 gates C2 (don't build on
an unverified LanceDB API). C3/C4 are independent; C5 needs a product decision.

#### Implementation status — `[COMPLETE]` C1–C5 (2026-06-23)

Sequenced C1 → C4 → C1-health → C3 → C2 → C5. Suite: **290 passed, 4 skipped**
(2 wren-core inverse + lancedb-absent + opt-in live-smoke); `ruff`/`prettier`
clean; frontend AiAgentPanel **10 passed**.

| Item | Outcome | Source |
| --- | --- | --- |
| **C1** (R-RET-A) | lancedb installed + **hard dep**; round-trip runs; **caught 2 real bugs** (invalid `#`/`:` table name; `to_pandas` needs `pylance`) → fixed (hashed table id; `to_arrow`). | [`schema_retriever.py`](semantic_layer/schema_retriever.py), [`requirements-ai-agent.txt`](../requirements-ai-agent.txt) |
| **C1-health** | `/health.vector_index` = `memory \| lancedb \| memory_fallback` + startup `WARNING` + UI `Alert` on fallback. | `effective_vector_index`; [`app.py`](app.py); [`schemas.py`](schemas.py); [`index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx) |
| **C2** (R-RET-B) | cold path uses **native ANN** `table.search(qvec).metric("cosine").limit(k)` — no full rehydrate (verified `_mem` stays empty); cosine aligned with warm path. | `LanceDbRetriever.retrieve` |
| **C3** (R-RET-C) | contract tests: `dimensions` sent for `3-*`, omitted for ada/ollama, one vector/input across batches, empty→no call; opt-in live smoke `skipif(not OPENAI_API_KEY)`. | `test_schema_retriever.py` |
| **C4** (R-RET-D/G7) | embedder+retriever built **once in `create_app`**, injected into all 4 graph sites; in-process index is an **LRU** bounded by `wren_retriever_cache_scopes` (64); `get` race hardened. | [`app.py`](app.py); `_LruIndex` |
| **C5** (R-RET-E) | `cap_context_items` dedups + bounds merged context (`wren_max_context_items`=40), retrieval-ranked chunks win on overflow. Applied in both graphs. | [`runtime.py`](semantic_layer/runtime.py) |

**Residual / still-open after C1–C5:**
- **C1 CI:** the round-trip passes **locally**; confirm the lancedb wheel resolves on
  the CI runner (`ubuntu-24.04`) so it runs there too. A strict-fail mode
  (`WREN_VECTOR_INDEX_STRICT`) was **not** added — fallback is loud (health + log +
  UI) but still degrades rather than failing startup.
- **C2 dev-intent shift:** ANN is **approximate** — the warm path (exact in-process
  cosine) and cold path (LanceDB ANN) can rank differently for large indexes. Small
  fixtures agree; at scale recall < 1.0.
- **C4 thread-safety:** the shared LRU is mutated under request-thread concurrency;
  ops are GIL-atomic and `index()` is idempotent per checksum, and the `get`/
  `move_to_end` race is now guarded — but there is **no explicit lock**. Fine for
  the current sync/threadpool model; revisit under heavy async.
- **C5 is the *decision-free half* only:** the cap closes prompt-bloat (R-RET-E),
  but the **full unification of the two retrieval systems** (dataset-level
  `WrenRetrievalArtifact` vs MDL-level `context_items`) and a **token-based** (vs
  item-count) budget remain **gated on a product decision** (does MDL retrieval
  subsume dataset retrieval?). Dedup is exact-dict only, so cross-source *semantic*
  duplicates (same column in two shapes) survive.

#### C1 — Verify LanceDB + make the fallback loud (closes R-RET-A)

**Status — `[DONE]` for local verification (2026-06-23); CI install + loud fallback
still TODO.** `lancedb>=0.13` was installed and promoted to a **hard dependency**
in [`requirements-ai-agent.txt`](../requirements-ai-agent.txt); the previously
`importorskip`-skipped round-trip now **runs and passes** against the real API.

> **This immediately validated R-RET-A.** Running the real round-trip caught **two
> bugs the degrade-on-error wrapping had been silently hiding** (both would have
> shipped as "lancedb configured but silently running in-memory"):
> 1. **Invalid table name** — the table name embedded the effective checksum
>    (`...#openai:model:1536`), but LanceDB rejects `#`/`:`; persist threw and
>    degraded. Fixed: `_table_name` now hashes both scope + checksum to a safe id.
> 2. **`to_pandas()` needs `pylance`** — rehydrate used `to_pandas()`, which needs
>    the separate `pylance` package; it threw and degraded. Fixed: read via
>    `to_arrow().to_pylist()` (pyarrow is a lancedb dep). Also switched table
>    lookup from the deprecated `table_names()` to a direct `open_table` (raises on
>    miss), verified against lancedb 0.33.

*Remaining for C1:*

- **CI:** ensure the CI job installs `requirements-ai-agent.txt` (now incl.
  lancedb) so the round-trip runs there too
  ([`superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml) already
  installs that file — confirm the wheel resolves on `ubuntu-24.04`).
- **Loud fallback:** when `wren_vector_index="lancedb"` but lancedb import/connect
  fails, log a startup `WARNING` and expose the effective index mode on `/health`
  (`vector_index: "lancedb" | "memory_fallback"`); optional strict-fail mode.

- **CI:** add `lancedb` (+ its `pandas`/`pyarrow` deps) to a job — either extend
  [`superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml) with an
  `pip install lancedb` step, or a dedicated `test-ai-agent-extras` job — so the
  `importorskip` round-trip in
  [`test_schema_retriever.py`](../tests/unit_tests/superset_ai_agent/test_schema_retriever.py)
  runs for real (persist → new instance → rehydrate → retrieve).
- **Loud fallback:** when `wren_vector_index="lancedb"` but `lancedb` import/connect
  fails, log a `WARNING` at startup (not just per-call) and expose the **effective**
  index mode on `/health` (`vector_index: "lancedb" | "memory_fallback"`) so the UI
  can warn (mirrors `semantic_layer_persistent`). Optionally add a strict mode
  (`WREN_VECTOR_INDEX_STRICT=true`) that fails startup instead of degrading.
- **Tests:** the CI round-trip (real lancedb); a unit test that the health field
  reports `memory_fallback` when lancedb is absent.
- **Prereq/risk:** LanceDB native wheels on `ubuntu-24.04` (R-RET1) — pin a known
  wheel; if CI can't install it, keep the job `continue-on-error` + a tracking note
  rather than a green check that proves nothing.

#### C2 — Native ANN search (closes R-RET-B)

*Today `retrieve` rehydrates **all** rows and ranks in-process — durable, but not
ANN-at-scale.*

- Change `LanceDbRetriever.retrieve` cold path to embed the question and call
  `table.search(query_vector).limit(k).to_list()`, mapping rows → `SchemaItem`.
  Keep the in-process cache for the warm path (small/medium schemas), use ANN for
  cold/miss + wide schemas.
- **Prereq:** C1 (verified API). **Risk / dev-intent shift:** ANN is *approximate*
  — results can differ from exact cosine (recall < 1.0). Decide a threshold (e.g.,
  only use ANN above N items; exact below) and document the behavior change so a
  reviewer expecting identical keyword/embedding parity isn't surprised.
- **Tests:** (lancedb-gated) ANN returns the planted top-k for a deterministic
  fake-vector fixture; exact/ANN agree on a tiny fixture.

#### C3 — Real embedding round-trip verification (closes R-RET-C)

*The `dimensions` passthrough + Ollama path are tested via an injected fake client
only.*

- Add **contract tests** with a recorded/mocked HTTP layer (`respx` for the
  httpx-based openai client, or `openai`'s test transport): assert the request body
  carries `dimensions` for `text-embedding-3-*`, omits it for `ada-002`/ollama, and
  the response parses to vectors of the expected length.
- Add an **opt-in live smoke** test `skipif(not OPENAI_API_KEY)` for a one-row embed
  (kept out of the default unit run; runs where creds exist).
- **Prereq:** pick the recording lib (add to dev reqs). **Risk:** openai SDK
  internals shift — pin the contract to the public request shape, not internals.

#### C4 — Shared app-level retriever + bounded, thread-safe cache (closes R-RET-D / G7)

*Each graph builds its own embedder/retriever; the per-request graph-build path is
cold every request, and the in-process index never evicts scopes.*

- **Singleton:** build the embedder + retriever **once in `create_app`** and inject
  into both the service-level and per-request graph builders (like
  `active_memory`), so a worker shares one warm index.
- **LRU bound:** cap the in-process `_index` to the N most-recently-used scopes
  (`wren_retriever_cache_scopes`, default e.g. 64); evict LRU. Stops unbounded
  growth across many projects/owners.
- **Thread-safety:** `index()`/`prime()` mutate a dict; assignment is atomic under
  the GIL and `index()` is idempotent per checksum (a race rewrites identical
  vectors), so it is **low-risk** today — but add a per-scope lock if the server
  moves to heavy async/threaded concurrency.
- **Multi-worker:** memory mode is per-worker **by design**; LanceDB (C1/C2) is the
  cross-worker answer. Document it.
- **Tests:** injected singleton is the same instance across two graph builds; LRU
  evicts the oldest scope past the cap.

#### C5 — Unify the two retrieval systems (closes G5) — *needs a product decision*

*Legacy dataset-ranking (`retrieval.py` → `WrenRetrievalArtifact`) and MDL-chunk
ranking (`schema_retriever.py` → `context_items`) run additively, with no shared
token budget.*

- **Incremental (recommended):** (b-first) populate `WrenRetrievalArtifact` from the
  MDL path too — candidate model/column names + scores derived from the retrieved
  chunks — so both surfaces are consistent; then add a **single token budget**
  across the three `context_items` sources (doc overlay, MDL chunks, `fetch_context`)
  to stop prompt bloat. (a-later) Fully unify behind one ranked+budgeted context.
- **Latent gap this exposes:** the three context sources **concatenate today with no
  combined budget** — a wide MDL + doc overlay can inflate the prompt. The unified
  budget is the real fix; flag it as its own risk (**R-RET-E**, prompt-size
  unbounded across sources).
- **Prereq:** the dataset retriever runs in `_load_context` (context provider), the
  MDL retriever in `_load_wren_context` — unifying means threading both through one
  ranker; non-trivial. **Decision needed:** is dataset-level retrieval still wanted
  once MDL retrieval is strong, or does MDL subsume it?
- **Dev/UI mismatch:** "retrieval" means two different things in the artifact vs the
  badge today; unifying changes the artifact contract and the panel surface.

#### Cross-cutting follow-ups (lower priority)

- **R-RET3 — per-file checksum deltas:** the index keys off the whole-project
  `_content_checksum`, so any single-file edit reindexes everything. Add per-file
  deltas only if projects get large.
- **Cost surface:** stamp a per-turn embedding-call/token count onto the artifact so
  operators can watch spend (extends G8); surface in the panel.

#### Suggested sequencing

1. **C1** (unblocks C2; makes the silent-fallback risk visible) — small.
2. **C4** (prod-correctness: shared warm cache + bound) — small/medium, high value.
3. **C3** (lock down the embedder contract) — small.
4. **C2** (ANN search) — medium, gated on C1.
5. **C5** (unify + shared token budget) — medium/large, needs sign-off.

### Parity target (what Wren does)

Wren embeds MDL `schema_items` **once at index time** into a LanceDB collection
(one vector per model / column / relationship), persists the vectors, and at
**query time embeds only the question** and vector-searches top-k. The index is
rebuilt when the model changes, keyed by a manifest checksum. Cost per query:
**1** embedding call (the question) + a vector search — independent of schema width.

### Current-state audit (source-backed, verified 2026-06-23)

| Component | State | Source |
| --- | --- | --- |
| Embedder seam | `Embedder` protocol + `NullEmbedder` (degrade-closed) + `OpenAiEmbedder` (batched, lazy client) + `create_embedder` reusing `OPENAI_*` | [`llm/embeddings.py`](llm/embeddings.py) |
| Retriever seam | `SchemaItem`, `manifest_to_schema_items` (model/column/relationship chunks ≈ Wren `schema_items`), `KeywordRetriever`, `EmbeddingRetriever` (in-memory cosine, degrades to keyword), `create_retriever` | [`schema_retriever.py`](semantic_layer/schema_retriever.py) |
| Consumption | `retrieve_mdl_context(...)` in both graphs' `_load_wren_context` + `SemanticPipeline`; appends top-k chunks to `WrenContextArtifact.context_items` + stamps `retrieval_mode` | [`graph.py`](graph.py), [`conversation_graph.py`](conversation_graph.py), [`pipeline.py`](semantic_layer/pipeline.py) |
| Persistence guard | parity features (incl. `wren_retriever != keyword`) require `semantic_layer_store=sqlalchemy` | [`app.py`](app.py) `_parity_features_enabled` |
| Tests | 9 in [`test_schema_retriever.py`](../tests/unit_tests/superset_ai_agent/test_schema_retriever.py) (chunking, keyword/embedding rank, degrade, factories) + the RV2 wiring tests | — |

### Gap analysis (what "full parity" still needs)

| ID | Gap | Severity | Evidence |
| --- | --- | --- | --- |
| **G1** | **No persistent vector index** — `EmbeddingRetriever.retrieve()` re-embeds **every schema item *and* the question on every request**, so cost is O(N) embedding calls per query for N items. Wren embeds items once and searches. | **High** (perf + cost + rate limits) | [`schema_retriever.py:156-157`](semantic_layer/schema_retriever.py#L156) |
| **G2** | **No indexing lifecycle / `index()` seam** — the `Retriever` protocol is `retrieve(question, items, k)`; there is no `index(manifest, scope_key)`, no checksum keying, no reindex-on-materialize hook. The plan's [0.1](#01-seam-protocols) / 2.2 spec called for exactly that. | High | [`schema_retriever.py:55-61`](semantic_layer/schema_retriever.py#L55) (no `index`) |
| **G3** | **Per-request MDL recompile** — `retrieve_mdl_context` runs `compile_manifest(active_files)` + `manifest_to_schema_items` every request (re-reads/parses YAML), even for keyword. | Medium | [`schema_retriever.py:223-224`](semantic_layer/schema_retriever.py#L223) |
| **G4** | **`embedder_dimensions` is unused + unvalidated** — config carries it and passes it to `OpenAiEmbedder`, but `embed()` never sends `dimensions` to the API, and nothing validates model↔dimension or guards a dimension change against a persisted index. | Medium (latent corruption once G1 lands) | [`llm/embeddings.py:97-100`](llm/embeddings.py#L97) (no `dimensions=` arg) |
| **G5** | **Two unreconciled retrieval systems** — legacy [`retrieval.py`](semantic_layer/retrieval.py) ranks physical *datasets* + populates `WrenRetrievalArtifact`; the new MDL retriever ranks *chunks* + appends to `context_items` but does **not** populate `WrenRetrievalArtifact`. Three `context_items` sources concatenate (doc overlay, MDL chunks, `fetch_context`) with no unified rank/budget. | Medium | [`runtime.py`](semantic_layer/runtime.py) + `retrieve_mdl_context` |
| **G6** | **Only OpenAI embedder** — plan 2.1 listed Azure/Ollama. Azure works via `base_url`; Ollama/self-hosted not first-class. | Low | [`llm/embeddings.py`](llm/embeddings.py) |
| **G7** | **Embedder rebuilt per graph/request** — each graph calls `create_embedder(config)`; the per-request graph-build path makes a fresh embedder (+ OpenAI client) per request. No app-level singleton or shared cache. | Low (compounds G1) | `graph.py`/`conversation_graph.py`/`pipeline.py` ctors |
| **G8** | **No embedding-path observability** — `WrenRetrievalArtifact` is populated only by the legacy path; the MDL/embedding path exposes only `retrieval_mode` + `context_items`. No scores, embedded-item counts, or index freshness. | Low | [`schemas.py`](schemas.py) `WrenRetrievalArtifact` |
| **G8a** | **UI badge can lie** — `EmbeddingRetriever.retrieve()` silently falls back to keyword (embedder unavailable / `embed` raises) but `retrieve_mdl_context` stamps `retriever.name = "embedding"`, so the panel shows `Retrieval: embedding` even when keyword was actually used. | Medium (user-trust) | [`schema_retriever.py:153-159`](semantic_layer/schema_retriever.py#L153) + badge in `AuditInfoPanel.tsx` |

### Implementation plan (phased; each phase ships + is tested independently)

**R1 — Index lifecycle seam (no new dependency).** *Closes G2, G3, and most of G1
without LanceDB.*
- Extend the `Retriever` protocol with `index(items, *, scope_key, checksum)` and
  change `retrieve(question, *, scope_key, k)` to read a prebuilt index. Keep an
  inline `retrieve(question, items, k)` overload for tests/back-compat or adapt
  call sites.
- `EmbeddingRetriever` holds an in-process cache `{(scope_key, checksum): (items, vectors)}`;
  `index()` embeds items **once** per checksum; `retrieve()` embeds **only the
  question**. So warm-path cost drops from O(N) to O(1) embedding calls.
- `retrieve_mdl_context` computes a `scope_key` (owner+scope) + `checksum` (reuse
  the materializer's `materialized_checksum`) and calls `index()` lazily on a
  miss, then `retrieve()`. Skip the recompile when the checksum is unchanged (G3).
- **Acceptance:** a fake embedder counts calls; second query on the same checksum
  makes exactly **1** embedding call (question only); a checksum change triggers a
  reindex.

**R2 — Persistent LanceDB index (optional dependency).** *Closes G1 across
restarts/workers (RV2a).*
- Add `LanceDbRetriever(Retriever)` writing vectors to
  `{wren_lancedb_path or agent_storage_dir/lancedb}/{scope_key}`; `index()` upserts,
  `retrieve()` vector-searches. Import-guarded; absent → R1 in-process cache.
- Wire reindex into the materializer / `indexer.rebuild_index` keyed by
  `materialized_checksum`; stale checksum → lazy reindex.
- **Config:** `wren_retriever="embedding"` already gates it; add
  `wren_vector_index="memory"|"lancedb"` (default `memory`) so LanceDB is opt-in.
- **Acceptance:** index survives a new store instance on the same path; reindex on
  checksum change; degrades to in-memory when `lancedb` absent.

**R3 — Embedder hardening.** *Closes G4, G6.*
- Pass `dimensions` to the OpenAI embeddings API for `text-embedding-3-*`; validate
  `embedder_dimensions` is consistent (and **bake it into the index**; a mismatch
  vs. the persisted index forces a reindex, never a silent dimension clash).
- Add `OllamaEmbedder` (+ document Azure via `base_url`); `create_embedder` selects
  by `embedder_provider`.
- **Acceptance:** dimension-mismatch test forces reindex; reduced-dimension request
  round-trips; Ollama embedder builds from config.

**R4 — Reconcile + observability.** *Closes G5, G8, G8a.*
- Populate `WrenRetrievalArtifact` from the MDL path (candidate model/column names,
  scores, `embedded_item_count`, `index_checksum`). Decide one of: (a) unify the
  two retrievers behind one ranked+budgeted context, or (b) document them as
  intentionally additive (dataset-level vs MDL-level) — recommend (a) long-term,
  (b) as the interim contract.
- **Fix G8a:** stamp the *effective* retriever (`"keyword"` when an
  `EmbeddingRetriever` fell back), not the configured one, so the badge cannot lie.
  Add an `index_fresh`/age signal for a future UI affordance.
- **Acceptance:** fallback stamps `retrieval_mode="keyword"`; artifact carries the
  embedded-item count + index checksum.

### Config (new keys, all default-safe)

| Key | Default | Purpose |
| --- | --- | --- |
| `wren_vector_index` | `memory` | `memory` (R1 in-process) \| `lancedb` (R2 persistent) |
| `embedder_dimensions` | `1536` | now **actually sent** to the API + baked into the index (R3) |
| (existing) `wren_lancedb_path` | `{agent_storage_dir}/lancedb` | vector index root |

### Risks / prerequisites / gaps

**Prerequisites**
- **Durable manifest + stable checksum.** R1/R2 key off `materialized_checksum`;
  this requires `semantic_layer_store=sqlalchemy` (already enforced by
  `_parity_features_enabled`) and that materialization runs before retrieval (it
  does, in `_load_wren_context`). Without a project/MDL there is nothing to index —
  the keyword path stays the contract.
- **An embedder must be configured** (`AI_AGENT_EMBEDDER_PROVIDER=openai` +
  `OPENAI_API_KEY`); absent → `NullEmbedder` → keyword (degrade closed, unchanged).

**Risks**
- **R-RET1 — LanceDB native wheels.** Like wren-core, LanceDB ships platform
  wheels; CI/offline installs can break. Mitigation: optional dep, import-guarded,
  R1 in-process fallback is always present.
- **R-RET2 — index/embedder are process-local until R2.** R1's cache is per-worker
  (inherits R-C/R14); multi-worker prod re-embeds once per worker until LanceDB.
- **R-RET3 — checksum granularity.** `materialized_checksum` covers all active MDL;
  any edit reindexes the whole project (acceptable; revisit per-file deltas only if
  projects get large).
- **R-RET4 — embedding drift on model change.** Changing `embedder_model`/dimensions
  invalidates a persisted index; R3 must force a reindex, or recall silently
  degrades. This is the main correctness hazard of G1→R2.

**Dev-intent vs. actual-implementation gaps (today, pre-plan)**
- The `Retriever` protocol **diverged** from the plan's [0.1](#01-seam-protocols)
  spec (`index()` + scope keying) — it ranks inline with no index (G2). R1 realigns it.
- `embedder_dimensions` reads as a supported knob but is **inert** (G4) — a config
  that looks load-bearing but isn't. R3 makes it real.
- "EmbeddingRetriever (LanceDB)" in [2.2](#22-embeddingretriever-lancedb) implies a
  persistent vector store; the shipped binding is **in-memory cosine** (RV1) — the
  name oversells the implementation until R2.

**User-intent vs. actual-UI gaps**
- **The `Retrieval: embedding` badge can be wrong (G8a):** users read it as "vectors
  were used," but a silent keyword fallback still shows "embedding." R4 makes the
  badge reflect the *effective* path. Until then, treat the badge as "configured,"
  not "used."
- **No index-freshness surface:** a user editing models expects retrieval to follow;
  there is no UI signal that the index is stale/rebuilding (the engine/retrieval
  badges show mode, not freshness). R4 adds the signal; a UI affordance is a
  follow-on.
- **No cost signal:** embedding retrieval makes API calls; nothing surfaces that it
  ran or how many items were embedded — relevant for operators watching spend.

### Acceptance (parity definition of done for this workstream)
- A repeated question against an unchanged model makes **1** embedding call (not N).
- The vector index survives a restart under `wren_vector_index=lancedb`.
- A model edit (new `materialized_checksum`) triggers a reindex; an embedder model/
  dimension change forces a reindex rather than corrupting recall.
- The retrieval badge reflects the **effective** retriever; the artifact carries the
  embedded-item count + index checksum.
- Absent embedder/LanceDB → keyword + in-process, never an error.

---

## Phase 3 — MDL Completeness + Deep-Validation Hardening

### Phase 3 status — `[COMPLETE]` for schema/compile (2026-06-22)

**Metrics and cubes are first-class in the schema + compile path.** Source:
[`mdl_schema.py`](semantic_layer/mdl_schema.py) (`MdlMetric`, `MdlCube` with
measures/dimensions/timeDimensions/hierarchies; `MdlManifest.metrics`/`cubes`);
[`mdl_compile.py`](semantic_layer/mdl_compile.py) (`_CUBE_KEYS`, `metric_to_camel`,
`cube_to_camel`, `CompiledManifest.cubes`, `to_engine_manifest` emits
`metrics`/`cubes`). Test:
[`test_mdl_compile.py`](../tests/unit_tests/superset_ai_agent/test_mdl_compile.py)::
`test_compile_manifest_maps_metrics_and_cubes`. Suite: **225 passed, 2 skipped**;
`ruff` clean.

**~~Residual risk RM1~~ — RESOLVED (2026-06-23):** the structural **validator**
now enforces metric/cube shape ([`mdl_validator.py`](semantic_layer/mdl_validator.py)
`_validate_metrics` / `_validate_cubes`): duplicate metric/cube names are errors;
`base_object` resolution against models/views/cubes is a per-file warning and a
project-manifest error (mirroring relationship resolution); metrics without an
expression/measures and cubes without measures (or measures without an
expression) warn. A metric/cube-only file is no longer `empty_root`. Tests:
[`test_mdl_validator.py`](../tests/unit_tests/superset_ai_agent/test_mdl_validator.py)
(7 new). **~~Residual RM1a~~ — partially RESOLVED (2026-06-23):** cube
dimensions/time-dimensions/hierarchies must now each be **named mappings**
(`_validate_named_entries`; codes `cube_entry_without_name`/`cube_invalid_entries`;
+3 tests). **Still open:** deeper semantics — time-dimension granularity values
and hierarchy levels resolving to real dimensions — remain `extra="allow"`, left
to wren-core deep validation.

**Residual risk RM2:** the camelCase metric/cube shapes
(`baseObject`/`timeDimensions`) are **unverified against a live wren-core**
(RE1/R16). Re-verify before enabling deep validation.

**~~Deep-validation CI job~~ — RESOLVED (2026-06-23):** a dedicated workflow
[`.github/workflows/superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml)
installs `requirements-ai-agent.txt` (which carries `wren-core-py`), asserts
`import wren_core` succeeds, then runs the AI-agent suite — so the wren-core-gated
rewrite/deep-validation tests (skipped locally when the wheel is absent) actually
execute in CI. New golden test
[`test_semantic_engine.py`](../tests/unit_tests/superset_ai_agent/test_semantic_engine.py)::
`test_wren_core_rewrites_multi_model_join` asserts a two-model join expands into
native CTEs + `INNER JOIN sales.deals/sales.customers`. **New finding RE1b:**
wren-core 0.7.1 in embedded mode (no registered data source) raises an internal
"CSV error: No such file or directory" on **relationship-traversal** auto-joins
(a calculated column like `customer.region` that the LLM references without
writing the join), so that specific litmus is unmet; explicit multi-model joins
do rewrite. The engine degrades closed on the error (SQL preserved + warning).

**Parity target:** Wren MDL includes cubes (measures/dimensions/time
dimensions/hierarchies) and first-class metrics; engine deep-validates manifests.

- [ ] Extend [`mdl_schema.py`](semantic_layer/mdl_schema.py) with `MdlMetric` and
      `MdlCube` (measures, dimensions, time dimensions, hierarchies) as
      first-class objects; extend `MdlManifest` containers.
- [ ] Extend `compile_manifest` + `to_wren_core_manifest` to emit the camelCase
      cube/metric shapes wren-core expects; extend the validator
      ([`mdl_validator.py`](semantic_layer/mdl_validator.py)) accordingly.
- [ ] Teach the Modeler (Phase 4 prompts) to propose metrics/cubes where the
      source supports them.
- [ ] Make `validate` run wren-core deep validation in CI on every manifest
      change (extends the Phase-1 CI job).

**Tests:** cube/metric round-trip compile + validate; deep-validation rejects a
semantically inconsistent manifest.

---

## Phase 4 — Orchestrator / Skills + Embeddable Surface

### Phase 4 status — intent + skills `[COMPLETE]` (2026-06-22)

**Intent classification and Skills landed** as standalone, tested units. Source:
[`intent.py`](intent.py) (`classify_intent` → `text_to_sql`/`general`/`clarify`,
fails closed to `text_to_sql`); [`skills/`](skills/) (`onboarding`,
`generate-mdl`, `enrich-context`, `usage` Markdown + `get_skill`/`list_skills`
loader). Tests:
[`test_intent_and_skills.py`](../tests/unit_tests/superset_ai_agent/test_intent_and_skills.py)
(6 — each intent, fail-closed on error/bad-JSON, skill listing/loading, unknown
skill). Suite: **231 passed, 2 skipped**; `ruff` clean.

**~~Residual risk RO1~~ — RESOLVED as a gated hint (2026-06-23):**
`classify_intent` is now wired into the **conversation graph** as a `classify_intent`
pre-node (after `load_conversation`, before `load_context`), gated by
`wren_intent_classification_enabled` (default **off** — adds one LLM call/turn).
When on, it stamps `state["intent"]`, emits a `classify_intent` trace event, and
passes the label to the conversation model as a hint in the draft payload.
Approved-SQL turns and the disabled path are no-ops; fails closed to
`text_to_sql`. Tests:
[`test_seam_wiring.py`](../tests/unit_tests/superset_ai_agent/test_seam_wiring.py)
(`test_intent_classification_runs_when_enabled` / `_off_by_default`).
**~~Residual RO1a~~ — RESOLVED (2026-06-23):** a true routing short-circuit now
exists, gated by `wren_intent_routing_enabled` (default off, requires
classification on). When a turn is classified `general`/`clarify`,
[`conversation_graph.py`](conversation_graph.py)'s `_route_after_intent` routes to
`_answer_directly`, which answers from conversation history + the intent label and
**skips context-load + MDL materialization + the entire SQL path** (no execution).
Tests: `test_seam_wiring.py::test_intent_routing_*`. **Risk (inherent):** a
misclassified data question labeled `general`/`clarify` gets a non-answer; mitigated
by gating off-by-default and failing closed to `text_to_sql` on classifier error,
but a *confident* wrong classification still short-circuits. One-shot graph is
intentionally excluded (its contract is text-to-SQL).

**~~Residual risk RO2~~ — PARTIALLY RESOLVED (2026-06-23):** the
**SemanticPipeline facade (4.1)** now exists
([`semantic_layer/pipeline.py`](semantic_layer/pipeline.py)) as a single
importable object composing the deterministic seams (Retriever, SemanticEngine,
Executor, Memory) + the intent classifier:
`classify_intent → retrieve_context → plan_and_execute (rewrite → validate →
execute → store)`. It reuses the exact shared steps the graphs use
(`plan_semantic_sql_step`, `validate_read_only_sql`, the Superset executor) so it
cannot drift. Tests:
[`test_semantic_pipeline.py`](../tests/unit_tests/superset_ai_agent/test_semantic_pipeline.py)
(3). **Residual RO2a:** the facade takes **semantic SQL as input** — the LLM
drafting/orchestration loop still lives in the LangGraph graphs, so the full
inversion (graphs become thin callers of the pipeline) is **not** done; the
pipeline is the embeddable deterministic core, not yet the drafting orchestrator.
**Residual RO2b:** the **LangChain/Pydantic-AI adapters (4.4)** are still not
implemented (no framework tool wrappers).

**Residual risk RO3 (UI gap):** skills are backend-only; nothing surfaces them in
the UI, and intent classification has no UI affordance.

**Parity target:** Wren's plan-and-execute SDK, Markdown **skills** that guide
agents, intent classification, and framework adapters (LangChain/Pydantic-AI).
This is the "extend beyond Wren / future agentic" seam.

### 4.1 SemanticPipeline facade (the `wrenai`-SDK equivalent)

- [ ] `semantic_layer/pipeline.py::SemanticPipeline` composing the seams:
      `intent → retrieve → modeler-context → draft semantic SQL →
      engine.plan_sql → validate → execute (Superset) → memory.store`. The graphs
      become thin callers; the pipeline is the reusable, embeddable unit.

### 4.2 Intent classification (parity gap #6)

- [ ] Lightweight pre-node classifying `text_to_sql | general | clarify |
      misleading` (reuse `model_client.chat` with a small prompt). Short-circuit
      non-SQL intents to a direct answer / clarification instead of forcing SQL.

### 4.3 Skills

- [ ] Adopt Wren's Markdown-skill pattern under `superset_ai_agent/skills/`:
      `onboarding.md`, `generate-mdl.md`, `enrich-context.md`, `usage.md`.
      Register via [`prompts/registry.py`](prompts/registry.py); these encode
      safe procedures ("build MDL before querying", "fetch context before SQL",
      "store confirmed example after success") and are the human-readable seam for
      future agentic workflows.

### 4.4 Embeddable / framework adapters (stretch)

- [ ] Expose the pipeline as a tool-callable interface; provide thin LangChain /
      Pydantic-AI adapters as references (mirrors `wren-langchain` /
      `wren-pydantic`). Keep optional and out of the default install.

**Tests:** intent routing; pipeline end-to-end with all seams; skill registry
load.

---

## Cross-Cutting

### Config surface (new keys, all env-overridable, all default-safe)

| Key | Default | Purpose |
| --- | --- | --- |
| `wren_engine` | `wren_core` | `passthrough` \| `wren_core` (default flipped to `wren_core` in Milestone B) |
| `wren_semantic_sql_enabled` | `false` | semantic-SQL prompt mode |
| `wren_engine_max_correction_retries` | `0` | engine hallucination-feedback correction loop (one-shot graph; default off — see RG4) |
| `wren_retriever` | `keyword` | `keyword` \| `embedding` |
| `wren_lancedb_path` | `{storage}/lancedb` | vector index root |
| `embedder_*` (`AI_AGENT_EMBEDDER_*`) | see [2.1](#21-embedder-seam) | embedder — full env-var table in Phase 2 |
| `wren_memory_store` | `none` | `none` \| `sqlalchemy` \| `lancedb` |
| `wren_memory_learning_enabled` | `true` | write-back on confirmed success |
| `wren_memory_recall_k` | `3` | few-shot count |
| `wren_intent_classification_enabled` | `false` | gated intent pre-node (conversation graph, RO1) |
| `wren_intent_routing_enabled` | `false` | route general/clarify to a direct answer (RO1a; needs classification on) |

Keep `WREN_EXECUTION_ENABLED` rejected at startup. Existing
`wren_core_validation_enabled` is subsumed by `wren_engine=wren_core` (keep as an
alias for one release).

### Dependencies (all optional, import-guarded)

- [x] `wren-core` (Phase 1) — a **hard dependency** in
      [`requirements-ai-agent.txt`](../requirements-ai-agent.txt); the AI-agent CI
      job installs it ([`superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml)).
- [ ] `lancedb` (Phase 2) — optional extra (commented); absent → keyword
      retrieval + sqla memory.
- [x] embeddings provider SDK reuse (Phase 2) — `openai` is already present and
      covers embeddings.
- [x] **`psycopg` (Phase 0, prod)** — documented as an optional (commented)
      production dependency in
      [`requirements-ai-agent.txt`](../requirements-ai-agent.txt); only `sqlite`
      works out of the box today. Not needed for dev (sqlite default).

### Migrations

- [ ] `0003_nl_sql_examples` (Phase 2). Follow the `0001`/`0002` pattern in
      [`persistence/migrations/versions/`](persistence/migrations/versions/).

### Frontend (follow-on, not blocking parity)

Baseline UI is already substantial and modeling-ready — `SemanticLayerEditor`
(onboarding/enrich/materialize/import wired). Parity adds surfacing only:

- [x] Surface engine status (`engine`) and the semantic-vs-native SQL in the AI
      panel audit collapsible — done (2026-06-23):
      [`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx)
      now renders an `Engine: …` badge + friendly `Semantic SQL`/`Native SQL`
      labels; [`api.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts)
      types `AuditInfo.engine/semantic_sql/native_sql`. Test: `AuditInfoPanel.test.tsx`.
- [x] Surface retrieval mode (keyword/embedding) and a "reused a learned
      example" badge — done (2026-06-23): a `Retrieval: …` badge from
      `WrenContextArtifact.retrieval_mode` and a `Reused N learned example(s)`
      badge from `WrenContextArtifact.recalled_example_count` (stamped in both
      graphs' draft nodes). Test: `AuditInfoPanel.test.tsx`.
- [x] Surface a "models are not persisted" warning when the service runs in
      `semantic_layer_store=memory` — done (2026-06-23):
      `HealthResponse.semantic_layer_persistent` ([`app.py`](app.py)/[`schemas.py`](schemas.py))
      drives a warning `Alert` in
      [`index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/index.tsx).
      Test: `test_app.py::test_health_and_models_use_injected_ollama_client`.

---

## Overall Acceptance — "Full Parity" Definition of Done

**Status (2026-06-23):** all six seams have a working binding with tests, are
wired into the live graphs (RV2 retriever, RV4 conversation memory, RO1 intent
hint), and now have a wren-core CI engine job (RE1), metric/cube structural
validation (RM1), and a SemanticPipeline facade (4.1). The remaining gaps are the
persistent vector index / dedup optimizations, the full graph→pipeline inversion
+ framework adapters (RO2a/b), and the UI surfacing. Backend suite: **253 passed,
2 skipped**; `ruff` clean on changed files.

- [x] **Persistence:** MDL, projects, and the materialized manifest are durable
      (survive restart) under `semantic_layer_store=sqlalchemy`; parity features
      refuse to run silently against an in-memory store (0.0). *(verified:
      `test_persistence_baseline.py`)*
- [x] **Engine:** wren-core rewrites semantic SQL → native SQL, executed through
      Superset only. *(verified live: `test_semantic_engine.py` model→physical +
      calculated-column rewrites; default-on; degrades to passthrough on
      unsupported dialects like sqlite.)*
- [x] **Retrieval:** embedding retrieval ranks by cosine and degrades to keyword,
      and is now consumed by both graphs' context-load (RV2). *(verified:
      `test_seam_wiring.py`; persistent LanceDB index still deferred — RV2a.)*
- [x] **Memory:** a previously-confirmed question is recalled as few-shot.
      *(verified: `test_memory_store.py` + graph round-trip.)*
- [x] **Modeler:** onboarding + doc enrichment produce activatable draft MDL
      validated against the live schema. *(pre-existing + cube/metric coverage.)*
- [x] **Executor:** every execution is Superset-only, read-only-validated, and
      audited with semantic+native SQL. *(verified: graph engine tests.)*
- [~] **Orchestrator/Skills:** intent classifier + skills exist and are tested;
      the classifier is wired into the conversation graph as a gated pre-node hint
      (RO1), and the **SemanticPipeline facade (4.1) now composes the deterministic
      seams** (RO2). A true intent routing short-circuit (RO1a), the full
      graph→pipeline drafting inversion (RO2a), and framework adapters (RO2b/4.4)
      remain deferred.
- [ ] Throwaway **A/B spike** vs. a full upstream Wren mesh (de-risking).
- [x] **CI:** backend unit suite green (**253 passed, 2 skipped**), ruff clean on
      changed files; the **wren-core CI engine job** (RE1) now installs the wheel
      and runs the engine-present tests
      ([`superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml)).

Legend: `[x]` done & verified · `[~]` core done, follow-up/gated · `[ ]` not started.

---

## Open Questions & Risks

- **OQ1 — Semantic-SQL rollout.** See Phase 1; default `passthrough`, flip
  per-project after golden + real-schema A/B.
- **OQ2 — Embedder provenance.** Which embedder is the supported default
  (OpenAI/Azure/self-hosted)? Affects LanceDB dimensions and offline story.
- **OQ3 — Memory governance.** Confirm owner+scope isolation is sufficient, or
  whether learned examples need an explicit opt-in/retention policy per the
  Superset security model.
- **R-A — wren-core version drift.** `transform_sql`/manifest shape can change
  across wren-core releases; the CI engine job + pinned dep mitigate (supersedes
  R16). Re-verify `BACKEND_TO_WREN_DIALECT` on upgrades.
- **R-B — dialect coverage.** Not every Superset backend maps to a wren-core
  dialect; unknown → passthrough + warning (no silent wrong-dialect SQL).
- **R-C — index/memory are process-local until backed by LanceDB-on-shared-store
  or DB.** Mirrors the existing R14/R18 job-runner caveat; fine for single-worker,
  revisit for multi-worker prod.
- **R-D — physical drift.** Engine `referenced_tables` validated against the
  permission-filtered `SchemaIndex` (live or snapshot per R12); a dropped column
  during an outage can mis-flag until the next live fetch (inherits R17).

## Pre-flight (every phase)

- [ ] `pre-commit run --all-files` green (mypy/ruff/pylint/eslint/prettier).
- [ ] New Python files carry the ASF header + type hints.
- [ ] New optional deps are import-guarded and degrade closed.
- [ ] **Any new env var is reflected in [`.env.example`](.env.example)** (and
      deployment env/compose templates) with a sane default commented in.
- [ ] Update the [Implementation Status](#implementation-status) table.
