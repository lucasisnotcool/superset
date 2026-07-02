# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""PostgreSQL/pgvector twins of the LanceDB vector stores (postgres-only deploys).

`PgVectorCache` mirrors `vector_cache.LanceVectorCache` (row-mutable collections:
``sql_pairs``, ``instructions``, ``document_chunks``) and `PgVectorSchemaStore`
backs `schema_retriever.PgVectorRetriever` (immutable whole-scope-per-checksum MDL
index). Both are selected by the ``postgres`` value on the existing mode knobs
(``WREN_VECTOR_INDEX`` / ``WREN_MEMORY_STORE`` / ``WREN_DOCUMENT_VECTOR_INDEX``)
so a deployment with no writable disk keeps every vector durable in the same
external Postgres that holds the agent's relational tables.

Governance — **degrade closed**, exactly like the LanceDB classes: a missing
driver, an unreachable server, a missing ``vector`` extension, or any query error
logs a warning and reports unavailable / returns ``None``; callers fall back to
their in-process or keyword paths. The one loud path is the startup warning, so a
misconfiguration is visible to an operator instead of silently degrading recall.

Ranking is an **exact** scan ordered by pgvector's cosine operator (``<=>``) over
the rows of one ``(collection, scope_key, signature)`` partition — the same
partition a LanceDB table held. No ANN index is created: partitions are small
(hundreds to low-thousands of rows), exact scan matches the in-process ranking
bit-for-bit, and it sidesteps both pgvector's 2000-dimension index cap and the
filtered-ANN recall loss. If a deployment ever grows past ~50k rows per
partition, adding an HNSW index (``USING hnsw (embedding vector_cosine_ops)``)
is a pure DDL change — no code here assumes its absence.

Tables are dimension-suffixed (``ai_agent_vector_cache_1536``) so an embedder
dimension change can never collide with existing ``vector(N)`` DDL — the new
dimension simply lands in its own table, mirroring how the embedder signature
already partitions rows.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.engine import Engine, make_url

from superset_ai_agent.llm.embeddings import Embedder

logger = logging.getLogger(__name__)

_POSTGRES_DIALECT = "postgresql"

#: Process-wide engine per URL so every pgvector store (cache collections, the
#: schema index) shares one pool instead of opening per-store connections.
_engines: dict[str, Engine] = {}
_engines_lock = threading.Lock()

#: Extension/DDL bootstrap already performed this process, keyed by url::table.
_prepared: set[str] = set()
_prepared_lock = threading.Lock()


def _normalized_url(database_url: str) -> str | None:
    """Return a psycopg-driver Postgres URL, or ``None`` for non-Postgres URLs.

    ``postgresql://`` without an explicit driver would make SQLAlchemy default to
    psycopg2 (not installed here); pin the psycopg (v3) driver the requirements
    file ships.
    """

    try:
        url = make_url(database_url)
    except Exception:  # pylint: disable=broad-except
        return None
    if url.get_backend_name() != _POSTGRES_DIALECT:
        return None
    if url.drivername == _POSTGRES_DIALECT:
        url = url.set(drivername=f"{_POSTGRES_DIALECT}+psycopg")
    return url.render_as_string(hide_password=False)


def _shared_engine(database_url: str) -> Engine | None:
    """Create/reuse the process-wide engine for ``database_url``; None on failure."""

    normalized = _normalized_url(database_url)
    if normalized is None:
        logger.warning(
            "pgvector store requires a postgresql:// database URL; got a "
            "non-Postgres URL. Vector persistence disabled."
        )
        return None
    with _engines_lock:
        engine = _engines.get(normalized)
        if engine is None:
            try:
                engine = create_engine(
                    normalized,
                    pool_pre_ping=True,
                    pool_size=5,
                    max_overflow=5,
                    future=True,
                )
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("pgvector engine unavailable (%s); disabled.", ex)
                return None
            _engines[normalized] = engine
        return engine


