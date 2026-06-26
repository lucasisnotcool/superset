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

# Wren skill-maintenance agents — task prompts

Three agents, one per Wren-derived skill we load into the MDL Copilot
(`onboarding`, `generate-mdl`, `enrich-context`). Each agent rewrites OUR skill
file so it reaches **methodological parity** with genuine upstream Wren, adapted
to our stack — not a trimmed paraphrase. Hand each block below to its own
instance. Run them in parallel; file ownership is disjoint.

## The parity mandate (read first — applies to all three)

Our current `superset_ai_agent/skills/*.md` are **short paraphrases** (10–28
lines) that drop most of Wren's methodology and, in places, contradict our own
stack (e.g. `generate-mdl.md` says "author YAML" but our Copilot authors **JSON
camelCase**). The genuine upstream skills are 170–300+ line playbooks.

Your job is **not** to trim the upstream CLI noise and stop. It is to **port the
upstream skill's reasoning to our stack** so our agent is as capable as Wren's:
keep the methodology (gap detection, type discipline, relationship inference,
grill/auto-pilot, decision trees), drop the parts that are purely Wren-CLI
plumbing (`wren context`, `.env`, profiles, `wren memory`), and replace each Wren
mechanism with **our** equivalent (the Copilot tools, the instructions store,
JSON MDL files, our validators). Where Wren has a capability we lack, either add
the closest equivalent we DO support or record it as a deliberate parity gap with
rationale.

Guiding principle (your `properties` example, generalized): bake correctness into
the prompt **natively** so generations are right from the first token. The
`tools.py:_preserve_superset_properties` guard then becomes defense-in-depth, not
the primary mechanism.

## Genuine upstream baselines (the source you tailor FROM)

These were fetched and verified, and copied into the repo so each agent can read
its baseline locally. Read your baseline before rewriting. The local copy is
authoritative for this task; the URL is the source of truth if you need to
re-fetch. Local copies live under `superset_ai_agent/wren_upstream_skills/`.

| Skill | Local baseline (read this) | Upstream URL (source) |
|---|---|---|
| onboarding | `superset_ai_agent/wren_upstream_skills/onboarding.SKILL.md` | `https://raw.githubusercontent.com/Canner/WrenAI/main/core/wren/src/wren/skills_content/onboarding/SKILL.md` |
| generate-mdl | `superset_ai_agent/wren_upstream_skills/generate-mdl.SKILL.md` | `.../generate-mdl/SKILL.md` |
| enrich-context | `superset_ai_agent/wren_upstream_skills/enrich-context.SKILL.md` | `.../enrich-context/SKILL.md` |
| enrich-context ref: gap_catalog | `superset_ai_agent/wren_upstream_skills/enrich-context.references.gap_catalog.md` | `.../enrich-context/references/gap_catalog.md` |
| enrich-context ref: cube_proposals | `superset_ai_agent/wren_upstream_skills/enrich-context.references.cube_proposals.md` | `.../enrich-context/references/cube_proposals.md` |

See `superset_ai_agent/wren_upstream_skills/README.md` for provenance. These are
third-party reference copies (not the active skills in `superset_ai_agent/skills/`).

