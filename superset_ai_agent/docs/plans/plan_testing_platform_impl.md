<!--
Implementation checklist for the Testing & Evaluation Platform.
Spec: evaluation/TESTING_PLATFORM_SPEC.md (Revision 2, §12-§17) — ALL decision
points DP-1..DP-21 ACCEPTED by stakeholder 2026-07-03, including DP-16 (native
product surfaces; Phoenix demoted to optional eng sidecar) and the P1 re-order
(Project Benchmarks before the prompt registry).

HOW TO USE THIS FILE (future sessions): work top-to-bottom. Each item has
Status / Requirements / Touchpoints / Tests / Risks. Flip Status to DONE with a
one-line as-built note (+ test count) when completed; add BLOCKED notes inline.
Do not reorder items — later items assume earlier schemas/services exist.
-->

# Testing Platform — Implementation Plan & Checklist

**Scope of this plan:** P0 (foundation) + P1 (F11 Project Benchmarks, the headline
use case) in full; P2/P3 items listed as stubs with dependencies so future
sessions can continue. Spec traceability: each item cites its spec section.

**Sources (industry grounding):** Genie Benchmarks (docs.databricks.com/aws/en/genie/benchmarks —
three-way verdict, 4-sig-fig tolerance, async runs, ≤500 items), Wren AI Cloud
Evaluation/AI Advisor (docs.getwren.ai/cp/guide/evaluation — pass/fail with named
reasons, manual override, regression-before-apply), BIRD mini_dev
(github.com/bird-bench/mini_dev — soft-F1, tie sorting), Snowflake VQR
(logical-name rule, verified_by/at), tau-bench pass^k (arXiv:2406.12045),
Anthropic error bars (anthropic.com/research/statistical-approach-to-model-evals),
Langfuse score data model + OTel `gen_ai.evaluation.result` (semconv ≥1.39) for
score-row shape.

---

## Architecture summary (decided)

- **New package** `superset_ai_agent/evals/` — comparator, typed-spec scorer,
  stats, Pydantic schemas, store (Protocol + InMemory + SqlAlchemy). Pure logic
  has no FastAPI imports (unit-testable offline).
- **5 new tables** (`ai_agent_eval_*`), Alembic `0019`. Immutability rule:
  results freeze the item's `question`/`answer_spec` at run time (no dataset
  version table in v1 — run rows carry `benchmark_checksum`; a frozen copy per
  result answers "what exactly did this run test").
- **Routes live in `app.py`** (matches the existing monolith convention), gated
  by `authorize_semantic_project` (project permission, NOT admin — F11 is
  curator-facing) + a `wren_benchmarks_enabled` config flag (default **True**;
  the surface is inert until used and holds no background workers).
- **Runs are jobs**: eval-run store follows `coverage_store.py` verbatim
  (create/claim CAS/progress/complete/fail/supersede), executed on
  `active_job_runner` (ThreadJobRunner prod, InlineJobRunner tests). Progress +
  completion emit `benchmark_*` events through `_append_semantic_event` → the
  existing project events SSE; frontend reuses `useProjectEvents`.
- **Agent invocation**: the run route builds the request-bound
  `TextToSqlGraph` + superset client (`build_text_to_sql_graph` /
  `build_superset_runtime`, app.py:502-541) at submit time and closes over them
  in the job. Gold SQL executes via `superset_client.execute_sql(...)`
  (integrations/superset/client.py:151) — same engine/session family as the
  agent's own execution (§16.6), under the caller's credentials (BYO-creds
  authz preserved).
- **Scores**: normalized `ai_agent_eval_scores` rows shaped like OTel
  `gen_ai.evaluation.result` (name / value / label / explanation / source), plus
  a denormalized three-way `verdict` on the result row for cheap UI.

## Global decision log (from spec §17.1, all accepted)

| DP | Resolution |
|---|---|
| DP-16 | Native surfaces; no Phoenix in product path |
| DP-17 | answer_spec supports `gold_sql` \| `expected_values` (typed) \| `eval_note`; `eval_note` items score `needs_review` in v1 (LLM-judge lands P2/F4) |
| DP-18 | One artifact two roles: `use_as_example` + promote/import endpoints; leakage guard at run time |
| DP-19 | Scientist v1 user-triggered (P3) |
| DP-20 | CI gating via DeepEval pytest later; not in this plan's P0/P1 |
| DP-21 | Comparator is our own code (evals/comparator.py); BIRD soft-F1 + Genie tolerances as the reference definitions |

