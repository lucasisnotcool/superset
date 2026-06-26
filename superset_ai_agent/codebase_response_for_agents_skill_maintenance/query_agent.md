# Query-agent maintenance report

## 1. Summary
- Ported Wren's **layered error recovery** into `sql_reflection.md` and the two
  draft prompts: the reviewer/drafter now classifies a failure as
  *semantic-layer (MDL)* vs *database/dialect* vs *empty-result* vs *duplicate*
  and writes layer-specific feedback, fixing **one root cause at a time** — the
  single highest-value methodology gain.
- Added **complexity assessment / decomposition** to both draft prompts, adapted
  to our architecture: `text_to_sql.md` (one-shot) folds decomposition into ONE
  CTE/subquery statement; `conversation.md` can additionally spread sub-questions
  across `remaining_sql_iterations`. Both carry the "don't over-decompose a
  simple GROUP BY" caveat.
- Promoted **recall as a strong default** in both draft prompts: `recalled_examples`
  are confirmed templates to build on, explicitly *"do not dismiss because the
  question seems simple"* (Wren's empirical strong-default phrasing).
- Strengthened/kept every hard contract (return-only-valid-JSON, read-only safety,
  one query at a time, conservative LIMIT, explicit columns, semantic-layer
  authority, "never reference a table/column absent from context") and aligned the
  four prompts on one vocabulary.
- **No code touched**, **no tool-calling or skill-loading added.** Query-agent
  tests stay green (90 passed, 1 skipped).

## 2. OUR query-agent requirements (extracted)
Invariants the prompts MUST preserve (with evidence):
- Draft prompts must emit JSON matching their schema — `SqlDraft{sql, explanation}`
  (`graph.py:197-201`) and `ConversationDraft{response_type, message, sql,
  explanation}` (`conversation_graph.py:114-122`); reviewer must match
  `SqlReflection{outcome, message, retry_feedback}` (`conversation_graph.py:125-143`).
- Read-only is enforced in code by `validate_read_only_sql`
  (`graph.py:710`, `conversation_graph.py:1137`); the prompt language must stay
  accurate to that gate, not replace it.
- One query per draft: `SqlDraft.sql` is a single string; the one-shot graph has no
  multi-query node (`graph.py:332-367`). The conversation graph *can* iterate via
  the reflect→draft loop bounded by `max_agent_sql_iterations`
  (`conversation_graph.py:1541-1549`, `config.py:106`).
- Context the draft node actually receives: `recalled_examples`, `wren_context`
  (with `context_items`), `instructions`, `validation_errors_to_fix`,
  `semantic_sql_mode` — `graph.py:959-974`; conversation adds `sql_observations`,
  `attempted_sql`, `reflection_feedback`, `remaining_sql_iterations`,
  `execution_mode` — `conversation_graph.py:1577-1605`.
- The reviewer receives `sql_observations`, `attempted_sql`, `latest_sql`,
  `remaining_sql_iterations`, `wren_context` — `conversation_graph.py:1655-1671`;
  observations carry `error`, `row_count`, `is_empty`, `is_duplicate`
  (`conversation_graph.py:1842-1865`).
- Selector receives `question`, `candidate_models`, `max_models`; returns
  `{models:[...]}` validated against candidates — `graph.py:128-158`.

Methodology gaps that were closed:
- No layered (semantic-vs-dialect) error triage in `sql_reflection.md` or the
  repair path — repair folds `validation.errors + engine_warnings + dry_plan`
  diagnostics (`graph.py:745-760`) but the prompt never told the model how to
  read them.
- No complexity/decomposition guidance in either draft prompt.
- Recall was only implied ("use prior artifacts"); no strong-default phrasing.

