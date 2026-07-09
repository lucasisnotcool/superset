<!--
Testing Platform spec — formalizing the local evaluation harness into a first-class,
in-product LLM/agentic testing platform for the Superset AI Agent.
Status: PROPOSED (awaiting sign-off). Owner: AI Agent team.
Covers: industry landscape, conceptual data model, feature list (pros/cons, adoption,
risks/mitigations, decision points), build-vs-buy, dev-intent<->spec<->UI alignment,
and a phased roadmap. Decision points DP-1..DP-12 are flagged inline; recommendations given.

DIRECTION UPDATE (post-review, supersedes the original §0/§5 "build native" lean):
Stakeholder decisions — (1) PREFER an OSS platform and INTEGRATE it with Superset,
rather than building a native eval platform from scratch; (2) DO NOT implement yet —
deepen the spec. The recommendation below is therefore reframed around OSS-selection +
integration architecture (see §5 REVISED and the new §11 Integration Architecture).
The industry landscape (§2), conceptual data model (§3), and feature analysis (§4)
remain valid — but each F-feature is now read as "provided by the adopted OSS tool"
vs "a thin bridge we build," not "built native." OSS selection facts are VERIFIED
(Jan 2026, §11.1): FINAL PICK = DeepEval (Apache-2.0) in-process engine + Arize
Phoenix (ELv2, Postgres-only) self-hosted UI + Promptfoo (MIT) optional in CI.
Langfuse ruled out (v3 mandates ClickHouse, violating postgres-only). Our Postgres
stays the system of record for eval data.

REVISION 2 (July 2026, re-verified research pass): §12-§17 below. Adds the two
features the original spec missed — F11 Project Benchmarks (the primary "test MY
MDL project" user flow, Genie-style, in MDL Lab) and F12 Scientist agent (writes
tests, interprets results, hands failure analyses to MDL Copilot as reviewable
changesets, built on the shipped coverage-recovery precedent). Refreshes all OSS
facts (Langfuse→ClickHouse acquisition; Promptfoo→OpenAI acquisition; MLflow 3.x
newly scored; OTel gen_ai.evaluation.result event). Re-opens ONE decision (DP-16):
Phoenix as the *product* UI vs native product surfaces + Phoenix as optional
internal-eng sidecar — new evidence (category precedent: Genie/Omni/Power BI all
built native; none embed a third-party eval UI) recommends the split. Upgrades the
scoring methodology (§16) to BIRD/Genie-grade comparison + pass^k + paired-delta
statistics. Re-orders phasing: Project Benchmarks before the prompt registry.
-->

# Superset AI Agent — Testing & Evaluation Platform

**A product spec to turn `evaluation/` (local scripts + notebooks) into a fully-featured, in-product LLM/agentic test suite** where users can edit prompts, curate questions, configure experiments, run agentic evals, and compare results over time.

---

## 0. TL;DR & headline recommendation

**What we have:** a genuinely well-engineered *offline* eval harness — Markdown fixtures, a ground-truth-keyed Python scorer (`seagate_scoring.py`), a parameterized runner (`run_eval_v4.py`), a capability-tagged scoreboard, and versioned findings (`RESULTS_v*.md`). It is a platform in everything but *productization*: no persistence of runs for comparison, no API, no UI, prompts are static files, and only an engineer at a shell can drive it.

**What to build:** a native **"AI Agent Lab › Evals"** surface inside Superset, reusing the *proven* admin-surface pattern already shipped for LLM-call logging (chokepoint capture → Protocol store → admin-gated FastAPI route → FAB menu link → lazy React page). Adopt the **industry-standard conceptual data model** (Dataset/Example, Experiment/Run, Evaluator/Score, Trace/Span, Prompt-version, Annotation) so our vocabulary matches every tool engineers already know.

**Build vs. buy — recommendation:** **Build native, but speak open standards.** Embed the surfaces (the whole point is that *Superset users* iterate on the *Superset agent* without leaving the product and without shipping their data to a third party). De-risk it by (a) modeling our schema on the common vocabulary below and (b) emitting **OpenTelemetry / OpenInference** GenAI spans so a team that already runs **Langfuse (self-hosted, MIT)** or **Arize Phoenix** can point their collector at us for free. This gives us a first-class in-product loop *and* an interop escape hatch, without a SaaS dependency or a data-egress problem. (Details & the dissent in §5.)

**Phasing:** P0 persist runs & prompts (unlock comparison) → P1 prompt editor + eval-run UI → P2 dataset/experiment authoring + LLM-judge library → P3 agentic/trajectory evals + CI gating + OTel export. Ship value at every phase.

---

## 1. Current state — what `evaluation/` already is

> Grounding: `superset_ai_agent/evaluation/`, `prompts/registry.py`, `app.py`, `persistence/models.py`. Full architectural map retained in the team notes; summarized here for the parts that drive product decisions.

### 1.1 The eval harness (offline, HTTP-client)

| Concern | Today | File |
|---|---|---|
| **Questions / dataset** | Authored in **Markdown** (`## L1..L4`, `**Q1 ...**`, `Answer:`), parsed by `parse_test_queries` | `dev_fixtures/seagate_multi/test_queries.md`, `eval_common.py` |
| **Ground truth** | Hand-maintained **Python dict** keyed by qid (`nums`/`names`/`absent`/`trap`/`zero`) | `seagate_scoring.py` `EXPECTED` |
| **Relevant vs distractor tables** | `tables.json` | `dev_fixtures/seagate_multi/tables.json` |
| **Experiment config** | **Code** — `expand_configs()` builds the 8-config `grounding × onboard` matrix | `run_eval_v4.py` |
| **Runner** | `argparse` CLI: `python run_eval_v4.py --trials 3 [--questions Q1,Q2]` | `run_eval_v{2,3,4}.py` |
| **Scoring** | Deterministic scorer (`correct/partial/wrong/trap_ok/trap_fail`, 2% rel-tolerance) **+** a conservative LLM/assistive grader | `seagate_scoring.py`, `eval_common.grade_one` |
| **Metrics** | Correctness, **capability tags** (`slang/join1/xschema2/bridge/metric/trap/...`), table-selection P/R/F1 + distractor-inclusion, view-authoring quality, golden-query recall lift, coverage | `eval_v2/v3.py`, `seagate_scoring.py` |
| **Results** | Flat **JSON** per experiment + `scoreboard.json`/`trials.json`; write-ups in `RESULTS_v*.md` | `results/seagate_multi_v4/` |
| **Trials / variance** | 3-trial runs, min–max ranges in the scoreboard | `run_eval_v4.build_scoreboard` |

**Strengths to preserve:** it is *diagnostic* (config × capability, not one number), it has *real ground truth* (reproducible from `superset/examples/seagate_manufacturing/generate_data.py`), it measures *variance* (multi-trial), and `EVAL_V4_SPEC.md` is effectively a pre-written spec for the science layer.

**Gaps that block "platform":**
1. **No persistence of runs** for cross-run/regression comparison (JSON files on disk, no history, no diff).
2. **No API / no UI** — only a shell + notebooks; non-engineers cannot add a question or read a scoreboard.
3. **Prompts are static files**, `lru_cache`d at process load — no runtime override, no versioning, no attribution of a score to a prompt version.
4. **Ground truth split across Markdown + Python** — brittle, not queryable, not user-editable.
5. **Model/provider is env-only** — can't sweep models from the UI.

### 1.2 The two seams the codebase already left open

These are decisive for scoping — the hard architectural moves are *already anticipated*:

- **Prompt registry seam.** `prompts/registry.py` `get_prompt()` docstring: *"This is intentionally file-backed for Phase 1. A later prompt registry can replace this function with a database-backed implementation."* → a DB-backed, versioned, user-editable prompt store is **intended dev intent**, not a new idea.
- **Admin-surface pattern (shipped & proven).** LLM-call logging shipped end-to-end: `MeteredModelClient` wraps the model client at one chokepoint (`app.py` `wrap_model_client`) → `SqlAlchemyLlmUsageStore` Protocol store → admin-gated `@api.get("/agent/admin/llm-usage")` → FAB menu link (`initialization/__init__.py`) → lazy React page (`src/pages/AiAgentUsage/`). **This exact chain is the template for every surface in this spec.**
- **Persistence is already Postgres+pgvector.** `ai_agent_*` tables, Alembic migrations, `ai_agent_nl_sql_examples` (golden store) — a new set of `ai_agent_eval_*` tables slots straight in. No new infra.
- **LLM client is abstracted.** `ModelClient` Protocol + `create_model_client(config)` factory + `MeteredModelClient` wrapper → model-sweeps and an LLM-judge reuse the same client contract.

---

## 2. Industry landscape

Nine platforms define the category. The three marked ✎ were researched in depth this cycle (see appendix notes); the rest are characterized from current knowledge (cutoff Jan 2026). **Verify any pricing before quoting — this category re-prices constantly.**

### 2.1 Comparison matrix

| Platform | License / model | Self-host | Prompt mgmt | Evaluators | Experiments/compare | Agentic/trajectory | RAG metrics | Notable adopters | Best at |
|---|---|---|---|---|---|---|---|---|---|
| **LangSmith** (LangChain) | Proprietary SaaS (OSS client libs MIT) | Enterprise only | Prompt Hub (commits, tags) | LLM-judge (`openevals`), code, off-the-shelf | Yes; pairwise; regression | Yes (LangGraph-native) | Via custom evals | Rakuten, Moody's, Klarna, Elastic | LangChain/LangGraph shops |
| **Langfuse** ✎-adjacent | **OSS MIT** (some ee features commercial) | **Yes, first-class** (Docker/K8s, PG+ClickHouse) | Versioned + **labels** (prod/staging), client cache | Managed LLM-judge, custom scores, human queues | Dataset experiments; compare | Sessions/observations | Custom + templates | Samsara, Twilio, Khan Academy | **Self-host / data-residency** |
| **Braintrust** | Proprietary SaaS (autoevals lib MIT) | Enterprise (hybrid) | Yes | **autoevals** (Factuality, ClosedQA…), LLM-judge, heuristic | **Best-in-class matrix Playground** | Spans | Some built-in | Notion, Stripe, Airtable, Vercel, Instacart | **Rapid eval iteration UX** |
| **Promptfoo** | **OSS MIT** | **Yes (CLI-first, local)** | Config (YAML) | Deterministic asserts + model-graded + **red-team/OWASP** | Matrix (prompts×providers×tests) | Basic | context recall/relevance/faithfulness | Shopify, Discord, Doordash | **CI gating & red-teaming** |
| **Arize Phoenix** ✎ | **ELv2** (source-available, *not* OSI) | **Yes** (pip/Docker) | Versioned + **Playground**, span replay | Rich pre-built (hallucination, QA, RAG, **SQL-gen**, tool-call), LLM-judge, human | Datasets→Experiments, compare runs | **Tool-call / function eval** | Strong pre-built | Booking.com, Uber, Duolingo, PepsiCo | **OSS tracing + eval, OTel** |
| **Arize AX** ✎ | Commercial SaaS/self-host | Enterprise | (as Phoenix) + online | + online/production evals, Copilot | + production monitoring | Yes | Yes | (as above) | Enterprise observability |
| **W&B Weave** ✎ | **Apache-2.0 SDK** (+ trace_server); licensed self-host backend | Enterprise (K8s+ClickHouse) | `StringPrompt`/`MessagesPrompt`, versioned, Playground | `weave.Scorer`, LLM-judge, **local SLM guardrails** (toxicity/PII/…), leaderboards | Compare view, **Leaderboards** | Sessions/turns/tools/sub-agents first-class | Hallucination, context-relevance | OpenAI, NVIDIA, Snowflake, Canva | W&B-ecosystem teams |
| **Humanloop** ✎ | **DEFUNCT** — acqui-hired by Anthropic, sunset **Sep 8 2025** | (was VPC) | Was best-in-class (`.prompt` files, git) | code/AI/human | regression via offline evals | Sessions | — | Duolingo, Gusto, Vanta | *(cautionary tale — see §5)* |
| **DeepEval** (Confident AI) | **OSS Apache-2.0** (SaaS layer separate) | Yes (library) | via Confident AI | **Pytest-style**: G-Eval, RAG suite, **tool-correctness, task-completion** | via Confident AI | **Yes (tool/task metrics)** | **Full RAGAS-style suite** | (OSS-wide) | **Agentic + RAG metrics in code** |

