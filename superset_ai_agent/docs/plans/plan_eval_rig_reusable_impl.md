# Reusable evaluation rig — implementation checklist

Status: IMPLEMENTED (offline) 2026-07-01 — Phases 0–8 built; 176 offline tests green,
ruff clean. **Two items are owner-executable and pending a live stack** (Docker was
down at build time; the user drives Docker): Phase 6.2 live Seagate parity smoke and
Phase 7.1/7.2 Oracle smoke. The *deterministic* parity (new grader vs legacy
`seagate_scoring` on identical rows, all 30 specs) passes offline — see
`evaluation/test_rig_parity.py`. Environment confirmed: rig runs in-repo (agent
package importable, `.env` present); Oracle is a live off-local DB via a Superset
connection (DP-9).

**As-built map** (all under `superset_ai_agent/evaluation/`):
`rig/{corpus,fixture,model_client,scoring,harness,preflight}.py`,
`run_rig.py` (run entrypoint), `prepare/{_agent_pass,prepare_bi_docs,prepare_targets,
prepare_corpus,run_prepare}.py` (generators + orchestrator), `RIG_RUNBOOK.md`
(on-box contract), `fixtures/seagate/` (parity fixture), tests
`test_rig_*.py` + `test_prepare.py` (176 total). `conftest.py` adds repo root to
`sys.path`. No existing file was modified except `conftest.py` (additive) — `run_eval_v4/v5`
and the Seagate research runners are untouched (R6).

