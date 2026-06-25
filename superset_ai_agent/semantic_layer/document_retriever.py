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

"""Uploaded-document chunk retrieval (RAG over ``raw/`` documents, plan §3.4).

Reuses the existing per-row mutable vector store (:class:`LanceVectorCache`) — the
same primitive backing ``sql_pairs`` and ``instructions`` — pointed at a dedicated
``document_chunks`` collection, instead of inventing new infrastructure.

Governance — **degrade closed** (mirrors ``schema_retriever`` / ``vector_cache``):
when no LanceDB-backed cache is configured/available, retrieval falls back to
keyword overlap over the candidate chunks, so document RAG is never a hard
dependency. Embedding is "index once at write time"; only the *query* is embedded
per recall.

Scope: this Phase-0 data-layer index covers the **persistent LanceDB path** and the
**keyword fallback**. A non-LanceDB *in-memory embedding* index for documents is a
deferred gap (memory mode degrades to keyword); see the plan's risk notes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.llm.embeddings import Embedder
from superset_ai_agent.semantic_layer.document_chunks import (
    DocumentChunk,
    DocumentChunkMatch,
    keyword_rank_chunks,
)
from superset_ai_agent.semantic_layer.store import scope_hash
from superset_ai_agent.semantic_layer.vector_cache import LanceVectorCache

logger = logging.getLogger(__name__)

#: Collection name for document-chunk vectors (namespaced separately from the
#: ``sql_pairs`` / ``instructions`` collections in the same LanceDB store).
DOCUMENT_CHUNK_COLLECTION = "document_chunks"


def document_scope_key(
    project_id: str | None, scope: ConversationScope | None = None
) -> str:
    """Vector-cache partition key for a document's chunks.

    Project-scoped when the document belongs to a project, else the scope hash.
    Indexing and retrieval must derive this identically or recall misses. ``scope``
    is only required for project-less documents (it is ignored when ``project_id``
    is present, so project-scoped callers may omit it).
    """

    if project_id:
        return f"doc:{project_id}"
    if scope is None:
        raise ValueError("document_scope_key requires a scope when project_id is None.")
    return f"doc:{scope_hash(scope)}"


class VectorCache(Protocol):
    """The subset of :class:`LanceVectorCache` this index depends on.

    Declared so tests can inject a deterministic fake (no LanceDB) and exercise the
    embedding-ranked recall path.
    """

    def is_available(self) -> bool: ...

    def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool: ...

    def remove(self, *, scope_key: str, row_id: str) -> bool: ...

    def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None: ...


class DocumentChunkIndex:
    """Index + retrieve document chunks, degrading closed to keyword overlap.

    The vector cache is an accelerator, never the source of truth — the chunk rows
    in the store remain authoritative. A stale or unavailable cache simply routes
    recall through the keyword fallback.
    """

    def __init__(self, cache: VectorCache | None) -> None:
        self._cache = cache

    @property
    def is_embedding_backed(self) -> bool:
        """Whether an available vector cache will serve embedding-ranked recall."""

        return self._cache is not None and self._cache.is_available()

    def index(self, chunks: list[DocumentChunk], *, scope_key: str) -> list[str]:
        """Embed-and-store each chunk; returns the ids actually persisted.

        Idempotent per chunk id (deterministic per ``document_id``/index), so a
        reindex replaces vectors in place. A no-op (returns ``[]``) when no cache is
        available — recall then falls back to keyword.
        """

        if self._cache is None or not self._cache.is_available():
            return []
        embedded: list[str] = []
        for chunk in chunks:
            if self._cache.upsert(
                scope_key=scope_key, row_id=chunk.id, text=chunk.text
            ):
                embedded.append(chunk.id)
        return embedded

    def remove(self, chunk_ids: list[str], *, scope_key: str) -> None:
        """Evict chunk vectors (delete cascade / reindex). Best-effort, never raises."""

        if self._cache is None:
            return
        for chunk_id in chunk_ids:
            self._cache.remove(scope_key=scope_key, row_id=chunk_id)

    def retrieve(
        self,
        query: str,
        chunks: list[DocumentChunk],
        *,
        scope_key: str,
        k: int,
    ) -> list[DocumentChunk]:
        """Return up to ``k`` of ``chunks`` most relevant to ``query``.

        Uses the vector cache when it can serve a result, otherwise keyword overlap.
        ``chunks`` is the candidate set (e.g. a document's persisted chunks); vector
        hits are mapped back to it, so ids missing from the candidate set are skipped
        and an empty mapping falls back to keyword.
        """

        if k <= 0:
            return []
        if self._cache is not None:
            ids = self._cache.search(scope_key=scope_key, query=query, k=k)
            if ids is not None:
                by_id = {chunk.id: chunk for chunk in chunks}
                ordered = [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]
                if ordered:
                    return ordered
        return keyword_rank_chunks(query, chunks, k)


def find_exact_duplicate_matches(
    chunks: list[DocumentChunk],
) -> list[DocumentChunkMatch]:
    """Pair chunks with identical content (checksum equality) — the cheap dedup pass.

    Zero-dependency and always available; the first occurrence of a checksum is the
    canonical chunk, each later occurrence pairs back to it. Cosine near-duplicate
    detection (embedding-based) is a deferred follow-on (plan R5).
    """

    canonical: dict[str, DocumentChunk] = {}
    matches: list[DocumentChunkMatch] = []
    for chunk in chunks:
        prior = canonical.get(chunk.checksum)
        if prior is None:
            canonical[chunk.checksum] = chunk
            continue
        matches.append(
            DocumentChunkMatch(
                chunk_id=prior.id,
                other_chunk_id=chunk.id,
                document_id=prior.document_id,
                other_document_id=chunk.document_id,
                score=1.0,
                exact=True,
            )
        )
    return matches


def _document_lancedb_path(config: AgentConfig) -> str:
    """Dedicated LanceDB directory for document chunks.

    Deliberately distinct from the MDL/sql_pairs/instructions store
    (``wren_lancedb_path``) so document vectors live in their own database and can
    never affect — or be affected by — the existing MDL retrieval index.
    """

    if config.wren_document_lancedb_path:
        return config.wren_document_lancedb_path
    return str(Path(config.agent_storage_dir) / "lancedb_documents")


def create_document_index(
    config: AgentConfig, embedder: Embedder
) -> DocumentChunkIndex:
    """Build the document chunk index from config.

    Honors ``wren_document_vector_index`` (independent of the MDL ``wren_vector_index``
    knob): ``lancedb`` + an available embedder yields a persistent, embedding-ranked
    index in the documents-only LanceDB directory; otherwise the keyword-fallback
    index. Degrades closed when LanceDB or the embedder is unavailable.

    No vector backend is constructed when document indexing is disabled, so a
    deployment with the feature off never opens a LanceDB connection.
    """

    if (
        config.wren_document_indexing_enabled
        and config.wren_document_vector_index == "lancedb"
    ):
        cache = LanceVectorCache(
            embedder, _document_lancedb_path(config), DOCUMENT_CHUNK_COLLECTION
        )
        return DocumentChunkIndex(cache)
    return DocumentChunkIndex(None)
