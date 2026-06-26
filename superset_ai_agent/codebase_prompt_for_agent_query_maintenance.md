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

# Query-agent maintenance agent

ONE agent. Your area is the **query agent** — the half of the system that
*consumes* the MDL semantic layer to answer natural-language data questions
(distinct from the MDL Copilot, which *authors* the layer). You bring our query
prompts to **methodological parity** with genuine upstream Wren, adapted to our
architecture, so the agent reasons about queries as thoroughly as Wren does.

## What the query agent actually is (read first)

Two **LangGraph** graphs, both **structured-output pipelines** (JSON schema per
node — NOT tool-calling, NOT skill-loading):

- `TextToSqlGraph` ([semantic_layer/../graph.py](graph.py)) — one-shot NL→SQL.
  Node chain: model/table selection (`table_selection.md`) → draft SQL
  (`text_to_sql.md`, structured `SqlDraft`) → `validate_sql` → `repair_sql` →
  `execute_sql` → `build_artifacts`.
- `ConversationGraph` ([conversation_graph.py](conversation_graph.py)) — multi-turn
  chat. Uses `conversation.md` + `sql_reflection.md`; handles follow-ups,
  `sql_observations`, reflection, and execution modes (manual / read_only / auto).

Both read the MDL via retrieval (`wren_context`) but never edit it. Read-only
safety (no DDL/DML) is baked into the prompts. There are **no skills** here and no
skill-loading wiring — do not add any.

## Files you own (edit ONLY these)
- `superset_ai_agent/prompts/text_to_sql.md`
- `superset_ai_agent/prompts/conversation.md`
- `superset_ai_agent/prompts/sql_reflection.md`
- `superset_ai_agent/prompts/table_selection.md`

Anything that needs a CODE change (a new planning/decomposition node, store-by-
default behavior, phase-tagged validation errors) goes in your report as a
RECOMMENDATION — do NOT edit `graph.py` / `conversation_graph.py` in this pass
(code changes need tests + review). Charts/`build_artifacts` (the genbi analog)
are out of scope.

## EVOLVE, do not copy (this is NOT the skill pattern)

