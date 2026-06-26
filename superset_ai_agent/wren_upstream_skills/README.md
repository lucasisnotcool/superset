# Upstream Wren skills — reference copies (third-party)

Verbatim copies of the genuine Wren AI skill prompts, kept locally so the
skill-maintenance agents (see
[`../codebase_prompt_for_agents_skill_maintenance.md`](../codebase_prompt_for_agents_skill_maintenance.md))
can read the real baseline they tailor FROM.

**Provenance:** Canner/WrenAI @ `main`,
`core/wren/src/wren/skills_content/`. Each file's header records its exact source
URL and fetch date. These are **third-party** documents (not Apache-licensed
Superset code) included here only as design reference — they are NOT runtime
inputs and are NOT loaded by the agent. The active skills the agent loads live in
[`../skills/`](../skills/).

Files:
- `onboarding.SKILL.md` — `skills_content/onboarding/SKILL.md`
- `generate-mdl.SKILL.md` — `skills_content/generate-mdl/SKILL.md`
- `enrich-context.SKILL.md` — `skills_content/enrich-context/SKILL.md`
- `enrich-context.references.gap_catalog.md` — `enrich-context/references/gap_catalog.md`
- `enrich-context.references.cube_proposals.md` — `enrich-context/references/cube_proposals.md`
- `AGENTS.md` — `context.py` `_AGENTS_MD_TEMPLATE` (per-project scaffold; the closest
  Wren equivalent of a base/system prompt — structural baseline for
  `../prompts/mdl_copilot.md`)

Query-agent baselines (for `../prompts/{text_to_sql,conversation,sql_reflection,table_selection}.md`):
- `usage.SKILL.md` — `skills_content/usage/SKILL.md` (the NL→SQL **workflow/methodology**:
  recall → context → SQL → dry-plan → execute → store; layered error recovery)
- `wren_langchain_prompt.py` — `sdk/wren-langchain/src/wren_langchain/_prompt.py` (the
  **system-prompt builder** — closest architectural match to our LangGraph query agent;
  the `usage` methodology distilled into prompt form)

Note: there is no `wren-ai-service` on current `main` — WrenAI has consolidated onto the
CLI + skills + SDK model, so the SDK prompt above is the canonical hosted-style baseline.

For the full map of how these baselines feed the runtime prompts (and which agents
maintain them), see **§AB.11 "Prompt network"** in
[`../wren_mdl_copilot.md`](../wren_mdl_copilot.md).

**Note:** because these are third-party, do not add ASF license headers to them.
If the RAT/license pre-commit check flags this directory, add it to
`.rat-excludes` (a repo-config decision left to the maintainer) rather than
relicensing the files. Re-fetch from the source URLs to refresh.
