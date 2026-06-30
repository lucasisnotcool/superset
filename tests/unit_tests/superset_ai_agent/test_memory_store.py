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

"""Memory learning loop — database-scoped pool + access-aware recall (F1/F2)."""

from __future__ import annotations

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.semantic_layer.memory_store import (
    build_recall_access,
    create_memory,
    InMemoryMemory,
    LanceDbMemory,
    load_recall_access,
    NullMemory,
    qualify_table_refs,
    RecallAccess,
    refs_from_sql,
    SqlAlchemyMemory,
)

# A common database pool id used by the ranking/dedup/decay tests (which pass no
# access, exercising ranking only — the access filter has its own tests below).
DB = 1


def _access(*tables: str) -> RecallAccess:
    """RecallAccess granting the given qualified ``schema.table`` keys."""

    frozen = frozenset(t.lower() for t in tables)
    schemas = frozenset(t.split(".")[0] for t in frozen if "." in t)
    return RecallAccess(
        accessible_tables=frozen, project_schemas=schemas, onboarded_tables=frozen
    )


def test_in_memory_store_and_recall() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="top names by births",
        semantic_sql="SELECT 1",
        native_sql="SELECT 1",
        database_id=DB,
    )
    recalled = memory.recall_examples("show top names", database_id=DB, k=3)
    assert len(recalled) == 1
    assert recalled[0].question == "top names by births"


def test_recall_is_database_isolated_not_owner_scoped() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="q", semantic_sql="x", native_sql="x", database_id=DB,
        created_by="owner-1",
    )
    # Another user on the SAME database shares the pool (no owner scoping).
    shared = memory.recall_examples("q", database_id=DB, k=3)
    assert len(shared) == 1
    # A different database is isolated.
    assert memory.recall_examples("q", database_id=999, k=3) == []


def test_in_memory_dedups_repeated_example() -> None:
    memory = InMemoryMemory()
    for _ in range(3):
        memory.store_confirmed(
            question="Top names",  # casing/whitespace variants normalize equal
            semantic_sql="SELECT 1",
            native_sql="SELECT 1 ",
            database_id=DB,
        )
    recalled = memory.recall_examples("top names", database_id=DB, k=9)
    assert len(recalled) == 1


def test_in_memory_keeps_distinct_sql_for_same_question() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="top names", semantic_sql="a", native_sql="SELECT 1", database_id=DB
    )
    memory.store_confirmed(
        question="top names", semantic_sql="b", native_sql="SELECT 2", database_id=DB
    )
    recalled = memory.recall_examples("top names", database_id=DB, k=9)
    assert len(recalled) == 2


def test_sqlalchemy_memory_dedups_repeated_example(tmp_path) -> None:
    config = AgentConfig(
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'agent.db'}",
    )
    run_migrations(config)
    memory = SqlAlchemyMemory(create_session_factory(create_engine_from_config(config)))
    for meta in ({"rows": 1}, {"rows": 2}):
        memory.store_confirmed(
            question="revenue by region",
            semantic_sql="SELECT region FROM sales",
            native_sql="SELECT region FROM public.sales",
            database_id=DB,
            result_meta=meta,
        )
    recalled = memory.recall_examples("revenue by region", database_id=DB, k=9)
    assert len(recalled) == 1
    # The refreshed row carries the latest result metadata.
    assert recalled[0].result_meta == {"rows": 2}


def test_in_memory_decay_evicts_oldest_past_cap() -> None:
    memory = InMemoryMemory(max_examples=2)
    for i in range(4):
        memory.store_confirmed(
            question=f"q{i}", semantic_sql=f"s{i}", native_sql=f"SELECT {i}",
            database_id=DB,
        )
    recalled = memory.recall_examples("q", database_id=DB, k=9)
    questions = {pair.question for pair in recalled}
    # Only the two most recent survive; the oldest two were evicted.
    assert questions == {"q2", "q3"}


