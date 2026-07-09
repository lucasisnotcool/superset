# MDL activation stability — implementation checklist

Status: IN PROGRESS (2026-07-09)

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

- [ ] **A. Schema-qualified column truth in `SchemaIndex`**
  (semantic_layer/mdl_validator.py)
  - [ ] A1. `from_agent_context`: flat `tables` entry becomes the union across
        datasets sharing a table name (was last-write-wins).
  - [ ] A2. `_apply_loaded`: after updating the qualified map, rebuild the
        flat entry as the union across all schemas containing the table.
  - [ ] A3. `has_column` / `columns_for` / `column_type` with `schema=None` on
        a schema-qualified index: resolve across the table's home schemas
        (any-match / union / unique-agreed type) instead of the collidable
        flat map. Fail-open on ambiguity — never a false "does not exist".
  - [ ] A-tests: same-named table in two schemas; unqualified lookup finds a
        column present in either; reflection order no longer changes results.
- [ ] **B. Deterministic activation gate** (app.py)
  - [ ] B1. Extract `_mdl_referenced_tables_from_contents(contents)` from
        `_mdl_referenced_tables` (same parsing, content-level).
  - [ ] B2. `_enforce_activation_manifest`: before `validate_project_manifest`,
        eagerly reflect ALL manifest-referenced tables budget-exempt via
        `ensure_columns_many(refs, charge_budget=False)` — outcome no longer
        depends on budget order, warm-daemon timing, or prior cache state.
  - [ ] B-tests: activation with reflect budget 0 still verifies columns
        (phantom caught deterministically); pending-table manifest validates
        identically on first and second call.
- [ ] **C. Onboarding activation parity gate**
  (semantic_layer/onboarding.py + app.py route)
  - [ ] C1. `onboard_schema_project(deep_validate=False)`: before
        auto-activating, validate the projected set of valid proposals as ONE
        manifest (`validate_project_manifest`, dedup_models=True, same
        schema_index). Whole-set valid → activate all; invalid → per-file
        fallback gate, offenders stay draft with the error in `warnings`.
  - [ ] C2. `_run_onboarding` passes
        `deep_validate=wren_core_validation_enabled or wren_activation_requires_engine`
        — same deep-gate condition as the manual route. Update the
        docstring's deviation note.
  - [ ] C-tests: an invalid proposal (phantom column / engine-rejected) stays
        draft while valid siblings activate; result warnings name it.
- [ ] **D. Full suite run** `venv/bin/python -m pytest
      tests/unit_tests/superset_ai_agent/ -q` + risk/gap report.

## Non-goals / accepted residual risk

- `columns_unverified` (reflection genuinely failed) still degrades OPEN
  (warning, check skipped, activation passes). Fail-closed would brick
  activation on transient Oracle outages, contradicting the validator's
  documented "must not brick activation" stance. After B, this is the only
  remaining nondeterminism and it requires an actual reflection failure.
- Physical drift (column dropped in Oracle after onboarding) is detected at
  the *next* activation, not proactively.
- No repair of the existing project's data (user recreates from scratch).