## 3. Upstream → ours mapping
| Wren step (tool) | Our node / prompt | Status | Why |
|---|---|---|---|
| `wren_recall_queries` (few-shot) | `recalled_examples` → `text_to_sql.md` / `conversation.md` | ported | Strong-default recall block added to both draft prompts. |
| `wren_fetch_context` | `load_wren_context` → `wren_context` in state | adapted | Already retrieval-backed; prompts keep treating `context_items` as authoritative. |
| compose SQL | `draft_sql` / `draft_response` | ported | Decomposition + complexity assessment added. |
| `wren_dry_plan` (complex only) | `validate_sql` (+ `dry_plan_with_wren` diagnostics) | adapted | We always dry-plan when enabled; diagnostics feed repair. Prompt teaches reading them. |
| `wren_query` | `execute_sql` | unchanged | Code path; out of prompt scope. |
| `wren_store_query` (store by default) | `memory.store_confirmed` in `execute_sql` | already in code | `graph.py:813-823`, `conversation_graph.py:1394-1406` — store-by-default is implemented; no prompt change needed. |
| phase-based error recovery | `repair_sql` + `correct_semantic_sql` + `sql_reflection.md` | ported | Layered MDL-vs-dialect triage is now in the reviewer + both draft prompts. |
| cube-vs-SQL routing | (none) | dropped | Cubes are not wired into the query path; see §7. |

## 4. Prompt changes
**text_to_sql.md** — kept all 11 original rule/semantic/instruction lines verbatim.
Added: a "Recalled examples (few-shot — strong default)" block; a "Complexity
assessment" block that decomposes into a single CTE/subquery statement (one-shot
constraint) with the over-decompose caveat; a "Fixing prior failures
(`validation_errors_to_fix`)" block with semantic-vs-dialect triage and
fix-one-root-cause.

**conversation.md** — kept all original rules. Added (in the rules list, same
voice): strong-default recall; complexity/decomposition that can either use CTEs
or spread across `remaining_sql_iterations`; layered triage of
`validation_errors_to_fix`. No change to the answer-vs-sql routing contract.

**sql_reflection.md** — kept all 9 original rules verbatim. Added a "Diagnosing a
failed observation" block: classify semantic / dialect / empty-result / duplicate
and write layer-specific `retry_feedback`, one root cause at a time; plus a
"prefer answer over speculative retry" guard to protect the retry budget.

**table_selection.md** — one line revised so "joins implied by the question" reads
"models reached through the semantic layer's defined relationships," matching the
join vocabulary of the draft prompts. Otherwise unchanged (already lean/correct).

## 5. Native-correctness changes
- Error recovery is now correct from the first repair token: the drafter is told
  *which layer* a `validation_errors_to_fix` entry belongs to and the specific fix
  shape (exact name from `wren_context`, qualify ambiguous column, CAST,
  dialect-neutral function, simplify) rather than blindly re-drafting.
- The reviewer produces `retry_feedback` that is already layer-targeted and
  single-issue, so the next draft is verifiable instead of churning.
- Decomposition is baked into the draft step, so complex questions produce a
  correct composed query (or a planned multi-turn sequence) on the first attempt
  instead of relying on post-hoc repair.
- Recall is a default, not an afterthought, so confirmed templates steer the very
  first draft.

## 6. Cross-prompt coherence
The four prompts share one vocabulary: `recalled_examples` (templates, strong
default), `wren_context`/`context_items` (authoritative semantic layer),
`validation_errors_to_fix` (drafter input), `retry_feedback`/`reflection_feedback`
(reviewer→drafter loop), `attempted_sql` (no duplicates), and the **semantic-layer
(MDL) vs database/dialect** error split used identically in `sql_reflection.md`,
`conversation.md`, and `text_to_sql.md`. The reviewer's `retry_feedback` is phrased
as drafter instructions, closing the loop with how `conversation.md` consumes
`reflection_feedback` (`conversation_graph.py:1517,1592`). `table_selection.md`
now describes its output as the focused model set the draft prompts join over.

## 7. Prompt-vs-code decisions + parity gaps
- **In prompts (this pass):** layered error triage, decomposition guidance,
  strong-default recall, vocabulary alignment. No schema or node behavior changes,
  so JSON validity and routing are untouched.
