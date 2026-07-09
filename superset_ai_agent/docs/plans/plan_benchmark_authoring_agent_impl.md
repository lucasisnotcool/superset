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

**Status:** IMPLEMENTED (2026-07-01) — DP-B0..DP-B8 accepted as recommended
(owner sign-off: "proceed with full implementation as proposed"; DP-B8 = strip
product surface only, research rig kept). All phases DONE except P7 (Oracle live
smoke — BLOCKED on the Oracle URI being registered as a Superset connection; the
owner runs deploys). As-built summary: `evals/authoring/` package (vocab, CSV
contract, author agent), SSE authoring route + bulk import route, AuthoringPanel
tab, legacy multi-config surface removed (single-config §1.1), anti-cheat pinned
by test, `ai_agent_eval_reporting` view (validated by execution on live Postgres)
+ operator runbook `docs/reference/benchmark_results_surfacing.md`. Verification:
33 new backend tests (suite 1600 green), 18 jest (both panels), tsc + ruff clean.
Flags: `WREN_BENCHMARK_AUTHORING_ENABLED` (code default OFF, example env ON),
`WREN_BENCHMARK_AUTHOR_MODEL`, `WREN_BENCHMARK_AUTHOR_MAX_STEPS` — **needs .env
sync + Windows image rebuild**.

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

### 1.1 SINGLE-CONFIG PARADIGM (owner directive, 2026 — supersedes prior multi-config intent)

**Directive:** the in-app benchmarks platform tests **exactly one configuration — the
agent as-is: auto-onboard + BI RAG/enrichment + Wren semantic layer.** No config
sweep, no grounding-mode arms, no per-run agent-model override. A run measures the
shipped production agent against verified questions; run-vs-run comparison then tracks
that single config **over time** (regression), never config-A-vs-config-B.

**Why this is mostly already true (source-backed):** the in-app run
(`_run_benchmark_job`, `app.py:5118`) already calls the agent with **no grounding
ablation** — no `context_dump`/`wren_base` arms exist in the product; those live only
in the research rig (`run_eval_v4/v5`) + Track A. So the product run is *already*
single-grounding-config.

