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

"""Persistent, row-mutable vector collection (Wren foundation §0.2, plan C0).

`LanceVectorCache` backs the named `sql_pairs` and `instructions` collections:
text is embedded **once at write time** and recall is an ANN lookup, instead of
re-embedding the whole candidate set on every query. Unlike
`schema_retriever.LanceDbRetriever` (immutable whole-table-per-checksum, keyed by
MDL content), this collection mutates row-by-row (`upsert`/`remove`) as the
learning loop and the operator add/refresh/delete individual rows.

Governance — **degrade closed**: when LanceDB or the embedder is unavailable, or
on any backend error, every method is a no-op and `search` returns ``None`` so
the caller falls back to its existing SQL/keyword recall path. A returned ``None``
means "cache unavailable, fall back"; a returned list (possibly ordered subset)
means "the populated collection was searched."

Rows are keyed by ``(collection, scope_key, embedder signature)`` table so an
embedder change can never mix vectors from different models; the stale-signature
table is simply cold (``search`` returns ``None``) until repopulated.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from superset_ai_agent.llm.embeddings import Embedder

logger = logging.getLogger(__name__)


def _table_name(collection: str, scope_key: str, signature: str) -> str:
    # LanceDB table names allow only alphanumerics/underscore/hyphen/period; the
    # scope_key and embedder signature carry ':' / '#', so hash them.
    scope = hashlib.sha1(scope_key.encode("utf-8")).hexdigest()[:16]  # noqa: S324
    sig = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]  # noqa: S324
    return f"{collection}_{scope}_{sig}"


class LanceVectorCache:
    """Per-(collection, scope) LanceDB rows: embed-on-write, ANN-on-recall.

    The cache is an *accelerator*, never a source of truth — the SQL/in-memory
    store still owns durability, dedup and eviction. Rows that no longer exist in
    the source store simply map to nothing on recall, so a stale cache row is
    inert (minor bloat), never a correctness problem.
    """

    name = "lancedb"

    def __init__(self, embedder: Embedder, path: str, collection: str) -> None:
        self.embedder = embedder
        self.collection = collection
        self.path = path
        self._db = self._connect()

    def _connect(self) -> Any | None:
        try:
            import lancedb  # type: ignore  # lazy, optional dependency

            return lancedb.connect(self.path)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("LanceDB unavailable (%s); vector cache disabled.", ex)
            return None

    def is_available(self) -> bool:
        """Whether the durable backend connected and an embedder can vectorize."""

        return self._db is not None and self.embedder.is_available()

    def _table(self, scope_key: str, *, create_row: dict[str, Any] | None = None):
        if self._db is None:
            return None
        name = _table_name(self.collection, scope_key, self.embedder.signature())
        try:
            return self._db.open_table(name)
        except Exception:  # pylint: disable=broad-except - missing table = cold
            if create_row is None:
                return None
            try:
                return self._db.create_table(name, data=[create_row])
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("LanceDB create_table failed (%s); cache skipped.", ex)
                return None

    def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool:
        """Embed ``text`` once and (over)write the row; returns whether persisted.

        Idempotent per ``row_id``: a refresh replaces the prior vector. Never
        raises — a backend failure leaves the source store authoritative.
        """

        if not self.is_available() or not row_id:
            return False
        try:
            vector = self.embedder.embed([text])[0]
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache embed failed (%s); row not cached.", ex)
            return False
        row = {"id": row_id, "text": text, "vector": vector}
        table = self._table(scope_key, create_row=row)
        if table is None:
            return False
        try:
            # delete-then-add is the version-robust upsert (older lancedb has no
            # merge_insert); the create path above already wrote the first row.
            table.delete(f"id = '{_escape(row_id)}'")
            table.add([row])
            return True
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache upsert failed (%s); row not cached.", ex)
            return False

    def remove(self, *, scope_key: str, row_id: str) -> bool:
        """Delete one cached row; best-effort, never raises."""

        if self._db is None or not row_id:
            return False
        table = self._table(scope_key)
        if table is None:
            return False
        try:
            table.delete(f"id = '{_escape(row_id)}'")
            return True
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache remove failed (%s).", ex)
            return False

    def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None:
        """Return up to ``k`` row ids ranked by cosine, or ``None`` to fall back.

        ``None`` means the cache is unavailable / cold / errored, so the caller
        must use its own recall path. An empty list is only returned for an empty
        query against a populated table.
        """

        if not self.is_available() or k <= 0:
            return None
        table = self._table(scope_key)
        if table is None:
            return None
        if not query.strip():
            return None
        try:
            query_vector = self.embedder.embed([query])[0]
            rows = (
                table.search(query_vector)
                .metric("cosine")
                .limit(k)
                .to_arrow()
                .to_pylist()
            )
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Vector cache search failed (%s); recall falls back.", ex)
            return None
        return [str(row["id"]) for row in rows if row.get("id")]


def _escape(value: str) -> str:
    """Escape single quotes for a LanceDB SQL filter literal."""

    return value.replace("'", "''")
