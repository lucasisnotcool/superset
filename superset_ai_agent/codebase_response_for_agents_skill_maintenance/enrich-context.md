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

# Skill maintenance report — enrich-context

## 1. Summary
- Replaced the 24-line paraphrase of `skills/enrich-context.md` with a full
  ~250-line playbook tailored from Wren's upstream `enrich-context/SKILL.md` +
  both reference files (`gap_catalog`, `cube_proposals`), ported to our copilot
  toolset and MDL JSON shape.
- Brought over Wren's complete methodology: hard rules, grill/auto-pilot modes,
  the three gap-detection lanes, the inlined ten-category gap catalog with the
  greppable `[tag]` discipline, and the aggregation decision tree.
- Made "add, never strip" **native** in both the skill and the single-shot
  enrichment prompt: the model is told to re-emit the full preserved object
  (every `properties` key + every physical field) so the
  `_preserve_superset_properties` / `_merge_*_preserving_structure` guards become
  defense-in-depth, not the primary mechanism.
- Corrected a stale, actively-wrong claim in `prompts/wren_enrichment.md` ("The
  MDL has no synonyms array"): `properties.synonyms` is a first-class governance
  key read by `schema_retriever._semantic_terms` and protected by
  `_preserve_superset_properties`. Synonyms now author natively into the MDL
  instead of degrading to a warning.
- Recorded the real parity gaps (no instruction-write tool, no cube authoring, no
  `queries.yml`/memory writeback, no live-DB probe) with our closest equivalents.

## 2. OUR requirements (extracted from the codebase)
- Copilot can write to exactly these sinks: `write_mdl_file` / `read_mdl_file` /
  `list_mdl_files` / `delete_mdl_file`, `validate_project`, `get_physical_schema`,
  `list_documents`, `search_documents`, `find_duplicate_documents`
  (`semantic_layer/copilot/tools.py:129-229`). **No instruction-writing tool
  exists** (grep for `instruction` in `tools.py` → none).
- `properties` keys that are consumed downstream: `displayName`, `alias`,
  `synonyms`, `description` are read by `schema_retriever._semantic_terms`
  (`schema_retriever.py:101-108`); `displayName`, `alias`, `description`,
  `expression` by `copilot/coverage._column_fact` (`coverage.py:162-165`).
  Dropping them silently degrades retrieval + coverage.
- The structure-preserving guards: `_preserve_superset_properties`
  (`tools.py:538`) additively restores any dropped base `properties` keys across
  `models/relationships/views/metrics/cubes` (`_PROPERTIES_SECTIONS`,
  `tools.py:484-490`); `_merge_column_preserving_structure` /
  `_merge_model_preserving_structure` / `_dropped_columns`
  (`integrations/wren/llm_client.py:720-770, 1005`) do the same on the single-shot
  path. New values win on collision; drops are restored (`tools.py:498-511`).
- MDL field shape is camelCase wren-core native: `tableReference`, `primaryKey`,
  `isCalculated`, `expression`, `relationship`, `notNull`, `joinType`, `condition`,
  `baseObject`; top-level `relationships[]`, `metrics[]`, `cubes[]`
  (`mdl_schema.py:22-23,165-168`). `type` is required on non-relationship columns
  (`mdl_schema.py:71`); authoring contract enforces it on `AuthoredColumn`.
- Modes map to a real flag: `wren_copilot_autopilot_enabled` (default False,
  `config.py:190`).
- Documents arrive two ways: inline attachments under "## Attached files"
  (`app.py:_attachments_text` ~1413, injected in `copilot/loop.py`) and a
  searchable corpus via the document tools.
- Instruction store is read-only to the agent: `app.py:_project_instruction_views`
  / `_recalled_instructions` (~1382-1411) call `instructions.py`'s
  `list_instructions` / `recall`; `add` exists on the store
  (`instructions.py:123`) but is **not** exposed as a copilot tool.
- Cubes are schema/validator/merge-supported (`mdl_schema.py:137`,
  `mdl_validator` `_validate_cubes`, `_merge_cube_preserving_structure`) but **not**
  in the authoring contract (`mdl_authoring.py` has no cube model; agent authors
  models/relationships/metrics).
- No `queries.yml` / `wren memory` writeback in the enrichment path (`queries.yml`
  is only a workspace UI placeholder, `copilot/workspace.py`).
- No live-DB distinct-value query tool in the copilot toolset (only
  `get_physical_schema` for tables + types).

## 3. Upstream → ours mapping
| Upstream capability | ported / adapted / dropped | Our equivalent | Why |
|---|---|---|---|
| `raw/` folder of artifacts | adapted | uploaded documents (`list_documents`/`search_documents`) + inline attachments | We have no filesystem `raw/`; docs are an indexed corpus |
| MDL YAML edits + `wren context validate` | adapted | `write_mdl_file` (JSON camelCase) + `validate_project` | Our MDL is JSON; validation is a tool |
| Ten-category gap catalog | ported | inlined in skill Step 5 | Highly portable; core of the capability |
| `[tag]` description discipline | ported | same tags inside `properties.description` | Fits our description string verbatim |
| Synonyms → `instructions.md` | **adapted (improved)** | `properties.synonyms` (native MDL key) | Read directly by retrieval; better home than free text |
| Default-filter/external-id/currency/canonical-table → `instructions.md` | adapted | **recommended operator Instruction** in summary | Agent has no instruction-write tool; human adds via UI |
| Cube proposals (`cubes/*.yml`) | adapted | aggregate calculated field (preferred) / `metrics[]` | Cubes not in authoring contract; calc fields are engine-validated |
| `wren cube list/describe` dedup | adapted | read existing `metrics[]` / columns before adding | Same dedup intent, our fields |
| grill vs auto-pilot | ported | `wren_copilot_autopilot_enabled` flag | Real config maps cleanly |
| Step 4.5 live-DB distinct probe | dropped (gap) | `get_physical_schema` types + document evidence | No live-query tool in copilot toolset |
| `queries.yml` / `wren memory` sinks | dropped (gap) | — | No enrichment writeback; query agent owns NL→SQL pairs |
| `wren context build` / memory index | dropped | `validate_project`; server-side re-embedding | No build step; persistence is implicit |
| Project selection / `wren_project.yml` / memory detect | dropped | active project's MDL files | Single active project context |

## 4. Files changed
- `superset_ai_agent/skills/enrich-context.md` — copied upstream baseline over the
  paraphrase, then tailored fully in place (ASF header, our tools/sinks, inlined
  gap catalog + decision tree, parity notes).
- `superset_ai_agent/prompts/wren_enrichment.md` — added a native "Add, never
  strip — emit the full preserved object" section; added the `[tag]` discipline to
  the descriptions bullet; rewrote the synonyms section to use
  `properties.synonyms`; updated the alias exemplar to show synonyms.

## 5. Declared contract changes / deviations (constraint C2)
- **Corrected an incorrect invariant** in `wren_enrichment.md`: the old text "The
  MDL has **no synonyms array** … do **not** invent a field" contradicted the code
  (`schema_retriever.py:107`, `_PROPERTIES_SECTIONS`). Changed to author
  `properties.synonyms`. This is a behavior change (synonyms now land in MDL rather
  than only as a warning) — intentional and code-grounded.
- Removed all Wren-CLI surface (`wren context/cube/memory`, `raw/`, `queries.yml`,
  `wren_project.yml`, `mkdir raw`, `wren context build`) from the active skill.
- Mode selection is no longer an in-session question; it is bound to
  `wren_copilot_autopilot_enabled`. (Deviation from upstream Step 0's interactive
  prompt — our mode is deployment config, not a per-session choice.)

## 6. Native-correctness changes
- Skill Rule 1 and the prompt's new "Add, never strip" section instruct the model
  to re-emit the **full** preserved object — every `properties` key
  (`displayName`/`alias`/`synonyms`/`description`) and every physical field
  (`tableReference`/`name`/`type`/`expression`/`relationship`/`isCalculated`/
  `notNull`) copied forward verbatim before adding semantics. With this, the
  generation is correct from the first token and the guards
  (`_preserve_superset_properties`, `_merge_*_preserving_structure`,
  `_dropped_columns`) only ever have to act on a model that misbehaves — they are
  defense-in-depth, not the load-bearing mechanism.
- Both files name the downstream consumers (retrieval + coverage) so the model
  knows *why* preservation matters, not just that it must.

## 7. Parity gaps remaining
- **No agent instruction-write tool.** Wren writes rule-shaped facts (default
  filters, external IDs, currency, canonical tables) to `instructions.md`; we can
  only *recommend* them in the summary for a human to add. Worth building: a
  scoped `add_instruction` copilot tool would close the largest gap and make
  categories 4/8/9/10 fully autonomous. (See §8.)
- **No cube authoring.** Cubes are supported end-to-end except in the authoring
  contract, so aggregations route to calculated fields / metrics. Acceptable;
  revisit only if operators want agent-authored cubes.
- **No `queries.yml` / memory writeback** in enrichment — by design; confirmed
  NL→SQL pairs belong to the query agent (`usage` skill).
- **No live-DB distinct-value probe** — enum/sentinel/grain are settled from
  physical types + documents; a read-only sampling tool would raise confidence but
  is not required.

## 8. Recommendations for shared files (no edits — proposals only)
- **`prompts/mdl_copilot.md`**: add a one-line global invariant — "Re-emit the
  full object on every `write_mdl_file`: copy all existing `properties` keys
  (`displayName`/`alias`/`synonyms`/`description`) and physical fields forward
  verbatim before adding anything." This benefits onboarding/generate-mdl too, not
  just enrichment, and reinforces the native-correctness goal across all three
  skills.
- **`mdl_copilot.md`**: state once that `properties.synonyms` is the canonical home
  for colloquial vocabulary (read by retrieval), so all three skills converge on it.
- **New tool (code change)**: a scoped `add_instruction(text, is_global)` copilot
  tool wired to `instructions.py:add` would let enrichment write categories
  4/8/9/10 directly instead of recommending them — the single highest-value parity
  closer. Must respect `owner_id` + `scope_hash` and likely gate on auto-pilot.
- **`COPILOT_SKILLS`**: no change needed (`enrich-context` already injected,
  `service.py:63`).

## 9. Unverified claims / open questions
- I did not confirm at runtime that `wren_copilot_autopilot_enabled` is threaded
  into the copilot loop's behavior (only that the flag exists, `config.py:190`).
  The skill describes the intended grill/auto-pilot semantics; if the loop does not
  yet branch on it, that branching is a follow-up, not a skill error.
- `metrics[]` is "not deeply planned" per the existing `wren_enrichment.md` wording;
  I preserved that guidance (prefer aggregate calculated fields) but did not
  independently benchmark metric planning depth.

## 10. Verification log
```bash
# tool names exist
grep -nE 'name="' semantic_layer/copilot/tools.py
#   → list_mdl_files, read_mdl_file, write_mdl_file, delete_mdl_file,
#     validate_project, get_physical_schema, list_documents, search_documents,
#     find_duplicate_documents
grep -niE 'instruction' semantic_layer/copilot/tools.py   # → (none) no write tool

# properties keys consumed downstream
grep -nE 'displayName|alias|synonyms|description' semantic_layer/schema_retriever.py  # :101-108
grep -nE 'displayName|alias|expression|description' semantic_layer/copilot/coverage.py # :162-165

# preservation guards
grep -nE '_preserve_superset_properties|_PROPERTIES_SECTIONS' semantic_layer/copilot/tools.py # :484,538
grep -nE '_merge_.*_preserving_structure|_dropped_columns' integrations/wren/llm_client.py    # :720-770,1005

# MDL field shape + top-level sections
grep -nE 'tableReference|joinType|isCalculated|baseObject|relationships|metrics|cubes' semantic_layer/mdl_schema.py # :22-23,165-168

# mode flag
grep -nE 'autopilot' config.py   # :190 wren_copilot_autopilot_enabled

# skills injected
grep -nE 'COPILOT_SKILLS' semantic_layer/copilot/service.py  # :63 ("onboarding","generate-mdl","enrich-context")
```