**What contradicts the directive and MUST be removed (legacy multi-config, "old
intent"):** all latent and **not wired to any UI** (grep found no frontend caller):
- `MatrixRunConfig`, `BenchmarkMatrixRunRequest`, `MatrixRunSubmitted`,
  `BenchmarkMatrixSubmitted` (`evals/schemas.py:259-286`).
- `start_benchmark_matrix` route (`app.py:5433`) + `startBenchmarkMatrix` api.ts helper.
- The per-run **agent-under-test** `model` override on `BenchmarkRunRequest`
  (`schemas.py:206`, "F7 sweeps") — remove. (The **judge** model override
  `wren_benchmark_judge_model` is separate and stays.)
- The `exclude_example_recall` **toggle** (`schemas.py:211`) — fix to the single
  invariant `True` (agent as-is, minus self-leakage of an item's own golden example)
  and drop the switch. This is exactly the owner's own anti-cheat stance.

**What stays (single-config-compatible, keep):** `trials`/`pass_hat_k`, per-run
`by_capability` scoreboard, run-vs-run `compare_benchmark_runs` (now = same config over
time), and `EvalRun.config` (retained as **descriptive provenance** — records the fixed
config that produced a run, so regression comparison knows what changed).

**Original-intent traceability:** the multi-config sweep was an explicit spec goal —
`TESTING_PLATFORM_SPEC.md` F5 ("sweep prompt version(s), model/provider, grounding
mode, onboard mode… the `run_eval_v4` matrix made interactive") and F7 (model sweeps);
§129 ("the 8-config matrix becomes rows here"). This plan **descopes** that for the
product. **DP-B8 (§3)** decides whether "single intent" also strips the research rig's
grounding matrix or leaves it as separate offline science tooling (recommend: leave the
rig; it is not "the benchmarks platform").

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
  default; this plan only *confirms and surfaces* it, R5.1).
- **R5.2 Single-config only (§1.1)** — the product benchmark tests exactly **one**
  configuration: the agent as-is (auto-onboard + BI RAG/enrichment + Wren). No config
  sweep, grounding-mode arm, onboard-mode arm, or agent-model override. The legacy
  multi-config surface is removed (Phase 4B). `EvalRun.config` records the fixed config
  as provenance only; run-vs-run comparison is same-config-over-time.
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
- **DP-B4 Fixed config under test + anti-cheat** → the single tested config is **the
  agent as-is: auto-onboard + BI RAG/enrichment + Wren** (§1.1). Build the semantic
  layer from the BI for *grounding* (onboard + enrich), but **never inject BI at run
  time** and always exclude an item's own golden example. There is **no `layer_mode`
  knob** (that was a multi-config residue — removed). `EvalRun.config` records the
  fixed config as **descriptive provenance only** (agent version, model, onboard/enrich
  fingerprint), not as a selectable arm. Rationale: "no BI injection" means no runtime
  prompt dump (already enforced), NOT "no semantic layer" — the enriched layer *is* the
  as-is agent and is the thing under test.
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
- **DP-B8 Scope of single-intent (§1.1)** → strip the **product** multi-config surface
  (the latent matrix/model-sweep), but **leave the research rig** (`run_eval_v4/v5` +
  Track A grounding matrix) intact as separate offline science tooling. Rationale: the
  owner directive names "the benchmarks platform" (the in-app `evals/` product); the
  rig is a distinct R&D instrument whose whole value is the ablation. **Confirm:** if
  the owner intends "entire" to include retiring the rig's matrix too, add a Track-A
  descope item — but that contradicts the rig's purpose, so recommend keeping it.
  Also confirm the F5/F7 spec sections are **annotated as descoped** (P4B.5), not
  silently ignored.

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

**REMOVED — legacy multi-config surface (Phase 4B, §1.1):** `MatrixRunConfig` /
`BenchmarkMatrixRunRequest` / `MatrixRunSubmitted` / `BenchmarkMatrixSubmitted`
(`evals/schemas.py:259-286`); `start_benchmark_matrix` (`app.py:5433`) + imports
(`app.py:97,107`); `BenchmarkRunRequest.model` (`schemas.py:206`) and
`.exclude_example_recall` toggle (`schemas.py:211`, fixed to `True`); `startBenchmarkMatrix`
api.ts helper. No frontend caller exists (grep-verified).

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

- [x] **P0.1 Confirm DP-B0..DP-B8** — DONE (owner sign-off 2026-07-01, all as recommended). (§3). **Blocker for everything.** Record sign-off
  inline. Deps: none.
- [x] **P0.2 Add config flags** — DONE (config.py + from_env + .env.example; 1 test). `wren_benchmark_authoring_enabled` (+ author model/steps)
  to `config.py:313` with `_env_bool`/`os.getenv` reads (~`:962`). Default authoring
  **off** until P3 lands. Tests: config from_env unit. Risks: none. Deps: P0.1.
- [x] **P0.3 Create `evals/authoring/` package** — DONE (pure modules, no FastAPI imports). (empty modules + `__init__`) with ASF
  headers; ensure no FastAPI imports in pure modules (offline-testable, mirrors
  `evals/` purity rule). Deps: P0.1.

### Phase 1 — Corpus contract + capability vocab (pure, offline-testable)

- [x] **P1.1 Capability vocabulary** — DONE (capability_vocab.py, 12 generic tags = rubric + legend; 3 tests). (`capability_vocab.py`) — a
  fixture-agnostic tag set + one-line definitions (e.g. `join`, `cross_schema`,
  `metric`, `temporal`, `filter_value`, `trap`, `negative`, `aggregation`). R3/R9.
  Tests: vocab is non-empty, stable slugs. Risks: bikeshedding — keep small, extensible.
  Deps: P0.3.
- [x] **P1.2 CSV↔draft-record parser** — DONE (corpus_csv.py; row-level errors; rig-aligned cell forms; 9 tests). (`corpus_csv.py`) — parse the DP-B3 semi-structured
  CSV into `DraftItem` records (question, answer_type, raw answer payload, tags,
  target_schema, source_row); strict validation with row-level error messages; a
  `--validate`-style dry parse (no agent calls). Reuse `AnswerType` (`schemas.py:28`).
  Tests: happy path + ragged/mixed/missing-column rows → precise errors. Risks: messy
  human CSVs — mitigated by declared columns (DP-B3) + clear errors. Deps: P1.1.

### Phase 2 — Authoring agent (backend, the core new build)

- [x] **P2.1 Author-agent prompt + loop** — DONE (author_agent.py mirrors copilot loop; StepSink progress; 11 tests across P2.*). (`author_agent.py`) — mirror `run_copilot_loop`
  (`copilot/loop.py:203`): bounded tool-calling loop over `model_client.chat(tools=…)`,
  returns a reviewable draft (no persistence). Tools: read uploaded doc text, propose
  items, self-correct. **Emits `StepSink`-style progress** for SSE (P6). R1/R10.
  Tests: offline with a fake model client (assert draft shape, step emissions). Risks:
  **hallucinated ground truth** (factor A5) — mitigated by P2.3 validation + P4 review
  gate. Blockers: P1.2. Deps: P1.1, P1.2.
- [x] **P2.2 Extract vs generate modes** — DONE (extract/generate/both; schema grounding via compact summary). — support `extract` (questions present in CSV),
  `generate` (author from data/context), `both` (Track A DP-10). Ground generation on
  live schema (`introspect_schema`, `integrations/superset/client.py:136`). Tests:
  each mode with a fake client. Risks: generation drift — bounded by schema grounding
  + review. Deps: P2.1.
- [x] **P2.3 Gold-SQL validation loop (R2)** — DONE (probe + 2-retry self-correct + needs_review flagging; read-only gate in the route's executor). Live-Oracle exercise deferred to P7. — for each `gold_sql` candidate, execute it
  against the real DB (agent `query`/`execute_sql`, or reuse `dry_run_benchmark_item`
  logic `app.py:4967`) and mark verified only if it runs and returns rows; else flag
  `needs_review`. **This is the single most important correctness gate.** Tests: valid
  SQL → verified; erroring/empty SQL → flagged. Risks: Oracle result-shape quirks
  (UPPERCASE cols, `NUMBER`) — surface tolerance knobs in `answer_spec`
  (`comparator` `casefold`/`sig_digits`/`rel_tol`). Deps: P2.1; **Blocker: P7 Oracle
  connection reachable for live validation.**
- [x] **P2.4 Context doc** — DONE as verbatim assembly (deliberately NOT model-synthesized — paraphrase-drift rule; upload left to the human/panel per R1 no-auto-commit). — turn context blocks into a clean doc and upload
  via `.../documents/text` (`app.py:6582`); optionally enrich the layer
  (`.../documents/{id}/enrich` `:6631`) per DP-B4. **Guard against paraphrase drift**
  (fork rule): prefer quoting/structuring source over free regeneration; mark
  synthesized facts for review. Tests: doc upload + enrich smoke (mocked). Risks:
  paraphrase drift (factor A2) — review gate + provenance. Deps: P2.1.
- [x] **P2.5 Capability tagging** — DONE (model tags from vocab; unknown tags warn). — agent assigns `capability_tags` from P1.1 vocab to
  each item. Tests: tags ⊆ vocab. Risks: low. Deps: P1.1, P2.1.

### Phase 3 — Authoring API + persistence + review UI

- [x] **P3.1 SSE authoring route** — DONE (`POST .../author/stream`; preflight-then-stream; flag-gated 404; catalog build non-fatal; 4 tests). `POST .../benchmarks/{id}/author/stream` (DP-B2) —
  wrap P2 in `StreamingResponse(media_type="text/event-stream")`, serialize with
  `_conversation_sse` (`app.py:460`); yields progress then a final draft. Gated on
  `wren_benchmark_authoring_enabled`. Tests: route emits progress + terminal draft
  (TestClient). Risks: long runs / disconnect — copy `run_stream`'s `GeneratorExit`
  handling (`conversation_graph.py:691`). Deps: P2.*.
- [x] **P3.2 Bulk import route** — DONE (`POST .../items/import`; per-row errors, dedup, cap; 2 tests). `POST .../benchmarks/{id}/items/import` (DP-B7) —
  construct `BenchmarkItem(...)` per reviewed draft and `add_item` (`store.py:120`);
  dedup by casefolded question (mirror `import_golden_queries_as_items` `app.py:6043`);
  return created/skipped counts (`GoldenImportResponse` shape). Tests: create + dedup.
  Risks: partial failure — wrap per-item, report row errors. Deps: P1.2.
- [x] **P3.3 AuthoringPanel tab** — DONE (upload CSV/.md, streamed steps, editable review grid, import approved; 3 jest tests; tsc clean). `AuthoringPanel.tsx` + tab wire in
  `SemanticLayerEditor/index.tsx:1824` (DP-B1). Upload CSV/`.md`, stream authoring
  (reuse `consumeConversationStream` `api.ts:1057`), render an **editable draft grid**
  (reuse `buildAnswerSpec` `api.ts:240` + add-item modal shape
  `BenchmarksPanel.tsx:261`), then **Import reviewed** → P3.2. Tests: RTL — upload →
  draft rows → import call. Risks: UI scope creep — v1 = table + edit + import only.
  Deps: P3.1, P3.2.
- [x] **P3.4 Review/verify gate (R4)** — DONE (import stamps verified_by/at; needs_review rows require an explicit tick; nothing persisted by the stream — pinned by test). — reviewed items imported with `verified=true`
  (stamps `verified_by`/`verified_at`); UI shows unverified vs verified; a run may warn
  if unverified items are included. Tests: verified flag round-trips. Risks: none.
  Deps: P3.2, P3.3.

### Phase 4 — Run wiring (mostly confirmation)

- [x] **P4.1 Anti-cheat pinned (R5/R5.1)** — DONE (test asserts bare question, no model override, exclude_example_questions=[own]; leakage flag on example items surfaces in scores). — verify `_run_benchmark_job`
  (`app.py:5118`) still calls the agent with **no `extra_context`** and
  `exclude_example_recall=True`; surface the `golden_example_recalled` score in the UI
  results view so leakage is visible. Tests: assert no BI context in the run call;
  leakage score renders. Risks: silent regression if the run path changes — pin with a
  test. Deps: none (runs today).
- [x] **P4.2 Fixed-config provenance** — DONE (EvalRun.config = {agent_config: as-is, model, layer: wren_bi, exclude_own_example: true}; test-pinned). — `EvalRun.config` records the
  single tested config descriptively (agent version, model, onboard/enrich fingerprint,
  `layer=wren_bi`). **No selectable arm / no `layer_mode` request field.** Tests: run
  config persists the fixed descriptor. Risks: none. Deps: P4B (schema simplified first).

### Phase 4B — Align legacy multi-config to single intent (§1.1, DP-B8) — do BEFORE P4.2/P5

> The product tests exactly one config. Strip the latent multi-config surface so the
> platform, its schemas, and its docs state one intent. All items below are **removals /
> invariant-fixes** of code that has **no frontend caller** (low blast radius), plus a
> spec annotation. **Blocker:** DP-B8 sign-off (do NOT delete the research rig).

- [x] **P4B.1 Matrix surface removed** — DONE (schemas, route, api.ts helper, panel fan-out + its tests; route now 404s — test-pinned). NOTE: the matrix HAD gained a UI caller since planning (comma-separated models in BenchmarksPanel) — removed too. — delete `MatrixRunConfig`,
  `BenchmarkMatrixRunRequest`, `MatrixRunSubmitted`, `BenchmarkMatrixSubmitted`
  (`evals/schemas.py:259-286`); the `start_benchmark_matrix` route (`app.py:5433`) and
  its imports (`app.py:97,107`); the `startBenchmarkMatrix` api.ts helper + its TS
  types. Tests: route 404s / removed; existing single-run tests still green. Risks:
  none (no UI caller — grep-verified). Deps: DP-B8.
- [x] **P4B.2 Model override removed** — DONE (request field, run-job param, panel input; judge override kept; legacy-knob-ignored test). — drop `model` from
  `BenchmarkRunRequest` (`schemas.py:206`) and its use in `start_benchmark_run` /
  `_run_benchmark_job`; the agent always answers with its configured model. **Keep** the
  judge override `wren_benchmark_judge_model` (separate concern). Tests: run ignores/omits
  agent-model; judge override still honored. Risks: none. Deps: DP-B8.
- [x] **P4B.3 Exclusion invariant** — DONE (field removed; hardwired [item.question]; leakage detection retained on example items). — remove the field/toggle
  from `BenchmarkRunRequest` (`schemas.py:211`) and hardwire the exclude-own-golden
  behavior in `_run_benchmark_job`. Rationale: "agent as-is, minus self-leakage" is the
  only correct value. Tests: run always excludes the item's own example; leakage score
  path intact. Risks: none. Deps: DP-B8.
- [x] **P4B.4 Comparison copy** — DONE (docstrings state same-config-over-time). — keep `compare_benchmark_runs` /
  `RunComparisonResponse` but confirm semantics/copy are "same config across runs"
  (regression), not "config arms." No behavior change; docstring + any UI label update.
  Tests: none new. Risks: none. Deps: P4B.1.
- [x] **P4B.5 Spec annotated** — DONE (TESTING_PLATFORM_SPEC.md F5/F7 marked DESCOPED, pointing at the rig). — mark `TESTING_PLATFORM_SPEC.md` F5 (config sweep /
  matrix) and F7 (model sweeps) as **DESCOPED for the product (single-config directive
  §1.1)**; point their multi-config value at the research rig. Do not delete the spec
  history — annotate. Tests: n/a. Risks: none. Deps: DP-B8.
- [x] **P4B.6 Data note** — DONE (no migration needed; historical run.config rows may carry legacy keys — documented in the surfacing runbook). — `EvalRun.config` may hold rows written under old
  matrix runs; a migration is **not** required (JSON column, descriptive), but document
  that historical rows may carry legacy keys. Tests: n/a. Deps: P4B.1.

### Phase 5 — Native Superset results surfacing (DP-B6, R7)

- [x] **P5.1 Reporting view** — DONE (migration 0022, Postgres-only with documented SQLite no-op; VALIDATED BY EXECUTION on live Postgres: tag unnest, tagless rows, override folding; 2 offline tests). — `CREATE VIEW` flattening
  `ai_agent_eval_results ⋈ ai_agent_eval_scores ⋈ ai_agent_eval_items` (columns:
  run_id, benchmark_id, item_id, question, capability_tag (unnested), verdict,
  verdict_source, trial_index, score name/value, run config). Ship as a migration or a
  documented DDL. Tests: view returns rows against seeded eval data. Risks: agent-DB
  dialect (SQLite dev vs Postgres prod) — target Postgres (postgres-only deploy);
  document the SQLite caveat. Deps: existing eval tables (0019).
- [x] **P5.2 Dataset runbook** — DONE as operator doc (docs/reference/benchmark_results_surfacing.md: read-only role DDL + connection + dataset steps). Live registration is an operator action. — in the
  postgres-only deploy the agent DB is reachable; add it as a Superset database and a
  dataset on the P5.1 view. Provide as an importable connection/dataset bundle (no
  secrets committed). Tests: manual — dataset previews. Risks: **read-only creds only**;
  never expose write. Deps: P5.1.
- [x] **P5.3 Charts** — DONE as chart recipes in the same runbook (pass-rate by tag/run, verdict mix, judge-vs-human, leakage watch); an importable ZIP deferred until the live dataset exists (needs P5.2 operator step). — charts: pass-rate by `capability_tag`, by run, and a
  run-vs-run comparison (reuse the platform's paired-delta semantics, not bare deltas).
  Ship as an importable dashboard ZIP. Tests: manual render. Risks: none. Deps: P5.2.

### Phase 6 — Observability (R8)

- [x] **P6.1 Authoring progress** — DONE (SSE steps render live in the panel; jest-covered). — the P3.1 SSE stream drives a live step list
  in `AuthoringPanel` (segment → synthesize → author → validate → tag). Reuse the
  progress-event shape (`conversation_graph.py:2234`). Tests: RTL sees streamed steps.
  Deps: P3.1, P3.3.
- [x] **P6.2 Run progress** — CONFIRMED as-is (polling + durable events untouched; suite green). — confirm BenchmarksPanel polling
  (`RUN_POLL_MS`, `BenchmarksPanel.tsx:205`) + durable `benchmark_run_progress` events
  (`app.py:5262`) cover the run. Optional: add authoring events to the same project
  event stream. Tests: none new. Deps: none.
- [x] **P6.3 Secondary workflows** — CONFIRMED existing (auto-analyze flag + handoff routes untouched); no new trigger added (optional item). — allow the authoring agent to trigger
  `analyze_benchmark_run` (`app.py:5900`) / `handoff_benchmark_failures_to_copilot`
  (`app.py:5773`) after a run, gated by `wren_benchmark_auto_analyze_enabled`
  (`config.py:327`). Tests: gated trigger fires once. Risks: cost/loops — keep behind
  the existing flag, off by default. Deps: P4.*.

### Phase 7 — Oracle validation smoke (cross-cutting blocker for P2.3)

- [ ] **P7.1 Oracle reachability preflight** — **BLOCKED (owner action)**: needs the Oracle URI registered as a Superset connection on the target stack; then a `SELECT 1` probe + one authoring pass with gold-SQL validation against Oracle. — confirm the Oracle URI is registered as a
  Superset connection (Track A DP-9) and the agent can `execute_sql` against it; verify
  the Oracle driver is in the Superset image (commit `909ca2ca55`). Tests: live
  `SELECT 1` via the agent. Risks: NLS/UPPERCASE/`NUMBER` shapes — feed into
  `answer_spec` tolerances. **Blocker for P2.3 live validation.** Deps: env/data (owner).
- [ ] **P7.2 End-to-end Oracle dry run** — BLOCKED on P7.1. — one real CSV → author → validate gold-SQL on
  Oracle → import → run → results in Superset. Tests: manual e2e. Deps: P2–P5, P7.1.

### Phase 8 — Tests, docs, parity

- [x] **P8.1 Unit suite** — DONE (33 new tests; full agent suite 1600 passed / 13 skipped). — `tests/unit_tests/superset_ai_agent/` for `corpus_csv`,
  `capability_vocab`, `author_agent` (fake client), the two routes (TestClient), P5.1
  view. Run `venv/bin/python -m pytest tests/unit_tests/superset_ai_agent/ -q`.
- [x] **P8.2 Frontend tests** — DONE (18 jest across AuthoringPanel + BenchmarksPanel; `npx tsc --noEmit` clean). — `npx jest AuthoringPanel` + `npx tsc` on touched files.
- [x] **P8.3 Docs** — DONE (README index entries, surfacing runbook, spec annotations, this plan updated). — update `docs/README.md` index line (done at plan creation),
  `ARCHITECTURE.md` if a new package/route class is added, and this plan's Status.
- [x] **P8.4 No-regression** — DONE (matrix/override/toggle tests replaced with single-config pins; authoring flag-off by default in code). — confirm the shipped BenchmarksPanel + run engine
  untouched paths still pass; authoring stays flag-off until P3 verified. After Phase 4B,
  update/remove any existing tests that exercised the matrix route / `model` override /
  `exclude_example_recall` toggle so the suite asserts the **single-config** contract.

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
- **Removing legacy multi-config breaks a hidden consumer** → the matrix surface has
  **no frontend caller** (grep-verified) and the model/recall knobs default to the
  single-config values already; Phase 4B updates/removes their tests (P8.4). Low blast
  radius, but re-grep for external callers (CI, scripts) before deleting.

## 7. Open questions for the owner (beyond DP-B0..B8)

- Real input CSV shape (data samples vs question lists vs both) — sets P2.2 default mode.
- **Does "single intent" extend to the research rig?** — DP-B8. Recommend no (keep the
  rig's grounding matrix as offline science; strip only the product surface).
- Native Superset dashboard vs in-app BenchmarksPanel charts as the *primary* results
  surface — DP-B6 (plan builds the dataset either way; dashboard polish scales with this).
