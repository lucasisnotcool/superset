# Spec: Raw BI-doc grounding + retrieval upgrade for the AI SQL agent

Status: Implemented (2026-07-07) — all phases built and unit-verified; see
`plan_sql_agent_doc_grounding_impl.md` for the as-built checklist + evidence.
Live eval v5 run (A4 ship gate) pending, user-driven.

## 0. Problem statement

The AI SQL agent (`TextToSqlGraph`) grounds only on (a) physical dataset
metadata, (b) the pinned project's MDL semantic layer, (c) golden queries,
(d) shared memory, (e) operator instructions. **Uploaded BI documents never
reach it** — document RAG (`semantic_layer/document_retriever.py`,
`DocumentChunkIndex`) is consumed only by the MDL Copilot
(`copilot/service.py:51`, `copilot/tools.py:1295-1340`) and the
enrichment/onboarding path (`integrations/wren/llm_client.py:178-285`).
Documents influence SQL only through whatever knowledge survived doc→MDL
authoring.

Eval v4 (the authoritative 8-config onboard×grounding matrix, 30 Q × 3 trials,
`evaluation/RESULTS_v4.md`, raw `results/seagate_multi_v4/scoreboard.json`)
shows this costs us ~12/30 points: `wren_bi_context` (layer + raw doc) scores
**22.0/30** vs `context_dump` alone **13.67** vs bare `wren_bi` **8.67–9.33**.

## 1. Verified experimental findings (what the proposal must explain)

Claims as commonly stated, vs what v4 actually shows:

- **"wren_bi better for pinpoint details & accuracy, esp. time-related" →
  PARTIALLY SUPPORTED.** Time-related: SUPPORTED — temporal capability:
  `context_dump` 0.0/5 vs `wren_bi` 1.0/5; Q22 (non-standard fiscal calendar)
  layer 3/3 vs raw doc 0/3. The doc *states* the fiscal rules; the agent only
  applies them when the layer surfaces structured date columns. Pinpoint/
  overall: REFUTED — `context_dump` beats bare `wren_bi` on `metric` (4.0 vs
  ≤1.33), `slang` (5.67 vs ≤4.67), and total (13.67 vs ~9).
- **"context dump better on multi-table / multi-schema" → SUPPORTED (vs the
  bare layer).** `xschema2` 4.33 vs 1.67, `xschema3` 0.67 vs 0.0, `bridge`
  0.67 vs 0.0, `multihop` 2.0 vs 0.33. But the *overall* winner on every one
  of these categories is `wren_bi_context` (e.g. xschema2 9.0–9.33). (v2's
  opposite single-trial signal on Q16–Q18 is superseded noise,
  `RESULTS_v2.md:196`.)
- **Root cause of the layer's weakness is SURFACING, not quality**
  (`RESULTS_v4.md` §2): enriched descriptions/metrics largely never reach the
  retrieved context (`wren_bi − wren_base` only +2; Q27's freshly-authored
  metric absent from retrievable MDL, 0–2/3 vs 3/3 with the raw doc present).
- **Extra context degrades abstention** (§4): Q12 distractor trap held 3/3 by
  baselines but only 1/3 by `wren_bi_context` — grounding induces over-eager
  joins into traps.
- Related surfacing gaps already tracked: views invisible to retrieval
  (RESULTS_v3 #1, R2 partially fixed), cross-schema golden recall (R1 FIXED,
  Q16/Q17 0/3→3/3), paraphrase-recall gap (§7.2), temporal weakest capability
  overall (best config ~2.3–2.7/5).

**Interpretation.** The eval does NOT say "docs beat the semantic layer"; it
says (1) the two channels are complementary — structured layer carries
executable semantics (calendars, date columns), raw prose carries business
definitions and join narratives; and (2) our retrieval pipeline drops most of
the layer's authored knowledge on the floor. The winning configuration in
every category combines both.

## 2. Current context assembly (entrypoints & touchpoints)

