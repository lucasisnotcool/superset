<!--
Implementation plan (sequential checklist) for Wren-parity VIEW authoring in the
MDL Copilot. Companion to plan_views_parity_spec.md. Source-backed: every step
lists exact entrypoints/touchpoints (file:line), requirements, tests, risks, and
dependencies. Designed to be picked up and continued by a future agent session —
check boxes as you go.
-->

# Implementation Plan — Wren-Parity Views (MDL Copilot)

Spec: [`plan_views_parity_spec.md`](plan_views_parity_spec.md) · Memory: [[views-parity-spec]]
Created: 2026-06-30 · Status: **Phase 0 + Phase 1 COMPLETE (semantic views shipped);
Phase 2 (native) gated on Step 6.5 eval, not built.**

> **As-built coherence note:** Phase 1 ships **semantic views only**. The `dialect`
> field exists on `MdlView`/`AuthoredView` as dormant forward-compat plumbing, and
> the validation path already filters native (`dialect`-carrying) views out of the
> wren-core manifest (R9 guard). But the **skills/prompts deliberately do NOT
> instruct native authoring yet** — telling the model to write native views before
> the execution path (Phase 2 Step 8) exists would let a native view reach the
> materialized engine manifest and poison query-time load. Native authoring
> guidance + the materializer exclusion + execution land together in Phase 2.

## How to use this checklist
- Phases are **strictly ordered**; within a phase, steps list `Depends on`.
- **Two blocking spikes (Phase 0)** decide scope — run them first, record outcomes
  inline before building.
- **Phase 1 (semantic views) is full Wren parity and ships independently.** Phase 2
  (native views) is the accuracy hedge from spec §5.7 and is a fast-follow gated on
  the Phase 0.5 spike. Do **not** block Phase 1 on Phase 2.
