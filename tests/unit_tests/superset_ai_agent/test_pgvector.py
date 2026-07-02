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

import os

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.semantic_layer.document_retriever import (
    create_document_index,
)
from superset_ai_agent.semantic_layer.instructions import (
    create_instruction_store,
    SqlAlchemyInstructionStore,
)
from superset_ai_agent.semantic_layer.memory_store import (
    create_memory,
    SqlAlchemyMemory,
)
from superset_ai_agent.semantic_layer.pgvector import (
    _normalized_url,
    _parse_vector,
    _vector_literal,
    PgVectorCache,
    PgVectorSchemaStore,
)
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    effective_vector_index,
    PgVectorRetriever,
    SchemaItem,
)

#: Set to a reachable postgresql+psycopg:// URL (with pgvector installable) to
#: run the live round-trip tests; unset, they skip.
_PG_URL = os.getenv("AI_AGENT_TEST_PG_URL", "")

requires_pg = pytest.mark.skipif(not _PG_URL, reason="AI_AGENT_TEST_PG_URL not set")


class _FakeEmbedder:
    """Deterministic 3-dim embedder: axis keyed by keyword."""

    def is_available(self) -> bool:
        return True

    def dimensions(self) -> int:
        return 3

    def signature(self) -> str:
        return "fake:3"

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "region" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            elif "revenue" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


def test_vector_literal_round_trips() -> None:
    vector = [0.25, -1.5, 3.0]
    assert _parse_vector(_vector_literal(vector)) == vector
    assert _parse_vector("[1,2,3]") == [1.0, 2.0, 3.0]
    assert _parse_vector([1, 2]) == [1.0, 2.0]


def test_normalized_url_pins_psycopg_and_rejects_non_postgres() -> None:
    assert (
        _normalized_url("postgresql://u:p@h:5432/db")
        == "postgresql+psycopg://u:p@h:5432/db"
    )
    assert (
        _normalized_url("postgresql+psycopg://u:p@h/db")
        == "postgresql+psycopg://u:p@h/db"
    )
    assert _normalized_url("sqlite:///./x.db") is None
    assert _normalized_url("not a url") is None


def test_pgvector_cache_degrades_closed_on_non_postgres_url() -> None:
    cache = PgVectorCache(_FakeEmbedder(), "sqlite:///./x.db", "sql_pairs")
    assert cache.is_available() is False
    assert cache.upsert(scope_key="s", row_id="r", text="body") is False
    assert cache.remove(scope_key="s", row_id="r") is False
    assert cache.search(scope_key="s", query="q", k=3) is None


def test_pgvector_schema_store_degrades_closed_on_non_postgres_url() -> None:
    store = PgVectorSchemaStore("sqlite:///./x.db", 3)
    assert store.is_available() is False
    assert store.exists("s", "c") is False
    assert store.replace(scope_key="s", checksum="c", rows=[], vectors=[]) is False
    assert store.search(scope_key="s", checksum="c", query_vector=[0.0], k=3) is None
    assert store.fetch_all(scope_key="s", checksum="c") is None


def test_create_retriever_postgres_mode_serves_in_process_when_pg_is_down() -> None:
    config = AgentConfig(
        wren_retriever="embedding",
        wren_vector_index="postgres",
        agent_database_url="sqlite:///./x.db",
    )
    retriever = create_retriever(config, _FakeEmbedder())
    assert isinstance(retriever, PgVectorRetriever)
    assert retriever.is_persistent() is False
    assert effective_vector_index(config, retriever) == "memory_fallback"

    items = [
        SchemaItem(kind="model", name="sales", text="model sales region column"),
        SchemaItem(kind="model", name="costs", text="model costs revenue column"),
    ]
    retriever.index(items, scope_key="s", checksum="c")
    top = retriever.retrieve("which region", scope_key="s", checksum="c", k=1)
    assert [item.name for item in top] == ["sales"]
    assert retriever.effective_name("s") == "embedding"


