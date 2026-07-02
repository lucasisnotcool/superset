# Fork Working Agreement (Mac + Windows multi-machine setup)

Rules distilled from a friction audit of 113 past agent sessions in this fork.
Every rule here was purchased with a real incident — follow them exactly.

## Definition of done — verify, don't assume

- "Done" means **verified**, not written. Before reporting an item complete:
  - Backend: run the relevant pytest subset (e.g. `venv/bin/python -m pytest tests/unit_tests/superset_ai_agent/ -q`).
  - Frontend: run `npx jest <touched paths>` AND typecheck the touched files with `npx tsc` — Claude-authored TS that fails `tsc` has been handed off before.
- Every claim about implementation state needs positive, source-backed evidence
  (file:line, test output, or a live probe). **Label hypotheses as hypotheses.**
  Never state file status, feature state, or a diagnosis as fact from memory or a
  prior-session summary — re-check the live tree first.
- After each completed work item: add tests, run them, then report
  (a) dev-intent vs implementation gaps and (b) user-expectation vs actual-UI gaps.
- When debugging a reported failure: get the actual observable error FIRST
  (browser DevTools → Network → response body for agent 500s — the agent has no
  app-level logging). Don't build hypotheses or tooling on a guess.

## Implementation workflow

- Features start from a source-backed spec/plan at `superset_ai_agent/plan_<topic>_impl.md`:
  entrypoints + touchpoints, risks + mitigations, decision points with recommendations,
  written as a **sequential checklist future sessions can resume**. Mark items
  `[COMPLETE]` with evidence as you go.
- **Re-anchor before resuming any plan**: other agents work this repo concurrently.
  Re-check `git status` and the plan's touchpoints against the live tree; never trust
  stale summaries. Never edit a file another agent is working on.
- When context runs long, flush all findings/decisions into the relevant plan doc
  BEFORE compaction.
- Copy reference/upstream files with commands (`cp`, `curl`) — never regenerate
  their content from memory (paraphrase drift has corrupted prompts before).
- Any new env var MUST also be added to `.env example` / `.env.example` and flagged
  to the user (needs sync + rebuild). New feature flags should default ON in example envs.
- **Cross-schema is the default assumption**: audit any new tool, prompt, ranking, or
  context path for single-schema assumptions before shipping — this gap recurred 7+ times.
- **Nothing is user-scoped unless explicitly decided**: memory, RAG docs, golden
  queries, and MDL projects are database-scoped (fingerprint); `owner_id` is audit-only.

## Git discipline

- NEVER `git commit --amend` and never force-push (hook-enforced): `origin/master`
  is the sync channel to the Windows box and history must stay fast-forward.
- Commit/push only when explicitly asked; the user usually manages git himself.
  Read-only git is always fine.
- Never stage `.env*` files (hook-enforced) — a real API key hit GitHub push
  protection once.
- Run pre-commit before pushing (a hook runs it automatically on `git commit`).

## Shell habits

- The working directory resets between Bash calls: use absolute paths; don't rely
  on `cd` persisting, and don't `cd` to relative paths.
- Never `source venv/bin/activate` — invoke the interpreter directly:
  `/Users/lohzh/superset/venv/bin/python` (or `venv/bin/ruff`, `venv/bin/pytest`
  from the repo root).

## Environments (these have burned us before)

- **Mac dev**: `make up-ai`; app behind nginx at `http://localhost:8090`
  (AI proxy at `/ai-agent`); Postgres at `localhost:5432` superset/superset/superset.
  Docker Desktop must use Apple Virtualization framework (Docker VMM SIGILLs
  `cryptography` on Apple Silicon).
- **Windows corporate box** (user drives it; hand over copy-pasteable **pwsh**
  commands, never bash): `scripts/docker-compose-ai-up.ps1 -detached` with
  `docker-compose.no-bind.yml` + `docker-compose.ai-agent.yml`. The image **bakes
  source**: `restart` does NOT pick up changes — requires `build` +
  `up -d --force-recreate`, then verify with an in-container grep marker
  (`docker compose ... exec superset-ai-agent grep -c "<marker>" /app/...`)
  before assuming a change is live. Optimize for the fewest rebuild cycles.
- `ai_agent.db` lives on the `superset_ai_agent_data` volume and **survives resets
  and rebuilds** — stale/legacy rows persist unless purged or `down -v`.
  "Reset and re-onboard" does not clear it.
- Uncommitted local edits never reach the Windows box; the only code channel is
  commit + push to `origin master`, then the user pulls. The Windows checkout may
  be on a different commit — don't assume files/schema/migrations match.
- The user runs docker, deploys, and manual UI testing himself — don't touch
  docker or the running stack unless explicitly asked.
