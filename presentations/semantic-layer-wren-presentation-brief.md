# Presentation Briefing — Semantic Layer Research with Wren (Superset AI Agent)

> **Purpose of this document.** This is a *design brief / prompt* for a presentation
> designer (and any LLM-assisted deck-builder) who does **not** have access to the
> codebase. It contains everything needed to assemble a company presentation on the
> semantic-layer research we have done with Wren inside the Superset AI Agent: the
> problem, what we built, how it works, and what the evaluation showed. Everything below
> is grounded in the actual implementation — treat the numbers, names, and the
> before/after evaluation as factual. Build the deck from this; do not invent new claims.

---

## 0. How to use this brief

- **Audience:** internal company audience — mixed technical and product. Assume they know
  what Apache Superset is (a BI / data-visualization platform) but **not** what Wren or a
  "semantic layer" is. Define those early.
- **Tone:** confident but honest. A core part of our story is that we ran a real field
  test, *found that the pipeline silently failed*, diagnosed the root causes, and fixed
  them. That narrative — "we held ourselves to strict parity and proved it" — is more
  credible than a flawless-from-day-one pitch. Keep it.
- **Length target:** ~16–20 slides. A suggested slide-by-slide outline is in
  [Section 9](#9-suggested-slide-by-slide-outline). Sections 1–8 are the content;
  Section 10 is a glossary the designer can pull callouts from.
- **Visual motifs to lean on:** (1) a pipeline diagram with stages, (2) a
  before/after split for the evaluation, (3) the "diner slang → real database column"
  translation as the memorable hook, (4) a "two Wrens" comparison.

---

## 1. The problem we are solving

**Text-to-SQL is easy to demo and hard to trust.** An LLM can turn "show me top sellers"
into SQL against a clean, well-named schema. It falls apart the moment the business
vocabulary diverges from the physical database:

- Real warehouses use cryptic column names (`units_scrapped`, `ticket_type`), and the
  business uses its own slang for them.
- The rules that matter ("Golden Yield is computed *only* over standard tickets") live in
  a wiki page or in someone's head — **not** in the database.
- Join paths between tables are often undocumented, and modern cloud warehouses
  (BigQuery, Snowflake, Redshift) frequently **don't even expose foreign keys**, so the
  model has no way to know how tables relate.

A bare LLM guesses at all of this, and a confident wrong number is worse than no answer.

**The thesis:** put a *semantic layer* between the question and the database — a curated,
machine-readable model of the business (names, synonyms, metrics, relationships, rules) —
and ground the LLM in it. That is what **Wren** provides, and what this research
integrates into Superset.

---

## 2. Background: what is a semantic layer, and what is Wren?

**Semantic layer (one-liner for the deck):** a governed translation layer that maps
business concepts to physical tables/columns, defines reusable metrics and relationships,
and is the single source of truth the AI consults before writing SQL.

**Wren** is an open-source semantic-layer + text-to-SQL stack. Its model definition
language is called **MDL** (Modeling Definition Language) — a JSON document describing
*models* (tables), *columns*, *relationships* (joins with cardinality), *calculated
fields*, *metrics*, and *views*. Its engine, **wren-core**, compiles MDL and does
dialect-aware SQL planning — it is the authority on whether a model is valid.

### The "two Wrens" distinction (important and worth one slide)

Our consultant research established that "Wren" is now **two products**, and we
deliberately built a *hybrid* of the two:

| | **Wren v1 — "GenBI"** | **Wren v2 — "the context layer"** |
| --- | --- | --- |
| Pipelines | Haystack/Hamilton over a Qdrant vector store | Rust `wren-core`, LanceDB memory |
| MDL format | Native **camelCase JSON** | YAML → compiled JSON |
| Doc → semantic layer? | **No** — structure is built by introspection; business terms enter only via descriptions, Instructions, and Question-SQL pairs | **Yes** — ingests unstructured docs into MDL |
| Authority model | Structure is **engine-authoritative**; the LLM never authors tables/columns/types | (looser) |

**Our decision (binding design constraint):** adopt **v2's enrichment trigger** (a
business document drives enrichment — this is our product premise), but mirror **v1's
exact MDL field surface and authority model** (structure comes from the catalog and is
engine-validated; the LLM only authors *semantics* — descriptions, aliases,
relationships, calculated fields, metric expressions). This "strict parity" bar drove
every design choice and is what makes the work credible rather than a toy.

