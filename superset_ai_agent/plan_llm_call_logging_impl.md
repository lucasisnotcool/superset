# LLM Call Logging — Implementation Plan & Checklist

**Status:** Not started. This is a resumable checklist for future sessions. Check items off as completed.

## Goal

Count and time every LLM (chat/reasoning) call the agent makes, persist it durably,
and surface aggregates in a **dedicated Superset admin menu item** (not the agent
panel). Priorities, in order: **(1) reliability, (2) minimal performance cost,
(3) granularity (take-what-we-can-get)**.

## Scope decisions (locked)

- **Chat calls only for v1.** Embeddings are **deferred**, but the design MUST leave
  a clean seam to add an equivalent embedding meter later (see §Seams).
- **UI = dedicated Superset admin menu item** (Option B). Do **not** add anything to
  the Ai Agent panel.
- Granularity is best-effort: capture what is free at the client layer; do not thread
  per-request context (owner_id, call-site label) in v1.

---

## Requirements

### Functional
- FR1: Every `ModelClient.chat(...)` invocation is recorded with: timestamp,
  provider, model, duration, success/failure. Token counts when the provider returns
  them (nullable otherwise).
- FR2: Records survive process restart (durable persistence).
- FR3: An Admin-only Superset menu item opens a page showing aggregates: total calls,
  total + average duration, success/failure split, and a breakdown by day and by
  model/provider.
- FR4: A non-admin user can neither see the menu item nor load the page/data.

### Non-functional
- NFR1 (reliability): a logging/DB failure MUST NOT affect the LLM call result —
  recording is fail-open (swallow all exceptions in the meter).
- NFR2 (perf): added latency per call ≪ the LLM call itself (target < 0.5%).
- NFR3: works across multiple agent worker processes (no in-memory-only aggregation).
- NFR4: bounded storage growth (retention or rollup).
- NFR5: unit-testable without a live DB (in-memory store impl, mirroring existing
  store pattern).

---

## Architecture summary

Single chokepoint: all calls go through `active_model_client.chat(...)`, and the
client is built once at startup. Wrap it.

```
create_model_client(config)                         superset_ai_agent/llm/factory.py
        │
        ▼
MeteredModelClient(inner, sink=LlmCallStore)         NEW — wraps .chat()
        │  time it; record(provider,model,ms,ok,tokens); return/raise unchanged
        ▼
Real provider client (OpenAI/Azure/Ollama/compatible)
```

Data flows: agent persists to `ai_agent.db` → read endpoint → Superset admin React
page (gated by RBAC) renders aggregates.

### Entrypoints & touchpoints

| # | Layer | File | Change |
|---|-------|------|--------|
| T1 | Meter | `superset_ai_agent/llm/metered.py` (NEW) | `MeteredModelClient` decorator |
| T2 | Wiring | `superset_ai_agent/llm/factory.py:13` `create_model_client` | wrap return value in the meter (sink passed in) |
| T3 | Wiring | `superset_ai_agent/app.py:303` `active_model_client = …` | construct store, inject as meter sink |
| T4 | Model | `superset_ai_agent/persistence/models.py` | new `AiAgentLlmCall` table (`ai_agent_llm_calls`) |
| T5 | Migration | `superset_ai_agent/persistence/migrations/versions/0016_*.py` (NEW) | `op.create_table` + index on `created_at` |
| T6 | Store | `superset_ai_agent/semantic_layer/...` or `persistence/llm_call_store.py` (NEW) | Protocol + in-memory + SQLAlchemy impl (mirror `coverage_store.py`) |
| T7 | Read API | `superset_ai_agent/app.py` | `GET /agent/admin/llm-usage` aggregates endpoint |
| T8 | Auth | `superset_ai_agent/auth.py:254` `_identity_from_superset_me_payload` + `:36` `AgentIdentity` | parse `roles`/`is_admin` from /me; add admin guard dependency |
| T9 | Retention | `superset_ai_agent/scripts/purge_*.py` (mirror `purge_legacy_jobs.py`) | periodic purge/rollup |
| T10 | Menu | `superset/initialization/__init__.py` (~line 399, "Manage" category) | `appbuilder.add_link(..., menu_cond=admin)` |
| T11 | Route | `superset-frontend/src/views/routes.tsx:351` (`if (isAdmin)` block) | push `/ai-agent/usage/` → lazy React `Component` |
| T12 | Page | `superset-frontend/src/pages/AiAgentUsage/` (NEW) | React page; fetches via `getAgentBaseUrl()` (api.ts:666) |

