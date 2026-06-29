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

# Skill: Generate / refine MDL (semantic models)

Goal: author **structurally correct, governance-complete MDL** — models, columns,
relationships, calculated fields, and metrics — that wren-core can compile and use
to rewrite SQL, grounded in the real Superset schema and valid from the first
token.

## How this differs from stock Wren

Upstream Wren authors **snake_case YAML** under `models/`, then runs
`wren context build` to compile it into a camelCase JSON manifest. **We have no
build step and no YAML.** You author the **native camelCase JSON manifest
directly** through the Copilot's MDL tools, and the engine consumes it as-is. So:

- Field names are **camelCase** (`tableReference`, `joinType`, `isCalculated`,
  `notNull`, `refSql`, `baseObject`) — not `table_reference` / `join_type`.
- `joinType` values are **UPPERCASE** enums (`MANY_TO_ONE`), not `many_to_one`.
- There is no `wren` CLI, no `wren_project.yml`, no `wren memory index`. Schema
  discovery, type grounding, and validation are **tools**, not shell commands.

The reasoning from the Wren workflow still applies — discover the schema, ground
types in the physical catalog, infer relationships from keys, write descriptions
that improve recall, validate before finishing. Only the mechanics change.

## Your tools (the only mechanics you have)

| Need | Tool | Notes |
|------|------|-------|
| See real tables/columns/types | `get_physical_schema` | The authority. Never reference anything absent from it. Multi-schema projects return `{schemas: {schema: {table: {columns, types}}}}` — use the schema each table is under. |
| List existing MDL files | `list_mdl_files` | Path + status (new / unchanged / modified). |
| Read a file's full JSON | `read_mdl_file` | Read before you edit. |
| Refine an existing file | `patch_mdl_file` | **Preferred for edits.** Emit only the changed entities/columns, keyed by name; omitted entities/columns and their `properties` are preserved by the merge. |
| Create / restructure a file | `write_mdl_file` | Full-content overwrite — for a NEW file or moving/removing an entity. Returns structural + physical validation. |
| Remove an entity | `remove_mdl_entity` | Drop a named model/relationship/metric/**calculated** column without re-emitting the file (physical columns can't be removed). Empties → file deleted. |
| Delete a file | `delete_mdl_file` | Whole-file by path. |
| Validate the whole project | `validate_project` | Structural + physical + (when available) engine deep-validate. Run before finishing. |
| Ground edits in operator docs | `search_documents` / `list_documents` / `find_duplicate_documents` | Glossaries, metric formulas, synonyms. |

## Phase 0 — Survey what exists

Call `list_mdl_files`. If models already exist, `read_mdl_file` the relevant ones
before editing. **To change an existing file, prefer `patch_mdl_file`** — emit
only the entities/columns you change, keyed by name; the merge preserves
everything you omit (other models, untouched columns, every `properties` block).
Use `write_mdl_file` only to create a new file or restructure one (move/remove an
entity); there you re-emit the full file and must carry the existing object
forward verbatim (see the `properties` rule below). Do not regenerate a model from
scratch when you mean to extend it.

## Phase 1 — Ground in the physical schema (physical authority)

Call `get_physical_schema` first, every time, before writing a model. For a
single-schema project it returns `{tables, column_types}`:

```json
{
  "tables": { "orders": ["order_id", "customer_id", "status", "total_amount", "discount_amount"],
              "customers": ["customer_id", "full_name", "created_at"] },
  "column_types": { "orders": { "order_id": "INTEGER", "total_amount": "DECIMAL(10,2)" } }
}
```

For a **multi-schema** project it returns the same data keyed by schema, so you
can author the right `tableReference.schema` and same-named tables don't collide:

```json
{
  "schemas": {
    "sales":   { "orders":    { "columns": ["order_id", "customer_id"], "types": { "order_id": "INTEGER" } } },
    "billing": { "invoices":  { "columns": ["invoice_id", "order_id"] } }
  }
}
```

A relationship may join models in **different** schemas (e.g. `sales.orders` ↔
`billing.invoices`); the join condition uses model/column names — the engine
translates to the dialect's schema-qualified SQL.

Rules — these are **code-enforced** by validation; an MDL that breaks them cannot
activate:

- **Never invent tables.** A model's `tableReference.table` must be a key in
  `tables` (else `unknown_table`).
- **Never invent columns.** A physical (non-calculated, non-relationship) column's
  `name` must exist under its table (else `unknown_column`).
- **Never invent or guess types.** Use the type from `column_types`. If
  `column_types` is absent (names-only fallback), copy the catalog type you can
  see; do not fabricate one. A column typed in a different **family** from the
  physical column (e.g. a numeric type on a `VARCHAR`) is rejected as
  `column_type_mismatch`. Type families: temporal (`DATE`/`TIME`/`TIMESTAMP`),
  boolean (`BOOL`), string (`CHAR`/`TEXT`/`VARCHAR`/`UUID`), numeric
  (`INT`/`DECIMAL`/`NUMERIC`/`FLOAT`/`DOUBLE`/`SERIAL`/`MONEY`).
- **Every non-relationship column needs a `type`.** wren-core rejects a typeless
  column with an opaque `missing field 'type'`; our validator catches it first as
  `column_without_type`. There is no default.

## Phase 2 — Author the model (the shape to imitate)

This is a complete, copy-paste-correct manifest in **our** native JSON. It
validates cleanly (structural + physical + project-level relationship resolution).
Imitate its shape exactly — field spellings, the populated `properties`, the
calculated column, the relationship column, and the metric:

```json
{
  "models": [
    {
      "name": "orders",
      "description": "One row per customer order.",
      "tableReference": { "schema": "public", "table": "orders" },
      "primaryKey": "order_id",
      "properties": { "displayName": "Orders", "synonyms": ["purchases", "sales"] },
      "columns": [
        {
          "name": "order_id",
          "type": "INTEGER",
          "notNull": true,
          "properties": { "displayName": "Order ID" }
        },
        {
          "name": "customer_id",
          "type": "INTEGER",
          "properties": { "displayName": "Customer ID" }
        },
        {
          "name": "status",
          "type": "VARCHAR",
          "description": "Lifecycle state: pending, shipped, delivered, cancelled.",
          "properties": { "displayName": "Order Status", "synonyms": ["state"] }
        },
        {
          "name": "total_amount",
          "type": "DECIMAL(10,2)",
          "description": "Order gross total.",
          "properties": { "displayName": "Order Total", "unit": "USD" }
        },
        {
          "name": "discount_amount",
          "type": "DECIMAL(10,2)",
          "properties": { "displayName": "Discount", "unit": "USD" }
        },
        {
          "name": "net_amount",
          "type": "DECIMAL(10,2)",
          "isCalculated": true,
          "expression": "total_amount - discount_amount",
          "description": "Order total after discounts.",
          "properties": { "displayName": "Net Amount", "unit": "USD" }
        },
        {
          "name": "customer",
          "type": "customers",
          "relationship": "orders_customers",
          "properties": { "displayName": "Customer" }
        }
      ]
    },
    {
      "name": "customers",
      "description": "One row per customer.",
      "tableReference": { "schema": "public", "table": "customers" },
      "primaryKey": "customer_id",
      "properties": { "displayName": "Customers" },
      "columns": [
        {
          "name": "customer_id",
          "type": "INTEGER",
          "notNull": true,
          "properties": { "displayName": "Customer ID" }
        },
        {
          "name": "full_name",
          "type": "VARCHAR",
          "description": "Customer full name.",
          "properties": { "displayName": "Name", "synonyms": ["client name"] }
        }
      ]
    }
  ],
  "relationships": [
    {
      "name": "orders_customers",
      "models": ["orders", "customers"],
      "joinType": "MANY_TO_ONE",
      "condition": "orders.customer_id = customers.customer_id"
    }
  ],
  "metrics": [
    {
      "name": "total_revenue",
      "baseObject": "orders",
      "expression": "SUM(net_amount)",
      "description": "Sum of net order amounts."
    }
  ]
}
```

### Field reference (exact camelCase keys)

- **Model**: `name`, `tableReference` `{catalog?, schema, table}` **or** `refSql`,
  `columns[]`, `primaryKey`, `description?`, `properties`. Inside
  `tableReference` the key is the bare `schema` (not `schemaName`). A model with
  neither `tableReference` nor `refSql` warns (`model_without_mapping`); a model
  with no columns warns (`model_without_columns`).
- **Column**: `name`, `type` (required unless it is a relationship column),
  `isCalculated` (bool), `expression`, `relationship`, `notNull` (bool),
  `description?`, `properties`.
- **Relationship**: `name`, `models` (**exactly two** model names), `joinType`
  (enum below), `condition` (the join predicate).
- **Metric**: `name`, `baseObject` (must resolve to a model/view), `expression`
  (an aggregate over the base object), `description?`. A metric with neither an
  `expression` nor a `measure` array computes nothing (warns).

### `joinType` enum (UPPERCASE — only these four)

`ONE_TO_ONE`, `ONE_TO_MANY`, `MANY_TO_ONE`, `MANY_TO_MANY`. Any other value
(including the lowercase Wren form) is rejected as `invalid_join_type`, and
wren-core rejects it as an `unknown variant`.

## Phase 3 — Infer relationships from keys

Map foreign keys (or `<table>_id` naming, confirmed against `get_physical_schema`)
to relationships. Pick `joinType` from the cardinality:

| Direction | `joinType` |
|-----------|-----------|
| FK side → PK side (many rows point at one) | `MANY_TO_ONE` |
| PK side → FK side (one row, many children) | `ONE_TO_MANY` |
| Unique FK (1:1) | `ONE_TO_ONE` |
| Junction table linking two entities | `MANY_TO_MANY` |

The `condition` is the literal join predicate
(`orders.customer_id = customers.customer_id`). A wrong condition produces silent
query errors, so confirm both sides exist in `get_physical_schema`. Relationships
are reusable joins — define them once; do **not** hand-write joins into a model's
`refSql`. A **relationship column** (like `customer` above) carries
`relationship` plus a `type` equal to the related model's name; it is exempt from
the physical-column check, so it does not need to exist in the table.

## Phase 4 — Calculated columns and metrics (what we support)

- **Calculated column**: set `isCalculated: true` and an `expression` over other
  columns of the same model. The `expression` is required (else
  `calculated_requires_expression`). Calculated columns are exempt from the
  physical-column existence and type-mismatch checks (a `CAST` may legitimately
  change family), but every column they reference should be real.
- **Metric**: a reusable aggregation (`SUM`, `COUNT`, …) over a `baseObject`. Use
  metrics for the aggregation questions Wren would answer with a cube — "revenue
  by month", "orders per customer" — so the engine has a named aggregation instead
  of hand-written `GROUP BY` / `DATE_TRUNC`.
- **Cubes are not authored here.** wren-core has a cube struct and our validator
  checks it structurally, but the authoring contract the agent fills
  (`AuthoredManifest`) exposes only `models`, `relationships`, and `metrics`.
  Express aggregation intent as **metrics** (or calculated columns), not cubes.
- **Views** (`{name, statement}`) are named SQL statements that behave like stable
  virtual tables; author one only when a query pattern genuinely needs it.

## Phase 5 — `properties`: carry forward, add, never strip

Every model and column you emit **includes its `properties` block**. This is a
positive rule, not an optional extra:

- `properties.displayName`, `properties.alias`, and `properties.synonyms` are the
  human/colloquial terms that **retrieval** indexes (`_semantic_terms` in
  `schema_retriever.py`) and that **coverage** scores (`_column_fact` in
  `coverage.py`). Without them, a chunk is names+types only and a question like
  "show me purchases" never matches the `orders` model.
- Use the keys the consumers read: `displayName` (string), `alias` (string),
  `synonyms` (list of strings). Free-form governance keys (`unit`, business notes)
  are fine — `properties` allows extra keys.
- **With `patch_mdl_file` the merge keeps existing `properties` automatically** —
  just include the keys you are adding. **When you instead `write_mdl_file` an
  existing model or column, copy its existing `properties` forward verbatim, then
  add to them.** Never drop or empty a key you did not intend to change.

**Why this matters / why you can't rely on validation to catch it:** wren-core
treats `properties` as a tolerated unknown field — dropping it does **not** fail
compile or validation. There is a safety net in `write_mdl_file`
(`_preserve_superset_properties`) that re-injects keys you silently drop, but that
is defense-in-depth. Author the full preserved object yourself so the net never
has to fire.

## Phase 6 — Validate before finishing

Call `validate_project` and resolve every **error** before you stop (warnings are
advisory but read them). `write_mdl_file` also returns per-file validation —
check it after each write. Common errors and fixes:

| Code | Meaning | Fix |
|------|---------|-----|
| `unknown_table` | `tableReference.table` not in the schema | Use a real table from `get_physical_schema`. |
| `unknown_column` | Physical column not in the table | Use a real column, or mark it `isCalculated`. |
| `column_without_type` | Non-relationship column has no `type` | Add the catalog type. |
| `column_type_mismatch` | Type family ≠ physical column's family | Use a type matching the physical column. |
| `calculated_requires_expression` | `isCalculated` without `expression` | Add the expression. |
| `invalid_join_type` | `joinType` not one of the four | Use an UPPERCASE enum value. |
| `relationship_arity` | Relationship `models` ≠ exactly 2 | Reference exactly two models. |
| `unresolved_relationship` / `unresolved_metric_base` | Endpoint / `baseObject` not defined in the project | Define the referenced model, or fix the name. |
| `duplicate_model` / `duplicate_column` | A name repeats | Make names unique. |

## Things to avoid

- Do **not** author snake_case keys (`table_reference`, `join_type`,
  `is_calculated`) — wren-core silently ignores `table_reference` and treats the
  model as having no source. Use camelCase.
- Do **not** invent tables, columns, or types — ground every one in
  `get_physical_schema`.
- Do **not** drop or empty `properties` when you `write_mdl_file` an existing
  entity — retrieval and governance read it and validation won't catch its loss.
  (`patch_mdl_file` preserves it for you — prefer it for edits.)
- Do **not** author cubes — express aggregations as metrics.
- Do **not** finish without a clean `validate_project` (zero errors).
- Do **not** hand-write joins into model SQL — define `relationships` instead.