def test_sqlalchemy_memory_decay_evicts_oldest(tmp_path) -> None:
    config = AgentConfig(
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'agent.db'}",
    )
    run_migrations(config)
    memory = SqlAlchemyMemory(
        create_session_factory(create_engine_from_config(config)), max_examples=2
    )
    for i in range(4):
        memory.store_confirmed(
            question=f"q{i}", semantic_sql=f"s{i}", native_sql=f"SELECT {i}",
            database_id=DB,
        )
    recalled = memory.recall_examples("q", database_id=DB, k=9)
    # Exactly the cap survives (which survive depends on created_at ordering,
    # which can tie under rapid inserts — so assert the count, not identity).
    assert len(recalled) == 2


def test_null_memory_is_inert() -> None:
    memory = NullMemory()
    memory.store_confirmed(
        question="q", semantic_sql="x", native_sql="x", database_id=DB
    )
    assert memory.recall_examples("q", database_id=DB, k=3) == []


def test_sqlalchemy_memory_persists_across_instances(tmp_path) -> None:
    config = AgentConfig(
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'agent.db'}",
    )
    run_migrations(config)
    engine = create_engine_from_config(config)

    writer = SqlAlchemyMemory(create_session_factory(engine))
    writer.store_confirmed(
        question="quarterly revenue by region",
        semantic_sql="SELECT region, revenue FROM sales",
        native_sql="SELECT region, revenue FROM public.sales",
        database_id=DB,
        referenced_tables=["public.sales"],
        referenced_schemas=["public"],
        result_meta={"rows": 4},
    )

    # New store on the same DB (simulating another worker) recalls the pair.
    reader = SqlAlchemyMemory(create_session_factory(create_engine_from_config(config)))
    recalled = reader.recall_examples("revenue by region", database_id=DB, k=3)
    assert len(recalled) == 1
    assert recalled[0].native_sql == "SELECT region, revenue FROM public.sales"
    assert recalled[0].referenced_tables == ["public.sales"]
    assert recalled[0].result_meta == {"rows": 4}


def test_create_memory_none_is_null() -> None:
    assert isinstance(create_memory(AgentConfig()), NullMemory)  # store=none default
    assert isinstance(
        create_memory(AgentConfig(wren_memory_learning_enabled=False)), NullMemory
    )


def test_create_memory_sqlalchemy_requires_db() -> None:
    with pytest.raises(ValueError, match="requires a database"):
        create_memory(AgentConfig(wren_memory_store="sqlalchemy"))


# --- F2: access-aware recall --------------------------------------------------


def test_refs_from_sql_qualifies_and_fails_closed() -> None:
    tables, schemas = refs_from_sql(
        "SELECT * FROM crm.customers c JOIN sales.deals d ON c.id = d.cid"
    )
    assert tables == ["crm.customers", "sales.deals"]
    assert schemas == ["crm", "sales"]
    # Parse failure -> empty (the recall filter then drops the pair).
    assert refs_from_sql("not sql ;;;") == ([], [])


def test_qualify_table_refs_lowercases_and_handles_unqualified() -> None:
    tables, schemas = qualify_table_refs([("CRM", "Customers"), (None, "Regions")])
    assert tables == ["crm.customers", "regions"]
    assert schemas == ["crm"]


def _store_pair(memory, question, refs, schemas, *, database_id=DB) -> None:
    memory.store_confirmed(
        question=question,
        semantic_sql="<semantic>",
        native_sql="<native>",
        database_id=database_id,
        referenced_tables=refs,
        referenced_schemas=schemas,
    )


def test_recall_drops_pairs_referencing_inaccessible_tables() -> None:
    memory = InMemoryMemory()
    _store_pair(memory, "customers", ["crm.customers"], ["crm"])
    _store_pair(memory, "secrets", ["hr.salaries"], ["hr"])
    # User can reach crm.customers but not hr.salaries.
    recalled = memory.recall_examples(
        "anything", database_id=DB, k=9, access=_access("crm.customers")
    )
    assert [p.question for p in recalled] == ["customers"]


def test_recall_fails_closed_when_references_unknown() -> None:
    memory = InMemoryMemory()
    # A legacy/unparseable pair with no referenced_tables is never surfaced.
    memory.store_confirmed(
        question="legacy", semantic_sql="x", native_sql="x", database_id=DB,
        referenced_tables=[], referenced_schemas=[],
    )
    recalled = memory.recall_examples(
        "legacy", database_id=DB, k=9, access=_access("crm.customers")
    )
    assert recalled == []