- After each step: add tests, run them, note residual risk + any expectation↔UI gap
  (per the team's standing instruction).
- Before any push: `pre-commit run --all-files` (CLAUDE.md, non-negotiable).

## Guiding patterns to reuse (existing in-repo / industry standard)
- **Mirror the `Authored*` types** when adding `AuthoredView`
  ([mdl_authoring.py](semantic_layer/mdl_authoring.py)) — pydantic schema is then
  auto-derived by `proposal_response_schema()`; **no manual JSON-schema edits**.
- **Degrade closed** on optional wren-core absence (every engine seam already does:
  [wren_core_validator.py:83](semantic_layer/wren_core_validator.py#L83)).
- **Reuse `_friendly_engine_error`** ([wren_core_validator.py:109](semantic_layer/wren_core_validator.py#L109))
  for any engine-surfaced view error.
- **Native dry-run = the pipeline pattern**: `validate_read_only_sql` → `execute_sql`
  exactly as [pipeline.plan_and_execute](semantic_layer/pipeline.py#L140) does
  (reuse [tools/sql.py:33](tools/sql.py#L33) + [client.py:324](integrations/superset/client.py#L324)).
- **Minimal-surface gating** (from the multi-schema work): new behavior activates
  only on a marker — here `dialect` presence — so the semantic path is byte-for-byte
  unchanged.
- **Adapted skill files keep a provenance header** citing upstream path + fetch date
  (existing port convention, e.g. [cube_proposals.md:1-9](wren_upstream_skills/enrich-context.references.cube_proposals.md#L1)).
- **File-level changeset review** is the right grain — **no new UI components**
  ([ChangesetReviewPanel.tsx](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/ChangesetReviewPanel.tsx)).

---

## PHASE 0 — Spikes (BLOCKERS; no production change)

### [ ] Step 0 — Spike D2: does manifest-load already validate a semantic view's SQL?
- **Goal:** decide whether G4 (per-view dry-plan) is required or optional.
- **Entrypoint:** new throwaway test beside
  [test_native_manifest_contract.py](../tests/unit_tests/superset_ai_agent/test_native_manifest_contract.py)
  (guard with `@requires_wren_core`).
- **Procedure:** build `NATIVE_MANIFEST` + one model + a view whose `statement`
  references a **non-existent column** of that model. (a) `SessionContext(compiled.to_base64_json())`
  — does it raise? (b) `ctx.transform_sql("SELECT * FROM <view>")` — does it raise?
- **Outcome → decision:** load raises on bad column ⇒ G4 optional (Layer A suffices);
  load passes but plan raises ⇒ **G4 required**; both pass ⇒ a column-resolving
  dry-plan is mandatory.
- **✅ RESULT (2026-06-30, wren-core 0.7.x):** `SessionContext` **LOAD raises
  eagerly** — bad column → `Schema error: No field named nonexistent_col`; missing
  model → `table 'wren.public.not_a_model' not found`. Valid views (incl. cross-model
  JOIN, WINDOW, CTE) load **and** `transform_sql` rewrites them. **DECISION: G4 Layer
  B (separate dry-plan) is NOT needed — Step 1 (Layer A) alone fully validates
  semantic view SQL at the activation gate. Step 2 is dropped.**
- **Risk:** none (no production code). **Depends on:** wren-core installed locally.

### [ ] Step 0.5 — Spike D6: can wren-core execute a NATIVE (physical-table) view?
- **Goal:** decide the native-view execution path (engine vs bypass) — blocks all of
  Phase 2.
- **Procedure:** manifest with a model + a **native** view (statement = raw
  `SELECT … FROM <real physical schema.table that is NOT a model>`; if a `dialect`
  field is accepted by the loader, set it). (a) Does `SessionContext` **load**
  without raising? (b) Does `transform_sql("SELECT * FROM <native_view>")` return
  runnable SQL? Try with and without a `dialect`/`schemaVersion` field to learn what
  wren-core-py 0.7.1 accepts.
- **Outcome → decision:** load+plan OK ⇒ native views use the engine (full parity,
  cheaper); any raise ⇒ **native views bypass the engine manifest** (Phase 2 routes
  them out of `to_engine_manifest`, validates via read-only+dry-run, and surfaces as
  golden-query-style context).
- **✅ RESULT (2026-06-30, wren-core 0.7.x):**
  - wren-core resolves view bodies **only against MODEL names** (`catalog.schema.<model_name>`).
    A view referencing a **physical table that is not a model fails LOAD**
    (`table 'wren.public.raw_orders_v2' not found`) — proven with model name ≠ table
    name. (A physical name *coincidentally equal* to a model name resolves, but that's
    incidental.)
  - The `dialect` field is **accepted by the view schema** (no serde rejection) but is
    **inert** in 0.7.x — it does **not** enable physical-table/native passthrough;
    `schemaVersion: 3` didn't change this either.
  - **DECISION: native (physical-table) views CONFIRMED to require the bypass path
    (2B) — they must be excluded from the engine manifest or they poison the whole
    project's load.** The `dialect` marker is safe to store but won't be honored by
    the engine; native execution must be ours (passthrough inline / golden-query).
    **See the re-scoping + eval gate in Phase 2 below.**
- **Risk:** none. **Mitigation for the "poison" failure mode is the whole point of
  this spike.** **Depends on:** wren-core installed; a reachable physical table.

---

## PHASE 1 — Semantic views (full Wren parity; ships standalone)

### [x] Step 1 — G2 Layer A: stop dropping views from deep validation ✅ DONE
- **Requirement:** a structurally/engine-invalid view must fail the **activation
  gate**, not at query time. Semantic views (and the models they depend on) reach
  wren-core.
- **Entrypoints / touchpoints:**
  - [`to_wren_core_manifest(models, relationships)`](semantic_layer/wren_core_validator.py#L145)
    → add `views: list[dict] | None = None`; include `"views": views` in the dict
    when present.
  - [`validate_with_wren_core(models, relationships)`](semantic_layer/wren_core_validator.py#L62)
    → add `views` param; forward to `to_wren_core_manifest`.
  - Call site [mdl_validator.py:509](semantic_layer/mdl_validator.py#L509) → pass
    `merged_views` (already assembled at [mdl_validator.py:475-491](semantic_layer/mdl_validator.py#L475)).
- **Requirement detail:** keep params optional/defaulted so existing callers and the
  models+relationships-only contract are untouched (degrade closed when wren-core
  absent — reuse the existing guard).
- **Tests** (`test_mdl_validator.py`, `test_native_manifest_contract.py`):
  - view with empty/missing `statement` → deep validation error.
  - clean single-model view → passes.
  - wren-core absent → info message, `valid=True`.
  - **cross-schema view** (statement joins two models whose `tableReference.schema`
    differ) → validates clean (spec §5.6 regression guard).
- **Risk R1 (false-green view):** this step is the primary fix. **Mitigation:** the
  test matrix above. **Depends on:** none (Step 0 not required for Layer A).

### [x] Step 2 — G4 Layer B: per-view dry-plan — ❌ DROPPED (Step 0 result)
- **Not needed.** Step 0 proved `SessionContext` **load** already validates view SQL
  eagerly (unknown column AND missing model both raise at load). Layer A (Step 1,
  passing views into the wren-core manifest) catches every bad semantic view at the
  activation gate. A separate dry-plan would be redundant cost. **Skip this step;
  fold its one test (unknown-column view → error) into Step 1.**

### [x] Step 3 — G1: authoring contract (`AuthoredView`) ✅ DONE
- **Requirement:** the structured generation/enrichment path can emit a view; its
  schema is handed to the model automatically.
- **Entrypoints / touchpoints:**
  - [mdl_authoring.py:~115](semantic_layer/mdl_authoring.py#L117) — add `AuthoredView`
    (`name`, `statement`, optional `dialect: str | None = None`, `properties`),
    mirroring [`MdlView`](semantic_layer/mdl_schema.py#L116).
  - Add `views: list[AuthoredView]` to `AuthoredManifest`
    ([mdl_authoring.py:122-124](semantic_layer/mdl_authoring.py#L122)).
  - Mirror optional `dialect` on [`MdlView`](semantic_layer/mdl_schema.py#L116)
    (forward-compat for Phase 2; harmless when unset).
  - **No change** to `proposal_response_schema()`
    ([mdl_authoring.py:146](semantic_layer/mdl_authoring.py#L146)) — auto-derives.
  - **No signature change** to `propose_mdl_from_document` / `generate_base_model`
    across the 3 client impls ([client.py:74/142/290](integrations/wren/client.py#L74),
    [llm_client.py:165/421](integrations/wren/llm_client.py#L165),
    `http_client.py`) — the contract widens transparently.
- **Requirement detail — input context (the metric-parity need):** a view statement
  references **model-column space**, so the enrichment payload must include existing
  **model names + columns**. Verify/extend the payload built in
  [`propose_mdl_from_document`](integrations/wren/llm_client.py#L165); if absent, add
  the active models' summary (same context metrics need).
- **Tests:** a `MdlProposalResponse` containing a view round-trips; the serialized
  `views/<n>.json` passes `validate_mdl`; `proposal_response_schema()` JSON contains
  a `views` property.
- **Risk:** model emits a view with a non-model reference. **Mitigation:** Steps 1-2
  reject it at validation. **Depends on:** Step 1 (emitted views get validated).

### [x] Step 4 — G6: view-only file path routing ✅ DONE
- **Requirement:** a proposal whose overlay is views-only lands at `views/<name>.json`.
- **Entrypoint:** draft-path logic in
  [llm_client.py:~346](integrations/wren/llm_client.py#L346) — when overlay has
  `views` and no `models`, default path to `views/<_safe_name(view.name)>.json`.
- **Note:** non-blocking; the store is path-agnostic
  ([mdl_files.py:357](semantic_layer/mdl_files.py#L357)). Convention only.
- **Tests:** path-derivation unit test (views-only → `views/…`; mixed → unchanged).
- **Risk:** low. **Depends on:** Step 3.

### [x] Step 5 — G3: skills & prompts (adapt upstream; spec §7) ✅ DONE (semantic-only)
- **Requirement:** the Copilot is *taught* when/how to author a view, at both
  creation points (doc-driven onboarding self-review; enrichment), reusing upstream
  text per the "download & adapt" rule.
- **Touchpoints (6 files):**
  1. **New** `skills/references/view_proposals.md` (or inline) — adapt the VIEW branch
     of [cube_proposals.md:15-27,90-91](wren_upstream_skills/enrich-context.references.cube_proposals.md#L15):
     *JOIN-across-models / window / CTE → VIEW*; our tweaks (write `views/<n>.json`;
     **semantic SQL over model names → cross-schema-correct**; require
     `properties.description`; high-blast-radius → review-gated).
  2. [`skills/enrich-context.md`](skills/enrich-context.md) — add views to Step 5 gap
     catalog + Step 7 routing; update Parity-notes
     ([enrich-context.md:311](skills/enrich-context.md#L311)) to mark views an
     authoring sink.
  3. [`skills/generate-mdl.md`](skills/generate-mdl.md#L273) — promote L273 into a
     "When to author a view" subsection (from generate-mdl Phase 7).
  4. [`skills/onboarding.md`](skills/onboarding.md#L181) — fix the layout note; extend
     Step 5 self-review to add a doc-justified view.
  5. [`prompts/wren_onboarding.md`](prompts/wren_onboarding.md) — base onboarding
     stays **view-free (D1)**; one line that views may be *proposed* during
     doc-grounded review, not from raw structure.
  6. [`prompts/mdl_copilot.md`](prompts/mdl_copilot.md#L113) — add `{name, statement}`
     shape + "semantic SQL over model names" + description-as-recall.
- **Requirement:** each adapted file carries a provenance header (upstream path +
  fetch date).
- **Verification (no unit tests for prose):** eval pass — feed a doc describing a
  multi-model query pattern → Copilot proposes a view that passes Steps 1-2.
- **Risk R3 (hallucinated view):** D1 + doc-grounded trigger. **Risk R6 (missing
  description):** skill requires it. **Depends on:** Steps 1-3.

### [x] Step 6 — Phase 1 gate: suite + lint + eval + parity sign-off ✅ DONE
- Run full suite + `pre-commit run --all-files`. Confirm: semantic views author →
  validate (incl. cross-schema) → review (file-level diff) → activate → materialize
  → query (`transform_sql`). **Phase 1 = shippable Wren parity.** Record residual
  risks + expectation↔UI gaps.

---

## PHASE 2 — Native SQL views (accuracy hedge; spec §5.7, D6; gated on Step 0.5)

> **Step 0.5 RESULT changes this phase's economics — read before starting.**
> Native physical-table views are **confirmed to require the full bypass path (2B)**:
> wren-core resolves view bodies only against **model names**, so a raw-table view
> poisons manifest load and must be kept out of the engine entirely. Path 2A
> (engine-native) is **not available** in 0.7.x. The `dialect` field is storable but
> inert.
>
> **New evidence also narrows the value.** The spike showed semantic views already
> handle complex **CTE / WINDOW / multi-model JOIN** SQL — the *only* thing the LLM
> must change vs. a raw-SQL dump is **substituting physical table names with model
> names** (e.g. `raw_orders_v2` → `Orders`). That is a name mapping, not the full
> semantic-query synthesis the motivating finding measured. So:
>
> **RECOMMENDED GATE — run an eval BEFORE building 2B (Step 6.5).** Measure whether
> the §7 "name-substitution" prompt (physical→model, with the model list in context)
> yields acceptable semantic-view authoring accuracy. If **yes** → native views
> become a narrow, lower-priority feature (only for *unmodeled/external* tables or
> dialect-specific SQL) and 2B can be deferred. If **no** (accuracy still degrades)
> → build 2B as below. **Do not build the bypass machinery blind.**

### [ ] Step 6.5 — EVAL GATE for Phase 2 (run before building native bypass)
- **Requirement:** decide empirically whether native views are worth the bypass
  build, given the spike's finding that the LLM's only extra burden is physical→model
  **name substitution** (not query synthesis).
- **Procedure:** with Phase 1 shipped, run the view-authoring eval on docs containing
  raw-SQL blocks. Prompt variant A = "write semantic views over model names (here is
  the model↔table map)". Measure valid-view rate + correctness vs. a native-allowed
  baseline.
- **Decision:** acceptable accuracy ⇒ **defer 2B**; native reserved for
  *unmodeled/external* tables only (rare) — record as a follow-up. Material drop ⇒
  **build Steps 7-9 (2B)**.
- **Depends on:** Step 6 (Phase 1 shipped), eval harness ([[eval-framework-v2]]).

### [ ] Step 7 — Native marker + native validation branch (PATH 2B, confirmed)
- **Requirement:** a view with `dialect` set is treated as native; it is validated
  off the wren-core path and **never enters the engine manifest** (Step 0.5 proved a
  raw-table view poisons load).
- **Entrypoints / touchpoints:**
  - `dialect` already added to `MdlView`/`AuthoredView` in Step 3 (schema accepts it;
    inert in engine — store as our marker).
  - Validation branch in [`validate_project_manifest`](semantic_layer/mdl_validator.py#L444)
    / `_validate_views` ([mdl_validator.py:802](semantic_layer/mdl_validator.py#L802)):
    - **semantic** (`dialect is None`) → existing wren-core path (Step 1).
    - **native** (`dialect` set) → `validate_read_only_sql(statement, dialect=...)`
      ([tools/sql.py:33](tools/sql.py#L33)); **exclude native views from
      `to_wren_core_manifest` AND `to_engine_manifest`/`_merge_json`**
      ([mdl_compile.py:67](semantic_layer/mdl_compile.py#L67),
      [wren_materializer.py:166](semantic_layer/wren_materializer.py#L166)) so they
      never reach `SessionContext`. **This exclusion is the R9 fix — required, not
      optional.**
- **Requirement detail:** native `statement` must be **read-only** (the guard
  rejects DDL/DML) and is stored verbatim with its `dialect`.
- **Tests:** native view with valid read-only SQL → valid; with `INSERT/UPDATE/DDL`
  → rejected by the read-only guard; a semantic view still routes to wren-core; a
  native view does **not** break a manifest that also has models (load-isolation
  test).
- **Risk R9 (poison load):** path 2B isolation test is the guard. **Depends on:**
  Step 0.5, Step 1, Step 3.

### [ ] Step 8 — Native execution wiring (PATH 2B only; 2A unavailable in 0.7.x)
- **Path 2B (confirmed):** at query time, inline the native `statement` as a
  sub-select/CTE for `SELECT … FROM <native_view>` via the
  [`PassthroughEngine`](semantic_layer/engine/passthrough.py#L72), **or** surface
  native views as golden-query context (align with [[golden-queries-shared-memory]]).
  Add an optional **source dry-run** at validation time: `execute_sql` with a `LIMIT
  0` wrapper ([client.py:324](integrations/superset/client.py#L324), the
  [pipeline.plan_and_execute](semantic_layer/pipeline.py#L176) pattern) to prove the
  raw SQL runs.
- **Requirement:** native execution path must honor the same read-only + executor
  seam as the rest of the system (no new execution authority).
- **Tests:** end-to-end — a native view is queryable by name and returns rows
  (integration, behind `@requires_wren_core` / live-DB marker); dry-run rejects
  invalid raw SQL.
- **Risk:** added execution surface. **Mitigation:** reuse existing executor seam
  only; no bespoke runner. **Depends on:** Step 7.

### [ ] Step 9 — Native authoring guidance + governance guardrail
- **Requirement:** the model chooses semantic vs native *correctly*, and native
  doesn't sprawl.
- **Touchpoints:** extend Step 5's skill edits with the **semantic-vs-native rule**
  (spec §7 / §5.7): *prefer semantic when the pattern maps cleanly to models; use
  native (`dialect`) for complex/raw/uncertain patterns lifted from a document —
  don't reverse-engineer a fragile semantic query when a correct native one is in
  hand.* Add the optional coverage hint *"native view — consider promoting to
  semantic."*
- **Verification (eval):** doc with a complex raw-SQL block → Copilot captures a
  valid **native** view (not a degraded semantic one); doc with a clean multi-model
  pattern → **semantic** view. This is the direct test of the spec's motivating
  finding.
- **Risk R8 (native sprawl):** prompt bias + human review gate + coverage hint.
  **Depends on:** Steps 7-8.

### [ ] Step 10 — Phase 2 gate: suite + lint + eval + golden-query alignment note
- Full suite + `pre-commit`. Confirm both view kinds round-trip. Record the
  native-view ↔ golden-query relationship decision (store once vs twice).

---

## PHASE 3 — Optional follow-ups (defer unless asked)

### [ ] Step 11 — G5: view-coverage signal
- Extend [`copilot/coverage.py`](semantic_layer/copilot/coverage.py) so a documented
  multi-model/windowed pattern with no covering view (and native views lacking a
  description, or "promotable to semantic") surface as `partial` claims feeding
  self-review. **Depends on:** Step 5. **Recommend:** defer (D5).

### [ ] Step 12 — UI entity-type chip
- Optional "view" chip derived from the file path in
  [ChangesetReviewPanel.tsx](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/ChangesetReviewPanel.tsx).
  **Recommend:** defer (D4); the path already conveys type.

---

## Decision points (carried from spec; recommendation in bold)
- **D1 base onboarding view-free** → **Yes.** No code; enforced by keeping
  `generate_base_model` unchanged.
- **D2 dry-plan required?** → **RESOLVED: No.** Step 0 proved load-time validation is
  eager; Layer A suffices. Step 2 dropped.
- **D3 reference file vs inline skill** → **inline** (Step 5), lower surface.
- **D4 UI chip** → **defer** (Step 12).
- **D5 view coverage** → **defer** (Step 11).
- **D6 native views** → **RESOLVED to bypass-only (2B); now gated on the Step 6.5
  eval** (not just feasibility). Ship Phase 1 first; build native only if the eval
  shows physical→model name-substitution authoring still degrades materially.

## Dependency graph (quick reference) — post-spike
```
Step 0  ✅ done → G4 dropped (Step 2 ❌)
Step 0.5 ✅ done → native = bypass (2B)
Step 1 ─► Step 3 ─► Step 4
Step 3 ─► Step 5 ─► Step 6  ====  PHASE 1 SHIPS (semantic, full parity)  ====
Step 6 ─► Step 6.5 (EVAL GATE) ─► [if degraded] Step 7 ─► Step 8 ─► Step 9 ─► Step 10
Step 5 ─► Step 11 (opt) ; (none) ─► Step 12 (opt)
```

## Definition of done
- Phase 1: semantic views author→validate (incl. cross-schema)→review→activate→
  query, full parity, suite green, pre-commit clean, eval passes. Shippable alone.
- Phase 2: native views author (correct semantic/native choice)→dual-validate→
  execute by name, governance guardrail in place, suite green.
- Risks R1, R2, R4, R8, R9 each have a passing test or an explicit accepted-residual
  note. Out-of-scope (cubes, golden-queries store dedupe) recorded for follow-up.
