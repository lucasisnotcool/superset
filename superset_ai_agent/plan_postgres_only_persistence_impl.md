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

# Postgres-Only Persistence — Implementation (as built)

Companion to [plan_postgres_only_persistence_spec.md](plan_postgres_only_persistence_spec.md).
That document is the architecture + decision record; this one is what was
actually built, how it was verified end-to-end on the Mac dev stack, and the
runbook for the Windows/offsite deploy.

## 1. What was built (by track)

### Track C — agent relational DB → Postgres (env only)
- `requirements-ai-agent.txt`: `psycopg[binary]` is now a **shipped** dependency
  (was commented out) — the image always carries the driver.
- No code change was needed: `persistence/database.py` + Alembic were already
  dialect-agnostic. Verified live: all `ai_agent_*` tables migrate to head on a
  fresh Postgres database at boot.

### Track D — vector stores → pgvector
New module [semantic_layer/pgvector.py](semantic_layer/pgvector.py):

- **`PgVectorCache`** — twin of `LanceVectorCache` (same method surface:
  `is_available/upsert/remove/search`), backing the `sql_pairs`,
  `instructions`, and `document_chunks` collections. One dimension-suffixed
  table `ai_agent_vector_cache_<dim>` partitioned by
  `(collection, scope_key, embedder_signature)` — the composite-key analogue of
  LanceDB's table-per-tuple. `upsert` is a native
  `INSERT ... ON CONFLICT DO UPDATE` (replaces LanceDB's delete-then-add).
- **`PgVectorSchemaStore`** + **`schema_retriever.PgVectorRetriever`** — twin
  of `LanceDbRetriever` (warm in-process / cold SQL search / rehydrate), rows
  in `ai_agent_schema_index_<dim>` keyed `(scope_key, checksum, item_seq)`.
  `replace()` clears the whole scope before inserting, so superseded manifest
  versions are garbage-collected (an improvement over LanceDB's stale tables).
- Selected by the **new `postgres` value** on the existing knobs — purely
  additive; `lancedb`/`memory`/`local` modes are untouched:
  - `WREN_VECTOR_INDEX=postgres` (MDL schema index)
  - `WREN_MEMORY_STORE=postgres` (sql_pairs + instructions caches; still
    SQL-durable via the sqlalchemy inner store)
  - `WREN_DOCUMENT_VECTOR_INDEX=postgres` (document chunks)
  - `AI_AGENT_VECTOR_DATABASE_URL` (optional; defaults to
    `AI_AGENT_DATABASE_URL` — the one-database topology)
- Design choices (researched + verified):
  - **Exact scan, no ANN index.** Ordering by pgvector's `<=>` cosine operator
    over one partition matches the in-process ranking exactly, sidesteps the
    2000-dim index cap and filtered-ANN recall loss, and is fast at this scale
    (partitions are 10²–10³ rows). HNSW is a pure DDL escape hatch if a
    deployment ever exceeds ~50k rows per partition.
  - **Dimension-suffixed tables** make an embedder-dimension change land in a
    new table instead of colliding with `vector(N)` DDL (spec R3).
  - **Text-format vectors** (`[x,y,...]` + `CAST(:v AS vector(N))`) — no
    `pgvector-python` dependency, no per-connection type registration.
  - **Degrade closed, loud at startup**: non-Postgres URL, unreachable server,
    or missing `vector` extension → one warning, `is_available()=False`, and
    every caller falls back exactly as with LanceDB absent.
    `effective_vector_index()` reports `postgres` / `memory_fallback` so the
    existing operator-visible badge keeps working.
  - The agent attempts `CREATE EXTENSION IF NOT EXISTS vector` itself and
    falls back to checking `pg_extension` — a DBA pre-installing the extension
    is sufficient when the app role lacks the privilege.

### Track E — uploaded-document bytes → Postgres
- `AI_AGENT_DOCUMENT_STORAGE=postgres` → new `PostgresDocumentStorage`
  ([semantic_layer/file_storage.py](semantic_layer/file_storage.py)): bytes in
  the new `ai_agent_document_blobs` table (model + Alembic `0017`), URI scheme
  `agent-db://documents/<document_id>/<filename>`. `LargeBinary`/bytea is the
  right store at the existing 10MB upload cap (TOASTed, deleted with the row,
  in pg_dump). Dialect-agnostic — works on dev SQLite too.
- Pre-existing documents under `local`/`s3` are **not** auto-migrated; their
  URIs keep working only if the old backend remains readable. Fresh offsite
  deploys have no legacy documents, so no backfill script was built (spec R5
  applies only if flipping an existing deployment with uploads worth keeping).

### Tracks A/B — Superset core (metadata DB, caches, results, Celery)
`docker/pythonpath_dev/superset_config.py` gains an env-selected block —
**`SUPERSET_PERSISTENCE_MODE=postgres`** (default `redis` keeps today's
behavior bit-for-bit):

- `RESULTS_BACKEND` → `MetastoreResultsBackend`, a lazy delegate to the
  in-repo `SupersetMetastoreCache` (metadata-DB `key_value` table). Lazy
  because constructing the real class at config-import time is a circular
  import; a fixed UUID namespace keeps web + worker keys aligned. Caveats
  carried into the class docstring: ~16MB per-value cap; expired rows need a
  periodic `DELETE FROM key_value WHERE expires_on < now()` if SQL Lab volume
  is high.
- `CACHE_CONFIG` / `DATA_CACHE_CONFIG` / `THUMBNAIL_CACHE_CONFIG` →
  `NullCache` (perf-only; upstream prod default). Found empirically: a
  metastore-backed `CACHE_CONFIG` breaks startup config-sync (its `key_value`
  write lands inside an in-progress session flush → "Failed to sync
  configuration to database"), so it is deliberately NullCache.
  The **required** caches (filter state, explore form data) already default to
  `SupersetMetastoreCache` upstream — Postgres-backed with zero config.
- Celery: `broker_url = sqla+postgresql://…` (kombu SQLAlchemy transport) and
  `result_backend = db+postgresql://…` on the metadata DB. The broker
  transport is upstream-experimental (polling; **no remote control**, so
  `celery inspect ping` healthchecks must be off; duplicate delivery possible
  with many consumers) — the overlay pins **one worker container,
  `CELERYD_CONCURRENCY=1`** per spec D2.

### Deployment packaging
- [docker-compose.postgres-only.yml](../docker-compose.postgres-only.yml) —
  third `-f` overlay: gates `db`/`redis`/`superset-websocket` behind a
  `local-state` profile, `!reset`s `superset-init`'s depends_on (needs compose
  ≥ 2.24), repoints the (unused) nginx `/ws` upstream so nginx still boots,
  sets `SUPERSET_PERSISTENCE_MODE=postgres` on all python services, pins the
  single low-concurrency worker, and disables the (impossible) worker
  healthcheck.
- `scripts/docker-compose-ai-up.ps1` — new **`-PostgresOnly`** switch appends
  the overlay.
- `docker/Dockerfile.postgres` — installs `postgresql-17-pgvector` so the
  bundled db (Mac dev / `local-state` rehearsals) can serve the vector stores.
  Offsite, pgvector availability on the **external** server is a DBA
  prerequisite instead.

## 2. Verification (Mac dev stack, live)

- Unit: `tests/unit_tests/superset_ai_agent/test_pgvector.py` (+ new cases in
  `test_semantic_layer_file_storage.py`) — degrade-closed on non-Postgres URLs,
  factory selection, URI round-trips; plus live round-trip tests gated on
  `AI_AGENT_TEST_PG_URL` (upsert/search/remove; cold-start retrieve from a
  fresh process; checksum-supersession GC).
- Existing suites for every touched module (schema_retriever, memory_store,
  instructions, document_retriever, vector_cache, file_storage): green.
- Live e2e (results recorded in §2.1 below): agent booted with all five knobs
  on `postgres` against the bundled db; Superset web + worker booted with
  `SUPERSET_PERSISTENCE_MODE=postgres` (worker banner shows
  `transport: sqla+postgresql://…`, `results: postgresql://…`).

### 2.1 Live e2e results (Mac, 2026-07-02, bundled db as the "external" Postgres)

Agent (`AI_AGENT_DATABASE_URL=postgresql+psycopg://…@db:5432/superset`, all
four mode knobs on `postgres`, image rebuilt):

- Alembic migrated a fresh shared database to head `0017_document_blobs`
  (21 `ai_agent_*` tables), after fixing R9/R10 below.
- `CREATE EXTENSION vector` executed by the agent itself (superuser role);
  `/health` reports **`"vector_index": "postgres"`**,
  `"semantic_layer_persistent": true`.
- App boot with the real 1536-dim embedder created
  `ai_agent_vector_cache_1536` + `ai_agent_schema_index_1536`.
- In-container round-trips (3-dim fake embedder, fresh instances simulating
  worker restarts): `PgVectorCache` upsert→search(top hit correct)→remove→
  cold-partition `None`; `PgVectorRetriever` cold-start SQL search with
  scores, checksum-supersession GC; `PostgresDocumentStorage`
  write→read→delete→`FileNotFoundError`, URI
  `agent-db://documents/e2e-doc/notes.md`. All green.

Superset (`SUPERSET_PERSISTENCE_MODE=postgres` via `docker/.env-local`,
web + worker + beat recreated):

- Web healthy; the "Failed to sync configuration to database" hit with a
  metastore `CACHE_CONFIG` (R11) is gone with `NullCache`.
- Worker banner: `transport: sqla+postgresql://…`, `results: postgresql://…`;
  beat-enqueued `reports.scheduler` tasks flow through `kombu_message` and
  complete into `celery_taskmeta` every minute.
- `RESULTS_BACKEND` resolves to `MetastoreResultsBackend`;
  set/get/delete of a binary payload against the `key_value` table verified
  under app context.

Defects found ONLY by the live run (both pre-existing, both fixed here —
unit tests on SQLite could never catch them):

- **R9 — Alembic version-table collision.** Superset core and the agent both
  used the default `alembic_version` table; on a shared database the agent's
  migrations resolved Superset's revision (`78a40c08b4be`) and crashed.
  Fixed: `version_table="ai_agent_alembic_version"` in migrations/env.py +
  one-time auto-adoption of legacy agent-owned state in
  `persistence/database.py` (agent revisions are `00NN_*`-style, Superset's
  are hex — foreign state is provably never touched; covered by a 4-scenario
  test).
- **R10 — revision ids longer than Alembic's `VARCHAR(32)`.** Postgres
  truncation-failed the first upgrade (SQLite ignores length). Fixed by
  pre-creating the state table with `VARCHAR(255)`; an empty pre-created
  table still counts as "unversioned" for the bootstrap guard.

## 3. Windows/offsite runbook

Code reaches the box via `git pull origin master` (one merge commit). Then:

1. **DBA prerequisites (external Postgres, once):**
   - A database (one is enough; agent tables are `ai_agent_`-prefixed and
     Superset's metadata tables coexist safely — this is the tested topology).
   - `CREATE EXTENSION vector;` by a privileged role (or grant the app role the
     ability; the agent runs `CREATE EXTENSION IF NOT EXISTS` itself).
   - The app role needs full DDL on the database (Alembic + runtime DDL).
2. **`docker/.env-local`** (create; wins over `docker/.env`):
   ```
   DATABASE_HOST=<external-host>
   DATABASE_PORT=<port>
   DATABASE_DB=<db>
   DATABASE_USER=<user>
   DATABASE_PASSWORD=<password>
   EXAMPLES_HOST=<external-host>   # examples DB — same server is fine
   EXAMPLES_PORT=<port>
   EXAMPLES_DB=<db or a second db>
   EXAMPLES_USER=<user>
   EXAMPLES_PASSWORD=<password>
   ```
   (`SUPERSET_PERSISTENCE_MODE=postgres` is injected by the overlay; setting it
   here too is harmless.)
3. **`superset_ai_agent/.env`:**
   ```
   AI_AGENT_DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:<port>/<db>
   WREN_VECTOR_INDEX=postgres
   WREN_MEMORY_STORE=postgres
   WREN_DOCUMENT_VECTOR_INDEX=postgres
   AI_AGENT_DOCUMENT_STORAGE=postgres
   ```
4. **Start (rebuild is REQUIRED — this stack bakes source into the image, and
   the agent image gains the psycopg dependency):**
   ```powershell
   scripts/docker-compose-ai-up.ps1 -detached -PostgresOnly
   ```
   (`up --build` is the script's default, so the rebuild happens; a plain
   `restart` would NOT pick any of this up.)
5. **Verify it's live in the containers, not assumed:**
   ```powershell
   docker compose -f docker-compose.no-bind.yml -f docker-compose.ai-agent.yml -f docker-compose.postgres-only.yml `
     exec superset-ai-agent grep -c "postgres" /app/superset_ai_agent/semantic_layer/pgvector.py   # file exists => code is in image
   docker compose ... logs superset-ai-agent | Select-String "vector_index"
   docker compose ... logs superset-worker | Select-String "sqla+postgresql"
   ```
   Smoke checks (spec R4 — do not wait for users to notice recall degraded):
   - agent readiness endpoint reports `vector_index: postgres` (not
     `memory_fallback`);
   - the external DB now has `ai_agent_*` tables, `alembic_version`, and (after
     first embed) `ai_agent_vector_cache_<dim>` / `ai_agent_schema_index_<dim>`;
   - upload a small document; its row appears in `ai_agent_document_blobs` and
     re-downloads.
6. **Constraints to keep:** exactly one `superset-worker` replica (kombu SQL
   transport); compose ≥ 2.24 on the box (`docker compose version`) for the
   `!reset` in the overlay.

Rollback: remove `-PostgresOnly` and revert the two env files — every change is
mode-gated, so the redis/volume topology is untouched.

## 4. Known limits / follow-ups

- **Celery on SQL transport** is the weakest link (spec D2/R1): polling-based,
  no `celery inspect`/Flower, duplicate delivery possible beyond one consumer.
  If alerts/reports volume grows, the escape hatch is a Postgres-native queue
  (procrastinate/pgqueuer) — a re-architecture, deliberately deferred.
- **`key_value` growth**: SQL Lab results + filter-state entries expire
  logically but rows are pruned only opportunistically; schedule a periodic
  `DELETE FROM key_value WHERE expires_on < now()` on the external DB.
- **Document blobs** count against DB size/backup; per-file cap is the existing
  10MB guard. Config-only escape hatch back to `s3` mode.
- **No data migration tooling** was built (fresh-DB cutover assumed). If the
  offsite box has SQLite/LanceDB/local-document state worth keeping, say so —
  a one-time copy script is a small, separate task (vectors don't need it:
  they re-embed on first use per scope; cold-start cost only).
- **GLOBAL_ASYNC_QUERIES** stays off — the websocket layer is Redis-only and is
  excluded by the overlay.
