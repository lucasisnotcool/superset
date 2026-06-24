# Deck Prompt — Add the "Wren, Decomposed & Rebuilt to Parity" Section (3 slides)

> **For the slide-deck consultant LLM.** This prompt asks you to add **three new
> slides** to the existing Superset × Wren semantic-layer deck. Read
> [`semantic-layer-wren-presentation-brief.md`](semantic-layer-wren-presentation-brief.md)
> first — it has the problem framing, the "two Wrens" decision, the four parity
> pillars, the two pipelines, and the glossary. This prompt supplies the one angle
> that brief does **not** spell out as its own section: **how we took Wren apart into
> its constituent components and re-built each one (or reused it) to parity inside the
> Superset AI Agent.** Everything you need is below; you do **not** need codebase
> access. Do not invent components, names, or claims beyond this document and the brief.

---

## Where these slides go

Insert this 3-slide mini-section **right after the "two Wrens + our parity decision"
slide and before "System overview"** (i.e. between outline slides 5 and 6 in the
brief's Section 9). It answers the question the parity decision raises — *"OK, so what
did you actually rebuild, and how close is it?"* — before the architecture diagram.

Theme of the section: **"We didn't fork Wren or run its stack. We disaggregated it into
its functional roles, kept the one part that must stay authoritative (the engine), and
re-implemented every other role natively inside a single, Superset-governed service —
holding each piece to Wren's own contract."**

---

## Slide 1 — "Anatomy of Wren: what it actually is"

Goal: give the audience a mental model of Wren as a **set of cooperating components**,
not a black box. Wren (in its "GenBI" v1 form — see the brief's two-Wrens table) ships
as a vertically-integrated, multi-container product. Its key components:

| Component | What it does |
|---|---|
| **MDL** (Modeling Definition Language) | The contract: a JSON model of the business — *models* (tables), *columns*, *relationships* (joins with cardinality), *calculated fields*, *metrics*, *views*. |
| **wren-core / wren-engine** | The **authority**: a Rust/Java engine that compiles MDL, resolves relationships / calculated fields / metrics, and does dialect-aware SQL planning. It decides whether a model is valid. |
| **Wren AI Service** | The **orchestrator**: a set of pipelines that index knowledge, retrieve the relevant slice for a question, generate SQL, correct it on error, and generate semantic descriptions. |
| **Vector store (Qdrant)** | The **memory**: a persistent store with several *collections* — schema chunks, NL→SQL example pairs, instructions, table descriptions — re-indexed every time the model is deployed. |
| **wren-ui** | The **authoring surface**: a UI to connect a data source, model the schema, and write reusable rules ("Instructions"). |
| **LLM gateway** | A provider abstraction so any LLM can drive the pipelines. |

One-line takeaway for the slide: *"Wren is five roles working together — a **contract**
(MDL), an **authority** (the engine), an **orchestrator** (the pipelines), a **memory**
(the vector store), and an **authoring surface** (the UI). To integrate it, you have to
account for all five."*

Suggested visual: five labelled blocks (Contract / Authority / Orchestrator / Memory /
Authoring) feeding a central "question → grounded SQL" arrow.

---

## Slide 2 — "What we replicated, and how close to parity"

Goal: show the **decomposition** — each Wren constituent mapped to what we did with it,
with an honest parity status. The strategic point: **we reused the one component that
must be authoritative (the engine) and rebuilt the rest natively**, so the agent runs
without Wren's Docker / Qdrant / Java service while still speaking Wren's contract.

Use three buckets (this is the key idea — colour-code them):

- **REUSED** — we embed Wren's real component as a library; we did not reimplement it.
- **REBUILT to parity** — we re-implemented the role natively, matching Wren's contract.
- **SUBSTITUTED** — we swapped the underlying infrastructure for an equivalent.

| Wren constituent | Our equivalent in the Superset AI Agent | Verdict |
|---|---|---|
| **wren-core engine** (MDL compile + SQL-planning authority) | We **embed the real wren-core in-process** (via its Python binding) as the validation authority | **REUSED** — the authority is Wren's own engine, not a clone |
| **MDL format** (the contract) | Native **camelCase JSON end-to-end**; the LLM returns a *typed object*, we serialize it, and the stored thing **is** what the engine validates | **REBUILT to parity** |
| **db_schema indexing + retrieval** | A schema retriever (keyword by default; embedding / persistent vector index in the prod profile); enriched **descriptions and aliases are baked into the indexed chunk** so they actually influence retrieval | **REBUILT to parity** |
| **NL→SQL example pairs** (few-shot) | A memory store that recalls confirmed question→SQL pairs by semantic similarity | **REBUILT to parity** |
| **Instructions** (reusable operator rules) | An Instructions subsystem with a UI panel; injected into both SQL generation and enrichment | **REBUILT to parity** |
| **SQL generation + correction** | A draft → validate → **engine-error-driven repair** loop with dry-plan diagnostics | **REBUILT to parity** |
| **Semantic description generation** | Extended into **document-driven enrichment**: the LLM overlays descriptions / aliases / relationships / calculated-field expressions onto introspected structure | **REBUILT + extended** |
| **Relationship recommendation** | The enrichment pass emits join relationships from the document (Wren's cloud data sources, and ours, often expose **no foreign keys**, so neither side can seed them from the catalog) | **PARTIAL — honest gap** |
| **Vector store (Qdrant, Docker)** | A persistent embedded vector store (and an in-memory default) holding the **same named collections** (schema, example pairs, instructions) | **SUBSTITUTED** |
| **Pipeline orchestration (Haystack/Hamilton)** | **LangGraph** workflows | **SUBSTITUTED** |
| **wren-ui modeling** | A React semantic-layer editor + **deterministic catalog onboarding** (structure comes from introspection, exactly as Wren does it) | **REBUILT** |
| **LLM gateway** | Pluggable providers (Ollama / OpenAI / OpenAI-compatible / Azure) with a structured-output fallback chain | **REBUILT** |
| **Docker stack packaging** | A single standalone service — **no separate Qdrant / Java / UI containers** | **SUBSTITUTED** |

Takeaway line: *"One component we reuse as-is — the engine, because the authority must be
real. Everything else we rebuilt to Wren's own contract or swapped for a lighter
equivalent. The result speaks Wren's MDL natively and validates against Wren's engine,
but ships as one Superset-governed service instead of a five-container stack."*

Suggested visual: the same five blocks from Slide 1, recoloured by REUSED / REBUILT /
SUBSTITUTED, with one honest amber "partial" flag on relationship recommendation.

---

## Slide 3 — "How Wren does it vs. how we did it"

Goal: a crisp **mechanism-by-mechanism** side-by-side. This is where the "blew it apart
and put it back together differently" story lands. Keep it to the rows below — they are
all factual.

| Aspect | How **Wren** does it | How **we** did it |
|---|---|---|
| **The engine** | A separate Rust/Java service you run alongside | **Embedded in-process** as a library — same engine, no extra service |
| **Orchestration** | Haystack / Hamilton pipeline graphs | **LangGraph** state machines inside the agent |
| **Knowledge store** | Qdrant, multi-collection, in Docker | Embedded persistent vector store (or in-memory) — **same collections** |
| **Authoring** | wren-ui (GraphQL modeller) | A React editor **plus the LLM returning a typed MDL object** + deterministic onboarding from the catalog |
| **Enrichment input** | Operates over an **already-modeled** MDL; no document ingestion | A **business document drives** enrichment (Wren v2's idea) — but structure still comes from introspection, never the LLM (Wren v1's rule) |
| **Synonyms** | No "synonyms" field; a single alias/display-name on the column **plus** Instructions, baked into the schema chunk | **Identical**: alias / display-name on the column, multi-term synonyms in Instructions, all baked into the retrieval chunk so they actually match the question |
| **Filtered metrics** | Metric definitions and calculated fields | **Calculated fields with the exclusion rule written into the expression** (`CASE WHEN …`), because those are what the engine fully validates on our build |
| **Keeping it current** | Re-index the vector store on every model deploy | Re-index on **activation**, triggered by a content checksum |
| **When infra is missing** | (production-grade, always on) | **Degrade-closed**: with no embedder or engine available, every path falls back to a safe keyword / structural / in-memory mode rather than failing |

Closing takeaway for the section: *"Same contract, same authority, same retrieval idea —
re-assembled as one governed service that runs inside Superset, with the parts that
needed to stay authoritative kept real and the rest rebuilt to match."*

Suggested visual: a two-column "Wren | Ours" diagram with a connecting line per row;
green where behavior is identical (synonyms, enrichment authority), blue where the
mechanism differs but the outcome matches.

---

## Instructions for you, the consultant

- **Produce exactly 3 slides** in the order above (Anatomy → What we replicated →
  How Wren vs. how we). They are a self-contained mini-section; don't merge them into
  the pipeline slides — they answer a *different* question (component decomposition, not
  data flow).
- **Match the deck's existing visual style and tone** (confident but honest; see the
  brief). The honesty hook here is the **REUSED vs. REBUILT vs. SUBSTITUTED** framing and
  the single amber "partial" on relationship recommendation — don't hide it.
- **Reuse the brief's facts; don't restate the whole pipeline.** The two pipelines, the
  four parity pillars, the native-manifest rebuild, and the eval already have slides.
  These three slides are the **bridge** from "we chose strict parity" to "here's the
  component-level proof."
- **Plain business language.** Prefer "the engine", "the vector store", "the authoring
  UI", "business rules", "example questions" over internal jargon. It is fine to name
  **MDL**, **wren-core**, **Qdrant**, **LangGraph**, and **Instructions** once with a
  one-line gloss (the brief's glossary has them), but lead with the role, not the name.
- **Do not invent numbers or new components.** If you want a figure, pull it from the
  brief or the eval-results prompt
  ([`eval-results-deck-prompt.md`](eval-results-deck-prompt.md)); otherwise stay
  qualitative. The only status nuances you may state are the ones written here:
  engine **reused**, most roles **rebuilt to parity**, infrastructure **substituted**,
  relationship recommendation **partial** (no foreign keys to seed from), metrics
  authored as **calculated fields** because that is what the engine fully validates.
- **One-sentence bridge to the next slide:** end the section by handing off to "System
  overview" — *"That single rebuilt service is what the next slide diagrams."*