def test_recall_unqualified_ref_matches_accessible_name() -> None:
    memory = InMemoryMemory()
    _store_pair(memory, "orders", ["orders"], [])  # source SQL left schema implicit
    recalled = memory.recall_examples(
        "orders", database_id=DB, k=9, access=_access("public.orders")
    )
    assert [p.question for p in recalled] == ["orders"]


def test_recall_strips_semantic_sql_for_non_onboarded_pair() -> None:
    memory = InMemoryMemory()
    _store_pair(memory, "deals", ["sales.deals"], ["sales"])
    # User can access sales.deals (so it passes Stage A) but it is not onboarded
    # in the active project (onboarded set is crm.customers only).
    access = RecallAccess(
        accessible_tables=frozenset({"sales.deals"}),
        project_schemas=frozenset({"sales", "crm"}),
        onboarded_tables=frozenset({"crm.customers"}),
    )
    recalled = memory.recall_examples("deals", database_id=DB, k=9, access=access)
    assert len(recalled) == 1
    assert recalled[0].native_sql  # native kept
    assert recalled[0].semantic_sql == ""  # project-local semantic SQL stripped
    # Provenance breadcrumb so the explain UI can mark it learned-from-broader.
    assert recalled[0].result_meta.get("out_of_scope") is True


def test_recall_marks_fully_onboarded_pair_in_scope() -> None:
    memory = InMemoryMemory()
    _store_pair(memory, "deals", ["sales.deals"], ["sales"])
    access = RecallAccess(
        accessible_tables=frozenset({"sales.deals"}),
        project_schemas=frozenset({"sales"}),
        onboarded_tables=frozenset({"sales.deals"}),
    )
    recalled = memory.recall_examples("deals", database_id=DB, k=9, access=access)
    assert len(recalled) == 1
    assert recalled[0].semantic_sql  # in-scope keeps semantic SQL
    assert "out_of_scope" not in recalled[0].result_meta


def test_recall_down_ranks_foreign_schema_below_in_scope() -> None:
    memory = InMemoryMemory()
    _store_pair(memory, "in scope deals", ["sales.deals"], ["sales"])
    _store_pair(memory, "foreign region", ["geo.regions"], ["geo"])
    # Both reachable, but only "sales" is a project schema; geo is foreign -> sinks.
    access = RecallAccess(
        accessible_tables=frozenset({"sales.deals", "geo.regions"}),
        project_schemas=frozenset({"sales"}),
        onboarded_tables=frozenset({"sales.deals", "geo.regions"}),
    )
    recalled = memory.recall_examples("x", database_id=DB, k=2, access=access)
    assert recalled[0].question == "in scope deals"
    assert recalled[-1].question == "foreign region"


def test_build_recall_access_from_datasets() -> None:
    class _DS:
        def __init__(self, schema, table):
            self.schema_name = schema
            self.table_name = table

    access = build_recall_access([_DS("CRM", "Customers"), _DS(None, "Regions")])
    assert access.accessible_tables == frozenset({"crm.customers", "regions"})
    assert access.onboarded_tables == access.accessible_tables
    assert access.project_schemas == frozenset({"crm"})


class _FakeDatasetClient:
    """Per-user access-filtered dataset listing: each schema returns its tables."""

    def __init__(self, by_schema):
        self.by_schema = by_schema
        self.calls: list[str] = []

    def list_datasets(self, *, database_id, catalog_name, schema_name, limit):
        self.calls.append(schema_name)

        class _DS:
            def __init__(self, schema, table):
                self.schema_name = schema
                self.table_name = table

        return [_DS(schema_name, t) for t in self.by_schema.get(schema_name, [])]


