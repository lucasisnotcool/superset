# Metric semantic translation fix — implementation plan

Status: SHIPPED — Layers 1–3 implemented + verified (2026-07-09). Layer 4
(migrate authoring to cubes/calc-cols, retire top-level `metrics`) deferred.

## As-built / verification

- **L2 (primary):** `semantic_layer/metric_inline.py` (new) — sqlglot AST inlining
  of metric names → measure expressions; wired at the top of
  `plan_semantic_sql_step` (before the passthrough branch, so both engines
  benefit), map built from `manifest.metrics` + `context.datasets[].metrics`, a
  real column of the same name always wins. `inlined_metrics` added to
  `PlanStepResult` + both graphs' plan trace.
- **L1:** `PlannedSql.correctable_warnings` (new); `WrenCoreEngine.plan_sql`
  splits manifest-load failure (degrade, non-correctable) from `transform_sql`
  rejection (`_rejected`, correctable) so a rejected draft feeds the re-draft
  loop instead of executing invalid SQL. L1.3 resolved: env already sets
  `WREN_ENGINE_MAX_CORRECTION_RETRIES=1`, no change needed.
- **L3:** `_SEMANTIC_SQL_GUIDANCE` (both graphs), `text_to_sql.md`, and
  `schema_retriever._metric_items` now mark a metric non-physical and instruct
  inlining; removed the false "engine rewrites metrics" claim.
- **Live probe (V.2):** real `WrenCoreEngine` + a manifest carrying
  `total_revenue = SUM(amount)`, draft `SELECT total_revenue FROM orders` →
  native_sql `SELECT sum(orders.amount) AS total_revenue FROM …`,
  `inlined_metrics=['total_revenue']`, no correctable warnings. ORA-00904 gone.
- **Tests:** `test_metric_inline.py`, `test_planning_metric_inline.py`,
  `test_semantic_guidance_metrics.py` (new) + additions to `test_semantic_engine.py`
  and `test_schema_retriever.py`. Regression sweep: 367 passed / 4 skipped.
  ruff clean; no new mypy errors (baseline pre-dirty).

### Residual gaps (not blocking the bug fix)
- graph.py `_plan_semantic_sql` short-circuits when the *configured* engine is
  `passthrough` (line ~1162), bypassing `plan_semantic_sql_step` and thus
  inlining. Default config is `wren_core`, so the bug path is covered; a
  passthrough-configured deployment would still need routing through the step.
- Layer 4 (author metrics as wren-native cubes / calculated columns and stop
  emitting top-level `metrics` + the invisible `model["metrics"]` in
  `mdl_exporter.py:93`) remains the true structural alignment.

## Original plan

## Problem (source-backed, empirically proven)

The AI SQL agent throws HTTP 500 `<metric>: invalid identifier` (Oracle
ORA-00904) when a question uses a top-level MDL `metric`. Root cause chain,
proven against the installed engine (`wren_core` 0.7.1):

1. **wren_core 0.7.1 has no `metrics` concept.** Round-tripping a manifest
   through the engine drops the `metrics` key entirely — surviving top-level
   keys are `[catalog, cubes, dataSource, layoutVersion, models, relationships,
   schema, views]`. This held even for a perfectly-shaped Wren metric
   (`baseObject` + `measure[]`). The only aggregation object 0.7.1 supports is
   `cubes` (queried via `cube_query_to_sql`, never raw SQL).
2. `SELECT total_revenue FROM orders` → `transform_sql` **rejects** it:
   `Schema error: No field named total_revenue`. The metric was dropped, so the
   name is unknown.
3. On that rejection `WrenCoreEngine.plan_sql` **degrades to passthrough**
   (`wren_core_engine.py:111-116`) → returns SQL **unchanged** → the raw metric
   name reaches Oracle → ORA-00904.
4. `SELECT SUM(amount) AS total_revenue FROM orders` (expression **inlined**)
   transforms cleanly. → the fix is: emit the metric's expression, not its name.

Why the LLM names the metric:
- `graph.py:117-125` `_SEMANTIC_SQL_GUIDANCE` lists "metrics" as referenceable
  and falsely promises "the semantic engine rewrites your query into native SQL".
- `schema_retriever.py:193-236` `_metric_items` renders a metric with
  `model=<baseObject>`, folding it in beside that model's columns with no
  "not a physical column" signal.
- `prompts/text_to_sql.md:52` says "use that exact formula" but never says the
  metric name itself is not selectable.
- `mdl_compile.py:88-89` forwards `metrics` to the engine, which drops them.

## Wren's actual expectation (research)