Owner intent: turn the bespoke Seagate research rig (`superset_ai_agent/evaluation/`)
into **fixture-agnostic, reusable infrastructure** with a two-tier split: **this
(smart) codebase agent provides the prompts, scripts, and infra; the less-capable
Windows agent** dumps raw inputs, runs the provided scripts to produce fixture
artifacts, then runs experiments — handling only env- and data-specific details.
This plan does **not** build the in-app Benchmarks platform (separate agent's track).

All claims are source-backed against repo state `git 3eb9054343`. File:line refs are
to that tree — re-anchor before editing (other agents work this repo concurrently).

---

## 1. Problem (source-backed)

The rig is a *fixture*, not a *framework*, and it assumes precomputed ground truth —
both fatal for real Oracle data with human/agent-authored questions.

1. **Ground truth is hardcoded** in `evaluation/seagate_scoring.py:43` (`EXPECTED`)
   and `:87` (`CAPABILITY`), transcribed by hand from a pandas recompute in
   `superset/examples/seagate_multi/generate_data.py`. There is **no LLM judge** —
   grading is deterministic number/name/trap matching (`score_result`, `:160`). The
   comment at `eval_common.py:719` defers non-numeric answers to "a human/LLM judge"
   that does not exist in the rig.
2. **Questions live in hand-formatted markdown** parsed by four brittle regexes
   (`eval_common.py:507-510`, `parse_test_queries` at `:568`).
3. **Seagate/schema names are hardcoded in the runners** (`run_eval_v4.py:49`
   `SCHEMAS=[...]`, fixture dirs at `:311`, delta labels at `:151-164`).
4. **Data generation/loading is Postgres-only** (`generic_loader.py:89`
   `CREATE SCHEMA`, `pandas.to_sql`, PG type strings) — irrelevant to Oracle.
5. **There is no *prepare* stage.** Today a human writes the markdown + the `EXPECTED`
   dict by hand. The owner's workflow needs raw inputs → **agent-generated** BI docs,
   question corpus with ground truth, capability tags, and target schemas.

**Preserve (reinforce, don't rewrite):** `eval_common.AgentClient` (`:147`, JWT +
CSRF + 401-refresh + project lifecycle + `query()` at `:282`); `eval_v2/v3` client
verbs (onboard/enrich/copilot/golden); the DB-agnostic query path (agent executes via
Superset SQL Lab by `database_id`, `integrations/superset/rest.py:308`); the pure,
tested scoreboard aggregation (`run_eval_v4.build_scoreboard`, `:106`).

---

## 2. End-to-end workflow & two-tier responsibility split

The canonical dataflow (maps 1:1 to the owner's four steps):

```
 inputs/                         [STEP 1] owner dumps raw CSVs + context .md
   *.csv   (data samples / schema dumps / and/or loose question lists)
   *.md    (business context an LLM needs)
     │
     ▼  [STEP 2] dumber agent runs PROVIDED prepare scripts (agent-driven generators)
 prepare/prepare_bi_docs.py   → fixture/context/*.md        (2a onboarding/context-dump docs)
 prepare/prepare_corpus.py    → fixture/questions.csv       (2b questions + gold_sql|eval_note)
                                                            (2c capability tags per question)
 prepare/prepare_targets.py   → fixture/fixture.yaml        (2d target Oracle schemas + db ref)
     │
     ▼  [STEP 3] dumber agent runs the reusable harness
 evaluation/run_rig.py --fixture fixture/fixture.yaml
     │
     ▼  [STEP 4] results at current-rig granularity
 results/<fixture_id>/{scoreboard.json, trials.json, channel_audit.json?}
```

**Two-tier split (a hard requirement, R12):**

| Provided by THIS agent (in-repo, portable) | Handled by the Windows (dumber) agent |
|---|---|
| All prepare/run/score **scripts** + their **prompts** | Dropping files into `inputs/` |
| The fixture/CSV **schemas** + the **runbook** | Supplying the Oracle **URI**/connection + `.env` |
| The scoring/judge **infra** (reused, §4) | Running the scripts; **reviewing** generated artifacts |
| **Env pickup** via `AgentConfig.from_env()` | Env- and data-**specific** values only |

**Prepare scripts are agent-driven generators, not string munging.** Each is a "script
stub" per the owner's instruction: it (a) picks up the model client + config from the
agent (`create_model_client(AgentConfig.from_env())`, §4), (b) reads `inputs/`, (c)
runs an LLM pass with a **prompt this plan provides** (§9), (d) writes a fixture
artifact, and (e) supports multi-pass **generate → validate → refine** with a
`--review` draft mode so the dumber agent can eyeball/edit before committing (DP-12).

---

## 3. Requirements (definition of done)

- **R1 Fixture-agnostic core** — no hardcoded schemas/`EXPECTED`/`CAPABILITY`/fixture
  names in framework code. Seagate becomes one fixture consumer.
- **R2 CSV question corpus** — questions + typed answer + tags load from CSV; robust
  parse, per-row errors.
- **R3 Reuse in-app scoring primitives** (`evals/typed_spec`, `evals/comparator`,
  `evals/judge`) — single source of truth; the LLM judge comes for free (DP-1).
- **R4 DB-backend-agnostic at run time** — Oracle via config only; no dependency on
  `generate_data.py`/the Postgres loader (DP-8).
- **R5 Standalone LLM judgement** using the agent's own model config (`.env`) (DP-2).
- **R6 Zero regression** — `run_eval_v4/v5` + Seagate keep producing the same
  scoreboard; Seagate migrates only after a parity check.
- **R7 One-command run entrypoint + one config file** for the dumber agent.
- **R8 Stable, comparable output** at current-rig granularity + a metadata block
  (model, memory regime, backend, fixture id, git sha, ts).
- **R9 Fail-fast preflight** — auth, connection, schema, judge model, CSV wellness.
- **R10 Offline unit tests** for every pure function (mirroring `test_eval_v4.py`).
- **R11 Prepare-stage generators** — provided scripts turn `inputs/` into the fixture
  artifacts (2a BI docs, 2b corpus w/ ground truth, 2c tags, 2d target schemas), each
  agent-driven with a provided prompt (§9) and a `--review` draft mode.
- **R12 Two-tier split** — this agent ships prompts+scripts+infra; the Windows agent
  supplies only env/data specifics (§2). No prompt authoring left to the dumber agent.
- **R13 Ground truth is validated, not trusted** — a generated `gold_sql` is accepted
  only if it **executes on the target DB and returns rows** (via `AgentClient.query`,
  `eval_common.py:282`); grounded on the **real schema** via `introspect_schema`
  (`integrations/superset/client.py:136`). `eval_note` rubrics are checkable text.

---

## 4. Key architectural decision — reuse, don't reinvent (DP-1/DP-2)

The agent package already ships the exact three-way scoring dispatch the rig needs,
used by the in-app platform at `app.py:5021` (`_score_benchmark_result`). Mirror it.

| Answer type | Reuse | Signature (verified) |
|---|---|---|
| `expected_values` | `evals.typed_spec.score_expected_values` (`:135`) | `(spec, rows) -> TypedSpecOutcome`; spec `nums`/`names`/`trap`/`zero`/`tolerance` |
| `gold_sql` | `evals.comparator.compare_result_sets` (`:248`) | `(*, predicted_columns, predicted_rows, gold_columns, gold_rows, ordered, sig_digits, rel_tol, extra_columns_policy, casefold) -> ComparisonOutcome` |
| `eval_note` | `evals.judge.judge_eval_note` (`:92`) | `(model_client, *, question, note, sql, rows, summary, votes, model) -> JudgeOutcome` (pass/fail + critique + PoLL) |

Verified working standalone (import smoke, repo root on `PYTHONPATH`): all three plus
`create_model_client(AgentConfig.from_env())` (`llm/factory.py:28`, `config.py:516`)
import and execute; `score_expected_values({'nums':[6]},[{'x':6}]).verdict == 'pass'`.

**Consequence (accepted):** the core + prepare scripts cross the "pure HTTP client"
boundary — importing `superset_ai_agent.evals.*` + `llm.factory` — while still driving
the agent over HTTP via `AgentClient`. Acceptable because the rig runs **in-repo** on
the Windows box (confirmed). The **same model client** powers both the judge (grading)
and the prepare generators (authoring) — one `.env`, one key, no divergence.

---

## 5. Decision points

- **DP-1 Reuse in-app scorers?** → YES (§4). Blocks Phases 2–3.
- **DP-2 Judge/generator model client via `AgentConfig.from_env()`?** → YES (§4).
- **DP-3 Corpus authoring surface: CSV** producing the same internal record; keep
  `parse_test_queries` for legacy Seagate markdown (R6).
- **DP-4 Ground-truth vocabulary** = the in-app `AnswerType` (`evals/schemas.py:28`):
  `gold_sql` / `expected_values` / `eval_note`. The prepare generator targets
  **`gold_sql` and `eval_note`** for real data (owner's 2b); `expected_values` stays
  supported for anyone who *can* precompute.
- **DP-5 Location:** new `evaluation/rig/` (core) + `evaluation/prepare/` (generators)
  + `evaluation/run_rig.py` (entrypoint); leave `eval_common/v2/v3` + `run_eval_v4/v5`
  untouched until the parity gate.
- **DP-6 Fixture manifest** declares schemas, context docs, corpus, onboard mode, db —
  env-overridable via extended `EvalConfig`.
- **DP-7 Memory regime** — not API-verifiable; warn-and-record (`assert_eval_preconditions`,
  `eval_v2.py:263`); real-data runs usually want the learning loop ON (recorded fact).
- **DP-8 Data loading / Oracle** — out of scope for the core; real data pre-exists.
- **DP-9 Oracle connectivity (confirmed)** — the agent executes SQL through Superset's
  SQL Lab by `database_id` (`rest.py:308`), so the Oracle URI is a **Superset
  connection**; the rig references it by `database_name`/id and **never touches Oracle
  directly** (`resolve_database_id`, `eval_common.py:263`). The Oracle **driver lives
  in the Superset image** (enabled by commit `909ca2ca55`; preflight verifies). The
  **local docker stack** brokers the off-local connection. Manifest carries
  `database_name`; optionally a `connection_uri_env` that preflight registers via
  Superset `POST /api/v1/database/` if missing. Secrets come from `EVAL_*` env only.
- **DP-10 What are the input CSVs? (NEW)** — ambiguous by design: they may be **data
  samples/schema dumps** (LLM *generates* questions from them) and/or **loose question
  lists** (LLM *extracts/normalises* them). → **Support both**: `prepare_corpus.py`
  extracts explicit questions when present and generates new ones from data+context
  when asked (`--mode extract|generate|both`). Recommend `both` as default.
- **DP-11 gold_sql grounding + validation (NEW)** — a generated SQL is only "ground
  truth" if it is correct on the real DB. → `prepare_corpus.py` grounds generation on
  `introspect_schema` output (`client.py:136`) and **validates each candidate by
  executing it** (`AgentClient.query`); SQL that errors or returns nothing is dropped
  or flagged for review (R13). This mirrors the in-app `dry_run_benchmark_item`
  (`app.py:4967`).
- **DP-12 Review gate (NEW)** — LLM-generated ground truth must be reviewable before it
  scores a run. → Every prepare script has a `--review` mode emitting a human-readable
  draft (question + chosen answer_type + gold rows preview / rubric + tags) that the
  dumber agent (or owner) approves; committing writes the final artifact. Prevents a
  hallucinated rubric/SQL from silently defining "correct".

### 5.1 Sign-off (recorded)
DP-1..DP-12 accepted as recommended (owner sign-off 2026-07-01: in-repo confirmed;
Oracle off-local via URL brokered by local docker as a Superset connection; this agent
provides prompts+scripts+infra, Windows agent handles env/data). Later adjustment
re-opens the affected phase.

---

## 6. Entrypoints & touchpoints

**New — reusable core (`evaluation/rig/`):** `corpus.py` (CSV↔records, +`from_markdown`
shim over `parse_test_queries`), `fixture.py` (manifest load/validate), `scoring.py`
(3-way dispatch mirroring `app.py:5021`, normalized verdict compatible with
`build_scoreboard`'s `CORRECT_VERDICTS`), `model_client.py` (lazy
`create_model_client(AgentConfig.from_env())`, shared by judge + generators),
`harness.py` (generalises `run_eval_v4.run_trial`/`grade_sweep`, reuses
`build_scoreboard`), `preflight.py` (R9).

**New — prepare generators (`evaluation/prepare/`):** `prepare_bi_docs.py` (2a),
`prepare_corpus.py` (2b/2c, grounded+validated per DP-11), `prepare_targets.py` (2d,
via `introspect_schema`). Shared `_agent_pass()` helper: prompt (from §9) → model
client → parse → optional refine loop → `--review` draft. `run_prepare.py` orchestrates
all three (owner's step 2 in one call), each independently re-runnable.

**New — entrypoint:** `evaluation/run_rig.py` (`--fixture/--trials/--validate/--questions`;
repo-root `sys.path` insert).

**Reused unchanged:** `AgentClient`/`EvalConfig`, `eval_v2/v3` verbs,
`build_scoreboard`/`format_scoreboard`, `evals.typed_spec/comparator/judge`,
`llm.factory`, `introspect_schema`.

**Touched (small, additive):** `EvalConfig` (`:79`) gains `fixture_path`, `schemas`,
`judge_votes/model/enabled`, `inputs_dir` with `EVAL_*` reads in `from_env` (`:106`);
defaults preserve current behaviour. `run_eval_v4/v5` refactored to consume the core
**only after the Phase 6 parity gate**.

---

## 7. Risks & mitigations

- **Agent-internal API drift** (importing `evals.*`/`llm.*`). → Pin to the 4 stable
  functions; `test_rig_imports.py` smoke; low churn (they're the shipped platform's own).
- **Packaging / `PYTHONPATH`** — scripts run as top-level modules from `cd evaluation`,
  so `import superset_ai_agent.*` fails there (verified `ModuleNotFoundError`). →
  entrypoints insert repo root on `sys.path`, or `python -m`. Phase 4 blocker; trivial.
- **LLM-generated ground truth is wrong** (hallucinated SQL/rubric/schema) — the
  central new risk. → DP-11 validation-by-execution + `introspect_schema` grounding +
  DP-12 review gate; `gold_sql` that won't run never becomes ground truth; a run
  records how many items were auto-accepted vs human-approved.
- **Oracle result-shape mismatches** (UPPERCASE cols, `NUMBER` vs float, NLS). →
  `score_expected_values` scans all cells w/ tolerance; `compare_result_sets` exposes
  `casefold`/`sig_digits`/`rel_tol` — surface in `answer_spec`. Oracle smoke (Phase 7).
- **Judge nondeterminism/cost.** → PoLL votes (`judge_votes ≥ 3` for real data);
  `_JUDGE_ROW_CAP=20`; only `eval_note` items reach the judge.
- **Breaking Seagate v4/v5 (R6).** → Additive build; parity gate (Phase 6) diffs the
  scoreboard against a saved baseline before touching `run_eval_v4/v5`.
- **Memory regime unverifiable.** → warn-and-record; document the `.env`+recreate
  toggle for ablations.
- **Dumber agent misformats inputs.** → strict schemas + `--validate` dry-run
  (preflight + corpus parse, no agent calls) reporting row-level errors.

---

## 8. Sequential checklist (resumable; blockers/deps noted)

Mark `[COMPLETE]` with evidence (file:line / test output / live probe). Re-anchor
before resuming.

### Phase 0 — Decisions & spec sign-off  ✅ COMPLETE (2026-07-01)
- [x] 0.1 DP-1..DP-12 accepted (§5.1).
- [ ] 0.2 Ratify exact CSV + manifest column names (draft §9) during Phase 1.1.
- [x] 0.3 In-repo confirmed; Oracle off-local via Superset connection (DP-9).

### Phase 1 — Corpus + fixture loaders  ✅ COMPLETE (rig/corpus.py, rig/fixture.py; 15 tests)
- [ ] 1.1 `rig/corpus.py`: CSV↔records, strict headers, per-row errors, `from_markdown`
      shim over `parse_test_queries` (`:568`) for legacy Seagate (R6).
- [ ] 1.2 `rig/fixture.py`: manifest load + validate (DP-6/DP-9).
- [ ] 1.3 Offline tests (`test_rig_corpus.py`, `test_rig_fixture.py`). ✅ `pytest` green.

### Phase 2 — Scoring dispatch + shared model client  ✅ COMPLETE (rig/scoring.py, rig/model_client.py; 14 tests incl. imports smoke)
- [ ] 2.1 `rig/model_client.py`: lazy `create_model_client(AgentConfig.from_env())`.
- [ ] 2.2 `rig/scoring.py`: 3-way dispatch mirroring `app.py:5021`; normalized verdict.
- [ ] 2.3 `test_rig_scoring.py` (mocked model client for the judge path) + 2.4
      `test_rig_imports.py`. ✅ green.

### Phase 3 — Fixture-agnostic harness  ✅ COMPLETE (rig/harness.py; 8 tests. Gap: wren-mode onboard/enrich orchestration only covered by the live smoke)
- [ ] 3.1 `rig/harness.py`: generalise `run_trial`/`grade_sweep`; run gold SQL for
      `gold_sql` items; reuse `build_scoreboard` (`:106`).
- [ ] 3.2 Grounding modes fixture-declared (onboard/enrich reuse `eval_v2/v3`).
- [ ] 3.3 Meta block (R8).

### Phase 4 — Run entrypoint + config + preflight  ✅ COMPLETE (rig/preflight.py, run_rig.py; 9 tests. EvalConfig NOT extended — configured via the entrypoint from the fixture instead, fewer touchpoints)
- [ ] 4.1 Extend `EvalConfig` (`:79`) — non-breaking.
- [ ] 4.2 `rig/preflight.py` (R9): auth; **Oracle connection registered + reachable**
      (resolve name→id; register `connection_uri_env` via Superset
      `POST /api/v1/database/` if missing; test-connection; Oracle-driver hint on
      failure — DP-9); schema exists; judge reachable; CSV wellness. Drop the
      Postgres-required assert (R4).
- [ ] 4.3 `evaluation/run_rig.py` (`--fixture/--trials/--validate/--questions`; sys.path
      insert). `--validate` = preflight + parse, no agent calls.
- [ ] 4.4 `test_rig_preflight.py`; `--validate` passes live on the Seagate fixture. ✅.

### Phase 5 — Prepare-stage generator scripts (steps 2a–2d)  ✅ COMPLETE (prepare/*.py + run_prepare.py; 14 tests; prompts §9 embedded)
- [ ] 5.1 `prepare/_agent_pass.py`: shared prompt→model-client→parse→refine helper +
      `--review` draft mode (DP-12).
- [ ] 5.2 `prepare/prepare_bi_docs.py` (2a): `inputs/*.md|*.csv` → `fixture/context/*.md`
      onboarding/context-dump docs. Prompt §9.1.
- [ ] 5.3 `prepare/prepare_targets.py` (2d): infer target Oracle schemas from inputs +
      `introspect_schema` (`client.py:136`) → `fixture/fixture.yaml`. Prompt §9.3.
- [ ] 5.4 `prepare/prepare_corpus.py` (2b/2c): questions + `gold_sql|eval_note` + tags →
      `fixture/questions.csv`. `--mode extract|generate|both` (DP-10). **Ground on
      `introspect_schema`; validate each gold_sql by executing via `AgentClient.query`
      (R13/DP-11); drop/flag failures.** Prompt §9.2.
- [ ] 5.5 `prepare/run_prepare.py`: orchestrate 5.2–5.4 (owner's step 2 in one call).
- [ ] 5.6 Offline tests with a mocked model client (each script's parse/validate/refine).
      ✅ `pytest` green. Live sanity: run on the Seagate CSV-ified inputs, eyeball drafts.

### Phase 6 — Seagate migration + parity gate  ◑ OFFLINE DONE / LIVE PENDING (fixtures/seagate/ + test_rig_parity.py 5 tests prove grader parity on all 30 specs; 6.2 live scoreboard smoke pending stack)
- [ ] 6.1 Seagate `fixture.yaml` + question CSV (from `test_queries.md` + `EXPECTED`).
- [ ] 6.2 `run_rig.py` on Seagate (5-q smoke, memory OFF) — **diff scoreboard vs a saved
      `run_eval_v4` baseline; must match.**
- [ ] 6.3 Only then: optionally thin `run_eval_v4/v5` onto the core (decision recorded).

### Phase 7 — Oracle smoke + runbook  ◑ RUNBOOK DONE / SMOKE PENDING (RIG_RUNBOOK.md written; 7.1/7.2 Oracle smoke need the live Oracle stack — owner-executable)
- [ ] 7.1 Owner drops a tiny real `inputs/` set; run `run_prepare.py` → review → `run_rig.py`.
- [ ] 7.2 Confirm gold_sql validation + judge both work on Oracle shapes; tune
      `casefold`/`sig_digits` if needed.
- [ ] 7.3 `evaluation/RIG_RUNBOOK.md`: the linear, script-only steps + CSV/manifest
      schemas + the two commands (`run_prepare`, `run_rig`). The dumber agent's contract.

### Phase 8 — Close-out  ✅ COMPLETE (176 offline tests, ruff clean; docs index + memory updated)
- [ ] 8.1 Full offline tests + `ruff` green (`venv/bin/python -m pytest evaluation/ -q`).
- [ ] 8.2 `docs/README.md` line already added; set `Status:` → IMPLEMENTED with evidence.
- [ ] 8.3 Update memory (new `eval-rig-reusable` entry).

---

## 9. Appendix — schemas + the provided prompts (I ship these; DP-12 review applies)

**Question CSV** (fill exactly one answer column; `expected_values` optional):
```
id, question, answer_type, capability_tags, gold_sql, eval_note, expected_values, tolerance, notes
Q1,"Total on-time deliveries for CRITICAL parts in Q4?",gold_sql,"metric;temporal","SELECT ...",,,,"validated:runs=1 rows=1"
Q2,"Is supply health improving this quarter?",eval_note,"metric;temporal",,"Passes iff it reports on-time rate for CRITICAL parts only and states the QoQ trend.",,,
```
**Fixture manifest** (`fixture.yaml`): `id`, `database_name` (or `connection_uri_env`),
`schemas: [...]`, `onboard_mode: manual|auto|none`, `context_docs: [context/*.md]`,
`corpus: questions.csv`, `grounding_modes: [...]`. Secrets via env only (DP-9).

**§9.1 BI-doc generation prompt (prepare_bi_docs):** *"You are writing an internal BI
glossary/onboarding doc for a text-to-SQL agent from the attached business context and
data samples. Produce Markdown that maps business slang→columns, defines custom metrics
with exact formulas, names join paths across schemas, and states calendar/rollup rules.
Only assert what the inputs support; never invent columns. Output the doc only."*

**§9.2 Corpus generation prompt (prepare_corpus):** *"Given the business context, the
introspected schema (tables/columns below), and any questions in the inputs, produce a
JSON array of test items. For each: `question`; `answer_type` = `gold_sql` when a
deterministic answer exists (include runnable `gold_sql` using ONLY the introspected
schema) or `eval_note` when correctness is judgmental (include a one–three sentence
rubric of what a correct answer must satisfy); 1–3 `capability_tags` from {slang, join,
xschema, bridge, metric, trap, negative, temporal, multihop, distractor}. Prefer
gold_sql. Never reference columns not in the schema."* — each gold_sql is then executed
to validate (R13); failures are dropped or `--review`-flagged.

**§9.3 Target-schema prompt (prepare_targets):** *"From the business context and the
list of available Oracle schemas/tables below, select the minimal set of schemas the
questions require and briefly justify each. Output JSON: {schemas: [...], rationale:
{...}}."*

**Script-stub contract (all prepare scripts):** pick up `create_model_client(
AgentConfig.from_env())`; read `inputs/` (+ live `introspect_schema` where needed);
write to `fixture/`; idempotent; `--review` emits a draft for approval before commit;
`--refine N` allows N generate→validate passes. The Windows agent supplies only env +
inputs and approves drafts — it authors no prompts (R12).
