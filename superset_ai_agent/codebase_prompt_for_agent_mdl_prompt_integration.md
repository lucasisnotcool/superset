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

# MDL Copilot base-prompt integration agent (the 4th agent)

ONE agent, run **after** the three skill-maintenance agents have finished
(`onboarding`, `generate-mdl`, `enrich-context` are already tailored). Your job is
to produce the final, polished **base system prompt** for the MDL Copilot —
`superset_ai_agent/prompts/mdl_copilot.md` — using genuine Wren as a structural
baseline, reconciling the three agents' recommendations, and getting the layering
between the base prompt and the three skills right (no over-prompting, no
under-prompting, no duplication).

## What the main MDL agent actually runs (read this first)

The Copilot's effective system prompt is **assembled at run time** by
`build_system_prompt` ([semantic_layer/copilot/loop.py](semantic_layer/copilot/loop.py)):

```
[ prompts/mdl_copilot.md ]            ← the BASE system prompt (you own this file)
## Skills
[ onboarding ][ generate-mdl ][ enrich-context ]   ← all three, ALWAYS injected
## Operator instructions for this schema
[ recalled per-schema instructions ]
```

Facts that govern your work:
- The base prompt is `get_prompt("mdl_copilot")` → `prompts/mdl_copilot.md`.
- The three skills are loaded by `COPILOT_SKILLS` in
  `semantic_layer/copilot/service.py` and concatenated into **every** run's system
  prompt — they are **always-on**, not fetched on demand, not tool-called.
- So the "final MDL agent prompt" the model sees = **base + all three skills +
  instructions**. You are tuning the BASE and the BASE↔SKILLS division of labor.

## Files you own (edit ONLY these)
- `superset_ai_agent/prompts/mdl_copilot.md`  — the base system prompt.

Do NOT edit the three skills (`skills/onboarding.md`, `skills/generate-mdl.md`,
`skills/enrich-context.md`) — they are finished and owned by the three prior
agents. If a skill needs a change, put it in your report as a RECOMMENDATION.

## Baseline & inputs
- **Wren baseline (structural):** `superset_ai_agent/wren_upstream_skills/AGENTS.md`
  — the closest Wren equivalent of a base/system prompt. Use its **skeleton**
  (project intro → answering data questions → modifying the data model →
  prerequisites → quick reference), NOT its CLI commands. Its "Modifying the data
  model" section is what maps to the Copilot.
- **The three finished skills:** `superset_ai_agent/skills/{onboarding,generate-mdl,enrich-context}.md`.
- **The three agents' reports:**
  `superset_ai_agent/codebase_response_for_agents_skill_maintenance/{onboarding,generate-mdl,enrich-context}.md`
  — especially each report's section 8 ("Recommendations for shared files") and
  section 7 ("Parity gaps"). These are the inputs you reconcile.
- **The current base prompt:** `superset_ai_agent/prompts/mdl_copilot.md`.

## IMPORTANT — evolve, do not overwrite (unlike the skill agents)

The skill agents copied the upstream file over a degraded paraphrase. **Do the
opposite here.** `mdl_copilot.md` is already OURS and reasonably good; the Wren
`AGENTS.md` is CLI-centric and would be a downgrade if copied wholesale. So you
**evolve the existing `mdl_copilot.md` in place**, using `AGENTS.md` only as a
structural reference for section ordering and coverage. Do not `cp` AGENTS.md over
mdl_copilot.md. Preserve every hard rule already in mdl_copilot.md unless you are
deliberately relocating it into a skill (and say so in the report).

---

## Steps

### Step 1 — Analyse our stack for the cross-cutting Wren requirements

The base prompt must hold the invariants that span **all three** skill areas
(onboarding, generate-mdl, enrich-context). Read (read-only) and extract them:

- `semantic_layer/copilot/loop.py` (`build_system_prompt`, the tool-call loop) and
  `service.py` (`COPILOT_SKILLS`, `run_copilot`) — how base + skills + tools combine.
- `semantic_layer/copilot/tools.py` — the exact tool surface the base prompt must
  describe (names + when to call each), and `_preserve_superset_properties` (the
  guard you are making the prompt redundant against, natively).
- `semantic_layer/mdl_schema.py` / `mdl_authoring.py` — the MDL field contract
  (camelCase keys; the `properties` bag on model/column).
- `semantic_layer/mdl_validator.py` + `wren_core_validator.py` — what validation
  enforces vs tolerates (confirm `properties` removal is NOT caught: the WHY).
- `semantic_layer/schema_retriever.py` (`_semantic_terms`) and
  `copilot/coverage.py` (`_column_fact`) — the consumers that silently degrade if
  `properties` (displayName/alias/synonyms) is dropped; confirm EXACT key names.
- `app.py` readiness gate (`_require_project_ready`) — the lifecycle the base
  prompt should assume (the layer is onboarded/stable before the Copilot edits).

Produce a short "base-prompt invariants" list (the rules that belong in the BASE
because they are true regardless of which skill is active), each with file:line.

### Step 2 — Step through `mdl_copilot.md` line by line and rewrite