def _ensure_extension(engine: Engine) -> bool:
    """Make sure the ``vector`` extension is installed; degrade closed if not.

    ``CREATE EXTENSION`` needs an elevated role; when that fails, fall back to
    checking whether an operator/DBA already installed it.
    """

    try:
        with engine.begin() as conn:
            conn.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
        return True
    except Exception:  # pylint: disable=broad-except
        try:
            with engine.connect() as conn:
                installed = conn.execute(
                    sql_text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                ).scalar()
            if installed:
                return True
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("pgvector extension check failed (%s); disabled.", ex)
            return False
        logger.warning(
            "The 'vector' extension is not installed and this role cannot "
            "create it. Run `CREATE EXTENSION vector;` as a privileged role "
            "on the target database. Vector persistence disabled until then."
        )
        return False


def _prepare(engine: Engine, table: str, ddl: str) -> bool:
    """Run one-time extension + table DDL for ``table``; never raises."""

    key = f"{engine.url.render_as_string(hide_password=False)}::{table}"
    with _prepared_lock:
        if key in _prepared:
            return True
    if not _ensure_extension(engine):
        return False
    try:
        with engine.begin() as conn:
            conn.execute(sql_text(ddl))
    except Exception as ex:  # pylint: disable=broad-except
        # A concurrent worker may have won the CREATE TABLE race; re-check.
        try:
            with engine.connect() as conn:
                conn.execute(sql_text(f"SELECT 1 FROM {table} LIMIT 1"))  # noqa: S608
        except Exception:  # pylint: disable=broad-except
            logger.warning("pgvector DDL for %s failed (%s); disabled.", table, ex)
            return False
    with _prepared_lock:
        _prepared.add(key)
    return True


def _vector_literal(vector: list[float]) -> str:
    """Serialize to pgvector's text input format (``[x,y,...]``)."""

    return "[" + ",".join(str(float(value)) for value in vector) + "]"


def _parse_vector(value: Any) -> list[float]:
    """Parse a ``vector``/``vector::text`` value back to floats."""

    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    body = str(value).strip().strip("[]")
    if not body:
        return []
    return [float(item) for item in body.split(",")]