Honorable mentions: **RAGAS** (the OSS RAG-metric definitions everyone borrows: faithfulness, answer-relevancy, context precision/recall), **PromptLayer** (non-technical prompt registry), **Helicone** (OSS proxy/observability), **Vellum/Galileo/LangWatch** (enterprise eval/guardrails), **Traceloop/OpenLLMetry** (the OTel GenAI semantic conventions).

### 2.2 What the deep-dives changed in our thinking

- **Humanloop (defunct):** the strongest *prompt-management* product in the category **exited the market** (Anthropic acqui-hire; platform + all data permanently inaccessible after a ~4-week notice). Direct evidence for **owning our prompt/eval store in-product** rather than depending on a young SaaS. (TechCrunch 2025-08-13; Humanloop docs changelog.)
- **Arize Phoenix is ELv2, not Apache-2.0** — source-available but forbids offering it "as a hosted/managed service." Fine to self-host and learn from; **a licensing blocker to vendoring its code into an Apache-2.0 project like Superset.** Use its *OpenInference schema* (open) for interop, not its code.
- **W&B Weave SDK is Apache-2.0** (incl. `trace_server`) — safe to reference/borrow patterns; the *production self-host backend* still needs a commercial license + ClickHouse ops.
- **Every SaaS meters on volume** (Arize $/span+$/GB; Weave $0.10/MB ingested) → embedding evals that trace every agent turn into a paid SaaS has an **unbounded, usage-coupled cost** we'd rather not put in front of Superset operators.

---

## 3. The common conceptual data model (adopt this vocabulary verbatim)

Every tool above converges on the same nouns. Matching them makes our platform legible to any engineer and makes OTel/OpenInference export a mechanical mapping.

| Concept | Definition | Our mapping | Superset-agent nuance |
|---|---|---|---|
| **Dataset** | A named, versioned collection of test inputs | new `ai_agent_eval_dataset` | e.g. "Seagate multi-schema v4" |
| **Example** (Datapoint/Item) | One test case: `input` + optional `reference`/expected + `metadata`/tags | `ai_agent_eval_example` | question + expected `{nums/names/absent/trap/zero}` + capability tags + relevant/distractor tables |
| **Experiment** | A configured run of a Dataset through a *target* (agent version × prompts × model × grounding config) with a set of Evaluators attached | `ai_agent_eval_experiment` | the 8-config matrix becomes rows here |
| **Run / Trial** | One execution of an Experiment (repeatable for variance) | `ai_agent_eval_run` (+ `trial_index`) | our 3-trial pattern; store min–max |
| **Result** | Per-example outcome within a Run: produced output (SQL, answer, trace ref) + Scores | `ai_agent_eval_result` | SQL, verdict, recalled-example count, sql_matches_golden |
| **Evaluator / Scorer** | A function Example×Output→Score. Deterministic, statistical, LLM-judge, or human | `ai_agent_eval_evaluator` | `seagate_scoring.score_result` is our first built-in |
| **Score** | A single measured value (bool/number/categorical) + optional rationale | `ai_agent_eval_score` | `correct/wrong/trap_ok`, P/R/F1, coverage |
| **Trace / Span** | Structured record of one agent execution (LLM calls, retrieval, tool calls) | reuse `ai_agent_events` + `ai_agent_llm_calls`; add span linkage | LangGraph nodes = spans; already partly captured |
| **Prompt (version)** | A versioned, named prompt template with variables; deployable by label | `ai_agent_prompt` + `ai_agent_prompt_version` | replaces static `prompts/*.md`; keep files as seed/default |
| **Annotation** | A human label/comment on a Result or Trace (ground-truth or review) | `ai_agent_eval_annotation` | powers "manual_review" verdicts + human eval |

**Design rule:** Datasets, Prompts, and Experiments are *versioned, addressable objects*; Runs and Results are *immutable events* referencing the exact object versions used. This is what makes "did prompt v7 regress against dataset v3?" answerable — the single most valuable capability we lack today.

---

## 4. Feature list (the product)

Each feature: **what**, **why/user intent**, **pros/cons**, **enterprise-adoption evidence**, **risks & mitigations**, **decision points**, **recommendation**. Ordered by dependency (early features unlock later ones).

### F1 — Run & scoreboard persistence (foundation)

- **What:** persist every eval run (Experiment/Run/Result/Score) to `ai_agent_eval_*` Postgres tables instead of loose JSON; keep a history; compute deltas vs. a chosen baseline run.
- **User intent:** "Did my change help or regress, vs. last week / vs. main?" — impossible today.
- **Pros:** unlocks comparison, regression detection, trend charts, and every UI below. Small surface (schema + a writer in the runner). **Cons:** schema-migration discipline; JSON results must be migrated/backfilled (or start fresh).
- **Adoption evidence:** *the* universal primitive — LangSmith Experiments, Langfuse dataset-runs, Braintrust Experiments, Phoenix Experiments all persist runs for comparison.
- **Risks/mitigations:** *R:* scoreboard schema churn → *M:* version the `scoreboard.json` schema in a `meta.schema_version` column; keep the runner able to emit both JSON (back-compat) and DB rows during transition.
- **DP-1:** Backfill historical JSON runs, or start history fresh at cutover? **Rec:** start fresh; optionally one-time import `seagate_multi_v4/*.json` as run #0.
- **Recommendation:** **Build first.** Everything depends on it and it's low-risk.

### F2 — DB-backed, versioned Prompt Registry + editor

- **What:** move prompts from `prompts/*.md` to `ai_agent_prompt` / `ai_agent_prompt_version`; `get_prompt(name)` resolves *active version by label* (default → the file-seeded content) with the file as the immutable fallback/seed. Admin UI to view/edit/diff/rollback, and to deploy a version to a label (`production`/`candidate`).
- **User intent:** "Let me tweak the text-to-SQL prompt and measure it — without a code deploy."
- **Pros:** directly fulfills the headline ask; the seam is pre-designed (`registry.py` docstring); enables prompt-as-experiment-variable (F5). **Cons:** prompts become runtime state (needs the same governance/audit as code); cache-invalidation on edit; a bad prompt can degrade prod → needs labels + guardrails.
- **Adoption evidence:** Langfuse (versions+labels+client cache), LangSmith Prompt Hub (commits/tags), Weave (`weave.publish`), Braintrust — prompt versioning is table-stakes. Humanloop's `.prompt`-in-git model is the gold standard for *diffability*.
- **Risks/mitigations:**
  - *R:* editable prod prompts = new attack/error surface. *M:* **admin-gated writes** (reuse `require_admin`), full audit trail (who/when/diff), and a **label indirection** so prod always runs a pinned, reviewed version — edits create *candidate* versions that must be promoted.
  - *R:* prompt/code drift (a prompt file changes but code expects old variables). *M:* validate declared `{variables}` against call-site context at save; keep files as the seed + a "reset to file default" action.
  - *R:* cache staleness across the FastAPI worker + processes. *M:* replace `lru_cache` with a short TTL cache keyed by `(name, active_version_id)`; bust on write.
- **DP-2:** File-backed defaults *plus* DB overrides (files remain source-of-truth defaults, DB is the override layer) **vs.** full migration into DB (files become one-time seed only)? **Rec:** **hybrid** — files are the versioned default seed shipped with the repo; DB holds overrides + history. Preserves git-diff review of defaults *and* runtime editability. **(mirrors Langfuse "prompt in code as fallback".)**
- **DP-3:** Who can edit prompts — Admin only, or a new `ai_agent_prompt_editor` role? **Rec:** Admin-only writes for P1 (matches the shipped admin-surface pattern & `SECURITY.md`: Admin is the trusted operational principal); consider a scoped role later.
- **Recommendation:** **Build second (P1).** Highest user-visible value; seam already designed.

### F3 — Dataset & question authoring UI