The skill agents `cp`'d upstream over degraded paraphrases. **Do the opposite.**
Our four query prompts are already OURS and good — lean, Superset-correct
(read-only safety, semantic-layer-authoritative, structured-output discipline).
So you **evolve them in place**, using the Wren baselines only as a methodology
source. Do **not** `cp` any baseline over our prompts. Preserve every existing
contract (return-only-valid-JSON, read-only safety, "never reference a
table/column absent from context") unless you are deliberately improving it.

## Baselines (methodology source — read, don't copy)

| Baseline | What it is | How to use |
|---|---|---|
| `superset_ai_agent/wren_upstream_skills/usage.SKILL.md` | Wren's NL→SQL **workflow** (recall → context → SQL → dry-plan → execute → store; layered error recovery; cube-vs-SQL routing; store-by-default) | The methodology to port |
| `superset_ai_agent/wren_upstream_skills/wren_langchain_prompt.py` | Wren's **system-prompt builder** — closest architectural match (LangChain/LangGraph), the `usage` methodology distilled into prompt form, with strong "by default" phrasing | The prompt-shaped reference |
| `superset_ai_agent/wren_upstream_skills/AGENTS.md` | "Answering data questions" section | Compact workflow reference |

**Critical architecture reconciliation** (the analog of the skills' YAML→JSON): the
wren-langchain baseline is **TOOL-CALLING** (`wren_recall_queries`,
`wren_fetch_context`, `wren_query`, `wren_dry_plan`, `wren_store_query`). OUR query
agent is a **structured-output graph**. So map Wren's workflow **steps onto our
graph NODES / node prompts**, never onto tools:

| Wren step (tool) | Our equivalent |
|---|---|
| `wren_recall_queries` (few-shot) | recalled examples (`RecalledExample`) fed into the draft prompt |
| `wren_fetch_context` | `wren_context` retrieval already in state |
| compose SQL | `text_to_sql.md` / `conversation.md` draft node |
| `wren_dry_plan` (complex only) | our `validate_sql` node |
| `wren_query` | our `execute_sql` node |
| `wren_store_query` (store by default) | conversation persistence (CODE — recommend) |
| phase-based error recovery | our `repair_sql` + `sql_reflection.md` |

---

## Steps

### Step 1 — Analyse OUR query-agent stack (read-only first)
- `graph.py` (`TextToSqlGraph`: the node chain, `SqlDraft` schema, `llm_select_models`,
  `validate_sql`, `repair_sql`, `execute_sql`, `build_artifacts`).
- `conversation_graph.py` (`ConversationGraph`: nodes, reflection loop, execution
  modes, `sql_observations`, `attempted_sql`, `reflection_feedback`).
- The four prompts you own + the structured-output schemas they must satisfy
  (so your edits never break JSON validity).
- The retrieval path that builds `wren_context` and `RecalledExample` (confirm
  what context the draft node actually receives — your guidance must match it).
- Confirm read-only enforcement (where DDL/DML is blocked) so you keep the safety
  language accurate.
Produce an "OUR query-agent requirements" list (the invariants + the methodology
gaps) with file:line evidence.

### Step 2 — Step through each prompt, line by line, and evolve it
Walk `text_to_sql.md`, `conversation.md`, `sql_reflection.md`, `table_selection.md`
top to bottom (keep/revise/delete/add per line). Bake the methodology in
**natively** so behavior is correct from the first token (the query-agent analog
of "properties native"):

- **Layered error recovery → `sql_reflection.md` (+ repair guidance in
  `conversation.md`).** Teach the reviewer to distinguish *semantic/MDL-level*
  failures (wrong model/column, ambiguous column, undefined join) from
  *dialect/DB-level* failures (type mismatch, unsupported function, permission,
  timeout) and to give different `retry_feedback` for each — "fix one issue, then
  re-validate." This is the single highest-value port.
- **Complexity assessment / decomposition → `text_to_sql.md` / `conversation.md`.**
  Add guidance to decompose multi-metric / month-over-month / by-segment questions
  into sub-queries (mirroring usage Step 2.5), with the "don't over-decompose
  simple GROUP BY" caveat.
- **Recall as few-shot, strong default → draft prompts.** State that recalled
  example pairs are templates to prefer over from-scratch SQL, and not to dismiss
  them just because a question "seems simple" (use Wren's strong-default phrasing;
  soft phrasing reads as "skip").
- **Semantic-layer authority** — keep/strengthen: map business terms to MDL
  model/column descriptions, use defined relationships for joins, prefer defined
  metric expressions; the layer adds meaning only — never reference a
  table/column absent from context.
- **Preserve hard contracts:** return-only-valid-JSON matching the schema,
  read-only safety, one query at a time, conservative LIMIT, explicit columns.
- Strip nothing that is a safety or structured-output rule. Remove any wording
  that doesn't match our actual node behavior.

### Step 3 — Cross-prompt coherence (the four are ONE pipeline)
The four prompts run as a single pipeline; keep them consistent:
- The draft prompt (`text_to_sql.md` / `conversation.md`), the reviewer
  (`sql_reflection.md`), and the selector (`table_selection.md`) must use the same
  vocabulary for context, retries, and the semantic layer. Resolve any drift.
- Ensure `retry_feedback` produced by `sql_reflection.md` is phrased the way the
  draft prompt expects to consume it (closed loop).
- Keep `table_selection.md` aligned with how the draft prompt expects the focused
  model set.

### Step 4 — Decide prompt-vs-code; handle the inert skill
- Put in your report (RECOMMENDATIONS, no edits): any methodology that needs code —
  e.g. a planning/decomposition node, store-by-default persistence, phase-tagged
  validation errors surfaced to `sql_reflection.md`, cube-vs-SQL routing (only if
  cubes get wired). Note cost per item.
- `superset_ai_agent/skills/usage.md` is currently **inert** (loaded by nothing —
  the query graphs use no skills). Recommend either deleting it or demoting it to a
  reference doc; do NOT wire skill-loading into the query agent.

### Step 5 — Verify
- Grep to confirm every field/state key you reference exists in `graph.py` /
  `conversation_graph.py` and the structured-output schemas.
- Run the query-agent tests:
  `python -m pytest tests/unit_tests/superset_ai_agent/ -q -k "graph or conversation or sql or text_to_sql"`
  (and the broader agent suite if quick). Keep them green; your edits are prompt
  text but tests may assert on prompt-driven behavior.

### Step 6 — Report
Write `superset_ai_agent/codebase_response_for_agents_skill_maintenance/query_agent.md`
using the template below.

---

## Tips / guardrails
- Evolve, don't copy. Don't add tool-calling or skill-loading — keep the
  structured-output pipeline.
- The gap here is *methodology*, not over-prompting; our prompts are already lean.
  Add reasoning (decomposition, layered repair, recall discipline), keep it tight.
- Strong defaults beat soft phrasing (Wren's empirical finding) — "do X by
  default; skip only when …" outperforms "consider X when useful."
- Ground every claim about state/fields in the graph code; flag anything you
  can't confirm rather than guessing.

## Report template (write to the response dir)

```markdown
# Query-agent maintenance report

## 1. Summary
3–5 bullets: what changed across the four prompts and the headline methodology gain.

## 2. OUR query-agent requirements (extracted)
Invariants + methodology gaps, each with file:line evidence.

## 3. Upstream → ours mapping
Table: Wren workflow step (tool) | our node / prompt | ported / adapted / dropped | why.

## 4. Prompt changes
Per file (text_to_sql / conversation / sql_reflection / table_selection): line-level
keep/revise/delete/add summary.

## 5. Native-correctness changes
How behavior is now correct from the first token (layered error recovery,
decomposition, recall discipline) instead of relying on post-hoc fixes.

## 6. Cross-prompt coherence
How the four prompts were aligned into one consistent pipeline.

## 7. Prompt-vs-code decisions + parity gaps
What you put in prompts vs what needs code (with cost). The fate of skills/usage.md.
Remaining parity gaps vs Wren (e.g. store-by-default, cube routing) + rationale.

## 8. Recommendations (no edits — proposals only)
graph.py / conversation_graph.py / persistence / retrieval changes, prioritized.

## 9. Unverified claims / open questions
Any state key / field / behavior you could not confirm in code.

## 10. Verification log
Greps + the pytest result for the query-agent tests.
```
