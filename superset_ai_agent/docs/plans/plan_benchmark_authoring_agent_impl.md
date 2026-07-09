<!--
Implementation checklist for the in-app Benchmark Authoring Agent + results
surfacing ("Track B"). Sibling to plan_eval_rig_reusable_impl.md ("Track A", the
headless script rig). Track B drives the SHIPPED in-app Benchmarks platform
(superset_ai_agent/evals/ + app.py routes + BenchmarksPanel UI) via a NEW agent
that turns raw CSV + context into reviewable benchmark items, then surfaces results
in native Superset charts.

HOW TO USE THIS FILE (future sessions): work top-to-bottom. Each item has
Status / Requirements / Touchpoints / Tests / Risks / Blockers+Deps. Flip Status to
DONE with a one-line as-built note (+ test count) when completed; add BLOCKED notes
inline. Do not reorder items — later items assume earlier schemas/services exist.
Re-anchor first (git status + re-verify file:line refs); other agents work this repo
concurrently. All file:line refs are against repo state git 3eb9054343.
-->

# Benchmark Authoring Agent + Results Surfacing — Implementation Plan & Checklist

**Status:** DRAFT — awaiting owner sign-off on DP-B0..DP-B7 (§3). No code written.

**Owner intent (captured verbatim from the conversation that produced this plan):**
The owner wants **agentic features at every step**. A user dumps CSV(s) + context
`.md` onto the frontend; an **authoring agent** reads them and (2a) generates/extracts
SQL questions with either gold-SQL ground truth or free-text (judge) validation,
(2b) tags each question by capability tested, (2c) captures target Oracle schemas —
**with no BI injected into the agent-under-test at run time (no "cheating")**. The
user reviews the drafted questions, then **runs the benchmark**; agent outputs become
validated results; results are **stored at the research-rig's granularity or finer**
and **presented in existing Superset UI/visualisations, charted by question type and
other tags**. Multiple passes are expected, so **secondary agent workflows** may run
automatically or be called by the authoring agent, and **every part must be surfaced
to the UI and observable.**

**Why this plan exists:** ~70% of the workflow is already shipped as the in-app
"Project Benchmarks" platform (F11). The genuinely new work is (a) the **authoring
agent** that fills the platform from messy CSV, and (b) **native-Superset results
surfacing**. This plan builds only those, reusing the shipped run engine, scorers,
judge, store, and UI shell.

**Sources (industry grounding, inherited from the parent testing platform):** Genie
Benchmarks (three-way verdict, async runs, ≤500 items), Wren AI Cloud Evaluation /
AI Advisor (pass/fail + named reasons, manual override, regression-before-apply),
BIRD mini_dev (soft-F1, tie sorting), Snowflake VQR (`verified_by`/`verified_at`),
tau-bench pass^k, OTel `gen_ai.evaluation.result` score-row shape. New authoring-agent
grounding: the shipped **MDL Copilot** loop (document → reviewable `Changeset`,
`copilot/loop.py:203`) is the in-repo pattern this agent mirrors.

---

## 1. Relationship to the other track (READ FIRST — avoids duplicate/contradictory work)

Two plans now target the owner's CSV→benchmark workflow. They **share a core** and
**diverge on substrate**. Do not merge them; do not let one silently re-implement the
other's half.

| | **Track A** — `plan_eval_rig_reusable_impl.md` (SIGNED OFF, READY TO BUILD) | **Track B** — this plan |
|---|---|---|
| Run substrate | New headless script harness (`evaluation/rig/` + `run_rig.py`), generalises `run_eval_v4.run_trial` | **Shipped in-app run engine** (`start_benchmark_run` → `_run_benchmark_job`, `app.py:5118`) |
| Authoring surface | `prepare/*.py` scripts the dumber Windows agent runs | **Interactive authoring agent** streamed to the MDL Lab UI |
| Results store | `scoreboard.json` (rig granularity) | `ai_agent_eval_*` tables (per-item/per-trial — finer) |
| Results surface | Terminal / JSON | **Native Superset charts** + BenchmarksPanel |
| Primary user | Less-capable Windows agent running provided scripts | Human + authoring agent in the browser |

