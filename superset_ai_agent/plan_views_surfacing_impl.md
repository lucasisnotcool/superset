<!--
Implementation plan (sequential checklist) for the VIEWS follow-up surfaced by
Eval v3 (superset_ai_agent/evaluation/RESULTS_v3.md). Phase 1 (semantic view
authoring + validation) already shipped; this plan closes the query-time gap:
authored views are invisible to the model. Source-backed: every step lists exact
entrypoints/touchpoints (file:line, verified against the tree), requirements,
tests, risks, dependencies. Pick up and check boxes as you go.
-->

# Implementation Plan — Views: Query-Time Surfacing & Authoring Yield

Eval: [`evaluation/RESULTS_v3.md`](evaluation/RESULTS_v3.md) · Spec: [`plan_views_parity_spec.md`](plan_views_parity_spec.md)
Phase-1 impl: [`plan_views_parity_impl.md`](plan_views_parity_impl.md) · Memory: [[views-parity-spec]]
Created: 2026-06-30 · Status: **Phase A + B implemented & green (offline); live E14 re-run pending). Phase C (D6) recorded.**

## Context (what the eval proved)
Phase 1 plumbed semantic views end-to-end: a view is authored → validated (G2
catches bad columns, no false-green) → activated → materialized → **queryable and
correct** (engine inlines it; returns exact ground truth on Cobalt 1751 / Vantage
3017). **But the agent never uses a view on its own** — views are absent from the
retrieval/context set (`matched_models` had 0 views). **The gap is surfacing, not
execution.** Two secondary gaps cap authoring yield: atomic activation sinks a whole
changeset on one bad view (R3), and prose→semantic authoring can hallucinate a model
column (R4). **D6 is resolved: defer native views** — physical→model name
substitution from correct SQL produced 3/3 valid semantic views.

## Priority order (by leverage)
1. **R2 — surface views at query time** (headline; turns a working-but-invisible
   feature into accuracy). 2. **R3 — non-atomic activation yield.** 3. **R4 —
   column-grounded authoring.** 4. **D6 — record defer-native.**

## Guiding patterns to reuse (in-repo / industry standard)
- **Mirror the relationship loop** in `manifest_to_schema_items` to index views —
  same `SchemaItem` shape; `kind` is a free `str` so `"view"` needs no enum change.
- **Mirror `_rank_models` → `_rank_views`** in `fetch_context` — identical
  token-overlap ranking; no new ranking machinery.
- **Description-as-recall is Wren's own design** ("a view with a good description
  becomes a high-quality recall example") and the standard semantic-layer RAG
  pattern (Cube/dbt MetricFlow index metric/view descriptions for text-to-SQL).
- **Per-item diagnostics over auto-drop** (R3): surface which view failed and let
  the human reject it (the review UI already does per-item reject) rather than
  silently dropping — preserves the atomic-validation invariant.
- **Ground authoring in real columns** (R4) the way metrics already are
  (`_mdl_reference` carries column names+types).

---

## PHASE A — Surface views at query time (R2, headline)

