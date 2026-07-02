<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Feature Spec — Postgres-Only Persistence (No Docker Volumes / PVs)

> **Status: BUILT + verified end-to-end locally.** All decision points below
> are resolved (annotated inline); as-built details, live e2e results, the two
> defects discovered only by running against real Postgres (§7 R9/R10 — both
> would have broken *any* Postgres deployment of the agent), and the
> Windows/offsite runbook live in
> [plan_postgres_only_persistence_impl.md](plan_postgres_only_persistence_impl.md).

> Scope: the full Docker Compose stack (`docker-compose.no-bind.yml` +
> `docker-compose.ai-agent.yml`, the Windows/offsite deploy variant) plus the
> `superset_ai_agent` service. Driven by a hard constraint: **the offsite
> environment provisions no persistent volumes at all** — every store that
> currently lives on a Docker named volume must move to network-reachable
> **PostgreSQL**, and nothing else (no Redis, no S3/object storage, no local
> disk assumed durable).

## 0. One-paragraph intent

Every durable store in this stack currently degrades to "write to a local
path" (a SQLite file, two LanceDB directories, an uploaded-document blob
directory, a Postgres data directory, a Redis RDB/AOF directory) that is only
durable because a Docker volume backs it. Remove that assumption everywhere:
point the metadata DB and the agent's relational DB at an external Postgres
(near-zero effort — already anticipated in the codebase), replace Redis
(optional in Superset core; the built-in fallbacks are Postgres-backed
already for the required caches) with Postgres-backed equivalents where a
built-in fallback doesn't already exist, and — the actual engineering lift —
give the two LanceDB vector stores and the document-blob store a first-class
`postgres` backend, added as a new mode alongside their existing mode-switch
config knobs, not a rewrite of the surrounding code.

---

## 1. Root cause / why this matters now

The stack was designed for two environments, both of which have local disk:
Mac dev (`docker-compose.yml` + `docker-compose.ai-agent.yml`, host bind
mounts) and Windows/offsite (`docker-compose.no-bind.yml` +
`docker-compose.ai-agent.yml`, source baked into the image, but **still uses
Docker named volumes** for state: `db_home`, `redis`, `superset_home`,
`superset_data`, `superset_static_assets`, `superset_ai_agent_data`). "No PV
available offsite" breaks every one of those volumes simultaneously, not just
the database — this is a full persistence-architecture change, not a
one-line config edit, even though two of the six volumes turn out to need no
work at all (§2).

**Assumption flagged, not verified against your box:** I'm treating "PV not
available" as "no Docker named volume of any kind survives or is even
grantable" (i.e., the offsite platform's container runtime gives you a
network egress to a managed Postgres and nothing else — no writable
persistent disk, not even ephemeral-scratch-across-restarts). If your
platform actually still allows anonymous/ephemeral volumes for
container-to-container handoff *within one compose run* (as opposed to
durable named volumes that must survive a redeploy), §2 rows marked
"ephemeral, non-load-bearing" don't need to move at all — that's a real
decision point (§11-D5), not a fact I can check from here.

---

## 2. Current-state persistence inventory (ground truth)

