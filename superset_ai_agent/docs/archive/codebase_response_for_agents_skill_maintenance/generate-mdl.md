# Skill maintenance report — generate-mdl

## 1. Summary
- Replaced the 28-line paraphrase (which told the agent to author **snake_case
  YAML** and to "prefer cubes") with a tailored full skill that encodes **our**
  native JSON camelCase MDL contract, so generations are correct from the first
  token.
- Added a complete, **copy-paste-correct** two-model manifest example (model +
  columns + calculated column + relationship column + relationship + metric +
  populated `properties`) that I verified validates clean under project-strict
  validation with a physical `SchemaIndex` (0 errors, 0 warnings).
- Ported the upstream methodology (schema discovery, type grounding, FK→joinType
  cardinality table, descriptions-improve-recall, validate-before-finish) onto
  **our** tools (`get_physical_schema`, `write_mdl_file`, `validate_project`),
  dropping all `wren` CLI / `wren_project.yml` / `wren memory` mechanics.
- Made `properties` a **positive native rule** (carry forward, add, never strip)
  with the explicit WHY (retrieval + coverage read it; wren-core tolerates its
  loss so validation can't catch it), demoting `_preserve_superset_properties` to
  defense-in-depth.
- Recorded **cubes** as a deliberate parity gap: schema + validator exist, but the
  authoring contract (`AuthoredManifest`) exposes only models/relationships/
  metrics, so the skill directs aggregation intent to **metrics**.

## 2. OUR requirements (extracted from the codebase)
- MDL is wren-core's **native camelCase** shape; there is no snake_case dialect and
  no build step — `mdl_schema.py:18-37`.
- Model fields: `name`, `tableReference`/`refSql`, `columns[]`, `primaryKey`,
  `properties` — `mdl_schema.py:92-103`. `tableReference` uses the bare `schema`
  key (alias), not `schemaName` — `mdl_schema.py:81-90`.
- Column fields: `name`, `type` (required for non-relationship), `isCalculated`,
  `expression`, `relationship`, `notNull`, `properties` — `mdl_schema.py:65-78`.
- Relationship fields: `name`, `models[]` (exactly two), `joinType`, `condition`;
  join enum is UPPERCASE `ONE_TO_ONE|ONE_TO_MANY|MANY_TO_ONE|MANY_TO_MANY` —
  `mdl_schema.py:46-53,105-114`.
- Metric fields: `name`, `baseObject`, `expression`, `properties` —
  `mdl_schema.py:126-134`; `baseObject` must resolve to a model/view/cube and a
  metric needs an `expression` or `measure[]` — `mdl_validator.py:587-652`.
- Structural/physical enforcement: typeless non-relationship column rejected
  (`column_without_type`, `mdl_validator.py:437-449`); calculated column requires
  expression (`mdl_validator.py:427-436`); unknown table/column
  (`mdl_validator.py:344-353,450-464`); cross-family type mismatch
  (`mdl_validator.py:473-510`); join enum + arity + endpoint resolution
  (`mdl_validator.py:539-584`).
- Engine blind spot: wren-core only fails on structural load (missing `type`,
  unknown `joinType` variant) and **tolerates unknown fields like `properties`** —
  `mdl_schema.py:36`, `wren_core_validator.py:109-142`.
- `properties` consumers: retrieval reads `displayName`/`alias`/`synonyms`/
  `description` — `schema_retriever.py:101-114`; coverage reads
  `displayName`/`alias` — `coverage.py:160-170`.
- The exporter (real Superset→MDL shape to match) emits `tableReference {schema,
  table}`, top-level `description`, column `type`, `isCalculated:false`, and
  model/column `properties` — `mdl_exporter.py:78-130`.
- Authoring envelope the model fills is models/relationships/metrics only (no
  cubes) — `mdl_authoring.py:112-119`.
- Tool surface: `list_mdl_files`, `read_mdl_file`, `write_mdl_file`,
  `delete_mdl_file`, `validate_project`, `get_physical_schema`, plus document
  tools — `tools.py:123-230`. Full-content overwrite + the
  `_preserve_superset_properties` guard — `tools.py:277-305,538-575`.

## 3. Upstream → ours mapping
| Upstream capability | Disposition | Our equivalent | Why |
|---|---|---|---|
| Author snake_case YAML under `models/` | **dropped** | Native camelCase JSON via `write_mdl_file` | No build step; wren-core ignores `table_reference` |
| `wren context build` compile | dropped | (none — direct authoring) | We author the compiled shape directly |
| `wren context init` / `wren_project.yml` | dropped | `list_mdl_files` / per-file JSON | No project scaffold |
| Schema discovery (SQLAlchemy/driver) | **adapted** | `get_physical_schema` tool | Permission-filtered schema is the authority |
| `parse_type` type normalization | adapted | Ground types in `column_types`; family-match rule | Validator enforces cross-family match |
| FK → relationship + join-type table | **ported** | Same cardinality table, UPPERCASE `joinType` | Engine + validator require the enum |
| Descriptions improve recall | ported | `description` + `properties.displayName/alias/synonyms` | These are what retrieval indexes |
| `wren context validate` | adapted | `validate_project` (+ per-write validation) | Same intent, tool not CLI |
| Cubes for aggregation | **dropped (gap)** | Metrics (and calculated columns) | Agent authoring contract has no cubes |
| `wren memory index` | dropped | (none) | Retrieval indexes the manifest directly |
| `wren --sql` smoke test | dropped | (none in this skill) | Query path is the `usage` skill |

## 4. Files changed
- `superset_ai_agent/skills/generate-mdl.md` — full rewrite: replaced upstream
  provenance comment with the ASF header; tailored every phase to our JSON/tools;
  added the validated example block, field reference, joinType enum, FK→joinType
  table, the `properties` positive rule, and an error-code troubleshooting table.

## 5. Declared contract changes / deviations (constraint C2)
- Removed stock-Wren wording: "Author MDL as readable snake_case YAML; it compiles
  to a camelCase manifest", "Prefer defined `metrics` and `cubes`", and the
  `join_type`/`is_calculated` snake_case spellings from the prior paraphrase — all
  contradicted our stack.
- Removed all Wren-CLI plumbing (`wren context`, `wren memory`, `wren_project.yml`,
  profiles, `parse_type`/`wren utils`).
- No deviation from our invariants: every field name, enum, validation code, tool
  name, and property key in the skill was grepped against the code (see §10).

## 6. Native-correctness changes
- The example block is in our exact native shape, so the model imitates correct
  camelCase + populated `properties` from the first token rather than producing
  YAML that the guard/validator must repair.
- `properties` is stated as a positive emit-every-time rule with the WHY, so the
  model preserves governance/retrieval keys natively;
  `tools.py:_preserve_superset_properties` becomes a backstop, not the primary
  mechanism.
- Physical-authority rules are tied directly to `get_physical_schema` and named
  validation codes, so "never invent tables/columns/types" is concrete and
  testable rather than aspirational.

## 7. Parity gaps remaining
- **Cubes**: wren-core + our validator support them, but the agent authoring
  contract (`AuthoredManifest`) omits them, so the skill routes aggregation to
  metrics. Worth building only if a structured measures/dimensions query API is
  needed beyond metrics; until the authoring envelope adds cubes, instructing the
  agent to emit them would produce content the proposal schema can't carry.
- **`queries.yml` / seed NL-SQL examples** and **`wren memory index`**: no
  equivalent in this skill's scope (retrieval indexes the manifest directly).
  Recorded as intentional, not a regression.
- **Views**: supported by schema + validator; mentioned briefly but not the
  primary path. No gap, just lower emphasis.

## 8. Recommendations for shared files (no edits — proposals only)
- `prompts/mdl_copilot.md`: ensure it does not also tell the model to author YAML
  or cubes; if it pre-dates the native-shape migration it should point at this
  skill's camelCase contract to avoid a mixed signal. (Not edited per scope.)
- `mdl_authoring.py`: `AuthoredMetric` lacks a `properties` field (relies on
  `extra="allow"`). If metric-level `displayName`/synonyms are ever wanted for
  retrieval, add an explicit `properties` field there — currently retrieval's
  `_semantic_terms` would read it only if present.
- Consider exposing `MdlCube` through `AuthoredManifest` if/when cube authoring is
  desired end-to-end; until then the schema-only cube support is a latent gap that
  could mislead a future skill author.

## 9. Unverified claims / open questions
- `wren_materializer.py` `dataSource.properties` (manifest-level, not per-file) was
  not deeply read — it is materializer-owned and outside what the agent authors,
  so the skill does not instruct on it. Flagged rather than described.
- The skill says engine deep-validate runs "when available"; `wren-core` is an
  optional import (`wren_core_validator.py:47-59`) — accurate, but whether it is
  installed in any given deployment is environment-dependent.

## 10. Verification log
- `cp` of upstream baseline → `skills/generate-mdl.md`; replaced provenance header
  with the ASF header copied from `skills/usage.md:1-16`.
- Ran `validate_project_manifest([example], schema_index=...)` against the skill's
  example JSON → `valid: True`, no messages.
- Grepped all nine tool names against `name="…"` in `tools.py` → all OK.
- Grepped `displayName`/`alias`/`synonyms` in `schema_retriever.py` +
  `coverage.py` → all consumed.
- Grepped all 13 cited validation `code="…"` values in `mdl_validator.py` → all
  present.
- Confirmed `AuthoredManifest` lists only models/relationships/metrics
  (`mdl_authoring.py:117-119`) — no cubes — backing the cube parity-gap claim.
