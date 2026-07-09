# Oracle ORA-00911 semantic-pipeline fix — implementation checklist

Status: SHIPPED (2026-07-09) — all items complete; pending live verification on
the Windows/Oracle box (commit + push + rebuild required; see residual risks).

## Root cause (verified by live repro)

wren-core (0.7.1) expands every model as
`SELECT __source.col AS col FROM schema.table AS __source` with the internal
`__source` alias **unquoted**. Oracle rejects any nonquoted identifier that does
not start with a letter with **ORA-00911 "invalid character"**, so every
semantic query on Oracle fails at execution — the dialect-finalization stage
(`dialect_finalize.py`) transpiles clauses but leaves the alias unquoted
(sqlglot preserves the unquoted flag, and `_fold_lowercase_identifiers_upper`
only touches quoted identifiers).

Repro (venv wren-core + sqlglot 30.11), semantic
`SELECT COUNT(DISTINCT lot_id) AS current_wip FROM table_x` →

```
SELECT COUNT(DISTINCT table_x.lot_id) AS current_wip
FROM (SELECT table_x.lot_id
      FROM (SELECT __source.lot_id AS lot_id
            FROM schema_f.table_x __source) table_x) table_x
FETCH FIRST 1000 ROWS ONLY
```

Two compounding retry-loop defects were confirmed in the same session:

- All limit-clause variants canonicalize to the identical executed string
  (finalize rewrites `LIMIT`→`FETCH FIRST`; `apply_limit` appends the same cap
  when absent), so the conversation-graph duplicate gate
  (`_execute_sql`, keyed on `_sql_match_key(validation.normalized_sql)`)
  blocks a limit-syntax-only retry with a generic "materially different query"
  message that hides the real ORA-00911 error.
- wren-core cannot parse `FETCH FIRST ... ROWS ONLY` in *semantic* SQL
  ("FETCH clause is not supported yet" — DataFusion), yet
  `finalization_guidance` steers the model toward "ANSI-standard SQL", whose
  standard row-cap clause is exactly `FETCH FIRST`.

## Checklist

- [COMPLETE] **F1 — quote invalid-unquoted identifiers in the Oracle finalize
  pass** (`superset_ai_agent/semantic_layer/engine/dialect_finalize.py`).
  Added `_ORACLE_SAFE_UNQUOTED` + `_quote_invalid_unquoted_identifiers`: any
  unquoted identifier not matching `^[A-Za-z][A-Za-z0-9_$#]*$` gets
  `quoted=True`. Runs AFTER the uppercase fold so newly-quoted names (whose
  true case SQLAlchemy preserves, e.g. `__source`, `_weird`) are NOT
  case-folded. Oracle only (T-SQL allows leading `_`).
  Evidence: 5 new tests in `test_dialect_finalize.py`
  (`test_oracle_quotes_wren_source_alias` etc.), 20/20 pass; live pipeline
  repro (wren-core transform → finalize → apply_limit) now emits
  `... FROM schema_f.table_x "__source") table_x ... FETCH FIRST 1000 ROWS
  ONLY` — Oracle-legal, alias quoted consistently at definition and reference.

- [COMPLETE] **F2 — duplicate gate surfaces the prior attempt's real error**
  (`superset_ai_agent/conversation_graph.py` `_execute_sql` +
  `_prior_attempt_error`). On a duplicate hit, the newest non-duplicate
  observation with the same `_sql_match_key` contributes its `error` to the
  duplicate observation, plus an explicit note that limit clauses are
  normalized so a limit-syntax-only change is not a different query.
  Evidence:
  `test_duplicate_sql_observation_repeats_the_prior_execution_error` passes
  (asserts the ORA-00911 text reappears in the duplicate observation).

- [COMPLETE] **F3 — authoring guidance: LIMIT, not FETCH FIRST**
  (`dialect_finalize.finalization_guidance` + `_SEMANTIC_SQL_GUIDANCE` in BOTH
  `conversation_graph.py` and `graph.py` — the string is intentionally
  duplicated in the two graphs). Guidance says: write `LIMIT n`; the semantic
  engine rejects the FETCH clause; LIMIT is transpiled to the backend's
  clause. "ANSI-standard" phrasing removed (ANSI's row cap IS `FETCH FIRST`).
  Evidence: `test_finalization_guidance_steers_limit_not_fetch` +
  `test_semantic_guidance_steers_limit_in_both_graphs` (also pins the two
  copies byte-identical).

- [COMPLETE] **F4 — verification**: full agent pytest subset — 1557 passed,
  13 skipped, 1 failed
  (`test_multi_schema_schema_index.py::test_bulk_activate_fetches_live_schema_once_and_deactivate_zero`)
  — confirmed PRE-EXISTING by `git stash` re-run on the clean tree (known
  break from commit 67a205e383, flagged 2026-07-09 conversation-management
  session). Ruff check + format clean on all touched files. End-to-end repro
  re-run: executed SQL is Oracle-legal.

## Residual risks / known gaps

- **R1 — no live Oracle in dev**: the fix is verified against Oracle's
  documented identifier grammar and the real wren-core/sqlglot pipeline, but
  not against a live Oracle 19c. Needs the standard Windows-box cycle:
  commit + push, pull, image **rebuild** (`build` + `up -d --force-recreate`,
  not `restart`), then re-run the failing question. Verify with the grep
  marker `_ORACLE_SAFE_UNQUOTED` in
  `/app/superset_ai_agent/semantic_layer/engine/dialect_finalize.py`.
- **R2 — lowercase-quoted-stored names**: the pre-existing uppercase fold
  already assumes lowercase = uppercase-stored; a genuine lowercase-stored
  QUOTED Oracle column (`"_weird"` created quoted-lowercase) that wren emits
  quoted would still be wrongly uppercased. Not made worse by this change
  (newly-quoted identifiers are exempt from the fold), rare in practice.
- **R3 — duplicate gate still blocks genuinely-identical retries by design**:
  after F2 the model sees the original driver error, but if it keeps emitting
  the same SQL the turn still ends in clarify/answer without execution. That
  is intended; the fix only removes the misleading message.
- **R4 — `FETCH FIRST` in semantic SQL still fails at wren-core parse** (the
  DataFusion gap is upstream). F3 steers the model away from it and the
  rejection is routed as a correctable warning into the re-draft loop, but a
  stubborn draft could still burn an iteration.
- **R5 — one-shot graph (`graph.py`) has no duplicate gate** — F2 applies to
  the conversation graph only; the one-shot path never had the gate, so no
  regression, but its retry loop cannot benefit from prior-error surfacing.