def test_effective_vector_index_reports_postgres_when_persistent() -> None:
    config = AgentConfig(wren_retriever="embedding", wren_vector_index="postgres")

    class _PersistentStub(PgVectorRetriever):
        def __init__(self) -> None:  # pylint: disable=super-init-not-called
            pass

        def is_persistent(self) -> bool:
            return True

    assert effective_vector_index(config, _PersistentStub()) == "postgres"


def test_create_document_index_postgres_mode_falls_back_to_keyword() -> None:
    config = AgentConfig(
        wren_document_vector_index="postgres",
        agent_database_url="sqlite:///./x.db",
    )
    index = create_document_index(config, _FakeEmbedder())
    assert index.is_embedding_backed is False


def _sqlite_session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from superset_ai_agent.persistence.models import Base

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def test_create_memory_postgres_mode_degrades_to_inner_store() -> None:
    config = AgentConfig(
        wren_memory_store="postgres",
        agent_database_url="sqlite:///./x.db",
    )
    memory = create_memory(
        config,
        session_factory=_sqlite_session_factory(),
        embedder=_FakeEmbedder(),
    )
    # Postgres unreachable -> the durable SQL store still serves (no cache wrap).
    assert isinstance(memory, SqlAlchemyMemory)


def test_create_instruction_store_postgres_mode_degrades_to_inner_store() -> None:
    config = AgentConfig(
        wren_memory_store="postgres",
        agent_database_url="sqlite:///./x.db",
    )
    store = create_instruction_store(
        config,
        session_factory=_sqlite_session_factory(),
        embedder=_FakeEmbedder(),
    )
    assert isinstance(store, SqlAlchemyInstructionStore)


@requires_pg
def test_pgvector_cache_round_trip_against_live_postgres() -> None:
    cache = PgVectorCache(_FakeEmbedder(), _PG_URL, "sql_pairs")
    assert cache.is_available() is True

    assert cache.upsert(scope_key="t:scope", row_id="a", text="the region row")
    assert cache.upsert(scope_key="t:scope", row_id="b", text="the revenue row")
    # Idempotent per row_id: refresh replaces the prior vector.
    assert cache.upsert(scope_key="t:scope", row_id="b", text="the revenue row v2")

    hits = cache.search(scope_key="t:scope", query="which region", k=2)
    assert hits is not None
    assert hits[0] == "a"

    assert cache.remove(scope_key="t:scope", row_id="a")
    assert cache.remove(scope_key="t:scope", row_id="b")
    # Empty partition reads as cold -> None (caller falls back).
    assert cache.search(scope_key="t:scope", query="which region", k=2) is None


@requires_pg
def test_pgvector_retriever_cold_start_against_live_postgres() -> None:
    embedder = _FakeEmbedder()
    items = [
        SchemaItem(kind="model", name="sales", text="model sales region column"),
        SchemaItem(kind="model", name="costs", text="model costs revenue column"),
    ]
    writer = PgVectorRetriever(embedder, _PG_URL)
    assert writer.is_persistent() is True
    writer.index(items, scope_key="t:cold", checksum="v1")

    # A fresh instance simulates a restarted worker: no in-process index, so the
    # first retrieve must serve from Postgres (cold SQL cosine search).
    reader = PgVectorRetriever(embedder, _PG_URL)
    assert reader.has_index("t:cold", "v1") is True
    top = reader.retrieve("which region", scope_key="t:cold", checksum="v1", k=1)
    assert [item.name for item in top] == ["sales"]
    assert top[0].score is not None
    assert reader.effective_name("t:cold") == "embedding"

    # Re-index under a new checksum garbage-collects the superseded version.
    writer2 = PgVectorRetriever(embedder, _PG_URL)
    writer2.index(items, scope_key="t:cold", checksum="v2")
    fresh = PgVectorRetriever(embedder, _PG_URL)
    assert fresh.has_index("t:cold", "v2") is True
    assert fresh.has_index("t:cold", "v1") is False