---

## 3. What we built — system overview

The deliverable is a **standalone AI Agent service** that plugs into Superset's SQL Lab
as a chat panel, plus the semantic-layer machinery behind it.

**Architecture in one breath:** SQL Lab chat panel → typed frontend client → agent proxy
→ **FastAPI agent service** → **LangGraph** workflows → pluggable model provider
(Ollama / OpenAI / OpenAI-compatible / Azure OpenAI) → Superset for governed metadata and
SQL execution, with the **Wren semantic layer** grounding everything.

Key properties to put on a slide:

- **Standalone & decoupled.** The agent is a separate service, not baked into Superset
  core — it talks to Superset over its REST API or MCP (Model Context Protocol) tools.
- **User-scoped & governed.** It runs under the logged-in Superset user's session, so it
  can only see and execute what that user is permitted to. SQL safety is enforced in code
  (parsed with `sqlglot`, restricted to a single read-only query, default row limit
  appended) — **not** trusted to the prompt.
- **Provider-neutral.** Swap LLM providers via config; the graph logic doesn't change.
- **Two execution surfaces:** a one-shot "question → SQL" endpoint, and a multi-turn
  conversation with a draft → validate → execute → reflect → retry loop.

---

## 4. The two semantic-layer pipelines (the heart of the talk)

Everything in the semantic-layer work organizes around **two runtime pipelines** and
**four parity pillars**. This is the core technical slide(s).

### The four parity pillars (the design spine)
1. **Engine as the authority** — wren-core validation/planning grounds what the model is
   allowed to produce. The LLM never invents structure.
2. **One persistent vector store, many collections** — schema chunks, NL→SQL example
   pairs, and instructions are all indexed and re-indexed on every deploy.
3. **Single semantic source** — one canonical MDL, no parallel heuristic overlay.
4. **Retrieval grounding everywhere** — retrieved schema feeds *both* enrichment and SQL
   generation.

### Pipeline A — Enrichment (document → activatable semantic model)
A business document (e.g. a BI glossary) becomes a validated MDL manifest. Stages:

1. **Ingest & extract** — retain whole document sections (not a blind truncation),
   selecting the most relevant sections within a budget.
2. **Context assembly** — hand the model a trimmed MDL reference plus the
   **authoritative physical schema** (real table/column names and types) so it cannot
   reference something that doesn't exist.
3. **Generation** — the LLM overlays *semantics only* onto the introspected structure,
   with a correction loop that re-prompts on validation errors.
4. **Apply / merge** — merge column-level, **preserving** existing structure; never drop
   or retype a real column.
5. **Validate** — structural + physical checks, and (in the parity profile)
   **wren-core engine compilation** as the gate.
6. **Index & learn** — re-index the vector store on activation so new semantics
   immediately influence retrieval.

### Pipeline B — Retrieval (question → grounded context → SQL)
A natural-language question becomes grounded SQL. Stages:

1. **Index build** — schema chunks indexed (keyword by default; embedding/persistent
   LanceDB in the production profile).
2. **Retrieve & re-rank** — unified retrieval + table selection (heuristic by default,
   optional LLM re-rank) to pick the relevant model subset.
3. **Few-shot & instructions** — inject recalled NL→SQL example pairs and operator
   **Instructions** (reusable rules) into the prompt.
4. **Generation** — produce SQL grounded in retrieved schema + examples + instructions.
5. **Correction loop** — engine-error-driven SQL repair with dry-plan diagnostics.
6. **Learning** — store confirmed NL→SQL pairs and recall them by semantic similarity,
   closing the loop (durable when a persistent memory store is configured).

