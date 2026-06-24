# Deck Prompt — "Authoring & Orchestration: Wren vs Us" (2 slides)

> **For the slide-deck consultant LLM.** Add **two slides** to the Superset × Wren
> semantic-layer deck. Read [`semantic-layer-wren-presentation-brief.md`](semantic-layer-wren-presentation-brief.md)
> (problem, two-Wrens decision, pipelines, glossary), the eval results in
> [`eval-results-deck-prompt.md`](eval-results-deck-prompt.md) (the 13.7 vs 8.7 numbers
> and *why*), and the component decomposition in
> [`wren-decomposition-slides-prompt.md`](wren-decomposition-slides-prompt.md) (the
> "Orchestration: Haystack/Hamilton vs LangGraph" row these slides expand on). Everything
> you need is below; you do **not** need codebase access. Do not invent numbers or claims.
>
> **The single most important instruction:** Slide B must *prevent* the audience from
> concluding "we used the wrong framework, that's why context-dump beat us." That is
> **false**, and the deck must say so plainly. The orchestration choice is not the cause
> of the eval result; scale and enrichment coverage are (both already established in the
> eval prompt).

These two slides go **immediately after** the decomposition section's "How Wren does it
vs. how we did it" slide — they zoom in on the two rows people ask about most: **how the
semantic layer is authored/enriched**, and **the orchestration framework**.

---

## Slide A — "Authoring & enrichment input: the real difference"

The biggest authoring difference between Wren and us is **what drives enrichment** — the
*input modality*, not the output format (the output is the same native MDL on both sides).

| | **Wren v1 ("GenBI")** | **Wren v2 ("context layer")** | **Ours (the hybrid)** |
|---|---|---|---|
| Structure (tables / columns / types) | Built by **introspection** in the wren-ui modeller | Introspection | **Introspection** (deterministic onboarding) — never the LLM |
| Enrichment **input** | The **already-modeled MDL itself** — the LLM only writes descriptions / display-names over it | **Unstructured documents** → MDL | A **business document** → semantics overlaid on the introspected structure |
| Document → semantic layer? | **No** | **Yes** | **Yes** (adopted from v2) |
| Authority | Engine-validated; LLM never authors structure | Looser | Engine-validated; LLM never authors structure (kept from v1) |

What to land on the slide:

- **Wren v1's enrichment input is the model itself.** Its "semantic description"
  step *polishes a hand-modeled layer* with names and descriptions. It has **no path
  from a wiki page to a semantic layer** — business terms must be typed in by a modeller,
  or added as Instructions / example questions.
- **We took v2's trigger and kept v1's rule.** A *business document* drives our
  enrichment (the thing v1 cannot do), but structure still comes from catalog
  introspection and is engine-validated — the LLM only adds *meaning* (descriptions,
  aliases, relationships, calculated-field expressions), never tables or types.
- **Net capability:** our authoring does something neither single Wren version does in
  one step — *turn a business glossary into an activatable, engine-validated layer* —
  without trusting the LLM to invent structure.

Suggested visual: two input arrows into the same "MDL + engine validation" box — Wren's
arrow labelled "a person models it in the UI", ours labelled "a document, overlaid on the
real schema". Same box, different front door.

---

## Slide B — "Orchestration: Haystack vs LangGraph — and what the eval is *really* about"

Two halves. The framework contrast (factual), then the honest correction.

### Half 1 — Two frameworks, two design intents (from public comparisons, 2026)

