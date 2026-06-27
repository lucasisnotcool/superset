# Plan — fix relationship-as-model enrichment failures + accept-button UX

**Status:** PROPOSAL (not implemented). Source-audited against the working tree.
Addresses the three observed defects in the Copilot enrichment → MDL pipeline
(see the prior analysis): (1) per-item Accept buttons appear inert, (2) created
file cannot be activated, (4) the LLM's self-fix can't delete the bad models.

**Root cause (issues 2 & 4):** the enrichment/Copilot step emits **relationships as
`models[]` entries** (e.g. `sites_to_production_lines`) with no `tableReference`/
`refSql` and no `columns`, violating the prompt rule that relationships belong in
`relationships[]` ([wren_enrichment.md:46-47](prompts/wren_enrichment.md#L46-L47)).
The **structural validator only warns** about these
([mdl_validator.py:332-342](semantic_layer/mdl_validator.py#L332-L342),
[:367-374](semantic_layer/mdl_validator.py#L367-L374)); the hard
`missing field 'columns'` **error** comes only from wren-core deep validation
([wren_core_validator.py:117-128](semantic_layer/wren_core_validator.py#L117-L128)).
But the Copilot run validates with `deep_validate=wren_modeling_deep_validation`
which **defaults to False** ([config.py:196](config.py#L196),
[app.py:1865-1877](app.py#L1865-L1877)), whereas **activation** validates deep
(`wren_core_validation_enabled=True`, [app.py:1215-1231](app.py#L1215-L1231)). The
asymmetry means the Copilot's in-loop self-correction
([loop.py:175-211](semantic_layer/copilot/loop.py#L175-L211)) never sees the error,
so the broken changeset is presented, applied as drafts, and only fails at
activation.

**Constraints:** additive; degrade-closed; do not touch the upload→MDL hot path's
contract; **do not auto-coerce** relationships (we cannot reliably infer
`joinType`/`condition`). **Cross-agent:** `app.py`, `CopilotPanel.tsx`, `tools.py`,
prompts carry the onboarding/provenance agent's landed work — re-confirm clean +
re-Read before editing (per-step blockers below).

---

## Requirements

- **R1 (keystone):** A model with **neither a physical mapping (`tableReference`/
  `refSql`) nor `columns`** must be an **error** at *proposal* time, with an
  actionable message pointing to `relationships[]`. This makes the Copilot's
  existing self-correction loop fire **before** the changeset is shown — without
  depending on wren-core being installed.
- **R2:** The Copilot must self-correct (or visibly fail) on R1 errors in-loop, not
  silently present an unactivatable changeset.
- **R3 (UI):** Changeset items that are invalid (`validation.valid === false`) must
  **not** be accepted by default; per-item Accept/Reject must be an unambiguous
  toggle, and "Apply N accepted" must reflect only valid+accepted items.
- **R4:** When the Copilot removes/relocates a model, it must **edit the containing
  file** (`write_mdl_file`); `delete_mdl_file` on a non-existent path must return
  actionable guidance (it is file-scoped, there is no per-model delete).
- **R5 (defense-in-depth):** Prompts reinforce relationships→`relationships[]` and
  "edit, don't delete-by-name". Prompts are the *last* line, not the control.

---

## Decision points (with recommendations)

- **DP1 — Scope of the new validator error.**
  - (a) **Narrow (RECOMMENDED):** error only when **both** mapping *and* columns are
    absent (`model_missing_mapping_and_columns`). A real model always has columns; a
    calculated/CTE model may lack a `tableReference` but still has columns; only a
    relationship-shaped junk entry has neither. Safe — wren-core rejects these
    anyway, and onboarding seeds always have columns+mapping
    ([wren_full.md:345,393](wren_full.md#L345) call `model_without_mapping`
    "impossible for seeded/untouched models").
  - (b) Broad: promote `model_without_columns` to error generally. **Rejected** —
    risks breaking legitimate intermediate states and seeded flows.
- **DP2 — Also enable deep validation for the Copilot run?**
  - Recommendation: **leave `wren_modeling_deep_validation` default False**; R1's
    structural error is the dep-free primary control. Optionally let operators set
    it True as a backstop. (Enabling by default couples the Copilot loop to
    wren-core availability and yields a less actionable message.)
- **DP3 — UI default-accept policy (issue 1).**
  - Recommendation: **default-accept VALID items only**; auto-exclude items whose
    `validation.valid === false`. Keeps the common "accept all (valid)" flow
    (GitHub-PR-style: items included by default) while making Accept meaningful for
    invalid items and preventing application of known-bad drafts.
  - Rejected: default-none (adds friction to every accept), keep-all (the status
    quo that confuses users and lets invalid drafts through).
- **DP4 — Auto-repair relationships?**
  - Recommendation: **No.** Reject with guidance; let the LLM re-author into
    `relationships[]`. Auto-coercion can't infer join semantics and would fabricate
    `condition`/`joinType`.

---

## PART A — Technical specification

### A1. Validator: relationship-shaped models become errors (R1)
- **Entrypoint:** `semantic_layer/mdl_validator.py`, `_validate_models`
  ([:332-355](semantic_layer/mdl_validator.py#L332-L355)) where `table` (None when
  no mapping) is already computed and `_validate_columns` is called.
- **Spec:** after determining `table is None`, also check columns; when **both** are
  absent, append a single **error** (not the two existing warnings):
  ```python
  cols = model.get("columns")
  if table is None and (not isinstance(cols, list) or not cols):
      messages.append(MdlValidationMessage(
          severity="error",
          message=(f"Model {name} has neither a physical mapping "
                   "(tableReference/refSql) nor columns. If it represents a join, "
                   "define it under relationships[] instead of models[]."),
          code="model_missing_mapping_and_columns",
      ))
      continue  # skip the now-redundant mapping/columns warnings for this model
  ```
- Keep the existing warnings for the *partial* cases (mapping-but-no-columns, etc.).
- This flows through `validate_mdl` → `validate_project_manifest`
  ([:221](semantic_layer/mdl_validator.py#L221) `valid = not any(error)`), so the
  proposal's `validate_working()` now returns an error even with `deep_validate=False`.

### A2. Loop self-correction already handles errors (R2)
- **Entrypoint:** `semantic_layer/copilot/loop.py`
  ([:175-211](semantic_layer/copilot/loop.py#L175-L211)) — it collects
  `severity=="error"` messages from `validate_working()` and feeds them back
  ("Fix exactly these and finalize") for up to `max_correction_retries`.
- **Spec:** no structural change needed; A1 makes the error visible. *Optional:*
  enrich the feedback so the model name + "move to relationships[]" is explicit
  (the message already carries it). Verify the loop re-runs `build_changeset` after
  correction ([:211](semantic_layer/copilot/loop.py#L211)).

### A3. UI: don't auto-accept invalid items; clear toggle (R3, issue 1)
- **Entrypoint:** `CopilotPanel.tsx` `handleSend`
  ([:280-285](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx#L280-L285)).
- **Spec:**
  ```js
  result.items.forEach(item => {
    initial[item.path] = item.validation?.valid === false ? 'rejected' : 'accepted';
  });
  ```
  So valid items stay accepted (common flow preserved); invalid items default to
  rejected and are excluded from `acceptedItems`
  ([:303-309](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx#L303-L309))
  and the "Apply N accepted" count
  ([:396-405](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx#L396-L405)).
- **Clarity (optional, recommended):** near an invalid item's `invalid` tag
  ([:430](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx#L430)),
  show why it's excluded (the first error message), so the user understands the
  Accept toggle. Keep Accept/Reject as the explicit per-item toggle; the batch
  "Apply N accepted" remains the only commit.

### A4. Tooling: actionable delete-not-found (R4, issue 4)
- **Entrypoint:** `semantic_layer/copilot/tools.py` `_delete_mdl_file`
  ([:307-314](semantic_layer/copilot/tools.py#L307-L314)).
- **Spec:** when no file exists at `path`, return a hint instead of a bare error:
  `{"error": "No MDL file at '<path>' to delete. Deletion removes whole files by
  path; to remove or relocate a model, rewrite its file with write_mdl_file."}`
  This steers the LLM away from delete-by-model-name toward editing the file.

### A5. Prompts: defense-in-depth (R5)
- **Entrypoints:** `prompts/wren_enrichment.md`
  ([:46-47](prompts/wren_enrichment.md#L46-L47), already correct — strengthen
  emphasis), `prompts/mdl_copilot.md` (add: "A join/relationship goes in
  `relationships[]`, never as a model; to remove a model, rewrite its file with
  write_mdl_file — delete_mdl_file only removes whole files by path").
- Prompts are the last line; A1–A4 are the enforced controls.

---

## PART B — Sequential checklist (for future sessions)

Ordered so the keystone backend control lands first; each phase is independently
shippable. Run after each: `pytest tests/unit_tests/superset_ai_agent/ -q`
(backend) and `jest src/SqlLab/components/AiAgentPanel` + `tsc --noEmit` (FE).

- [ ] **P1 — Validator error rule (R1, DP1-narrow).** Add
      `model_missing_mapping_and_columns` (error) in `mdl_validator.py`
      `_validate_models` (A1); suppress the redundant warnings for that model.
      **Tests:** `test_mdl_validator` — a model with neither mapping nor columns →
      `valid=false` + the new code; a calculated model (columns, no mapping) stays a
      warning; a normal model passes. **Blocker:** `mdl_validator.py` is shared
      (validation + activation + onboarding) — run the FULL agent suite to catch
      regressions (esp. onboarding seeding tests). _Dep:_ none (keystone).

- [ ] **P2 — Loop self-correction verification (R2).** Confirm `loop.py` feeds the
      new error back and re-builds the changeset; optionally sharpen the correction
      message. **Tests:** `test_copilot_loop` — a tool-run that writes a
      relationship-as-model triggers a correction retry; after the model is moved to
      `relationships[]`, the loop finalizes valid. **Blocker:** `loop.py` shared;
      re-Read first. _Dep:_ **P1** (needs the error to exist).

- [ ] **P3 — UI: exclude invalid items by default (R3, DP3).** `CopilotPanel.tsx`
      `handleSend` defaults invalid items to `rejected` (A3); optional invalid-reason
      hint. **Tests:** `CopilotPanel.test.tsx` — a streamed changeset with one
      invalid item shows "Apply N accepted" excluding it; the invalid item renders
      its Reject state; accepting it manually re-includes it. **Blocker:**
      `CopilotPanel.tsx` is the other agent's landed file — confirm clean, re-Read.
      _Dep:_ none functionally (item.validation already populated), but most
      meaningful **after P1** (so relationship-models are flagged invalid).

- [ ] **P4 — Delete-not-found guidance (R4).** `tools.py` `_delete_mdl_file`
      actionable message (A4). **Tests:** `test_copilot_tools` — deleting a missing
      path returns the new hint. **Blocker:** `tools.py` shared; re-Read. _Dep:_ none.

- [ ] **P5 — Prompt reinforcement (R5).** Update `wren_enrichment.md` +
      `mdl_copilot.md` (A5). **Tests:** prompt-registry/snapshot test if present;
      otherwise none. **Blocker:** prompts may be edited by the other agent. _Dep:_
      none. Lowest priority.

- [ ] **P6 — Close-out.** `pytest` (full agent suite), `jest` (AiAgentPanel), `tsc`,
      `pre-commit run --files <changed>`; update the prior analysis doc / this plan
      with what landed. _Dep:_ all above.

### Dependency graph
```
P1 ──► P2          (loop needs the error)
P1 ··► P3          (UI most useful once items are flagged invalid; not a hard dep)
P4, P5  independent
P6 after all
```

---

## PART C — Risks & mitigations

- **R-1 Over-broad validator error breaks legit/seeded models.** *Mitigation:*
  DP1-narrow (both mapping AND columns missing). Such a model is invalid in
  wren-core regardless; onboarding seeds always carry columns+mapping
  ([wren_full.md:393](wren_full.md#L393)). Run the full suite (P1 blocker).
- **R-2 Activation behavior change.** P1 makes `validate_project_manifest` (used at
  activation, [app.py:1215](app.py#L1215)) raise the new error *as well as* wren-core's.
  Net effect is identical (still blocked) but the message is more actionable and now
  fires even when wren-core is absent. *Mitigation:* this is desired; document it.
- **R-3 UI default change surprises users.** *Mitigation:* only **invalid** items are
  excluded; the valid-item "accept all" flow is unchanged. Show the invalid reason.
- **R-4 No auto-repair leaves the user to re-prompt.** *Mitigation:* the loop's
  bounded self-correction (P2) usually fixes it before the user sees it; the UI
  exclusion (P3) prevents applying bad drafts if it doesn't.
- **R-5 Cross-agent file churn.** `mdl_validator.py`, `loop.py`, `tools.py`,
  `CopilotPanel.tsx`, prompts are shared. *Mitigation:* per-step blockers — confirm
  `git status` clean + re-Read immediately before each edit; keep edits small/disjoint.
- **R-6 Prompt-only regression risk.** Relying on P5 alone would not hold (the LLM
  already ignored the existing rule). *Mitigation:* P1–P4 are the enforced controls;
  P5 is defense-in-depth only.

---

## PART D — File touch list (conflict-checking)
`semantic_layer/mdl_validator.py` (P1) · `semantic_layer/copilot/loop.py` (P2) ·
`SemanticLayerEditor/CopilotPanel.tsx` (P3) · `semantic_layer/copilot/tools.py` (P4) ·
`prompts/wren_enrichment.md`, `prompts/mdl_copilot.md` (P5) · tests:
`test_mdl_validator`, `test_copilot_loop`, `CopilotPanel.test.tsx`,
`test_copilot_tools`. No DB migration. No change to the upload→MDL hot path, the
activation route logic (only the validator it calls), or wren-core itself.