def test_load_recall_access_unions_across_project_schemas() -> None:
    # R1: the access set must span every project schema (not the request's primary
    # schema), so a cross-schema golden/memory pair can pass the Stage-A filter.
    client = _FakeDatasetClient(
        {"core": ["drive_skus", "lines"], "ops": ["events"]},
    )
    access = load_recall_access(
        client,
        database_id=1,
        catalog_name=None,
        schema_names=["core", "ops"],
        limit=100,
    )
    assert access.accessible_tables == frozenset(
        {"core.drive_skus", "core.lines", "ops.events"}
    )
    assert access.project_schemas == frozenset({"core", "ops"})
    assert client.calls == ["core", "ops"]


def test_load_recall_access_excludes_inaccessible_schema() -> None:
    # A schema the user cannot reach returns no datasets -> contributes nothing,
    # so Stage A still fails closed for it (fail-closed preserved across schemas).
    client = _FakeDatasetClient({"core": ["drive_skus"]})  # 'ops' -> []
    access = load_recall_access(
        client,
        database_id=1,
        catalog_name=None,
        schema_names=["core", "ops"],
        limit=100,
    )
    assert access.accessible_tables == frozenset({"core.drive_skus"})


def test_load_recall_access_degrades_closed_on_error() -> None:
    class _Boom:
        def list_datasets(self, **_kwargs):
            raise RuntimeError("listing unavailable")

    access = load_recall_access(
        _Boom(), database_id=1, catalog_name=None, schema_names=["core"], limit=10
    )
    assert access.accessible_tables == frozenset()


# --- R3/R6: semantic recall via embeddings -----------------------------------


_REVENUE_WORDS = {"revenue", "sales", "total", "quarterly", "figures", "sum"}


class _TopicEmbedder:
    """Deterministic 2-D embedder: revenue topic -> [1,0], else [0,1]."""

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.embed_calls = 0
        self.embedded: list[list[str]] = []

    def is_available(self) -> bool:
        return self.available

    def dimensions(self) -> int:
        return 2

    def signature(self) -> str:
        return "topic:2"

    def embed(self, texts):
        self.embed_calls += 1
        self.embedded.append(list(texts))
        return [
            [1.0, 0.0]
            if _REVENUE_WORDS & {w.lower() for w in t.split()}
            else [0.0, 1.0]
            for t in texts
        ]


class _RaisingEmbedder(_TopicEmbedder):
    def embed(self, texts):
        raise RuntimeError("embedding backend down")


def _seed_two(memory) -> None:
    # "revenue" example shares no tokens with the query; "area" example shares one.
    memory.store_confirmed(
        question="quarterly revenue figures",
        semantic_sql="SA",
        native_sql="NA",
        database_id=DB,
    )
    memory.store_confirmed(
        question="names list area",
        semantic_sql="SB",
        native_sql="NB",
        database_id=DB,
    )


_QUERY = "total sales by area"


def test_semantic_recall_beats_keyword_overlap() -> None:
    # Keyword overlap would pick "names list area" (shares 'area'); the embedder
    # knows the query is about revenue and picks "quarterly revenue figures".
    embedder = _TopicEmbedder()
    memory = InMemoryMemory(embedder=embedder)
    _seed_two(memory)

    recalled = memory.recall_examples(_QUERY, database_id=DB, k=1)
    assert [pair.question for pair in recalled] == ["quarterly revenue figures"]

    keyword_only = InMemoryMemory()
    _seed_two(keyword_only)
    kw = keyword_only.recall_examples(_QUERY, database_id=DB, k=1)
    assert [pair.question for pair in kw] == ["names list area"]


def test_recall_degrades_to_keyword_when_embedder_unavailable() -> None:
    embedder = _TopicEmbedder(available=False)
    memory = InMemoryMemory(embedder=embedder)
    _seed_two(memory)

    recalled = memory.recall_examples(_QUERY, database_id=DB, k=1)
    assert [pair.question for pair in recalled] == ["names list area"]  # keyword
    assert embedder.embed_calls == 0  # never embedded


def test_recall_degrades_when_embedding_raises() -> None:
    embedder = _RaisingEmbedder()
    memory = InMemoryMemory(embedder=embedder)
    _seed_two(memory)

    recalled = memory.recall_examples(_QUERY, database_id=DB, k=1)
    assert [pair.question for pair in recalled] == ["names list area"]  # fallback