---

## Decision points (resolve before/at the marked step)

- **DP1 — Storage shape (blocks T4/T5/T6).** Append-one-row-per-call vs in-memory
  counter+flush vs aggregate-UPSERT.
  **Recommendation: append-one-row-per-call.** Rationale: an LLM call is seconds;
  a single indexed SQLite insert is sub-ms (NFR2 holds), it is durable per call (NFR1),
  aggregates across workers (NFR3), and gives max granularity for free. In-memory
  counters fail NFR3 (per-process) and risk loss on crash; aggregate-UPSERT serializes
  all concurrent calls on one hot row. Industry-standard telemetry would use an async
  queue + batch writer — keep that as the NFR2 upgrade path (DP4) only if contention
  appears.

- **DP2 — Data path for the admin page (blocks T7/T12).**
  - Path A (recommended): React page fetches the **agent** endpoint directly via
    `getAgentBaseUrl()` — consistent with every existing AiAgentPanel call (api.ts).
    Admin gating layered: menu `menu_cond` (T10) + `isAdmin` route guard (T11) +
    agent endpoint re-checks roles-from-/me (T8) as defense-in-depth.
  - Path B: add the endpoint to the Superset bridge `AiAgentRestApi`
    (`superset/ai_agent/api.py:54`, native `@protect()` + a custom permission) which
    server-side proxies to the agent. Cleaner single RBAC gate, but adds a Superset→agent
    hop and new bridge code.
  **Recommendation: Path A** (reuses the established FE→agent pattern; least new code).
  Pick Path B if you need Superset RBAC to be the *sole* gate or to avoid exposing an
  admin route on the agent surface.

- **DP3 — Admin signal source (blocks T8).** Confirm Superset `/api/v1/me/` returns
  `roles`. If yes: derive `is_admin` from roles in the /me parser. If no: the FE page
  is still safely admin-gated by `isUserAdmin(bootstrap.user)` (T11) and `menu_cond`
  (T10); gate the agent endpoint via a separate Superset roles lookup or accept FE-only
  gating for v1. **Recommendation: parse roles from /me; fall back to menu+route
  gating if absent. Verify before coding T8.**

- **DP4 — Async write (optional, post-v1).** Only if SQLite write contention is
  observed under concurrency: move the meter to enqueue → single daemon writer thread
  (reuse the `ThreadJobRunner` daemon pattern, `semantic_layer/jobs.py:242`) doing
  batched inserts, flush on shutdown. **Recommendation: do NOT pre-optimize; ship
  sync, measure first.**

- **DP5 — Retention policy (blocks T9).** Time-window purge (delete > N days) vs
  nightly rollup into a daily-aggregate table. **Recommendation: start with a
  time-window purge (simplest, matches `purge_legacy_jobs.py`); add rollup only if row
  volume warrants.** Default window: 90 days (config-driven).

---

## Sequential checklist

### Phase 0 — Verification (no code; unblocks later phases)
- [ ] V0.1 Confirm current alembic head: `cd superset_ai_agent && alembic heads`
  (note: an untracked `0015_nl_sql_example_db_scope_and_refs.py` exists; new revision
  chains from the real head — **do not hardcode `down_revision` without checking**).
- [ ] V0.2 (DP3) Verify Superset `/api/v1/me/` payload includes `roles`. Decide admin
  signal source.
- [ ] V0.3 Confirm `agent_database_url` engine + `session_factory` are available at
  the point we construct the meter (`app.py:318-323`).

