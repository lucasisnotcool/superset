<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Plan — Make the MDL Copilot enrich assertively (relationships + metrics, not just descriptions)

**Status:** proposed (no code changed yet)
**Owner skill:** `enrich-context`, `generate-mdl`, base `mdl_copilot`
**Related:** [plan_enrichment_relationship_model_fix.md](plan_enrichment_relationship_model_fix.md) (the join-as-model guard — already landed; complementary, not overlapping)

---

## 1. Problem & root causes (source-backed)

The Copilot under-enriches: it adds descriptions/tags but rarely proposes
**relationships** or **metrics** unless the user explicitly asks. The *skills* are
fine; the *runtime* never lets the assertive path fire. Five causes, prioritized:

| # | Root cause | Evidence |
|---|---|---|
| **RC1** | **The mode flag is dead.** `enrich-context` branches its whole behavior on grill vs auto-pilot, but the resolved flag is never injected into the prompt, so the model defaults to the cautious (grill) reading. | flag declared [config.py:202](config.py#L202) + parsed [config.py:675-677](config.py#L675); **never read** by [loop.build_system_prompt](semantic_layer/copilot/loop.py#L50-L68) or [service.run_copilot](semantic_layer/copilot/service.py#L101-L150); skill Step 0 [enrich-context.md:79-88](skills/enrich-context.md#L79-L88) |
| **RC2** | **Relationships & metrics are gated behind "ask first."** The skill marks exactly these as high-blast-radius and tells the agent to grill (not volunteer) them. | [enrich-context.md:71-77](skills/enrich-context.md#L71-L77), [enrich-context.md:239-240](skills/enrich-context.md#L239) |
| **RC3** | **Base invariant says do the minimum, and invariants win over skills.** | "smallest set of edits" [mdl_copilot.md:33-34](prompts/mdl_copilot.md#L33-L34); "invariants win" [mdl_copilot.md:10](prompts/mdl_copilot.md#L10) |
| **RC4** | **No skill routing.** All three skills are concatenated every turn; `generate-mdl` (eager on relationships/metrics) and `enrich-context` (gated) give opposite postures, and the cautious one dominates. | fixed tuple [service.py:64](semantic_layer/copilot/service.py#L64); `_skill_texts` always returns all three [service.py:89-98](semantic_layer/copilot/service.py#L89) |
| **RC5** | **Step budget too tight for a full pass** (read schema + files + docs can exhaust it before authoring). | one step per model turn [loop.py:122](semantic_layer/copilot/loop.py#L122); default `max_steps=8` [copilot/schemas.py:149](semantic_layer/copilot/schemas.py#L149); `max_correction_retries=1` [loop.py:82](semantic_layer/copilot/loop.py#L82) |

**Keystone:** RC1. There is currently **no input under which the Copilot runs in the
assertive mode the enrichment skill was written for.** Fix RC1 first; the rest reinforce it.

---

## 2. Requirements (acceptance criteria)

- **R1** The resolved `wren_copilot_autopilot_enabled` value MUST reach the system
  prompt so the agent knows which mode (`grill` | `autopilot`) it is in.
- **R2** In **auto-pilot**, the agent MUST be allowed to *propose* new relationships
  and metrics in the changeset without first asking — the human accept/reject gate on
  the changeset IS the review step. (Grill behavior unchanged: still proposes and waits.)
- **R3** The "smallest set of edits" invariant MUST NOT suppress a deliberate
  enrichment sweep; scope it so targeted edits stay minimal but "enrich" sweeps the gap catalog.
- **R4** Default behavior with the flag **off** MUST remain today's grill behavior
  (no regression for existing deployments).
- **R5** Every existing test stays green; new behavior is covered by tests.
- **R6** No change to the changeset/accept contract, the validators, or persistence
  (those are correct; this is a prompt-wiring change).

---

## 3. Entrypoints & touchpoints

**Backend wiring (the keystone path):**
- [semantic_layer/copilot/loop.py:50-68](semantic_layer/copilot/loop.py#L50-L68) — `build_system_prompt(...)`: add a `mode` parameter, render an `## Active mode` block.
- [semantic_layer/copilot/loop.py:71-103](semantic_layer/copilot/loop.py#L71-L103) — `run_copilot_loop(...)`: accept `autopilot: bool`, thread into `build_system_prompt`.
- [semantic_layer/copilot/service.py:101-150](semantic_layer/copilot/service.py#L101-L150) — `run_copilot(...)`: accept `autopilot: bool = False`, pass through.
- [semantic_layer/copilot/service.py:277-297](semantic_layer/copilot/service.py#L277-L297) — `build_inspector(...)`: pass `mode` so the inspector preview shows the active mode (the inspector should reflect reality).
- [app.py:1865-1883](app.py#L1865-L1883) (`run_project_copilot`) and [app.py:1929-1946](app.py#L1929-L1946) (`stream_project_copilot`) — pass `autopilot=app_config.wren_copilot_autopilot_enabled` (same pattern already used for `deep_validate=app_config.wren_modeling_deep_validation` and `retrieve_k=app_config.wren_document_retrieve_k`).

**Prompt/skill copy:**
- [prompts/mdl_copilot.md:33-34](prompts/mdl_copilot.md#L33-L34) — scope "smallest set of edits" (RC3 / R3).
- [skills/enrich-context.md:69-88](skills/enrich-context.md#L69-L88) and [:239-240](skills/enrich-context.md#L239) — reframe relationships/metrics in auto-pilot as *propose-in-changeset* (RC2 / R2).

**Optional (decision points, below):**
- [semantic_layer/copilot/service.py:64,89-98](semantic_layer/copilot/service.py#L64) — skill routing (RC4).
- [config.py:197-202](config.py#L197-L202) + the two `app.py` call sites — enrichment step budget (RC5).

**Tests:**
- `tests/unit_tests/superset_ai_agent/test_copilot_loop.py` — assert the mode block renders per flag.
- `tests/unit_tests/superset_ai_agent/test_copilot_service.py` (or nearest existing) — `run_copilot(autopilot=…)` plumbs through; `build_inspector` reflects mode.
- Existing: `test_copilot_tools.py`, `test_mdl_validator.py` must stay green.

---

## 4. Decision points (with recommendations)

- **DP1 — How to expose mode to the model.** Options: (a) a one-line `## Active mode`
  block in the system prompt; (b) restructure skills to ship only the active branch.
  **Recommend (a)** — minimal, reversible, leaves the skills as the single source of
  truth; matches how operator instructions are already appended in `build_system_prompt`.
- **DP2 — Scope of R2 (propose relationships/metrics without asking).** Options:
  (a) auto-pilot only; (b) both modes. **Recommend (a)** — grill's "propose one at a
  time and wait" is a deliberate UX; loosening it risks noisy turns. Auto-pilot is the
  opt-in assertive mode by design ([config.py:199-200](config.py#L199-L200)).
- **DP3 — Skill routing (RC4).** Options: (a) leave all three loaded; (b) drop
  `onboarding` once a base MDL exists; (c) full intent router. **Recommend (b)** as a
  cheap win (an onboarded project doesn't need the onboarding playbook in-context),
  defer (c). Low risk, shrinks the prompt, removes the eager-vs-gated contradiction's
  loudest voice. *Gate: only if RC1+RC2 alone don't move the needle in manual testing.*
- **DP4 — Step budget (RC5).** Options: (a) leave defaults; (b) raise the request
  default; (c) a separate enrichment ceiling. **Recommend (b)** small bump only if
  testing shows truncation; the request already allows up to 24 ([copilot/schemas.py:149](semantic_layer/copilot/schemas.py#L149)) so the client can opt up without code change. *Treat as observe-then-tune, not a blind raise.*

---

## 5. Sequential checklist (with blockers & dependencies)

> Do P1 → P2 → P3 in order. P4/P5 are gated on P3 results (don't do them blindly).
> After each phase: add/adjust tests, run the Python suite, note risks.

### P1 — Wire the mode flag into the prompt (RC1 / R1, R4) — **keystone, no dependencies**
- [ ] `build_system_prompt`: add `mode: Literal["grill","autopilot"] = "grill"`; append a block, e.g.
      `## Active mode\nMODE = {mode}. In grill mode propose and wait; in auto-pilot make best-effort inferences and propose them in the changeset.`
- [ ] `run_copilot_loop`: add `autopilot: bool = False`; map to `mode` and pass down.
- [ ] `run_copilot` (service): add `autopilot: bool = False`; pass to `run_copilot_loop`.
- [ ] `build_inspector`: thread `mode` so the inspector preview matches the live prompt.
- [ ] Both `app.py` call sites: pass `autopilot=app_config.wren_copilot_autopilot_enabled`.
- [ ] **Test:** flag off → prompt contains `MODE = grill`; flag on → `MODE = autopilot`.
- [ ] **Test:** `build_inspector(mode=...)` reflects the mode.
- **Blocker for:** P2 is far weaker without P1 (the model can't tell which posture to take).

### P2 — Reframe relationships/metrics for auto-pilot (RC2 / R2) — **depends on P1**
- [ ] `enrich-context.md` Rule 8b / Step 6: in **auto-pilot**, *propose* new
      relationships/metrics/aggregate-calc-fields directly into the changeset (the human
      accept gate is the review); keep grill's ask-first behavior unchanged. Keep the
      conflict escalation (8a) and routing-ambiguity escalation (8c) intact for both modes.
- [ ] Cross-check against the landed join-as-model guard: a proposed relationship still
      goes in `relationships[]`, never `models[]` (see [plan_enrichment_relationship_model_fix.md](plan_enrichment_relationship_model_fix.md) — `strict_models`).
- [ ] **Test (prompt-content):** assert the auto-pilot branch no longer says "grill" for
      relationships/metrics. (Behavioral proof needs a live model; assert the copy contract.)

### P3 — Scope the "smallest edits" invariant (RC3 / R3) — **depends on P1**
- [ ] `mdl_copilot.md:33-34`: reword to "Make the smallest set of edits for a *targeted*
      request; for an explicit **enrich** request, sweep every applicable gap in the
      enrich-context catalog." Keep "invariants win" intact.
- [ ] **Test:** prompt assembles and still contains the invariants section; new wording present.

### P4 — (GATED on P1–P3 manual test) Skill routing — DP3 recommendation (b)
- [ ] Only if assertiveness still lacking: in `_skill_texts`, drop `onboarding` when the
      project already has ≥1 active model (pass a small flag from `run_copilot`/inspector).
- [ ] **Test:** onboarded project → `onboarding` skill omitted; empty project → retained.

### P5 — (GATED on observation) Step budget — DP4 recommendation (b)
- [ ] Only if steps observed exhausting before authoring: raise the client/default
      `max_steps` for enrichment turns (no engine change; ceiling is already 24).
- [ ] **Test:** request default reflects the new value; ≤ ceiling.

### Finalize
- [ ] `ruff check` clean on changed `.py`; `mypy` clean on changed `.py`.
- [ ] Full Python suite green (expect parity with prior 786 passing baseline + new tests).
- [ ] `pre-commit run` on staged files.
- [ ] Update this file's checkboxes; note any residual gap between expectation and behavior.

---

## 6. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Auto-pilot becomes *too* eager (noisy/low-confidence relationships) | Med | Default flag stays **off** (R4); changeset is review-gated; keep confidence tagging (skill Rule 5) and conflict/ambiguity escalations (8a/8c). |
| Prompt-content tests are brittle (assert exact strings) | Med | Assert on stable anchors (`MODE = `, section headers), not full sentences. |
| Larger prompt (mode block) marginally raises tokens | Low | One line; net-negative if P4 drops a whole skill. |
| Behavior can't be unit-proven (needs a live LLM) | High (inherent) | Unit-test the *wiring + prompt contract*; verify end behavior in manual/integration with a real model before claiming the UX is fixed. |
| Divergence from base "invariants win" rule (P3) | Low | Keep the rule; only scope the one clause it overrides. |
| Interaction with landed `strict_models` guard | Low | P2 explicitly routes relationships to `relationships[]`; covered by existing self-correction tests. |

---

## 7. Out of scope
- Validators, changeset schema, persistence, activation (already correct).
- The single-shot structured enrichment path (`prompts/wren_enrichment.md` via
  `llm_client._draft_with_correction`) — different code path; this plan is the **copilot
  loop** only. (Note for a future plan: that path may have its own assertiveness profile.)
- Cube authoring (deliberately not a sink — see skill parity notes).

---

## 8. Verification summary

- [x] **P1 keystone landed + tested.** `wren_copilot_autopilot_enabled` now threads
      `run_copilot → run_copilot_loop → build_system_prompt`, rendering an
      `## Active mode` banner (grill | autopilot); `build_inspector` mirrors it; both
      `app.py` call sites + the inspector route pass the flag. Tests: 5 (grill/autopilot
      banner in prompt, loop sends banner, inspector reflects mode).
- [x] **P2 / P3 copy changes landed + tested.** enrich-context auto-pilot now *proposes*
      relationships/metrics into the review-gated changeset (Rule 8 trimmed to 2
      escalations; Step 4/6 + things-to-avoid reconciled). Base prompt scopes
      "smallest edits" to targeted requests; enrichment sweeps the catalog incl.
      relationships+metrics. Tests: 2 (skill contract guard; base-prompt scope).
- [x] **P4 / P5 evaluated → DEFERRED (recorded).** Both are gated on live-model
      evidence (DP3/DP4 say "observe-then-tune; don't do blindly"). No live model in
      this environment, so deferred rather than implemented speculatively. Re-open if
      manual testing shows assertiveness still lacking (P4) or step exhaustion (P5).
- [x] **Suite green; lint clean.** 793 passed / 11 skipped (786 baseline + 7 new);
      ruff clean on changed `.py`; no new mypy errors (only pre-existing
      `persistence/models.py` `Base` + `MdlFileStore` protocol conflicts remain).
- [ ] **Manual check with a live model** (REQUIRED before claiming UX fixed): set
      `wren_copilot_autopilot_enabled=true`, run an enrichment turn, confirm
      relationships + metrics are proposed in the changeset. Wiring + prompt contract
      are unit-proven; end behavior depends on the model honoring the prompt.
