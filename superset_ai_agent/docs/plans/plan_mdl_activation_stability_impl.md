# MDL activation stability — implementation checklist

Status: IMPLEMENTED (2026-07-09) — full-suite verification recorded below.

## Problem (source-backed)

MDL activation outcomes flip between pass and 422 with no change to the failing
file. Reported symptom: removing only metrics from table A's MDL makes
activation fail with "Column X in table B does not exist", where X is not in
B's MDL nor on the physical Oracle table the user checked.

Root-cause analysis (traced 2026-07-09, this repo state = 416c541bd5):

1. **Activation re-validates the whole projected active manifest** —
   `_enforce_activation_manifest` (app.py) validates *every* staying-active
   file plus the changed one. Editing A re-checks B. By design (atomic
   invariant), not a bug.
2. **The physical column check is non-deterministic.** `columns_known`
   (mdl_validator.py `_validate_columns`) hinges on
   `SchemaIndex.ensure_columns`, which depends on:
   - a per-request reflect budget (`wren_introspection_column_reflect_budget`,
     default 40) — walk-order dependent;
   - a budget-exempt background warm daemon (`_warm_mdl_referenced_columns`)
     progressively mutating the shared TTL-cached index — time dependent;
   - transient Oracle reflection failures — memoized as failed for the
     request; skipped-with-warning → activation *passes*.
   Same manifest, different cache state → different outcome. Also explains
   unstable activation *speed* (inline live reflections).
3. **Cross-schema flat-map collision (false positive path).** `has_column`
   falls back to the flat, schema-unqualified `tables` map when the model's
   `tableReference` omits `schema`; `_apply_loaded` *overwrites* that flat
   entry with whichever schema's same-named table was reflected last. A real
   column of B (schema S1) can read "does not exist" because the flat entry
   holds B-from-S2's columns. Recurring single-schema-assumption class.
4. **Onboarding asymmetry.** `onboard_schema_project` auto-activates with
   per-file `validate_mdl` only — no whole-manifest gate, no deep wren-core
   gate (documented deviation in its docstring). A file that activates at
   onboarding can be un-reactivatable later.

User decision: existing project will NOT be repaired; it will be recreated via
auto-onboard + iterative MDL-Copilot edits. Fixes therefore target the
pipeline, not the data.

## Checklist

- [COMPLETE] **A. Schema-qualified column truth in `SchemaIndex`**
  (semantic_layer/mdl_validator.py)
  - [COMPLETE] A1. `from_agent_context`: flat `tables` entry becomes the union
        across datasets sharing a table name (was last-write-wins).
  - [COMPLETE] A2. `_apply_loaded`: after updating the qualified map, rebuild
        the flat entry as the union across all schemas containing the table.
        Bonus: `_claim_reflection_jobs` now reflects EVERY pending schema copy
        for an unqualified ref (was: first pending schema only), and
        `_pending_schema_for` was replaced by `_schemas_containing`.
  - [COMPLETE] A3. `has_column` / `columns_for` / `column_type` with
        `schema=None` on a schema-qualified index: resolve across the table's
        home schemas (any-match / union / unique-agreed type, cross-schema
        type disagreement reads unknown) instead of the collidable flat map.
  - [COMPLETE] A-tests: 6 new tests in test_multi_schema_schema_index.py
        (collision truth, reflection-order stability, unqualified ensure
        reflects all copies, type disagreement, unqualified-model validation).
