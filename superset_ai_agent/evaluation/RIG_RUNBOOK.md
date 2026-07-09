# Reusable eval rig — runbook (the on-box agent's contract)

This is the linear, script-only procedure for running an evaluation against real
data (e.g. Oracle) with human/agent-authored questions. You (the on-box agent)
supply **only env and data**; every prompt, script, and piece of infra is already
here. Design + rationale: `docs/plans/plan_eval_rig_reusable_impl.md`.

Two commands do everything: `prepare/run_prepare.py` (inputs → fixture) and
`run_rig.py` (fixture → results).

---

## 0. Prerequisites (one-time)

- Run **in-repo** (the rig imports the agent's own scorers/judge). Activate nothing;
  invoke the interpreter directly, e.g. `venv/bin/python`.
- `superset_ai_agent/.env` present and valid (provides the model client for the
  judge **and** the generators — same key, no second secret).
- The local Docker stack is **up** (`make up-ai`); it brokers the DB connection.
- The **Oracle connection** exists in Superset (the stack reaches the off-local DB
  via its URI). Either an admin registered it, or set an env var with the URI and let
  preflight register it:
  ```bash
  export EVAL_ORACLE_URI='oracle+oracledb://user:pass@host:1521/?service_name=...'
  ```
  The Oracle driver must be in the **Superset** image (not the rig/agent).
- Point the rig at the stack:
  ```bash
  export EVAL_AGENT_BASE_URL=http://localhost:8090/ai-agent
  export EVAL_SUPERSET_BASE_URL=http://localhost:8090
  export EVAL_SUPERSET_USERNAME=admin EVAL_SUPERSET_PASSWORD=admin
  ```

## 1. Dump inputs

Put the source material in an `inputs/` folder:
- `*.md` — business context an LLM needs (glossaries, metric definitions, rules).
- `*.csv` — **real data samples** (their headers ground gold-SQL generation) and/or
  loose question lists to extract.
- Optionally a file with `schema` in its name (e.g. `schema_notes.md`) — its full
  text is treated as schema context.

## 2. Prepare (inputs → fixture) — agent-driven, review before trusting

```bash
venv/bin/python superset_ai_agent/evaluation/prepare/run_prepare.py \
    --inputs inputs --out fixture \
    --fixture-id oracle_v1 --database-name <SUPERSET_DB_NAME> \
    --review
```
This runs, in order: **2a** BI doc → `fixture/context/bi_context.md`; **2d** target
schemas → `fixture/fixture.yaml`; **2b/2c** question corpus → `fixture/questions.csv`
(each `gold_sql` is **executed to validate it**; ones that error or return nothing are
dropped and reported). `--review` writes the drafts and prints a summary.

**Review the drafts** (`fixture/questions.csv`, `fixture/context/bi_context.md`,
`fixture/fixture.yaml`): fix any `<FILL_ME>`, confirm the target schemas, spot-check a
few gold-SQLs and eval-note rubrics. Edit the CSV directly if needed. Use
`--connection-uri-env EVAL_ORACLE_URI` instead of `--database-name` to register a URI.
Add `--keep-invalid` to keep (flagged) gold-SQL that failed validation.

## 3. Run (fixture → results)

```bash
# Dry-run: preflight + corpus parse, no agent calls.
venv/bin/python superset_ai_agent/evaluation/run_rig.py \
    --fixture fixture/fixture.yaml --validate

# Full run (3 trials by default; --questions Q1,Q2 for a smoke subset).
venv/bin/python superset_ai_agent/evaluation/run_rig.py \
    --fixture fixture/fixture.yaml --trials 3
```
`--validate` must pass (auth, connection registered + reachable, schemas exist, judge
ready, CSV clean) before a real run. Results land in
`superset_ai_agent/evaluation/results/<fixture_id>/{scoreboard.json, trials.json}` at
the same granularity as the legacy rig, plus a metadata block (model, memory regime,
backend, grounding modes).

---

## Schemas

**Question CSV** (`fixture/questions.csv`) — one row per item, fill exactly one answer
column:

| column | meaning |
|---|---|
| `id`, `question` | required |
| `answer_type` | `gold_sql` \| `expected_values` \| `eval_note` |
| `gold_sql` | reference SQL (result compared to the agent's) — real-schema, validated |
| `eval_note` | free-text rubric → **LLM judge** (pass/fail + critique) |
| `expected_values` | number(s)/name(s), `trap`, `zero`, or a JSON spec |
| `capability_tags` | `;`-separated (slang, join, xschema, bridge, metric, trap, negative, temporal, multihop, distractor) |
| `tolerance`, `notes` | optional |

**Fixture manifest** (`fixture/fixture.yaml`): `id`, `database_name` **or**
`connection_uri_env`, `schemas: [...]`, `onboard_mode: manual|auto|none`,
`context_docs: [context/*.md]`, `corpus: questions.csv`,
`grounding_modes: [basic, context_dump, wren_bi, wren_bi_context]`. **No secrets** —
the URI comes from the env var named by `connection_uri_env`.

## Notes & troubleshooting

- **Grounding modes**: `basic`/`context_dump` need no semantic layer; the `wren_*`
  modes require `onboard_mode` `manual` or `auto`. Set `grounding_modes` to only what
  you want to measure (fewer modes = faster).
- **Memory regime** is *recorded*, never toggled by the rig. For a fair grounding
  ablation set `WREN_MEMORY_STORE=none` in `.env` and recreate the agent container
  first, then restore it after. For real-data runs, leaving the learning loop on
  (`lancedb`) is usually intended.
- **Judge** grades only `eval_note` items; increase `WREN_BENCHMARK_JUDGE_VOTES` (≥3)
  in `.env` for a more reliable panel vote on real data.
- **`connection: failed to register`** → the Oracle driver is likely missing from the
  Superset image, or the account lacks admin. Register the connection in the Superset
  UI and reference it by `database_name`.
- **gold_sql all dropped** → the generated SQL doesn't match the real schema; check
  that your `inputs/*.csv` headers reflect the actual Oracle column names, or add a
  `schema`-named context file.