### Phase 1 — Persistence (depends: V0.1)
- [ ] 1.1 (T4) Add `AiAgentLlmCall` model, table `ai_agent_llm_calls`, columns:
  `id` String(36) PK, `created_at` DateTime(tz) **indexed**, `provider` String(32),
  `model` String(255) nullable, `duration_ms` Integer, `ok` Boolean,
  `prompt_tokens`/`completion_tokens` Integer nullable. Match the `Column(...)` style
  in `persistence/models.py`.
- [ ] 1.2 (T5) New migration `0016_llm_call_log.py` (revise id per V0.1): `create_table`
  + index on `created_at`. Provide `downgrade()` (`drop_table`). Mirror
  `0012_coverage_run_progress.py` style.
- [ ] 1.3 (T6) `LlmCallStore`: `Protocol` + `InMemoryLlmCallStore` (tests) +
  `SqlAlchemyLlmCallStore`. Methods: `record(provider, model, duration_ms, ok, tokens)`
  and `aggregate(...)` (totals + group-by day + group-by model). Mirror `coverage_store.py`
  (Protocol + two impls, `_now()` UTC). **Insert uses its own short-lived session,
  not a request transaction.**
- [ ] 1.4 Enable SQLite **WAL** PRAGMA on the agent engine (`persistence/database.py:24`
  `create_engine_from_config`) — `connect_args`/event listener — so the meter writer
  never blocks readers. (Verify it doesn't regress existing stores; WAL is DB-wide.)
- [ ] 1.5 Unit tests for the store (in-memory + a sqlite temp file): record N calls,
  assert aggregates. (NFR5)

### Phase 2 — Meter + wiring (depends: Phase 1)
- [ ] 2.1 (T1) `MeteredModelClient(ModelClient)` in `llm/metered.py`. `chat(...)`:
  `start = monotonic()`; try inner.chat → on success record `ok=True` + tokens from
  `result.raw.get("usage")`; on exception record `ok=False` then **re-raise**.
  Provider from `config.model_provider`; model from the `model` arg (fallback
  `config.default_model()`). `is_reachable`/`list_models` delegate unchanged.
- [ ] 2.2 (NFR1) Wrap the *recording* (not the inner call) in `try/except Exception`
  that logs at warning and swallows — a sink/DB error never reaches the caller.
- [ ] 2.3 (T2/T3) Inject: build `LlmCallStore` in `app.py` near other `active_*_store`s
  (after `session_factory`), pass into `create_model_client` (add optional `sink`
  param) or wrap `active_model_client` directly at `app.py:303`. Keep test-injection
  seam (the `model_client or … or create_model_client` pattern).
- [ ] 2.4 Tests: a fake inner client + in-memory store → assert one record per chat,
  success and failure both recorded, and that a store that raises does NOT break chat
  (NFR1 regression test).

### Phase 3 — Read API + admin auth (depends: Phase 1; DP2, DP3)
- [ ] 3.1 (T8) Add `roles: list[str]` (and/or `is_admin: bool`) to `AgentIdentity`
  (`auth.py:36`); populate in `_identity_from_superset_me_payload` (`auth.py:254`).
  Per DP3 fallback if /me lacks roles.
- [ ] 3.2 (T8) Add an `admin_required` FastAPI dependency (reject non-admin with 403).
- [ ] 3.3 (T7) `GET /agent/admin/llm-usage` → returns aggregates from the store
  (totals, by-day, by-model). Read-only; `admin_required`. Add a response schema
  (`schemas.py`). Follow the existing `/health` / `/models` endpoint shape.
- [ ] 3.4 Tests: endpoint returns aggregates; non-admin identity → 403.

### Phase 4 — Superset admin menu + page (depends: Phase 3; DP2 Path A)
- [ ] 4.1 (T10) `appbuilder.add_link("AI Agent Usage", label=…, href="/ai-agent/usage/",
  category="Manage", category_label=_("Manage"), menu_cond=<admin>)` in
  `superset/initialization/__init__.py` (~line 399, alongside other Manage items).
  Gate `menu_cond` to admins (mirror existing `menu_cond` usages).
- [ ] 4.2 (T11) In `superset-frontend/src/views/routes.tsx`, **inside the existing
  `if (isAdmin)` block (line ~351)**, push `{ path: '/ai-agent/usage/', Component:
  AiAgentUsage }` with a lazy import.
- [ ] 4.3 (T12) New page `superset-frontend/src/pages/AiAgentUsage/index.tsx`: fetch
  `${getAgentBaseUrl()}/agent/admin/llm-usage` (reuse `api.ts:666` `getAgentBaseUrl`),
  render totals card + a per-day table/chart. Use `@superset-ui/core/components`
  (no direct antd), no `any`, functional component + hooks (per CLAUDE.md).
- [ ] 4.4 Jest test for the page (RTL): renders totals from a mocked fetch; handles the
  empty/loading/error states.

### Phase 5 — Retention (depends: Phase 1; DP5)
- [ ] 5.1 (T9) Purge script mirroring `scripts/purge_legacy_jobs.py`: delete
  `ai_agent_llm_calls` rows older than `N` days (config knob, default 90). Document the
  cron/ops invocation.
- [ ] 5.2 Test the purge boundary (keeps < N days, deletes ≥ N days).

### Phase 6 — Validation
- [ ] 6.1 `pre-commit run --all-files` (CLAUDE.md: mandatory before push; mypy/ruff/
  prettier/eslint).
- [ ] 6.2 Backend: `pytest tests/unit_tests/superset_ai_agent/` (new store/meter/endpoint
  tests green).
- [ ] 6.3 Frontend: `npm run test -- AiAgentUsage`.
- [ ] 6.4 Manual: trigger a few agent queries, open the admin menu item, confirm counts
  + timing increment and persist across an agent restart.
- [ ] 6.5 Update `UPDATING.md` if the menu item / config knobs are user-facing; add
  docstrings (CLAUDE.md).

---

## Seams for deferred embeddings (do in v1, exercise later)

- The `Embedder` (`llm/embeddings.py`, `create_embedder`) is a **separate** interface
  from `ModelClient` — it is NOT captured by `MeteredModelClient`.
- Leave the seam: (a) give `AiAgentLlmCall` a `kind` column (`"chat"` default; future
  `"embedding"`) **OR** keep the store generic enough to accept a `kind`; (b) keep
  `LlmCallStore.record(...)` provider/model/duration/ok-shaped so a future
  `MeteredEmbedder` can call the same store; (c) note the wrap point: `create_embedder`
  / where `active_embedder` is built (`app.py:357`).
- **Recommendation:** add the nullable `kind` column now (cheap, avoids a later
  migration) but only ever write `"chat"` in v1.

---

## Risks & mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Logging failure breaks an LLM call | High | NFR1: fail-open try/except around recording; re-raise only the *original* inner exception (test 2.4) |
| SQLite single-writer contention under concurrent calls | Med | WAL mode (1.4) + own short txn (1.3); LLM latency ≫ insert; escalate to async queue only if measured (DP4) |
| Unbounded table growth | Med | Retention purge (Phase 5); index on `created_at` keeps aggregates cheap |
| Admin gating bypass (non-admin reads data) | High | Triple gate: `menu_cond` (4.1) + `isAdmin` route (4.2) + `admin_required` endpoint (3.2); verify /me roles (DP3) |
| `/me` lacks roles → endpoint can't gate | Med | DP3 fallback: rely on FE menu+route gating for v1; document the limitation |
| Multi-worker double counting / loss | Low | Append-per-row aggregates correctly across workers (DP1); no in-memory counters |
| Token counts absent (Ollama/compat) | Low | Columns nullable; "take what we can get" (FR1) |
| WAL change affects other agent stores | Low | WAL is DB-wide and safe for the existing single-writer SQLite usage; smoke-test existing store tests (1.4) |
| Migration `down_revision` collision with untracked 0015 | Med | V0.1: read real head before authoring 0016 |

## Performance cost summary
- Hot path adds: one `monotonic()` pair + one indexed SQLite insert in its own txn.
  Against a multi-second LLM call this is < 0.1% — well within NFR2. No change to the
  call's critical path beyond timing. Reads are admin-only and aggregate over an
  indexed column.

---

## AS-BUILT (implementation complete)

All phases implemented and tested. Net backend result: **new feature tests green**
(store 15, meter 7, usage API 4, auth admin 7, purge 3 = 36) + **1164 agent unit
tests pass**; FE **3 jest tests pass**; prettier clean; ruff clean on source.

### Files shipped
- `superset_ai_agent/persistence/models.py` — `AiAgentLlmCall` (table `ai_agent_llm_calls`, nullable `kind` seam for embeddings).
- `superset_ai_agent/persistence/migrations/versions/0016_llm_call_log.py` — create table + indexes (chains 0015).
- `superset_ai_agent/persistence/database.py` — SQLite WAL + `synchronous=NORMAL` on the agent engine.
- `superset_ai_agent/llm/usage_store.py` — `LlmUsageStore` Protocol + InMemory + SQLAlchemy + shared `summarize()`.
- `superset_ai_agent/llm/metered.py` — `MeteredModelClient` (fail-open recording, transparent delegation).
- `superset_ai_agent/schemas.py` — `LlmUsageSummary` / `LlmUsageBucket`.
- `superset_ai_agent/config.py` — `admin_roles`, `llm_usage_retention_days` (+ env).
- `superset_ai_agent/auth.py` — `AgentIdentity.roles`, `IdentityProvider.is_admin`, lazy `/api/v1/me/roles/` fetch, `_role_names_from_me_roles`.
- `superset_ai_agent/app.py` — wrap client at chokepoint, `_create_llm_usage_store`, `require_admin`, `GET /agent/admin/llm-usage`.
- `superset_ai_agent/scripts/purge_llm_calls.py` — retention CLI.
- `superset/initialization/__init__.py` — Manage-category admin link (uses `cond=`, the correct add_link kwarg).
- `superset-frontend/src/views/routes.tsx` — `/ai-agent/usage/` route inside the `isAdmin` block.
- `superset-frontend/src/pages/AiAgentUsage/{index.tsx,AiAgentUsage.test.tsx}` — admin page.

### Risks / gaps (carry forward)
- **R1 admin-definition asymmetry**: the Superset *menu* is gated on FAB `appbuilder.sm.is_admin()`; the agent *API* is gated on `config.admin_roles` (default `("Admin",)`). Aligned by default; they diverge only if an operator sets `AI_AGENT_ADMIN_ROLES` to a custom role (API allows it, menu still hidden). The API is the real gate, so this is a UX inconsistency, not a security hole.
- **R2 lint not run here**: `oxlint` native binary missing in this env and `tsc` not run — prettier + jest pass, but eslint/oxlint + full type-check must be confirmed in CI.
- **R3 data path depends on `/ai-agent` proxy**: the page fetches `getAgentBaseUrl()` (same as the whole AiAgentPanel). If `SUPERSET_AI_AGENT_URL`/proxy is unset in a deployment, the page loads but shows a fetch error — consistent with existing agent UI.
- **R4 in-memory mode**: when no agent DB is configured the store is process-local (non-durable, per-worker). Durable path (SQLite/WAL) is the default; this only affects ephemeral/test deployments.
- **R5 WAL sidecar files**: WAL adds `-wal`/`-shm` files next to the SQLite DB; ops backups must copy them (or checkpoint first) to avoid losing recent rows.
- **R6 aggregation reads rows**: `summary()` pulls windowed rows and aggregates in Python (shared by both stores). Bounded by retention; if volume grows, push GROUP BY into SQL.
- **R7 menu category**: placed under **Manage** (closest existing admin area). If "Settings/Security" is preferred, it's a one-line category change.
- **R8 pre-existing failing test**: `test_multi_schema_schema_index::test_bulk_activate_fetches_live_schema_once...` fails on this clone independent of this work (verified by reverting app.py to HEAD) — part of the in-flight cross-schema drift, not this feature.
