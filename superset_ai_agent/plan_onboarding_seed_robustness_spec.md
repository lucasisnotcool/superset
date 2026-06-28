<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Review & Proposal: Onboarding Seed Robustness (column identity + types)

Status: **proposed (review only, no code changed)** · Date: 2026-06-28
Scope: the **internal** onboard pipeline — deterministic seed generation, LLM
overlay, and MDL validation. Explicitly **out of scope**: table selection /
registration before onboarding, and the job/poll/readiness machinery after it.

## 0. TL;DR

The reported onboarding errors are **not LLM hallucinations**. By design (the
"W3" split, [llm_client.py:427-451](integrations/wren/llm_client.py#L427-L451))
structure — model name, column names, column types — is **seeded
deterministically from the Superset catalog**, and the LLM only overlays
descriptions/synonyms ([`_overlay_model_semantics`](integrations/wren/llm_client.py#L1083-L1111)
never touches `name` or `type`). So every error in the report traces to the
**deterministic seed builder** ([`column_to_field`](integrations/wren/mdl_exporter.py#L114-L130))
or to **validation matching**, not to the model.

Two root causes explain all three error classes:

- **RC1 — Identifier rename with no physical back-reference.** `column_to_field`
  renames each column via `_safe_identifier` (`2003` → `_2003`,
  [mdl_exporter.py:119,133-140](integrations/wren/mdl_exporter.py#L133-L140)) but
  emits **no `expression`** mapping the renamed handle back to the real column,
  and stashes the real name in `properties.superset_column_name` which **nothing
  reads back**. Validation then compares the renamed `_2003` against the raw
  physical `2003` and reports *"does not exist"*
  ([mdl_validator.py:546-558](semantic_layer/mdl_validator.py#L546-L558)). Even if
  it passed, wren-core uses `name` as the physical column for non-calculated
  columns, so generated SQL would `SELECT _2003` and fail at the database too.
  → the `birth_france_by_region._2003…_2014` errors.
- **RC2 — Typeless catalog columns propagated verbatim.** `column_to_field` sets
  `type = column.type`; when Superset's `ColumnSummary.type` is `None`
  ([client.py:52-56](integrations/superset/client.py#L52-L56)) `_drop_none`
  removes the field entirely, and wren-core/validation reject it
  ([mdl_validator.py:531-543](semantic_layer/mdl_validator.py#L531-L543)). There
  is **no type-inference fallback**. → the `birth_names.num_california` and
  `FCC_2018_Survey.first_time_developer` *"missing a type"* errors.

Both are deterministic, reproducible, and fixable deterministically — no model
retry needed. The proposal below adds a **round-trip-safe column-identity** path,
a **type-resolution ladder**, and a **single source of truth** shared by the seed
builder and the validator, plus targeted LLM-logic refinements.

---

## 1. The pipeline (as built)

```
onboard_schema_project                      semantic_layer/onboarding.py:59
 ├─ SchemaIndex.from_agent_context(ctx)      mdl_validator.py:63   ← physical truth (raw names, lowercased)
 └─ wren_client.generate_base_model(...)     integrations/wren/llm_client.py:406
      ├─ seed = model_from_dataset(ds)        mdl_exporter.py:78   ← DETERMINISTIC structure
      │     └─ column_to_field(col)           mdl_exporter.py:114  ← name=_safe_identifier, type=col.type
      ├─ llm = _call_model("wren_onboarding") llm_client.py:570    ← semantics only
      ├─ _overlay_model_semantics(seed, llm)  llm_client.py:1083   ← desc/props only; never name/type
      └─ validate_mdl(seed, schema_index)     mdl_validator.py:200 ← RC1/RC2 surface here
   → proposals → onboarding.py writes draft; auto-activates only if valid
```

The architecture is sound: catalog owns structure, model owns meaning. That is
exactly why these failures are deterministic — the seed faithfully reproduces two
**catalog/seed defects** that the W3 guard does nothing to repair.

---

## 2. Root-cause detail

### RC1 — column rename without physical mapping (the `_2003` class)

`birth_france_by_region` has year columns physically named `2003 … 2014` (leading
digit — common in wide/pivoted CSV datasets).

1. `_safe_identifier("2003")` → `"_2003"` (leading-digit guard prefixes `_`).
2. `column_to_field` emits `{"name": "_2003", "type": …, "properties": {"superset_column_name": "2003"}}` — **no `expression`**.
3. `SchemaIndex.from_agent_context` indexes the **raw** physical name lowercased:
   the set contains `"2003"`, not `"_2003"`
   ([mdl_validator.py:71-83](semantic_layer/mdl_validator.py#L71-L83)).
4. `has_column(table, "_2003")` → `"_2003" not in {"2003", …}` → **False** →
   `unknown_column` error ([mdl_validator.py:546-558](semantic_layer/mdl_validator.py#L546-L558)).

The retained `superset_column_name` is **dead metadata** — the only read site of
that key in the whole pipeline is its own write
([mdl_exporter.py:125](integrations/wren/mdl_exporter.py#L125)). And because
wren-core resolves a non-calculated column by `name`, the rename would also
produce invalid SQL at query time, so "just suppress the validation error" is
**not** a correct fix — the column genuinely won't resolve.

Same latent bug for any column whose name contains non-`[A-Za-z0-9_]`
characters (spaces, `%`, `-`, accents) — `_safe_identifier` collapses them to
`_`, diverging from the physical name.

### RC2 — typeless columns (the "missing a type" class)

`birth_names.num_california` and `FCC_2018_Survey.first_time_developer` arrive
with `ColumnSummary.type is None` (the serializer copies `column.type` verbatim,
[client.py:414-423](integrations/superset/client.py#L414-L423); example/CSV
datasets and partially-synced datasets routinely have empty column types).
`column_to_field` → `type=None` → `_drop_none` strips it → `column_without_type`
error ([mdl_validator.py:531-543](semantic_layer/mdl_validator.py#L531-L543)).

Note the seed builder has **two** signals it currently ignores when `type` is
empty: `is_dttm` (already known true/false) and Superset's `type_generic`
(`GenericDataType`: TEMPORAL/NUMERIC/STRING/BOOLEAN) — which isn't even plumbed
into `ColumnSummary` yet.

### Net effect on the user

Per onboarding.py, an invalid model is **written as draft but not activated**,
and the error strings are concatenated into `OnboardingResult.warnings`
([onboarding.py:112-121](semantic_layer/onboarding.py#L112-L121)). So a schema
with any leading-digit or typeless column produces a model the user cannot
activate — onboarding "fails" for that table with an opaque wall of
per-column messages.

---

## 3. Proposed improvements

### I1 — Round-trip-safe column identity (fixes RC1)

Make a renamed column carry its physical mapping, and make validation match on
**physical identity**, not the logical handle.

- **Seed builder.** In `column_to_field`, when `_safe_identifier(col.name) !=
  col.name`, additionally emit the column's **physical reference** so wren-core
  targets the real column. (The exact wren-core field — `expression` with a
  properly-quoted identifier vs. a `columnName`/`refColumn` — must be confirmed
  with a one-off engine compile test; see D-A. The table side already does this
  via `tableReference.table` carrying the raw name while `name` is sanitized.)
- **Validation.** `_validate_column_semantics` should resolve the column's
  **physical name** — from the emitted physical reference / `superset_column_name`
  property, falling back to `name` — and call `has_column` with *that*. This ends
  the dead-metadata situation and removes the false positive.
- **SchemaIndex stays raw** (true physical truth). The matching layer, not the
  index, owns normalization.

> **Decision D-A (identity strategy).** Two coherent options:
> **(a) Keep-raw + quote** — stop sanitizing column names; pass the raw physical
> name as `name` and rely on wren-core quoting. Simplest; removes the whole
> rename-divergence class. Risk: wren-core may reject names that aren't valid
> logical identifiers. **(b) Rename + physical-map** — keep `_safe_identifier`
> for the logical handle but always emit the physical reference + teach
> validation to match physically. More moving parts; preserves clean logical
> names. **Recommendation: (a) if** an engine spike shows wren-core accepts
> quoted arbitrary physical column names; **else (b).** Resolve with the spike,
> not by guessing.

### I2 — Deterministic type-resolution ladder (fixes RC2)

Replace the bare `type = column.type` with an ordered, catalog-grounded ladder,
and plumb the missing signal:

1. `column.type` (raw Superset type string) — unchanged when present.
2. else **`type_generic`** mapped to a concrete wren type
   (TEMPORAL→`TIMESTAMP`, NUMERIC→`DOUBLE`, STRING→`VARCHAR`, BOOLEAN→`BOOLEAN`).
   *Requires adding `type_generic` to `ColumnSummary` + the serializer.*
3. else if `is_dttm` → `TIMESTAMP`.
4. else **unresolved**: keep the column but tag `properties.inferred_type =
   "unknown"` and emit a per-column warning.

The genuinely-unknowable remainder (step 4) is the only place a policy choice
remains:

> **Decision D-B (typeless tail).** **(a) Draft + actionable warning** — do not
> auto-activate; tell the user exactly which columns need a type (recommended:
> correctness over convenience — a wrong type silently breaks metrics/casts).
> **(b) Default-and-activate** — assign `VARCHAR` + warning so the table onboards
> immediately. **Recommendation: (a)**, because steps 2–3 should resolve the
> overwhelming majority deterministically and correctly; reserving (b)'s guess
> for the rare tail avoids silent numeric→string corruption. Make the typeless
> tail a **first-class, Copilot-fixable** state (see I5).

### I3 — One source of truth for name + type derivation

RC1 exists because the **exporter** normalizes names but the **SchemaIndex**
does not — two independent derivations that drift. Extract a single helper
(e.g. `physical_column_identity(col) -> (logical_name, physical_name, resolved_type)`)
used by **both** `column_to_field` and `SchemaIndex.from_agent_context`. This
structurally prevents the divergence class (not just the `_2003` instance) and is
where the I2 ladder lives.

### I4 — Deterministic pre-validation repair (self-heal, not retry)

Because RC1/RC2 are deterministic, add a **repair step inside
`generate_base_model`** that runs the I1/I2 fixes on the seed *before*
`validate_mdl`, rather than surfacing them as warnings. This converts today's
"writes an unactivatable draft" into "writes an activatable model" for every case
the ladder/identity-map can resolve — directly cutting the failure rate the user
is seeing. No extra model calls; pure deterministic transformation.

### I5 — LLM-logic refinements

The structure/semantics split is correct and should stay. Targeted changes:

- **Stop asking the model for structure it cannot influence.** The prompt tells
  the model to re-emit `name`/`tableReference`/`type` exactly
  ([prompts/wren_onboarding.md], rules block), but the overlay discards all of it
  except descriptions/properties. Slim the contract to *semantics keyed by
  column name* (descriptions, synonyms). Benefit: fewer tokens, less chance of a
  malformed structured-output parse failing the whole call, and the prompt stops
  implying the model is responsible for types it can't fix.
- **Do NOT use the LLM to invent missing types as the primary path** — that
  re-introduces structural hallucination for a load-bearing field. The
  deterministic ladder (I2) is strictly better. *If* a future last-resort is
  wanted for the step-4 tail, gate it behind an explicit, constrained
  (enum-typed, low-temperature) call whose output is tagged `inferred_type:
  "model"` and stays **draft** for human review — never auto-activated.
- **Surface the typeless tail to the Copilot as a fix-it action.** With F4 of the
  MDL Lab spec (Copilot drives onboarding), an unresolved-type column is a
  natural reviewable changeset: "3 columns need types — proposed: …". This keeps
  humans/agent in the loop instead of a guessed default.

### I6 — Validation precision

- Existence check matches on physical identity (folded into I1).
- Consider downgrading `unknown_column` to a **warning with a hint** when a
  physical-name property exists but differs (i.e., a likely normalization issue)
  rather than a hard `error`, so a single odd column doesn't block a 200-column
  table. (Secondary to actually fixing the mapping.)

---

## 4. Decision points (consolidated)

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **D-A** | Column identity strategy | (a) keep-raw + quote; (b) rename + physical-map | Spike wren-core first; **(a)** if it accepts quoted physical names, else **(b)**. |
| **D-B** | Truly-typeless columns | (a) draft + actionable warning; (b) default `VARCHAR` + activate | **(a)** — ladder resolves most; don't risk silent numeric→string corruption on the tail. |
| **D-C** | LLM type inference for the tail | (a) never; (b) constrained last-resort, draft-only | **(a)** for now; revisit as a draft-only, tagged last resort if the tail is non-trivial in practice. |
| **D-D** | Slim the onboarding prompt to semantics-only | (a) yes; (b) keep structure echo | **(a)** — overlay already ignores structure; less cost + fewer parse failures. |

---

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Wrong wren-core physical-mapping field for renamed columns (I1) | Confirm with a focused engine-compile test on a leading-digit column **before** shipping; the test is the acceptance gate (D-A). |
| `type_generic` also unknown for some columns | Ladder falls through to the warned/draft tail (D-B); no regression vs. today (still surfaced, now per-column + actionable). |
| Default type corrupts numeric semantics (if D-B(b) ever chosen) | Prefer D-B(a); if defaulting, tag `inferred_type` and keep draft; never auto-activate a guessed type. |
| Slimming the prompt loses useful model-provided structure | The overlay already discards it; behavior is unchanged — this only removes dead instructions. Verify with the existing onboarding snapshot tests. |
| Shared helper (I3) changes both exporter and validator at once | Cover with a round-trip test: every physical column the index knows must satisfy `has_column` for its emitted logical name. |

---

## 6. Tests to add (regression-locking the two classes)

1. **Leading-digit columns:** a dataset with columns `2003…2014` onboards to a
   model where every column passes `has_column` and (engine test) compiles to
   valid SQL referencing the real physical column.
2. **Special-char columns:** `"% growth"`, `"col-name"`, accented names → same
   round-trip guarantee.
3. **Typeless columns with `type_generic`:** `type=None, type_generic=NUMERIC`
   resolves to a numeric wren type and **activates**.
4. **Truly-typeless tail:** `type=None, type_generic=UNKNOWN, is_dttm=False`
   stays **draft** with a per-column actionable warning (D-B(a)).
5. **Round-trip invariant (I3):** for any `AgentContext`, every column the
   `SchemaIndex` indexes is `has_column`-resolvable from the emitted seed.

---

## 7. File touchpoints (proposal — no edits made)

| File:symbol | Change |
|---|---|
| `integrations/wren/mdl_exporter.py::column_to_field, _safe_identifier` | Physical mapping for renamed columns (I1); call the shared identity/type helper (I2/I3). |
| `integrations/superset/client.py::ColumnSummary, _serialize_dataset` | Add `type_generic`; populate from Superset `TableColumn.type_generic` (I2). |
| `semantic_layer/mdl_validator.py::_validate_column_semantics, SchemaIndex` | Match existence on physical identity; host/share the identity helper (I1/I3). |
| `integrations/wren/llm_client.py::generate_base_model` | Deterministic repair pass before `validate_mdl` (I4). |
| `prompts/wren_onboarding.md` | Slim to semantics-keyed-by-column-name (I5/D-D). |
| `semantic_layer/onboarding.py` | Surface the typeless tail as a structured, Copilot-actionable result, not a flat warning string (I5). |
| `tests/unit_tests/superset_ai_agent/` | Add the §6 regression suite. |

---

## 8. Why this is the right altitude

Every fix lands **inside** onboarding (seed builder, overlay, validator), touches
no pre-onboarding (selection/registration) or post-onboarding (job/poll/readiness)
pathway, and preserves the W3 structure-from-catalog / semantics-from-model
invariant that makes the layer trustworthy. It removes two deterministic defect
classes at their source rather than papering over them with model retries or
suppressed validation.