- [COMPLETE] **B. Deterministic activation gate** (app.py)
  - [COMPLETE] B1. Extracted `_mdl_referenced_tables_from_contents(contents)`;
        `_mdl_referenced_tables` delegates to it.
  - [COMPLETE] B2. `_enforce_activation_manifest`: eager budget-exempt
        `ensure_columns_many(refs, charge_budget=False)` over every table the
        projected manifest references, before `validate_project_manifest`.
        Fail-soft (unreflectable tables still degrade to columns_unverified).
  - [COMPLETE] B-tests: 3 new API-level tests (reflect budget pinned to 0 so
        ONLY the gate's exempt pass can resolve columns): phantom column 422s
        deterministically on repeat, real column activates across toggles,
        single-file PATCH path uses the same gate. Also fixed the
        pre-existing stale test
        `test_bulk_activate_fetches_live_schema_once_and_deactivate_zero`
        (broken by the physical-catalog TTL cache from commit 67a205e383's
        follow-ups; the test now disables the cache to keep counting fetches).
- [COMPLETE] **C. Onboarding activation parity gate**
  (semantic_layer/onboarding.py + app.py route)
  - [COMPLETE] C1. `onboard_schema_project(deep_validate=False)`: staged
        activation — whole projected set validated as ONE manifest
        (dedup_models=True); on failure each file is gated alone and offenders
        stay draft with a named warning. Docstring deviation note replaced.
  - [COMPLETE] C2. `_run_onboarding` passes
        `deep_validate=wren_core_validation_enabled or wren_activation_requires_engine`.
  - [COMPLETE] C-tests: new test_onboarding_activation_parity.py (3 tests):
        manual-gate offender (relationships fragment with unresolved endpoint)
        strands as draft while the sibling activates; valid set fast-path;
        deep_validate flag reaches/skips the engine gate.
- [COMPLETE] **D. Full suite run** — see verification section below.

## Verification (2026-07-09)

- Full suite: `venv/bin/python -m pytest tests/unit_tests/superset_ai_agent/ -q`
  → **1569 passed, 13 skipped** (includes 12 new tests: 6 A + 3 B + 3 C).
- `venv/bin/ruff check` + `ruff format` clean on all touched files.
- mypy (pre-commit hook): no NEW errors in touched files. Two pre-existing,
  unrelated error classes remain on the clean tree too: the
  `adopt_resolved_from` str/str|None variable reuse (mdl_validator.py) and the
  app.py `_MdlFileStoreLike` / benchmark event-type literals.
- Test-suite repairs bundled here (both asserting behavior this plan
  deliberately changed or that a prior perf commit made stale):
  - `test_bulk_activate_fetches_live_schema_once_and_deactivate_zero` now
    disables the physical-catalog TTL cache so its fetch-count assertions are
    observable again (pre-existing failure on master).
  - `test_column_type_resolves_by_schema_on_collision`: unqualified lookup on
    a cross-schema type disagreement now asserts `None` (unknown/skip) instead
    of "whichever schema won the flat overwrite".

## Non-goals / accepted residual risk

- `columns_unverified` (reflection genuinely failed) still degrades OPEN
  (warning, check skipped, activation passes). Fail-closed would brick
  activation on transient Oracle outages, contradicting the validator's
  documented "must not brick activation" stance. After B, this is the only
  remaining nondeterminism and it requires an actual reflection failure.
- Activation latency now includes up-front reflection of every referenced
  table not yet memoized (concurrent, pool of 5, one `/table_metadata` call
  per table). First activation on a big cold project pays seconds; repeats
  hit memoized columns. This is the price of determinism and also makes the
  previously erratic activation *speed* predictable.
- Onboarding parity is the deep-validation FLAG only. The manual route's
  hard 409s (engine-required-but-missing, live-schema-required-but-missing)
  are not replicated in onboarding; with wren-core absent the deep pass
  degrades open there (valid + info), as everywhere else.
- If the whole onboarding set fails the manifest gate but a subset of
  solo-valid files is itself jointly invalid (only possible with cross-file
  interactions between solo-valid files — not the base-model shape), the
  activated subset could still 422 on a later manual re-activation. Accepted:
  onboarding proposals are per-table base models.
- Physical drift (column dropped in Oracle after onboarding) is detected at
  the *next* activation, not proactively.
- No repair of the existing project's data (user recreates from scratch).