| # | Store | Backend today | Volume | Load-bearing? | Already portable? |
|---|---|---|---|---|---|
| 1 | Superset metadata DB (users, dashboards, datasets, saved queries, `SupersetMetastoreCache` KV table) | Postgres (`db` service, `docker/Dockerfile.postgres`) | `db_home` | **Yes** | **Yes — env var only.** `DATABASE_HOST`/`DATABASE_DIALECT`/etc. in `docker/.env` (docker/pythonpath_dev/superset_config.py:116-134); no hardcoded hostname outside env vars |
| 2 | Superset `superset_home` (SQL Lab `FileSystemCache` results, Celery beat schedule, logs) | Local FS | `superset_home` | Only SQL Lab **async** results (`sql_lab.py:413` raises `SupersetResultsBackendNotConfigureException` if `allow_run_async` and no `results_backend`) | No — needs a new `RESULTS_BACKEND` (§4.2); beat schedule/logs are disposable |
| 3 | Superset `superset_data` (`/app/data`) | Local FS | `superset_data` | **No — confirmed unused.** No Superset core config (`UPLOAD_FOLDER`, `DATA_DIR`, `IMAGE_UPLOAD_FOLDER`) points at `/app/data`; `UPLOAD_FOLDER` actually resolves under `BASE_DIR/static/uploads` (superset/config.py:1231) | Drop the volume outright, zero migration risk |
| 4 | Superset `superset_static_assets` (webpack manifest + JS/CSS) | Local FS, shared between `superset-node` (writer) and `superset`/`nginx` (readers) | `superset_static_assets` | No — fully regenerated by every build | Ephemeral by nature; only a concern if the platform disallows even in-run shared volumes (§11-D5) |
| 5 | Redis (cache + Celery broker/result backend) | Redis | `redis` | Partially. `FILTER_STATE_CACHE_CONFIG`/`EXPLORE_FORM_DATA_CACHE_CONFIG` **already default to `SupersetMetastoreCache`** (Postgres-backed, superset/config.py:1257-1279) if unset — Redis here is dev-only convenience, not a requirement. `CACHE_CONFIG`/`DATA_CACHE_CONFIG`/`THUMBNAIL_CACHE_CONFIG` default to `NullCache` in prod (perf-only, safe to lose). Celery broker/result backend have **no built-in non-Redis production backend** in this repo | Cache: done via existing defaults. Celery: needs a decision (§4.3) |
| 6 | AI agent relational DB (17 tables: conversations, projects, jobs, MDL files, instructions, examples, coverage runs, LLM-call metering, …) | SQLite (`ai_agent.db`) | `superset_ai_agent_data` | **Yes** | **Yes — already anticipated.** Dialect-agnostic SQLAlchemy/Alembic (superset_ai_agent/persistence/database.py, models.py — postgresql-specific partial indexes already present at models.py:232-240); `psycopg[binary]` driver is pre-listed and commented out in `requirements-ai-agent.txt:60` specifically for this |
| 7 | MDL schema/instruction/example vectors (`wren_lancedb`) | LanceDB (embedded, file-based) | `superset_ai_agent_data` (`.data/wren_lancedb`) | Yes for recall quality (degrades closed to in-process ranking if unavailable — never a hard failure, but a silent quality regression) | **No.** No Postgres/pgvector backend exists; this is new engineering (§4.4) |
| 8 | Document-chunk vectors (`wren_lancedb_documents`) | LanceDB (embedded, file-based) | `superset_ai_agent_data` (`.data/wren_lancedb_documents`) | Same as above, degrades to keyword overlap | **No** — same gap, same fix shape (§4.4) |
| 9 | Uploaded raw document bytes | Local FS (`AI_AGENT_DOCUMENT_STORAGE=local`) or S3 (`=s3`, already supported) | `superset_ai_agent_data` (`.data/documents/...`) | Yes, for "view/re-extract original file" | Partially — S3 mode already exists, but the ask is Postgres-only, so a new `postgres` mode is needed (§4.5) |

---

## 3. Design principle — extend the existing pattern, don't invent one

Every store in rows 6-9 above is already selected by a config-driven mode
switch with a **degrade-closed** contract (never raise; fall back to a
weaker in-process behavior; log a warning):

- `AI_AGENT_DATABASE_URL` — any SQLAlchemy URL, dialect-agnostic already.
- `WREN_VECTOR_INDEX` / `WREN_MEMORY_STORE` / `WREN_DOCUMENT_VECTOR_INDEX` —
  `"memory"` / `"none"` vs `"lancedb"`, chosen per-store.
- `AI_AGENT_DOCUMENT_STORAGE` — `"local"` vs `"s3"`.

The correct shape for this migration is to **add `"postgres"` as a new value
next to `"lancedb"`/`"s3"` in each of those enums**, implement a class with
the exact same method signature as the LanceDB/local-FS class it sits next
to, and let the existing selection code choose it. This is additive (no
existing mode is removed — Mac dev keeps working exactly as today) and keeps
the blast radius to the persistence layer only; nothing in the four call
sites (`schema_retriever.py`, `memory_store.py`, `instructions.py`,
`document_retriever.py`, `file_storage.py`) needs to change beyond the
factory/selection function.

---

## 4. Target architecture, track by track

### 4.1 Track A — Superset metadata DB → external Postgres
No code change. Point `DATABASE_HOST`/`DATABASE_PORT`/`DATABASE_DB`/
`DATABASE_USER`/`DATABASE_PASSWORD` (`docker/.env`) at the external instance,
remove the `db` service and `db_home` volume from the compose override used
offsite. This also removes the only other in-repo hard Postgres dependency
(the `db` container), so it must land before/with Track C if both share the
same external instance's provisioning step.