**Shared, already-decided (inherit — do NOT re-litigate):** reuse
`evals.typed_spec.score_expected_values`, `evals.comparator.compare_result_sets`,
`evals.judge.judge_eval_note`, and `create_model_client(AgentConfig.from_env())`
(Track A §4, DP-1/DP-2). Oracle is a **Superset connection**; the agent executes SQL
by `database_id` and never touches Oracle directly (Track A DP-9). Input CSVs are
ambiguous by design (data samples *or* question lists) → support extract **and**
generate (Track A DP-10). Gold-SQL is only ground truth if it **executes correctly on
the real DB** (Track A DP-11 → in-app `dry_run_benchmark_item`, `app.py:4967`).
LLM-authored ground truth needs a **review gate** before it scores (Track A DP-12 →
in-app `verified_by`/`verified_at`, `evals/schemas.py:73`).

**DP-B0 (decision, §3):** does Track B **complement** Track A (recommended: A = headless/CI
path, B = product surface, both feeding the same benchmark items + judge) or
**supersede** it? Recommendation: **complement.** They are one core with two front
ends; killing A loses the scriptable/CI path.

---

## 2. Requirements (definition of done)

- **R1 Authoring agent** — given uploaded CSV(s) + context `.md`, produces a
  **reviewable draft**: (a) a synthesized BI/context doc, (b) benchmark items with
  `question`, `answer_type ∈ {gold_sql, eval_note, expected_values}`, `answer_spec`,
  `capability_tags`, and suggested target schema(s). Never auto-commits.
- **R2 Grounded + validated gold-SQL** — every `gold_sql` candidate is executed
  against the real (Oracle) DB via the agent before it can be marked verified; SQL
  that errors or returns nothing is flagged, never silently accepted (Track A DP-11).
- **R3 Capability tagging** — each item carries `capability_tags` from a **declared,
  fixture-agnostic vocabulary** (not the Seagate-specific set hardcoded in
  `seagate_scoring.CAPABILITY`).
- **R4 Human review gate** — a drafted benchmark cannot be run until items are
  reviewed; approval stamps `verified_by`/`verified_at`. Draft state is visible and
  editable in the UI.
- **R5 Anti-cheat run mode** — the agent-under-test receives **no BI/context dump**
  and **no recall of an item's own golden example** at run time (already the platform
  default; this plan only *confirms and surfaces* it, R5.1). The run records **which
  semantic layer** (bare/onboarded vs enriched) was under test (DP-B4).
- **R6 Results at rig granularity or finer** — per-item, per-trial verdicts + scores +
  capability rollup (already satisfied by `EvalResult`/`EvalScore`/`RunTotals`).
- **R7 Native Superset surfacing** — a queryable dataset over the eval tables plus at
  least one dashboard charting pass-rate by capability tag, by run, and run-vs-run.
- **R8 Observability** — every authoring pass and run emits progress that the UI shows
  live; nothing runs invisibly. Reuse the existing SSE (conversation) or
  background-job+event (benchmark) patterns.
- **R9 Fixture-agnostic** — no Seagate/schema names hardcoded in any new code; all
  targets come from the project + uploaded inputs.
- **R10 Standalone judge/author model** — both the authoring agent and the judge use
  the agent's own `.env` model config; judge may use a different model
  (`wren_benchmark_judge_model`) for self-preference mitigation.

---

## 3. Decision points (recommendations to confirm at sign-off)

- **DP-B0 Track relationship** → **complement** Track A (§1). Blocks nothing; framing only.
- **DP-B1 Authoring surface** → a **new "Authoring" tab** in the MDL Lab
  `SemanticLayerEditor` beside `benchmarks` (`SemanticLayerEditor/index.tsx:1824`),
  reusing the Copilot loop infra — **not** a second Copilot chat. Rationale: the
  authoring output is a *structured draft to review*, exactly the `Changeset`-review
  shape the Copilot rail already uses.