class PgVectorCache:
    """`LanceVectorCache` twin on Postgres/pgvector — same contract, same keying.

    Rows live in one dimension-suffixed table partitioned by ``(collection,
    scope_key, signature)`` (composite key instead of LanceDB's table-per-tuple);
    ``upsert`` is a native ``INSERT ... ON CONFLICT DO UPDATE`` instead of the
    LanceDB delete-then-add workaround. The cache stays an accelerator, never a
    source of truth: any backend failure degrades closed.
    """

    name = "postgres"

    def __init__(self, embedder: Embedder, database_url: str, collection: str) -> None:
        self.embedder = embedder
        self.collection = collection
        self._dimensions = max(int(embedder.dimensions()), 0)
        self._engine = _shared_engine(database_url)
        self._table = f"ai_agent_vector_cache_{self._dimensions}"
        self._ready = False
        if self._engine is not None and self._dimensions > 0:
            self._ready = _prepare(self._engine, self._table, self._ddl())

    def _ddl(self) -> str:
        return f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                collection TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                signature TEXT NOT NULL,
                row_id TEXT NOT NULL,
                body TEXT NOT NULL,
                embedding vector({self._dimensions}) NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (collection, scope_key, signature, row_id)
            )
        """

    def is_available(self) -> bool:
        """Whether the durable backend connected and an embedder can vectorize."""

        return self._ready and self.embedder.is_available()

    def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool:
        body = text
        if not self.is_available() or not row_id:
            return False
        try:
            vector = self.embedder.embed([body])[0]
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache embed failed (%s); row not cached.", ex)
            return False
        if len(vector) != self._dimensions:
            logger.warning(
                "Embedder returned %d dimensions but the pgvector table is "
                "vector(%d); row not cached. Align AI_AGENT_EMBEDDER_DIMENSIONS "
                "with the embedder's actual output.",
                len(vector),
                self._dimensions,
            )
            return False
        statement = sql_text(
            f"""
            INSERT INTO {self._table}
                (collection, scope_key, signature, row_id, body, embedding)
            VALUES
                (:collection, :scope_key, :signature, :row_id, :body,
                 CAST(:embedding AS vector({self._dimensions})))
            ON CONFLICT (collection, scope_key, signature, row_id)
            DO UPDATE SET
                body = EXCLUDED.body,
                embedding = EXCLUDED.embedding,
                updated_at = now()
            """  # noqa: S608
        )
        try:
            assert self._engine is not None  # guarded by is_available()
            with self._engine.begin() as conn:
                conn.execute(
                    statement,
                    {
                        "collection": self.collection,
                        "scope_key": scope_key,
                        "signature": self.embedder.signature(),
                        "row_id": row_id,
                        "body": body,
                        "embedding": _vector_literal(vector),
                    },
                )
            return True
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache upsert failed (%s); row not cached.", ex)
            return False

    def remove(self, *, scope_key: str, row_id: str) -> bool:
        """Delete one cached row; best-effort, never raises."""

        if not self._ready or not row_id:
            return False
        try:
            assert self._engine is not None
            with self._engine.begin() as conn:
                conn.execute(
                    sql_text(
                        f"DELETE FROM {self._table} WHERE collection = :collection "  # noqa: S608
                        "AND scope_key = :scope_key AND row_id = :row_id"
                    ),
                    {
                        "collection": self.collection,
                        "scope_key": scope_key,
                        "row_id": row_id,
                    },
                )
            return True
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache remove failed (%s).", ex)
            return False

    def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None:
        """Return up to ``k`` row ids ranked by cosine, or ``None`` to fall back.

        ``None`` covers unavailable / errored / **cold** (empty partition), so
        callers keep their own recall path — matching the LanceDB semantics where
        a missing table returned ``None``.
        """

        if not self.is_available() or k <= 0 or not query.strip():
            return None
        cast = f"CAST(:query AS vector({self._dimensions}))"
        try:
            query_vector = self.embedder.embed([query])[0]
            assert self._engine is not None
            with self._engine.connect() as conn:
                rows = conn.execute(
                    sql_text(
                        f"""
                        SELECT row_id FROM {self._table}
                        WHERE collection = :collection
                          AND scope_key = :scope_key
                          AND signature = :signature
                        ORDER BY embedding <=> {cast}
                        LIMIT :k
                        """  # noqa: S608
                    ),
                    {
                        "collection": self.collection,
                        "scope_key": scope_key,
                        "signature": self.embedder.signature(),
                        "query": _vector_literal(query_vector),
                        "k": k,
                    },
                ).fetchall()
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache search failed (%s); recall falls back.", ex)
            return None
        if not rows:
            return None  # cold partition — let the caller's own ranking serve
        return [str(row[0]) for row in rows if row[0]]


class PgVectorSchemaStore:
    """Durable MDL schema-item vectors, keyed by ``(scope_key, checksum)`` rows.

    The persistence layer under `schema_retriever.PgVectorRetriever` — the twin of
    the per-checksum LanceDB tables, as rows in one dimension-suffixed table.
    ``replace`` clears **all** of a scope's rows before inserting the new
    checksum's, so superseded manifest versions are garbage-collected instead of
    accumulating (an improvement over the stale LanceDB tables).
    """

    def __init__(self, database_url: str, dimensions: int) -> None:
        self._dimensions = max(int(dimensions), 0)
        self._engine = _shared_engine(database_url)
        self._table = f"ai_agent_schema_index_{self._dimensions}"
        self._ready = False
        if self._engine is not None and self._dimensions > 0:
            self._ready = _prepare(self._engine, self._table, self._ddl())

    def _ddl(self) -> str:
        return f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                scope_key TEXT NOT NULL,
                checksum TEXT NOT NULL,
                item_seq INTEGER NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                model TEXT,
                body TEXT NOT NULL,
                embedding vector({self._dimensions}) NOT NULL,
                PRIMARY KEY (scope_key, checksum, item_seq)
            )
        """

    def is_available(self) -> bool:
        return self._ready

    def exists(self, scope_key: str, checksum: str) -> bool:
        if not self._ready:
            return False
        try:
            assert self._engine is not None
            with self._engine.connect() as conn:
                found = conn.execute(
                    sql_text(
                        f"SELECT 1 FROM {self._table} WHERE scope_key = :scope_key "  # noqa: S608
                        "AND checksum = :checksum LIMIT 1"
                    ),
                    {"scope_key": scope_key, "checksum": checksum},
                ).scalar()
            return bool(found)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("pgvector schema-index exists check failed (%s).", ex)
            return False

    def replace(
        self,
        *,
        scope_key: str,
        checksum: str,
        rows: list[dict[str, Any]],
        vectors: list[list[float]],
    ) -> bool:
        """Atomically swap the scope's index to this checksum's rows."""

        if not self._ready or not rows:
            return False
        if any(len(vector) != self._dimensions for vector in vectors):
            logger.warning(
                "Embedding dimensions do not match vector(%d); schema index "
                "not persisted.",
                self._dimensions,
            )
            return False
        params = [
            {
                "scope_key": scope_key,
                "checksum": checksum,
                "item_seq": seq,
                "kind": row["kind"],
                "name": row["name"],
                "model": row.get("model"),
                "body": row["body"],
                "embedding": _vector_literal(vector),
            }
            for seq, (row, vector) in enumerate(zip(rows, vectors, strict=True))
        ]
        try:
            assert self._engine is not None
            with self._engine.begin() as conn:
                conn.execute(
                    sql_text(f"DELETE FROM {self._table} WHERE scope_key = :scope_key"),  # noqa: S608
                    {"scope_key": scope_key},
                )
                conn.execute(
                    sql_text(
                        f"""
                        INSERT INTO {self._table}
                            (scope_key, checksum, item_seq, kind, name, model, body,
                             embedding)
                        VALUES
                            (:scope_key, :checksum, :item_seq, :kind, :name, :model,
                             :body, CAST(:embedding AS vector({self._dimensions})))
                        """  # noqa: S608
                    ),
                    params,
                )
            return True
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("pgvector schema-index persist failed (%s).", ex)
            return False

    def search(
        self,
        *,
        scope_key: str,
        checksum: str,
        query_vector: list[float],
        k: int,
    ) -> list[dict[str, Any]] | None:
        """Top-k rows by cosine similarity, or ``None`` on error/cold."""

        if not self._ready or k <= 0:
            return None
        cast = f"CAST(:query AS vector({self._dimensions}))"
        try:
            assert self._engine is not None
            with self._engine.connect() as conn:
                rows = (
                    conn.execute(
                        sql_text(
                            f"""
                        SELECT kind, name, model, body,
                               1 - (embedding <=> {cast}) AS score
                        FROM {self._table}
                        WHERE scope_key = :scope_key AND checksum = :checksum
                        ORDER BY embedding <=> {cast}
                        LIMIT :k
                        """  # noqa: S608
                        ),
                        {
                            "scope_key": scope_key,
                            "checksum": checksum,
                            "query": _vector_literal(query_vector),
                            "k": k,
                        },
                    )
                    .mappings()
                    .fetchall()
                )
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("pgvector schema-index search failed (%s).", ex)
            return None
        if not rows:
            return None
        return [dict(row) for row in rows]

    def fetch_all(
        self, *, scope_key: str, checksum: str
    ) -> tuple[list[dict[str, Any]], list[list[float]]] | None:
        """All rows + vectors for cold-start rehydration into the in-process index."""

        if not self._ready:
            return None
        try:
            assert self._engine is not None
            with self._engine.connect() as conn:
                rows = (
                    conn.execute(
                        sql_text(
                            f"""
                        SELECT kind, name, model, body, embedding::text AS embedding
                        FROM {self._table}
                        WHERE scope_key = :scope_key AND checksum = :checksum
                        ORDER BY item_seq
                        """  # noqa: S608
                        ),
                        {"scope_key": scope_key, "checksum": checksum},
                    )
                    .mappings()
                    .fetchall()
                )
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("pgvector schema-index rehydrate failed (%s).", ex)
            return None
        if not rows:
            return None
        items = [
            {
                "kind": row["kind"],
                "name": row["name"],
                "model": row["model"],
                "body": row["body"],
            }
            for row in rows
        ]
        vectors = [_parse_vector(row["embedding"]) for row in rows]
        return items, vectors
