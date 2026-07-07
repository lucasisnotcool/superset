# Live schema introspection for dataset-free onboarding

Status: IMPLEMENTED (default-on; awaiting live Oracle deploy verification).
Steps 1–8 built with tests; 41 unit tests green (introspection + provider +
config + onboarding + access), ruff/mypy clean on changed files. Default-on per
the "no unnecessary dataset gating" directive. Remaining: live verification on
the Windows/K8s Oracle deployment (get_physical_schema returns the tables).

## Problem

The agent's physical catalog (`SchemaIndex`) is built **only** from registered
Superset *datasets*. In a BYO-connection deployment where users connect a
database and model straight from it — **without** cataloguing datasets — the
`tables` metadata table is empty, so:

- `get_full_schema` → `list_datasets(...)` returns `[]`
  ([context/superset_metadata.py:166-173](../../context/superset_metadata.py#L166-L173)),
- `_schema_index_for_project` builds an empty `SchemaIndex`
  ([app.py:2297-2314](../../app.py#L2297-L2314)),
- `get_physical_schema` returns `{ "tables": {} }`
  ([semantic_layer/copilot/tools.py:985-1005](../../semantic_layer/copilot/tools.py#L985-L1005)),
- onboarding/validation fail `empty_root` — the agent correctly refuses to
  invent tables/columns it cannot see.

Confirmed live: a 4-schema Oracle project with a working connection but
`SELECT ... FROM tables` returning zero rows → onboarding blocked.

**Goal:** when no datasets exist, source the physical catalog from **live
database introspection** through Superset's owner-scoped REST API, so onboarding
and MDL validation work against the real tables. Keep every existing contract
(fail-closed access, dataset-sourced path, snapshot fallback) intact.

## Non-goals

- Not replacing the dataset path — datasets stay authoritative when present;
  introspection is a **fallback** only when the dataset scan is empty.
- Not changing query-time (text-to-SQL) context — this targets modeling-time
  (`get_full_schema`) consumers: onboarding, enrichment, MDL validation,
  `get_physical_schema`, `find_tables`.
- Not solving Oracle query execution (thick-mode DPY-3015). Introspection is a
  separate metadata path; see Risks.

## Current architecture (touchpoints)

| Concern | Location |
|---|---|
| Modeling-time catalog fetch | `context/superset_metadata.py::get_full_schema` ([:139](../../context/superset_metadata.py#L139)) |
| Query-time catalog fetch | `context/superset_metadata.py::get_context` ([:51](../../context/superset_metadata.py#L51)) |
| SchemaIndex builder | `app.py::_schema_index_for_project` ([:2248](../../app.py#L2248)), onboarding endpoint ([:5405](../../app.py#L5405)) |
| SchemaIndex from datasets | `semantic_layer/mdl_validator.py::SchemaIndex.from_agent_context` ([:69](../../semantic_layer/mdl_validator.py#L69)) |
| Client contract | `integrations/superset/client.py::SupersetClient` ([:103](../../integrations/superset/client.py#L103)) |
| REST client | `integrations/superset/rest.py` (`list_datasets` [:323](../../integrations/superset/rest.py#L323), `request` [:75](../../integrations/superset/rest.py#L75)) |
| MCP client | `integrations/superset/mcp.py` (`list_datasets` [:294](../../integrations/superset/mcp.py#L294)) |
| Synth target shape | `DatasetMetadata`/`ColumnSummary` ([client.py:52-84](../../integrations/superset/client.py#L52-L84)) |

`SchemaIndex.from_agent_context` reads only `dataset.table_name`,
`dataset.schema_name`, and `dataset.columns[].name/.type` — **not** dataset ids —
so a synthetic `DatasetMetadata` per live table feeds it with no other changes.
Injecting the fallback inside `get_full_schema` fixes **all** modeling-time
consumers at one point (both `_schema_index_for_project` and the onboarding
endpoint call `get_full_schema`).

## Superset REST endpoints (both owner-scoped, run under the caller's session)

1. **List tables** — `GET /api/v1/database/<pk>/tables/?q=(schema_name:'<schema>')`
   ([databases/api.py:835-894](../../../superset/databases/api.py#L835-L894)).
   `@protect()` + `TablesDatabaseCommand`. Response:
   `{count, result: [{value: <name>, type: "table"|"view"|"materialized_view", extra}]}`
   ([commands/database/tables.py:143-168](../../../superset/commands/database/tables.py#L143-L168)).
   **Note:** the doc's `mv_wip_analytics` is a `materialized_view` — we must
   include views/MVs, not just `type=="table"`.
2. **Table columns** — `GET /api/v1/database/<pk>/table_metadata/?name=<t>&schema=<s>[&catalog=<c>]`
   ([databases/api.py:1034-1113](../../../superset/databases/api.py#L1034-L1113)).
   `security_manager.raise_for_access(database, table)` → **404 on no access**
   (hides existence). Response `payload` includes `columns: [{name, type, ...}]`.

Both enforce access, so a principal who cannot see the database/table gets
404/empty → the catalog stays empty for them (fail-closed, R1/R6 preserved).
The request-scoped client already runs under the caller's session
([app.py:716-721](../../app.py#L716-L721)), so scoping is automatic.

## Decision points (with recommendations)

- **D1 — Injection point.** Fallback inside `get_full_schema` when
  `list_datasets` is empty. *Recommend: yes* — single point, covers all
  modeling consumers; leaves `get_context` (query-time) unchanged for now.
- **D2 — Feature flag.** `AI_AGENT_WREN_LIVE_SCHEMA_INTROSPECTION`. **Decided
  (per directive): default `True` in `config.py`** (live DB + MDL project must be
  fully functional without forcing dataset registration), env-overridable to
  `false` as an escape hatch if an engine's inspector misbehaves. Safe as a
  default because it only activates when the dataset scan is empty and is
  fail-soft.
- **D3 — Object types.** Include `table`, `view`, and `materialized_view`
  (needed for the MV in the sample doc). *Recommend: all three*; add
  `WREN_INTROSPECTION_INCLUDE_VIEWS` (default true) only if view noise becomes a
  problem later.
- **D4 — Synthetic dataset ids.** `SchemaIndex` ignores ids, but dedup in
  `require_schema_set_permission` ([access.py:217-219](../../semantic_layer/access.py#L217-L219))
  and `_schema_index_for_project` ([app.py:2298-2305](../../app.py#L2298-L2305))
  keys on `dataset.id`. *Recommend:* deterministic **negative** id from a stable
  hash of `f"{schema}.{table}"` (negative space never collides with real dataset
  ids; deterministic keeps dedup correct across schemas and cache reuse).
- **D5 — N+1 bounding.** List = 1 call/schema; columns = 1 call/table. *Recommend:*
  cap tables per schema at `wren_schema_table_scan_limit` (default 100,
  [config.py:139](../../config.py#L139)); fetch columns with bounded concurrency;
  rely on the existing `_schema_index_cache` TTL + `SchemaSnapshot` so repeated
  Copilot turns don't re-introspect. `log()` when the cap truncates.
- **D6 — MCP adapter.** REST-first. The MCP client
  ([mcp.py](../../integrations/superset/mcp.py)) returns empty/NotImplemented for
  the new method until an MCP tool exists. *Recommend:* ship REST; guard the
  provider so a client lacking the method degrades to today's behavior.
- **D7 — Permission interaction.** A non-empty introspected context makes
  `_access_level_from_context` return FULL organically
  ([access.py:444-451](../../semantic_layer/access.py#L444-L451)), so write is
  granted even without `semantic_full_access_grants_write`. *Recommend:* keep the
  flag (belt-and-suspenders; a genuinely empty schema still needs it). Document
  the overlap.

## Implementation checklist (sequential, resumable)

Steps 1–8 `[COMPLETE]` (see Status). Step 9 in progress. Evidence: 41 unit tests
green; `tests/unit_tests/superset_ai_agent/test_live_schema_introspection.py`.

1. **[COMPLETE] Client contract** — add to `SupersetClient` Protocol
   ([client.py:103](../../integrations/superset/client.py#L103)):
   `introspect_schema(*, database_id, catalog_name, schema_name, limit,
   include_views=True) -> list[DatasetMetadata]`. Returns synthetic
   `DatasetMetadata` (negative id, `table_name`, `schema_name`, `columns`, empty
   `metrics`).
2. **[ ] REST impl** — in `rest.py`, add:
   - `list_tables_raw(database_id, schema_name, catalog_name)` → GET `/tables/`
     with rison `q=(schema_name:'…',catalog_name:'…')` via `self.request`.
   - `get_table_metadata_raw(database_id, name, schema, catalog)` → GET
     `/table_metadata/`.
   - `introspect_schema(...)` — list tables (filter by type per D3, cap per D5),
     fetch columns per table (bounded), map to `DatasetMetadata` with a
     `_synthetic_dataset_id(schema, table)` helper (D4). Reuse `_normalize_*`
     column typing so `ColumnSummary.type`/`type_generic`/`is_dttm` match the
     dataset path.
3. **[ ] MCP impl** — add `introspect_schema` to `mcp.py` returning `[]` (or an
   MCP tool call if one exists); keep signature parity so the provider's
   `getattr` guard is unnecessary but safe.
4. **[ ] Provider fallback** — in `get_full_schema`
   ([superset_metadata.py:166-174](../../context/superset_metadata.py#L166-L174)):
   when `candidate_datasets` is empty **and** `config.wren_live_schema_introspection`
   is on **and** the client has `introspect_schema`, replace with
   `self.superset_client.introspect_schema(...)`. Preserve the `dataset_ids`/
   no-schema early return unchanged.
5. **[ ] Config** — add `wren_live_schema_introspection: bool = False` to
   `AgentConfig` ([config.py:~139](../../config.py#L139)) + `from_env` parse
   (`AI_AGENT_WREN_LIVE_SCHEMA_INTROSPECTION`), mirroring existing `_env_bool`
   knobs.
6. **[ ] Env** — add `AI_AGENT_WREN_LIVE_SCHEMA_INTROSPECTION=true` to
   `superset_ai_agent/.env.example` (documented) and the local `.env`; flag to
   the user to set it on the Windows box (needs ai-agent rebuild).
7. **[ ] Snapshot parity** — confirm the introspected index snapshots
   ([app.py:2315-2327](../../app.py#L2315-L2327)) so an introspection outage
   still degrades to the last snapshot, not empty.
8. **[ ] Tests** (`tests/unit_tests/superset_ai_agent/`):
   - fallback triggers only when datasets empty + flag on;
   - synthetic ids are unique across same-named tables in different schemas;
   - multi-schema project → schema-qualified `SchemaIndex`
     (`is_multi_schema()` true, `get_physical_schema` returns `schemas`);
   - views/MVs included; column types populated;
   - client lacking `introspect_schema` or flag off → today's behavior (empty).
9. **[ ] Docs** — update
   [docs/reference/MDL_LAB.md](../reference/MDL_LAB.md) (catalog source), add a
   `docs/README.md` line, flip this doc's `Status:` to SHIPPED with as-built
   notes.

## Risks & mitigations

- **Oracle inspector reliability.** Table/column introspection uses the engine
  spec's metadata path. Oracle thick-mode failures (DPY-3015) primarily hit
  execution, but if introspection also fails the REST calls return errors →
  `introspect_schema` must **catch per-table and per-schema**, returning what it
  can (partial catalog) and never crashing onboarding. If everything fails, the
  provider degrades to today's empty catalog (unchanged failure mode, not worse).
- **Oracle identifier casing.** Oracle schema/table names are typically
  UPPERCASE (`WLOS_OWNER`). REST calls must pass the **actual** stored schema
  names (`project.schema_names`); `SchemaIndex` lowercases for matching, so
  authoring/validation casing is already normalized — but verify the project's
  `schema_names` hold the real Oracle casing (they come from the connection).
- **N+1 latency on wide schemas.** Bounded by `wren_schema_table_scan_limit` +
  TTL cache + snapshot (D5). Log truncation so a capped scan isn't mistaken for
  a complete one.
- **Synthetic id collisions.** Deterministic negative hash (D4); unit-tested.
- **Views/MVs as base models.** Onboarding must accept `view`/`materialized_view`
  as base objects (MDL models can reference them). Verify the onboarding
  proposal path doesn't assume physical tables only.
- **Double catalog when datasets exist.** Fallback is empty-only, so a project
  with some datasets keeps the dataset path — introspection never mixes in.
  (Future option: union both; out of scope here.)

## Deployment

ai-agent image bakes source → after merge/pull on the Windows box:
`docker compose … build superset-ai-agent && up -d --force-recreate
superset-ai-agent`, with `AI_AGENT_WREN_LIVE_SCHEMA_INTROSPECTION=true` in
`superset_ai_agent/.env`. Verify with a Copilot `get_physical_schema` call
returning the Oracle tables.
