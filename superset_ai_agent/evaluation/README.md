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

# Semantic-layer evaluation — Seagate Manufacturing

Four experiments that measure how much the Wren semantic layer changes text-to-SQL
answer quality, graded against the ground-truth in
[`../dev_fixtures/seagate_manufacturing/test_queries.md`](../dev_fixtures/seagate_manufacturing/test_queries.md).

| # | Notebook | What the agent has |
| --- | --- | --- |
| 1 | `01_experiment_basic.ipynb` | DB only — no semantic layer, no extra context |
| 2 | `02_experiment_context_dump.ipynb` | DB + the BI glossary prepended into the prompt |
| 3 | `03_experiment_wren_base.ipynb` | DB + onboarded **base** Wren layer (structure only) |
| 4 | `04_experiment_wren_bi.ipynb` | DB + Wren layer **enriched** from the BI glossary |

The deliverable is the **delta** across these four — produced by
`05_compare_and_score.ipynb`. A completed live run (OpenAI `gpt-4.1-mini`, 3 trials
per condition) and its findings are written up in **[`RESULTS.md`](RESULTS.md)**.

Files: `eval_common.py` (client + harness), `seagate_scoring.py` (ground-truth-keyed
scorer), the six notebooks, `RESULTS.md` (findings), and `results/` (raw + scored
outputs).

## How it works

The notebooks are thin HTTP clients against a **running** agent, so they inherit
whatever model provider and Wren settings the agent was started with
(`superset_ai_agent/.env`). All logic lives in `eval_common.py`.

Because the agent only grounds on an *existing* semantic project, the four
experiments form a **monotonic progression on one project** — each step only adds
state, so they run in order with no teardown between them:

```
00 setup  → archive any existing Seagate project (clean baseline)
01 basic       ─┐ no active MDL
02 context dump ┘ no active MDL (+ glossary in prompt)
03 wren base    → resolve + onboard (auto-activates base models)
04 wren + BI    → upload glossary → enrich → activate (re-indexes)
05 compare      → grade all four vs. ground truth
```

## Prerequisites

1. **Superset + the AI agent are running** with `superset_ai_agent/.env`
   (see `superset_ai_agent/README.md` / `MACOS.md`). The shipped `.env` is the
   full-parity Wren profile (`openai` provider, embedding retriever, LanceDB,
   deep validation + engine-gated activation).
2. **The Seagate data is loaded:** `superset load-examples` registers the 7
   `seagate_*` tables in the `examples` database under the `seagate` schema.
3. **Python deps** for the notebooks: `requests`, `pandas` (and `matplotlib` for
   the optional chart). Use the repo `venv`, e.g.:
   ```bash
   venv/bin/pip install requests pandas matplotlib jupyter
   ```

## Authentication

The shipped `.env` runs the agent in `superset_session` / `user_session` mode, so
every agent call must carry a Superset identity. `AgentClient.login()` logs into
Superset (JWT) and forwards the bearer + CSRF token on every request — the same
identity the SQL Lab panel forwards through the proxy. Defaults are `admin/admin`.

## Configuration

Defaults target **native dev** (agent `:8097`, Superset `:8088`). Override in the
config cell or via env vars:

| Env var | Default | Notes |
| --- | --- | --- |
| `EVAL_AGENT_BASE_URL` | `http://localhost:8097` | Docker: `http://localhost:8090/ai-agent` |
| `EVAL_SUPERSET_BASE_URL` | `http://localhost:8088` | Docker: `http://localhost:8090` |
| `EVAL_SUPERSET_USERNAME` / `EVAL_SUPERSET_PASSWORD` | `admin` / `admin` | Superset login |
| `EVAL_DATABASE_NAME` | `examples` | Auto-discovers the DB id by name |
| `EVAL_DATABASE_ID` | *(unset)* | Set to skip name discovery |
| `EVAL_SCHEMA_NAME` | `seagate` | — |

## Running

From this directory (so `eval_common.py` is importable):

```bash
cd superset_ai_agent/evaluation
venv/bin/jupyter lab    # or: jupyter notebook
```

Run `00` once, then `01 → 02 → 03 → 04` **in order**, then `05`. Each experiment
notebook writes `results/<name>.json`; `05` reads all four back.

## Grading

`grade_one` is an **assistive** grader, not the final word:

- **`match` / `mismatch`** — single-value L1–L3 questions: the bolded ground-truth
  number is matched against the agent's executed result (1% tolerance).
- **`trap_ok` / `trap_failed`** — Q12 (Golden Yield of short-order tickets): correct
  iff the agent *refuses* / calls it undefined rather than returning a number.
- **`manual_review`** — multi-value questions (Q5 site list, Q8 breakdown, the L4
  chained questions) need a human or LLM judge. Use the side-by-side cell in `05`.

Everything (SQL, answer, rows, matched models) is captured in `results/` regardless,
so you can always re-grade or inspect by hand.

## Caveats (read before trusting the numbers)

- **Disable the learning loop for a fair ablation.** With `WREN_MEMORY_STORE=lancedb`
  the one-shot query path *recalls* and *stores* NL→SQL pairs scoped only by
  database+schema, so running the experiments in sequence lets later conditions
  **recall earlier ones' SQL** — a real confound (see `RESULTS.md` Finding F1).
  Re-create the agent with `WREN_MEMORY_STORE=none` before the sweep.
- **Enrichment supersedes the base files.** Enrichment re-emits the whole manifest
  into one file; activating it *alongside* the base files causes `Duplicate model
  name` errors. `apply_enrichment` deactivates the superseded base files for you.
- **State is shared and durable.** Project, MDL, and learned examples persist across
  runs. Re-run `00` (and reset memory) before a fresh sweep.
- **Order matters.** Experiment 4 (`enrich`) returns **409** unless experiment 3
  (`onboard`) has populated base models first.
- **LLM non-determinism.** Results vary ±1–2/15 run-to-run; average several trials.
- **Small-schema caveat.** This 7-table fixture lets the whole glossary fit in the
  prompt, which favours `context_dump`; it does **not** exercise the semantic
  layer's selective-retrieval-at-scale premise. See `RESULTS.md` Finding F2.
- Onboarding and enrichment make real LLM calls and can take a minute or two each.