### 4.2 Track B1 — SQL Lab async results backend
`RESULTS_BACKEND` has no built-in Postgres-backed `BaseCache`
implementation in Flask-Caching (confirmed: only
`FileSystemCache`/`RedisCache`/`MemcachedCache`/`SimpleCache`/`NullCache`
variants ship). Two options:
- **(Recommended) Implement a small `PostgresResultsBackend(BaseCache)`** —
  a K/V table (`key text primary key, value bytea, expires_at timestamptz`)
  with `get`/`set`/`delete`, following Flask-Caching's documented "subclass
  `BaseCache`" extension point. Query results are already msgpack-compressed
  before `results_backend.set()` (sql_lab.py:578-619), so this is a narrow,
  well-bounded blob store, not a query engine.
- **(Fallback) Disable `allow_run_async`** for connected databases (sync SQL
  Lab only). Zero-code, but removes a user-facing feature — only take this
  if async SQL Lab isn't actually used offsite (confirm with the operator
  before choosing this over the small build).

### 4.3 Track B2 — Celery broker / result backend
No Postgres-native option ships in this codebase, and industry consensus
(kombu maintainers, Celery issue trackers) is explicit: the SQLAlchemy
transport is not a supported production broker — no pub/sub, polling-based,
and duplicate task execution is possible with more than one worker process.
Superset's own dev fallback already uses this exact transport shape, just
against SQLite (`superset/config.py:1509-1518`,
`sqla+sqlite:///celerydb.sqlite` / `db+sqlite:///celery_results.sqlite`),
so pointing it at Postgres (`db+postgresql://...` /
`sqla+postgresql://...`) is in-pattern, not novel — it's just explicitly
not a scale-safe pattern. This is the highest-risk decision in the whole
spec; see §11-D2.

### 4.4 Track C — AI agent relational DB → external Postgres
1. Uncomment `psycopg[binary]>=3.1,<4.0` in `requirements-ai-agent.txt:60`.
2. Rebuild the `superset-ai-agent` image (new dependency → cannot be a
   restart).
3. Set `AI_AGENT_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/ai_agent`
   in `superset_ai_agent/.env`.
4. `AI_AGENT_RUN_MIGRATIONS=true` (default) runs Alembic against the new
   dialect on boot — no migration-script changes anticipated (the one
   dialect-conditional index already branches on `postgresql_where`).

Lowest-risk track in this spec; do it first as the validation case that
"external Postgres from this box is reachable and correctly credentialed"
before spending effort on Track D.

### 4.5 Track D — LanceDB vector stores → pgvector (the real engineering lift)
Both LanceDB call sites expose narrow, already-isolated interfaces —
implement pgvector-backed twins with identical method signatures, selected
by a new `"postgres"` enum value.

**`vector_cache.py:LanceVectorCache` → `PgVectorCache`** (backs `sql_pairs`
and `instructions`, row-mutable):
- Schema: one table (or one table partitioned by `collection`), columns
  `(collection text, scope_key text, embedder_signature text, row_id text,
  text text, embedding vector(N), primary key (collection, scope_key,
  embedder_signature, row_id))` — mirrors the LanceDB per-`(collection,
  scope_key, signature)` table-per-tuple keying (vector_cache.py:49-54) as a
  composite key instead of a generated table name, since Postgres doesn't
  need per-tuple physical tables.
- `upsert()` → `INSERT ... ON CONFLICT (…) DO UPDATE` (this is *the* natural
  win over LanceDB's delete-then-add workaround at vector_cache.py:122-126,
  which exists only because older LanceDB lacks `merge_insert`).
- `search()` → `ORDER BY embedding <=> :query_vector LIMIT :k` (cosine
  distance operator from pgvector), filtered by `collection`/`scope_key`/
  `embedder_signature`.
- `remove()` → plain `DELETE`.
- Preserve the **degrade-closed contract exactly**: any connection or query
  error → log + return `False`/`None`, never raise. This is the single most
  important invariant to carry over — every caller already treats "cache
  unavailable" as a legitimate steady state, not an error path to add
  handling for.
- Index: `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)` (or
  `ivfflat` if the target Postgres version/pgvector build lacks HNSW) per
  the embedding dimension in `AI_AGENT_EMBEDDER_DIMENSIONS` (default 1536).

