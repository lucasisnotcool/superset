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

"""Plan C0 — persistent, row-mutable `LanceVectorCache` (sql_pairs/instructions)."""

from __future__ import annotations

import pytest

from superset_ai_agent.llm.embeddings import NullEmbedder
from superset_ai_agent.semantic_layer import vector_cache as vc
from superset_ai_agent.semantic_layer.vector_cache import LanceVectorCache

_REVENUE = {"revenue", "sales", "total"}


class _TopicEmbedder:
    """Deterministic 2-D embedder: revenue topic -> [1,0], else [0,1]."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.embedded: list[list[str]] = []

    def is_available(self) -> bool:
        return self.available

    def dimensions(self) -> int:
        return 2

    def signature(self) -> str:
        return "topic:2"

    def embed(self, texts):
        self.embedded.append(list(texts))
        return [
            [1.0, 0.0] if _REVENUE & {w.lower() for w in t.split()} else [0.0, 1.0]
            for t in texts
        ]


def test_cache_unavailable_without_embedder(tmp_path) -> None:
    cache = LanceVectorCache(NullEmbedder(), str(tmp_path / "db"), "sql_pairs")
    assert cache.is_available() is False
    assert cache.upsert(scope_key="u:s", row_id="a", text="hi") is False
    # search degrades to None (caller falls back) — never raises.
    assert cache.search(scope_key="u:s", query="hi", k=3) is None


def test_cache_search_cold_scope_returns_none(tmp_path) -> None:
    pytest.importorskip("lancedb")
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    # Never written → no table → fall back, not an empty list.
    assert cache.search(scope_key="u:s", query="revenue", k=3) is None


def test_cache_upsert_and_search_ranks_by_cosine(tmp_path) -> None:
    pytest.importorskip("lancedb")
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    assert cache.upsert(scope_key="u:s", row_id="rev", text="total revenue") is True
    assert cache.upsert(scope_key="u:s", row_id="emp", text="names list") is True

    ids = cache.search(scope_key="u:s", query="quarterly sales total", k=2)
    assert ids is not None
    assert ids[0] == "rev"  # revenue topic wins on cosine


def test_cache_upsert_refresh_is_idempotent_by_id(tmp_path) -> None:
    pytest.importorskip("lancedb")
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    cache.upsert(scope_key="u:s", row_id="rev", text="total revenue")
    cache.upsert(scope_key="u:s", row_id="rev", text="total sales revenue")

    ids = cache.search(scope_key="u:s", query="revenue", k=5)
    assert ids == ["rev"]  # one row, not two — the refresh overwrote in place


def test_cache_remove_drops_row(tmp_path) -> None:
    pytest.importorskip("lancedb")
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    cache.upsert(scope_key="u:s", row_id="rev", text="total revenue")
    cache.upsert(scope_key="u:s", row_id="emp", text="names list")

    assert cache.remove(scope_key="u:s", row_id="rev") is True
    ids = cache.search(scope_key="u:s", query="revenue total sales", k=5)
    assert ids is not None
    assert "rev" not in ids


def test_cache_scope_isolation(tmp_path) -> None:
    pytest.importorskip("lancedb")
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    cache.upsert(scope_key="u:s1", row_id="rev", text="total revenue")
    # A different scope has its own table → cold → None.
    assert cache.search(scope_key="u:s2", query="revenue", k=3) is None


def test_cache_persists_across_instances(tmp_path) -> None:
    pytest.importorskip("lancedb")
    path = str(tmp_path / "db")
    LanceVectorCache(_TopicEmbedder(), path, "sql_pairs").upsert(
        scope_key="u:s", row_id="rev", text="total revenue"
    )
    # A fresh cache on the same path (another worker) reads the persisted row.
    reader = LanceVectorCache(_TopicEmbedder(), path, "sql_pairs")
    assert reader.search(scope_key="u:s", query="revenue total", k=3) == ["rev"]


def test_cache_collections_are_separate_tables(tmp_path) -> None:
    pytest.importorskip("lancedb")
    path = str(tmp_path / "db")
    pairs = LanceVectorCache(_TopicEmbedder(), path, "sql_pairs")
    instr = LanceVectorCache(_TopicEmbedder(), path, "instructions")
    pairs.upsert(scope_key="u:s", row_id="rev", text="total revenue")
    # Same scope+id, different collection → the instructions table stays cold.
    assert instr.search(scope_key="u:s", query="revenue", k=3) is None


def test_cache_degrades_to_none_on_connect_failure(tmp_path, monkeypatch) -> None:
    pytest.importorskip("lancedb")
    import lancedb

    monkeypatch.setattr(
        lancedb, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    assert cache.is_available() is False
    assert cache.upsert(scope_key="u:s", row_id="a", text="hi") is False
    assert cache.search(scope_key="u:s", query="hi", k=3) is None


def test_cache_search_degrades_when_embedding_raises(tmp_path) -> None:
    pytest.importorskip("lancedb")
    cache = LanceVectorCache(_TopicEmbedder(), str(tmp_path / "db"), "sql_pairs")
    cache.upsert(scope_key="u:s", row_id="rev", text="total revenue")

    class _Boom(_TopicEmbedder):
        def embed(self, texts):
            raise RuntimeError("embedding backend down")

    cache.embedder = _Boom()
    # Query embedding raises → fall back (None), never crash the recall path.
    assert cache.search(scope_key="u:s", query="revenue", k=3) is None


def test_table_name_is_collection_scope_signature_scoped() -> None:
    a = vc._table_name("sql_pairs", "u:s", "topic:2")
    assert a.startswith("sql_pairs_")
    # Distinct collection, scope, or signature → distinct table.
    assert a != vc._table_name("instructions", "u:s", "topic:2")
    assert a != vc._table_name("sql_pairs", "u:s2", "topic:2")
    assert a != vc._table_name("sql_pairs", "u:s", "topic:3")
