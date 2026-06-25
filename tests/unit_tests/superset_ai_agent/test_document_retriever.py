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

from __future__ import annotations

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.embeddings import NullEmbedder
from superset_ai_agent.semantic_layer.document_chunks import (
    build_chunk_records,
    DocumentChunk,
)
from superset_ai_agent.semantic_layer.document_retriever import (
    create_document_index,
    DocumentChunkIndex,
    find_exact_duplicate_matches,
)


class _FakeCache:
    """Deterministic stand-in for LanceVectorCache (no LanceDB needed)."""

    def __init__(self, ranking: list[str] | None) -> None:
        self._ranking = ranking
        self.upserted: list[str] = []
        self.removed: list[str] = []

    def is_available(self) -> bool:
        return True

    def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool:
        self.upserted.append(row_id)
        return True

    def remove(self, *, scope_key: str, row_id: str) -> bool:
        self.removed.append(row_id)
        return True

    def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None:
        if self._ranking is None:
            return None
        return self._ranking[:k]


def _chunks() -> list[DocumentChunk]:
    return build_chunk_records(
        "doc-1",
        "revenue by region\n\nweather notes\n\ncustomer churn drivers",
    )


def test_retrieve_uses_cache_ranking_when_available() -> None:
    chunks = _chunks()
    # Cache "ranks" the third chunk first, then the first.
    cache = _FakeCache(ranking=[chunks[2].id, chunks[0].id])
    index = DocumentChunkIndex(cache)

    result = index.retrieve("anything", chunks, scope_key="s", k=2)
    assert [chunk.id for chunk in result] == [chunks[2].id, chunks[0].id]
    assert index.is_embedding_backed is True


def test_index_and_remove_forward_to_cache() -> None:
    chunks = _chunks()
    cache = _FakeCache(ranking=[])
    index = DocumentChunkIndex(cache)

    embedded = index.index(chunks, scope_key="s")
    assert embedded == [chunk.id for chunk in chunks]
    assert cache.upserted == [chunk.id for chunk in chunks]

    index.remove([chunks[0].id], scope_key="s")
    assert cache.removed == [chunks[0].id]


def test_retrieve_falls_back_to_keyword_when_cache_cold() -> None:
    chunks = _chunks()
    index = DocumentChunkIndex(_FakeCache(ranking=None))  # search -> None
    result = index.retrieve("customer churn", chunks, scope_key="s", k=3)
    assert result[0].text == "customer churn drivers"


def test_retrieve_falls_back_when_cache_ids_unknown() -> None:
    chunks = _chunks()
    # Cache returns ids not present in the candidate set (stale) -> keyword fallback.
    index = DocumentChunkIndex(_FakeCache(ranking=["ghost-1", "ghost-2"]))
    result = index.retrieve("weather", chunks, scope_key="s", k=3)
    assert result[0].text == "weather notes"


def test_no_cache_index_is_keyword_only() -> None:
    chunks = _chunks()
    index = DocumentChunkIndex(None)
    assert index.is_embedding_backed is False
    assert index.index(chunks, scope_key="s") == []
    result = index.retrieve("region revenue", chunks, scope_key="s", k=1)
    assert result[0].text == "revenue by region"


def test_find_exact_duplicate_matches_pairs_identical_chunks() -> None:
    chunks = build_chunk_records("doc-1", "repeat me\n\nunique\n\nrepeat me")
    matches = find_exact_duplicate_matches(chunks)
    assert len(matches) == 1
    match = matches[0]
    assert match.exact is True
    assert match.score == 1.0
    assert match.chunk_id == chunks[0].id
    assert match.other_chunk_id == chunks[2].id


def test_factory_memory_mode_is_keyword_only() -> None:
    index = create_document_index(AgentConfig(), NullEmbedder())
    assert index.is_embedding_backed is False
