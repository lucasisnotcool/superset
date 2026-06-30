# Explain UI — recalled-query provenance

**Goal (user intent):** the Explain dialog must show *clearly where each recalled
query came from*. Today a recalled NL→SQL example shows the question, an optional
**Golden/Verified** tag, and its native SQL. A *learned (runtime memory)* example
shows **no source marker at all** — it is indistinguishable from "nothing", and a
pair that was *learned from a different project/schema on the same database*
(Stage C native-only) is completely invisible. Users cannot tell whether the
draft was steered by a curated, human-verified query or by an auto-captured one,
nor whether that example even belongs to the project they are looking at.

## What exists (source-backed)

- A recalled example is an `NlSqlPair` with `result_meta` (memory_store.py:65).
  Golden queries stamp `result_meta = {"golden": True, "name", "verified"}`
  (golden_queries.py:335). Runtime memory carries the stored row's `result_meta`
  (usually `{}`).
- `_tier_and_present` (memory_store.py:370) strips a not-fully-onboarded pair's
  `semantic_sql` to `""` (Stage C) but leaves **no breadcrumb** that it did so.
- `compact_recalled_examples` (explain.py:69) surfaces only `golden`/`verified`
  from `result_meta`; everything else is dropped before the UI sees it.
- FE `RecalledExamples` (AgentStepDetail.tsx:403) renders one Tag, keyed on
  `example.golden`, green when `verified`. Memory → no tag.

## Provenance taxonomy (the four honest origins)

| Origin | Source | Signal in data | Badge |
|---|---|---|---|
| Curated, human-verified | golden | `golden && verified` | **Verified** (green) |
| Curated, not yet verified | golden | `golden && !verified` | **Golden** (blue) |
| Learned, applies to this project | memory | onboarded in active project | **Learned** (neutral) |
| Learned, from a broader DB context | memory | Stage C stripped (`out_of_scope`) | **Learned · broader** (neutral, tooltip) |

This mirrors industry verified-asset surfacing: Snowflake Cortex Analyst badges
*Verified Query*; Databricks Genie marks *trusted assets*; Vanna distinguishes
*trained* documentation from generated. None of them surface the "learned from a
neighbouring scope" case — that is a Superset-specific honesty win flowing from
F2's cross-project recall.

## Decisions

- **DP-1 — one `source` discriminator, not two booleans.** Replace `golden:bool`
  with `source: "golden" | "memory"`; keep `verified`; add `name` (golden's
  curated name) and `in_scope` (memory). Single source of truth; the FE switches
  on `source`. *Risk:* a historical persisted trace with `golden:true` re-renders
  without the badge (pydantic ignores the unknown field → `source` defaults to
  `memory`). Cosmetic, historical-only; accepted.
- **DP-2 — stamp `out_of_scope` at the strip site.** `_tier_and_present` already
  knows a pair is not fully onboarded when it strips `semantic_sql`; merge
  `{"out_of_scope": True}` into a copy of `result_meta` there so the breadcrumb
  reaches the UI without re-deriving scope downstream.
- **DP-3 — golden name as provenance subtitle.** Surface `result_meta.name` so a
  verified answer reads "Golden query · Top customers by revenue", not just a
  colour. Strong, legible provenance.

## Sequential checklist

- [ ] **T1 — backend breadcrumb.** memory_store `_tier_and_present`: when a pair
  is not fully onboarded, merge `out_of_scope=True` into its `result_meta` on the
  `model_copy`. Unit test: a foreign-schema/non-onboarded survivor carries
  `result_meta["out_of_scope"]`; a fully-onboarded one does not.
- [ ] **T2 — compact passthrough.** explain.py `compact_recalled_examples`: always
  emit `source` ("golden" when `result_meta.golden` else "memory"); for golden
  add `verified` + `name`; for memory add `in_scope = not result_meta.out_of_scope`.
  Unit tests for all four origins.
- [ ] **T3 — schema.** `RecalledExample`: drop `golden`, add
  `source: Literal["golden","memory"] = "memory"`, `name: str|None`,
  `in_scope: bool = True`; keep `verified`. Round-trip test.
- [ ] **T4 — FE types.** api.ts `RecalledExample`: mirror T3.
- [ ] **T5 — FE render.** AgentStepDetail `RecalledExamples`: provenance badge per
  `source`/`verified`/`in_scope` with tooltips; golden `name` subtitle; a header
  info tooltip explaining the badges. Jest tests for each badge + tooltip.
- [ ] **T6 — suite + lint.** pytest agent suite, jest AiAgentPanel, ruff, prettier.

## Risks / gaps

- Provenance is only as honest as `out_of_scope`: it conflates "different schema"
  with "same schema, not onboarded here". Both are "outside this project's
  onboarded tables" — the tooltip says exactly that, so no over-claim.
- No per-example *author* surfaced (memory rows keep `owner_id` as authorship).
  Out of scope by the no-user-scoping directive; could become a future "shared by
  the team" affordance without exposing identity.
- Historical-trace badge regression (DP-1) — cosmetic, accepted.