**Scope note (what's adjacent but out of scope).** Upstream `skills_content/` has
six skills: `onboarding`, `generate-mdl`, `enrich-context` (yours), plus `usage`
(day-to-day NL→SQL — that's our *query* agent, `graph.py`/`conversation_graph.py`,
not the Copilot), `genbi` (generative BI / charts), and `dlt-connector` (SaaS
ingestion — N/A, we use Superset datasets). Only `onboarding`/`generate-mdl`/
`enrich-context` carry reference subfiles relevant to MDL skill editing, and the
only references are `enrich-context/references/{gap_catalog,cube_proposals}.md`
(both fetched above). If during your read you discover an upstream reference your
skill depends on that is NOT listed here, STOP and report it rather than guessing.

## Working method — COPY FIRST, then tailor in place (all three)

Do **not** regenerate a skill from memory. The genuine upstream skill is long and
detail-dense; retyping it risks silent truncation and hallucinated steps. Instead:

1. **Copy the upstream baseline over the active skill file with a shell command**
   (this is the literal first action of each prompt — see each Step 0). Example:
   ```bash
   cp superset_ai_agent/wren_upstream_skills/onboarding.SKILL.md \
      superset_ai_agent/skills/onboarding.md
   ```
2. **Now tailor the copied file IN PLACE** with edit operations — walk it line by
   line, keep/revise/delete/add. Every line you keep is the genuine upstream text,
   not a paraphrase. You are editing a real file, not composing from scratch.
3. **Header hygiene:** the upstream copy carries a third-party provenance comment.
   When you finish, the file is OUR skill again — replace that provenance header
   with the standard ASF license header (copy it verbatim from any other file in
   `superset_ai_agent/skills/`, e.g. the current `usage.md`). Do not leave the
   third-party provenance block in an active skill file.
4. The `cp` overwrites the current short paraphrase — that is intended; the
   paraphrase is being replaced by the tailored full skill. (If you want to keep
   the old paraphrase for diffing, `git show HEAD:<path>` retrieves it.)

This copy-first rule applies to the **skill file** (`skills/<name>.md`). The
sibling **prompt file** you own (`prompts/wren_onboarding.md` /
`prompts/wren_enrichment.md`) is already ours — edit it normally, do not overwrite
it from upstream.

## Reporting (required — all three)

When done, write a markdown report to:
`superset_ai_agent/codebase_response_for_agents_skill_maintenance/<skill>.md`
(`onboarding.md`, `generate-mdl.md`, or `enrich-context.md`). Use the template at
the very bottom of this file. The report is how we reconcile the three passes —
especially the shared-file recommendations and the parity-gap list.

---

## Prompt 1 — Onboarding skill agent

You are tailoring ONE Wren-derived skill to Apache Superset's fork of the
semantic-layer agent. Your area is ONBOARDING (schema → base MDL). Read the
"parity mandate", "baselines", and "reporting" sections above first.

GOAL
Rewrite `superset_ai_agent/skills/onboarding.md` (and its sibling prompt
`superset_ai_agent/prompts/wren_onboarding.md`) to reach methodological parity
with the genuine upstream onboarding skill, adapted to our stack, encoding OUR
requirements natively (correct-by-construction) rather than relying on guards.

FILES YOU OWN (edit ONLY these):
- superset_ai_agent/skills/onboarding.md
- superset_ai_agent/prompts/wren_onboarding.md
For anything `superset_ai_agent/prompts/mdl_copilot.md` needs, write a
RECOMMENDATION in your report — do NOT edit it (shared system prompt).

STEP 0 — Copy the baseline in, then read it. FIRST run the copy command (do not
retype the skill from memory):
```bash
cp superset_ai_agent/wren_upstream_skills/onboarding.SKILL.md \
   superset_ai_agent/skills/onboarding.md
```
Now read the file you just copied and list its real capabilities (preflight
discipline, one-step-per-turn, scaffold layout, never-query-before-MDL,
cross-skill routing). Decide per capability: port to our stack / replace with our
equivalent / drop as Wren-CLI-only. You will tailor THIS copied file in place.

STEP 1 — Learn OUR onboarding stack (read-only analysis first):
- superset_ai_agent/semantic_layer/onboarding.py (onboard_schema_project: base
  model generation, validation, auto-activation, drafts, reindex)
- superset_ai_agent/integrations/wren/mdl_exporter.py (model_from_dataset,
  column_to_field — Superset dataset → MDL model/column)
- superset_ai_agent/integrations/wren/llm_client.py (generate_base_model,
  _overlay_model_semantics — "structure from the catalog, semantics from the
  model"; the LLM NEVER invents structure)
- superset_ai_agent/semantic_layer/mdl_schema.py + mdl_authoring.py (exact MDL
  field set: camelCase tableReference/isCalculated/joinType/baseObject; the
  `properties` bag on model/column)
- superset_ai_agent/semantic_layer/wren_materializer.py (dataSource.properties:
  superset_database_id / semantic_project_id / schema_name)
- superset_ai_agent/semantic_layer/schema_retriever.py (_semantic_terms) and
  copilot/coverage.py (_column_fact) — the CONSUMERS that silently break if
  `properties` (displayName/alias/synonyms) is dropped; confirm exact key names.
- superset_ai_agent/semantic_layer/copilot/tools.py — confirm the tool names
  (get_physical_schema, write_mdl_file, validate_project, …) and read
  _preserve_superset_properties (the guard you are making redundant).
- superset_ai_agent/semantic_layer/copilot/service.py (COPILOT_SKILLS) and
  loop.py (build_system_prompt) — how/where this skill text is injected.
- superset_ai_agent/app.py readiness gate (`_require_project_ready`) — onboarding
  must stabilize the layer before the Copilot edits; reflect that lifecycle.
Produce an "OUR onboarding requirements" list (8–15 bullets) with file:line
evidence.

STEP 2 — Rewrite the markdown, line by line (keep/revise/delete/add per line):
- Port the upstream's procedural discipline (scope confirmation, never query
  before MDL exists, scaffold the Wren layout models/relationships/views) but in
  OUR terms (no `wren context init`; paths create folders; JSON not YAML).
- `properties` contract IN the prompt: every emitted/kept model or column carries
  its `properties` (displayName/alias/synonyms — EXACT keys you confirmed)
  forward; never drop them. Positive rule, not "a guard fixes it."
- Physical authority: never invent/rename/retype a physical table/column; pull
  structure from get_physical_schema; semantics only from model/docs.
- camelCase keys, every column needs `type`, drafts only (activation is human),
  RBAC-filtered datasets only, one model per file under models/.
- Remove Wren CLI / `.env` / profile / `wren memory` content we don't have;
  replace the "memory index" step with our reindex behavior if relevant.

STEP 3 — Verify + report. Grep to confirm every field/key/tool you mention
exists. Write the report to
`…/codebase_response_for_agents_skill_maintenance/onboarding.md` per the template.

ENTRYPOINTS & TIPS
- Start at onboarding.py → mdl_exporter.py → llm_client.py.
- Ground every `properties` key claim in schema_retriever.py / coverage.py.
- Do not weaken any existing safety rule; you are ADDING native correctness.

---

## Prompt 2 — generate-mdl skill agent

You are tailoring ONE Wren-derived skill to our fork. Your area is GENERATE-MDL
(structurally correct MDL: models, columns, relationships, calculated fields,
metrics). Read the "parity mandate", "baselines", and "reporting" sections first.

GOAL
Rewrite `superset_ai_agent/skills/generate-mdl.md` to reach parity with the
upstream generate-mdl skill, adapted to our stack, encoding OUR exact MDL schema
and Superset extension fields natively — valid, governance-complete MDL from the
first token.

FILES YOU OWN (edit ONLY this):
- superset_ai_agent/skills/generate-mdl.md
Do NOT edit prompts/mdl_copilot.md, prompts/wren_onboarding.md, or the other two
skills — emit RECOMMENDATIONS in your report instead.

STEP 0 — Copy the baseline in, then read it. FIRST run the copy command (do not
retype the skill from memory):
```bash
cp superset_ai_agent/wren_upstream_skills/generate-mdl.SKILL.md \
   superset_ai_agent/skills/generate-mdl.md
```
Now read the file you just copied. Note its real methodology: schema discovery,
**type normalization**, FK→relationship mapping with join-type table, descriptions
improve recall, validate-before-build, cube guidance. CRITICAL reconciliation:
upstream authors snake_case YAML compiled by `wren context build`; WE author JSON
camelCase directly with no build step. Port the *reasoning*, not the YAML/CLI
mechanics. You will tailor THIS copied file in place.

STEP 1 — Learn OUR MDL contract (read-only analysis first):
- superset_ai_agent/semantic_layer/mdl_schema.py (authoritative: MdlModel,
  MdlColumn, MdlView, MdlMetric, MdlCube — exact fields, camelCase, where
  `properties` lives, what `extra="allow"` permits). Confirm whether MdlCube is
  actually wired end-to-end (validator + materializer + query) or schema-only;
  your skill must only tell the agent to author what we truly support.
- superset_ai_agent/semantic_layer/mdl_authoring.py (AuthoredModel/AuthoredColumn
  — LLM-facing authoring contract; note divergence from mdl_schema.py)
- superset_ai_agent/semantic_layer/mdl_validator.py (validate_mdl,
  validate_project_manifest, SchemaIndex — what STRUCTURAL validation enforces:
  calculated-field rules, relationship rules, physical mapping) and
  wren_core_validator.py (what the ENGINE enforces vs tolerates — confirm
  `properties` removal is NOT caught: the WHY behind native preservation)
- superset_ai_agent/integrations/wren/mdl_exporter.py (how real models/columns/
  metrics are built from Superset datasets — the shape you must match)
- superset_ai_agent/semantic_layer/wren_materializer.py (dataSource.properties)
  and schema_retriever.py / copilot/coverage.py (consumers of column `properties`
  displayName/alias/synonyms — confirm EXACT key names)
- superset_ai_agent/semantic_layer/copilot/tools.py (the tool surface +
  _preserve_superset_properties guard you are making redundant)
Produce an "OUR MDL field contract" list: for model/column/relationship/metric
(/cube if supported), the required keys, the camelCase spelling, the Superset
`properties` keys — with file:line evidence.

STEP 2 — Rewrite the markdown, line by line:
- A concrete, copy-paste-correct MDL example block in OUR JSON camelCase (model +
  column + relationship + a calculated column + a populated `properties` block) —
  models imitate examples, so this is the highest-leverage edit.
- `properties` as a POSITIVE rule: every model/column you emit includes its
  `properties`, carried forward verbatim on edits, added to (never removed/
  emptied). State WHY (retrieval + governance read it; engine validation won't
  catch its loss).
- Physical-authority / "never invent tables/columns/types" tied to
  get_physical_schema.
- Calculated columns (isCalculated + expression), relationship joinType enum, and
  metric shape EXACTLY as the validator enforces.
- Port the upstream type-normalization and FK→join-type discipline as guidance;
  replace YAML/`metrics:`-vs-`cubes:` wording with what OUR schema supports
  (confirm from mdl_schema.py first).

STEP 3 — Verify + report. Grep every key/tool you mention. Write the report to
`…/codebase_response_for_agents_skill_maintenance/generate-mdl.md` per template.

ENTRYPOINTS & TIPS
- mdl_schema.py is the single source of truth for field names.
  wren_core_validator.py tells you the engine's blind spots.
- The JSON example block must validate against our schema — verify it mentally
  against mdl_validator.py rules.

---

## Prompt 3 — Enrich-context skill agent

You are tailoring ONE Wren-derived skill to our fork. Your area is ENRICH-CONTEXT
(adding business semantics to existing MDL from uploaded documents/attachments).
Read the "parity mandate", "baselines", and "reporting" sections first. This is
the richest upstream skill — bring its full methodology over, adapted.

GOAL
Rewrite `superset_ai_agent/skills/enrich-context.md` (and its sibling prompt
`superset_ai_agent/prompts/wren_enrichment.md`) so enrichment is correct-by-
construction: it ADDS semantics while natively preserving the Superset
`properties` and physical structure — no downstream merge/guard needed — AND it
carries Wren's gap-detection methodology adapted to our stack.

FILES YOU OWN (edit ONLY these):
- superset_ai_agent/skills/enrich-context.md
- superset_ai_agent/prompts/wren_enrichment.md
Emit RECOMMENDATIONS (do not edit) for prompts/mdl_copilot.md.

STEP 0 — Copy the baseline in, then read it (skill + both references). FIRST run
the copy command for the active skill (do not retype the skill from memory):
```bash
cp superset_ai_agent/wren_upstream_skills/enrich-context.SKILL.md \
   superset_ai_agent/skills/enrich-context.md
```
Then READ both references in place (do NOT copy them into `skills/` — our loader
only injects named skills, not reference files; you will fold their essential
content into `skills/enrich-context.md` as you tailor):
- `superset_ai_agent/wren_upstream_skills/enrich-context.references.gap_catalog.md`
  (the 10 business-semantic categories)
- `superset_ai_agent/wren_upstream_skills/enrich-context.references.cube_proposals.md`
  (the aggregation sink decision tree)
These are the core of Wren's capability. You will tailor the copied
`skills/enrich-context.md` in place and inline an adapted gap-catalog. Decide how
to port each piece to our stack:
- Upstream sinks are `raw/`, MDL YAML, `instructions.md`, `queries.yml`,
  `wren memory`. OUR equivalents: uploaded documents via the Copilot document
  tools (`search_documents`/`list_documents`/`find_duplicate_documents`) instead
  of `raw/`; JSON MDL files; our **instructions store** instead of
  `instructions.md`; confirm what stands in for `queries.yml`/`wren memory`
  (there may be NO equivalent — record as a parity gap).
- The 10-category gap_catalog and the column `properties.description` `[tag]`
  convention are highly portable — adapt them to our `properties` keys.
- For cube_proposals: FIRST confirm in mdl_schema.py/validator/materializer
  whether cubes are end-to-end supported. If not, map aggregation metrics to our
  supported sink (metrics / calculated columns) and record the cube gap.

STEP 1 — Learn OUR enrichment stack (read-only analysis first):
- superset_ai_agent/integrations/wren/llm_client.py (propose_mdl_from_document
  and the *_preserving_structure merges incl. _merge_column_preserving_structure,
  _dropped_columns — the legacy guard your prompt should make the model do
  natively)
- superset_ai_agent/prompts/wren_enrichment.md and skills/enrich-context.md
  (current)
- superset_ai_agent/semantic_layer/mdl_schema.py / mdl_authoring.py (exact fields
  + `properties` bag; confirm displayName/alias/synonyms key names)
- superset_ai_agent/semantic_layer/schema_retriever.py (_semantic_terms) and
  copilot/coverage.py (_column_fact) — consumers that silently degrade if
  `properties` is dropped; the rationale your prompt must state
- superset_ai_agent/semantic_layer/copilot/tools.py (document tools
  list_documents/search_documents/find_duplicate_documents, MDL CRUD,
  validate_project, get_physical_schema; _preserve_superset_properties)
- superset_ai_agent/semantic_layer/copilot/service.py (COPILOT_SKILLS) / loop.py
  (build_system_prompt) — injection; and app.py _attachments_text (attachments
  arrive as inline long-context)
- the instructions store: superset_ai_agent/app.py (_project_instruction_views,
  _recalled_instructions) and the instruction store module — OUR equivalent of
  Wren's instructions.md sink. Confirm how rules are added/recalled.
Produce an "OUR enrichment requirements" list with file:line evidence.

STEP 2 — Rewrite the markdown, line by line:
- "Add, never strip" as NATIVE behavior: when re-emitting a model/column, copy
  existing `properties` (displayName/alias/synonyms) and all physical fields
  (tableReference, column name, type) forward verbatim, THEN add semantics. The
  agent must emit the full preserved object, not a partial overlay.
- Port the gap_catalog: a compact version of the 10 categories with OUR sinks
  (column properties for enum/unit/null/magic/time; our instructions store for
  default-filters/synonyms/external-ids/currency/canonical-tables). Keep the
  greppable `[tag]` discipline if it fits our `properties` shape.
- A document-grounding workflow with OUR real tools: search_documents/
  list_documents to find definitions/units/enums/synonyms/metric formulas before
  editing; find_duplicate_documents to reconcile conflicts; cite the passage.
- Port the cube/metric decision tree to what we support; "only add, never modify
  existing — surface conflicts on a manual-fix list"; validate after edits via
  validate_project.
- Map grill vs auto-pilot to our flags if present (e.g.
  wren_copilot_autopilot_enabled) or record as a parity gap.
- Remove Wren CLI (`wren context`, `wren memory`, `wren cube`) — replace with our
  tools or record the gap.

STEP 3 — Verify + report. Grep every field/tool you mention. Write the report to
`…/codebase_response_for_agents_skill_maintenance/enrich-context.md` per template.

ENTRYPOINTS & TIPS
- Start at llm_client.py propose_mdl_from_document and the *_preserving_structure
  merges — your prompt makes the MODEL do what those merges do today.
- Ground `properties` keys in schema_retriever.py / coverage.py; ground tools in
  tools.py; ground the instructions sink in app.py + the instruction store.
- Goal: the guard becomes redundant AND our enrichment reasons about gaps as
  thoroughly as Wren's.

---

## Report template (all three agents — write to the response dir)

```markdown
# Skill maintenance report — <onboarding | generate-mdl | enrich-context>

## 1. Summary
3–5 bullets: what you changed and the headline parity improvement.

## 2. OUR requirements (extracted from the codebase)
Bulleted, each with file:line evidence.

## 3. Upstream → ours mapping
Table: upstream capability | ported / adapted / dropped | our equivalent | why.

## 4. Files changed
path — one line per change.

## 5. Declared contract changes / deviations (constraint C2)
Any stock-Wren wording removed/changed, and any pattern/invariant deviation.

## 6. Native-correctness changes
How the prompt now makes the model correct from the first token (e.g. how the
properties guard becomes defense-in-depth).

## 7. Parity gaps remaining
What upstream Wren does that we deliberately do NOT (e.g. cubes, queries.yml,
wren memory, grill/auto-pilot) + rationale + whether worth building.

## 8. Recommendations for shared files (no edits — proposals only)
mdl_copilot.md, COPILOT_SKILLS, code changes, or new tools the skill needs.

## 9. Unverified claims / open questions
Any field/tool/key you could NOT confirm in code (flag, don't guess).

## 10. Verification log
The greps/commands you ran to confirm field + tool names exist.
```