Implementation-level decisions made while grounding (recorded so future
sessions don't re-litigate):
- **I-1** No dataset-version table in v1; immutability via per-result frozen
  copies + `benchmark_checksum` on runs. Revisit if benchmark forking is needed.
- **I-2** `wren_benchmarks_enabled` defaults True (user-triggered surface only).
- **I-3** One active run per benchmark: submitting a run supersedes in-flight
  runs for the same benchmark (coverage-store pattern).
- **I-4** Trials default 1 in the API (UI offers 1/3); pass^k reported when
  trials>1. (3-trial spec default is a UI default, not a server force —
  keeps dry-runs cheap.)
- **I-5** Row previews stored on results are capped (50 rows) — full result
  sets are not persisted (storage + PII prudence). Verdict math runs on the
  full in-memory sets before capping.
- **I-6** Leakage guard v1 = detection not prevention: a result whose recalled
  golden example matches the item's question is flagged
  (`leakage_suspected=true` score) rather than re-plumbing memory recall.
  Prevention (recall exclusion) is P1.6-b, needs a `memory.recall` param.

---

## P0 — Foundation

### ☑ P0.1 Schema: `ai_agent_eval_*` models + migration `0019_eval_benchmarks`  [spec §3, §12.4, §14]
**Status:** DONE — 5 models appended to persistence/models.py + migration 0019_eval_benchmarks; exercised by P0.4 store tests.
**Requirements**
- Tables: `ai_agent_eval_benchmarks`, `ai_agent_eval_items`,
  `ai_agent_eval_runs`, `ai_agent_eval_results`, `ai_agent_eval_scores`.
- Benchmarks: project-scoped (`project_id` idx), name, description, owner audit,
  soft delete. Items: `benchmark_id` idx, position, question, `answer_type`,
  `answer_spec` JSON, `capability_tags` JSON, `verified_by/verified_at`,
  `use_as_example`, soft delete (results reference dead items safely).
  Runs: status lifecycle `pending|running|complete|failed|superseded`, `trials`,
  `config` JSON, `mdl_checksum`, `benchmark_checksum`, `database_id`,
  `score`, `totals` JSON, `progress` JSON, `error`. Results: run_id idx,
  item_id idx, `trial_index`, frozen `question`+`answer_spec`, agent sql/status,
  capped row previews, three-way `verdict` + `verdict_source`, human override
  columns, `duration_ms`. Scores: OTel-shaped
  (`name`,`value` float?,`label` str?,`explanation`,`source`).
- No DB-level FKs to project tables (codebase convention: logical FKs, cascade
  in store code).
**Touchpoints:** `persistence/models.py` (append), new
`persistence/migrations/versions/0019_eval_benchmarks.py` (down_revision =
`0018_db_tied_artifacts`).
**Tests:** exercised via P0.4 store tests (sqlite create_all + round-trip).
**Risks:** JSON columns differ sqlite/postgres — mitigated: follow existing JSON
usage (all agent tables already do this).

### ☑ P0.2 Comparator v2 (`evals/comparator.py`) + typed-spec scorer (`evals/typed_spec.py`)  [spec §16.1-16.7]
**Status:** DONE — evals/comparator.py + evals/typed_spec.py; 27 tests green (test_eval_comparator.py).
**Requirements (normative — from spec §16)**
1. Multiset row semantics (bag compare); canonicalize cells first: numeric
   strings→float, Decimal→float, bool distinct from int, date/datetime→ISO,
   None normalized; strings trimmed (casefold optional flag).
2. Numeric match at 4 significant digits default (`sig_digits=4`), optional
   relative tolerance override.
3. Row order ignored unless `ordered=True` (gold has top-level ORDER BY —
   caller detects via sqlglot or flag); ordered mode compares position-wise but
   sorts within tie-groups.
4. Column order/name invariant: greedy best value-alignment of gold columns to
   predicted columns (BIRD soft-F1 method).
5. Dual scores: binary `ex` (pass/fail) + `soft_f1` (matched cells TP, extra
   predicted cells FP, missing gold cells FN). `extra_columns_policy`:
   `strict` (extras fail EX — Genie) vs `lenient` (extras don't fail EX if all
   gold columns matched — Spider 2.0); default **strict**, both always reflected
   in soft_f1.
6. Empty-vs-empty match ⇒ `ex=pass` but `low_confidence=True` (EX
   false-positive guard).
7. Output: `ComparisonOutcome{verdict, ex, soft_f1, matched_cells, fp, fn,
   low_confidence, reasons: list[str]}` — named reasons per Wren ("Column count
   mismatch…", "2 gold cells unmatched…").
- Typed spec (`typed_spec.py`): generalize `evaluation/seagate_scoring.py`
  semantics → spec JSON `{nums:[...], tolerance?, names:[...], absent:[...],
  trap:bool, zero:bool, multi_value?}` scored against rows; verdicts
  `pass|fail|needs_review` (trap_ok→pass, manual→needs_review). Pure function,
  no imports from evaluation/ (that folder is not a package on the app path).
**Touchpoints:** new `evals/__init__.py`, `evals/comparator.py`,
`evals/typed_spec.py`.
**Tests:** `tests/unit_tests/superset_ai_agent/test_eval_comparator.py` — tie
groups, sig-fig rounding (0.12345 vs 0.1235), column permutation, alias
mismatch, extra column strict vs lenient, empty-empty low-confidence, multiset
duplicates, NULL handling, typed nums/names/absent/trap/zero.
**Risks:** greedy column alignment is O(cols²·rows) — fine at agent row caps
(≤1000); noted for huge results (rows capped before compare at 5000).

### ☑ P0.3 Stats module (`evals/stats.py`)  [spec §16 "Statistics", tau-bench, Anthropic]
**Status:** DONE — evals/stats.py; 10 tests green (test_eval_stats.py).
**Requirements**
- `pass_hat_k(per_item_trial_verdicts) -> float` — fraction of items whose ALL
  k trials passed; also per-item breakdown.
- `mean_pass_rate(...)` — trial-mean per item, then item-mean.
- `paired_delta_ci(a_by_item, b_by_item, n_boot=2000, seed=7)` — bootstrap CI
  over per-item paired deltas on the SHARED item set; returns
  `{delta, ci_low, ci_high, significant (0 outside CI), n}`. Deterministic
  (seeded); pure Python (no numpy/scipy dependency — keep agent deps lean).
- `compare_runs(results_a, results_b)` — joins on item_id, emits
  improved/regressed/unchanged lists + the paired CI verdict.
**Touchpoints:** `evals/stats.py`.
**Tests:** `test_eval_stats.py` — pass^k vs pass@1 divergence, CI includes 0 on
identical runs, CI excludes 0 on a large uniform improvement, disjoint item
sets → only shared items compared.

### ☑ P0.4 Eval store (`evals/store.py`, `evals/schemas.py`)  [pattern: semantic_layer/coverage_store.py]
**Status:** DONE — evals/schemas.py + evals/store.py (Protocol + InMemory + SqlAlchemy); 22 param'd tests green (test_eval_store.py). App wiring lands with P1.1.
**Requirements**
- Pydantic domain schemas: `Benchmark`, `BenchmarkItem`, `EvalRun`,
  `EvalResult`, `EvalScore`, `RunProgress`, `RunTotals` (+ request/response
  models used by the API kept in `evals/schemas.py` too).
- `EvalStore` Protocol + `InMemoryEvalStore` + `SqlAlchemyEvalStore`:
  benchmarks CRUD (soft delete), items CRUD (soft delete, reorder), runs
  `create/claim (CAS pending→running)/report_progress/complete/fail/
  supersede(benchmark_id)/get/list_for_benchmark/active_run`,
  results `add (with scores)/list_for_run/get/override_verdict`,
  `benchmark_checksum(benchmark_id)` (sha256 over ordered item
  id+question+answer_spec).
- Wire into `create_app`: `eval_store` kwarg + `_create_eval_store(config,
  session_factory)` (memory when no DB — matches `_create_coverage_run_store`).
**Touchpoints:** `evals/store.py`, `evals/schemas.py`, `app.py` (DI block
~line 415), `_create_*` factory near the others.
**Tests:** `test_eval_store.py` parameterized memory/sqlalchemy (copy
`test_coverage_store.py` harness): CRUD, CAS claim single-winner, supersede,
override, checksum stability.

---

## P1 — F11 Project Benchmarks

### ☑ P1.1 Benchmark + item CRUD API  [spec §14 data model; pattern: instructions/golden-query routes]
**Status:** DONE — 9 CRUD routes + flag + validation in app.py; wren_benchmarks_enabled in config.py; covered in test_benchmark_api.py (13 tests green, incl. P1.2 + flywheel).
**Requirements**
- Routes (all under `/agent/semantic-layer/projects/{project_id}/…`, all
  through `authorize_semantic_project`; write ops require permission="write"):
  - `GET|POST /benchmarks`; `PATCH|DELETE /benchmarks/{benchmark_id}`
  - `GET|POST /benchmarks/{id}/items`;
    `PATCH|DELETE /benchmarks/{id}/items/{item_id}`
- Item create/update validates `answer_type`∈{gold_sql,expected_values,
  eval_note} and shape-checks `answer_spec` (pydantic discriminated union);
  gold_sql must be non-empty read-only SQL (reuse
  `tools/sql_policy` validation); ~500-item soft cap per benchmark (409 above).
- Flag guard `_require_benchmarks_enabled()` (mirrors `_require_copilot_enabled`).
**Touchpoints:** `app.py` (new route block after golden-queries ~line 3660),
`config.py` (`wren_benchmarks_enabled`), `evals/schemas.py`.
**Tests:** `test_benchmark_api.py` — TestClient app with memory stores +
stubbed access service (copy harness from `test_copilot_api.py`): CRUD happy
path, 403 on read-only principal for writes, 404 unknown project, invalid
answer_spec 422, cap 409, flag-off 404/403.

### ☑ P1.2 Dry-run endpoint (gold preview + comparator sanity)  [spec §6.3, §14 UI; Wren "Preview data"]
**Status:** DONE — dry-run route (gold preview / typed-spec echo / note echo, 400 on SQL error); tests in test_benchmark_api.py.
**Requirements**
- `POST /benchmarks/{id}/items/{item_id}/dry-run` — executes `gold_sql` via the
  caller's superset runtime (`build_superset_runtime`) against the resolved
  database (project `default_database_id`, else caller's fingerprint-matched
  connection via the access service); returns capped rows + column list +
  row_count, or validation errors. For `expected_values`, echoes the parsed
  spec; for `eval_note`, echoes note (nothing to execute).
- Never persists anything; read permission suffices (execution rides the
  caller's own DB rights).
**Touchpoints:** `app.py`.
**Tests:** in `test_benchmark_api.py` with a fake superset client injected via
`create_app(superset_client=...)`: gold rows preview, SQL error surfaced as
400 with reason, eval_note echo.

### ☑ P1.3 Benchmark run job  [spec §14 Runs; pattern: `_schedule_coverage`/`_run_coverage_job` app.py:1549-1751]
**Status:** DONE — submit route + _run_benchmark_job in app.py (claim/loop/score/persist/progress/events, supersession, gold cache, leakage flag I-6); 11 tests green (test_benchmark_run_job.py).
**Requirements**
- `POST /benchmarks/{id}/runs` body `{trials?=1, item_ids?, execute?=true}` →
  authorize(write) → resolve `database_id` → supersede in-flight runs for the
  benchmark → create run (with `benchmark_checksum`, `mdl_checksum` via
  `_active_mdl_checksum`, config snapshot) → build request-bound graph +
  superset client NOW → `active_job_runner.submit(partial(_run_benchmark_job,…))`
  → 202 with run id.
- `_run_benchmark_job`: claim (CAS) → for each item × trial:
  1. agent: `graph.run(AgentQueryRequest(question, database_id, project_id,
     execute=True), owner_id=…)`;
  2. gold: `gold_sql` → `superset_client.execute_sql(...)`; `expected_values` →
     no gold execution; `eval_note` → skip compare;
  3. score: comparator / typed_spec → verdict + scores (+`low_confidence`,
     `leakage_suspected` per I-6); `eval_note` ⇒ `needs_review` (DP-17);
     agent status=="error" ⇒ verdict `error`;
  4. persist result (frozen question/spec, capped previews, matched_models
     from `wren_context`, duration) + score rows;
  5. progress tick every item (`report_progress`), event
     `benchmark_run_progress` only every N=5 items (SSE flood guard,
     coverage precedent).
  - finish: totals + `score` (pass rate; pass^k when trials>1) →
    `complete` → event `benchmark_run_complete`. Any per-item exception ⇒
    verdict `error` on that item, run continues; job-level exception ⇒
    `fail(error)`.
- Superseded mid-run: check `status=="superseded"` between items → stop
  quietly (coverage `_superseded()` pattern).
**Touchpoints:** `app.py` (route + job fn near coverage jobs).
**Tests:** `test_benchmark_run_job.py` — `create_app(job_runner=InlineJobRunner(),
text_to_sql_graph=FakeGraph, superset_client=FakeSuperset, …)`: full run
completes with mixed verdicts, gold execution failure → item error not run
fail, supersession stops the loop, progress/events emitted, pass^k on 2 trials,
frozen spec on results.
**Risks:** long runs may outlive the caller's Superset token in
`user_session` mode (graph captured at submit). Documented; acceptable v1
(mitigation options: service-account mode unaffected; item-level auth errors
surface as item `error` results, run completes).

### ☑ P1.4 Runs / history / compare / override API  [spec §14 UI; Genie Evaluations tab; Braintrust diff]
**Status:** DONE — runs list/get/results/compare/override routes; compare uses paired_delta_ci + benchmark_changed; override recomputes totals; covered in test_benchmark_run_job.py.
**Requirements**
- `GET /benchmarks/{id}/runs` (newest-first summaries),
  `GET …/runs/{run_id}` (run + totals + progress),
  `GET …/runs/{run_id}/results` (per-item, incl. previews + scores),
  `GET …/runs/{run_id}/compare/{other_run_id}` → `compare_runs` output
  (improved/regressed lists + paired CI; refuses cross-benchmark compare),
  `POST …/runs/{run_id}/results/{result_id}/override`
  `{verdict, comment}` → stored as HUMAN-source score + override columns
  (Wren manual-override precedent), recomputes run totals.
**Touchpoints:** `app.py`, `evals/schemas.py` (response models).
**Tests:** in `test_benchmark_api.py`: history ordering, compare math flows
through (seeded via two InlineJobRunner runs with different fake-graph
accuracy), CI fields present, override mutates verdict + totals + audit trail,
cross-benchmark compare 400.

### ☑ P1.5 Frontend: BenchmarksPanel in MDL Lab  [spec §6.2, §14 UI; template: GoldenQueriesPanel/CoveragePanel]
**Status:** DONE — BenchmarksPanel.tsx (items editor w/ 3-mode answer-spec dialog + dry-run, run bar w/ trials + poll-based progress, Evaluations history, run-detail modal w/ verdicts/previews/override, compare w/ CI banner) + 13 api.ts helpers + Benchmarks tab in index.tsx; 11 jest tests green (BenchmarksPanel.test.tsx). Note: Superset Modal wrapper uses show/onHide, not antd open/onCancel.
**Requirements**
- New `SemanticLayerEditor/BenchmarksPanel.tsx`: items table (question,
  answer type chip, tags, verified, use-as-example), add/edit dialog with
  typed answer-spec editor (three modes per DP-17) + **Dry run** preview
  button; Run bar ("Run all / Run selected", trials 1|3) with live progress
  (reuse `useProjectEvents` for `benchmark_run_*`+ polling fallback);
  Evaluations history list → run detail: per-item verdict rows,
  agent-SQL vs gold-SQL, result-preview vs gold-preview side-by-side,
  reasons, override control; "Compare to run…" view with improved/regressed
  coloring and the CI banner (never a bare delta — §16).
- `api.ts`: helper per endpoint (follow existing fetch helpers +
  `getAgentBaseUrl()`); antd/@superset-ui components only; no custom CSS.
- Tab/entry wiring in `SemanticLayerEditor/index.tsx` next to
  Coverage/GoldenQueries panels (follow whatever nav pattern index.tsx uses).
**Touchpoints:** `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts`,
`…/SemanticLayerEditor/index.tsx`, new `BenchmarksPanel.tsx` +
`BenchmarksPanel.test.tsx`.
**Tests:** jest/RTL mirroring `GoldenQueriesPanel.test.tsx`: renders items,
add-item dialog validation, dry-run renders preview, run button fires POST +
shows progress, run detail renders verdicts + override, compare renders CI
text. `npm run test -- BenchmarksPanel.test.tsx`.
**Risks:** UI-vs-expectation gaps to re-check after build (see Gap log).

### ☑ P1.6 Golden-query flywheel  [spec §14 flywheel, DP-18; pattern: promote_golden_query app.py:3609]
**Status:** DONE (a): promote-example + import-golden routes (dedupe, cap-aware) + UI buttons; leakage detection score in run job (I-6). (b) recall-exclusion DEFERRED to P2.4 as planned. Tests in test_benchmark_api.py + leakage test in test_benchmark_run_job.py.
**Requirements**
- (a) `POST …/items/{item_id}/promote-example` — writes the item's
  question+gold_sql into the project golden set (reuse
  `upsert_golden_query`/`find_golden_queries_file`) and sets
  `use_as_example=true`; `POST …/benchmarks/{id}/import-golden` — creates
  items from the project's golden queries (skip duplicates by question).
- (b) leakage: v1 detection only (I-6) — implemented in P1.3 scoring
  (`leakage_suspected` score when a recalled example's question ==
  item question, read from response wren_context/recall metadata when
  available). Full recall-exclusion = P2 follow-up (touch `memory.py` recall
  signature + graph pass-through).
**Touchpoints:** `app.py`, `evals/store.py` (set flag), frontend buttons in
BenchmarksPanel (promote/import).
**Tests:** API tests: promote writes queries.json content via fake mdl file
store + flags item; import creates items, dedupes; UI test for the buttons.

### ☑ P1.7 Verification pass + gap log
**Status:** DONE — new-code tests: 83 backend (27 comparator + 10 stats + 22 store + 13 api + 11 run-job) + 11 jest, all green. Full agent suite: 1325 passed / 3 PRE-EXISTING failures (reproduced with changes stashed: test_llm_usage_store day-bucket ×2, test_multi_schema_schema_index bulk-activate — unrelated). SemanticLayerEditor: 32 suites / 275 tests green. Ruff clean on all touched files; prettier applied. pre-commit hook install blocked by local SSL cert error; mypy not in venv — run `pre-commit run --all-files` in a networked env before pushing.

---

## P2/P3 — stubs for future sessions (dependencies noted)

- **P2.1 Prompt registry — DONE.** Tables `ai_agent_prompt_versions`/`_labels`
  (migration 0020); `prompts/store.py` (Protocol + InMemory + SqlAlchemy,
  append-only versions, `production` label); `registry.set_prompt_resolver`
  seam with 5s TTL cache + fail-safe file fallback (installed in create_app);
  5 admin routes (`/agent/admin/prompts…`, candidate→promote→reset, all
  `require_admin`); admin page `src/pages/AiAgentPrompts/` + route
  `/ai-agent/prompts/` + FAB menu link. 14 backend (test_prompt_registry.py)
  + 4 jest tests green. NOTE: resolver is a process-global seam — last
  create_app wins (fine in prod single-app; tests reset via fixture).
- **P2.2 LLM judge (F4) — DONE.** `evals/judge.py`: binary pass/fail + written
  critique against the author's eval_note rubric; PoLL panel via
  `wren_benchmark_judge_votes` (majority; ties/malformed/errors → needs_review);
  wired into run-job scoring behind `wren_benchmark_judge_enabled` (default on;
  off → needs_review as before); judged results carry
  `verdict_source=llm_judge` + critique in reasons. 7 unit tests
  (test_eval_judge.py) + 2 run-job tests. NOTE: a formal `Evaluator` Protocol
  registry was folded into the scoring dispatch (three answer types, one
  dispatcher) — revisit only if third-party evaluators become a need.
- **P2.3 Experiments (F5) — PARTIAL.** Shipped: per-run **capability ×
  verdict breakdown** (`RunTotals.by_capability` computed from item tags in
  the run job; tags rendered on run rows in the UI) + per-run `model` and
  `exclude_example_recall` recorded in run config — so manual config sweeps
  are runnable+comparable today (submit runs with different model/prompt
  state, compare with the CI'd diff). UPDATE (same day): **matrix fan-out SHIPPED** —
  `POST …/matrix-runs` (≤6 labeled arms, batch-safe supersession via
  `supersede_runs(except_run_ids=)`, per-arm model/exclusion; 2 tests) + UI
  sweep (comma-separated models in the model input submits a matrix; run rows
  show per-capability tags, so capability × config reads off the run list).
  by_capability IS now recomputed on override (from current item tags;
  deleted items drop out of the pivot — frozen results stay authoritative).
  STILL DESCOPED (rationale): a dedicated pivot-table admin surface (the run
  list + labels covers the need at ≤6 arms); `run_eval_v4.py --write-db` (the
  offline harness lives in a separate env without agent-DB creds — in-product
  benchmarks now cover the persistent-runs need; revisit only if the offline
  matrix must feed the same history).
- **P2.4 Recall-exclusion leakage guard — DONE.**
  `AgentQueryRequest.exclude_example_questions` (server-side) filtered in
  `graph._draft_sql` over BOTH memory and golden recall (normalized question
  match — post-recall filter, no Memory-protocol change needed);
  `BenchmarkRunRequest.exclude_example_recall` (default True) threads it per
  item; leakage_suspected flag now only fires when exclusion is deliberately
  off. 2 run-job tests.
- **P3.1 Scientist v1 — DONE (v2/v3 remain).** `evals/scientist.py`:
  diagnosis taxonomy (schema_linking/join_path/aggregation/filter_value/
  time_semantics/test_is_wrong/other → each mapped to an MDL fix type),
  code-computed paired-CI stats gate injected into the prompt (within-noise
  runs MUST be reported as noise), parse-degradation fallback, all-pass
  short-circuit (no model call). Endpoint POST …/runs/{id}/analyze
  (write-authz, 409 unless complete) persists a `kind="scientist"`
  conversation; UI "Analyze failures" button + report rendering in the run
  modal. 6 tests (test_eval_scientist.py). UPDATE (same day): **v2 + v3 SHIPPED** — v2:
  `POST …/runs/{id}/handoff-copilot` (write-authz + copilot flag; refuses
  no-failure runs; analyst report + failure evidence seed a synchronous
  `run_copilot` turn; STAGED changeset returned + persisted as a `scientist`
  conversation with the changeset artifact; never auto-applied;
  `verification_hint` tells the user to re-run + compare) + UI "Propose MDL
  fixes" button rendering the staged items. v3:
  `wren_benchmark_auto_analyze_enabled` (default off) chains an auto-analysis
  job after failed runs (`benchmark_analysis_ready` event). 3 tests.
  REMAINING (small): automatic changeset→verification-run annotation (today
  the loop closes manually via re-run + compare); apply flow rides the
  existing Copilot apply UI rather than an in-panel diff.
- **P3.2 Model sweeps — DONE.** `BenchmarkRunRequest.model` →
  `AgentQueryRequest.model` (graph already passed request.model to
  ModelClient.chat); recorded in run config; UI model-override input on the
  run bar. 2 tests. NOTE: judge/scientist calls use the default model (their
  own override is a knob for later).
- **P3.3 OTel export — DONE (scores).** `evals/otel_export.py` serializes
  run scores as `gen_ai.evaluation.result` events (semconv ≥1.39 attrs +
  namespaced correlation attrs); GET …/runs/{id}/export-otel. DESCOPED BY DESIGN
  (recorded 2026-07-03): agent *trace spans* (ai_agent_events → OTLP spans)
  and a push transport — ai_agent_events rows are not spans (no
  duration/parent hierarchy), so a faithful mapping needs span capture at the
  graph layer first; and pushing to a collector is an operator/deployment
  concern. The eval-score events (the standardized part of OTel GenAI evals)
  ARE exported. Revisit when a concrete consumer (Phoenix/Langfuse instance)
  exists.
- **P3.4 CI gating — DONE (framework-agnostic).** `evals/ci_gate.py`:
  `gate_regression(baseline, current, allowed_regression, require_significance)`
  on paired deltas with CI (advisory-first per DP-10; strict mode available)
  + `verdicts_by_item` adapter; 7 tests (test_eval_ci_gate.py). Usable from
  pytest or a CI script over the runs API. DeepEval was NOT added as a
  dependency (nothing needed it; revisit only if its metrics get adopted).

---

## Risk register (live)

| Risk | Mitigation / status |
|---|---|
| Token expiry on long user_session runs (P1.3) | Documented; service-account mode unaffected; item-level errors don't kill runs. Consider re-auth or chunked runs later. |
| Gold SQL authored against physical names drifts from MDL | v1 accepts native read-only SQL (validated read-only); logical-name validation deferred (needs MDL name resolver) — **gap noted**, Snowflake-rule enforcement is a P2 item. |
| Eval-vs-exemplar leakage inflates scores | I-6 detection flag now; exclusion in P2.4. |
| Empty-vs-empty false passes | `low_confidence` flag surfaced in UI. |
| Run cost blowups (500 items × trials) | v1: run confirm dialog shows item×trial count; MeteredModelClient records cost. Pre-run $ estimate = P2 polish. |
| SSE flood on big runs | Progress events every 5 items; row previews capped (I-5). |
| Concurrent runs per benchmark | Supersede-on-submit (I-3) + CAS claim. |

## Gap log (user expectation ↔ actual UI) — update after each UI item

- Final pass (2026-07-03, second build wave): **(a)** matrix sweep uses one
  comma-separated model input — arms beyond model×exclusion (e.g. prompt
  version per arm) need API calls; prompt-version-per-arm requires per-run
  prompt resolution (global resolver today) — recorded as the known limit of
  the matrix. **(b)** "Propose MDL fixes" shows staged items in the run modal;
  reviewing the full diff + applying happens in the Copilot panel (no deep
  link yet). **(c)** Prompt diff view compares draft vs live only (not
  arbitrary version pairs). **(d)** judge/scientist can now use a separate
  model via WREN_BENCHMARK_JUDGE_MODEL (self-preference mitigated when set;
  default remains the agent model). **(e)** benchmark-before-promote remains
  advisory (no hard gate linking prompt promotion to a green benchmark run) —
  deliberate: admins may need emergency prompt fixes.

- P2/P3 (post-build review, 2026-07-03): **(a)** Prompt editor is a plain
  textarea with no diff view between versions and no live "which agents use
  this prompt" hint — functional for admins, below Langfuse-grade UX; diff
  view is the highest-value polish. **(b)** The prompts page advises measuring
  with benchmarks before promoting, but there is no enforced link (run a
  benchmark → promote from the comparison view) — the candidate→promote flow
  is safe but unmeasured promotion is possible. **(c)** Scientist report is
  displayed transiently in the run modal; the persisted `scientist`
  conversation is not yet listed anywhere in the UI (retrievable via API).
  **(d)** `exclude_example_recall` has no UI toggle (default-on backend flag;
  exemplar-assisted mode reachable only via API) — deliberate for v1 to keep
  the run bar simple. **(e)** Judge/scientist model = the agent's default
  model; self-preference bias is possible when judging its own answers
  (PoLL votes mitigate; separate judge-model knob is the next step).
  **(f)** by_capability not recomputed on verdict override (see P2.3 note).

- P1.5 (CONFIRMED post-build review): **(a)** answer-spec editor: `expected_values` mode is a
  JSON textarea with schema hint + client-side validation — functional but not the
  "typed form fields" a non-technical curator might expect; revisit with per-field
  inputs in P2. **(b)** run progress uses `useProjectEvents` + 3s polling fallback;
  event delivery within ~1 poll interval, acceptable. **(c)** compare view requires
  picking two runs manually; no auto "vs previous" default yet (small P2 polish).
  **(d)** ordered-compare (`ordered=True`) is wired in the comparator but the run
  job does not yet auto-detect top-level ORDER BY in gold SQL (uses unordered
  default); tie-safe but can over-accept wrong orderings when the question demands
  a specific sort — noted for P2 (sqlglot detect). **(e)** verified_by/at set on
  create but no dedicated "verify" review flow (Snowflake-style) yet.