**`schema_retriever.py:LanceDbRetriever` → `PgVectorRetriever`** (backs the
MDL schema index, immutable whole-scope-per-checksum):
- Same table shape, keyed by `(scope_key, checksum, item_id)` instead of a
  per-checksum LanceDB table; `has_index(scope_key, checksum)` becomes an
  `EXISTS` check; `index()` a bulk `INSERT`; ranking logic itself is
  untouched (`EmbeddingRetriever` already does the ranking math in-process —
  LanceDB/pgvector is purely the persistence + cold-start rehydration layer,
  per the existing docstring at schema_retriever.py:430-436).

**`document_retriever.py:DocumentChunkIndex`** — same shape again, keyed by
`document_scope_key(project_id, scope)`; degrades to keyword token overlap
on backend failure exactly as today.

**Extension provisioning is an operator action, not a code concern:**
`CREATE EXTENSION IF NOT EXISTS vector;` must run once per target database,
by a role with the privilege to create extensions (superuser or a role
granted `CREATE` on the extension in newer Postgres, or pre-installed by the
managed-Postgres provider — RDS/Cloud SQL both support pgvector out of the
box as of recent versions). This must be called out explicitly to the
platform/DBA team offsite; it's the one step outside application config
that can silently block this whole track if skipped (§10-R2).

### 4.6 Track E — Uploaded document raw bytes → Postgres blob mode
Add `AI_AGENT_DOCUMENT_STORAGE=postgres` alongside the existing `local`/`s3`
modes (`file_storage.py`). Store bytes in a new table
`(document_id uuid primary key, filename text, content_type text, size_bytes
int, data bytea, created_at timestamptz)`. Per the general Postgres
guidance (bytea is fine up to low-single-digit-GB; anything larger belongs
in true object storage), cap accepted upload size at whatever the existing
size guard already enforces client-side
(`DEFAULT_MAX_DOCUMENT_BYTES=10_000_000`, noted in memory
[[document-rag-suite]]) — comfortably inside bytea's practical range, so no
new size-tiering logic is needed. `bytea` over large objects (`oid`): no
separate cleanup step needed on delete (a large object's bytes survive row
deletion unless separately unlinked; bytea does not have that failure mode),
and streaming isn't a real requirement here since the extracted-text/chunk
pipeline already reads the whole file into memory to run pypdf/python-docx/
openpyxl/python-pptx extraction.

### 4.7 Track F — Ephemeral, non-load-bearing volumes
`superset_data` (unused, drop the volume declaration entirely) and
`superset_static_assets`/`superset_home`'s beat-schedule-and-logs portion
(regenerated every build/run) need no data-migration — only a
decision on whether the offsite platform still permits *ephemeral*,
non-durable scratch space for them (§11-D5). If it does not, `logs` can go
to stdout (12-factor pattern, already how container logs are typically
collected) and the Celery beat schedule file can be pointed at `/tmp` (its
loss on restart just means beat re-derives its own schedule state, not a
correctness issue for a single beat process).

---

## 5. Single Postgres instance vs. several

**Decided (revised from the draft): one database, co-located tables** — the
operator directive for the offsite environment is a single PostgreSQL
database, and the web research confirmed that is also the dominant
pgvector-era pattern (Supabase, LangChain's PGVector store both co-locate
vectors with relational tables in the app database; the strong convention
that exists is one-database-many-schemas over many-databases, because
cross-database queries in Postgres are painful). Isolation is preserved by
table naming instead of database boundaries: every agent table is
`ai_agent_`-prefixed, and the two apps still never share a SQLAlchemy
session (the agent reaches Superset only through its REST client,
`integrations/superset/client.py:80-120`). **The one real hazard of sharing
a database was found live and fixed** — both apps used Alembic's default
`alembic_version` table (§7 R9). The `vector` extension lands in the shared
database; a deployment that wants it elsewhere can point
`AI_AGENT_VECTOR_DATABASE_URL` (and/or `AI_AGENT_DATABASE_URL`) at a second
database with zero code change — the split-later escape hatch survives.

---

## 6. User intent / flow ↔ actual UI

This is entirely an operator/deployment concern — **no end-user-facing UI
surfaces any of this today** (confirmed: no frontend reference to
`vector_index`, LanceDB paths, or storage backend mode exists in
`superset-frontend/src/features/AiAgentPanel`, and there's no admin
diagnostics endpoint exposing backend health). That silence is itself the
main UX risk to flag, not a UI gap to close:

