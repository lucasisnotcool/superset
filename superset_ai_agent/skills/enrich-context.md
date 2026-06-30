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

# Enrich Context — Fill the Business-Context Gap

Most business context never lives in a database schema — it lives in handbooks,
glossaries, finance reports, support playbooks, code comments, rules-of-thumb.
You read those uploaded documents and attachments, find what's missing from the
project's MDL, and add the missing semantics back into the MDL — **without ever
disturbing the physical structure or the governance `properties` already there**.

Your sinks are not Wren's. You write MDL JSON files with `write_mdl_file`; you
record the few rule-shaped facts you cannot encode in MDL as **recommended
operator Instructions** in your final summary (you have no tool to write the
instruction store — a human adds those through the UI). There is no `raw/`
folder, no `wren` CLI, no `queries.yml`, no cube authoring.

## Hard rules — READ FIRST

### Universal (apply to both modes)

1. **Only add, never modify or strip existing.** Enrichment is the ideal case for
   **`patch_mdl_file`**: emit a *sparse overlay* — only the models/columns you are
   adding semantics to, keyed by name, carrying only your new keys. The
   structure-preserving merge keeps every physical field (`tableReference`, column
   `name`, `type`, `expression`, `relationship`, `isCalculated`, `notNull`) and
   every existing `properties` key (`displayName`, `alias`, `synonyms`,
   `description`) for you, and additively merges `properties`. These keys feed
   retrieval (`schema_retriever`) and coverage scoring (`copilot/coverage`);
   dropping one degrades both without any error, which is exactly why the overlay
   path never touches what you omit. If you ever fall back to `write_mdl_file`
   (restructuring), copy every physical field and `properties` key forward
   verbatim, then add — be correct from the first token; do not rely on the
   `_preserve_superset_properties` guard.
2. **If existing MDL looks wrong, do not edit it.** A description, relationship,
   type, or rule that looks incorrect goes on the "please fix manually" list in
   Step 9 — never silently corrected.
3. **Every MDL edit must validate.** After any `write_mdl_file`, run
   `validate_project`. If it fails, revert that single change (rewrite the prior
   content) and feed the error back. Never leave the project invalid.
4. **Pre-draft every proposal.** Whether you show a draft (grill) or apply it
   directly (auto-pilot), generate the concrete content — never lazy-ask "what
   should the description say?".
5. **Be explicit about confidence.** In grill mode, open an inference question
   with "I'm guessing — ". In auto-pilot, tag every inference and partial match
   in the Step 9 audit with confidence (high / med / low) and source.

### Grill mode only

6. **One question at a time.** Walk every gap top-down, resolve one decision
   before the next, and propose a recommended answer for each. Prefer searching
   the documents (`search_documents`) over asking when the answer is grounded.
7. **Skip is final for this session.** No pending queue, no nagging. To revisit,
   the user re-runs.

### Auto-pilot mode only

8. **Drop into grill for two cases only.** Interrupt auto-pilot and ask when:
   - (a) **Conflict** — a document and the current MDL disagree.
   - (b) **Routing ambiguity** — you cannot confidently pick a sink (MDL field
     vs recommend-an-Instruction).

   Everything else: propose it directly into the changeset and log to the audit
   list. **This includes new relationships, metrics, and aggregate calculated
   fields** — do *not* suppress or grill them. They are proposals, not deployments:
   every changeset item is reviewed and accepted (or rejected) by a human before
   anything is persisted or activated, so the accept step *is* the review gate.
   Propose the relationships and metrics the schema and documents imply, each
   tagged with confidence (high / med / low) and its source. A semantic layer
   without relationships and metrics is under-enriched; surfacing them as
   review-gated drafts is the job, not a risk to escalate.

## Step 0 — Mode is set by configuration

There is no in-session mode prompt. Your mode is fixed by the deployment flag
`wren_copilot_autopilot_enabled` (default **off**):

- **off → grill mode.** Propose each change and wait for accept / edit / skip.
- **on → auto-pilot mode.** Make your best inferences and apply them, escalating
  only the three cases in Rule 8; end with a confidence-tagged audit.

Treat `MODE = grill | autopilot` accordingly through Steps 4 and 9.

## Step 1 — Read everything before forming an opinion

Read both sides — the source material and the current MDL.

### Source material
- **Attachments** arrive inline in your context under an "## Attached files"
  heading — read them directly.