| | **Haystack** (Wren's choice) | **LangGraph** (our choice) |
|---|---|---|
| Built for | **Retrieval-first** RAG / search / document-QA pipelines | **Agent-first** stateful graphs |
| Model | Modular, mostly **stateless** pipelines (sequential data transformation) | **Stateful** graph: nodes share state, with loops, branching, human-in-the-loop |
| Strengths | First-class hybrid search, **re-rankers**, retrieval components, evaluation | Multi-step reasoning, **retries / correction loops**, tool orchestration, conversation memory |
| Weakness | Less flexible for complex agentic routing | Retrieval is via integrations, not its core specialty |

**Why we chose LangGraph:** our product is an **agent**, not a search pipeline. It runs a
multi-turn conversation and a *draft → validate → repair → reflect* loop, and makes
governed tool calls into Superset. That is exactly LangGraph's wheelhouse (stateful loops,
retries, branching). Wren's Haystack choice optimizes the **retrieval leg**; ours
optimizes the **agentic leg**. Different tool for a deliberately different job.

### Half 2 — Did the framework cause "context-dump (13.7) beat enrichment (8.7)"? **No.**

State this directly on the slide so no one mis-attributes the result:

- **The framework is plumbing.** It decides how components are wired and how state/loops
  work — **not what content reaches the LLM.** Either framework can run the identical
  retrieve → generate → correct steps. Swapping Haystack for LangGraph does not change a
  single answer in this evaluation.
- **The result is explained by causes already in the deck, none of which is the
  framework:**
  1. **Scale** (eval prompt §3): 7 tables and a ~7,000-character glossary fit in the
     prompt *every time*, so a raw context dump trivially wins — the exact situation a
     semantic layer is *not* built for.
  2. **Enrichment coverage** (eval prompt §4): enrichment hadn't yet captured the
     markdown-only knowledge — region rollups, the Diner-Week calendar, shift remaps,
     multi-hop chains. That's an *enrichment-completeness* gap, not an orchestration gap.
  3. **Query reshaping** (eval prompt §4b): when the layer is active, its **query engine
     reshapes the SQL** and can override a query the glossary alone got right — again the
     **engine**, not the framework.
- **One honest nuance worth stating:** Haystack ships **more mature retrieval / re-ranking
  components** out of the box, and our default retrieval is keyword (embedding is opt-in
  in the production profile). At 7-table scale that difference is irrelevant — but at real
  warehouse scale, retrieval quality is where it *would* matter, which is exactly why our
  architecture supports embedding + a persistent vector index in the production profile.

**Takeaway to land:** *"The Haystack→LangGraph swap is a deliberate product choice — we're
building a stateful agent, not a search pipeline — and it is **not** why the context dump
won. That came down to a toy-sized schema and enrichment we haven't finished teaching, both
already on the roadmap. The framework choice is orthogonal to the score."*

Suggested visual: two columns (Haystack = retrieval-first, LangGraph = agent-first) with a
big struck-through arrow from "framework" to "eval score", and three small icons pointing
to the *real* causes (scale, coverage, reshaping).

---

## Instructions for you, the consultant

- **Produce exactly 2 slides** in this order (Authoring input → Orchestration + honesty).
- **Slide B's job is to *defuse* a wrong conclusion**, so give the "No, the framework is
  not the cause" half visual weight at least equal to the framework table. If you only
  have room for one message on Slide B, it is *that*.
- **Stay consistent with the other prompts.** The numbers (13.7 / 8.7 / 11.7), the scale
  argument, and the "stacking both hurt" reshaping finding all come from
  [`eval-results-deck-prompt.md`](eval-results-deck-prompt.md) — reference them, don't
  restate or alter them. The two-Wrens table and "structure is introspected, LLM never
  authors it" come from the brief.
- **Plain business language.** "Retrieval-first pipeline" vs "stateful agent framework"
  is fine; you may name **Haystack** and **LangGraph** once each. Avoid deep jargon
  ("DAG", "re-ranker") unless you gloss it ("re-ranker = a step that re-orders search
  hits by relevance").
- **Do not invent benchmark numbers comparing the two frameworks.** There is no
  head-to-head measurement in our work; the comparison is of *design intent and fit*,
  plus the explicit statement that the framework did not drive the eval result.
- **Do not overclaim our retrieval.** Default retrieval is keyword; embedding + persistent
  index are the production profile (opt-in). Say so if retrieval quality comes up.
