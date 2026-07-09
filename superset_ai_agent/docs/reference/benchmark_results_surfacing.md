# Benchmark results in native Superset (plan P5.2/P5.3, DP-B6)

Status: As-built reference (plan_benchmark_authoring_agent_impl.md Phase 5).

The reporting substrate is the **`ai_agent_eval_reporting` view** (migration
`0022_eval_reporting_view`, Postgres-only) over the `ai_agent_eval_*` tables:
one row per *(result, score, capability_tag)* with `effective_verdict`
(human override folded in), run provenance (`run_config`, `run_created_at`),
and `capability_tag` unnested from the item's tags. Validated by execution
against live Postgres (tag unnest, tag-less rows kept, override folding).

SQLite dev DBs skip the view (documented no-op in the migration) — there the
in-app BenchmarksPanel remains the results surface.

## One-time operator setup (postgres-only deployment)

1. **Run migrations** so the view exists in the agent database:
   the agent applies Alembic migrations at startup; confirm with
   `SELECT * FROM ai_agent_eval_reporting LIMIT 1;` on the agent DB.
2. **Create a read-only role** (never expose the agent DB read-write):

   ```sql
   CREATE ROLE eval_reporting_ro LOGIN PASSWORD '<secret>';
   GRANT CONNECT ON DATABASE <agent_db> TO eval_reporting_ro;
   GRANT USAGE ON SCHEMA public TO eval_reporting_ro;
   GRANT SELECT ON ai_agent_eval_reporting TO eval_reporting_ro;
   ```

3. **Register the connection in Superset** (Settings → Database Connections →
   + Database → PostgreSQL) with the read-only DSN, e.g.
   `postgresql://eval_reporting_ro:<secret>@<host>:5432/<agent_db>`.
   Leave DML/DDL disabled (default).
4. **Create the dataset**: Datasets → + Dataset → that connection →
   schema `public` → table `ai_agent_eval_reporting`.

## Suggested charts (single-config paradigm: trends over time, never config arms)

Filter every chart to `score_name = 'ex' OR score_name IS NULL` to count each
result once (other score rows — `soft_f1`, `leakage_suspected` — are
diagnostics).

- **Pass rate by capability tag** — bar: `capability_tag` ×
  `AVG(CASE WHEN effective_verdict = 'pass' THEN 1.0 ELSE 0 END)`; excludes
  `capability_tag IS NULL` rows via filter.
- **Pass rate over runs (regression trend)** — line: `run_created_at` (or
  `run_id`) × the same AVG, one series per benchmark.
- **Verdict mix per run** — stacked bar: `run_id` × `COUNT(*)` grouped by
  `effective_verdict` (pass/fail/needs_review/error).
- **Judge vs human agreement** — table: rows where `verdict_source =
  'llm_judge'`, split by whether an override exists (override = human
  disagreed) — the running judge-trust metric.
- **Leakage watch** — count of `score_name = 'leakage_suspected'` rows per run
  (should be ~0; a spike means example-recall exclusion needs a look).

The statistically careful **run-vs-run comparison** (paired delta + CI, spec
§16) stays in the BenchmarksPanel "Compare" action — a bare SQL delta between
two runs has no confidence interval, so don't rebuild that chart in SQL.

## Notes

- `run_config` records the fixed single config descriptively
  (`agent_config: "as-is"`, model, `layer: wren_bi`,
  `exclude_own_example: true`); historical rows written before the
  single-config alignment may carry legacy keys (`model`, `label`, `matrix`).
- Per-trial rows are present (`trial_index`); aggregate or filter to
  `trial_index = 0` depending on whether you want reliability or first-shot.
