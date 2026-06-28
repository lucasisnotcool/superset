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

Status: **IMPLEMENTED** (I1–I6, D-A/B/C/D) · Date: 2026-06-28
Re-anchored: 2026-06-28 after commit `1a46b8f639` (see §0a).
As-built status + residual risks: see §10.
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

## 0a. Re-scan & re-anchor (post-`1a46b8f639`)

Another agent shipped **`1a46b8f639` "Extend MDL and MDL Copilot with
cross-schema capability"** — which implemented `plan_copilot_onboarding_spec.md`
(Copilot-driven onboarding), **not** this seed-robustness spec. Confirmed by
re-scan of the working tree (git clean):

- **Seed builder untouched → RC1 & RC2 fully live.** `mdl_exporter.py` is
  unchanged: `column_to_field` still does `name=_safe_identifier(column.name)`
  ([mdl_exporter.py:119](integrations/wren/mdl_exporter.py#L119)), `type=column.type`
  ([:120](integrations/wren/mdl_exporter.py#L120)), no `expression`. Both root
  causes reproduce exactly as described.
- **`ColumnSummary` still lacks `type_generic`** ([client.py:52-56](integrations/superset/client.py#L52-L56))
  — the I2 fallback source is still unplumbed.
- **Validation matching unchanged** — `_validate_column_semantics` still passes the
  *renamed* MDL `name` straight into `has_column`
  ([mdl_validator.py:625-627](semantic_layer/mdl_validator.py#L625-L627)); the
  `superset_column_name` property is still never read back.
- **New, usable primitive:** the commit added `SchemaIndex.columns_for(table,
  schema)` ([mdl_validator.py:154](semantic_layer/mdl_validator.py#L154)) plus a
  `search`/`_table_match_score` discovery helper. `columns_for` is the natural
  accessor for the I1/I3 physical-identity match — build on it rather than adding
  a parallel one.
- **Positive interplay with the shipped Copilot onboarding.** That spec ungates
  the Copilot and adds reviewable tools (`find_tables`, `propose_metric`,
  `add_project_schema`). This makes I5's "surface the typeless tail as a
  Copilot-actionable fix-it changeset" concrete — the tail becomes a
  `propose_*`-style proposal, not a dead-end warning.

**Net:** the diagnosis stands verbatim; only line anchors drifted (+87 lines in
`mdl_validator.py`). Anchors below and in §7 are refreshed to the current tree.

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

## 4. Decision points — restated, with sourced recommendations & implications

Summary table first; full reasoning, sources, and codebase/functionality
implications follow.

| ID | Decision | Recommendation (confidence) |
|---|---|---|
| **D-A** | How to represent columns whose physical name isn't a clean identifier | **(b) Rename + `expression` physical-map** — Wren's documented mechanism (high; gate on a one-off engine compile test) |
| **D-B** | What to do with columns that have *no* resolvable type after the ladder | **(a) Fail-closed: draft + per-column warning**, never auto-default (high) |
| **D-C** | Use the LLM to infer missing types? | **(a) Never in the seed path**; optional draft-only, tagged last resort (high) |
| **D-D** | Slim the onboarding prompt to semantics-only? | **(a) Yes** — overlay already discards structure (high) |

### D-A — Column identity for non-identifier physical names

**Restate.** Physical columns named `2003`, `% growth`, `col-name`, accented
text, etc. are not legal bare identifiers. Today `_safe_identifier` rewrites them
to a sanitized handle (`_2003`) with **no link back to the real column**, which
breaks validation matching *and* the SQL wren-core would generate. We must choose
how a renamed column keeps a physical mapping.

- **(a) Keep-raw + quote.** Stop sanitizing; emit `name` = the raw physical name
  and rely on the engine quoting it in generated SQL.
- **(b) Rename + `expression` physical-map.** Keep the clean logical `name`
  (`_2003`) and emit `expression = "\"2003\""` (the quoted physical column) as the
  physical reference.

**Best practice / sources.** Wren's own modeling docs state you "use an
expression to refer to the physical column and **rename it by setting the `name`
as the alias** of this column" — i.e. (b) is the **vendor-sanctioned** pattern,
not a workaround ([Wren — Model](https://docs.getwren.ai/oss/engine/guide/modeling/model),
[Wren — What is MDL](https://docs.getwren.ai/oss/concepts/what_is_mdl)). It also
matches the general semantic-layer norm (dbt/LookML) of separating a clean
**logical** name from the **physical** identifier and quoting the latter.

**Recommendation: (b)**, gated on a one-off wren-core compile test to lock the
exact quoting/escaping. Rationale: it is what Wren documents; it preserves clean
logical names, which matter because NL→SQL retrieval and the Copilot key on column
*names* — (a) would pollute those with quotes/odd characters and risks wren-core
rejecting a non-identifier as a model column name.

**Implications.**
- *Codebase:* `column_to_field` emits a conditional `expression` only when
  `_safe_identifier(name) != name`; the I3 shared helper produces
  `(logical_name, physical_ref)`. Validation's existence check resolves the
  **physical** column (via the expression / `superset_column_name`) before
  `has_column`, reusing the new `columns_for` accessor. Scope: `mdl_exporter.py`,
  `mdl_validator.py::_validate_column_semantics`, the shared helper. Localized;
  no schema/migration change.
- *Functionality:* leading-digit / special-char columns onboard, validate, and
  query correctly; the false "does not exist" disappears; logical names stay
  retrieval-friendly. Cost: slightly more verbose MDL (expression on renamed
  columns only). Residual risk: wrong quote/escape → engine error — retired by the
  compile test + §6 regression cases.

### D-B — Columns with no resolvable type after the ladder

**Restate.** After the I2 ladder (raw `type` → `type_generic` → `is_dttm`), a few
columns may still have no type. Block them (draft + warning) or guess a default
and activate?

- **(a) Fail-closed:** keep the model **draft**, emit a precise per-column
  warning, never auto-activate a guessed type.
- **(b) Fail-open:** assign a default (e.g. `VARCHAR`) and activate so the table
  onboards immediately.

**Best practice / sources.** Industry consensus is **fail-closed**: silent type
coercion (e.g. forcing a numeric column to string) corrupts data and produces
hard-to-debug downstream failures; engines like Delta Lake **fail the write** on
schema mismatch rather than guess, and Azure Data Flow's quiet cast-to-null is
cited as the anti-pattern ([DEV — Schema Validation Passed, So Why Did My
Pipeline Fail?](https://dev.to/sumit_agarwal_9af86ae465b/schema-validation-passed-so-why-did-my-pipeline-fail-2coj),
[Databricks — schema enforcement on write](https://docs.databricks.com/aws/en/error-messages/error-classes)).

**Recommendation: (a).** The ladder — especially `type_generic` — should resolve
the overwhelming majority *correctly*; reserving the rare genuine-unknown for a
human/Copilot confirmation is far safer than defaulting `num_california` to
`VARCHAR` and silently breaking every metric over it.

**Implications.**
- *Codebase:* the ladder lives in the I3 helper; onboarding.py *already* keeps
  invalid models as draft, so the blocking path needs no new branch — only a
  richer, structured warning (tag `properties.inferred_type="unknown"`) so the
  Copilot can pick it up. Scope: helper + `onboarding.py` warning shape.
- *Functionality:* clean-catalog tables onboard fully; only tables with a
  genuinely missing type wait on a quick type confirmation. Correctness is
  preserved; no silently-wrong aggregations. Trade-off: those specific tables
  aren't immediately queryable — acceptable, and strictly better than wrong
  results.

### D-C — LLM-inferred types for the tail

**Restate.** Should the LLM fill a missing `type` as a fallback?

**Best practice / sources.** Text-to-SQL/semantic-layer research is consistent:
LLMs hallucinate **schema-level** facts — nonexistent columns, invented
types/metrics — and the mitigation is to **ground structure in the catalog** and
use the model for *semantics* + validation-feedback loops, never as the source of
structural truth ([Wren — Reducing Hallucinations in Text-to-SQL](https://www.getwren.ai/post/reducing-hallucinations-in-text-to-sql-building-trust-and-accuracy-in-data-access),
[arXiv 2512.22250 — Hallucination Detection for Text-to-SQL](https://arxiv.org/abs/2512.22250)).
This *is* the codebase's existing W3 split.

**Recommendation: (a) never in the seed path.** A `type` drives casts and
aggregations — exactly the load-bearing structural field the architecture
deliberately keeps the model away from. If a last resort is ever wanted, gate it
as a **constrained** (enum of wren types), low-temperature, **draft-only**,
`inferred_type="model"`-tagged suggestion a human approves — HITL, not
auto-applied.

**Implications.**
- *Codebase:* no change now; the overlay stays semantics-only. A future opt-in is
  an isolated flagged call outside the deterministic seed path.
- *Functionality:* preserves the core trust property ("the layer never invents
  structure"); the typeless tail is handled deterministically + HITL.

### D-D — Slim the onboarding prompt to semantics-only

**Restate.** The prompt tells the model to re-emit `name`/`tableReference`/`type`
exactly, but `_overlay_model_semantics` discards everything except
descriptions/properties. Keep the structure echo or drop it?

**Best practice / sources.** Structured-output reliability guidance: request only
the fields you consume — large structural echoes raise parse-failure rate,
latency, and token cost for no benefit; schema-aware designs feed rich metadata
*in* but keep the model's *output* minimal ([Wren — Reducing Hallucinations](https://www.getwren.ai/post/reducing-hallucinations-in-text-to-sql-building-trust-and-accuracy-in-data-access)).
Here the structure echo is provably dead output (the overlay drops it).

**Recommendation: (a) slim it** to "semantics keyed by column name"
(descriptions, synonyms).

**Implications.**
- *Codebase:* edit `prompts/wren_onboarding.md` and shrink
  `proposal_response_schema()`; overlay logic is unchanged (already reads only
  desc/props). Existing onboarding snapshot tests confirm identical structure
  output.
- *Functionality:* fewer structured-output parse failures (today a parse failure
  drops *all* enrichment to the `_PROVIDER_FALLBACK_WARNING` path), lower
  cost/latency, clearer contract. Zero change to structure (already
  deterministic).

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

> Anchors re-verified against the tree at commit `1a46b8f639`.

| File:symbol (current line) | Change |
|---|---|
| `integrations/wren/mdl_exporter.py::column_to_field` (114), `_safe_identifier` (133) | Physical `expression` mapping for renamed columns (I1/D-A); call the shared identity/type helper (I2/I3). |
| `integrations/superset/client.py::ColumnSummary` (52), `_serialize_dataset` (418, `type=column.type` @422) | Add `type_generic`; populate from Superset `TableColumn.type_generic` (I2). |
| `semantic_layer/mdl_validator.py::_validate_column_semantics` (585; existence check 625-637), `SchemaIndex` (46; `from_agent_context` 64; `has_column` 143; `column_type` 151; **`columns_for` 154 — reuse**) | Match existence on **physical** identity via `columns_for` (I1/D-A); host the shared identity helper (I3). |
| `integrations/wren/llm_client.py::generate_base_model` (406), `_overlay_model_semantics` (1083) | Deterministic repair pass before `validate_mdl` (I4); semantics-only overlay unchanged. |
| `prompts/wren_onboarding.md` + `semantic_layer/mdl_authoring.py::proposal_response_schema` | Slim to semantics-keyed-by-column-name (I5/D-D). |
| `semantic_layer/onboarding.py` (validate 97; draft branch 112; auto-activate 122) | Surface the typeless tail as a structured, Copilot-actionable result, not a flat warning string (I5; ties into the shipped `propose_*` tools). |
| `tests/unit_tests/superset_ai_agent/` (alongside the new `test_mdl_validator.py`) | Add the §6 regression suite. |

---

## 8. Why this is the right altitude

Every fix lands **inside** onboarding (seed builder, overlay, validator), touches
no pre-onboarding (selection/registration) or post-onboarding (job/poll/readiness)
pathway, and preserves the W3 structure-from-catalog / semantics-from-model
invariant that makes the layer trustworthy. It removes two deterministic defect
classes at their source rather than papering over them with model retries or
suppressed validation.

---

## 10. As-built status & residual risks (2026-06-28)

**Delivered (all items, in sequence, each test-locked):**

- **I2 — type ladder + `type_generic` plumbed.** New
  `semantic_layer/column_identity.py` (`safe_identifier`, `physical_column_reference`,
  `resolve_column_type`). `ColumnSummary.type_generic` added + populated in
  `LocalSupersetClient._serialize_dataset` via `_generic_type_name`.
  *(test_column_identity.py — 11 cases.)*
- **I1/I3/D-A — round-trip-safe identity.** `column_to_field` emits a quoted
  `expression` physical-map when sanitizing renames a column, tags
  `inferred_type`, and resolves type via the ladder; `_safe_identifier` now
  delegates to the shared helper. `mdl_validator._validate_column_semantics` +
  `_type_mismatch_message` resolve the **physical** name (`superset_column_name`)
  before `has_column`/type lookups. Overlay also falls back to the physical name.
  *(test_onboarding_seed_robustness.py — leading-digit, special-char, round-trip
  invariant.)*
- **I4 — end-to-end.** `onboard_schema_project` now **activates** a table with
  leading-digit + generic-typed columns (the original failure), and keeps a
  truly-typeless table **draft** with a per-column warning. *(Onboarding-level
  tests with the deterministic client + `InMemoryMdlFileStore`.)*
- **I5/D-D — semantics-only authoring.** `AuthoredColumn.type` optional; prompt
  `wren_onboarding.md` slimmed to descriptions/synonyms. *(Typeless authored
  column parses; overlay applies without an authored type.)*
- **I5/D-B/D-C — fail-closed tail.** Unresolved types stay untyped +
  `inferred_type=unknown`; never guessed, never model-invented.

**Verification:** full `pytest tests/unit_tests/superset_ai_agent/` → **963
passed, 11 skipped**; ruff + ruff-format clean on touched files; mypy introduces
**zero** new errors (the 36 reported are pre-existing baseline in
`persistence/models.py`, `wren_core_validator.py`, and the other agent's
`SchemaIndex.search`, identical with these changes stashed).

**Residual risks & gaps (for the next session):**

1. **R-A (engine compile, the D-A spike — NOT yet run).** The `expression`
   physical-map is implemented per Wren's documented "expression-as-physical-
   rename", and validates + unit-tests green, but it has **not** been compiled by a
   live wren-core engine. Risk: wren-core's exact quoting/escaping for a
   non-calculated column with a quoted `expression` differs from `"name"`. *Action:*
   run one real onboard of `birth_france_by_region` (or `test_mdl_compile`/
   `wren_core_validator` against a renamed column) before relying on it in prod.
2. **R-B (enrichment new-column type).** Making `AuthoredColumn.type` optional is
   safe for onboarding (type is deterministic) and for enrichment of *existing*
   columns (base type preserved), but a **genuinely new** column introduced via
   enrichment may now arrive typeless → it fails physical validation and stays
   draft (fail-closed, not silent). If new-column enrichment is common, consider a
   separate strict schema for that path rather than the shared `AuthoredColumn`.
3. **R-C (`type_generic` only on the local adapter).** The fallback is populated by
   `LocalSupersetClient`; a remote/HTTP Superset adapter that doesn't send
   `type_generic` degrades to the `is_dttm`→`TIMESTAMP`→untyped tail. Acceptable
   (fail-closed) but means the typeless-tail rate is adapter-dependent.
4. **R-D (snapshot path types).** `SchemaIndex.from_snapshot` remains names-only,
   so on a Superset outage the cross-family type-mismatch check still degrades to
   off. Unchanged by this work; noted for completeness.

**User-expectation ↔ UI gaps (post-onboarding surface, out of this scope but
flagged):**

- **G-1 — the typeless tail is invisible in the UI.** Backend now tags
  `properties.inferred_type=unknown` and emits a per-column warning, but the
  onboarding result still surfaces as a **flat warning string**
  ([onboarding.py:112-121](semantic_layer/onboarding.py#L112-L121)); the editor has
  no per-column "needs a type" affordance. The shipped Copilot tools
  (`propose_metric`, etc.) make a fix-it changeset feasible — a natural next task to
  turn the tag into a one-click/agent fix. Until then a user sees "draft, can't
  activate" with the reason buried in a toast/warning list.
- **G-2 — `inferred_type=generic` is silent.** A column typed from its generic
  family (e.g. `num_california`→`DOUBLE`) activates with no UI signal that the type
  was inferred rather than catalog-declared. Low risk (family is correct), but a
  small "inferred" badge would set expectations honestly.
- **G-3 — renamed columns show their logical handle.** `2003` displays as `_2003`
  in the model/editor; the physical name lives in `properties.superset_column_name`
  and the `expression`. Correct, but a user scanning the model may not recognize
  `_2003`. A display-name affordance (use `superset_column_name` as the label)
  would close the recognition gap.
