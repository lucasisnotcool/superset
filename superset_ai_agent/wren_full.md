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

# Wren Integration — Native-Manifest Rebuild Plan

This document **supersedes** the previous "Wren Full-Parity Implementation Plan"
in this file. The earlier plan's *runtime* work (semantic engine, retrieval,
memory, intent) is real, test-verified, and **carried forward** — it is
summarized in [§2](#2-foundation-already-in-place). What we are reversing is the
MDL **authoring/storage representation**: it was built as a bespoke *snake_case
YAML dialect* that the LLM hand-writes as free text and that we then translate
into wren-core's real shape. That dialect is the source of the activation
failures and of ongoing maintenance debt.

**The corrected direction:** the agent should speak **wren-core's native
manifest shape (camelCase JSON) end-to-end** — LLM output, storage, validation,
and the engine all use one vocabulary, with **zero translation**. We undo the
dialect and the YAML substrate together, and rebuild the authoring path on the
engine's own data model.

Status legend: `[TODO]` not started · `[WIP]` in progress · `[DONE]`
source-backed and test-verified · `[BLOCKED]` waiting on a decision/dependency.

---

## 1. Why we are doing this (the debt, stated plainly)

The goal was a fully Wren-integrated AI workflow: onboard a schema → draft MDL →
enrich from documents → activate → use the semantic layer to plan SQL. The
runtime half works. The **authoring** half was built on two wrong substrates,
stacked:

1. **The LLM emits MDL as a raw YAML string** —
   [`integrations/wren/llm_client.py:57-68`](integrations/wren/llm_client.py#L57-L68),
   `_ProposedMdlFile.yaml: str`. The `format_schema` we hand the model
   ([`llm_client.py:305`](integrations/wren/llm_client.py#L305)) constrains only
   the envelope; the MDL inside `yaml` is unconstrained generated text.
2. **We invented a snake_case dialect** (`table_reference`, `join_type`,
   `is_calculated`, `ref_sql`, …) that is *not* what wren-core consumes, then
   built a translation layer to convert it to the engine's camelCase shape at
   compile time — [`mdl_compile.py:164-247`](semantic_layer/mdl_compile.py#L164-L247).
   This dialect is a second source of truth that can (and does) drift from the
   engine's, and the translation step is where structural fields get silently
   dropped.

### 1.1 The mechanism (source-backed)

| Observed error (code) | Severity | Root cause | Source |
| --- | --- | --- | --- |
| `duplicate_model: <name>` (×6) | error | Enrichment is fed the **entire current MDL** and asked to "improve" it, so the model **re-emits all existing models into a new file**. Activation merges siblings + new file → every name appears twice. | context: [`llm_client.py:183`](integrations/wren/llm_client.py#L183); merge: [`app.py:1043`](app.py#L1043); dup check: [`mdl_validator.py:249-256`](semantic_layer/mdl_validator.py#L249-L256) |
| `model_without_mapping` (×6) | warning | The re-emitted models **dropped `table_reference`** during the free-text rewrite. | [`mdl_validator.py:706-715`](semantic_layer/mdl_validator.py#L706-L715); prompt rule it ignored: [`prompts/wren_enrichment.md:21-22`](prompts/wren_enrichment.md#L21-L22) |
| `wren_core_error: missing field 'type' …` | error | A column was emitted **without a `type`**. It passes structural validation (no `type` check) and physical validation (existence only), then the dialect translator `column_to_camel` sets `type=None` and **`_drop_none` silently deletes the key**, so wren-core's serde rejects it with an unreadable byte offset. | drop: [`mdl_compile.py:190-200`](semantic_layer/mdl_compile.py#L190-L200), [`:246-247`](semantic_layer/mdl_compile.py#L246-L247); no structural check: [`mdl_validator.py:285-355`](semantic_layer/mdl_validator.py#L285-L355) |
| `yaml_parse_error` (colons) | error | The model writes a value containing a colon / `-` / `#` unquoted and PyYAML throws. Pure artifact of LLM-hand-written whitespace-sensitive text. | [`mdl_validator.py:737-757`](semantic_layer/mdl_validator.py#L737-L757) |

### 1.2 What actually fixes the *class* of failure

Three moves, applied together:

1. **The LLM never authors serialized text.** It returns a typed object; we
   serialize. Deletes the colon class; makes `type` un-omittable.
2. **There is one vocabulary: wren-core's native camelCase manifest.** No
   snake_case dialect, no translation layer. The thing we store *is* the thing
   the engine validates. Deletes the silent-drop class and the drift risk.
3. **JSON, not YAML, everywhere — no dual path.** JSON is what the engine eats;
   it gives the editor real line/col diagnostics and a publishable JSON Schema.

---

## 2. Foundation already in place

Built and test-verified under the prior plan; **representation-agnostic** — these
consume the *compiled manifest* (already camelCase JSON) or in-memory dicts, not
authoring text. They carry forward unchanged.

| Area | What exists | Source |
| --- | --- | --- |
| **Compiled manifest is already native JSON** | `CompiledManifest.to_engine_manifest()` / `to_base64_json()` emit camelCase JSON; the materializer writes `mdl.json` + `manifest.json`. The live engine test proves this shape loads into **wren-core 0.7.1**. | [`mdl_compile.py:68-92`](semantic_layer/mdl_compile.py#L68-L92); [`wren_materializer.py:87-115`](semantic_layer/wren_materializer.py#L87-L115); live proof: `test_semantic_engine.py` (skipif-gated rewrite) |
| **SemanticEngine seam** (`WrenCoreEngine`, degrade-closed) | Consumes base64(JSON) manifest. | [`semantic_layer/engine/`](semantic_layer/engine/) |
| **Retriever / Embedder seams** (keyword + embedding, LanceDB, index-once) | Operate on `SchemaItem` chunks + vectors. | [`schema_retriever.py`](semantic_layer/schema_retriever.py) |
| **Memory learning loop** (durable NL→SQL, dedup, decay) | Operates on stored examples. | [`memory_store.py`](semantic_layer/memory_store.py) |
| **Intent classification + correction loop** (gated) | Graph-level. | `graph.py` / `conversation_graph.py` |

**Key consequence:** the camelCase manifest shape is *already* the agent's
engine contract and is *already* validated against the installed wren-core. This
rebuild does not invent a new target — it **promotes the proven compile output
to be the authoring/storage shape too**, and deletes everything upstream that
spoke a different language.

---

## 3. Decisions (with reasoning)

| # | Decision | Reasoning | Alternative rejected |
| --- | --- | --- | --- |
| D1 | **One vocabulary: wren-core's native camelCase manifest, end-to-end.** Author, store, validate, and execute on the same shape. Delete the snake_case dialect and the `*_to_camel` translation layer. | A translation layer between two shapes is a second source of truth that drifts and where fields get dropped — it *caused* the missing-`type` failure. The native shape is already proven against wren-core 0.7.1 ([§2](#2-foundation-already-in-place)). Speaking it directly is "build the integration properly." | *Keep snake_case authoring, only flip YAML→JSON* (the prior plan's D3): retains the dialect, the translator, and the drift/drop risk. Explicitly reversed by this revision (promoting former DF1 to the spine). |
| D2 | **JSON is the only serialization.** No dual YAML/JSON. | YAML's sole benefit was human editing, which a JSON+Schema editor does better (autocomplete, inline validation, real line/col). The engine wants JSON. | Dual support doubles the parse/merge/materialize surface and keeps the fragile path alive. |
| D3 | **Abandon all existing MDL data.** ~~No migration;~~ reset stores. **(Updated:** a one-time *purge* migration `0004` and a manual script shipped after the fact — see [§8 risk #1](#8-implementation-outcomes--empirical-findings-2026-06-23). Not a backfill; it deletes legacy non-JSON rows + derived state so a deployed instance self-resets on upgrade.**)** | Per the owner: nothing generated so far is critical; a backfill would carry dialect+YAML quirks into the new world for no value. | A YAML-snake→JSON-camel backfill — unjustified for throwaway data. |
| D4 | **LLM returns a typed Pydantic tree in native shape; we serialize to canonical JSON.** `type` required; `joinType` and granularity enums; `isCalculated`/`notNull` typed booleans. | Structured output is the right tool for "fill these fields"; emitting native field names means its output drops straight into storage with no mapping. Makes the colon and missing-`type` classes impossible at the source. | A free-text JSON string from the LLM — trades YAML fragility for JSON fragility (unescaped quotes, trailing commas). Must be a typed object. |
| D5 | **Onboarding seeds structure deterministically from `SchemaIndex`; the LLM fills only semantics.** | Our ground truth is Superset's permission-filtered datasets — we already hold authoritative table/column/`type` in `SchemaIndex` ([`mdl_validator.py:53-88`](semantic_layer/mdl_validator.py#L53-L88)). This is how Wren avoids the class: structure from the catalog, semantics from the model. | LLM authors structure too — the failure mode we are removing. |
| D6 | **Enrichment is a delta/patch contract, applied in place.** LLM returns only new/changed entities keyed by name; we update the target file. Merge-time dedup as a safety net. | Kills `duplicate_model` (no whole-document re-emission) and the `tableReference` loss (untouched models are not rewritten). | Whole-document rewrite — the current cause of the duplicate cascade. |
| D7 | **A golden round-trip test pins the native shape to the installed wren-core wheel.** One test loads a representative authored manifest straight into `SessionContext` and asserts a real rewrite. | With the dialect gone, the authored shape *is* the engine contract — so the contract must be anchored by an executable test, not a hand-maintained mapping comment. CI runs it against the pinned wheel. | Relying on the prose note in `wren_core_validator.py` that says "re-verify the mapping" — that note exists *because* a hand-maintained mapping is unsafe; D1 removes the mapping, D7 removes the note's reason to exist. |
| D8 | **File extension/content-type = `.json` / `application/json`; reject non-JSON.** | One representation end-to-end. | Keeping `.yaml` accepted contradicts D2. |

---

## 4. The plan — workstreams in dependency order

**W1 is the spine** (the native-shape rebuild). W2–W4 depend on the canonical
schema W1 establishes; W5 hardens; W6 removes the debt.

> **Implementation status (audited 2026-06-23): W1–W6 all `[DONE]`.** Backend
> `pytest tests/unit_tests/superset_ai_agent` **308 passed, 4 skipped** (re-run at
> audit; +3 over the original 305 are the D2 purge tests); frontend
> `jest src/SqlLab/components/AiAgentPanel` **37 passed**; ruff + prettier clean on
> changed files; D7 golden contract green against wren-core 0.7.1. Grep gates:
> zero `import yaml`/`yaml.safe_load`/`yaml.safe_dump` and zero YAML/`proposed_yaml`
> in non-test source/frontend. **Precision note:** the "snake_case dialect" gate is
> about *serialized/stored* keys — the JSON the engine eats is camelCase. Python
> Pydantic field names in `mdl_schema.py`/`mdl_authoring.py` are idiomatic
> snake_case but alias to camelCase via `alias_generator=to_camel`, so they match a
> naive grep without being dialect leakage. One intentional `.yaml/.yml` literal
> survives in `llm_client.py:447` — it *strips* legacy extensions and forces
> `.json`, the opposite of YAML support. The four field errors are now structurally
> impossible — see [§6](#6-acceptance-for-the-whole-rebuild). Residual risks in
> [§5](#5-sequencing--risk); empirical findings appended at the end of this file.

### W1 — Native camelCase manifest as the single MDL shape `[DONE]`

Promote the proven compile output to be the authoring/storage shape; delete the
dialect and the translation layer.

- **Canonical typed spec** — rewrite [`mdl_schema.py`](semantic_layer/mdl_schema.py)
  from snake_case to wren-core native: `Column{name, type (required),
  isCalculated=False, expression?, relationship?, notNull=False}`,
  `Model{name, tableReference{catalog,schema,table}, refSql?, columns[],
  primaryKey?}`, `Relationship{name, models[2], joinType (enum), condition}`,
  `Metric{name, baseObject, expression}`, `Cube{name, measures, dimensions,
  timeDimensions, hierarchies}`, `Manifest{catalog, schema, dataSource, models,
  relationships, views, metrics, cubes}`. Keep `extra="allow"` for `properties`.
  Field names mirror the camelCase the live engine test already accepts.
- **Delete the translation layer** — remove `model_to_camel`, `column_to_camel`,
  `relationship_to_camel`, `view_to_camel`, `metric_to_camel`, `cube_to_camel`,
  `_drop_none` from [`mdl_compile.py:164-247`](semantic_layer/mdl_compile.py#L164-L247).
  `compile_manifest` becomes a thin **merge into the manifest envelope** (already
  builds `catalog/schema/dataSource` + lists) — no per-entity mapping.
  `to_wren_core_manifest` in [`wren_core_validator.py:118-134`](semantic_layer/wren_core_validator.py#L118-L134)
  collapses to pass-through.
- **Validator reads native fields** — flip every snake_case read in
  [`mdl_validator.py`](semantic_layer/mdl_validator.py): `table_reference`→`tableReference`,
  `ref_sql`→`refSql`, `join_type`→`joinType`, `is_calculated`→`isCalculated`,
  `base_object`→`baseObject`, `time_dimensions`→`timeDimensions`,
  `primary_key`→`primaryKey`. Container keys → native `("models",)` only
  ([`mdl_schema.py:43`](semantic_layer/mdl_schema.py#L43)).
- **Parse = JSON** — `_parse_yaml`→`_parse_json`
  ([`mdl_validator.py:737-757`](semantic_layer/mdl_validator.py#L737-L757)); use
  `json.JSONDecodeError.lineno/colno` for editor annotations (real line/col, an
  upgrade over YAML's `problem_mark`). The merged re-dump in
  `validate_project_manifest` ([`:191-201`](semantic_layer/mdl_validator.py#L191-L201))
  uses `json.dumps`.
- **Merge/materialize = JSON, native keys** — `_merge_yaml`/`yaml.safe_load`
  ([`mdl_compile.py:128-152`](semantic_layer/mdl_compile.py#L128-L152)) and
  `_merge_mdl_yaml` ([`wren_materializer.py:140-155`](semantic_layer/wren_materializer.py#L140-L155))
  → `json.loads` over native keys; per-file target extension `.yaml`→`.json`
  ([`wren_materializer.py:79`](semantic_layer/wren_materializer.py#L79)).
- **File store** — extension gate `.yaml/.yml`→`.json`
  ([`mdl_files.py:472-473`](semantic_layer/mdl_files.py#L472-L473)).
- **Schemas/contract** — `MdlContentType`→`Literal["application/json"]`;
  `MdlFile.content_type` default; `MdlEnrichmentProposal.proposed_yaml`→
  `proposed_content`; docstrings ([`schemas.py:179`, `:235-280`](semantic_layer/schemas.py#L179)).
- **D7 golden test** — `test_native_manifest_contract.py`: build a representative
  authored manifest, base64-load it into `SessionContext`, assert a real
  rewrite. CI-gated against the pinned wren-core wheel.

**Acceptance:** `test_mdl_compile.py`, `test_mdl_validator.py`,
`test_wren_materializer.py` rewritten to native-JSON fixtures; the D7 golden
test passes; a colon-bearing `description` round-trips create→validate→compile→
materialize losslessly; a `.yaml` upload is rejected.

### W2 — Typed LLM output in native shape `[DONE]`

- Reuse the W1 typed spec as the LLM `format_schema`. Replace
  `_ProposedMdlFile.yaml: str` with the native `ProposedManifest`
  ([`llm_client.py:57-68`](integrations/wren/llm_client.py#L57-L68)); **we**
  serialize via `json.dumps(doc.model_dump(by_alias=True), indent=2)`.
- Rewrite [`prompts/wren_onboarding.md`](prompts/wren_onboarding.md) and
  [`prompts/wren_enrichment.md`](prompts/wren_enrichment.md) to describe the
  native JSON object (camelCase field names) — drop all "return YAML" language.
- Deterministic fallbacks in
  [`integrations/wren/client.py:452-490`](integrations/wren/client.py#L452-L490)
  build the typed native object and `json.dumps` it (replacing `yaml.safe_dump`).

**Acceptance:** `test_llm_wren_client.py` — (a) a colon-laden description
serializes to valid JSON that validates clean; (b) a response missing a column
`type` is rejected by the Pydantic schema before compile.

### W3 — Deterministic structural seeding from `SchemaIndex` `[DONE]`

- In `generate_base_model`
  ([`llm_client.py:218-271`](integrations/wren/llm_client.py#L218-L271)),
  pre-build each model's `name`, `tableReference`, and `columns[].{name,type}`
  from `superset_context.datasets`; the model fills only `description`, synonyms
  (`properties`), `metrics`, `relationships`. Output is **merged onto** the
  seeded skeleton, never replaces it — structure and `type` cannot be lost.

**Acceptance:** `test_llm_wren_client.py` — with a stub model returning empty
descriptions, every column still carries its real `type` and a valid
`tableReference`.

### W4 — Delta/patch enrichment + in-place targeting `[DONE]`

- Enrichment passes existing models as **read-only reference context** (names +
  table refs + column names — not full re-emittable bodies) and requests **only
  new/changed entities** keyed by name
  ([`llm_client.py:166-207`](integrations/wren/llm_client.py#L166-L207)).
- Apply the delta to the **target file in place**; do not create a colliding
  sibling. Safety net in `_enforce_activation`
  ([`app.py:1019-1055`](app.py#L1019-L1055)): on a re-declared model that is
  byte-identical or a strict superset, prefer the newer, drop the older.

**Acceptance:** `test_app.py` — enriching a project with an existing active model
yields an activatable manifest (no `duplicate_model`); untouched models retain
`tableReference`.

### W5 — Validation hardening + friendly engine errors `[DONE]`

- **Structural `type` check** — `_validate_columns`
  ([`mdl_validator.py:285-355`](semantic_layer/mdl_validator.py#L285-L355)) emits
  `column_without_type` with line context before deep validation. (The silent
  `_drop_none` path is *gone* with the translation layer — W1 — so this is the
  remaining belt-and-braces.)
- **Map wren-core serde errors** — in `validate_engine_manifest`
  ([`wren_core_validator.py:102-114`](semantic_layer/wren_core_validator.py#L102-L114))
  translate "missing field `X`" / "unknown variant" into field-anchored
  messages instead of byte offsets.

**Acceptance:** `test_mdl_validator.py` / `test_wren_core_validator.py` — a
type-less column fails structurally with a readable message; a synthetic serde
error renders as guidance.

### W6 — Debt cleanup `[DONE]`

- Remove `import yaml` from all modules (§4.7); drop `PyYAML` from
  `requirements-ai-agent.txt`.
- Frontend: editor language mode `yaml`→`json` with the published JSON Schema
  attached; upload `accept=".json"`; `api.ts` `content_type:'application/json'`,
  `proposed_yaml`→`proposed_content`; update import-dialog copy and all
  fixtures/tests. Field labels in any MDL form move to camelCase native names.
- Docs: `.env.example` comments, `wren.md`/`wren_model.md` references to "YAML
  MDL files" and the snake_case spec.
- Final grep gate: zero `yaml|safe_load|safe_dump|\.ya?ml` and zero
  snake_case dialect keys (`table_reference|join_type|is_calculated|ref_sql|
  base_object|time_dimensions`) outside test archaeology.

**Acceptance:** repo-wide grep clean; `jest src/SqlLab/components/AiAgentPanel`
and full `pytest tests/unit_tests/superset_ai_agent` green.

### 4.7 What gets deleted vs. flipped (rebuild checklist)

Source: full-tree grep `2026-06-23`; boxes confirmed against source at audit
(2026-06-23). `[x]` = done & verified.

**Deleted outright (the dialect + translation layer)**
- [x] `mdl_compile.py` — `model_to_camel`, `column_to_camel`, `relationship_to_camel`, `view_to_camel`, `metric_to_camel`, `cube_to_camel`, `_drop_none` (W1) — *grep-verified gone*
- [x] `mdl_compile.py` — `_merge_yaml` snake-key collection; `MODEL_CONTAINER_KEYS` alias `semantic_models` (W1) — *now `("models",)` only, `mdl_schema.py:56`*
- [x] `wren_core_validator.py` — `to_wren_core_manifest` mapping body → pass-through; the "re-verify the mapping" prose note (W1/D7)

**Rewritten in place (snake→native, YAML→JSON)**
- [x] `mdl_schema.py` — typed spec to camelCase native via `alias_generator=to_camel` (W1)
- [x] `mdl_validator.py` — all field reads camelCase + `_parse_json` + `column_without_type` (W1/W5)
- [x] `mdl_validation.py` — re-export rename (W1)
- [x] `mdl_files.py` — `validate_mdl` calls; `.json` gate (W1)
- [x] `wren_materializer.py` — JSON native keys; per-file target `.json` (W1)
- [x] `onboarding.py` — `proposal.proposed_content` (W1)
- [x] `schemas.py` — `MdlContentType`, `MdlFile.content_type`, `MdlEnrichmentProposal.proposed_content`, docstrings (W1)
- [x] `app.py` — `model.json` default, `proposal.proposed_content`, `dedup_models=True` (W1/W4)
- [x] `integrations/wren/llm_client.py` — `_ProposedMdlFile`→`mdl_authoring`, `_active_mdl_json`, `.json` paths (W2/W3/W4)
- [x] `integrations/wren/client.py` — `import yaml`/`yaml.safe_dump` removed, `.json` paths (W2)
- [x] `integrations/wren/mdl_exporter.py` — `tableReference`/`isCalculated` native camelCase (W2)
- [x] `integrations/wren/http_client.py` — `proposed_content`, `.json` paths (W1/W2)
- [x] `persistence/models.py` — `content_type` default `application/json` (W1)

**Frontend**
- [x] `api.ts` — `content_type:'application/json'`, `proposed_content` field (W6)
- [x] `SemanticLayerEditor/SemanticLayerImportDialog.tsx` — `accept`, path suffix, field, copy (W6)
- [x] `SemanticLayerEditor/index.tsx` + editor — ACE mode `json` + JSON default template (W6; live JSON-Schema validation is DF2, see [§9](#9-df2--as-you-type-schema-validation-assessment--plan))
- [x] `AiAgentPanel/**/*.test.{ts,tsx}` — fixtures migrated to JSON/`proposed_content` (W6)

---

## 5. Sequencing & risk

1. **W1 first**, and within W1, land the **D7 golden round-trip test early** — it
   is the executable contract that replaces the deleted translation layer. Build
   the native schema against the test, not against the old mapping comments.
2. **W5 alongside W1** — cheap, prevents missing-`type` surviving even before W2.
3. **W2 → W3 → W4** — LLM contract, deterministic seeding, delta contract; each
   independently testable and shippable.
4. **W6 last** — drop deps and flip the frontend once the backend is native-JSON.

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Native shape diverges from the installed wren-core across versions | Med | D7 golden test pinned to the wheel + CI gate; a wren-core bump that breaks it fails CI loudly instead of silently at activation. |
| Deleting the translation layer breaks a caller that assumed snake_case | Med | The only consumers are `compile_manifest` and `to_wren_core_manifest` (both rewritten in W1) and the validator (rewritten); §4.7 + the W6 snake-key grep gate are the backstop. |
| Frontend/back drift during the flip | Med | Land W1 + W6 frontend types together; `application/json` is the contract gate. |
| Structured-output support varies by provider (Ollama vs OpenAI) | Med | `format_schema` already drives the envelope today ([`llm_client.py:305`](integrations/wren/llm_client.py#L305)); native MDL is the same mechanism. Keep the deterministic fallback (W2). |

---

## 6. Acceptance for the whole rebuild

Done when **all four field errors are structurally impossible**, demonstrated by
tests, *and* the dialect is gone:

1. `yaml_parse_error` — impossible: LLM never emits serialized text (W2); colons
   round-trip (W1).
2. `wren_core_error: missing field 'type'` — impossible: `type` is schema-required
   (W2), structurally checked (W5), and the silent-drop translator is deleted (W1).
3. `duplicate_model` — impossible under normal enrichment: delta + in-place +
   dedup (W4).
4. `model_without_mapping` — impossible for seeded/untouched models: structure
   from `SchemaIndex`; untouched models never rewritten (W3/W4).

Plus: **one vocabulary** — repo grep clean of YAML usage (`import yaml`,
`safe_load`/`safe_dump`, `proposed_yaml`) and of *serialized* snake_case dialect
keys; the stored/engine JSON is camelCase (W6). (Python Pydantic field identifiers
remain idiomatic snake_case but alias to camelCase, so they are out of scope for
this gate — see the §4 precision note.) The D7 golden test is green against the
pinned wren-core wheel; full backend + AiAgentPanel suites green; `.env.example`
and docs updated.

---

## 7. Deferred options (recorded, not scheduled)

| ID | Option | Why deferred |
| --- | --- | --- |
| DF2 | Publish the native JSON Schema for **ACE** (not Monaco — see [§9](#9-df2--as-you-type-schema-validation-assessment--plan)) as-you-type validation | UX win that JSON+native-shape enables; assessed in §9 (~1–1.5 days), not yet implemented. |
| DF3 | Regenerate-on-validation-failure loop in the **modeling** client (Wren-style) | After W2–W5, residual MDL-authoring failures are rare; add only if telemetry shows need. (Note: a *SQL-planning* correction loop already exists, gated by `WREN_ENGINE_MAX_CORRECTION_RETRIES` — `graph.py:741`, `conversation_graph.py:1433`; DF3 is the distinct authoring-side analogue.) |

*(Former DF1 — "author directly in camelCase, drop the compile hop" — is now the
W1 spine and no longer deferred.)*

---

## 8. Implementation outcomes & empirical findings (2026-06-23)

The rebuild (W1–W6) is implemented and test-verified. Suites (re-run at audit
2026-06-23): `pytest tests/unit_tests/superset_ai_agent` **308 passed, 4 skipped**
(was 305 before the +3 D2 purge tests);
`jest src/SqlLab/components/AiAgentPanel` **37 passed**; ruff + prettier clean on
changed files. The 4 skips are environment-gated (wren-core-absent inverse,
lancedb-absent inverse, opt-in live smoke) — meaningful, not blind.

### What each error class became (acceptance, proven)

| Field error | Now | Test |
| --- | --- | --- |
| `yaml_parse_error` (colons) | **Impossible** — LLM returns a typed object; we serialize. A colon-laden description round-trips. | `test_mdl_validator`, `test_llm_wren_client` |
| `wren_core_error: missing field 'type'` | **Caught structurally** with a readable, field-anchored message (`column_without_type`) before the engine; the silent `_drop_none` translator is deleted; serde errors that still reach the engine are mapped friendly. | `test_mdl_validator::test_column_without_type_*`, `test_wren_core_validator::test_friendly_engine_error_*` |
| `duplicate_model` | **Superseded, not erroring** — activation dedups re-emitted models (newest wins, with an info); enrichment targets the existing file in place. | `test_mdl_validator::test_dedup_*`, `test_semantic_layer_api::test_activation_dedups_*` |
| `model_without_mapping` | **Impossible for seeded/untouched models** — onboarding structure is seeded from `SchemaIndex`; invented columns are dropped. | `test_llm_wren_client::test_generate_base_model_seeds_*`, `test_semantic_layer_api::test_onboard_seeding_ignores_*` |

### Empirically verified against wren-core-py 0.7.1 (the wheel, not the docs)

- camelCase native manifest from `compile_manifest` **loads + rewrites SQL** (D7
  golden: `test_native_manifest_contract.py`).
- A column missing `type` is rejected with `missing field 'type'` — reproduced
  the production failure from first principles.
- A snake_case `table_reference` is **silently ignored** by the engine (treated
  as "no source"), proving the dialect was a real divergence, not cosmetic.
- **wren-core requires `type` on *calculated* columns too** (verified) — so the
  structural `type` check applies to all non-relationship columns, and a prior
  test fixture that omitted it was itself engine-invalid (now fixed).

### Residual risks / gaps (dev intent vs. implementation, user intent vs. UI)

1. **Abandoned data (D2) — RESOLVED.** Auto-purge ships as migration
   [`0004_purge_legacy_yaml_mdl`](persistence/migrations/versions/0004_purge_legacy_yaml_mdl.py):
   it runs on startup when `AI_AGENT_RUN_MIGRATIONS=true` and deletes legacy
   (non-`application/json`) MDL file rows plus the derived semantic-layer
   versions/cache, nulling `current_version_id` so projects re-materialize.
   Native JSON rows, projects, and documents are preserved; a fresh install
   purges nothing. For out-of-band/inspection use, the same logic is in
   [`scripts/purge_legacy_mdl.py`](scripts/purge_legacy_mdl.py)
   (`python -m superset_ai_agent.scripts.purge_legacy_mdl [--apply]`). Tests:
   `test_purge_legacy_mdl.py`.
2. **Weak-provider structured output — SURFACED (F5).** The typed schema is deep
   (nested manifest); providers that don't honor `json_schema` well (some Ollama
   models) fail the typed parse and fall back to the deterministic (seeded,
   description-only) proposal. Correctness holds; richness degrades. The fallback
   is by design, but it is **no longer silent**: when the model is invoked and
   returns nothing usable, both `propose_mdl_from_document` and
   `generate_base_model` attach `_PROVIDER_FALLBACK_WARNING` to the proposal, and
   the import dialog renders proposal `warnings` as an info `Alert`
   (`test_llm_wren_client::test_*_fallback_warning`, `SemanticLayerImportDialog`
   test "surfaces enrichment proposal warnings"). The user now sees that a
   deterministic draft was substituted and why.
3. **Enrichment delta (W4) — STRENGTHENED to a per-entity patch (F6).** In-place
   targeting no longer requires exactly one active file: `_patch_target`
   (`integrations/wren/llm_client.py`) maps each overlay model to the active file
   that owns it and **merges** the overlay into that file's full content
   (`_merge_manifest_sections`, preserving the file's untouched models/relationships/
   metrics/views/cubes), so a multi-file project is patched in the owning file
   rather than spawning a colliding sibling. It also generalizes the single-file
   case (a brand-new model is appended to the lone active file). Only a genuinely
   ambiguous overlay — one that spans *several* files — falls back to the model's
   path, where the activation dedup net still guarantees activatability. Tests:
   `test_llm_wren_client::test_propose_mdl_patches_owning_file_among_many`,
   `test_propose_mdl_falls_back_when_overlay_spans_files`,
   `test_propose_mdl_appends_new_model_to_single_active_file`. The endpoint stays
   single-proposal (no breaking API change); patching *across* multiple files in
   one enrichment remains the only unhandled case, by design.
4. **Cube/metric shape pinned to wren-core (RM2) — RESOLVED (F4).** The cube
   model was originally built on a Cube.dev-style mental model (granularity,
   hierarchy levels) that wren-core does not have. Verified against
   wren-core-py 0.7.1, the engine's cube requires `baseObject` plus
   measures/dimensions/timeDimensions each carrying `{name, type, expression}`
   (`hierarchies` is an engine *map*, not a list). `MdlCube` now carries
   `baseObject`, the structural validator promotes the engine's hard requirements
   to **errors** (`cube_without_base`, `cube_measure_without_type/expression`,
   `cube_entry_without_type/expression`) with readable messages, and the
   speculative granularity/hierarchy-level checks were removed. Golden round-trips
   pin a metric and a well-formed cube into a live `SessionContext`, and prove a
   typeless cube measure is engine-rejected (`test_native_manifest_contract.py`).
   The agent still does not *author* cubes; this hardens the path for hand-edited
   MDL and replaces the engine's opaque serde byte-offset with a field-anchored
   error before activation.
5. **CI wren-core gate (F1) — HARDENED.** The D7 golden + engine tests are
   skipif-gated locally but the AI-agent CI job
   ([`superset-ai-agent.yml`](../.github/workflows/superset-ai-agent.yml)) installs
   the wheel, asserts it imports, then runs `pytest -m requires_wren_core
   --strict-markers` and **fails if any of those tests skip** — so a wheel that
   stops resolving, or a refactor that drops the marker, breaks CI loudly instead
   of silently dropping engine coverage. The gated tests carry a shared
   `requires_wren_core` marker (`tests/unit_tests/superset_ai_agent/wren_core_markers.py`)
   that stacks the marker + a local skipif so CI and dev stay in lockstep.
6. **UI alignment:** the editor is now JSON (`language="json"`, JSON default
   template, `.json` upload), but it is **ACE, not Monaco** — so live JSON-Schema
   validation is not a built-in toggle. ACE shows JSON *well-formedness* errors
   via its worker; schema-aware as-you-type validation (DF2) requires a custom
   annotations pipeline. Today the editor validates on save via the backend. Full
   DF2 assessment + plan in [§9](#9-df2--as-you-type-schema-validation-assessment--plan).

---

## 9. DF2 — as-you-type schema validation (assessment + plan)

**Goal:** surface MDL schema errors (missing `type`, bad `joinType`, wrong shape)
in the editor *as the user types*, instead of only after they save and the
backend validates.

### Findings (source-backed)

- The MDL editor is **ACE**, via `AceEditorProvider`
  ([`superset-frontend/src/core/editors/AceEditorProvider.tsx`](../superset-frontend/src/core/editors/AceEditorProvider.tsx)),
  not Monaco. So there is **no built-in JSON-Schema diagnostics option** (Monaco's
  `json.jsonDefaults.setDiagnosticsOptions({schemas})` does not exist here).
- ACE's `json` mode already flags **syntax** errors (well-formedness) through its
  worker. It does **not** validate against a schema (required fields, enums, types).
- `AceEditorProvider` exposes an **`annotations` prop** + a `setAnnotations`
  handle that map `EditorAnnotation[]` → ACE gutter annotations. This is the hook
  DF2 would drive — no provider change needed.
- **Ajv 8.20** (the standard JSON-Schema validator) is already resolvable in the
  frontend (transitive). We already produce the native schema server-side
  (`MdlManifest.model_json_schema()` / `proposal_response_schema()`), so there is
  a single source of truth to expose.

### Plan (ACE + Ajv + position mapping)

1. **Expose the schema once (backend).** Add a tiny `GET
   /agent/semantic-layer/mdl-schema` returning `MdlManifest.model_json_schema()`
   so the frontend validates against the *same* shape the engine enforces (no
   drift). ~20 lines, no auth-sensitive data.
2. **Validate on change (frontend).** In `SemanticLayerEditor`, on a debounced
   (~250 ms) `onChange`: `JSON.parse` the buffer; on parse failure rely on ACE's
   own syntax annotation; on success run a memoized `ajv.compile(schema)` over the
   parsed value.
3. **Map Ajv errors → editor positions.** Ajv yields a JSON pointer
   (`/models/0/columns/2/type`), but ACE annotations need `{row, column}`. Use a
   JSON source-position parser (`jsonc-parser` or `json-source-map`, small dep) to
   resolve pointer → offset → row/col, then push `EditorAnnotation[]` via the
   existing `annotations` prop.
4. **Tests.** Unit-test the pointer→position mapper and the Ajv-error→annotation
   transform with fixtures (missing `type`, bad `joinType`).

### Complexity

**Medium — ~1 to 1.5 days.** Breakdown: schema endpoint (S), debounced
validate + Ajv wiring (S), **pointer→line/col mapping (M — the only real
fiddle)**, annotation transform + tests (S). Low risk: it is additive (a new
annotations source), provider-agnostic, and degrades to the current save-time
validation if anything fails.

**Rejected alternative — swap this editor to Monaco** for built-in schema
diagnostics: Superset standardized on ACE (`AceEditorProvider` is the registered
default); introducing Monaco for one panel is a large architectural deviation and
bundle-size cost for a feature ACE can deliver via annotations. Not worth it.

**Deferred sub-option (DF2a):** schema-driven *autocomplete* (suggest field names
/ enum values) is a further step — ACE completers are workable but a bigger lift;
validate-as-you-type is the high-value 80%.

### Status (F3) — DEFERRED after a feasibility spike

F3 (the implementation of DF2) was attempted and **deferred** per the agreed rule
"defer entirely if precise per-line mapping isn't clean." The spike found:

- The F2 prerequisite is **done**: `GET /agent/semantic-layer/mdl-schema` ships and
  is the single source of truth.
- `ajv` (8.20, has `dist/2020` for the draft-2020-12 schema Pydantic emits) and
  `jsonc-parser` are both present in `node_modules` **but only transitively** —
  neither is declared in `superset-frontend/package.json`. Importing them directly
  for this feature means **adding two new direct frontend dependencies** (a
  governance step) and, until then, tripping `import/no-extraneous-dependencies`.
- The JSON-pointer→`{row,column}` mapping (the assessment's one "real fiddle")
  still needs `jsonc-parser`'s position API; hand-rolling it to avoid the dep is
  exactly the "not clean" path the deferral rule guards against.

**Recommendation:** land F3 as its own change that (1) adds `ajv` + `jsonc-parser`
as declared deps, (2) wires the debounced Ajv validate + pointer→position mapper
behind the existing ACE `annotations` prop, (3) unit-tests the mapper. Everything
upstream (schema endpoint, native shape) is ready, so it is a clean, self-contained
follow-up rather than a rushed addition here.

---

## 10. Audit (2026-06-23) — what was checked, and what remains

### 10.1 Audit result

The rebuild claims were re-verified against source at audit. **The core claims
hold:** the dialect + translation layer are gone (grep-verified), the four error
classes are structurally closed by code that exists (`column_without_type`
`mdl_validator.py:351`; `_friendly_engine_error` `wren_core_validator.py:109`;
`_dedup_models_keep_last` `mdl_validator.py:732` + `app.py:1060` `dedup_models=True`;
`SchemaIndex` seeding `llm_client.py:238` + `_overlay_model_semantics`), the D2
purge migration `0004` + script exist, and the suites pass (308 backend / 37
frontend). Corrections applied this pass: **test counts 305→308**, **D2 "no
migration" → purge shipped**, **§4.7 checklist ticked**, **§7 DF2 "Monaco"→ACE**,
and **grep-gate wording made precise** (Python snake_case field identifiers alias
to camelCase and are not dialect leakage; one intentional `.yaml`-stripping line
remains in `llm_client.py:447`).

### 10.2 Feature status (implemented 2026-06-23)

F1, F2, F4, F5, F6 are **done & test-verified**; F3 was **attempted then deferred**
(see §9 "Status (F3)"); F7/F8 remain deferred per the owner's direction.

| # | Item | Status |
| --- | --- | --- |
| F1 | CI proves the wren-core wheel resolves *and* the gated tests run (no silent skip) | **DONE** — `superset-ai-agent.yml` + `requires_wren_core` marker; see §8 #5 |
| F2 | `GET /agent/semantic-layer/mdl-schema` (publishes `MdlManifest.model_json_schema(by_alias=True)`) | **DONE** — `app.py`; `test_app.py::test_mdl_schema_endpoint_publishes_native_manifest_shape` |
| F3 | DF2 as-you-type schema validation in the ACE editor | **DEFERRED** after spike — `ajv`/`jsonc-parser` are undeclared (transitive-only) deps; per-line mapping not clean to land here. F2 prereq shipped. See §9. |
| F4 | Cube/metric shape pinned to live wren-core (RM2) | **DONE** — `MdlCube.baseObject`, promoted cube errors, metric+cube golden round-trips; see §8 #4 |
| F5 | Weak-provider fallback surfaced in the UI | **DONE** — `_PROVIDER_FALLBACK_WARNING` + import-dialog `Alert`; see §8 #2 |
| F6 | Per-entity patch enrichment across multiple files | **DONE** — `_patch_target` + `_merge_manifest_sections`; see §8 #3 |
| F7 | DF3 authoring-side regenerate-on-validation-failure loop | **DEFERRED** (owner) — SQL-planning analogue already exists via `WREN_ENGINE_MAX_CORRECTION_RETRIES` |
| F8 | DF2a schema-driven autocomplete | **DEFERRED** (owner) — extends F3 |

### 10.3 Verification (this implementation pass)

- Backend `pytest tests/unit_tests/superset_ai_agent --strict-markers`: **315 passed,
  4 skipped** (was 308; +7 net from F2/F4/F5/F6 tests, after F4 replaced the
  speculative granularity/hierarchy tests).
- Frontend `jest src/SqlLab/components/AiAgentPanel`: **38 passed** — green under
  *both* parallel and `--runInBand`, after fixing a pre-existing stale test
  (`index.test.tsx` "chat scope" still mocked the retired `resolveSemanticProject`
  POST; the prior W6 work had migrated the source to `listSemanticProjects`, and
  lucky parallel scheduling had masked it). This was a latent defect from the
  earlier JSON migration, not from F1–F6.
- `ruff` clean on all changed Python; `prettier` clean on all changed TS.
  (`oxlint` could not run locally — native-binding load error in this environment;
  pre-commit/CI covers it. Changed TS mirrors existing lint-clean patterns.)

### 10.4 What remains after this pass

- **F3 (DF2)** — ship as its own change that declares `ajv` + `jsonc-parser` and
  wires the ACE annotations pipeline (§9 "Status (F3)").
- **F7 / F8** — deferred by the owner; revisit only if telemetry/editor-friction
  justifies.
- **Cross-file enrichment** — a single enrichment whose models span *several*
  existing files still falls back to the dedup net rather than patching each file
  (F6 handles single-owner + new-model cases; the endpoint is single-proposal by
  design).