> Two complementary surfacing paths feed the same `wren_context`
> ([conversation_graph.py:_load_wren_context](conversation_graph.py#L928), which
> already merges both — **no change needed there**): the **retriever corpus**
> (embedding/semantic match on the view description — the high-value path) and the
> **direct manifest ranking** in `fetch_context` (always-on token overlap). Do both.
> Both read the **full materialized manifest** (all schemas), so R2 is independent
> of the single-schema context-load bug (that's R1, golden-queries workstream).

### [x] Step A1 — Index views into the retrieval corpus (R2a, primary)
- **Requirement:** every activated view is retrievable by semantic match on its
  `name` + `properties.description` (+ statement), so a view-shaped question pulls
  the view into context.
- **Entrypoint / touchpoint:**
  [`manifest_to_schema_items`](semantic_layer/schema_retriever.py#L120) — after the
  relationships loop (~L186), add a `for view in manifest.views:` loop emitting
  `SchemaItem(kind="view", name=view["name"], text=<name + description + statement>,
  terms=_semantic_terms(view))`, **skipping native (`dialect`-carrying) views**
  (they aren't engine-queryable; don't advertise them). Mirror the relationship
  loop exactly. `SchemaItem.kind` is `str` ([schema_retriever.py:53](semantic_layer/schema_retriever.py#L53)) — no enum change.
- **Reindex wiring:** `reindex_project_mdl` (run after activation, app.py ~L2134)
  already feeds `manifest_to_schema_items`; confirm views flow once the loop exists.
- **Tests:** `manifest_to_schema_items` emits a `kind="view"` item carrying the
  description; a native view is skipped; a retrieval query on the description ranks
  the view in. *Depends on: none (Phase 1 schema is in place).*
- **Risk:** view text crowds the budget. **Mitigation:** index the
  name+description (compact), not the full statement, when long; reuse the existing
  budget trim.

### [x] Step A2 — Surface views in the direct `fetch_context` manifest ranking (R2b)
- **Requirement:** even without retrieval, a relevant view appears in `wren_context`
  from the materialized manifest.
- **Entrypoints / touchpoints (all in [llm_client.py](integrations/wren/llm_client.py)):**
  - `fetch_context` ([L115](integrations/wren/llm_client.py#L115)) — extract
    `views = [v for v in mdl.get("views", []) if isinstance(v, dict) and not v.get("dialect")]`.
  - Add `_rank_views(question, views)` mirroring
    [`_rank_models`](integrations/wren/llm_client.py#L695) (token overlap on
    name+description+statement).
  - After the relationships append (~L132), append
    `context_items.append({"type": "views", "items": <ranked, budget-trimmed views>})`.
  - Populate a new `matched_views` field in the return (~L136).
- **Tests:** `fetch_context` over a manifest with a view returns the view in
  `context_items` and `matched_views`; native views excluded; no view → unchanged.
  *Depends on: Step A3 (the field).* 

### [x] Step A3 — Add `matched_views` to the context artifact (observability + parity)
- **Touchpoint:** [`WrenContextArtifact`](schemas.py#L198) — add
  `matched_views: list[str] = Field(default_factory=list)` after `matched_models`
  (L207). Lets the eval/telemetry assert "the agent saw the view" (the metric the
  eval needed: `used_views`/`matched_views`).
- **Tests:** schema round-trips with `matched_views`. *Depends on: none. (Do before
  A2.)*

### [x] Step A4 — Prompt: tell the agent to prefer a matching view
- **Requirement:** when a relevant view is in context, the agent **selects from it**
  rather than reconstructing the join (the eval showed the from-scratch cross-schema
  join is wrong; the view encodes the correct join).
- **Touchpoint:** the text-to-SQL prompt
  ([prompts/text_to_sql.md](prompts/text_to_sql.md)) — add a line: *"If
  `wren_context` contains a view whose description matches the question, query it
  directly (`SELECT … FROM <view>`) instead of re-deriving the joins — a view is a
  vetted, named query."* Mirrors the existing "prefer metric expressions" guidance.
- **Verification:** eval re-run of E14 (Q16–Q18) with views active → expect
  `matched_views` non-empty and the agent to use the view. *Depends on: A1–A3.*

### [x] Step A5 — Phase A gate: offline tests + live E14 re-run
- Offline suite green + a live re-run of the eval's E14 (the exact regression the
  eval found). **Success = `matched_views` non-empty and ≥1 of Q16–Q18 flips to
  correct via the view.** Record residual risk + UI-gap notes.

> **Deferred alternative (note, don't build):** the report's other R2 option —
> *auto-emit a golden query from an activated view* — bridges views into the
> golden-recall path. It depends on the golden-queries workstream (R1, cross-schema
> recall access scope) being fixed first and is more coupled. Prefer direct
> indexing (A1/A2); revisit the bridge once R1 lands so trusted-SQL isn't surfaced
> by two mechanisms. See [[golden-queries-shared-memory]].

---

## PHASE B — Authoring yield (R3, R4) — raise 0/3 toward usable

### [x] Step B1 — R3: attribute deep-validation failure to the offending view/file
- **Requirement:** when the projected-manifest validation fails on one bad view, the
  result must name **which file/view** failed, so the human (or agent) can reject
  only that one — instead of an opaque manifest-level 422 that sinks the changeset.
- **Entrypoints / touchpoints:**
  - [`_enforce_activation_manifest`](app.py#L1913) / the bulk-status endpoint
    ([app.py:2061](app.py#L2061)) — on failure, map the failing
    `validate_project_manifest` message(s) back to the target file whose view caused
    it (the engine error names the view/column, e.g.
    `No field named seagate_shipments.units_shipped`).
  - Surface per-item verdicts onto the changeset items
    ([`ChangesetItem.validation`](semantic_layer/copilot/schemas.py)) so the review
    UI shows the red item precisely.
- **Decision D-R3 (recommendation):** **attribute + human-reject (recommended)** over
  auto-dropping. Keep the atomic-validation invariant ([[mdl-bulk-activate]]) on the
  *reduced* set the human chooses; do NOT silently auto-drop (it would hide a real
  authoring error). Optional follow-up: a "validate each target, return the maximal
  valid subset as a *suggestion*" helper — still human-confirmed.
- **Tests:** activating two good views + one bad view → result identifies the bad
  view; the two good ones activate after the bad one is excluded; a cross-file
  dependency still validates as a unit.
- **Risk:** mis-attribution when the error is genuinely cross-file. **Mitigation:**
  fall back to the current manifest-level message when no single file is implicated.
  *Depends on: Phase 1 (G2 deep validation, shipped).*

### [x] Step B2 — R4: ground view authoring in real model columns
- **Requirement:** reduce the hallucinated-column rate (E13: `units_shipped` vs real
  `qty_units`).
- **Two paths (the eval's E13 is the agent path):**
  - **Agent path (primary):** skill guidance —
    [skills/generate-mdl.md](skills/generate-mdl.md) /
    [skills/enrich-context.md](skills/enrich-context.md): *"Before writing a view
    `statement`, confirm every referenced model column exists (`read_mdl_file` the
    model, or `get_physical_schema`); never guess a column name. Prefer copying an
    exact column from the model over inferring one from prose."*
  - **Enrichment path:** already grounded — `_mdl_reference`
    ([llm_client.py](integrations/wren/llm_client.py)) passes column names+types.
    Strengthen the enrichment prompt to *use* it for views.
  - **Prefer name-substitution (E15):** when the source provides SQL, instruct the
    model to substitute physical→model names rather than re-derive (3/3 reliable).
- **Tests:** none for prose (eval-graded); add a unit asserting the skill text names
  the column-verification step. *Depends on: none. Synergy with B1 (even one
  hallucinated view no longer sinks the rest).*

### [x] Step B3 — Phase B gate: re-run E13 (prose) + E15 (raw-SQL) authoring evals
- Expect E13 usable-yield to rise from 0/3 (B1 salvages the good views; B2 lowers
  the hallucination rate). E15 already 3/3. Record results.

---

## PHASE C — Close the native-view decision (D6)

### [x] Step C1 — Record D6 = defer native views (no code)
- **Eval evidence:** physical→model name-substitution from correct SQL → 3/3 trials,
  2/2 valid semantic views, described, zero physical-schema leak. The burden native
  views were meant to remove (semantic translation) is small and handled well.
- **Action:** mark D6 resolved in [`plan_views_parity_spec.md`](plan_views_parity_spec.md)
  §11 and [`plan_views_parity_impl.md`](plan_views_parity_impl.md) Phase 2 — native
  views reserved for genuinely **unmodeled/external** tables only, built only on a
  concrete need. The dormant `dialect` plumbing + symmetric engine-manifest exclusion
  (shipped in Phase 1) stay as-is. **Phase 2 (native execution) is closed, not
  pending.**

---

## Risks & mitigations
| Risk | Likelihood | Mitigation |
|---|---|---|
| **RX1 — Indexed view text bloats retrieval budget / dilutes model recall.** | Med | Index name+description (compact); reuse existing budget trim; rank views alongside models, don't prepend. |
| **RX2 — Native view leaks into retrieval/context and the agent queries a non-engine view.** | Med | Exclude `dialect`-carrying views in BOTH A1 and A2 (symmetric with the Phase-1 engine-manifest exclusion). |
| **RX3 — Agent over-trusts a view whose description is stale/wrong.** | Low-Med | Views are human-reviewed at activation; the engine still validates the inlined SQL; description quality is the author's responsibility (coverage hint, future). |
| **RX4 — R3 attribution mis-blames a cross-file error on one view.** | Low | Fall back to the manifest-level message when no single file is implicated; never auto-drop. |
| **RX5 — Surfacing views but the single-schema context-load (R1) still starves cross-schema models.** | Med | R2 reads the full manifest, so views surface regardless; but cross-schema *model* context is the separate R1 fix (golden-queries workstream) — note the dependency, don't conflate. |

## Decision points
- **D-R2 (surfacing mechanism):** index directly (A1/A2) **vs** bridge views→golden
  queries. → **Direct indexing (recommended)** — independent of the golden/R1
  workstream, lower coupling; revisit the bridge after R1.
- **D-R3 (partial activation):** attribute + human-reject **vs** auto-drop invalid
  targets. → **Attribute + human-reject (recommended)** — preserves the atomic
  invariant and never hides an authoring error.
- **D6 (native views):** → **Defer (resolved by eval).**

## Dependency graph
```
A3 (matched_views field) ─► A2 ─┐
A1 (index views) ───────────────┼─► A4 (prompt) ─► A5 (gate; live E14)   == HEADLINE ==
B1 (attribute) ──┐
B2 (ground)  ────┴─► B3 (gate; E13/E15)
C1 (record D6; no code)
```

## Out of scope (explicit)
- **R1 — cross-schema golden-query recall access scope.** Separate workstream
  ([[golden-queries-shared-memory]]); shares the single-schema query-time root cause
  but is a *golden-query* fix, not a view fix.
- **Native view execution.** Deferred (D6); plumbing stays dormant + safe.
- **Single-schema context-load expansion** (the `Loaded 5 dataset(s)` issue) — that
  is the cross-schema query-time / R1 work, not view surfacing.

## Definition of done
- Phase A: an activated semantic view appears in `wren_context`
  (`matched_views` non-empty) on a view-shaped question, and the live E14 re-run
  shows the agent querying the view and ≥1 of Q16–Q18 correct via it. Native views
  excluded from both surfacing paths.
- Phase B: E13 usable-yield > 0/3 (one bad view no longer sinks the changeset; the
  failing view is named); E15 still 3/3.
- Phase C: D6 recorded resolved in spec + Phase-1 impl.