def test_create_memory_passes_embedder_for_semantic_recall(tmp_path) -> None:
    config = AgentConfig(
        wren_memory_store="sqlalchemy",
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'mem.db'}",
    )
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    embedder = _TopicEmbedder()

    memory = create_memory(config, session_factory=session_factory, embedder=embedder)
    _seed_two(memory)

    recalled = memory.recall_examples(_QUERY, database_id=DB, k=1)
    assert [pair.question for pair in recalled] == ["quarterly revenue figures"]


# --- C0.1: persistent `sql_pairs` vector cache --------------------------------


def _lancedb_config(tmp_path):
    return AgentConfig(
        wren_memory_store="lancedb",
        agent_database_url=f"sqlite+pysqlite:///{tmp_path / 'mem.db'}",
        wren_lancedb_path=str(tmp_path / "lancedb"),
    )


def test_create_memory_lancedb_wraps_with_cache(tmp_path) -> None:
    pytest.importorskip("lancedb")
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    memory = create_memory(
        config, session_factory=session_factory, embedder=_TopicEmbedder()
    )
    assert isinstance(memory, LanceDbMemory)


def test_create_memory_lancedb_falls_back_without_embedder(tmp_path) -> None:
    # store=lancedb but no usable embedder → the cache cannot vectorize, so we
    # keep the durable SQL store (degrade closed), not a half-built cache.
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    memory = create_memory(
        config,
        session_factory=session_factory,
        embedder=_TopicEmbedder(available=False),
    )
    assert isinstance(memory, SqlAlchemyMemory)


def test_lancedb_memory_recall_is_semantic_via_cache(tmp_path) -> None:
    pytest.importorskip("lancedb")
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    embedder = _TopicEmbedder()
    memory = create_memory(
        config, session_factory=session_factory, embedder=embedder
    )
    assert isinstance(memory, LanceDbMemory)
    _seed_two(memory)

    embedder.embedded = []  # ignore the store-time embeds; watch only recall
    recalled = memory.recall_examples(_QUERY, database_id=DB, k=1)
    # ANN pick is the revenue example (cosine), not the token-overlap "area" one.
    assert [pair.question for pair in recalled] == ["quarterly revenue figures"]
    # Recall embedded only the query, never the candidate set (the C0.1 win).
    assert embedder.embedded == [[_QUERY]]


def test_lancedb_memory_persists_cache_across_instances(tmp_path) -> None:
    pytest.importorskip("lancedb")
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    writer = create_memory(
        config, session_factory=session_factory, embedder=_TopicEmbedder()
    )
    _seed_two(writer)

    # Fresh store (another worker) over the same DB + LanceDB dir recalls via ANN.
    reader = create_memory(
        config,
        session_factory=create_session_factory(create_engine_from_config(config)),
        embedder=_TopicEmbedder(),
    )
    recalled = reader.recall_examples(_QUERY, database_id=DB, k=1)
    assert [pair.question for pair in recalled] == ["quarterly revenue figures"]


def test_lancedb_memory_recalls_uncached_pair_via_fill(tmp_path) -> None:
    # A pair stored straight through the inner SQL store (e.g. written before
    # lancedb mode was enabled) has no cache row; recall must still surface it by
    # filling from the SQL window after the cache-ranked hits — never drop it.
    pytest.importorskip("lancedb")
    config = _lancedb_config(tmp_path)
    run_migrations(config)
    session_factory = create_session_factory(create_engine_from_config(config))
    memory = create_memory(
        config, session_factory=session_factory, embedder=_TopicEmbedder()
    )
    assert isinstance(memory, LanceDbMemory)
    memory.store_confirmed(
        question="quarterly revenue figures",
        semantic_sql="SA",
        native_sql="NA",
        database_id=DB,
    )
    # Bypass the cache: write only to the inner SQL store.
    memory.inner.store_confirmed(
        question="names list area",
        semantic_sql="SB",
        native_sql="NB",
        database_id=DB,
    )

    recalled = memory.recall_examples(_QUERY, database_id=DB, k=2)
    questions = {pair.question for pair in recalled}
    assert questions == {"quarterly revenue figures", "names list area"}