- **Uploaded documents** are a searchable corpus. `list_documents` to see what's
  available (filename, status, summary); `search_documents(query, k)` to pull the
  passages that define a term, unit, enum, synonym, or metric formula;
  `find_duplicate_documents` to spot redundant or conflicting passages to
  reconcile. Always ground an edit in a real passage and cite it.

### Current MDL
- `list_mdl_files` → every MDL file and its status.
- `read_mdl_file(path)` → the full JSON for each model you may touch (camelCase:
  `models[]` with `columns[]`, `relationships[]`, `metrics[]`, `properties`).
- `get_physical_schema` → the authoritative real tables and, when available,
  their column types. Never attach semantics to a table or physical column absent
  from here.

## Step 2 — Ground-truth probe (parity gap — read, don't sample)

Wren samples `SELECT DISTINCT` from the live DB to settle enum / sentinel / grain
semantics. **We have no live-DB query tool in this toolset.** Settle those
categories from `get_physical_schema` types plus document evidence
(`search_documents` for "values", "code", "means", "0 ="). When neither resolves
it, raise it as a grill question or tag it low-confidence in auto-pilot — do not
guess a meaning.

## Step 3 — Three gap-detection lanes (in your head, no artifact)

Sweep with the gap catalog (Step 5) loaded.

### Lane 1 — Structural coverage (mechanical)
Scan the current MDL: every model has a non-empty `properties.description`? every
non-PK/FK column has a `description`? every model has a `primaryKey`? at least one
`relationship`? Then walk every column/model against the gap-catalog triggers
(enum / unit / null / magic / time tags; soft-delete → default filter; lookalike
tables → canonical-table rule; currency / external-id signals).

### Lane 2 — Claim-diff (documents vs current MDL)
For each document, extract 5–15 atomic claims. Classify each: **covered** (skip) /
**partial** (tighten) / **new** (route to a sink) / **conflict** (escalate to the
user in both modes; never edit existing — surface for manual fix).

### Lane 3 — Inference (your own guesses)
Propose additions the documents did not literally state but that help later
(undefined business terms → synonyms; a repeated named metric → a calculated
field; an undocumented JSON column). In grill mode open every Lane 3 question with
"I'm guessing — "; in auto-pilot tag the audit entry `agent inference`.

## Step 4 — Resolve gaps

Branch on `MODE`.

### Grill mode
For every gap: (1) state the gap + source (quote the document passage for Lane 2);
(2) propose the concrete answer; (3) propose the sink; (4) accept / edit / skip;
(5) on accept write back; (6) on edit apply the wording then write back; (7) on
skip drop it. Search the documents instead of asking whenever you can. One
question at a time.

### Auto-pilot mode
Process every Lane 1–3 finding directly except the two escalations (conflict;
routing ambiguity → grill those). New relationships, metrics, and aggregate
calculated fields are **proposed directly into the changeset**, not escalated — the
human accept step reviews them. For every finding: synthesize the proposal, pick
the sink, write back, `validate_project` immediately after any MDL edit (revert the
single change on failure), append to the audit with confidence + source.

## Step 5 — Gap catalog (the ten business-semantic categories)

Ten categories the schema alone cannot carry. Lane 1 uses the *Trigger* as a
mechanical check; Lane 2 maps each document claim onto a category; Lane 3 proposes
one when the slot is empty and a trigger fires.

**Two sink families in our stack:**
- **MDL `properties` / fields** (categories 1, 2, 3, 5, 6, 7) — you write these
  with `write_mdl_file`.
- **Recommended operator Instruction** (categories 4, 8, 9, 10) — you have no tool
  to write the instruction store, so you surface these as concrete suggested
  Instructions in the Step 9 summary for a human to add through the UI. Where it
  helps retrieval, also fold a plain-language note into the model/column
  `description`.

### Description write format (column-local categories)
Prose first, then one greppable `[tag]` line per category, all inside the
column's `properties.description` string. Keep the prior description text; append.

```json
{
  "name": "status",
  "type": "VARCHAR",
  "properties": {
    "description": "Customer subscription status snapshot at row creation.\n[enum] free=unpaid trial, pro=paid monthly, enterprise=contracted SLA\n[null] NULL = signup not yet completed"
  }
}
```

Use lowercase tag names exactly as below; never append a `[tag]` line if that
category's tag already exists for the column.

