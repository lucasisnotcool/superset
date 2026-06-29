# Plan: Let relationships-only MDL files activate (stop breaking Wren's abstraction)

**Status:** Not started — resumable checklist for future agent sessions.
**Owner area:** `superset_ai_agent/semantic_layer` (MDL validation + activation).
**One-line:** The per-file activation gate rejects relationships-only files via `empty_root`,
breaking the Copilot apply→activate path. Wren itself stores relationships in their own
top-level file, so the fix is to make the per-file gate relationship-aware and keep the
round-trip guarantee where Wren puts it — engine validation of the **merged** manifest.

---

## 1. Problem statement (source-backed)

`propose_relationships` writes relationships-only files `{"relationships":[…]}` under
`relationships/` ([copilot/tools.py:867](semantic_layer/copilot/tools.py#L867)). The per-file
activation gate rejects any file with no model/view/metric/cube as `empty_root`
([mdl_validator.py:306-316](semantic_layer/mdl_validator.py#L306-L316)), so a Copilot
changeset that proposes relationships 422s on activation. A harness workaround folds
relationships into a model file (`consolidate_relationship_items`,
[eval_v2.py:197-232](evaluation/eval_v2.py#L197-L232)) — but that **only runs in the eval
harness** ([eval_v2.py:514](evaluation/eval_v2.py#L514)), never in production, and it folds
relationships into a model file, which is the opposite of what we want.

## 2. How Wren actually models relationships (external sources)

- The compiled manifest (`target/mdl.json`) has top-level keys `catalog`, `schema`,
  `models`, `relationships`, `metrics`, `views`, `cubes`.
  — https://docs.getwren.ai/oss/concepts/what_is_mdl
- In the **source project layout**, relationships live in their **own top-level
  `relationships.yml`**, *separate from models* (models under `models/`, views under
  `views/`, cubes under `cubes/`). Relationship fields: `name`, `models` (exactly two,
  `[from, to]`), `join_type`/`joinType`, `condition`.
  — https://docs.getwren.ai/oss/reference/mdl
- A relationship associates exactly two models; the engine uses `condition` as the join
  predicate during SQL generation; endpoints resolve in the **assembled** manifest.
  — https://www.getwren.ai/post/how-we-design-our-semantic-engine-for-llms-the-backbone-of-the-semantic-layer-for-llm-architecture

**Conclusion:** a relationships-only file is *native* to Wren's layout (`relationships.yml`).
Our fork's per-file `empty_root` gate imposes a constraint Wren does not have. The fold
workaround breaks Wren's model/relationship separation; we should remove the need for it.

## 3. Root cause & current architecture (source-backed)

There are **two** validation layers at activation:

| Layer | Code | Granularity | Sees merged manifest? | Handles relationships correctly? |
|---|---|---|---|---|
| Project/manifest gate (authoritative) | `_enforce_activation` / `_enforce_activation_manifest` ([app.py:1678-1763](app.py#L1678-L1763)); bulk: `set_mdl_files_status` ([app.py:1825-1910](app.py#L1825-L1910)) | **Projected active set merged into one manifest** | **Yes** | **Yes** — `validate_project_manifest(strict_relationships=True, deep_validate=…)` resolves every endpoint and runs wren-core/engine ([mdl_validator.py:348-421](semantic_layer/mdl_validator.py#L348-L421)) |
| Per-file gate (defense-in-depth) | `_assert_activatable` ([mdl_files.py:54-73](semantic_layer/mdl_files.py#L54-L73)), called in store `update()` ([mdl_files.py:261-264](semantic_layer/mdl_files.py#L261-L264) in-memory, [:451](semantic_layer/mdl_files.py#L451) SQLAlchemy) | **One file alone** | No | **No** — `validate_mdl(content)` hits `empty_root` for a relationships-only file |

The per-file gate is the *only* blocker. Once a relationships-only file is past `empty_root`,
the rest of `validate_mdl` already handles it: `strict_relationships` defaults `False`
([mdl_validator.py:283](semantic_layer/mdl_validator.py#L283)), so unresolved endpoints are
**warnings**, not errors ([mdl_validator.py:760-772](semantic_layer/mdl_validator.py#L760-L772)),
and warnings don't flip validity ([mdl_validator.py:344](semantic_layer/mdl_validator.py#L344)).
Malformed relationships still **fail** per-file because arity (`relationship_arity`) and
`invalid_join_type` messages use the default severity `"error"`
([schemas.py:317](semantic_layer/schemas.py#L317); checks at
[mdl_validator.py:738-758](semantic_layer/mdl_validator.py#L738-L758)).

> Note the **second** `empty_root` in `_parse_json`
> ([mdl_validator.py:1124-1128](semantic_layer/mdl_validator.py#L1124-L1128)) guards truly
> empty payloads (`{}`/`[]`/`null`). **Do not touch it.** Only the structural check at
> [mdl_validator.py:306](semantic_layer/mdl_validator.py#L306) changes.

Side effect of the fix: the per-file stored `validation` for relationship files
([mdl_files.py:248-252](semantic_layer/mdl_files.py#L248-L252),
[:437-442](semantic_layer/mdl_files.py#L437-L442)) flips from invalid→valid, so the MDL Lab
file-list badge stops showing relationship files as broken (free UI win, no FE change).

## 4. Requirements (acceptance criteria)

- **REQ-1** A relationships-only file with ≥1 well-formed relationship passes per-file
  `validate_mdl` (no `empty_root`) and activates when its endpoint models are in the
  projected active manifest.
- **REQ-2** Truly empty payloads (`{}`, `[]`, `null`, `{"relationships":[]}`) are still rejected.
- **REQ-3** A relationships-only file whose endpoints are absent from the projected active
  manifest still **422s at the project-level gate** (no safety regression).
- **REQ-4** A malformed relationship (wrong arity, bad/missing `joinType`) still fails per-file.
- **REQ-5** The merged manifest is still validated by the wren engine at activation when
  configured (`wren_core_validation_enabled` / `wren_activation_requires_engine`) —
  the "semantic SQL always translates back" guarantee is preserved/strengthened, not weakened.
- **REQ-6** `consolidate_relationship_items` is no longer required for the production
  apply→activate path; eval mirrors production (relationship files activate natively).

## 5. Decision points (with recommendations)

- **D1 — Per-relationship files vs single `relationships.json`.**
  Current: one file per relationship under `relationships/`. Wren convention: a single
  `relationships.yml`. **Recommendation: keep per-file (no change).** The merge
  (`_merge_json`, [mdl_compile.py:130-162](semantic_layer/mdl_compile.py#L130-L162)) and
  last-wins dedupe-by-name ([mdl_compile.py:165-176](semantic_layer/mdl_compile.py#L165-L176))
  already handle many files; relationship names are unique per pair. Smaller blast radius.
- **D2 — Remove `consolidate_relationship_items` vs keep as no-op shim.**
  **Recommendation: remove it and its call site** once REQ-1 lands and eval is repointed to
  activate relationship files natively (Phase 4). Keeping a now-unnecessary fold perpetuates
  the abstraction break and hides eval/production drift. Keep a one-line note in
  `RESULTS_v2.md` recording the bug + fix for provenance.
- **D3 — Should `empty_root` accept relationships referencing nothing resolvable?**
  **No.** Per-file admits well-formed relationships (warnings); the project-level gate with
  `strict_relationships=True` still rejects genuinely dangling relationships. Keep that strict.
- **D4 — Engine-validation posture for round-trip safety.**
  **Recommendation:** for deployments that care about guaranteed round-trip, set
  `wren_core_validation_enabled=True` (or `wren_activation_requires_engine=True`,
  [app.py:1711-1720](app.py#L1711-L1720)). Even without the engine, project-level **structural**
  strict resolution catches dangling relationships; the engine adds the SQL-rewrite guarantee.

## 6. Sequential checklist

> Legend: ☐ todo · 🚧 blocker · ⤷ dependency. Do phases in order; each phase is independently
> committable. Run `pre-commit run --all-files` before any push (see root `CLAUDE.md`).

### Phase 0 — Confirm preconditions (no code change)
- [ ] Re-read §3 and confirm line numbers haven't drifted (`rg -n "empty_root" semantic_layer/mdl_validator.py`).
- [ ] Confirm the project-level gate runs for **both** routes: single PATCH `update_mdl_file`
      ([app.py:1916-1957](app.py#L1916-L1957)) via `_enforce_activation`, and bulk
      `set_mdl_files_status` ([app.py:1825-1910](app.py#L1825-L1910)) via `_enforce_activation_manifest`.
      ⤷ This is what makes relaxing the per-file gate safe (REQ-3).

### Phase 1 — Core fix (the only behavioral change) 🚩
- [ ] In `validate_mdl`, include `relationships` in the non-empty check at
      [mdl_validator.py:306](semantic_layer/mdl_validator.py#L306):
      `if not models and not views and not metrics and not cubes and not relationships:`
- [ ] Update the function docstring ([mdl_validator.py:286-294](semantic_layer/mdl_validator.py#L286-L294))
      to note relationships-only files are valid fragments (endpoints resolved at project merge).
- [ ] **Do not** modify the `_parse_json` `empty_root` at
      [mdl_validator.py:1124-1128](semantic_layer/mdl_validator.py#L1124-L1128) (REQ-2 guard).
- ⤷ Blocks Phases 2–4.

### Phase 2 — Unit tests (lock REQ-1..4)
- [ ] `validate_mdl('{"relationships":[{"name":"a_b","models":["a","b"],"joinType":"MANY_TO_ONE","condition":"a.x=b.y"}]}')`
      → `valid=True`, no `empty_root`, two `unresolved_relationship` **warnings** (REQ-1).
- [ ] `validate_mdl('{}')`, `'{"relationships":[]}'`, `'[]'`, `'null'` → invalid `empty_root` (REQ-2).
- [ ] Malformed relationship (one endpoint / bad `joinType`) → `valid=False` with
      `relationship_arity` / `invalid_join_type` errors (REQ-4).
- [ ] `_assert_activatable("active", <relationships-only content>)` does **not** raise
      (import from [mdl_files.py:54](semantic_layer/mdl_files.py#L54)).
- [ ] Project-level: `validate_project_manifest([model_file, rel_file], strict_relationships path)`
      → valid when both endpoint models present; `validate_project_manifest([rel_file_only])`
      → invalid `unresolved_relationship` **error** (REQ-3). See existing
      [tests/unit_tests/superset_ai_agent/test_wren_core_validator.py](../tests/unit_tests/superset_ai_agent/test_wren_core_validator.py) for the deep-validate pattern.

### Phase 3 — API/integration test (end-to-end apply→activate)
- [ ] Test that activating a model file + a relationships-only file via the **bulk-status**
      route (`set_mdl_files_status`, [app.py:1829](app.py#L1829)) returns 200 and both go active
      (REQ-1 + REQ-3 happy path).
- [ ] Test that activating a relationships-only file whose endpoints are absent → 422 from
      `_enforce_activation_manifest` ([app.py:1730-1737](app.py#L1730-L1737)) (REQ-3 negative).
- ⤷ Depends on Phase 1.

### Phase 4 — Retire the workaround (D2, REQ-6)
- [ ] Repoint eval to exercise the production path: in `_copilot_onboard_and_activate`
      ([eval_v2.py:507-533](evaluation/eval_v2.py#L507-L533)) drop the
      `consolidate_relationship_items` call ([eval_v2.py:514](evaluation/eval_v2.py#L514)) and
      apply/activate `raw_items` directly.
      🚧 **Blocker/verify first:** confirm whether `copilot_apply`→`activate_all`
      ([eval_v2.py:518-521](evaluation/eval_v2.py#L518-L521)) routes through the real
      bulk-status API (then Phase 1 alone unblocks it) or merges files by another path.
      If the latter, ensure that path no longer needs the fold before deleting it.
- [ ] Remove `consolidate_relationship_items`, `_is_relationships_only`, `_item_mdl` helpers
      if now unused ([eval_v2.py:191-232](evaluation/eval_v2.py#L191-L232)) and their tests
      ([evaluation/test_eval_v2.py:341-364](evaluation/test_eval_v2.py#L341-L364)).
- [ ] Keep `relationships_folded` out of the result payload
      ([eval_v2.py:529](evaluation/eval_v2.py#L529)) once folding is gone, or hardcode 0 with a note.
- [ ] Leave a one-line provenance note in
      [evaluation/RESULTS_v2.md](evaluation/RESULTS_v2.md) (lines ~226-240) recording the fix.

### Phase 5 — Docs & provenance
- [ ] Update the activation-gate description in
      [wren_mdl_copilot.md:514](wren_mdl_copilot.md#L514) and
      [wren_model.md:367](wren_model.md#L367) to state relationships-only files are valid
      fragments validated at merge.
- [ ] Note the change in `UPDATING.md` only if any documented external behavior changes
      (likely not — this lifts a false rejection; no breaking change).

### Phase 6 — Verify in-app (optional but recommended)
- [ ] Run the MDL Lab flow: Copilot proposes relationships → apply → activate; confirm 200
      and the file-list badge shows the relationship file valid (the `/verify` or `/run` skill).
- [ ] Confirm with `wren_core_validation_enabled=True` the merged manifest still deep-validates
      (REQ-5) — a relationship with a bad `condition` should be caught by the engine, not slip.

## 7. Entrypoints & touchpoints

| Concern | File:line | Change |
|---|---|---|
| Core fix | [mdl_validator.py:306](semantic_layer/mdl_validator.py#L306) | add `and not relationships` |
| Docstring | [mdl_validator.py:286-294](semantic_layer/mdl_validator.py#L286-L294) | clarify fragment validity |
| Do NOT change | [mdl_validator.py:1124-1128](semantic_layer/mdl_validator.py#L1124-L1128) | parse-time empty guard stays |
| Per-file gate (unblocked, no edit) | [mdl_files.py:54-73](semantic_layer/mdl_files.py#L54-L73) | behavior changes via §Phase 1 only |
| Project gate (authoritative, no edit) | [app.py:1678-1763](app.py#L1678-L1763), [app.py:1825-1910](app.py#L1825-L1910) | relied upon for REQ-3/REQ-5 |
| Proposal tool (no edit, D1) | [copilot/tools.py:789-877](semantic_layer/copilot/tools.py#L789-L877) | keep relationships-only output |
| Eval workaround (remove, Phase 4) | [eval_v2.py:191-232](evaluation/eval_v2.py#L191-L232), [eval_v2.py:514](evaluation/eval_v2.py#L514) | delete fold + call site |
| Tests | new unit tests; [evaluation/test_eval_v2.py:341-364](evaluation/test_eval_v2.py#L341-L364) | add/remove |
| Docs | [wren_mdl_copilot.md:514](wren_mdl_copilot.md#L514), [wren_model.md:367](wren_model.md#L367), [evaluation/RESULTS_v2.md](evaluation/RESULTS_v2.md) | update |

## 8. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Relaxing `empty_root` lets garbage activate | Arity + `invalid_join_type` are **errors** (default severity `error`, [schemas.py:317](semantic_layer/schemas.py#L317)) → malformed relationships still fail per-file (REQ-4). Truly empty payloads caught by `_parse_json` guard (REQ-2). |
| R2 | Dangling relationship slips to active | Project-level gate uses `strict_relationships=True` → unresolved endpoints become **errors** in the merged manifest ([mdl_validator.py:764](semantic_layer/mdl_validator.py#L764)); both PATCH and bulk routes run it (Phase 0). |
| R3 | Round-trip/SQL-rewrite regression | Don't weaken project-level `deep_validate` ([app.py:1724](app.py#L1724)); recommend enabling engine validation (D4). Engine validates the merged manifest where relationships are meaningful (REQ-5). |
| R4 | Removing the fold breaks eval reproducibility | Repoint eval to the real activation path and update tests in the same change (Phase 4); record in `RESULTS_v2.md`. |
| R5 | Stale stored `validation` on pre-existing draft relationship files shows invalid | Recomputed on next content update/activation ([mdl_files.py:248-252](semantic_layer/mdl_files.py#L248-L252)); optional one-off backfill only if the UI surfaces stale drafts — likely unnecessary. |
| R6 | Wren-core not installed in some envs | Project-level **structural** strict resolution still catches dangling relationships without the engine; `wren_activation_requires_engine` degrades **closed** if engine authority is required ([app.py:1711-1720](app.py#L1711-L1720)). |

## 9. Rollback
Phase 1 is a one-line revert. Phases 2–3 are additive tests. Phase 4 (workaround removal) is
the only one with coupled changes — revert the eval edit + restore the helper/tests together
if eval drift appears.
