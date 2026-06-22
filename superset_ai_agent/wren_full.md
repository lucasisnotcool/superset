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
| 1 | Semantic-SQL prompt mode + engine-feedback correction loop | `[COMPLETE]` (2026-06-22) |
| 1 | Semantic-SQL prompt + correction loop | `[TODO]` |
| 2 | Embedder + EmbeddingRetriever (LanceDB) | `[TODO]` |
| 2 | Memory learning loop | `[TODO]` |
| 3 | MDL completeness (cubes/metrics) + deep-validation CI | `[TODO]` |
| 4 | Orchestrator/Skills + intent classification + SDK facade | `[TODO]` |

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

**Residual risk RE1:** the real rewrite path (`transform_sql`, manifest serde,
dialect tokens) is **unverified against a live wren-core** — the engine-present
test is skipped. This is the Phase-1 CI-job gap (R-A/R16); do not flip
`wren_engine=wren_core` in any environment until that job is green.

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

**Residual risk RG4:** 1.4 feedback is best-effort — engine rewrite failures
*degrade to passthrough* (no hard error), so `engine_warnings` carries the
soft-gate/degrade reasons, not a wren-core validation error (that arrives only
once RE1 lands and `plan_sql` can surface a hard failure).

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

## Phase 3 — MDL Completeness + Deep-Validation Hardening

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
| `wren_engine` | `passthrough` | `passthrough` \| `wren_core` |
| `wren_semantic_sql_enabled` | `false` | semantic-SQL prompt mode |
| `wren_engine_max_correction_retries` | `2` | engine-error correction loop |
| `wren_retriever` | `keyword` | `keyword` \| `embedding` |
| `wren_lancedb_path` | `{storage}/lancedb` | vector index root |
| `embedder_*` (`AI_AGENT_EMBEDDER_*`) | see [2.1](#21-embedder-seam) | embedder — full env-var table in Phase 2 |
| `wren_memory_store` | `none` | `none` \| `sqlalchemy` \| `lancedb` |
| `wren_memory_learning_enabled` | `true` | write-back on confirmed success |
| `wren_memory_recall_k` | `3` | few-shot count |

Keep `WREN_EXECUTION_ENABLED` rejected at startup. Existing
`wren_core_validation_enabled` is subsumed by `wren_engine=wren_core` (keep as an
alias for one release).

### Dependencies (all optional, import-guarded)

- [ ] `wren-core` (Phase 1) — uncomment in
      [`requirements-ai-agent.txt`](../requirements-ai-agent.txt); CI installs it.
- [ ] `lancedb` (Phase 2) — optional extra; absent → keyword retrieval + sqla
      memory.
- [ ] embeddings provider SDK reuse (Phase 2) — reuse existing provider deps
      (`openai` is already present and covers embeddings) where possible.
- [ ] **`psycopg` (Phase 0, prod)** — required for Postgres-backed persistence;
      only `sqlite` works out of the box today. Add to a deployment requirements
      file (per [`wren.md`](wren.md) Agent-Owned Database). Not needed for dev
      (sqlite default).

### Migrations

- [ ] `0003_nl_sql_examples` (Phase 2). Follow the `0001`/`0002` pattern in
      [`persistence/migrations/versions/`](persistence/migrations/versions/).

### Frontend (follow-on, not blocking parity)

Baseline UI is already substantial and modeling-ready — `SemanticLayerEditor`
(onboarding/enrich/materialize/import wired). Parity adds surfacing only:

- [ ] Surface engine status (`engine=wren_core|passthrough`) and the
      semantic-vs-native SQL in the AI panel audit collapsible
      ([`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx)).
- [ ] Surface retrieval mode (keyword/embedding) and whether a query reused a
      learned example (memory badge).
- [ ] Surface persistence mode + a "models are not persisted" warning when the
      service runs in `semantic_layer_store=memory` (dev), so users don't model
      against an ephemeral store unaware.

---

## Overall Acceptance — "Full Parity" Definition of Done

Parity is achieved when **all six seams** have their parity binding passing and:

- [ ] **Persistence:** MDL, projects, and the materialized manifest are durable
      (survive restart) under `semantic_layer_store=sqlalchemy`; parity features
      refuse to run silently against an in-memory store (0.0).
- [ ] **Engine:** cross-model join + calculated metric question → executed native
      SQL with engine-generated joins (Phase 1 litmus), through Superset only.
- [ ] **Retrieval:** on a wide schema, embedding retrieval surfaces the relevant
      models a keyword scan misses (fixture A/B).
- [ ] **Memory:** a previously-confirmed question is answered using its own stored
      pair as few-shot (learning loop demonstrated).
- [ ] **Modeler:** onboarding + doc enrichment produce activatable draft MDL
      validated against the live schema (existing tests + cube/metric coverage).
- [ ] **Executor:** every execution is Superset-only, read-only-validated, and
      audited with semantic+native SQL.
- [ ] **Orchestrator/Skills:** intent routing works; the pipeline is importable
      and skill-guided.
- [ ] Throwaway **A/B spike** vs. a full upstream Wren mesh shows comparable
      end-to-end SQL quality on the dev fixtures (de-risking, not a build target).
- [ ] `pre-commit run --all-files` green; new CI engine job green.

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
