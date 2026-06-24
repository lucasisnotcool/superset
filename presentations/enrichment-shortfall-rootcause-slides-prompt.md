# Deck Prompt — "Why Enrichment Lost to the Context Dump: Decision → Investigation → Explanation" (3–5 slides)

> **For the slide-deck consultant LLM.** Add a **3–5 slide root-cause section** to the
> Superset × Wren semantic-layer deck. This section answers the question the evaluation
> raises but does not fully resolve: *why did the enriched semantic layer (8.7/15) score
> below a raw context dump (13.7/15)?* It is a **decision → investigation → explanation**
> arc, ending on a roadmap. Everything you need is below; you do **not** need codebase
> access. Do not invent numbers or claims.
>
> **Read these companion prompts first and reuse their facts (do not restate or alter
> them):**
> - [`eval-results-deck-prompt.md`](eval-results-deck-prompt.md) — the numbers
>   (13.7 / 8.7 / 11.7 / 4.3 / 4.0), the win/lose matrix, the **scale** argument (§3), and
>   the **"stacking both hurt / the engine reshapes SQL"** finding (§4b). This section
>   *explains* those results; it must stay consistent with them.
> - [`authoring-and-orchestration-slides-prompt.md`](authoring-and-orchestration-slides-prompt.md)
>   — Slide A already established the **authoring-input difference** (Wren = a human models
>   the MDL; ours = a document drives LLM enrichment over introspected structure) and
>   Slide B already established that **the framework is not the cause.** This section is the
>   *cost side* of that same authoring choice — do not re-explain the input difference, build
>   on it.
> - [`semantic-layer-wren-presentation-brief.md`](semantic-layer-wren-presentation-brief.md)
>   — the Seagate fixture, the diner-slang examples, the two-Wrens decision, the glossary.

**Placement:** immediately **after** the evaluation's win/lose matrix and "stacking both
hurt" slides, and **before** the closing roadmap. It is the bridge from "here is the
surprising result" to "here is exactly why, and how we close it."

**Supplied facts you may rely on (not in the other prompts):**
- Our enrichment is **one LLM pass with a single correction retry by default** — the model
  reads the document and emits the semantic layer in essentially one shot.
- **Wren's parity model assumes a human modeller.** In Wren, a person authors the MDL
  (relationships, calculated fields, metric formulas) in the UI and the engine validates
  it; Wren's "semantic description" step then only *polishes descriptions* over that
  human-authored model. Wren never asks an LLM to translate a prose rule into an engine
  construct — a person already did.
- We author **filtered metrics** (e.g. Golden Yield, True Pass Rate) as **calculated
  fields with a `CASE WHEN` exclusion in the expression**, not as first-class metric
  objects, because that is what our engine build fully validates.

---

## Slide 1 (DECISION) — "We replaced the modeller with a model"

The premise of our enrichment is a deliberate, aggressive bet: **a business document
should become an activatable semantic layer with no human modelling step.** That is Wren
v2's trigger, which Wren v1 cannot do.

State the bet plainly, and state its cost up front (honesty earns the slide):

- **Wren's bet:** a *person* encodes the business rules into the model precisely; the LLM
  only describes what the person built. High precision, high human effort.
- **Our bet:** the *LLM* reads the wiki page and authors the semantics — descriptions,
  aliases, relationships, calculated-field expressions — in one pass, with structure still
  pinned to the real catalog and validated by the engine. **Near-zero human effort, but
  the precision now depends on the LLM.**
- **The consequence to flag now:** we moved *the single highest-precision step in the
  system — turning English business rules into exact engine constructs — from a human to
  one LLM pass.* The evaluation is what happens when that bet meets reality.

Suggested visual: a balance/scale — left pan "human modeller: precise, slow", right pan
"LLM enrichment: instant, approximate" — tipping toward the right with a small "but…" tag.

---

## Slide 2 (INVESTIGATION) — "The gap *is* what the model failed to write down"

Don't re-show the full matrix (it's on the eval slide). Show the **diagnosis**: we mapped
every question the enriched layer missed back to a root cause, and they all share one.

The questions enrichment got wrong are exactly the **markdown-only, prose-defined rules**
the LLM did not compress into MDL:

- **Region rollups** ("Tigerline = SGY + SGT") — a grouping that lives only in the
  glossary; the LLM did not fully encode it as a calculated field/mapping.
- **The Diner-Week calendar** and the **shift remap** — date/bucket logic defined in prose.
- **Multi-hop L4 chains** — region rollup + custom metric + jargon at once.

The context dump answered these because it **re-supplies the entire glossary on every
question** — nothing is lost; the model reasons over the raw rule live. Our enrichment had
**one chance** to translate each rule into MDL, and whatever it missed is simply *gone*
from the semantic-layer path.

Land this sentence: *"The 8.7-vs-13.7 gap is not random. It is, almost exactly, the list
of business rules our one-shot enrichment failed to write into the model — the same rules
a context dump never has to 'remember,' because it brings the whole rulebook every time."*

Suggested visual: two funnels. Context dump: wide doc → wide prompt (lossless, every
query). Enrichment: wide doc → narrow MDL (lossy, once) → retrieval. Mark the lost slice.

---

## Slide 3 (EXPLANATION) — "Three ways one-shot authoring costs us"

The core mental model: **our enrichment is a lossy, one-time compression of a prose
document into an engine model, gated by LLM accuracy.** That single property produces
three distinct, concrete costs. Give each a line and an eval-grounded example.