- **Silent quality regression, not an error.** Every vector-store fallback
  in this codebase degrades closed (§4.5) — if pgvector is misconfigured
  (extension missing, wrong dimension, connection refused), the agent
  doesn't fail; it quietly falls back to in-process ranking or keyword
  overlap. A user asking the Copilot a question gets a *worse* answer with
  no error surfaced anywhere. Recommend adding this migration's rollout
  checklist item: confirm `is_available()`/`is_persistent()` (already
  present on both LanceDB classes; carry the same method to the pgvector
  twins) returns `True` in a smoke test immediately after cutover, not
  "wait for a user to notice recall got worse."
- **Cold-start re-indexing, not data loss.** Schema/instruction/example
  indexes are checksum-keyed and immutable per scope — migrating to a new
  empty pgvector table means the *first* query per project/scope after
  cutover pays a re-embedding cost (or serves degraded results until the
  background re-index completes), not that anything is lost. Communicate
  this as an expected one-time dip, not a bug.
- **Document "view original file" is a real data-migration item**, not just
  a config flip: existing documents uploaded under `local`/`s3` storage
  won't appear through the new `postgres` mode until their bytes are copied
  over. If this stack has real existing uploaded documents offsite, a
  one-time backfill script (read existing files, insert as bytea rows) is
  required before flipping the mode, or users will see "attachment
  unavailable" for pre-migration documents — flag this to the operator
  explicitly before cutover.

---

## 7. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Celery SQLAlchemy/Postgres transport is not production-safe per upstream (duplicate task execution with >1 worker, no pub/sub) | Run exactly one Celery worker replica offsite (document this constraint in the compose override); revisit if task volume/concurrency grows (§11-D2) |
| R2 | `CREATE EXTENSION vector` requires elevated Postgres privilege the offsite DBA/platform team must grant | Call this out as a pre-flight requirement, separate from the application deploy step, before Track D starts |
| R3 | Embedding dimension mismatch between old LanceDB data and new pgvector table (`vector(N)` is fixed at table-creation time) | Read `AI_AGENT_EMBEDDER_DIMENSIONS` at migration/DDL time, not hardcode 1536; fail loudly (not degrade-closed) on a DDL-time mismatch since this is a one-time setup error, not a steady-state fallback |
| R4 | Silent vector-store degrade-closed masks a real misconfiguration (§6) | Smoke-test `is_available()` immediately post-cutover; don't rely on user-visible symptoms |
| R5 | Existing uploaded document bytes not migrated before flipping `AI_AGENT_DOCUMENT_STORAGE=postgres` → "attachment unavailable" for old docs | One-time backfill script; sequence it before the mode flip, not after (§6) |
| R6 | `bytea` document storage growing unbounded blows up base-table bloat/backup size if upload volume is high | Existing `DEFAULT_MAX_DOCUMENT_BYTES` cap already bounds per-row size; if aggregate volume becomes a problem later, this is a config-only escape hatch back to the existing S3 mode — not a re-architecture |
| R7 | No production precedent in this repo for a Postgres `RESULTS_BACKEND`; a hand-rolled `BaseCache` subclass is new, untested code on a feature (async SQL Lab) that fails loudly if misconfigured (`SupersetResultsBackendNotConfigureException`) | Small, narrow implementation (K/V blob get/set/delete only); alternatively disable `allow_run_async` if async SQL Lab isn't actually used offsite (§4.2) — confirm with the operator which applies |
| R8 | Multiple tracks (A/C first, D/E harder) tempts a big-bang single rebuild-and-pray cutover | Sequence per §8; each track is independently testable against the external Postgres before the next starts |
| R9 | **(Found live)** Superset core and the agent both used Alembic's default `alembic_version` table — sharing one database made the agent's migrations resolve *Superset's* revision and crash at boot | Fixed: agent Alembic state moved to `ai_agent_alembic_version` (migrations/env.py `version_table`), with one-time auto-adoption of the legacy table on agent-owned databases (agent revision ids are distinguishable from Superset's hex ids, so foreign state is never touched) |
| R10 | **(Found live)** Alembic bootstraps its version table as `VARCHAR(32)`; this repo's revision ids are longer (`0015_nl_sql_example_db_scope_and_refs`). SQLite ignores the length; Postgres truncation-fails the very first upgrade | Fixed: the state table is pre-created with `VARCHAR(255)` before Alembic runs (Alembic reuses an existing table as-is); "present but empty" is treated as unversioned |
| R11 | **(Found live)** A metastore-backed `CACHE_CONFIG` breaks Superset's startup config-sync (`SeedSystemThemesCommand`) — the cache's `key_value` write lands inside an in-progress session flush | pg-only mode keeps all three perf caches on `NullCache` (upstream prod default); only `RESULTS_BACKEND` + the required filter-state/form-data caches ride the metastore |