A reusable aggregation is **not** a top-level `metric` and never a column. In
wren_core it is a **cube** (`base_object` + `measures[]`, queried structurally)
or a **calculated column** (`is_calculated: true`, `expression`). Legacy Java
wren-engine had top-level metrics with `baseObject`/`measure[]`/`dimension[]`;
the Rust wren-core replaced them with cubes. The fork's `{name, expression,
description}` shape matches neither generation.

Because the agent writes raw SQL through `transform_sql` (not cube queries), the
correct query-time behavior is: **inline the metric expression**. Cubes/calc-cols
are the Layer-4 (deferred) structural alignment.

## Touchpoints

- `semantic_layer/engine/wren_core_engine.py` — `plan_sql`, `_degraded` (L1)
- `semantic_layer/engine/planning.py` — `plan_semantic_sql_step`, `PlanStepResult` (L1/L2)
- `semantic_layer/metric_inline.py` — NEW helper (L2)
- `graph.py` — `_SEMANTIC_SQL_GUIDANCE` (L3), `_plan_semantic_sql` trace (L2)
- `conversation_graph.py` — mirror plan-step wiring if it inlines separately (verify)
- `semantic_layer/schema_retriever.py` — `_metric_items` text (L3)
- `prompts/text_to_sql.md` — metric guidance (L3)
- `config.py` — `wren_engine_max_correction_retries` default (L1 decision)

## Checklist

### Layer 2 — inline metrics before planning (PRIMARY FIX)
- [ ] L2.1 New `semantic_layer/metric_inline.py`: `inline_metrics(sql, *,
      metrics: Mapping[str,str], dialect) -> InlineResult(sql, inlined: list[str])`.
      sqlglot-based: replace unqualified `exp.Column` refs whose name matches a
      metric name (case-insensitive) with `(<expression>)`, preserving the
      projection alias (`(<expr>) AS <name>`). Skip qualified columns
      (`t.metric`), alias positions, and names that collide with a real column of
      a referenced model (physical column wins). Degrade closed: on any sqlglot
      parse error return the SQL unchanged.
- [ ] L2.2 Build the metric→expression map from BOTH sources in
      `plan_semantic_sql_step`: `manifest.metrics` (dicts; tolerate `expression`
      or `measure[].expression`) and `context.datasets[].metrics`
      (`MetricSummary.expression`). MDL metric wins on name clash.
- [ ] L2.3 Apply inlining at the TOP of `plan_semantic_sql_step`, BEFORE the
      passthrough early-return, so passthrough backends benefit too. Add
      `inlined_metrics: list[str]` to `PlanStepResult`; keep `semantic_sql` =
      original draft for audit; feed inlined SQL into `engine.plan_sql`.
- [ ] L2.4 Surface inlined metrics in `_plan_semantic_sql` trace details.
- [ ] L2.5 Tests: unit tests for `inline_metrics` (projection, group-by,
      qualified-skip, column-collision-skip, parse-failure passthrough) +
      plan-step test proving a metric-named draft becomes expression SQL and
      wren_core transforms it.

### Layer 1 — stop forwarding engine-rejected SQL as if valid
- [ ] L1.1 In `WrenCoreEngine.plan_sql` distinguish **rejection**
      (`transform_sql` raised) from **degrade** (engine absent / unmapped
      dialect). Add a `correctable` flag (or a distinct warning class) to the
      rejection path so it feeds the re-draft loop rather than silently
      executing.
- [ ] L1.2 In `plan_semantic_sql_step` route a wren_core rejection into
      `correctable_warnings`.
- [x] L1.3 DECISION — RESOLVED: no code/env change needed. Both `.env` and
      `.env.example` already set `WREN_ENGINE_MAX_CORRECTION_RETRIES=1`
      (env.example:263), so the re-draft loop is already active in deployment;
      the L1.1/L1.2 rejection routing engages it. Code default left at 0
      (conservative; env is the deployment source of truth).
- [ ] L1.4 Tests: plan-step test that a genuinely-unknown identifier yields a
      correctable warning (not a silent passthrough execute).

### Layer 3 — context + prompt guidance
- [ ] L3.1 `schema_retriever._metric_items`: mark metrics non-physical and
      instruct inlining, e.g. `metric total_revenue (aggregate measure — NOT a
      selectable column) on orders — inline its formula SUM(amount)`.
- [ ] L3.2 `graph._SEMANTIC_SQL_GUIDANCE`: drop the false "engine rewrites
      metrics" claim; state metrics are formulas to inline, never referenced by
      name as a column.
- [ ] L3.3 `prompts/text_to_sql.md:6,52`: state the metric name is not a
      selectable identifier; always substitute its measure expression.
- [ ] L3.4 Tests: assert the guidance/prompt no longer promise metric-name
      rewriting (string assertions) + `_metric_items` text marks non-physical.

### Verify
- [ ] V.1 `venv/bin/python -m pytest tests/unit_tests/superset_ai_agent/ -q`
      for touched areas.
- [ ] V.2 Live probe: draft `SELECT total_revenue FROM orders` end-to-end →
      confirm executed SQL is `SUM(amount)`-based, no ORA-00904.
- [ ] V.3 Report dev-intent vs impl gaps + user-expectation vs actual-UI gaps.

## Risks / mitigations
- **R1 naive text substitution corrupts SQL** → use sqlglot AST, skip qualified
  refs + real-column collisions, degrade-closed on parse error.
- **R2 aggregate metric inlined without GROUP BY** → produces a valid aggregate;
  grouping is the drafter's job (Layer 3 guidance reinforces). Not worse than today.
- **R3 conversation_graph has a parallel path** → verify it routes through the
  same `plan_semantic_sql_step`; if not, mirror L2 there.
- **R4 default-retry flip changes behavior broadly** → gated as L1.3 decision;
  env-flagged.