- **DP-B2 Authoring agent transport** → **SSE**, mirroring
  `ConversationGraph.run_stream` + `stream_conversation_message` (`app.py:1267`).
  Rationale: authoring is a foreground, human-in-the-loop step; the owner wants it
  observable step-by-step. (The *run* stays background-job+poll — it already is.)
- **DP-B3 CSV contract** → **semi-structured**, not free-form. A declared optional
  `type` column (`context` | `question`) and, for questions, one of
  `gold_sql` | `expected_values` | `eval_note` plus optional `capability_tags`,
  `target_schema`. The agent still *interprets within* rows (synthesize doc, author/
  validate SQL, write rubrics) but is not asked to guess document structure.
  Rationale: removes the highest-variance decision (segmentation, factor A1 in the
  risk review). Reuse Track A's `--mode extract|generate|both` semantics (DP-10).
- **DP-B4 Layer under test + anti-cheat** → build the semantic layer from the BI for
  *grounding* (onboard + optional enrich), but **never inject BI at run time**; record
  the layer mode (`wren_base` vs `wren_bi`) in `EvalRun.config`. Rationale: "no BI
  injection" means no runtime prompt dump (already enforced), NOT "no semantic layer" —
  the layer is the thing under test. v4 showed enrichment barely helps, so the mode
  must be an explicit, recorded knob.
- **DP-B5 Per-item target schema** → **v1 = project-level scoping** (the run already
  uses `project.schema_name`); capture the agent's suggested target schema(s) as item
  **metadata/tags only**, do not change run scoping yet. Rationale: minimal schema
  change; revisit if multi-schema-per-benchmark demand appears.