---

## 8. Decision points (recommendation in **bold**; resolution noted per item)

Resolutions as built: D1 — all tracks landed in one pass (A/B/C/D/E), since
C/D/E verified independently against live Postgres along the way. D2 —
accepted, with the single-worker + `CELERYD_CONCURRENCY=1` constraint pinned
in `docker-compose.postgres-only.yml` itself (and the broker-healthcheck
disabled there, since `celery inspect ping` cannot work on the SQL
transport). D3 — built, but as a lazy delegate to the in-repo
`SupersetMetastoreCache` rather than a from-scratch backend (~16MB/value cap
inherited and documented). D4 — kept. D5 — moot for the compose form: the
overlay keeps the ephemeral in-run volumes (static assets handoff), which any
compose-capable runtime provides; only *durable* state moved. D6 — revised
to one shared database per §5 (operator directive + industry pattern), with
`AI_AGENT_VECTOR_DATABASE_URL` as the split-later escape hatch.

- **D1 — Scope this turn.** **Land Track A + Track C first** (metadata DB +
  agent relational DB — both are env-var/dependency-uncomment only, no new
  code) as an immediately-shippable slice; treat Track D (pgvector) and
  Track E (document blobs) as a follow-on implementation spec once this
  plan is agreed, since they require actual new classes/migrations. Track
  B (results backend + Celery) is orthogonal and can land in parallel with
  either.
- **D2 — Celery broker/backend.** **Accept the Postgres SQLAlchemy
  transport with a hard single-worker constraint**, given the offsite
  deployment's likely scale, rather than reintroducing Redis (which the
  "strictly PostgreSQL" constraint rules out) or building a bespoke broker.
  Document the single-worker requirement in the compose file itself as a
  comment, not just this spec, so it survives a future editor who adds a
  `replicas: 2`.
- **D3 — SQL Lab async results backend.** **Build the small
  `PostgresResultsBackend(BaseCache)`** rather than disabling
  `allow_run_async`, unless the operator confirms offsite SQL Lab never
  needs async execution (e.g., all queries are fast/interactive) — cheaper
  to ask than to build speculatively.
- **D4 — Document blob storage cap.** **Keep the existing
  `DEFAULT_MAX_DOCUMENT_BYTES=10_000_000` client-side guard as the de facto
  bytea size ceiling** rather than inventing a separate server-side limit —
  it's already comfortably inside Postgres's practical bytea range, so no
  new tiering logic needed.
- **D5 — Are truly-ephemeral (non-durable) Docker volumes available
  offsite, or none at all?** **Unknown — needs a direct answer from
  whoever controls the offsite platform**, since it changes whether Track F
  needs any work at all. If ephemeral scratch is fine, skip Track F
  entirely; if not, redirect `superset_home` writes to stdout/`/tmp`-style
  transient space instead of a declared volume.
- **D6 — One Postgres instance/many databases, or one per service.**
  **One instance, two databases (`superset`, `ai_agent`)** per §5 —
  matches current isolation, cheapest to provision, splits later without
  app changes if ever needed.

---

## 9. Out of scope (explicit)

- Migrating existing `db_home`/`superset_ai_agent_data` volume *data* into
  the external Postgres (this spec is architecture; a companion runbook
  should cover `pg_dump`/`sqlite3 .dump`-and-replay for whatever existing
  offsite data must survive cutover — flag if that's needed before treating
  this as done).
- Implementing Track D/E code (pgvector classes, blob-storage mode,
  `PostgresResultsBackend`) — this spec defines the shape; a follow-on
  `_impl.md` should track the actual build once D1 is confirmed.
- Re-architecting Celery away from SQLAlchemy-transport toward a
  Postgres-native task queue library (e.g., a `pgqueuer`/`procrastinate`
  style dedicated Postgres queue) — viable future upgrade if R1's
  single-worker constraint becomes limiting, deliberately not chosen now to
  keep this migration in-pattern with the repo's existing Celery config
  shape.