- **What:** CRUD for Datasets/Examples in the UI: add a question, set expected answer (typed: numeric+tolerance / name-set±absent / trap / zero), attach capability tags and relevant/distractor tables, version the dataset. Import from the existing Markdown fixtures; export back.
- **User intent:** "Add my org's real questions and correct answers without editing Python."
- **Pros:** replaces the brittle Markdown+Python split; lets domain experts (who know the *right answers*) contribute; datasets become versioned objects. **Cons:** need a typed answer-spec editor (numeric tolerance, membership, trap) — more than a text box; migration of `seagate_scoring.EXPECTED` into rows.
- **Adoption evidence:** universal (LangSmith/Langfuse/Braintrust/Phoenix all have dataset UIs + import from traces).
- **Risks/mitigations:** *R:* users author under-specified ground truth → noisy scores. *M:* the typed answer-spec + a "dry-run this example against current agent" preview before saving; keep the deterministic scorer's tolerances explicit and visible.
- **DP-4:** Model expected-answer as our current typed dict, or adopt a more general assertion model (à la Promptfoo `assert:` / DeepEval metrics)? **Rec:** keep the **typed answer-spec** for numeric/SQL ground truth (it's our differentiator and matches Seagate ground truth), *and* allow attaching general Evaluators (F4) on top. Two complementary layers.
- **Recommendation:** **P2.** Depends on F1; pairs with F4.

### F4 — Evaluator library (deterministic + LLM-judge + human)

- **What:** a registry of Evaluators with a common interface. Ship: (1) the existing **deterministic ground-truth scorer**; (2) **SQL evaluators** (result-set match, `sql_matches_golden`, executes-without-error, plan-valid); (3) **RAG/retrieval** metrics (table-selection P/R/F1 + distractor-inclusion — already built; plus context-recall/precision framing); (4) **LLM-as-judge** (pointwise rubric + pairwise A/B) reusing `ModelClient`; (5) **human annotation** queue.
- **User intent:** "Score correctness, retrieval quality, and 'is this answer good' — and let a human break ties."
- **Pros:** most of this **already exists** as pure functions (`seagate_scoring`, `table_selection_metrics`, `view_authoring_metrics`) — this feature is largely *packaging* them behind a common interface + adding LLM-judge & human queue. **Cons:** LLM-judge cost/latency and judge reliability; human queue is real UI surface.
- **Adoption evidence:** DeepEval (G-Eval, tool-correctness, RAG suite), Phoenix (SQL-gen, tool-call, hallucination), Weave (`Scorer` + SLM guardrails), RAGAS (RAG metric definitions). Pointwise+pairwise LLM-judge is standard.
- **Risks/mitigations:**
  - *R:* LLM-judge is non-deterministic / gameable / costs money. *M:* prefer deterministic ground-truth where we have it (we do, for Seagate); use LLM-judge only for open-ended answers; log judge prompts+versions as first-class (a judge is just another Prompt); support a cheaper local SLM judge (cf. Weave guardrails) as an option.
  - *R:* judge and generator sharing a model biases scores. *M:* allow a distinct judge model/provider; record it in the run meta.
- **DP-5:** Build our own evaluator interface, or vendor an OSS lib (DeepEval Apache-2.0 / autoevals MIT)? **Rec:** **own the interface, borrow the definitions.** Wrap our functions in a thin `Evaluator` Protocol (mirrors `weave.Scorer`); optionally *implement* RAG metrics per RAGAS/DeepEval definitions without a heavy dependency. Keeps us Apache-2.0-clean and dependency-light.
- **Recommendation:** **P2**, alongside F3.

### F5 — Experiment configuration & comparison UI

> **DESCOPED for the product (single-config directive, 2026-07-01 —
> plan_benchmark_authoring_agent_impl.md §1.1):** the in-app benchmarks
> platform tests exactly one configuration — the agent as-is (auto-onboard +
> BI enrichment + Wren). The config/grounding/onboard sweep described below
> lives ONLY in the offline research rig (`run_eval_v4/v5`,
> plan_eval_rig_reusable_impl.md); the product's latent matrix surface was
> removed (P4B). Run-vs-run comparison remains, as same-config-over-time.


- **What:** define an Experiment in the UI — pick Dataset(version), pick the **variables to sweep** (prompt version(s), model/provider, grounding mode, onboard mode, trials), attach Evaluators, run. Then a **comparison view**: run-vs-run and matrix (config × capability) with deltas, min–max, and drill-down to per-example SQL/trace. This is the `run_eval_v4` matrix, made interactive.
- **User intent:** "Set up an experiment (like the 8-config matrix) and see exactly what improved."
- **Pros:** turns the crown-jewel `run_eval_v4` into a first-class, repeatable, shareable product; capability × config is *better than most SaaS* (they give one score). **Cons:** async job orchestration (runs are long — minutes); live progress UI (we already have SSE patterns — `/copilot/stream`, `/events`).
- **Adoption evidence:** Braintrust Playground (matrix), LangSmith comparison view, Phoenix experiment compare, Weave Leaderboards. The *diagnostic capability breakdown* is our edge.
- **Risks/mitigations:** *R:* long runs block/timeout. *M:* reuse the existing **job/async + SSE** infrastructure (`ai_agent_jobs`, events SSE) that already powers onboarding/copilot; runs are jobs with progress. *R:* runaway model spend on a big sweep. *M:* pre-run cost estimate (we already meter tokens via `MeteredModelClient`) + a confirmable budget cap.
- **DP-6:** Reuse `ai_agent_jobs` for eval runs, or a dedicated queue? **Rec:** reuse `ai_agent_jobs` + SSE — proven, no new infra.
- **Recommendation:** **P2/P3.** The payoff feature; depends on F1–F4.

### F6 — Agentic / trajectory evaluation

- **What:** evaluate not just the final SQL but the **path**: tool-call correctness (did it call the right retrieval/onboard/enrich tools?), trajectory/step count, multi-turn conversation success, context-selection quality (the join-closure / cross-schema failure modes already studied in `cross_schema_tooling_audit.md`). Uses Traces/Spans (F reuse of `ai_agent_events`).
- **User intent:** "My agent got the wrong answer — was it retrieval, planning, or SQL?" (already the whole point of the capability taxonomy).
- **Pros:** matches our **agentic** reality (LangGraph, multi-node); differentiates from prompt-only tools; we already trace nodes and have a capability taxonomy that *is* a trajectory signal. **Cons:** trajectory eval is the least-standardized area; needs clean span capture.
- **Adoption evidence:** DeepEval (tool-correctness, task-completion), Phoenix (function/tool-call eval), Weave (sessions/turns/tools/sub-agents first-class), LangSmith (LangGraph trajectory). This is the *frontier* everyone is racing on.
- **Risks/mitigations:** *R:* span schema sprawl. *M:* adopt **OpenInference/OTel GenAI** span conventions from day one (F8) so trajectory data is standard.
- **DP-7:** Scope for v1 — tool-call correctness + step-count only, or full multi-turn task completion? **Rec:** start with **tool-call correctness + retrieval-selection** (we already compute selection P/R/F1 and have the audit); defer multi-turn task-completion to a later pass.
- **Recommendation:** **P3.**

### F7 — Model & provider sweeps

> **DESCOPED for the product (single-config directive, 2026-07-01):** per-run
> model overrides were removed with the matrix surface (P4B); the agent always
> answers with its configured model. Model comparisons belong to the offline
> research rig. (The separate judge-model override, a grading concern, stays.)


- **What:** make model/provider a first-class Experiment variable (not just `.env`): run the same Dataset across `gpt-4.1-mini` / a local Ollama model / Azure, compare quality×cost×latency (we already capture tokens+duration via `MeteredModelClient`).
- **User intent:** "Is the cheaper/local model good enough for my questions?"
- **Pros:** high value for cost-conscious operators; the metering + factory already exist. **Cons:** requires the run path to accept a per-run model override (today it inherits boot config).
- **Adoption evidence:** Braintrust/Weave/Phoenix Playgrounds all sweep models; quality×cost×latency comparison is a standard axis.
- **Risks/mitigations:** *R:* per-run client override touches the DI singleton. *M:* the `create_app` DI already builds clients per-config; thread an optional per-run `ModelClient` through the runner (the graph is constructed per-request).
- **DP-8:** Expose arbitrary provider creds in the UI, or only pre-configured providers? **Rec:** **only operator-preconfigured providers** (per `SECURITY.md` — creds are an operator/deployment concern, not a UI input). Sweep *among configured* models only.
- **Recommendation:** **P3** (small once F5 exists).

### F8 — OpenTelemetry / OpenInference trace export (interop escape hatch)

- **What:** emit agent executions as **OTel GenAI / OpenInference spans**; expose an OTLP export (or let an OTel collector scrape). Optional, config-gated, off by default.
- **User intent (operator):** "We already run Langfuse/Phoenix — send the agent's traces there."
- **Pros:** interop without a hard dependency; standards-based; makes our trajectory data (F6) portable; near-zero marginal cost given we already record `ai_agent_events` + `ai_agent_llm_calls`. **Cons:** mapping our events → OTel semantic conventions is non-trivial; standard is still evolving.
- **Adoption evidence:** Phoenix (OpenInference), Weave (OTel ingest), Langfuse (OTel), Traceloop/OpenLLMetry — OTel GenAI is the emerging lingua franca.
- **Risks/mitigations:** *R:* spec churn in OTel GenAI conventions. *M:* isolate the mapping in one adapter module; treat it as best-effort/experimental.
- **DP-9:** Ship export in-scope, or defer? **Rec:** **defer to P3 but design spans OTel-shaped from F6** so it's a serializer, not a rewrite. This is the concrete de-risking of the build-vs-buy decision (§5).
- **Recommendation:** **P3, low effort if F6 spans are OTel-shaped.**

### F9 — CI / regression gating

- **What:** a headless entrypoint (extend `run_eval_v4.py`) + a stored **baseline** so CI can run a Dataset and fail if capability scores regress beyond a threshold; publish the scoreboard as a PR artifact/comment.
- **User intent (dev):** "Block a merge that regresses cross-schema correctness."
- **Pros:** turns evals into a guardrail, not just a dashboard; the runner is already CLI + the offline pure-fn tests already run in CI. **Cons:** non-determinism (LLM variance) makes hard gates flaky → need tolerance bands.
- **Adoption evidence:** Promptfoo (CI-first, assertions gate), DeepEval (pytest), LangSmith/Braintrust CI integrations.
- **Risks/mitigations:** *R:* flaky gates from model variance. *M:* gate on **multi-trial means with min–max bands** (we already produce these), not single runs; start as **non-blocking report**, promote to blocking per-capability once stable.
- **DP-10:** Gate blocking or advisory at launch? **Rec:** advisory (PR comment) first; opt-in blocking per dataset later.
- **Recommendation:** **P3.**

### F10 — Findings / report generation

- **What:** auto-generate the `RESULTS_v*.md`-style narrative from a run (headline deltas, per-capability table, notable regressions) as a shareable artifact.
- **User intent:** "Give me the write-up I currently hand-author."
- **Pros:** codifies an existing manual practice; cheap once F1/F5 exist. **Cons:** narrative quality (could use an LLM summarizer over the scoreboard).
- **Recommendation:** **P3, nice-to-have.** Low risk.

---

## 5. Build vs. Buy vs. Embed — the core decision

**Options:**
- **(A) Adopt an external SaaS** (LangSmith / Braintrust / Arize AX / Weave cloud) and wire the agent to it.
- **(B) Self-host an OSS platform** (Langfuse MIT, or Phoenix ELv2) beside Superset and point the agent at it.
- **(C) Build native surfaces in the Superset AI agent**, speaking open standards for interop.

**Analysis against our constraints:**

| Constraint | (A) SaaS | (B) Self-host OSS | (C) Build native |
|---|---|---|---|
| **In-product UX** (users edit prompts/questions *inside Superset*) | ✗ separate tool | ✗ separate tool | ✓ native |
| **Data residency** (Superset is often on-prem; our memory notes postgres-only, no-PV offsite) | ✗ egress to vendor | ~ ok if self-hosted | ✓ stays in the agent DB |
| **License fit** (Superset is Apache-2.0) | n/a (external) | Langfuse MIT ✓ / **Phoenix ELv2 ✗ to vendor** | ✓ |
| **Cost model** | ✗ per-span/GB, unbounded | ~ ops cost (ClickHouse etc.) | ✓ reuses existing Postgres |
| **Vendor risk** | ✗ (see **Humanloop** sunset) | ~ | ✓ we own it |
| **Time-to-value** | ✓ fast | ~ medium | ✗ slower to build |
| **Interop** | vendor-locked | OTel | ✓ if we emit OTel (F8) |

**Recommendation — (C) build native, with (B) as a first-class interop path.** The user's explicit ask ("build in a testing platform to superset ai agent" where "users can modify prompts, add questions, setup experiments") is an **embed** requirement — options A/B put the loop in a *different* product. Native also aligns with the deployment reality in our own notes (postgres-only, no offsite PVs, self-contained). We de-risk the "reinventing an eval platform" objection two ways: (1) adopt the **exact conceptual data model** (§3) so nothing is bespoke; (2) ship **OTel/OpenInference export (F8)** so a team already on Langfuse/Phoenix gets their traces for free. **Humanloop's sunset** is the cautionary evidence for owning the store; **Phoenix's ELv2** is why we borrow its *schema*, not its *code*.

**Honest dissent / when to revisit:** if the goal shifts from "let Superset users evaluate the Superset agent" to "give our AI *engineers* a general-purpose observability backend across many services," a self-hosted **Langfuse** (MIT, Postgres-friendly) would be the pragmatic buy — and F8 is exactly the bridge that keeps that door open. **DP-11:** confirm the primary user is the *Superset operator/analyst tuning the agent* (→ build native) vs. *our internal AI eng team* (→ consider Langfuse). **Rec/assumption:** the former, per the request.

---

## 6. Dev-intent ↔ feature-spec ↔ user-flow ↔ UI alignment

Making the three layers line up explicitly (the request's "align clear, rational dev intent and actual feature spec, as well as user intent/flow with actual UI").

### 6.1 Personas & their loops
- **Agent tuner (Admin/analyst)** — *"tweak a prompt, run the suite, see if cross-schema improved."* Primary persona.
- **Domain expert (Alpha/analyst)** — *"add our real questions + correct answers."* Contributes Datasets/Examples; may not touch prompts.
- **AI engineer (dev)** — *"gate merges on regression; sweep models; export traces."* CLI + CI + OTel.

### 6.2 The canonical user flow (F1–F5) → UI surfaces

```
Manage ▸ AI Agent ▸ Evals            (FAB menu link — same pattern as "AI Agent Usage")
  ├─ Datasets      → list/version; open a Dataset → Examples table (add/edit typed expected answer, tags, tables)   [F3]
  ├─ Prompts       → list prompts; open → version history + diff + editor; "Save as candidate" / "Promote to prod"  [F2]
  ├─ Experiments   → "New Experiment": pick Dataset@version, sweep {prompt versions, model, grounding, onboard, trials},
  │                   attach Evaluators → Run (async job + live SSE progress)                                        [F5,F4]
  └─ Runs          → history; open a Run → scoreboard (config × capability, deltas vs baseline, min–max),
                      drill into a cell → per-example SQL / verdict / trace; "Compare to run…"                       [F5,F1]
```

- **Reuses:** the `AiAgentUsage` page scaffold (fetch `getAgentBaseUrl()/agent/...`, 403 handling, stat cards + tables), the SSE progress pattern (`/copilot/stream`, `/events`), and the `SemanticLayerEditor` component vocabulary (browser + detail pane) — so the Evals surface *looks and behaves like* the existing MDL Lab, not a bolt-on.
- **Where it lives:** an admin-gated route under `src/pages/AiAgentEvals/` (mirror `src/pages/AiAgentUsage/`), plus optionally an entry inside the existing **MDL Lab / AiAgentPanel** for the "run these questions against *this* project" flow (project-scoped evals — natural because Datasets can be project-scoped, like golden queries already are).

### 6.3 Intent-alignment checks (guarding against drift)
- **Prompt editing** *intends* safe iteration → UI enforces **candidate→promote** (never edit prod in place). If the UI let you edit the live prompt directly, dev-intent (safe iteration) and user-flow (fearless tweaking) would diverge → the label indirection (F2) keeps them aligned.
- **"Add a question"** *intends* trustworthy scoring → UI forces a **typed expected answer + dry-run preview**, so a user can't add an unscored/under-specified question that silently reads as "wrong."
- **"Set up an experiment"** *intends* apples-to-apples → UI pins **Dataset version + prompt version + model** into the immutable Run, so a later prompt edit can't retroactively change what a past Run meant.

---

## 7. Phased roadmap

| Phase | Features | Outcome | Risk |
|---|---|---|---|
| **P0 — Foundation** | F1 (run persistence), schema `ai_agent_eval_*`, runner writes DB + JSON | Cross-run comparison + history exist; nothing user-facing yet | Low |
| **P1 — Prompt platform** | F2 (DB prompt registry + editor UI) | Users edit/version/promote prompts in-product; the headline ask | Med (runtime prompt state) |
| **P2 — Eval authoring** | F3 (dataset UI), F4 (evaluator library), F5 (experiment config + compare, MVP) | Users add questions, configure & run experiments, read scoreboards | Med (async runs, LLM-judge) |
| **P3 — Depth & interop** | F6 (agentic/trajectory), F7 (model sweeps), F8 (OTel export), F9 (CI gating), F10 (reports) | Trajectory evals, model sweeps, CI guardrail, external interop | Med |

Each phase is independently shippable and useful. **DP-12:** is P1 (prompts) or P2 (datasets/experiments) the higher priority after P0? **Rec:** **P1 first** — it's the explicit headline ask, the seam is pre-built, and it immediately makes the *existing* eval runs more valuable (you can attribute a score to a prompt version).

---

## 8. Consolidated decision points

| # | Decision | Recommendation |
|---|---|---|
| DP-1 | Backfill historical JSON runs? | Start fresh; import v4 as run #0 |
| DP-2 | Prompt storage: file-default+DB-override vs full DB migration | **Hybrid** (files = versioned default seed, DB = overrides+history) |
| DP-3 | Who can edit prompts | Admin-only writes at P1 |
| DP-4 | Expected-answer model: typed spec vs general assertions | **Both** — typed ground-truth + attachable Evaluators |
| DP-5 | Evaluator interface: build vs vendor OSS | Own the interface, borrow RAGAS/DeepEval definitions (Apache-clean) |
| DP-6 | Run orchestration | Reuse `ai_agent_jobs` + SSE |
| DP-7 | Trajectory eval v1 scope | Tool-call correctness + retrieval selection first |
| DP-8 | Model creds in UI | No — sweep only operator-preconfigured providers |
| DP-9 | OTel export scope | Defer to P3; design F6 spans OTel-shaped now |
| DP-10 | CI gating: blocking vs advisory | Advisory first, opt-in blocking later |
| DP-11 | **RESOLVED** — direction | **Adopt OSS + integrate** (not build native). Persona = operator/analyst tuning the agent, admin-facing |
| DP-12 | P1 = prompts vs datasets | Prompts first (via Phoenix/§11.4) |
| DP-13 | Eval platform as system-of-record vs UI over our tables | **Our `ai_agent_eval_*` = system of record**; tool is a lens (postgres-only + residency) |
| DP-14 | Auth: admin-only vs analyst bridge at launch | Admin-only first (Phoenix behind `require_admin`/proxy) |
| DP-15 | Prompt resolver: Phoenix client vs our 1-table | Prototype Phoenix client; fall back to minimal `ai_agent_prompt` table |
| — | **License guardrail (ELv2)** | Keep Phoenix eval surface operator-facing only, never exposed to external Superset end-customers; else legal review or swap to custom UI over our tables |

---

## 5-REVISED. Decision reframed: adopt OSS + integrate (supersedes §5)

Per stakeholder direction, the choice is no longer "build native vs. buy SaaS" — it is **which OSS platform to self-host and how to integrate it with Superset**. Options A (SaaS) and C (build-from-scratch) are dropped. The live question is **which OSS tool**, and **how much of it we surface inside Superset vs. deep-link to**.

**Candidate OSS tools** (full facts in §11.1 [PENDING VERIFY]):
- **Langfuse** (MIT core) — the most complete OSS *platform*: prompt mgmt (versions+labels), datasets, LLM-judge evals, human annotation, tracing, OTel ingest, web UI. **Tension:** current self-host stack pulls in **ClickHouse** (+ Redis + S3) — at odds with our postgres-only/no-offsite-PV reality.
- **Arize Phoenix** (ELv2) — strong OSS tracing + evals + datasets/experiments + prompt playground; **Postgres/SQLite backend (no ClickHouse)** fits our infra. **Tension:** ELv2 (self-host internal use is fine; can't offer as a managed service or vendor its code into Superset) and historically **thin multi-user auth/RBAC**.
- **DeepEval** (Apache-2.0) — OSS *library* (pytest-style) with the richest agentic/RAG metric set (tool-correctness, task-completion, RAGAS-style). Not a UI/platform; ideal as the **in-process eval engine**.
- **Promptfoo** (MIT) — config/CLI-first; best for **CI gating + red-teaming**, not an interactive team UI.

**The real decision (DP-11 REVISED):** the postgres-only constraint and the ELv2 nuance pull in opposite directions:
- If **postgres-only is hard** → **Phoenix** (or a **DeepEval-engine + lightweight custom UI** hybrid) wins; Langfuse's ClickHouse dependency is disqualifying unless we accept the extra infra.
- If **we can run ClickHouse** (or Langfuse ships a supported postgres-only mode) → **Langfuse** is the most complete platform and the cleanest MIT story.
- **ELv2 acceptability:** self-hosting Phoenix *beside* Superset for internal use is permitted; we must **not bundle/redistribute Phoenix inside the Superset artifact** — integration is via network + reverse-proxy, never code-vendoring. (Confirm with your license policy — some Apache-project-adjacent orgs avoid shipping ELv2 even as a compose dependency.)

**FINAL recommendation (verified §11.1):** a **hybrid** —
1. **DeepEval (Apache-2.0) as the in-process eval/metric engine**, wired into `run_eval_v4.py`. It speaks our data model natively, ships the agentic/RAG metrics we need (tool-correctness, task-completion, RAGAS-style, G-Eval), runs fully offline, and writes scores into **our** `ai_agent_eval_*` Postgres tables (system of record — no egress, no new stateful infra).
2. **Arize Phoenix (ELv2, Postgres-only) as the self-hosted platform UI** for prompt playground, dataset/experiment authoring, run visualization, and trace drill-down — mounted under Superset's origin via its **official subpath reverse-proxy mode**, behind our admin gate. Ingest via **OTLP/OpenInference**.
3. **Promptfoo (MIT) — optional, CI-only** for regression gating + red-teaming (repo-committed YAML). Keep it CI-scoped; its SQLite server is not a Postgres-only shared platform.

**Langfuse is explicitly ruled out** by the postgres-only constraint (mandatory ClickHouse+Redis+S3 in v3). This hybrid keeps *all persistent eval data on our existing Postgres*, everything Apache-2.0/MIT except Phoenix (ELv2, acceptable for internal operator-facing use), and adds **zero new stateful services beyond the Phoenix container** (which itself points at our Postgres).

---

## 11. Integration architecture (OSS-adopt path)

Tool-agnostic where possible; tool-specific bindings marked. This is the "how it bolts onto Superset" layer the original spec deferred.

### 11.1 OSS platform selection — scoring (VERIFIED)

Facts verified against official docs (Jan 2026). Scoring axes weighted by our constraints; the **postgres-only row is decisive**.

| Axis (weight) | Langfuse | Phoenix | DeepEval | Promptfoo |
|---|---|---|---|---|
| License clean for our use (0.20) | **MIT ✓✓** | ELv2 ~ (internal self-host OK; not customer-exposed) | Apache ✓✓ | MIT ✓✓ |
| **Postgres-only fit (0.25)** | **ClickHouse+Redis+S3 required ✗✗** (no PG-only path in v3) | **PG-only ✓✓** (SQLite/PG, no ClickHouse) | in-proc ✓✓ | SQLite, not shared/PG ✗ |
| Prompt mgmt + UI (0.20) | ✓✓ (labels+cache, best) | ✓ (versioning + playground; runtime label API less battle-tested) | ✗ (lib) | ~ (YAML) |
| Datasets/experiments/compare UI (0.15) | ✓✓ | ✓✓ | ~ (needs Confident AI SaaS for UI) | ~ (local UI) |
| Agentic/RAG metrics (0.10) | ~ | ✓ | **✓✓** (tool-correctness, task-completion, RAGAS, G-Eval) | ~ |
| Auth/SSO + embedding (0.10) | OSS SSO ✓ but **RBAC=EE**, no doc'd embed | **OSS SSO+RBAC ✓✓, official subpath reverse-proxy embed ✓✓** | n/a | n/a |

**Verdict — Phoenix wins as the platform UI; DeepEval as the engine; Promptfoo optional in CI.**
- **Langfuse is DISQUALIFIED by the postgres-only constraint** — v3 *mandates* ClickHouse + Redis + S3-blob; the single-container Postgres-only deployment was the now-deprecated v2. Despite the best product + cleanest (MIT) license, it violates constraint (b). Revisit *only* if the infra constraint relaxes.
- **Phoenix is the only full platform that runs Postgres-only** (`PHOENIX_SQL_DATABASE_URL`, no ClickHouse), and it over-delivers on the axes we feared were weak: **OSS auth/RBAC + OAuth2/OIDC SSO** (since v5.0), and **first-class subpath reverse-proxy embedding** (`PHOENIX_HOST_ROOT_PATH` + `PHOENIX_CSRF_TRUSTED_ORIGINS`) — so it mounts under Superset's origin behind our existing auth far more cleanly than Langfuse.
- **ELv2 is acceptable for our use**: internal self-host alongside Superset for operators/analysts is fully permitted; the only prohibited zone is exposing Phoenix's *substantial functionality to external customers as a managed service*. **Guardrail:** keep the eval surface **admin/operator-facing (never surfaced to end-customers of a Superset deployment)** — which matches DP-11 (Superset tuner persona) and the admin-only auth choice (§11.3). If a future deployment wants to expose evals to external tenants, that needs legal review or a swap to a custom UI over our Postgres tables (§11.7 already keeps our data independent, making that swap cheap).

### 11.2 Deployment topology

The OSS tool runs as a **sidecar service** next to the existing FastAPI agent (`:8097`) and Superset, all behind Superset's reverse proxy — the *same* topology that already fronts the agent (`getAgentBaseUrl()`, `/ai-agent` in Docker). No new pattern.

```
Browser ──▶ Superset (Flask, session/JWT) ──▶ reverse-proxy
                                              ├─▶ AI agent (FastAPI :8097)   [existing]
                                              └─▶ eval platform (Langfuse/Phoenix)  [new sidecar]
Agent ──(SDK or OTLP spans)──▶ eval platform          [instrumentation]
Agent ──(prompt fetch by name+label)──▶ eval platform  [prompt registry, §11.4]
Eval results/scores ──▶ ai_agent_eval_* (our Postgres)  [if DeepEval-in-proc]  OR  the tool's own store
```

**Postgres-only guardrail:** if we keep **DeepEval in-process**, the *eval results* live in **our** `ai_agent_eval_*` tables (§3) — no dependency on the tool's storage backend. The sidecar UI then becomes a *viewer/prompt-manager*, not the system of record. This is how we neutralize the ClickHouse tension even if we pick Langfuse for the UI: **our scores are ours; the tool is a lens.** **DP-13 (new):** is the eval platform the *system of record* for runs, or just a UI over our tables? **Rec:** our tables are the system of record (data residency + postgres-only); mirror/push to the tool for visualization.

### 11.3 Auth bridge (the integration's hardest part)

Superset owns identity (session cookie + FAB RBAC). The sidecar tool has its own auth. Three bridging options:

1. **Reverse-proxy trust + header injection** — Superset proxies to the tool only for authorized users (gate at the proxy with a `@has_access`-style check), injecting a trusted identity header; the tool runs in a trusted-network/single-org mode. Simplest; relies on network trust (fits `SECURITY.md`: operator-owned infra boundary).
2. **SSO/OIDC** — if the tool supports OSS SSO [verify per tool], point it at the same IdP as Superset. Cleanest for multi-user + RBAC; depends on the tool's OSS auth capabilities (Phoenix historically thin; Langfuse OSS SSO scope to verify).
3. **Admin-only, no bridge** — expose the tool **only to Superset Admins** behind the existing `require_admin`/FAB `is_admin()` gate (exactly like the AI Agent Usage page). Zero auth-bridge work; acceptable because per `SECURITY.md` Admin is the trusted operational principal and prompt/eval editing is an operational capability.

**Rec:** **start with (3) admin-only** (matches the shipped admin-surface pattern, zero bridge risk, correct trust boundary), graduate to (1) or (2) if non-admin personas (domain experts adding questions) need in. **DP-14 (new):** admin-only at launch vs. bridge for analyst personas. **Rec:** admin-only first.

### 11.4 Prompt-registry integration (the headline "modify prompts" ask)

The pre-designed seam (`prompts/registry.py` `get_prompt()`) becomes the integration point. Replace the file read with a **tool-backed lookup + file fallback**:

```
get_prompt(name):
    v = eval_platform.get_prompt(name, label="production")   # Langfuse/Phoenix prompt API
    if v is None:                                             # tool down / not yet managed
        v = read file prompts/{name}.md (current behavior)    # git-versioned default, always works
    return strip_leading_metadata(v)
```

- **Files stay the seed/default** (git-reviewed), the tool holds **overrides + versions + labels** — the hybrid from DP-2, now realized via the OSS tool's prompt store instead of new `ai_agent_prompt` tables. *This removes F2's custom prompt tables entirely* if the tool's prompt API is OSS [verify].
- **Caching:** the tool's client-side prompt cache (Langfuse has one) replaces our `lru_cache`; fallback path keeps the file cache. Bust on label change.
- **Editing UX:** users edit/version/promote prompts in the tool's UI (embedded per §11.3) — we build **no prompt editor**. This is the biggest scope reduction from adopting OSS.
- **Risk:** prod now depends on the tool for prompt resolution. *M:* the **file fallback makes it fail-safe** (agent still runs on defaults if the tool is unreachable) + pin prod to a reviewed label, never "latest."
- **Phoenix caveat (verified gap):** Phoenix has prompt versioning + a playground, but its **runtime "fetch prompt by label with client-side caching" API is less battle-tested than Langfuse's** (Langfuse's `get_prompt(name, label)` + 60s stale-while-revalidate cache is the category benchmark — but Langfuse is ruled out on infra). Two mitigations: **(a)** use Phoenix's prompt client if its label+versioning suffices at our load; **(b)** if it proves thin, keep a **minimal `ai_agent_prompt`/`_version` table in our Postgres** (a ~1-table build, the original F2 core) as the runtime resolver and use Phoenix's playground only for *authoring/experimentation*, syncing the promoted version into our table. Either way the file fallback stands. **DP-15 (new):** Phoenix prompt client as runtime resolver vs. our own minimal prompt table fed by Phoenix authoring. **Rec:** prototype Phoenix's client first; fall back to the 1-table resolver if latency/label semantics disappoint.

### 11.5 Agent instrumentation (traces + runs)

- **Traces:** wrap LangGraph nodes to emit spans to the tool. Prefer **OTel/OpenInference** (both Langfuse and Phoenix ingest it [verify]) so instrumentation is tool-portable — a single adapter over the `ai_agent_events` we already record. This is F6/F8 realized via the tool, not built.
- **Eval runs:** the existing `run_eval_v4.py` becomes a thin driver that (a) executes the agent per example (unchanged), (b) scores via **DeepEval metrics + our `seagate_scoring`** (in-proc), (c) writes to `ai_agent_eval_*` (system of record), and (d) **pushes the run to the tool's dataset-run API** for the comparison UI. The crown-jewel capability × config scoreboard stays ours (no OSS tool models it as richly); the tool provides trace drill-down + trend UI.

### 11.6 Data-model mapping (our harness → tool SDK)

| Ours (§3 / current) | Langfuse | Phoenix | DeepEval |
|---|---|---|---|
| Dataset / test_queries.md | Dataset | Dataset | `EvaluationDataset` |
| Example (+ expected dict) | Dataset Item (input/expected) | Example (input/reference) | `LLMTestCase` |
| Experiment run (8-config) | Dataset Run | Experiment | eval run |
| `seagate_scoring.score_result` | custom Score | custom eval | custom `Metric` |
| table-selection P/R/F1 | custom Score | eval | `Metric` (retrieval) |
| LLM-judge | managed evaluator | LLM eval | `GEval` |
| prompt `.md` | Prompt (version+label) | Prompt (version) | n/a |
| `ai_agent_events` spans | trace/observation | span (OpenInference) | n/a |

The mapping is 1:1 because our harness already uses the standard vocabulary — **adoption is a serialization exercise, not a redesign.**

### 11.7 What we still build vs. what the tool gives

| Capability | Source |
|---|---|
| Prompt editing/versioning/labels + UI | **Tool** (§11.4) |
| Run visualization / trend / compare UI | **Tool** |
| Trace drill-down | **Tool** (OTel) |
| Dataset/experiment authoring UI | **Tool** |
| The capability × config diagnostic scoreboard | **Us** (keep; richer than tool defaults) |
| Ground-truth deterministic scorer | **Us** (`seagate_scoring`) |
| Agentic tool-selection metrics | **Us + DeepEval** |
| Auth bridge / FAB menu link / proxy | **Us** (thin, §11.3) |
| `run_eval` driver + push-to-tool | **Us** (thin) |
| Eval-results system of record | **Us** (`ai_agent_eval_*`, postgres-only) |

**Net:** adopting OSS collapses F2 (prompt editor), F3 (dataset UI), F5 (compare UI), F8 (OTel) and much of F1 into **configuration + thin bridges**, leaving us to own only the *diagnostic scoring* that is genuinely our IP. That is the payoff of the OSS-integrate direction.

### 11.8 Revised phasing (OSS-integrate)

| Phase | Work | Outcome |
|---|---|---|
| **P0** | Stand up chosen sidecar (§11.1) admin-only behind proxy; FAB menu link | Tool reachable in-product |
| **P1** | Prompt-registry seam → tool-backed + file fallback (§11.4); manage prompts in tool UI | The "modify prompts" ask, minimal build |
| **P2** | `run_eval` → DeepEval/`seagate_scoring` in-proc + push runs to tool; datasets authored in tool | Users add questions + run/compare experiments |
| **P3** | OTel span export (§11.5); model sweeps; CI gate via Promptfoo/DeepEval | Trajectory + interop + regression gating |

---

## 9. Appendix — research provenance

- **Deep-dives this cycle:** Arize Phoenix/AX (license **ELv2**, verified against repo; adopters Booking/Uber/Duolingo; $/span+$/GB metering), W&B Weave (**Apache-2.0 SDK**, ClickHouse self-host, $0.10/MB ingest; OpenAI/NVIDIA/Snowflake), Humanloop (**defunct** — Anthropic acqui-hire, platform sunset 2025-09-08; strongest prompt-mgmt product, now a cautionary tale). Sources cited in team research notes.
- **Characterized from current knowledge (cutoff Jan 2026):** LangSmith, Braintrust, RAGAS, and honorable mentions. Pricing/adopter specifics for these should be re-verified against live docs before external quotation.
- **OSS integration facts — VERIFIED (Jan 2026, official docs/GitHub):** Langfuse v3 self-host mandates ClickHouse+Redis+S3 (no PG-only path; PG-only was deprecated v2) — MIT core incl. prompt mgmt/datasets/evals; OSS SSO but EE RBAC; OTLP ingest; no documented UI embed. Phoenix ELv2 (internal self-host permitted, not customer-facing managed service); SQLite/**Postgres** backend, no ClickHouse; OSS auth/RBAC + OAuth2/OIDC SSO since v5.0; prompt playground + datasets/experiments + evals in OSS; OTLP/OpenInference ingest; official subpath reverse-proxy embed (`PHOENIX_HOST_ROOT_PATH`). Promptfoo MIT, CLI/CI-first, SQLite (self-host "not recommended for production"). DeepEval Apache-2.0, offline pytest-style lib, 50+ metrics incl. tool-correctness/task-completion/RAGAS/G-Eval; Confident AI SaaS optional. Sources: langfuse.com/self-hosting, /docs/open-source, /self-hosting/license-key; arize.com/docs/phoenix/self-hosting/{configuration,features/authentication}; elastic.co/licensing/elastic-license/faq; github.com/promptfoo/promptfoo; github.com/confident-ai/deepeval.
- **Residual unverified:** Langfuse iframe/embedding (likely deep-link-out only); Phoenix runtime prompt-fetch-by-label maturity at load (see DP-15); exact Phoenix image tag features at deploy time.
- **Codebase grounding:** `evaluation/` (harness), `prompts/registry.py` (prompt seam), `app.py` + `initialization/__init__.py` + `src/pages/AiAgentUsage/` (admin-surface pattern), `persistence/models.py` (`ai_agent_*` schema), `llm/` (client factory + metering).

---

# REVISION 2 (July 2026) — verification refresh, competitive analogues, Project Benchmarks, Scientist agent

Everything below was produced from a fresh research pass (official docs/repos/pricing fetched July 2026) plus a full codebase re-survey. §12 refreshes facts; §13 adds the competitive-analogue evidence the original spec lacked; §14-§15 add the two missing features (F11, F12); §16 upgrades the scoring methodology; §17 consolidates revised decisions and phasing.

---

## 12. Verification refresh (July 2026)

### 12.1 Market events since the Jan 2026 verification

| Event | Impact on this spec |
|---|---|
| **Langfuse acquired by ClickHouse** (Jan 16 2026; part of ClickHouse's $400M raise). All product features confirmed MIT (datasets, experiments, prompt mgmt, annotation queues); only SCIM/audit/retention are EE. | Disqualification **stands** — v3 still mandates ClickHouse+Redis+S3. The acquisition makes a future "Postgres-only Langfuse" *less* likely, not more. |
| **Promptfoo acquired by OpenAI** (announced Mar 2026; $18.4M Series A Jul 2025 prior). | Risk flag on the "Promptfoo in CI" leg (§5-REVISED item 3). Neutrality/roadmap now OpenAI-controlled. **Mitigation:** make DeepEval's pytest gating (`assert_test` + `deepeval test run`) the primary CI mechanism; keep Promptfoo strictly optional. |
| **Braintrust** $80M Series B (ICONIQ, Feb 2026, ~$800M valuation; Notion/Stripe/Zapier/Vercel/Ramp). Control plane (UI+auth) **cannot** be self-hosted even hybrid. | Still out. |
| **LangSmith** — LangChain $125M Series B at $1.25B (Oct 2025). Self-host remains Enterprise-only (license key, K8s+ClickHouse). | Still out. |
| **Weave** — self-managed requires commercial W&B license + ClickHouse operator. **Opik** — Apache-2.0, ~20k stars, but ~10-service stack (ClickHouse+ZooKeeper+MySQL+Redis+MinIO) and OSS ships **zero user management**. | Both still out. |

### 12.2 Confirmed picks (unchanged)

- **Phoenix** re-verified: ELv2 (internal self-host free, full features, air-gap OK; prohibited only as a hosted/managed service to third parties); **single container, SQLite/Postgres only** (`PHOENIX_SQL_DATABASE_URL`); OSS OAuth2/OIDC + RBAC; full REST (OpenAPI-generated Python/TS clients) + GraphQL; OpenInference/OTLP native; ~10.4k stars.
- **DeepEval** re-verified: Apache-2.0, ~16.6k stars, fully offline without Confident AI; G-Eval + DAG metric (decomposed binary micro-judgments) + tool-correctness/task-completion; pytest CI gating documented. Claimed in CI at BCG, AstraZeneca, AXA, Microsoft.
- **Ragas** (new addition to the engine layer): Apache-2.0, ~14.6k stars, ships the exact SQL metrics we need — **`DataCompyScore` (execution result-DataFrame comparison) and `LLMSQLEquivalence`** — plus agent metrics (tool-call accuracy, goal accuracy). Use as metric definitions/reference implementation alongside DeepEval; our comparator (§16) remains our own code.

### 12.3 Newly scored candidate: MLflow 3.x GenAI

Apache-2.0 (~26.8k stars, Linux Foundation), pure Python library + Flask tracking server over SQLAlchemy (SQLite/Postgres) — the lightest self-host and the most architecturally adjacent to Superset. Ships `mlflow.genai.evaluate(data, predict_fn, scorers)`, 50+ scorers incl. guidelines-based judges, versioned Evaluation Datasets, Prompt Registry (immutable versions + aliases + lineage), OTel GenAI-semconv tracing (ingest *and* export). **Why it doesn't displace the picks:** OSS auth is experimental basic-auth only (no RBAC/SSO without a proxy), the human-review Review App and production monitoring are **Databricks-exclusive**, and its generic experiment UI would still need all our NL→SQL-specific surfaces built around it. **Verdict:** treat MLflow as *schema/API inspiration* (its prompt-registry alias model and evaluate() interface) and the Apache-2.0-clean fallback if the ELv2 posture on Phoenix ever hardens. No change to picks.

### 12.4 Standards updates

- **OTel GenAI semconv now includes evaluation results**: the `gen_ai.evaluation.result` event (semconv v1.39.0, merged Aug 2025) with `gen_ai.evaluation.name`, `.score.value`, `.score.label`, `.explanation`. → **Action:** name the columns of `ai_agent_eval_score` to map 1:1 onto these attributes (name, value numeric, label categorical, explanation text). Cheap future-proofing for F8.
- LLM client spans are stable/near-stable; agent spans (`invoke_agent` → `chat`/`execute_tool`) still experimental but converged in practice. OpenInference remains the richer LLM/RAG vocabulary; both ride OTLP, so emitting OTLP keeps us portable either way.
- **Consensus data model re-confirmed** across Langfuse/LangSmith/Braintrust/Phoenix/Opik/Weave/MLflow/OpenAI-Evals — §3's vocabulary is still exactly right. Two refinements worth adopting: (a) **Score object** = name + typed value (numeric|categorical|boolean) + `source` enum (CODE | LLM_JUDGE | HUMAN | API) + comment/rationale — the Langfuse shape, interoperable with all; (b) **dataset versioning with experiments pinned to a dataset version** is now universal (Phoenix versions on every mutation; Langfuse added versioned-dataset experiments Feb 2026) — §3's design rule holds.
- **LLM-judge practice** (for F4's judge layer): binary pass/fail + written critique beats Likert scales (Hamel Husain "critique shadowing"); explicit `evaluation_steps`/rubrics beat judge-invented criteria; a **panel of 3 small judges from disjoint model families beats a single GPT-4-class judge at ~1/7 cost** (PoLL, arXiv:2404.18796); calibrate judges against a small human-labeled set before trusting them; keep the judge's rationale in the score's comment field. And for SQL the doctrine is unambiguous: **execute and compare result sets; judges are diagnostic/tie-break only** (§16).

---

## 13. Competitive analogues — in-product NL→SQL testing (new evidence)

The original spec's landscape (§2) covered *eval platforms*. This section covers the *product category we're actually building in*: NL-to-data products letting users test **their** semantic model. Key finding: **every vendor that shipped in-product evals built the surface natively — none embedded a third-party eval platform's UI.** External platforms (Langfuse etc.) appear only as internal engineering tools (e.g., Wren's OSS eval traces to Langfuse).

### 13.1 The two shipped scored-eval harnesses

**Databricks AI/BI Genie — Benchmarks** (docs.databricks.com/aws/en/genie/benchmarks) — the validated product shape:
- Per-Genie-space **Benchmarks tab**; up to **500 questions per space**.
- Item = question + mode: **Chat** (optional ground-truth **SQL answer**; UC SQL functions usable as gold) or **Agent** (optional free-text **evaluation note** guiding an LLM judge).
- Chat-mode scoring is **data comparison with a published rubric** — *Good:* exact SQL, exact result set, same data different sort order, or numerics rounding to the **same 4 significant digits**. *Bad:* empty result, errors, **extra columns**, mismatched cells. *Manual review needed:* undecidable. (Three-way verdict — adopt this; never force binary.)
- Async runs (navigate away, keep running); **Evaluations tab** = timestamped run history with overall accuracy % and per-question "Model output vs Ground truth" drill-down.
- Feedback loop: instructions + example SQL + **trusted assets** (answers from trusted assets get a "verified" badge); no automated repair loop.

**Wren AI Cloud — Evaluation + AI Advisor** (docs.getwren.ai/cp/guide/evaluation/) — the closest analogue to our whole plan, on our own MDL substrate:
- Test scenario = NL question + **ground-truth SQL** (Wren SQL, with AI "Generate" assist and "Preview data" validation) + AI-generated, author-editable **expected output** (expected answer / tables / conditions).
- Runs produce per-question **Pass/Fail with named score reasons** (e.g., "Column count mismatch: generated is missing 1 column(s)"), SQL diffs, data-preview comparisons, AI thinking steps, and **manual verdict override**.
- **AI Advisor** — the shipped Scientist-agent precedent: analyzes Failed questions → stages suggestions (schema-metadata edits + global/question-matched instructions) → **verifies against the originally failed questions** → **runs full-benchmark regression** → only then "Apply to AI system."
- Runtime levers: Question-SQL pairs (verified answers reused for similar questions) + Instructions (global / question-matching) — mirrors our golden-queries + instructions stores exactly.
- The OSS repo's eval framework (`wren-ai-service/eval`, legacy branch) is curate→predict→eval with execution-based "Accuracy" + LLM judges (`SqlSemanticsJudge` etc.), traced to Langfuse — engineering tool, not product.

**Omni — AI Evals** (docs.omni.co/ai/evals): reusable prompt sets (≤25 questions/set), optional free-text "expected behavior" reference, **fixed built-in LLM judge** returning binary pass/fail + confidence + evidence-anchored rationale with explicit critical-error checks (hallucination, date/off-by-one filters, row-limit, mental-math). Distinctive: runs target a **model branch**, so authors measure a semantic-model change **before promoting to main**; side-by-side run comparison with per-prompt regressed/improved and cost/duration.

### 13.2 Everyone else: levers without a harness

- **Snowflake Cortex Analyst** — **Verified Query Repository**: `verified_queries` in the semantic-model YAML (`name, question, sql, verified_at, verified_by, use_as_onboarding_question`); SQL must use **logical semantic-model names, not physical** — verified queries are semantic-layer artifacts. Used at inference as retrieval/few-shot; API exposes `verified_query_used` in the confidence field; Snowsight **suggests new verified queries from observed usage** (production traffic mined into candidate exemplars/tests). Their engineering blog "Agentic Semantic Model Improvement" describes a multi-agent loop (Model Creation / Relationships / Semantic Model Editor / Custom Instruction Editor / Evaluator agents, two-step validation: column comparison then LLM semantic-equivalence) lifting BIRD-domain EX **57%→78% average** — published research, **not shipped product**.
- **Power BI / Fabric "Prep data for AI"** — verified answers = **trigger phrases** (5-7 rec., max 15/answer) + pinned visual + ≤3 filters, **max 250/model**, verified checkmark + provenance ("How Copilot arrived at this"); AI instructions (10k chars); testing is manual chat-pane iteration; docs explicitly warn Copilot is nondeterministic. No scoring.
- **Looker Conversational Analytics** — "golden queries" as question/query pairs *inside instruction text*; preview pane; thumbs feedback goes to Google; no harness. **ThoughtSpot Spotter** — richest human loop: 4-way failure taxonomy at thumbs-down (incorrect data / lost context / poor viz / incomplete), Coach Spotter token-mapping console, Conversations Liveboard monitoring; no batch eval. **dbt/MetricFlow** — parsing/semantic/data-platform validations (`mf validate-configs`, `dbt sl validate`), CI-friendly, but config-level only; their separate `dbt-llm-sl-bench` repo (execute-and-compare vs gold) found **semantic layer ≥98% vs 84-90% raw text-to-SQL** — evidence for the MDL premise itself. **QuickSight Q** — verified-answers tab, human review only. **Tableau/Zenlytic** — trust markers, no evals.

### 13.3 What this means for us

1. **The product shape is validated three times over** (Genie, Wren, Omni): project-scoped test sets of question+gold-SQL, async scored runs, run history with per-question output-vs-truth drill-down, three-way verdicts.
2. **The differentiator is the closed loop.** Only Wren Cloud ships eval→advisor→regression→apply. Nobody ships it wired to a *reviewable changeset* UX (our coverage-recovery pattern) or with our capability × config diagnostics. Genie has scale but no repair loop; Omni has branches but no SQL ground truth; Snowflake published the method but not the product.
3. **Dual-use is the flywheel every vendor converged on**: verified/golden Q-SQL pairs are simultaneously *eval ground truth* and *retrieval few-shot assets*. We already have the retrieval half shipped (`ai_agent_nl_sql_examples` golden store + recall). F11 must make the two sides of the flywheel one artifact, not two.
4. **Native UI is the category norm** — feeds DP-16 (§17).

---

## 14. F11 — Project Benchmarks (the primary user flow; NEW)

- **What:** a **Benchmarks tab in MDL Lab** (SemanticLayerEditor detail pane, sibling of CoveragePanel/GoldenQueriesPanel): per-project test sets of NL question + typed ground truth; async scored runs against the *current* (or a chosen) state of the project + agent; run history with per-question drill-down. This is the Genie/Wren shape, project-scoped — distinct from the admin-side Experiments surface (F5), which sweeps configs; F11 is "score MY project," F5 is "compare configurations."
- **User intent:** *"I curated this MDL project — is the agent right about my data, and did my last MDL change help?"* This is the headline use case of the request and the daily loop for the MDL-curator persona. It is deliberately **not admin-gated** — it belongs to whoever can edit the project.
- **Data model** (slots into §3): a Benchmark is a **project-scoped Dataset**; item = `{question, answer_spec}` where `answer_spec` is one of:
  - `gold_sql` — ground-truth SQL **written against MDL logical names** (Snowflake's rule; survives physical schema changes and keeps the artifact semantic-layer-native). Authoring assists: "Generate with AI" + "Preview data" (Wren), or "promote from a conversation" (save a correct answer as a benchmark item — the Snowsight suggestion pattern, mineable from `ai_agent_conversations`).
  - `typed expected values` — our existing `{nums (±tolerance) | names+absent | trap | zero}` spec (`seagate_scoring` semantics; our differentiator — Genie/Wren have nothing like trap/zero/absent assertions).
  - `eval_note` — free-text rubric for an LLM judge (Genie Agent-mode / Omni pattern; for open-ended questions with no single gold query).
  Plus per-item: capability tags, `verified_by/verified_at`, `use_as_example` (see flywheel), soft cap ~500 items/project (Genie-validated scale).
- **The flywheel (dual-use with golden queries):** one artifact, two roles. A benchmark item with `gold_sql` and `use_as_example=true` is *also* recallable as a few-shot exemplar (feeds the existing `ai_agent_nl_sql_examples`/golden-query recall); conversely a golden query can be imported as a benchmark item in one click. **Leakage control:** runs execute with example-recall **excluding the item under test** (or with recall off), else the agent is handed the answer — report "with/without exemplar recall" as an explicit run config instead. This subsumes part of the golden-queries-shared-memory spec's scope; reconcile there.
- **Runs:** an F11 run is an `ai_agent_jobs` job (DP-6 unchanged) emitting progress over the project events SSE; per-item execution = the existing `TextToSqlGraph.run()` with the project pinned; results/scores persist to `ai_agent_eval_*` (F1 tables — F11 is their first consumer, ahead of F5). Verdicts are **three-way** (pass / fail / needs-review) per §16, with **manual override** stored as a HUMAN-source score (Wren precedent).
- **UI:** Benchmarks tab → items table (typed answer-spec editor + dry-run preview per DP-4/F3) → "Run all / Run subset" → run banner with live progress → **Evaluations history** (timestamped, overall score + per-capability) → per-question view: agent SQL vs gold SQL diff, result-rows vs expected side-by-side, trace/timeline link, verdict + override. Comparison view: run A vs B joined on item (improved/regressed coloring — Braintrust/Omni pattern), annotated with the MDL version (files checksum) each ran against — the "did my MDL edit help" answer.
- **Authz:** project routes already pass `authorize_semantic_project(...)`; benchmark CRUD/runs ride the same gate. **SQL execution uses the caller's fingerprint-proved connection** (BYO-credentials pattern) — gold SQL runs with the runner's own DB rights, so a benchmark author cannot exfiltrate data beyond what they can already query. Runs record which connection/owner executed them.
- **Pros:** the request's core ask; validated shape (§13.1); nearly all machinery exists (graph run primitive, jobs+SSE, scorer, MDL Lab surface, golden store); non-admin personas get value without touching prompts. **Cons:** the typed answer-spec editor and the diff/compare views are real frontend work; result-set comparator needs the §16 upgrade to be trustworthy on arbitrary user data.
- **Risks / mitigations:** *R:* gold SQL authored against physical tables silently diverges from the MDL → *M:* validate references against project logical names at save (reject or warn). *R:* eval-vs-exemplar leakage inflates scores → *M:* exclusion rule above + run-config transparency. *R:* users run 500 questions × trials and burn tokens → *M:* pre-run cost estimate from `MeteredModelClient` history + confirmable cap (same as F5). *R:* gold SQL is wrong/ambiguous (a top failure bucket in BIRD/Spider audits) → *M:* "needs review" verdict class + F12's "the test is wrong" diagnosis + verified_by provenance.
- **DP-16..DP-18: see §17.**
- **Recommendation: build first after F1 — this replaces F2 as the P1 headline** (see §17 phasing rationale).

## 15. F12 — Scientist agent (NEW)

- **What:** an agent (conversation `kind="scientist"`, reusing the shipped recovery-agent architecture end-to-end) that operates the testing platform for the user: **(a) writes tests** — proposes benchmark items from the MDL, documents, golden queries, and real conversation history (each with draft gold SQL + preview, saved only on user approval); **(b) interprets results** — turns a run (or run-pair) into a findings narrative with statistical honesty (§16): what regressed, which capability, paired deltas with CIs, "this movement is within noise"; **(c) diagnoses failures** using a fixed error taxonomy, each class wired to an MDL fix type: schema-linking → synonyms/descriptions; join-path → relationships; aggregation/GROUP BY → metric definitions; filter/value → sample values/enums; time semantics → time-dimension config; **plus the mandatory verdict "the test is wrong/ambiguous"**; **(d) proposes fixes** — hands its failure analysis to the **MDL Copilot** as a seeded conversation producing a staged, reviewable **changeset artifact** (never auto-applied), exactly like `_run_recovery_job`: coverage report → recovery conversation → changeset → persist-until-dismissed notification → ChangesetReviewPanel diff dialog. F10 (report generation) folds into (b).
- **User intent:** *"Don't make me be the eval scientist — tell me why it failed, what to change, and prove the change helps."*
- **Why this shape is right (evidence):** Wren's **AI Advisor** ships this loop today (analyze fails → stage suggestions → verify against fails → **full regression** → apply) — the regression-before-apply discipline is the part to copy verbatim. Snowflake's agentic semantic-model improvement validated the method (BIRD domains EX 57%→78%) with the same two-step evaluator (columns, then LLM equivalence). GEPA (arXiv:2507.19457) shows *reflective* failure analysis beats brute-force search at ~35× fewer rollouts — the Scientist is GEPA's reflect-and-mutate loop with "MDL patch" replacing "prompt patch." And per §13.2, nobody in the category ships this wired to a reviewable-changeset UX — it's the differentiator.
- **Tools (extends `MdlToolset` pattern):** `list_benchmark_items / read_run / read_result(item)` (needs F1+F11), `propose_benchmark_item`, `diagnose_failure` (structured taxonomy output), `compare_runs` (paired stats), `read_document / find_tables / get_physical_schema` (existing), `handoff_to_copilot(failure_report)` → seeds the copilot conversation; **no direct MDL mutation tools** — all changes go through the Copilot changeset gate.
- **Verification loop (the Wren discipline):** after a Copilot changeset from a Scientist handoff is applied, the platform offers (later: auto-runs, flag-gated) a **verification run** — failed items first, then full benchmark — and the comparison annotates the changeset with measured effect. A proposal is only ever *validated* by re-measurement, never by assertion.
- **Pros:** converts the platform from dashboard to closed loop; ~80% of the architecture is shipped (recovery agent, copilot toolset, changeset review, jobs/SSE, conversations); consumes exactly what F1/F11 produce. **Cons:** LLM cost (an analysis pass over N failures + copilot session + verification run); depends on F1+F11 existing; diagnosis quality needs the trace/timeline data to be complete per-run.
- **Risks / mitigations:** *R:* Scientist chases noise (proposes MDL churn off a within-variance delta) → *M:* the statistical gate in §16 is a **hard precondition** for `handoff_to_copilot` (paired delta must clear the CI), and prompts instruct "insufficient evidence" as a first-class conclusion. *R:* runaway cost → *M:* user-triggered at launch (a "Analyze this run" button on a completed run), budget cap, `MeteredModelClient` metering; auto-run mode ships later behind a flag (mirror `wren_coverage_recovery_enabled`, off by default). *R:* bad proposed tests pollute the benchmark → *M:* proposals land as drafts requiring approval + dry-run preview (F3/F11 rule); provenance records `proposed_by=scientist`. *R:* self-review bias (agent judging the agent) → *M:* diagnosis uses a separately configured judge model where available (F4 judge-model separation; PoLL-style small-judge panel is the cheap upgrade path).
- **Phasing:** **v1 (read-only Scientist):** interpret + diagnose + draft tests — no Copilot handoff. **v2:** `handoff_to_copilot` + verification run + changeset annotation. **v3:** auto-run after benchmark completion, flag-gated off by default (recovery-agent parity).
- **Recommendation:** P3, immediately after F11 has produced real run data; v1 is small (a system prompt + read tools + the run schema).

## 16. Scoring & statistics methodology (upgrades F4's comparator; normative)

**Result-set comparator v2** (replaces "scan cells for expected values" as the general-purpose scorer; `seagate_scoring`'s typed semantics remain the *assertion* layer on top):
1. Compare rows as **multisets** (bag semantics); sets only when gold SQL has DISTINCT.
2. **Row order invariant**, unless gold has a top-level ORDER BY — then compare ordered but treat **tie-groups as unordered** (BIRD sorts before compare for `ORDER BY … LIMIT` ties).
3. **Column order/name invariant**: align columns by best value-alignment (BIRD soft-F1 method), never by generated alias.
4. **Numeric tolerance**: match at **4 significant digits** by default (Genie's shipped rule), configurable relative tolerance; canonicalize types before compare (numeric strings→numbers, canonical date format, NULL vs empty-string policy).
5. **Extra columns are a policy knob**: strict mode = fail (Genie); soft mode = partial credit. Emit **both** scores per item: binary **EX** (three-way verdict) *and* **soft-F1** (matched cells TP / extra predicted FP / missing gold FN) so users see "80% right," not just "fail."
6. Run gold and predicted SQL **in the same engine/session** (we do — the caller's connection), with runtime caps; flag **empty-vs-empty matches as low-confidence passes** (classic EX false positive); optionally support a second data snapshot to kill coincidental passes (test-suite-accuracy idea, cheap version).
7. LLM judge only where deterministic comparison can't decide (`eval_note` items, "needs review" escalation, divergence *explanation*) — never as the primary correctness signal for SQL.

**Statistics (normative for every comparison surface, and F12's gate):**
- Default **3 trials** per item (existing pattern); report **pass^k** ("all k trials passed" — reliability, per tau-bench) alongside mean pass-rate, with per-trial visibility. Agents that look fine at pass@1 collapse at pass^k; a BI product sells reliability.
- Run-vs-run banners must show **paired deltas on the shared item set with a CI** (bootstrap over per-item paired differences works at n≈50-200; cluster by table/topic where items are correlated — Anthropic "Adding Error Bars to Evals" recs 1-4). **Never render a bare "72%→78%" without significance**; the UI marks within-noise deltas as such, and F12 refuses to act on them.
- Cache scoring by `(item_id, mdl_checksum, agent_config_hash, prompt_version, data_snapshot_id)`; store the gold result fingerprint once per MDL/data version; offer a fixed smoke-subset for cheap pre-checks (full suite for releases).

## 17. Revised decisions & phasing

### 17.1 New/changed decision points

| # | Decision | Recommendation |
|---|---|---|
| **DP-16** | **Phoenix as the *product* UI (per §11) vs native product surfaces + Phoenix as optional internal-eng sidecar** — re-opened with new evidence: (a) category precedent is unanimous — Genie/Wren/Omni/Power BI all built native, none embed a third-party eval UI; (b) the primary flow (F11) is project-scoped inside MDL Lab and cannot render inside Phoenix, nor can Phoenix drive `TextToSqlGraph`, fingerprint authz, or Copilot changesets; (c) ELv2 constrains any future multi-tenant/customer-facing exposure of the surface; (d) the auth bridge (§11.3) was always the hardest integration piece — spent on a generic UI that knows nothing of capability × config, MDL projects, or changesets. | **Split the roles.** Product surfaces (F11 Benchmarks in MDL Lab; F5 experiments/compare; F2 prompt editor) = **native**, on our tables, per the §6.2 flows. **Phoenix stays as an optional, admin-only, OTLP-fed internal-engineering sidecar** (trace drill-down, playground) — P3, config-gated, never the product dependency. DeepEval/Ragas as in-process engine and the §3 schema/OTel standards **unchanged** — this preserves the "prefer OSS" intent where OSS genuinely saves work (engine, standards, eng observability) and follows the category norm where it doesn't (the user-facing surface). Downstream effects: DP-14 moot for F11 (project authz, not admin); DP-15 resolves to the minimal `ai_agent_prompt`/`_version` tables (§11.4 option b). **Needs sign-off — supersedes §11.4/§11.7-§11.8 scope if accepted.** |
| **DP-17** | F11 answer-spec forms at launch | All three (`gold_sql` + typed values + `eval_note`); `gold_sql` is the primary authored path, typed values the migration target for `seagate_scoring.EXPECTED`, `eval_note` the judge escape hatch. |
| **DP-18** | Benchmark ↔ golden-query relationship | **One artifact, two roles** (`use_as_example` flag + leakage exclusion at run time), not two synced stores. Reconcile with the golden-queries-shared-memory spec (project-scoped, nothing user-scoped — consistent with its directive). |
| **DP-19** | Scientist autonomy at launch | User-triggered analysis on a completed run (v1); Copilot handoff v2; auto-run v3 flag-gated off (recovery parity). Statistical gate is non-negotiable in all versions. |
| **DP-20** | CI gating mechanism (updates §5-REVISED item 3) | **DeepEval pytest** as primary (Apache-2.0, already the engine); Promptfoo optional/at-risk post-OpenAI-acquisition. Gate on paired deltas with tolerance bands (F9 unchanged otherwise). |
| **DP-21** | Comparator implementation | Own code implementing §16 (it must run inside our execution/authz path), using Ragas `DataCompyScore`/BIRD soft-F1 as the reference definitions; DeepEval G-Eval/DAG for the judge layer. |

### 17.2 Revised phasing (supersedes §11.8)

Rationale for the re-order: the request's stated main use case is F11, its persona is the project curator (not admin), and it needs no prompt registry — while F2 without run data can't attribute scores to prompt versions anyway. Prompts move to P2.

| Phase | Work | Outcome |
|---|---|---|
| **P0 — Foundation** | `ai_agent_eval_*` tables (§3 vocabulary, scores OTel-shaped per §12.4); comparator v2 + stats module (§16, pure functions + offline tests); `run_eval_v4.py` writes DB rows alongside JSON | History + trustworthy scoring exist; CLI unchanged |
| **P1 — Project Benchmarks (F11)** | Benchmark CRUD API + MDL Lab Benchmarks tab; typed answer-spec editor + dry-run; async runs (jobs+SSE) via `TextToSqlGraph`; Evaluations history + per-question drill-down + run-vs-run compare; golden-query flywheel (DP-18) | **The headline use case ships**: users test their MDL project in-product |
| **P2 — Prompts & experiments** | F2 prompt registry (minimal own tables per DP-16/DP-15, file-seeded, candidate→promote) + editor; F4 evaluator registry formalized (judge layer: rubric + PoLL-style panel option); F5 admin Experiments surface (config matrix over datasets incl. benchmarks; capability × config scoreboard) | Tuners sweep prompts/configs and attribute scores to prompt versions |
| **P3 — Scientist & depth** | F12 v1→v2 (analysis, diagnosis, test drafting; Copilot handoff + verification runs); F7 model sweeps; F9 CI gating (DP-20); F8 OTLP export + optional Phoenix eng-sidecar; F12 v3 auto-run (flag-gated) | The closed loop: fail → diagnose → propose → review → re-measure |

**Status: PROPOSED, Revision 2.** Sign-off needed on **DP-16** (native product surfaces vs Phoenix-as-product-UI — supersedes parts of §11 if accepted), **DP-17..DP-21**, and the P1 re-order (Benchmarks before prompts). Research provenance: July 2026 pass — official docs/repos for Langfuse (+ClickHouse acquisition), LangSmith, Braintrust, Phoenix, Opik, Weave, MLflow 3.x, DeepEval, Ragas, Promptfoo (+OpenAI acquisition), OpenAI Evals API, OTel GenAI semconv; Genie Benchmarks, Snowflake VQR + agentic-improvement blog, Wren AI Cloud Evaluation/AI Advisor, Omni AI Evals, Power BI Prep-data-for-AI, Looker CA, ThoughtSpot Spotter, dbt/MetricFlow + dbt-llm-sl-bench; BIRD/mini-dev (soft-F1, R-VES), Spider test-suite eval, Spider 2.0, tau-bench (pass^k), GEPA, Anthropic error-bars.