- Route `POST /agent/query` → `app.py:986-1002` → `TextToSqlGraph.run`;
  node order `load_context → load_wren_context → draft_sql → dry_plan →
  plan_semantic_sql → validate → repair/execute` (`graph.py:404-439`).
- LLM payload (`graph.py:1183-1196`): question, database, `datasets`
  (keyword-ranked physical metadata, 6000-token budget,
  `semantic_layer/retrieval.py:40+`, `config.py:151`), `wren_context`
  (merged: `fetch_context` keyword-ranked MDL `llm_client.py:101-153` +
  `retrieve_mdl_context` top-k via `schema_retriever.py:830-883` + full-project
  join-closure source; unified/capped by `runtime.py:105-148`,
  `wren_max_context_items=40`), `recalled_examples` (golden-first,
  `golden_queries.py:265-347`, then memory), `instructions`.
- Retriever seam already pluggable: `KeywordRetriever` (default) /
  `EmbeddingRetriever` / `LanceDbRetriever` / `PgVectorRetriever`
  (`schema_retriever.py`, `create_retriever` :710-726, `wren_retriever`
  config default `keyword`, `config.py:306`).
- System prompt `prompts/text_to_sql.md` templates sections: recalled
  examples, semantic layer, complexity, prior failures, instructions.
- Documents are DB-scoped by `database_uri_fingerprint` (never user-scoped —
  standing directive), stored/indexed via `documents.py` +
  `DocumentChunkIndex`.

## 3. External evidence (2025–2026 SOTA, condensed)

Full sourced report retained in §9. Load-bearing points:

- **Production consensus (Snowflake Cortex Analyst, Databricks Genie,
  ThoughtSpot, WrenAI, dbt/Cube):** generation routes *through* the semantic
  layer; unstructured docs are compiled into it offline (descriptions,
  instructions, verified queries) and any query-time doc channel is narrow,
  budgeted, and advisory. Independent KG study: raw schema 16.7% vs
  structured/ontology 54.2% (arXiv 2311.07509). Doc-RAG oversupply measurably
  degrades SQL accuracy ("Balancing Content Size in RAG-Text2SQL", 2025).
- **Trust ladder, not fusion-by-vibes:** golden/verified match → near-verbatim
  reuse (labeled); else MDL+instructions → generate; doc snippets advisory;
  raw schema only for coverage gaps; coverage gap → clarify/decline, never
  silent fallback. No production system arbitrates layer-vs-doc conflicts at
  runtime; conflicts are curation-time lint errors (Genie best-practices).
- **Schema linking is size-gated:** <~10K tokens → dump everything (pruning
  recall-loss beats distractor cost; arXiv 2408.07702); ~10–50K → recall-first
  retrieval (≥94% column recall, RSL-SQL) + join-closure; >50K → agentic
  exploration (Spider 2.0 top tier; a stock Claude-agent-with-tools scores
  61.2 Lite beating bespoke pipelines). Missing join partner is fatal; an
  extra table only distracts → over-retrieve + closure, never precision-prune.
- **Retrieval plumbing with strong effect sizes:** hybrid BM25+vector+RRF
  (+15–30% recall; exact identifiers are BM25 territory), cross-encoder
  rerank (+33–40% on complex queries, ~120ms), Anthropic contextual retrieval
  (−49–67% retrieval failures on doc chunks).
- **Dimension-value/literal retrieval** is Cortex Analyst's most non-obvious
  component (per-column search over distinct values + rerank) and CHESS's LSH
  equivalent — fixes the wrong-string-literal failure class nothing else
  covers.
- **Candidate diversity + selection** beats retry-on-error by ablation
  (Agentar-Scale-SQL: −3.8 to −4.9 pts removing diverse generators; −0.52
  removing the retry loop).
- **Context rot:** effective reliable context ≈40–80K even on 200K models
  (Chroma); coherent near-duplicate distractors are the worst regime —
  explains the Q12 trap regression.

