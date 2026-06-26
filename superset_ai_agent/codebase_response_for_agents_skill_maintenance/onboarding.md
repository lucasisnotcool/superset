# Skill maintenance report — onboarding

## 1. Summary

- Replaced the 28-line paraphrase `skills/onboarding.md` with a full tailored
  playbook ported from the genuine upstream `onboarding.SKILL.md`: kept the
  behavioral spine (one-step-per-turn, never-query-before-MDL, never-invent,
  scaffold-by-path, cross-skill routing, on-error) and dropped all Wren-CLI
  plumbing (`wren` commands, `.env`, profiles, credential collection, `wren
  memory`) that does not exist in our stack.
- Encoded OUR requirements **natively** rather than as guard cleanup: the
  `properties` carry-forward contract, the seed-overlay split ("structure from
  the catalog, semantics from the model"), camelCase + required `type`, and
  drafts-with-auto-activation are now positive rules in the prompt.
- Grounded the `properties` keys the prompt names (`displayName`, `alias`,
  `synonyms`) in the actual consumers (`schema_retriever._semantic_terms`,
  `coverage._column_fact`) so the agent writes the terms retrieval really reads.
- Strengthened the sibling LLM prompt `prompts/wren_onboarding.md` to state that
  structure is seeded/authoritative and the model supplies semantics only, with
  the exact `properties` keys and a carry-forward rule.
- Net parity effect: `tools.py:_preserve_superset_properties` becomes
  defense-in-depth — the generation is correct from the first token.

## 2. OUR requirements (extracted from the codebase)

- Onboarding is deterministic Python, not a CLI session: `onboard_schema_project`
  drives base-model generation → validation → write → auto-activation
  (`semantic_layer/onboarding.py:59-147`).
- Structure is seeded from the permission-filtered datasets; the LLM overlays
  semantics only — "structure from the catalog, semantics from the model"
  (`integrations/wren/llm_client.py:427-439`, `_overlay_model_semantics:1083-1112`).
- One model per dataset; `tableReference` is `{schema, table}` for Superset
  datasets (catalog allowed but unset) (`integrations/wren/mdl_exporter.py:78-96`).
- Every column requires `type`, is `isCalculated: false` when physical
  (`mdl_exporter.py:114-130`; `mdl_schema.py:65-78`).
- Seeded model `properties`: `superset_dataset_id`, `superset_database_id`,
  `source` (`mdl_exporter.py:90-94`). Seeded column `properties`:
  `superset_column_name`, `is_time` (`mdl_exporter.py:123-128`).
- Retrieval reads `properties.displayName` / `alias` / `synonyms` (+ description)
  on entities (`schema_retriever.py:105-108`); coverage reads
  `properties.displayName` / `alias` on columns (`coverage.py:163-164`). Dropping
  these silently degrades retrieval/coverage.
- The structure-preserving guard restores `properties` on
  `models|relationships|views|metrics|cubes` and on columns
  (`tools.py:_preserve_superset_properties:538-577`, `_PROPERTIES_SECTIONS:484-490`).
- `joinType` ∈ `ONE_TO_ONE|ONE_TO_MANY|MANY_TO_ONE|MANY_TO_MANY`
  (`mdl_schema.py:46-53`, JOIN_TYPES).
- Validation is structural + physical; a hallucinated table/column stays draft
  but is still written for human correction (`onboarding.py:94-95`).
- Auto-activation is the default; valid models go `active`, invalid stay `draft`;
  the Copilot never activates (`onboarding.py:66, 70-79, 122-136`).
- Readiness gate exposes `empty | indexing | ready | failed`; Copilot edits are
  409-blocked until `ready` (`app.py:_project_readiness:1312-1365`,
  `_require_project_ready:1367-1380`).
- The skill markdown is injected verbatim into the Copilot system prompt under a
  `## Skills` block (`copilot/service.py:_skill_texts:88-97`,
  `loop.py:build_system_prompt:50-68`); `COPILOT_SKILLS = ("onboarding",
  "generate-mdl", "enrich-context")` (`service.py:63`).
- The agent's MDL tools are `list_mdl_files`, `read_mdl_file`, `write_mdl_file`,
  `delete_mdl_file`, `validate_project`, `get_physical_schema`, `list_documents`,
  `search_documents`, `find_duplicate_documents` (`tools.py:129-244`).

## 3. Upstream → ours mapping

| Upstream capability | Ported / adapted / dropped | Our equivalent | Why |
|---|---|---|---|
| One step per round-trip | Ported | Same rule, our steps | Discipline is stack-agnostic |
| Never query before MDL | Ported | `get_physical_schema` reads structure only; query via `usage` after activation | Same invariant |
| Preflight: python/venv/`wren --version`/`pwd` | Adapted | Readiness (`empty/indexing/ready/failed`) + scope (project, schema) + non-empty filtered datasets | We have no CLI env; the real preflight is layer readiness |
| Demo vs own DB branch | Dropped | — | No bundled demo; DB is an existing Superset datasource |
| Project name + DB type collection | Dropped | — | Project/schema already selected upstream of the agent |
| `.env` / credentials in chat / profiles / `wren profile add`/`set-profile` | Dropped | Superset connection + RBAC | No credential handling in our flow |
| `wren context init --empty` scaffold | Adapted | Paths create folders via `write_mdl_file`; `models/`, `relationships.json`, `views/` | JSON files, no mkdir/CLI |
| Generate-MDL handoff + `context validate/build` | Adapted | Deterministic seed + `_overlay_model_semantics`, then structural+physical validation, auto-activate | Same intent, our pipeline |
| `wren memory index` (≥200 models) | Dropped (parity gap) | Activation triggers retrieval indexing automatically; no agent-run reindex | No separate memory store; see §7 |
| Cross-skill routing table | Adapted | `onboarding`/`generate-mdl`/`enrich-context`/`usage`; no `dlt-connector` | Our skill set, no SaaS connector |
| `connect.md#troubleshooting` playbook | Adapted | Compact on-error section (empty context, validation warnings, failed readiness, query-before-ready) | We have no connection troubleshooting surface |

## 4. Files changed

- `superset_ai_agent/skills/onboarding.md` — `cp`'d from
  `wren_upstream_skills/onboarding.SKILL.md`, then fully tailored in place;
  provenance comment replaced with the ASF license header.
- `superset_ai_agent/prompts/wren_onboarding.md` — added the seed/authoritative-
  structure framing, exact `properties` keys (`displayName`/`alias`/`synonyms`),
  the carry-forward rule, and an explicit "re-emit name/tableReference exactly".

## 5. Declared contract changes / deviations (constraint C2)

- Removed all stock-Wren CLI wording (`wren context`, `wren profile`, `wren docs
  connection-info`, `wren skills get`, `wren memory`, `.env`, `wren_project.yml`)
  from the active skill. This is intentional: those mechanisms do not exist in our
  stack and would mislead the agent.
- Cross-skill routing now references our skill names directly (no `wren skills
  get <name>` invocation) because skills are injected into the system prompt, not
  fetched at runtime (`loop.py:build_system_prompt`).
- No MDL field, tool name, `properties` key, or invariant was invented; every
  identifier the skill names is verified in code (see §10).

## 6. Native-correctness changes

- The skill now states the seed/overlay split as a rule ("structure from the
  catalog … your job is semantics"), mirroring `llm_client._overlay_model_semantics`
  — so the model never tries to author structure that the guard would have to fix.
- The `properties` contract is a positive instruction ("carry every existing key
  forward verbatim, then add"), naming the seeded keys (`superset_*`, `is_time`)
  and the consumer keys (`displayName`/`alias`/`synonyms`). The agent emits the
  full preserved object rather than a partial overlay, so
  `tools.py:_preserve_superset_properties` rarely has anything to restore — it is
  defense-in-depth, not the primary mechanism.
- `type` REQUIRED, camelCase keys, and `joinType` enum are stated inline, matching
  `mdl_schema.py`, so validation passes on first generation.
- Drafts-vs-activation is framed as a lifecycle fact (auto-activate valid, keep
  invalid as draft, Copilot never activates) so the agent does not attempt to
  promote unvalidated output.

## 7. Parity gaps remaining

- **`wren memory index` / 200-model threshold** — dropped. We index for retrieval
  on activation; there is no separate, optionally-installed memory store with a
  size threshold. Not worth replicating; the behavior is automatic.
- **Bundled demo (`jaffle_shop`) / quickstart branch** — dropped. No equivalent;
  onboarding always targets a real connected datasource. Not worth building.
- **`dlt-connector` SaaS ingestion routing** — dropped. We ingest via Superset
  datasets, so there is no SaaS-source onboarding branch.
- **Connection troubleshooting playbook** — dropped. Connection/credentials live
  in Superset, outside this agent's surface; nothing to port.
- **`queries.yml` / first-query confirmation step** — not part of base onboarding
  here; querying is the `usage` skill. Recorded, not replicated.

## 8. Recommendations for shared files (no edits — proposals only)

- `prompts/mdl_copilot.md` (shared system prompt): it already states the
  `properties` add-never-strip rule well. Consider naming the **seeded** keys
  explicitly (`superset_dataset_id`, `superset_database_id`, `superset_column_name`,
  `is_time`, `source`) alongside the governance keys, so the Copilot knows those
  catalog-provenance keys must also be carried forward — they are restored by the
  guard (`tools.py:_PROPERTIES_SECTIONS`) but not currently named in the prompt.
- `prompts/mdl_copilot.md`: the column-level `properties` example lists
  `displayName`/`alias`; coverage reads exactly those (`coverage.py:163-164`) and
  retrieval additionally reads `synonyms` on entities (`schema_retriever.py:107`).
  Consider adding `synonyms` to the column `properties` example for consistency
  with what retrieval consumes.
- No code changes or new tools are required for the onboarding skill; all
  referenced tools exist.

## 9. Unverified claims / open questions

- The skill says activation "indexes the layer for retrieval" and flips readiness
  to `ready`. Readiness→`ready` on active files is confirmed
  (`app.py:_project_readiness:1342-1349`); the *indexing* step is implied by the
  retrieval consumers rather than traced to a single reindex call in
  `onboard_schema_project`. Phrased as "indexed for retrieval … no manual reindex
  step for the agent" to avoid overclaiming a specific call.
- `tableReference` `catalog` key: present in the schema (`mdl_schema.py:81-89`) but
  not set by the Superset exporter. Skill says catalog "is allowed but unset" —
  accurate to both.

## 10. Verification log

```
grep -n "displayName\|alias\|synonyms" semantic_layer/schema_retriever.py
  → 105 displayName, 106 alias, 107 synonyms
grep -n "displayName\|alias" semantic_layer/copilot/coverage.py
  → 163 displayName, 164 alias
grep -n "superset_dataset_id\|superset_database_id\|superset_column_name\|is_time\|\"source\"" integrations/wren/mdl_exporter.py
  → 91 superset_dataset_id, 92 superset_database_id, 93 source, 125 superset_column_name, 126 is_time
grep -n "superset_database_id\|semantic_project_id\|schema_name" semantic_layer/wren_materializer.py
  → 64/65/66 dataSource.properties keys
grep -n 'name="' semantic_layer/copilot/tools.py
  → 129 list_mdl_files, 134 read_mdl_file, 143 write_mdl_file, 165 delete_mdl_file,
    177 validate_project, 185 get_physical_schema, 193 list_documents,
    201 search_documents, 223 find_duplicate_documents
grep -n "ONE_TO_ONE\|ONE_TO_MANY\|MANY_TO_ONE\|MANY_TO_MANY" semantic_layer/mdl_schema.py
  → 48-51 JOIN_TYPES enum
grep -n "auto_activate\|status=\"active\"\|status=\"draft\"" semantic_layer/onboarding.py
  → 66 auto_activate default True, 122 elif auto_activate, 129 status="active", 140 active count
COPILOT_SKILLS → service.py:63 ("onboarding","generate-mdl","enrich-context")
build_system_prompt → loop.py:50-68 ("## Skills" block injection)
_require_project_ready → app.py:1367-1380 (409 until ready)
```