Walk the current `prompts/mdl_copilot.md` top to bottom. For each line decide
keep / revise / delete / move-to-skill / add. Bake correctness in **natively**:

- **`properties` native (the canonical example):** the base prompt must state, as
  a positive hard rule, that any re-emitted model/column carries its existing
  `properties` (displayName/alias/synonyms — the EXACT keys you confirmed) forward
  verbatim and only adds to them — so generations are correct from the first
  token. Do not phrase it as "a guard will fix dropped properties"; the guard is
  defense-in-depth, the prompt is the primary mechanism.
- **Physical authority:** never invent/rename/retype a physical table/column;
  structure comes from `get_physical_schema`; semantics only from model/docs.
- **MDL shape:** JSON with camelCase keys; every column needs `type`. (We author
  JSON directly — there is no YAML/`wren context build` step. Strip any such
  wording inherited from Wren.)
- **Tool contract + loop discipline:** describe the tools and the
  edit→`validate_project`→fix→finish loop; propose-don't-persist; drafts only.
- **Structure:** reshape the base to the AGENTS.md skeleton adapted to us — what
  the Copilot is, how it works (tools + loop), the hard contracts, and a compact
  quick-reference — with NO CLI commands we don't have.

### Step 3 — Understand context from the three skills and de-duplicate (layering)

Read the three finished skills and decide the **division of labor** so the
assembled prompt (base + 3 skills) says each thing exactly once:

- **Base = always-true invariants**: tool contract, the `properties`/physical
  rules, MDL shape, validate-before-finish, propose-don't-persist, output
  discipline.
- **Skills = task procedures**: the onboarding sequence, the generate-mdl example
  + field discipline, the enrich-context gap catalog + document workflow.
- Remove from the base anything a skill now fully owns; remove from your mental
  model anything the base now owns that a skill still repeats (note the latter as
  a skill RECOMMENDATION — you may not edit skills). Flag any **conflict** between
  a skill and the base and resolve it in the base's favor for invariants.

### Step 4 — Reconcile the three reports' recommendations

Merge section 8 ("Recommendations for shared files") from all three reports into a
single coherent set of base-prompt edits. Where two reports recommend conflicting
base wording, choose one and record why. Carry forward each report's section 7
("Parity gaps") into your own report so the gaps are tracked in one place.

### Step 5 — Decide the over/under-prompting architecture

State the choice explicitly:
- **Option A (default, prompt-only):** keep always-on injection but keep the base
  lean and rely on the skills for depth; ensure the assembled prompt is not bloated
  or self-repeating. Implement this now.
- **Option B (parity, code change — recommend, don't build):** on-demand skill
  loading via a new `get_skill`-style tool in `MdlToolset`, so only the relevant
  skill is pulled per task (true Wren `wren skills get` parity). Note the cost
  (tool + loop wiring) and leave it as a recommendation.

### Step 6 — Verify

- Confirm every field/key/tool you mention exists (grep the code).
- Sanity-check the assembled prompt: construct it mentally (or via a scratch call
  to `build_system_prompt`) and check size + coherence + no duplication.
- Run the copilot tests:
  `python -m pytest tests/unit_tests/superset_ai_agent/test_copilot_*.py -q`
  (the inspector test asserts the base prompt advertises tools — keep it green).

### Step 7 — Report

Write `superset_ai_agent/codebase_response_for_agents_skill_maintenance/mdl_copilot_integration.md`
using the template below.

---

## Tips / guardrails
- You are the SOLE editor of `mdl_copilot.md`; run only after the three are done.
- Evolve, don't overwrite. Preserve existing hard rules unless deliberately moved.
- The single biggest risk is over-prompting now that skills are long — your value
  is ruthless de-duplication and clean base↔skill layering.
- Ground every `properties` key claim in `schema_retriever.py` / `coverage.py`;
  ground tool names in `tools.py`. Don't guess; flag unconfirmed items.

## Report template (write to the response dir)

```markdown
# MDL Copilot base-prompt integration report

## 1. Summary
3–5 bullets: what changed in mdl_copilot.md and the headline layering improvement.

## 2. Base-prompt invariants (what now lives in the base, and why)
Bulleted, each with file:line evidence.

## 3. Base ↔ skills layering map
Table: concern | lives in base | lives in which skill | (was it duplicated before?).

## 4. mdl_copilot.md changes
Line-level keep/revise/delete/move-to-skill/add summary.

## 5. Native-correctness changes
How the base now makes the model correct from the first token (properties, physical
authority) — and how the tools.py guard becomes defense-in-depth.

## 6. Reconciled recommendations
How you merged the three reports' shared-file recommendations; conflicts + resolution.

## 7. Over/under-prompting decision
Option A vs B: what you implemented, assembled-prompt size before/after, and the
Option B recommendation with its cost.

## 8. Skill recommendations (no edits — proposals only)
Anything the finished skills should change for clean layering.

## 9. Consolidated parity gaps
The union of the three reports' parity gaps + any new ones, with rationale.

## 10. Verification log
Greps + the pytest result for test_copilot_*.py.
```