**Degrade-closed is the contract everywhere:** if no embedder/engine/live schema is
available, every path falls back to a safe keyword/structural/heuristic mode rather than
failing. A zero-config developer path and a richer production profile coexist.

---

## 5. The native-manifest rebuild (a credibility story worth telling)

Early on, the authoring path was built on the wrong foundation: the LLM hand-wrote MDL as
a **snake_case YAML dialect** that we then translated into wren-core's real camelCase
shape. That translation layer was a second source of truth that drifted and silently
dropped fields — producing unreadable engine errors (`missing field 'type'`, duplicate
models, YAML parse errors on stray colons).

**We reversed it.** The agent now speaks **wren-core's native camelCase JSON end-to-end**
— the LLM returns a *typed object* (not free text), we serialize it, and the thing we
store *is* the thing the engine validates. One vocabulary, zero translation. This made an
entire class of failures **structurally impossible**.

Slide takeaway: *"We deleted our own clever abstraction and adopted the engine's native
format. Fewer moving parts, no drift, no silent data loss."*

---

## 6. Evaluation — the Seagate diner-slang field test

This is the most memorable part of the talk. **Build a whole slide around the hook.**

### The setup
We built a synthetic but realistic fixture: a fictional hard-drive manufacturer,
**"Seagate Manufacturing,"** with 7 related tables (sites, production lines, drive SKUs,
work orders, production events, quality tests, shipments) and a **BI glossary wiki page**
written in deliberately confusing **diner slang**:

- a **"patty"** = a hard disk unit
- **"on the griddle"** = a work order in `BAKING` status
- **"86'd"** = `units_scrapped`
- **"garnish"** = a bracket/hardware defect
- the **"Tigerline region"** = sites `SGY` + `SGT` — a grouping that **exists only in the
  markdown**, with no `region` column anywhere in the database
- **"Golden Yield"**, **"True Pass Rate"**, **"Diner Week"** = custom metrics with precise
  formulas and exclusion rules defined only in prose

### The methodology
A graded question set of **15 queries across 4 difficulty levels**, each with a
**ground-truth answer** computed directly from the data:

- **L1** — jargon only, single table (e.g. *"How many patties got 86'd on 2025-10-30?"* → 6)
- **L2** — joins required, including the markdown-only region mapping
- **L3** — custom derived metrics with exclusion rules (Golden Yield, True Pass Rate)
- **L4** — chained multi-hop: region rollup + custom metric + jargon, all at once

Run every question **before** loading the glossary (the "intuition only" baseline) and
**after** enriching and activating the semantic layer. **The delta is the deliverable.**

### The trap question (a great slide)
**Q12:** *"What is the Golden Yield for Short Order tickets at Scotts Valley West?"* —
Golden Yield is *defined* to exclude short-order tickets, so the correct answer is a
**refusal** ("undefined / not applicable"), not a number. A confident number here means
the model applied the formula mechanically without internalizing the rule. This tests
whether the semantic layer conveys *rules*, not just *names*.

### What the field test found (the honest, valuable part)
The first end-to-end run **failed silently**: instead of 7 enriched models, enrichment
returned a single blob named after the schema with the document's first 500 characters as
a description — and returned **HTTP 200, looking successful.** Every piece of business
knowledge was dropped.

This is the strongest slide in the deck if framed right: *"Our unit tests were green, but
the real operator flow was broken — and it failed in the most dangerous way: silently and
with a success code."*

**Root causes diagnosed (source-backed):**
- Enrichment only read **activated** models, but onboarding wrote **drafts** — so the
  model it was asked to "improve" was effectively empty.
- The prompt was biased to *no-op* (an "improve existing MDL" framing with an
  empty-output escape hatch).
- Grounding was driven by a meaningless placeholder question and could silently drop
  tables.
- A real provider bug surfaced: strict structured-output mode was incompatible with our
  free-form `properties` field, with no fallback — diagnostics we'd just added caught it.

**What we fixed (the remediation):**
- Enrich the *modeled* MDL (drafts included), not just the active MDL.
- A hard precondition guard: an enrichment that can't run returns an **actionable error**,
  never a fake-success blob.