| # | Category | Trigger (short) | Our sink |
|---|---|---|---|
| 1 | **Enum value semantics** | low-cardinality VARCHAR/INT code col, no `[enum]`; names like status/type/*_code | `properties.description` → `[enum] A=active, B=banned` |
| 2 | **Unit / scale** | `*_amount/_price/_cost/_qty/_duration/_bytes/_rate`, no `[unit]` | `properties.description` → `[unit] cents (×0.01 = USD)` |
| 3 | **NULL semantics** | nullable col where NULL carries meaning, no `[null]` | `properties.description` → `[null] NULL = never logged in` |
| 4 | **Soft-delete / active filters** | `deleted_at/is_active/is_internal/archived_at` | **Recommend Instruction** (e.g. "`orders` exclude `deleted_at IS NOT NULL` unless asked"); optionally note in model description |
| 5 | **Magic sentinels** | numeric outliers (-1, 0, 9999) with meaning, no `[magic]` | `properties.description` → `[magic] -1 = unknown; 0 = system` |
| 6 | **Synonyms / business aliases** | document term not verbatim in MDL names (ARR, DAU, "patty") | **`properties.synonyms`** (native MDL key, read by retrieval) — colloquial terms; set `displayName`/`alias` for the single canonical label |
| 7 | **Date / time conventions** | DATE/TIMESTAMP col with TZ / event-vs-record / grain ambiguity, no `[time]` | `properties.description` → `[time] UTC; event time; month-end snapshot` |
| 8 | **Cross-system identifiers** | `stripe_*/salesforce_*/*_external_id` or doc maps internal→external | **Recommend Instruction** (External identifiers) + note format/null in description |
| 9 | **Currency / locale** | `currency/fx_rate/original_amount` or doc mentions multi-currency | **Recommend Instruction** (Currency); add `[unit]` note on the amount column |
| 10 | **Canonical table preferences** | lookalike tables (users/users_v3) or doc says "use X not Y" | **Recommend Instruction** (Canonical tables) — MDL cannot enforce table choice |

Category 6 is the standout adaptation: Wren routes synonyms to `instructions.md`,
but our `properties.synonyms` is read directly by `schema_retriever._semantic_
terms`, so synonyms belong **in the MDL** where retrieval will actually use them.

**Out of scope** (do not draft here): org-wide PII/privacy policy (a single
`[pii] mask in non-prod` note on a flagged column is fine); performance hints;
row-level access; schema corrections (surface on manual-fix, never edit).

## Step 6 — Aggregation decision tree (cube → our supported sinks)

Wren's default sink for a named aggregation is a **cube**. In our stack the
enrichment authoring contract does **not** author cubes (they exist in the schema,
validator, and structure-preserving merge, but only as hand-edited pass-through —
see parity note). Map the decision tree to what you *can* author:

```
Document defines a named metric / aggregation (ARR, WAU, churn, a ratio)
├── Aggregation over one base model (SUM/COUNT/AVG/ratio, with group-by dims)
│   → AGGREGATE CALCULATED FIELD (preferred — engine-validated) on that model,
│     isCalculated:true, expression over existing columns.
│     Use metrics[] (baseObject + measure/dimension) only when a calculated
│     field cannot express it; metrics are not deeply planned.
├── Pure row-level expression (amount_with_tax = amount * 1.1, no grouping)
│   → CALCULATED FIELD (isCalculated:true, expression, type).
├── Needs a JOIN / window / CTE across models
│   → add the RELATIONSHIP, then a calculated field crossing it (name the
│     relationship); if that can't express it, surface on manual-fix.
└── An existing metrics[] entry already covers it
    → surface on "please fix manually"; do not add a duplicate.
```

Use a document's explicit formula verbatim and cite the source passage in the
field's `description`. Filtered ratios belong inside the measure expression
(`SUM(CASE WHEN … )` or `FILTER (WHERE …)`) — there is no separate filter field.
In **auto-pilot**, propose new relationships / metrics / aggregate calculated
fields directly into the changeset (review-gated; tag confidence + source) — do not
grill them. In **grill** mode, propose each and wait, one at a time.

## Step 7 — Routing & writeback

| Finding | Sink | How |
|---|---|---|
| Model/column description, `displayName`, `alias`, `synonyms` | MDL `properties` | `write_mdl_file` with the full preserved object; for enum/unit/null/magic/time append a `[tag]` line to `properties.description` |
| Calculated / aggregate field | MDL column `isCalculated:true` + `expression` + `type` | `write_mdl_file` |
| Relationship | MDL `relationships[]` (`name`, two `models`, `joinType`, `condition`) | `write_mdl_file` |
| Metric (only when a calc field can't express it) | MDL `metrics[]` (`baseObject`, measure/dimension) | `write_mdl_file` |
| Default filter / external-id / currency / canonical-table rule | **Recommended operator Instruction** | Surface in Step 9 — no write tool; a human adds it via the UI |

After **every** `write_mdl_file`, run `validate_project`. On failure: rewrite the
prior content (revert the single change), show/log the error, and re-grill (grill)
or mark "revert: validation failed" (auto-pilot). All MDL keys are camelCase;
`type` is required on every column including calculated fields.

## Step 8 — Session finalize

No `wren context build` step — `write_mdl_file` persists the change and
re-embedding of updated semantics happens server-side. End with a final
`validate_project` so the session closes on a valid project.

## Step 9 — Summary

Print a tight report:
- Added counts by sink and by `[tag]`.
- **Recommended operator Instructions** — the concrete rule-shaped facts
  (default filters, external-id maps, currency rules, canonical-table choices)
  for a human to add, each with its source.
- **Please fix manually** — contradictions / suspected-wrong existing fields you
  did not edit (Rule 2).
- Grill extra: skipped count. Auto-pilot extras: confidence-tagged inference
  audit, validation applies/reverts, items escalated to grill.

## Things to avoid

- Do not write any tracking artifact (`gaps.yml`, `state.yml`); the session lives
  in conversation.
- Do not modify or strip any existing MDL field or `properties` key — only
  add/append. Surface mismatches on the manual-fix list.
- Prefer `patch_mdl_file` with a sparse overlay (only the entities/columns you are
  enriching); the merge preserves every omitted physical field and `properties`
  key. Only when you fall back to `write_mdl_file` must you re-emit the full
  preserved object so nothing is lost.
- Do not attach semantics to a table or physical column absent from
  `get_physical_schema`.
- Do not auto-resolve a conflict between a document and current MDL — escalate in
  both modes.
- Do not present a Lane 3 inference as quoted from a document.
- Do not append a `[tag]` line when that category tag already exists for the
  column.
- Do not invent an instruction-writing tool — surface rule-shaped facts as
  recommended Instructions instead.
- Do not author cubes or write `queries.yml` / NL→SQL pairs — neither is a sink
  in this path (see parity notes).
- Do not skip `validate_project` after a write; never leave the project invalid.
- In auto-pilot, do not grill or suppress new relationships / metrics / aggregate
  calculated fields / **views** — propose them into the (review-gated) changeset.
  Only conflicts and routing ambiguity (Rule 8a/8b) interrupt for a question. A
  new view is a public, named interface, so flag low-confidence ones in the Step 9
  audit — but still propose it (the human accept step is the review gate).

## Views (an authoring sink)

When a document describes a **reusable query pattern** — a named analysis, a
recurring JOIN across models, a windowed/CTE computation — propose a **view**
(`views/<name>.json`, `{name, statement, properties}`) with `write_mdl_file`.
Write the `statement` as **semantic SQL over model names** (cross-schema-correct
via each model's `tableReference`; never hand-qualify physical schemas). Semantic
views handle JOIN/WINDOW/CTE fine, so translate a raw-SQL pattern by swapping
physical table names for model names rather than copying it verbatim. **Verify each
referenced column against the model before writing it** (`read_mdl_file` /
`get_physical_schema`) — don't infer a column name from prose. Author each view in
its own `views/<name>.json` so one bad view never blocks the others at activation.
Always give `properties.description` — it doubles as a recall example. Run
`validate_project` after writing; the engine rejects a semantic view that references
an unknown column or model. (Native `dialect` views are reserved for a later phase —
not enabled yet.)

## Parity notes (Wren → ours)
- **instructions.md → instruction store, but agent-read-only.** The agent has no
  tool to write the store; rule-shaped facts are *recommended* in Step 9 for a
  human to add. Synonyms, which Wren routes to instructions.md, go natively into
  `properties.synonyms`.
- **Views** ARE an authoring sink here (parity with Wren's enrich-context view
  proposals): semantic views over models, plus an optional native (`dialect`)
  escape hatch for raw-SQL patterns. Validated by the engine at activation.
- **Cubes** are schema/validator/merge-supported but not in the authoring
  contract → aggregations map to calculated fields / metrics.
- **`queries.yml` / `wren memory`** have no enrichment writeback — confirmed
  NL→SQL pairs are the query agent's domain, not this skill's.
- **Live-DB distinct-value probe** has no tool here — settle enum/sentinel/grain
  from `get_physical_schema` types and document evidence.
- **`raw/`, `wren` CLI, project selection, memory detection** are removed —
  uploaded documents + inline attachments replace `raw/`; the active project's
  MDL files replace `wren context`.