1. **Coverage loss — missing knowledge.** Whatever the LLM doesn't encode is *permanently
   absent* from the layer (the regions / calendar / shift / multi-hop above). A context
   dump has none of this loss.
2. **Fidelity loss — *worse than missing*.** When the semantic layer is active, the
   engine **rewrites the question into native SQL** (the §4b "stacking hurt" finding). A
   rule the LLM encoded *wrongly* doesn't just fail to help — it makes the engine produce
   a **confidently wrong** query that can *override* the correct one the raw glossary
   would have written. Eval evidence: "on the griddle" 57 → **39**, "Diner Week" 145 →
   **no rows**. An incompletely-taught layer can actively *suppress* knowledge.
3. **Representation limit — metrics as calculated fields** *(state this as a reasoned
   hypothesis, not a measured fact)*. A first-class metric declares the dimensions and
   grain it aggregates at. We instead author a filtered ratio as a **calculated field**
   (`SUM(CASE WHEN…)/SUM(…)`). That works for a flat number — **True Pass Rate went 0/3 →
   3/3**, a real win — but a ratio-of-sums does not cleanly re-aggregate when **sliced by
   a new dimension** the metric wasn't authored at (e.g. "Golden Yield *by region*"). That
   is exactly where the layer stayed weak (**Golden Yield ½, L4 mostly missed**), so the
   metric→calculated-field substitution is a **likely** contributor to the L3/L4 shortfall.

Suggested visual: three stacked bands (Coverage / Fidelity / Representation), each with a
tiny before→after example pulled from the list above. Mark band 3 as "hypothesis".

---

## Slide 4 (RESOLUTION) — "A fixable authoring gap, not a ceiling"

This is the slide that protects the whole story. The honest verdict:

- **The differences on the authoring slides are the *proximate cause* of losing this
  benchmark** — yes. But they describe a **fixable authoring-quality gap on a deliberately
  undersized test**, *not* a structural limit of the semantic-layer approach.
- **Why this is the case *for* the layer, not against it:** the context dump wins *only*
  because the schema is a 7-table toy and the glossary fits in every prompt (eval §3). At
  real warehouse scale you **cannot** paste the whole glossary or all the tables — the
  lossless option disappears and the lossy-but-retrievable layer wins by necessity.
- **How we close the gap (the roadmap — present as planned, not done):**
  1. **Stop doing it in one shot.** Dedicated per-construct passes (relationships, then
     calculated fields, then metrics) and a larger correction budget, instead of one
     omnibus prompt with a single retry.
  2. **Put the human back where Wren keeps them.** Wren assumes a person curates the
     model; our editor already supports review/edit before activation — use it to correct
     the LLM's calculated fields rather than activating the first draft.
  3. **Author real metrics, not workaround calculated fields,** once the engine build
     deep-validates them — restoring correct slice-by-dimension aggregation.
  4. **Finish teaching the missing definitions** (regions, calendar, shifts) so the layer
     stops being *overridden* by — and starts *beating* — the raw document.

Takeaway to land: *"We made an aggressive bet — let an LLM do a human modeller's job from
a wiki page. On a toy schema, a lossless context dump beat our lossy one-shot model, and
the gap is exactly the rules we didn't finish teaching. That is an authoring-quality
roadmap, not a dead end — and at the scale a semantic layer is actually for, the context
dump isn't even on the table."*

---

## Optional 5th slide (only if the deck has room) — "Honest reading of the metric result"

A short credibility coda, pulled from the eval prompt's caveats (§6): some "correct"
answers match the *number* without applying the *rule* (Golden Yield's filter barely moves
the value on this data), so only the enriched layer that *encoded* the rule got it right
*by logic*. This reinforces Slide 3's point that the value is in **teaching rules**, not
matching numbers — and that measuring "logic-correct" matters as much as "number-correct".

---

## Instructions for you, the consultant

- **Produce 3–5 slides** in the order above: Decision → Investigation → Explanation →
  Resolution (+ optional honesty coda). If space is tight, **merge Investigation and
  Explanation** into one slide and keep Decision and Resolution distinct — the
  *decision* (we bet on LLM authoring) and the *resolution* (it's a fixable gap) are the
  two that must survive.
- **This section's job is causal honesty.** It must leave the audience with: *the
  authoring/enrichment design is why we lost the toy benchmark; that is a fixable
  coverage/precision gap, not a flaw in the semantic-layer idea; and at real scale the
  comparison flips.* Do not let it read as either an excuse or a defeat.
- **Stay consistent with the companion prompts.** Reuse the numbers and the three eval
  causes (scale, coverage, reshaping) exactly as written in
  [`eval-results-deck-prompt.md`](eval-results-deck-prompt.md). Build on — don't repeat —
  the authoring-input difference from
  [`authoring-and-orchestration-slides-prompt.md`](authoring-and-orchestration-slides-prompt.md).
- **Label the metric-grain point as a hypothesis.** It is reasoned from how metrics vs.
  calculated fields aggregate, **not** a measured result. Everything else here is grounded
  in the eval or is a stated design fact.
- **Do not claim the fixes are done.** Slide 4's four items are roadmap — "planned",
  "next", not "shipped".
- **Plain business language.** "One-shot translation of the wiki into the model", "the
  engine rewrites the query", "teach the layer the rule" — avoid "MDL grain", "ratio-of-
  sums", "deep validation" unless you gloss them in one clause.