- **Full-scope** schema grounding, independent of any question string.
- A **semantics-overlay prompt** — the LLM overlays descriptions/aliases/relationships/
  calculated-field expressions onto introspected structure and is forbidden from
  authoring physical structure.
- **Bake semantics into the retrieval chunk** — the crucial insight that an enriched
  alias is *invisible* to retrieval unless it's in the indexed text; re-index on
  activation so colloquial terms ("patty", "griddle") actually retrieve the right column.
- Multi-term synonyms routed to the **Instructions** store (Wren has no synonyms field);
  derived/filtered metrics authored as engine-validated **calculated fields** with
  in-expression `CASE WHEN` exclusions.
- Engine-authoritative validation turned on in the parity profile.

### Evaluation outcome (how to state it)
- The structural fixes shipped and are test-verified: the agent test suite stands at
  **428 passing tests** (up from 419) after the remediation pass, lint clean.
- The before/after methodology is defined end-to-end and the failure that blocked it is
  resolved: the silent blob is gone, enrichment now grounds on the onboarded structure,
  and enriched semantics reach retrieval.
- **Be precise and honest on this slide:** the *capability* to flip the L1–L4 baseline
  into ground-truth-correct answers is built and unit-verified; the full end-to-end
  before/after numeric run against a live LLM is the closing validation step (the
  "definition of done" we hold ourselves to). Present it as "the harness and the fixes
  are in place; this is the bar we measure against," not as a finished benchmark score,
  unless a fresh live run is captured before the talk.

---

## 7. Supporting deliverables (one slide, fast)

- **Explainability & Audit UI.** A single, sequential, human-readable timeline of
  *everything* between a user's message and the answer: intent, schema context loaded,
  semantic context retrieved, examples recalled, the SQL draft, semantic→native rewrite,
  dry-plan diagnostics, validation, repairs, execution, final answer — shown in a lightbox
  per message, grouped by retry attempt. It's a pure observability layer (it changes no
  agent behavior) and is implemented and tested (backend + frontend).
- **Instructions subsystem.** Operators author reusable rules (and colloquial synonyms)
  in a UI panel; they're recalled and injected into both SQL generation and enrichment.
- **Learning loop.** Confirmed NL→SQL pairs are stored and recalled by semantic
  similarity, so the agent improves with use (when a durable store is configured).
- **Graph view (in design / early build).** A combined visualization that draws the
  physical database schema and overlays the MDL semantic layer on top — showing coverage
  (modeled vs. not), grounding errors (hallucinated/dropped columns as red nodes), and
  *which tables the agent actually used* to answer a question. Performance-first:
  zero cost until opened, seed-then-expand loading for warehouses with thousands of
  tables. Present this as a designed, partially-built direction, not a finished feature.

---

## 8. Honest status & what's next (a credibility slide, keep it)

**Shipped & test-verified:** native camelCase MDL end-to-end; both pipelines in their
structural form; onboarding → enrich → activate flow with the silent-failure class fixed;
Instructions; the explain/audit timeline; degrade-closed fallbacks; persistent
multi-collection vector store; the evaluation fixture and graded query harness.

**Built but opt-in / config-dependent:** engine-authoritative deep validation, embedding
retrieval + persistent LanceDB, the durable learning loop — all on in the production
profile, off in the zero-config dev default (deliberate).

**Open work toward full parity:** the live end-to-end before/after numeric run; full
"fetcher collapse" to sole reliance on the retriever; relationship-complete deep
validation; persistent document collection for SQL-time retrieval; column-level LLM
selection; the graph view build-out.

Framing line for the deck: *"This is a research POC that we held to a strict-parity bar.
The core is built and tested; the remaining work is hardening and the final live
benchmark, not unknowns."*

---

## 9. Suggested slide-by-slide outline