- **DP-B6 Results surfacing** → **build a reporting SQL view + Superset dataset** over
  the `ai_agent_eval_*` tables (the owner explicitly asked for "existing Superset UI
  and visualisations"), AND keep the in-app BenchmarksPanel scoreboard. Rationale: the
  agent DB is Postgres in the postgres-only deploy, so it is registerable as a Superset
  connection; a flattened view makes tag/run charting trivial.
- **DP-B7 Bulk item creation** → add a **bulk/import REST route** mirroring
  `import_golden_queries_as_items` (`app.py:6043`), not a client-side loop. Rationale:
  the agent emits a batch; one atomic-ish call with dedup beats N round-trips and
  matches the existing golden-import precedent.

*Record sign-off here when obtained; a later change re-opens the affected phase.*

---

## 4. Entrypoints & touchpoints (source-backed)

**Reused UNCHANGED (do not modify):**
- Run engine: `start_benchmark_run` (`app.py:5355`) → background `_run_benchmark_job`
  (`app.py:5118`) → scoring dispatch `_score_benchmark_result` (`app.py:5021`).
- Scorers/judge: `evals/typed_spec.py:135`, `evals/comparator.py:248`,
  `evals/judge.py:92`. Model client: `llm/factory.create_model_client` (via
  `AgentConfig.from_env`).
- Store: `evals/store.py` (`EvalStore` Protocol `:96`; `SqlAlchemyEvalStore` `:461`).
  Key verbs: `create_benchmark`, `add_item(BenchmarkItem)`, `create_run`,
  `report_run_progress`, `add_result`, `override_result`,
  `compute_benchmark_checksum` (`:79`).
- Schemas + tables: `evals/schemas.py`; `ai_agent_eval_{benchmarks,items,runs,results,scores}`
  (migration `persistence/migrations/versions/0019_eval_benchmarks.py`).
- Dry-run validation: `dry_run_benchmark_item` (`app.py:4967`). Post-run analysis:
  `evals/scientist.py:analyze_run` + `analyze_benchmark_run` (`app.py:5900`) +
  `handoff_benchmark_failures_to_copilot` (`app.py:5773`).
- Document ingest: `POST .../documents` (`app.py:6504`), `.../documents/text`
  (`app.py:6582`), `.../documents/{id}/enrich` (`app.py:6631`). CSV is an allowed
  upload type (`config.py:198-208`, `text/csv`) and auto-converts to a Markdown table
  (`semantic_layer/extractors.py:_extract_csv`).
- Copilot agent pattern to mirror: `run_copilot` (`copilot/service.py:114`) →
  `run_copilot_loop` (`copilot/loop.py:203`); progress hook `StepSink` (`loop.py:45`);
  returns a reviewable `Changeset` (never persists).
- SSE pattern to mirror: `ConversationGraph.run_stream` (`conversation_graph.py:643`)
  emitting `{"type":"progress",...}`/`{"type":"complete",...}`; route
  `stream_conversation_message` (`app.py:1267`); serializer `_conversation_sse`
  (`app.py:460`); frontend consumer `consumeConversationStream` (`api.ts:1057`).

**NEW — backend (`superset_ai_agent/evals/authoring/` new subpackage):**
- `corpus_csv.py` — pure CSV↔draft-record parse/validate (DP-B3 contract), shares the
  `AnswerType` vocabulary (`evals/schemas.py:28`).
- `author_agent.py` — the authoring agent: prompt(s) → `create_model_client` →
  segment/synthesize/author/tag → per-candidate gold-SQL validation → reviewable draft.
  Structured output via tool-call specs (Copilot pattern), not `response_format`.
- `capability_vocab.py` — declared fixture-agnostic tag set + descriptions (R3/R9).
- `otel`/reporting glue only if needed.

**NEW — backend (`app.py`, additive routes near the benchmark block ~`:4682`–`6100`):**
- `POST .../benchmarks/{id}/author/stream` — run the authoring agent over uploaded
  doc(s), SSE-stream progress, return a draft (DP-B2).
- `POST .../benchmarks/{id}/items/import` — bulk create reviewed items (DP-B7),
  mirroring `import_golden_queries_as_items` (`:6043`), dedup by casefolded question.
- (If DP-B4 needs it) accept a `layer_mode` in `BenchmarkRunRequest` and record it in
  `EvalRun.config`.

**NEW — config (`config.py` near `:313`):** `wren_benchmark_authoring_enabled: bool`,
optional `wren_benchmark_author_model: str | None`, `wren_benchmark_author_max_steps`.

**NEW — frontend (`superset-frontend/src/SqlLab/components/AiAgentPanel/`):**
- `SemanticLayerEditor/AuthoringPanel.tsx` — upload + streamed authoring + draft review
  grid; new tab in `SemanticLayerEditor/index.tsx` beside `benchmarks` (`:1824`).
- `api.ts` (~`:2614`+) — `authorBenchmarkItems` (SSE, reuse `consumeConversationStream`
  shape), `importBenchmarkItems`; types beside the benchmark block.
- Reuse `buildAnswerSpec` (`api.ts:240`) and the add-item modal shape
  (`BenchmarksPanel.tsx:261`,`:801`) for per-row review/edit.

**NEW — results surfacing (DP-B6):**
- A reporting SQL **view** over `ai_agent_eval_results`⋈`ai_agent_eval_scores`⋈
  `ai_agent_eval_items` (flatten verdict, capability_tag, run, config). Ship as a
  migration or a documented `CREATE VIEW` the operator runs.
- A Superset **dataset** on that view + a **dashboard** (pass-rate by tag, by run,
  run-vs-run). Provide as an importable dashboard bundle.

---

## 5. Sequential checklist

> Convention: each item = **[ ] Pn.k Title** with Status / Requirements / Touchpoints /
> Tests / Risks / Blockers+Deps. Flip to **[x] … DONE — <as-built + test count>**.

### Phase 0 — Sign-off & scaffolding

- [ ] **P0.1 Confirm DP-B0..DP-B7** (§3). **Blocker for everything.** Record sign-off
  inline. Deps: none.
- [ ] **P0.2 Add config flags** `wren_benchmark_authoring_enabled` (+ author model/steps)
  to `config.py:313` with `_env_bool`/`os.getenv` reads (~`:962`). Default authoring
  **off** until P3 lands. Tests: config from_env unit. Risks: none. Deps: P0.1.
- [ ] **P0.3 Create `evals/authoring/` package** (empty modules + `__init__`) with ASF
  headers; ensure no FastAPI imports in pure modules (offline-testable, mirrors
  `evals/` purity rule). Deps: P0.1.

### Phase 1 — Corpus contract + capability vocab (pure, offline-testable)

- [ ] **P1.1 Declare the capability vocabulary** (`capability_vocab.py`) — a
  fixture-agnostic tag set + one-line definitions (e.g. `join`, `cross_schema`,
  `metric`, `temporal`, `filter_value`, `trap`, `negative`, `aggregation`). R3/R9.
  Tests: vocab is non-empty, stable slugs. Risks: bikeshedding — keep small, extensible.
  Deps: P0.3.
- [ ] **P1.2 CSV↔draft-record parser** (`corpus_csv.py`) — parse the DP-B3 semi-structured
  CSV into `DraftItem` records (question, answer_type, raw answer payload, tags,
  target_schema, source_row); strict validation with row-level error messages; a
  `--validate`-style dry parse (no agent calls). Reuse `AnswerType` (`schemas.py:28`).
  Tests: happy path + ragged/mixed/missing-column rows → precise errors. Risks: messy
  human CSVs — mitigated by declared columns (DP-B3) + clear errors. Deps: P1.1.

### Phase 2 — Authoring agent (backend, the core new build)

- [ ] **P2.1 Author-agent prompt + loop** (`author_agent.py`) — mirror `run_copilot_loop`
  (`copilot/loop.py:203`): bounded tool-calling loop over `model_client.chat(tools=…)`,
  returns a reviewable draft (no persistence). Tools: read uploaded doc text, propose
  items, self-correct. **Emits `StepSink`-style progress** for SSE (P6). R1/R10.
  Tests: offline with a fake model client (assert draft shape, step emissions). Risks:
  **hallucinated ground truth** (factor A5) — mitigated by P2.3 validation + P4 review
  gate. Blockers: P1.2. Deps: P1.1, P1.2.
- [ ] **P2.2 Extract vs generate modes** — support `extract` (questions present in CSV),
  `generate` (author from data/context), `both` (Track A DP-10). Ground generation on
  live schema (`introspect_schema`, `integrations/superset/client.py:136`). Tests:
  each mode with a fake client. Risks: generation drift — bounded by schema grounding
  + review. Deps: P2.1.
- [ ] **P2.3 Gold-SQL validation loop (R2)** — for each `gold_sql` candidate, execute it
  against the real DB (agent `query`/`execute_sql`, or reuse `dry_run_benchmark_item`
  logic `app.py:4967`) and mark verified only if it runs and returns rows; else flag
  `needs_review`. **This is the single most important correctness gate.** Tests: valid
  SQL → verified; erroring/empty SQL → flagged. Risks: Oracle result-shape quirks
  (UPPERCASE cols, `NUMBER`) — surface tolerance knobs in `answer_spec`
  (`comparator` `casefold`/`sig_digits`/`rel_tol`). Deps: P2.1; **Blocker: P7 Oracle
  connection reachable for live validation.**
- [ ] **P2.4 BI/context doc synthesis** — turn context blocks into a clean doc and upload
  via `.../documents/text` (`app.py:6582`); optionally enrich the layer
  (`.../documents/{id}/enrich` `:6631`) per DP-B4. **Guard against paraphrase drift**
  (fork rule): prefer quoting/structuring source over free regeneration; mark
  synthesized facts for review. Tests: doc upload + enrich smoke (mocked). Risks:
  paraphrase drift (factor A2) — review gate + provenance. Deps: P2.1.
- [ ] **P2.5 Capability tagging** — agent assigns `capability_tags` from P1.1 vocab to
  each item. Tests: tags ⊆ vocab. Risks: low. Deps: P1.1, P2.1.

### Phase 3 — Authoring API + persistence + review UI

- [ ] **P3.1 SSE authoring route** `POST .../benchmarks/{id}/author/stream` (DP-B2) —
  wrap P2 in `StreamingResponse(media_type="text/event-stream")`, serialize with
  `_conversation_sse` (`app.py:460`); yields progress then a final draft. Gated on
  `wren_benchmark_authoring_enabled`. Tests: route emits progress + terminal draft
  (TestClient). Risks: long runs / disconnect — copy `run_stream`'s `GeneratorExit`
  handling (`conversation_graph.py:691`). Deps: P2.*.
- [ ] **P3.2 Bulk import route** `POST .../benchmarks/{id}/items/import` (DP-B7) —
  construct `BenchmarkItem(...)` per reviewed draft and `add_item` (`store.py:120`);
  dedup by casefolded question (mirror `import_golden_queries_as_items` `app.py:6043`);
  return created/skipped counts (`GoldenImportResponse` shape). Tests: create + dedup.
  Risks: partial failure — wrap per-item, report row errors. Deps: P1.2.
- [ ] **P3.3 Authoring frontend tab** `AuthoringPanel.tsx` + tab wire in
  `SemanticLayerEditor/index.tsx:1824` (DP-B1). Upload CSV/`.md`, stream authoring
  (reuse `consumeConversationStream` `api.ts:1057`), render an **editable draft grid**
  (reuse `buildAnswerSpec` `api.ts:240` + add-item modal shape
  `BenchmarksPanel.tsx:261`), then **Import reviewed** → P3.2. Tests: RTL — upload →
  draft rows → import call. Risks: UI scope creep — v1 = table + edit + import only.
  Deps: P3.1, P3.2.
- [ ] **P3.4 Review/verify gate (R4)** — reviewed items imported with `verified=true`
  (stamps `verified_by`/`verified_at`); UI shows unverified vs verified; a run may warn
  if unverified items are included. Tests: verified flag round-trips. Risks: none.
  Deps: P3.2, P3.3.

### Phase 4 — Run wiring (mostly confirmation)

- [ ] **P4.1 Confirm anti-cheat run mode (R5/R5.1)** — verify `_run_benchmark_job`
  (`app.py:5118`) still calls the agent with **no `extra_context`** and
  `exclude_example_recall=True`; surface the `golden_example_recalled` score in the UI
  results view so leakage is visible. Tests: assert no BI context in the run call;
  leakage score renders. Risks: silent regression if the run path changes — pin with a
  test. Deps: none (runs today).
- [ ] **P4.2 Record layer-under-test (DP-B4)** — thread a `layer_mode`
  (`wren_base`|`wren_bi`) into `BenchmarkRunRequest`/`EvalRun.config`; default per
  owner choice. Tests: run config persists mode. Risks: minor schema add. Deps: P0.1.

### Phase 5 — Native Superset results surfacing (DP-B6, R7)

- [ ] **P5.1 Reporting view** — `CREATE VIEW` flattening
  `ai_agent_eval_results ⋈ ai_agent_eval_scores ⋈ ai_agent_eval_items` (columns:
  run_id, benchmark_id, item_id, question, capability_tag (unnested), verdict,
  verdict_source, trial_index, score name/value, run config). Ship as a migration or a
  documented DDL. Tests: view returns rows against seeded eval data. Risks: agent-DB
  dialect (SQLite dev vs Postgres prod) — target Postgres (postgres-only deploy);
  document the SQLite caveat. Deps: existing eval tables (0019).
- [ ] **P5.2 Register agent DB as a Superset connection + dataset** — in the
  postgres-only deploy the agent DB is reachable; add it as a Superset database and a
  dataset on the P5.1 view. Provide as an importable connection/dataset bundle (no
  secrets committed). Tests: manual — dataset previews. Risks: **read-only creds only**;
  never expose write. Deps: P5.1.
- [ ] **P5.3 Dashboard bundle** — charts: pass-rate by `capability_tag`, by run, and a
  run-vs-run comparison (reuse the platform's paired-delta semantics, not bare deltas).
  Ship as an importable dashboard ZIP. Tests: manual render. Risks: none. Deps: P5.2.

### Phase 6 — Observability (R8)

- [ ] **P6.1 Authoring progress surfaced** — the P3.1 SSE stream drives a live step list
  in `AuthoringPanel` (segment → synthesize → author → validate → tag). Reuse the
  progress-event shape (`conversation_graph.py:2234`). Tests: RTL sees streamed steps.
  Deps: P3.1, P3.3.
- [ ] **P6.2 Run progress already surfaced** — confirm BenchmarksPanel polling
  (`RUN_POLL_MS`, `BenchmarksPanel.tsx:205`) + durable `benchmark_run_progress` events
  (`app.py:5262`) cover the run. Optional: add authoring events to the same project
  event stream. Tests: none new. Deps: none.
- [ ] **P6.3 (Optional) Auto secondary workflow** — allow the authoring agent to trigger
  `analyze_benchmark_run` (`app.py:5900`) / `handoff_benchmark_failures_to_copilot`
  (`app.py:5773`) after a run, gated by `wren_benchmark_auto_analyze_enabled`
  (`config.py:327`). Tests: gated trigger fires once. Risks: cost/loops — keep behind
  the existing flag, off by default. Deps: P4.*.

### Phase 7 — Oracle validation smoke (cross-cutting blocker for P2.3)

- [ ] **P7.1 Oracle reachability preflight** — confirm the Oracle URI is registered as a
  Superset connection (Track A DP-9) and the agent can `execute_sql` against it; verify
  the Oracle driver is in the Superset image (commit `909ca2ca55`). Tests: live
  `SELECT 1` via the agent. Risks: NLS/UPPERCASE/`NUMBER` shapes — feed into
  `answer_spec` tolerances. **Blocker for P2.3 live validation.** Deps: env/data (owner).
- [ ] **P7.2 End-to-end Oracle dry run** — one real CSV → author → validate gold-SQL on
  Oracle → import → run → results in Superset. Tests: manual e2e. Deps: P2–P5, P7.1.

### Phase 8 — Tests, docs, parity

- [ ] **P8.1 Unit suite** — `tests/unit_tests/superset_ai_agent/` for `corpus_csv`,
  `capability_vocab`, `author_agent` (fake client), the two routes (TestClient), P5.1
  view. Run `venv/bin/python -m pytest tests/unit_tests/superset_ai_agent/ -q`.
- [ ] **P8.2 Frontend tests** — `npx jest AuthoringPanel` + `npx tsc` on touched files.
- [ ] **P8.3 Docs** — update `docs/README.md` index line (done at plan creation),
  `ARCHITECTURE.md` if a new package/route class is added, and this plan's Status.
- [ ] **P8.4 No-regression** — confirm the shipped BenchmarksPanel + run engine
  untouched paths still pass; authoring stays flag-off until P3 verified.

---

## 6. Risks & mitigations (consolidated)

- **LLM-authored ground truth is wrong** (hallucinated gold-SQL/rubric/tags) — the
  central risk; it silently poisons every future run. → P2.3 validation-by-execution +
  P3.4 review gate + `verified_by` provenance; a benchmark records auto-accepted vs
  human-approved counts.
- **Paraphrase drift in synthesized BI docs** (fork has been burned) → P2.4 prefers
  structuring source over free regeneration; review gate; provenance events.
- **CSV segmentation variance** → DP-B3 semi-structured contract removes the guess.
- **Anti-cheat regression** (a future change injects context or enables example recall)
  → P4.1 pins it with a test and surfaces the leakage score in the UI.
- **Oracle result-shape mismatches** → tolerance knobs in `answer_spec`
  (`casefold`/`sig_digits`/`rel_tol`); P7 smoke.
- **Judge non-determinism/cost** → PoLL `wren_benchmark_judge_votes ≥ 3` for real data;
  only `eval_note` items reach the judge; `_JUDGE_ROW_CAP=20`.
- **Agent-DB dialect for the reporting view** → target Postgres (prod), document SQLite
  dev caveat.
- **Scope creep between Track A and Track B** → §1 boundary table; shared core is the
  four `evals.*` functions only.
- **Surfacing exposes write creds to Superset** → P5.2 read-only connection only.

## 7. Open questions for the owner (beyond DP-B0..B7)

- Real input CSV shape (data samples vs question lists vs both) — sets P2.2 default mode.
- Default `layer_mode` under test (bare vs enriched) — DP-B4.
- Native Superset dashboard vs in-app BenchmarksPanel charts as the *primary* results
  surface — DP-B6 (plan builds the dataset either way; dashboard polish scales with this).