## 4. Proposal

Design principle: **MDL is the trunk; docs feed it offline; a budgeted
doc-RAG channel covers what enrichment hasn't compiled yet; retrieval is
recall-first with join-closure; provenance-ranked trust ladder in the
prompt.** Cross-schema is the default assumption throughout (DB-scoped
retrieval, never per-schema); nothing user-scoped.

### Phase A — Close the surfacing gap + add the doc channel (the +12)

- **A1. Doc-RAG channel in `TextToSqlGraph`.** New context provider that
  queries the existing `DocumentChunkIndex` (DB-fingerprint scope, all
  schemas) with the question; top-k chunks injected as a new prompt section
  `## Business context (from uploaded documents — advisory)`. Hard char/token
  budget (new `wren_sql_doc_context_budget`, default ~4000 tokens ≈ 16000
  chars; deliberately ≪ the 20000-char enrichment budget), feature-flagged
  (`wren_sql_doc_context_enabled`, default ON in example envs per fork rule;
  new env vars → `.env example`/`.env.example`). Prompt text must state the
  precedence rule: semantic layer wins on conflicts; docs add meaning only,
  never tables/columns (mirrors the existing guard in `text_to_sql.md:28`).
- **A2. Surface enrichment into MDL retrieval.** Audit
  `manifest_to_schema_items` (`schema_retriever.py:121-189`) and the
  `fetch_context` trim path so authored descriptions, metric definitions
  (Q27 class), calendar/date semantics, and views are (a) present in the
  indexed chunks and (b) weighted in ranking. Acceptance: Q27-style
  freshly-authored metric answerable in `wren_bi` (no raw doc) config.