1. **Title** — "Grounding AI in Meaning: A Semantic Layer for Superset with Wren."
2. **The problem** — text-to-SQL is easy to demo, hard to trust (Section 1).
3. **The hook** — the diner-slang example: "How many patties are on the griddle?" Show a
   bare LLM guessing vs. what the business actually means. (Pull from Section 6.)
4. **What is a semantic layer / what is Wren** (Section 2, top half).
5. **The two Wrens + our parity decision** (Section 2, the table).
6. **System overview** — the architecture diagram (Section 3).
7. **The four parity pillars** (Section 4).
8. **Pipeline A: Enrichment** — document → activatable model (Section 4).
9. **Pipeline B: Retrieval** — question → grounded SQL (Section 4).
10. **The native-manifest rebuild** — "we deleted our own abstraction" (Section 5).
11. **Evaluation setup** — the Seagate fixture + graded L1–L4 queries (Section 6).
12. **The trap question** — Q12 / Golden Yield (Section 6).
13. **What the field test found** — the silent failure (Section 6) — *the standout slide*.
14. **Root causes + remediation** (Section 6).
15. **Evaluation outcome** — honest framing + 428 tests (Section 6).
16. **Supporting deliverables** — explain/audit, instructions, learning loop, graph view
    (Section 7).
17. **Honest status & roadmap** (Section 8).
18. **Closing** — the thesis restated: meaning, governed, engine-validated, measurable.

---

## 10. Glossary & fact sheet (for callouts and the appendix)

- **Superset** — open-source BI / data-visualization platform; the host product. SQL Lab
  is its SQL editor, where the agent's chat panel lives.
- **AI Agent** — standalone FastAPI service using LangGraph; pluggable LLM providers
  (Ollama, OpenAI, OpenAI-compatible gateways, Azure OpenAI).
- **Wren / wren-core** — open-source semantic layer; `wren-core` is its Rust engine and the
  authority on MDL validity (we validate against wren-core 0.7.x).
- **MDL** (Modeling Definition Language) — Wren's JSON model: models, columns,
  relationships (with cardinality `ONE_TO_ONE` / `ONE_TO_MANY` / `MANY_TO_ONE` /
  `MANY_TO_MANY`), calculated fields, metrics, views. We use the **native camelCase JSON**
  shape end-to-end.
- **Enrichment** — turning a business document into semantic-layer model content.
- **Retrieval** — turning a question into grounded context + SQL.
- **Instructions** — reusable operator-authored rules (and the correct home for
  multi-term synonyms); injected into prompts.
- **Onboarding** — bootstrapping the model structure deterministically from the catalog
  (introspection-authoritative); the LLM only fills semantics.
- **Degrade-closed** — every smart path has a safe fallback; absence of an embedder/engine
  never causes a failure, only reduced quality.
- **MCP** (Model Context Protocol) — one of the transports the agent uses to call Superset
  tools.

**Evaluation fast facts:**
- Fixture: **Seagate Manufacturing**, 7 related tables, a diner-slang BI glossary.
- **15 graded questions**, 4 levels (L1 jargon → L4 chained multi-hop), each with a
  ground-truth answer computed from the seeded data.
- One deliberate **trap** (Q12) where the correct answer is a refusal.
- Methodology: same questions **before vs. after** enrichment; the delta is the proof.
- Test suite after remediation: **428 passing** (agent unit tests), lint clean.

**Memorable example translations (great for a visual):**
| Business says | Database means |
| --- | --- |
| "patty" | a hard-disk unit (`units_*`) |
| "on the griddle" | work order `status = BAKING` |
| "86'd" | `units_scrapped` |
| "garnish defect" | bracket/hardware defect |
| "Tigerline region" | sites `SGY` + `SGT` *(exists only in the doc)* |
| "Golden Yield" | `(completed − scrapped − reworked) / completed`, STANDARD tickets only |

---

*Source of truth: this brief is distilled from the `superset_ai_agent` implementation and
its design docs (the Wren enrichment/retrieval study, the native-manifest rebuild, the
explain/audit plan, the graph-view plan, and the Seagate evaluation fixture). All figures
and behaviors reflect the implemented system as of the briefing date.*
