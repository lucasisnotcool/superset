# Deck Prompt — Add the Evaluation Results & Findings

> **For the slide-deck consultant LLM.** This prompt asks you to add an
> **evaluation results** section to the existing Superset × Wren semantic-layer
> deck. Read [`semantic-layer-wren-presentation-brief.md`](semantic-layer-wren-presentation-brief.md)
> first — it is the companion brief with the problem framing, the Seagate
> diner-slang fixture, the "two Wrens" decision, and the pipeline overview. This
> document supplies the **actual measured results** (the brief described the
> evaluation as designed; this is what it produced when run for real). Everything
> you need is below; you do **not** need codebase access. Build 5–7 slides from
> this and weave the findings into the existing evaluation narrative.

---

## 1. What was run (one setup slide)

A controlled, five-way comparison of how much *grounding* improves natural-language→SQL
on the Seagate fixture (7 manufacturing tables; a BI glossary written in deliberately
confusing "diner slang"; 15 graded questions across four difficulty levels L1–L4, each
with a ground-truth answer — see the brief).

The five conditions, each given the **same 15 questions**:

| # | Condition | What the AI agent had to work with |
|---|---|---|
| 1 | **Basic** | The database only — no business context |
| 2 | **Context dump** | The database + the *entire* BI glossary pasted into the prompt |
| 3 | **Wren base** | The database + an onboarded semantic layer (table/column **structure** only) |
| 4 | **Wren + BI** | The database + a semantic layer **enriched** from the BI glossary |
| 5 | **Wren + BI + context** | Both at once — the enriched semantic layer **and** the full glossary in the prompt |

Run conditions to state on the slide: live system, real LLM (**OpenAI GPT-4.1-mini**),
each condition run **3 times** and averaged (the LLM is non-deterministic), answers
graded automatically against ground truth.

---

## 2. Headline result (the money slide)

**Mean questions correct out of 15:**

| Basic | Context dump | Wren base | Wren + BI | Wren + BI + context |
|:---:|:---:|:---:|:---:|:---:|
| 4.3 | **13.7** | 4.0 | **8.7** | 11.7 |

Three things the audience must take away from this slide:

1. **Enrichment roughly doubles the baseline** — 4.3 → 8.7. The semantic layer,
   once it absorbs the business glossary, demonstrably makes the agent more correct.
2. **Raw context-dump wins outright here (13.7/15)** — and the *reason why* is the
   most important strategic point of the whole talk (next slide).
