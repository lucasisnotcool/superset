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

# Skill: Onboarding a schema → base MDL

This skill turns a permission-filtered Superset database schema into a documented,
queryable **base MDL** layer. Onboarding is the lifecycle step that *stabilizes*
the semantic layer: it seeds one model per dataset, validates each against the
live schema, writes drafts, and auto-activates the valid ones so the Copilot and
the query agent have something governed to edit and read.

Unlike Wren's CLI onboarding, ours has **no `wren` command, no `.env`, no
connection profiles, and no credential collection** — the database is already a
connected Superset datasource and RBAC is enforced by Superset. So this skill is
not about wiring a connection; it is about the agent-side discipline that keeps
the generated MDL correct-by-construction: structure from the catalog, semantics
from the model, properties carried forward, drafts not silent deploys.

## Mode of operation — READ THIS FIRST

**One step per round-trip.** Each numbered step below is its own turn: explain
briefly, do *only* what the step needs, confirm the result, move on. Never collect
information for future steps upfront.

- **Never query data before base MDL exists.** A model that is not in the layer
  is not queryable. Onboarding builds the layer first; the query agent (`usage`)
  comes after. Use `get_physical_schema` to *read structure*, never to run a data
  query during onboarding.
- **Never invent, rename, or retype a physical table or column.** Tables, column
  names, and column `type`s come from the catalog (`get_physical_schema`) and the
  deterministic dataset seed — never from you. You may add `description`s,
  `properties` (`displayName`/`alias`/`synonyms`), **calculated** columns
  (`"isCalculated": true` + `expression`), `relationships`, and `metrics`.
- **Never bypass RBAC.** Only the permission-filtered datasets are in scope. If a
  table is not in the filtered context, it does not exist for this run — do not
  reach around the filter.
- **Drafts, not deploys.** Onboarding writes drafts and auto-activates only the
  models that pass structural + physical validation. The Copilot never activates;
  promotion of anything that failed validation is a human decision.
- Wait for each operation to finish, report its outcome in plain language, then
  continue.

## Preflight (read-only — confirm scope, no scaffolding yet)

Onboarding's analogue of Wren's environment checks is a **readiness + scope**
check. Report findings as a short bullet list, then continue — do not start
writing files yet.

1. **Confirm the target.** Onboarding operates on one
   `(semantic project → database, catalog, schema)`. Confirm the selected
   project and schema with the user; do not guess across schemas.
2. **Confirm readiness state.** The backend gates Copilot edits on a readiness
   status: `empty` (nothing onboarded yet — the normal start), `indexing` (an
   onboarding job is running — wait, don't double-run), `ready` (active MDL files
   already exist — onboarding may have run; ask before regenerating), or `failed`
   (the last onboarding job errored — surface the reason). Match your action to
   the state.
3. **Confirm there are permission-filtered datasets.** If the filtered context is
   empty, there is nothing to onboard — say so and stop rather than emitting an
   empty layer.

## Step 1 — Seed structure from the catalog (deterministic, not invented)

Base structure is generated **deterministically** from the permission-filtered
datasets, one MDL model per dataset. For each dataset the seed sets, in native
camelCase:

- `name` — the model name (sanitized from the dataset).
- `tableReference` — `{ "schema": <schema>, "table": <table> }` (a `catalog` key
  is allowed but Superset datasets set only schema + table).
- `columns` — one per physical column, each with `name`, `type` (REQUIRED —
  copied from the catalog), and `isCalculated: false`.
- `properties` (model) — seeded with `superset_dataset_id`, `superset_database_id`,
  and `source: "superset_ai_agent"`.
- `properties` (column) — seeded with `superset_column_name`, and `is_time: true`
  for temporal columns.

You do not retype any of this from memory. Read it from `get_physical_schema` /
the seed and treat it as authoritative. Your job in the next step is **semantics**.

## Step 2 — Overlay business semantics (the only thing you author)

Onto each seeded model the agent overlays *meaning* — never structure:

- A concise, factual **`description`** on every model and every column, grounded
  in the names and types (and any operator docs). Do not fabricate semantics you
  cannot infer.
- Under each model's / column's **`properties`**, add the retrieval-facing keys
  that downstream consumers actually read: **`displayName`**, **`alias`**, and
  **`synonyms`** (the colloquial terms a question like "patty" or "revenue" must
  match). These feed schema retrieval and coverage scoring; without them a chunk
  is names-and-types only and your semantics never influence retrieval.
- **Carry every existing `properties` key forward verbatim.** When you re-emit a
  model or column, copy the seeded `superset_*` / `is_time` keys and any prior
  `displayName`/`alias`/`synonyms` first, *then* add to them. Never drop or empty
  a `properties` block — the structure-preserving guard is defense-in-depth, not
  your license to overwrite.

This is the positive form of the "add, never strip" rule: emit the full preserved
object, not a partial overlay.

## Step 3 — Relationships & metrics (only what's grounded)

- Infer a **`relationship`** only when column naming strongly implies a foreign
  key (e.g. `*_id` matching another model's key, or a confirmed join). Set `name`,
  `models` (the two model names), `joinType` ∈
  `ONE_TO_ONE | ONE_TO_MANY | MANY_TO_ONE | MANY_TO_MANY`, and `condition`. Never
  guess a join you cannot justify.
- Preserve any provided **`metrics`** (`name`, `baseObject`, `expression`). Do not
  invent metrics absent from the input.

## Step 4 — Validate, then draft / activate

Each generated model is validated structurally and **against the live schema**. A
hallucinated table or column makes that draft non-activatable — it is still
written (so a human can correct it) but it stays `draft` with a warning rather than
being silently dropped.

- Valid models are auto-activated so the layer is immediately queryable.
- Invalid models remain drafts pending human fix.
- Report the count of models written, how many activated, and every validation
  warning in plain language.

After activation the layer is indexed for retrieval and the readiness gate flips
to `ready` — that is what unblocks the Copilot and the query agent. There is no
manual reindex step for the agent to run.

## Scaffold / workspace layout

There is no `mkdir` and no `wren context init`. The folder structure is implied by
each file's path; `write_mdl_file` creates whatever folders the path names. Mirror
the layout:

- `models/<table>.json` — one base model per physical table (the onboarding seed).
- `relationships.json` — foreign-key relationships between models.
- `views/<name>.json` — named SQL views (added later, not in base onboarding).

Cross-model business rules (default filters, naming conventions, glossary
synonyms) belong in the project's **instructions** / uploaded documents, not in
invented MDL fields.

## Cross-skill routing

| Trigger | Skill |
|---------|-------|
| Connected schema, no base MDL yet | `onboarding` (this skill) |
| Base MDL exists; deepen models / add calculated columns, relationships, metrics | `generate-mdl` |
| Need business meaning from operator documents (units, enums, glossary, formulas) | `enrich-context` |
| Layer is `ready`; user wants to ask questions | `usage` (the query agent) |

There is no SaaS-connector or bundled-demo path here — onboarding always operates
on an already-connected, RBAC-filtered Superset datasource.

## On error

- Empty filtered context → nothing to onboard; tell the user and stop.
- Validation warnings → name the offending model/column and what failed; the draft
  is preserved for correction, not lost.
- `failed` readiness → surface the last onboarding job's error; do not blindly
  re-run.
- A request to query before the layer is `ready` → explain that base MDL must exist
  and validate first, then route to `usage` once active.