- **A3. Trust-ladder prompt restructure** in `text_to_sql.md`: order =
  golden queries (labeled verified) → semantic layer + instructions → doc
  snippets (advisory) → raw datasets (gap-filler); add explicit abstention
  guidance ("if the needed table/metric is absent from all sections, say so
  rather than improvising") to claw back the Q12 trap regression.
- **A4. Eval v5 gate.** Extend the v4 harness with configs
  `wren_bi_rag` (layer + retrieved doc chunks) vs `wren_bi_context` (layer +
  full doc dump) vs current four; keep the trap questions; add high-distractor
  variant (v4 §3 recommendation). Ship Phase A only if `wren_bi_rag` ≥
  `wren_bi_context` − 1 at a fraction of the tokens, and temporal/metric
  categories don't regress.

### Phase B — Retrieval quality (mechanical, high effect-size)

- **B1. Hybrid retrieval + RRF** for both MDL chunks and doc chunks: keep
  keyword scoring (it is our BM25 stand-in for exact table/column/metric
  names) and fuse with embedding ranks instead of choosing one
  (`create_retriever` gains a `hybrid` mode; default flips from `keyword`).
- **B2. Contextual chunk enrichment for docs** (Anthropic contextual
  retrieval): prepend an LLM-generated situating line per chunk at index
  time. For MDL fragments do it structurally: embed each column/metric with
  model name + description + schema path.
- **B3. Optional cross-encoder rerank** stage over the fused candidate pool
  (config-gated; local bge-reranker class model; skip if latency budget
  disallows).
- **B4. Size-gating.** If the pinned project's full MDL + doc corpus
  serialize under ~10K tokens, skip retrieval and dump both (cache-friendly
  stable prefix); otherwise retrieve per B1–B3. Removes retrieval failure
  modes entirely for small projects (most current deployments).

### Phase C — Agentic retrieval + generation quality (the 2026 shape)

- **C1. Just-in-time tools for the conversational agent**
  (`ConversationGraph`): `search_documents` / `read_document` (reuse Copilot
  tool impls `copilot/tools.py:1295-1340`), `describe_model`,
  `sample_column_values`. Pre-loaded context stays the stable cheap core;
  the agent fetches the rest on demand (hybrid pattern; append-only prefix
  for KV-cache).
- **C2. Dimension-value retrieval channel**: index distinct values of
  low-cardinality string dimension columns (DB-scoped, access-filtered);
  at query time retrieve candidate literals for quoted/entity-like tokens and
  inject alongside the matched column. New failure class covered; expected
  large win on real-data questions.
- **C3. Candidate diversity + selection**: generate 2 candidates (MDL-grounded
  semantic-SQL vs raw-schema-grounded), dry-run both via wren-core, select by
  execution result + pairwise LLM judge. Supersedes reliance on the repair
  loop alone.
- **C4. Curation-time consistency linter** (Copilot-side): flag
  contradictions between uploaded docs, instructions, MDL, and golden queries
  to the curator; runtime never arbitrates.

### Decision points

- **DP1 (A1 budget):** 4000-token doc budget — recommend YES; literature shows
  an inverted-U where oversized doc context degrades accuracy.
- **DP2 (A1 scope):** doc retrieval DB-scoped across all schemas (recommend)
  vs pinned-project-schema-filtered. Cross-schema default says DB-scoped;
  chunk metadata doesn't reliably carry schema anyway.
- **DP3 (B1 default):** flip `wren_retriever` default keyword→hybrid after
  v5 passes (recommend), keep keyword as fallback.
- **DP4 (C3 cost):** 2× generation tokens per query — gate behind a
  "complexity" heuristic (only for multi-table/low-confidence drafts)?
  Recommend yes, gated.
- **DP5:** unify the two token-budget accounting schemes (count-cap vs
  char/4) into one global context budget — recommend fold into Phase B.

### Risks

- R-a: doc chunks reintroduce the Q12 trap regression → A3 abstention text +
  v5 trap questions are the gate.
- R-b: per-request embedding of the question for doc retrieval adds latency →
  reuse the embedder already configured for memory recall; keyword fallback.
- R-c: doc content may name tables the user can't access — doc channel is
  advisory prose only; SQL validity/access is still enforced by the existing
  dataset/MDL gates and Stage-A access filters; audit for leakage of
  restricted *values* in doc text (same exposure as Copilot today).
- R-d: index staleness on doc update — `DocumentChunkIndex` already rebuilds
  per checksum; verify for the new call path.

## 5. Sequencing

A1+A3 (small, immediately testable) → A2 → A4 gate → B1/B4 → B2/B3 → C.
Phase A alone is expected to capture most of the measured +12 headroom at a
fraction of `wren_bi_context`'s token cost.

## 9. Appendix — external source list

BIRD (bird-bench.github.io) · Spider 2.0 (spider2-sql.github.io) · Death of
Schema Linking arXiv:2408.07702 · CHESS arXiv:2405.16755 · ReFoRCE
arXiv:2502.00675 · Agentar-Scale-SQL arXiv:2509.24403 · RSL-SQL
arXiv:2411.00073 · XiYan-SQL arXiv:2411.08599 · CHASE-SQL arXiv:2410.01943 ·
Anthropic "Effective context engineering for AI agents" (2025-09) · Anthropic
"Contextual Retrieval" · Cognition "Don't Build Multi-Agents" · Agentic RAG
survey arXiv:2501.09136 · Fishing for Answers arXiv:2509.04820 · Snowflake
Cortex Analyst engineering blogs (behind-the-scenes; Cortex Search
integration) · Databricks Genie best-practices + trusted assets docs · WrenAI
OSS architecture docs · data.world KG study arXiv:2311.07509 · Chroma
"Context Rot" · NoLiMa arXiv:2502.05167 · Self-Route arXiv:2407.16833 ·
LazyGraphRAG (Microsoft Research) · BIRD metric critique CIDR 2026 p5-jin.
Vendor accuracy magnitudes (Snowflake 90%+, Cube ≈2×) are internal evals —
direction corroborated, magnitudes marketing-adjacent.