3. **Doing *both* did not help — it slightly hurt (11.7, below context-dump's 13.7).**
   More grounding is not automatically better; the two approaches can fight each other
   (its own slide — §4b).

Suggested visual: a 5-bar chart, bars 2 and 4 highlighted. Annotate bar 4 with
"+100% vs. baseline", bar 2 with "best — but see why", and bar 5 with "both at once →
*worse* than context alone".

---

## 3. The key strategic insight (do not skip — this reframes the win)

**Why did dumping the whole glossary beat the semantic layer?** Because this test
schema is *tiny* — 7 tables, a ~7,000-character glossary. The entire business context
fits in the prompt every single time, so the model never has to *choose* what's
relevant. That is precisely the situation a semantic layer is **not** built for.

Frame it as: *"On a 7-table toy, you can hand the model the whole rulebook every time.
In a real warehouse — hundreds or thousands of tables, a glossary far too large to
paste — you can't. That's where a semantic layer earns its keep: it retrieves the
right slice, enforces the definitions once, and reuses them. This evaluation proves the
enrichment works; it is deliberately too small to test the scaling premise that is the
layer's actual reason to exist."*

This turns "context dump won" from an awkward result into the setup for the real value
proposition. **Make this its own slide.**

---

## 4. Where each approach wins and loses (one matrix slide)

Group the 15 questions into classes and show correctness (out of 3 trials) per class.
This is far more legible than 15 rows and tells the story directly:

| Question class (example) | Basic | Context | Wren base | Wren + BI | + both |
|---|:---:|:---:|:---:|:---:|:---:|
| **Jargon → column** (Q1 "86'd", Q4 "garnish") | partial | ✅ | partial | ✅ | ✅ |
| **Simple joins** (Q5 WARM sites, Q8 drive family) | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Region rollups, markdown-only** (Q6, Q7) | ❌ | ✅ | ❌ | ½ (Q6 ✅, Q7 ❌) | ✅ |
| **Calendar / shift, markdown-only** (Q11 Diner Week) | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Custom metric *rules*** (Q9 Golden Yield, Q10 True Pass Rate) | partial | ✅ | ❌ | Q10 ✅ / Q9 ½ | Q9 ✅ / Q10 ½ |
| **The trap** (Q12 — should refuse) | inconclusive | inconclusive | inconclusive | inconclusive | inconclusive |
| **L4 chained multi-hop** (Q13–Q15) | ❌ | mostly ✅ | ❌ | mostly ❌ | partly ✅ |

The narrative this matrix supports:
- **Enrichment specifically fixed jargon and one custom metric.** "Short order" and
  "garnish" questions went from 0/3 → 3/3 once the enriched **aliases** reached
  retrieval, and the **True Pass Rate** metric (with its tricky exclusion rule) went
  0/3 → 3/3 — the only condition that got that rule right *by logic*.
- **The base layer alone added essentially nothing** (4.0 ≈ 4.3 baseline). Structure
  without business meaning doesn't move the needle. The value is in the *enrichment*,
  not the onboarding.
- **Enrichment's current gaps are concrete and honest:** it did not yet capture the
  markdown-only **region rollups** (fully), the **Diner-Week calendar**, the **shift
  remap**, or the **multi-hop L4 chains** — exactly the unstructured knowledge a raw
  context dump still conveys better today. This is a clean roadmap slide, not a failure.
- **Both-at-once is not the sum of its parts** (the "+ both" column): it recovered some
  things enrichment missed (region To-Go, some L4 chains) but *lost* others the context
  dump alone got right (see §4b). Net: 11.7 — better than the layer alone, worse than
  context alone.

---

## 4b. The counter-intuitive finding: stacking both *hurt* (give it a slide)

The obvious move — "use the semantic layer **and** paste the glossary, surely that's
best" — **scored 11.7/15, below the 13.7 of the glossary alone.** This is one of the
most useful slides in the deck because it is surprising and it sharpens the strategy.

**Why it happens (plain language):** when the semantic layer is active, the agent's
query runs through the layer's **own query engine, which reshapes the SQL**. For a
question the layer *doesn't* fully understand, that reshaping can **override the correct
query the glossary alone would have written**. Concrete cases:
- **"On the griddle" (Q3):** glossary alone → the right column, answer **57** ✅. With
  the layer also on, the engine swapped in a different column → **39** ❌.
- **"Diner Week" (Q11):** glossary alone → **145** ✅. With the layer on, the reshaped
  query returned **no rows** ❌.

**The takeaway to land:** *"More context is not automatically better. An
incompletely-taught semantic layer can actively suppress knowledge the raw document
would have supplied. The path to a near-perfect score is to finish teaching the layer
the missing definitions — regions, calendar, shifts — not to bolt the two approaches
together."* This reinforces the roadmap message rather than undercutting it.

---

## 5. The methodology finding that builds credibility (include it)

We caught a **measurement trap** mid-evaluation and corrected it — share this; it makes
the whole result more trustworthy, not less.

The agent has a **learning loop**: it remembers question→SQL pairs it has answered and
reuses them on later questions in the same database. Run naively, the four experiments
**contaminated each other** — the later conditions silently *recalled the earlier
conditions' answers*, inflating their scores. The smoking gun: the "structure-only"
condition reproduced the context-dump condition's glossary-derived SQL **word-for-word**,
despite never seeing the glossary.

We isolated the experiments (disabled the learning loop) and re-ran everything. The
numbers above are the clean, fair version.

Framing for the audience: *"A naive A/B would have shown the semantic layer looking far
better than it is — because the experiments were teaching each other. Catching that is
the difference between a demo and an evaluation."* Also flag the **product implication**:
in real use, that same memory makes answers history-dependent and is worth governing.

---

## 6. Honest caveats to put on a "how to read this" slide

Keep these visible — they make the talk credible and pre-empt the obvious challenges:

- **Number-correct ≠ logic-correct.** A few "correct" answers match the *number* without
  applying the *rule* (e.g. Golden Yield's "standard tickets only" filter barely changes
  the value on this data, so a wrong method still lands the right number). Only the
  enriched layer actually *encoded* the rule.
- **The schema half-leaks the jargon.** Some column names (`garnish_defect`,
  `units_scrapped`) already hint at the slang, so even the baseline guesses a few L1
  answers. The "jargon" isn't fully hidden.
- **LLM non-determinism** is real (±1–2 / 15 between runs) — hence the 3-run average.
  Individual-question results wobble; the *ordering* (context > both > enriched > base ≈
  basic) is stable. The enrichment step is non-deterministic too — one run produced an
  invalid layer that the system correctly *rejected*; a retry produced a clean one (the
  safety check working as intended, worth a one-liner).
- **The trap question (Q12) was inconclusive** — every condition technically "passed,"
  but mostly by accident (the query returns empty rather than by a reasoned refusal).
  Don't claim the agent "understood" the trap.

---

## 7. The closing takeaways (a summary slide — 4 bullets)

1. **The semantic-layer enrichment works:** absorbing the business glossary roughly
   **doubled** answer correctness (4.3 → 8.7 / 15).
2. **Structure isn't enough; meaning is:** the base layer alone ≈ no layer. The win
   comes from enriching it with business definitions.
3. **Raw context still wins at toy scale** — which is the case *for* the semantic layer
   at real scale, where you can't paste the whole glossary.
4. **More grounding isn't automatically better:** doing both at once (11.7) scored
   *below* the glossary alone (13.7) — an incompletely-taught layer can suppress what
   the raw document already conveyed. The fix is to finish teaching the layer, not to
   stack approaches.
5. **Rigour matters:** we found and removed a cross-experiment contamination effect, so
   these are honest numbers — and the gaps (regions, calendar, multi-hop joins) are a
   concrete roadmap, not a verdict.

---

## 8. Instructions for you, the consultant

- **Add ~5–7 slides** in this order: (1) eval setup, (2) headline bar chart (5 bars),
  (3) the "why context-dump won = the scaling argument" reframe, (4) the win/lose matrix,
  (4b) the counter-intuitive "stacking both *hurt*" slide (§4b), (5) the
  contamination/credibility finding, (6) closing takeaways. Fold the caveats (§6) into a
  small "how to read this" footnote or one light slide.
- **Match the deck's existing visual style** and the brief's tone: confident but honest.
  The found-and-fixed-the-confound story is a strength — give it room.
- **Do not invent numbers or per-question details** beyond what is in this document. If
  you need a figure that isn't here, use a qualitative label ("partial", "✅/❌") rather
  than a fabricated value.
- **Use plain business language.** Avoid jargon like "MDL", "retrieval chunk",
  "monotonic", "ablation". Say "the semantic layer", "the business glossary", "the
  agent remembered past answers", "a fair, isolated test".
- **Keep the through-line** the brief already establishes (meaning, governed,
  engine-validated, measurable) and let these results be the "measurable" proof point.