- **Already satisfied in code (no action):** store-by-default
  (`memory.store_confirmed` runs automatically on successful execution),
  duplicate-SQL suppression (`attempted_sql` + `_sql_match_key`), retry budget
  (`max_agent_sql_iterations`), engine-feedback correction loop
  (`correct_semantic_sql`).
- **Needs code (RECOMMENDATIONS, see §8):** phase-tagged validation errors
  surfaced structurally to `sql_reflection.md`; a true planning/decomposition node
  for the one-shot graph; cube routing.
- **skills/usage.md:** confirmed **inert** — no skill-loading exists in `graph.py`,
  `conversation_graph.py`, `prompts/registry.py`, or `skills/__init__.py`; the
  graphs only call `get_prompt(...)`. Recommend **demoting it to a reference doc**
  (it duplicates the now-evolved methodology). Do NOT wire skill-loading in.
- **Remaining parity gaps vs Wren:** (a) cube-vs-SQL routing — cubes aren't wired
  into the query path, so the routing in `usage.SKILL.md` has no analog; defer
  until cubes are materializable. (b) Wren's `error.phase` is a structured field;
  ours arrives as free-text in `validation.errors`/`sql_observations`, so the
  layer classification is model-inferred from the message rather than read from a
  tag.

## 8. Recommendations (no edits — proposals only)
1. **Phase-tag validation/engine errors** (med cost): attach a `phase`
   (`SQL_PARSING` / `MDL` / `SQL_EXECUTION`) to entries in `validation.errors`,
   `engine_warnings`, and `sql_observations[].error` so `sql_reflection.md` reads
   the layer instead of inferring it. Highest-leverage code follow-up; makes the
   §4 triage deterministic.
2. **Decomposition node for the one-shot graph** (high cost): the conversation
   graph can iterate; `TextToSqlGraph` cannot. A planner node that emits
   sub-questions would let one-shot mirror Wren's Step 2.5 without forcing
   everything into a single CTE. Lower priority — the CTE guidance covers most
   cases.
3. **Surface `dry_plan` diagnostics into reflection** (low cost): `dry_plan_diagnostics`
   (`graph.py:161-194`) already feeds repair; thread the same into the conversation
   reflection payload so the reviewer sees engine planning errors, not just
   execution errors.
4. **Demote `skills/usage.md`** to `references/` or delete (low cost): it is loaded
   by nothing and now duplicates evolved prompt methodology.
5. **Cube routing** (deferred): only if/when cubes are wired into materialization +
   execution; revisit `usage.SKILL.md`'s cube decision tree then.

## 9. Unverified claims / open questions
- None of the prompt-referenced keys were unverifiable — all are present in the
  payloads (see §10). Open question: whether `wren_context.context_items` always
  contains the exact model/column names the triage feedback tells the model to use
  when the retriever caps items (`wren_max_context_items`); if a needed name is
  capped out, the "use the exact name from wren_context" instruction can't be
  satisfied. Not a regression (pre-existing), but worth noting for recommendation 1.

## 10. Verification log
- Confirmed every referenced payload key exists:
  `grep -n '"validation_errors_to_fix"|"recalled_examples"|"remaining_sql_iterations"|"attempted_sql"|"sql_observations"|"reflection_feedback"|"candidate_models"|"max_models"|"instructions"' graph.py conversation_graph.py`
  → all present (`graph.py:130-131,595-596,966-973`;
  `conversation_graph.py:438-446,1356-1517,1659-1661`).
- Confirmed `skills/usage.md` is inert: no `get_skill`/`load_skill`/`skills/`
  references in `graph.py` / `conversation_graph.py` / `prompts/registry.py` /
  `skills/__init__.py`.
- Confirmed no test asserts on the four prompts' system text (only
  `test_graph.py:395` asserts on the *user payload* content).
- Tests: `python -m pytest tests/unit_tests/superset_ai_agent/ -q -k "graph or
  conversation or sql or text_to_sql"` → **90 passed, 1 skipped, 469 deselected**.
