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

"""Phase 2.1/2.2 — Embedder seam + Retriever seam."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
import os

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.embeddings import (
    create_embedder,
    NullEmbedder,
    OpenAiEmbedder,
)
from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    EmbeddingRetriever,
    KeywordRetriever,
    LanceDbRetriever,
    manifest_to_schema_items,
)

_MDL = json.dumps(
    {
        "models": [
            {
                "name": "deals",
                "tableReference": {"schema": "sales", "table": "deals"},
                "columns": [
                    {"name": "amount", "type": "DOUBLE"},
                    {"name": "stage", "type": "VARCHAR"},
                ],
            },
            {
                "name": "customers",
                "tableReference": {"schema": "sales", "table": "customers"},
                "columns": [{"name": "region", "type": "VARCHAR"}],
            },
        ],
        "relationships": [
            {
                "name": "deal_customer",
                "models": ["deals", "customers"],
                "joinType": "MANY_TO_ONE",
            }
        ],
    }
)


def _items():
    return manifest_to_schema_items(compile_manifest(json_contents=[_MDL]))


class _FakeEmbedder:
    """Deterministic embedder: vector keyed on whether 'region' appears."""

    def __init__(self) -> None:
        self.calls = 0
        self.embedded_texts: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def dimensions(self) -> int:
        return 2

    def signature(self) -> str:
        return "fake:2"

    def embed(self, texts):
        self.calls += 1
        self.embedded_texts.append(list(texts))
        return [[1.0, 0.0] if "region" in t.lower() else [0.0, 1.0] for t in texts]


def _rank(retriever, question, items, k, *, scope="s", checksum="c"):
    retriever.index(items, scope_key=scope, checksum=checksum)
    return retriever.retrieve(question, scope_key=scope, checksum=checksum, k=k)


def test_manifest_chunks_into_models_columns_relationships() -> None:
    items = _items()
    kinds = {item.kind for item in items}
    assert kinds == {"model", "column", "relationship"}
    assert any(i.kind == "model" and i.name == "deals" for i in items)
    assert any(i.kind == "relationship" and i.name == "deal_customer" for i in items)


def test_semantic_view_is_indexed_with_its_description_native_excluded() -> None:
    # A semantic view becomes a retrievable chunk whose text carries the
    # description (the recall key); a native (dialect) view is not advertised
    # because it is not in the engine manifest.
    mdl = json.dumps(
        {
            "models": [
                {
                    "name": "orders",
                    "tableReference": {"schema": "public", "table": "orders"},
                    "columns": [{"name": "amount", "type": "DOUBLE"}],
                }
            ],
            "views": [
                {
                    "name": "warm_line_output",
                    "statement": "SELECT amount FROM orders",
                    "properties": {"description": "Warm line output by family"},
                },
                {
                    "name": "legacy_rollup",
                    "statement": "SELECT * FROM public.raw_rollup",
                    "dialect": "postgres",
                },
            ],
        }
    )
    items = manifest_to_schema_items(compile_manifest(json_contents=[mdl]))
    view_items = [i for i in items if i.kind == "view"]
    assert [i.name for i in view_items] == ["warm_line_output"]
    assert "Warm line output by family" in view_items[0].text


def test_chunk_text_carries_enriched_semantics_and_join_condition() -> None:
    # CR9: enriched description/alias/synonyms and the relationship condition must be
    # in the chunk *text* — that is what a colloquial question embeds/matches against.
    mdl = json.dumps(
        {
            "models": [
                {
                    "name": "drives",
                    "tableReference": {"schema": "seagate", "table": "drives"},
                    "description": "One finished drive unit",
                    "columns": [
                        {
                            "name": "drive_unit",
                            "type": "VARCHAR",
                            "description": "A single drive; floor term patty",
                            "properties": {
                                "displayName": "Drive Unit",
                                "alias": "patty",
                            },
                        }
                    ],
                }
            ],
            "relationships": [
                {
                    "name": "wo_drives",
                    "models": ["work_orders", "drives"],
                    "joinType": "ONE_TO_MANY",
                    "condition": "work_orders.id = drives.work_order_id",
                }
            ],
        }
    )
    items = manifest_to_schema_items(compile_manifest(json_contents=[mdl]))
    column = next(i for i in items if i.kind == "column" and i.name == "drive_unit")
    assert "patty" in column.text  # alias/synonym retrievable
    assert "floor term patty" in column.text  # description retrievable
    model = next(i for i in items if i.kind == "model" and i.name == "drives")
    assert "One finished drive unit" in model.text
    relationship = next(i for i in items if i.kind == "relationship")
    assert "work_orders.id = drives.work_order_id" in relationship.text


def test_keyword_retriever_ranks_by_overlap() -> None:
    top = _rank(KeywordRetriever(), "which region are customers in", _items(), 1)
    assert top[0].name == "region" or top[0].model == "customers"


def test_keyword_rank_attaches_normalized_score() -> None:
    # A3: the top item carries a 0-1 score (token overlap / query token count).
    top = _rank(KeywordRetriever(), "which region are customers in", _items(), 1)
    assert top[0].score is not None
    assert 0.0 < top[0].score <= 1.0


def test_embedding_rank_attaches_cosine_score() -> None:
    top = _rank(EmbeddingRetriever(_FakeEmbedder()), "region", _items(), 1)
    assert top[0].score is not None
    assert 0.0 <= top[0].score <= 1.0


def test_embedding_retriever_uses_cosine() -> None:
    top = _rank(EmbeddingRetriever(_FakeEmbedder()), "region", _items(), 2)
    # Region-related items (customers model + region column) embed to [1,0] and
    # rank above the deals/amount/stage items which embed to [0,1].
    assert any(item.name == "region" or item.model == "customers" for item in top)
    assert all(item.name not in {"amount", "stage"} for item in top)


def test_embedding_retriever_degrades_to_keyword_when_embedder_absent() -> None:
    retriever = EmbeddingRetriever(NullEmbedder())
    top = _rank(retriever, "stage", _items(), 1)
    assert top  # falls back to keyword, still returns a result
    assert retriever.effective_name("s") == "keyword"


def test_index_built_once_then_only_question_is_embedded() -> None:
    # R1/G1: item vectors are computed once at index time; warm queries embed
    # only the question (1 embed call), independent of schema width.
    embedder = _FakeEmbedder()
    retriever = EmbeddingRetriever(embedder)
    items = _items()
    retriever.index(items, scope_key="s", checksum="c1")
    assert embedder.calls == 1  # the item batch
    retriever.retrieve("region", scope_key="s", checksum="c1", k=2)
    retriever.retrieve("amount", scope_key="s", checksum="c1", k=2)
    # Two queries -> two question embeds; items were NOT re-embedded.
    assert embedder.calls == 3
    assert all(len(batch) == 1 for batch in embedder.embedded_texts[1:])


def test_reindex_on_checksum_change() -> None:
    embedder = _FakeEmbedder()
    retriever = EmbeddingRetriever(embedder)
    items = _items()
    retriever.index(items, scope_key="s", checksum="c1")
    assert retriever.has_index("s", "c1") is True
    assert retriever.has_index("s", "c2") is False
    retriever.index(items, scope_key="s", checksum="c2")  # content changed
    assert embedder.calls == 2  # re-embedded the items for the new checksum
    # A stale checksum no longer retrieves.
    assert retriever.retrieve("region", scope_key="s", checksum="c1", k=1) == []


def test_effective_name_reflects_embedding_when_available() -> None:
    retriever = EmbeddingRetriever(_FakeEmbedder())
    retriever.index(_items(), scope_key="s", checksum="c")
    assert retriever.effective_name("s") == "embedding"


class _SigEmbedder(_FakeEmbedder):
    def __init__(self, sig: str) -> None:
        super().__init__()
        self._sig = sig

    def signature(self) -> str:
        return self._sig


def test_index_reindexes_when_embedder_signature_changes() -> None:
    # R3/R-RET4: same MDL content but a different embedder model/dimension must
    # re-index, never serve vectors built by a different model.
    items = _items()
    retriever = EmbeddingRetriever(_SigEmbedder("openai:m1:1536"))
    retriever.index(items, scope_key="s", checksum="content-v1")
    assert retriever.has_index("s", "content-v1") is True

    swapped = EmbeddingRetriever(_SigEmbedder("openai:m2:3072"))
    swapped.index(items, scope_key="s", checksum="content-v1")
    # Different signature → the v1 content checksum is treated as a new version.
    assert swapped.effective_checksum("content-v1") != retriever.effective_checksum(
        "content-v1"
    )


def test_lru_index_evicts_least_recently_used_scope() -> None:
    # C4: the in-process index is bounded; past the cap the LRU scope is evicted.
    retriever = KeywordRetriever(max_scopes=2)
    items = _items()
    for scope in ("a", "b"):
        retriever.index(items, scope_key=scope, checksum="c")
    retriever.retrieve("region", scope_key="a", checksum="c", k=1)  # touch a
    retriever.index(items, scope_key="c", checksum="c")  # evicts b (LRU)
    assert retriever.has_index("a", "c") is True
    assert retriever.has_index("c", "c") is True
    assert retriever.has_index("b", "c") is False


def test_create_retriever_passes_cache_bound() -> None:
    retriever = create_retriever(AgentConfig(wren_retriever_cache_scopes=7))
    assert retriever._index.max_scopes == 7


def test_effective_vector_index_reports_memory_and_fallback() -> None:
    from superset_ai_agent.semantic_layer.schema_retriever import (
        effective_vector_index,
    )

    # Default config → plain in-process memory index.
    cfg_mem = AgentConfig()
    assert effective_vector_index(cfg_mem, KeywordRetriever()) == "memory"

    # Configured lancedb but the retriever could not connect (lancedb may be
    # absent here) → the loud "memory_fallback" signal.
    cfg_lance = AgentConfig(wren_retriever="embedding", wren_vector_index="lancedb")
    retriever = create_retriever(cfg_lance, _FakeEmbedder())
    mode = effective_vector_index(cfg_lance, retriever)
    assert mode in {"lancedb", "memory_fallback"}
    assert mode == ("lancedb" if retriever.is_persistent() else "memory_fallback")


def test_create_retriever_defaults_to_keyword() -> None:
    assert isinstance(create_retriever(AgentConfig()), KeywordRetriever)


def test_create_retriever_embedding_needs_available_embedder() -> None:
    # Embedding requested but no embedder -> keyword.
    assert isinstance(
        create_retriever(AgentConfig(wren_retriever="embedding")), KeywordRetriever
    )
    assert isinstance(
        create_retriever(AgentConfig(wren_retriever="embedding"), _FakeEmbedder()),
        EmbeddingRetriever,
    )


def test_create_retriever_lancedb_selected_when_configured() -> None:
    retriever = create_retriever(
        AgentConfig(wren_retriever="embedding", wren_vector_index="lancedb"),
        _FakeEmbedder(),
    )
    assert isinstance(retriever, LanceDbRetriever)


def _lancedb_installed() -> bool:
    try:
        import lancedb  # noqa: F401

        return True
    except Exception:  # pylint: disable=broad-except
        return False


@pytest.mark.skipif(
    _lancedb_installed(), reason="lancedb installed; this asserts the absent path"
)
def test_lancedb_retriever_degrades_to_in_process_when_lancedb_absent(
    tmp_path,
) -> None:
    # lancedb is an optional native dep; absent → _db is None and the retriever
    # behaves exactly like the in-process embedding index (never crashes).
    retriever = LanceDbRetriever(_FakeEmbedder(), path=str(tmp_path / "lancedb"))
    assert retriever._db is None
    items = _items()
    retriever.index(items, scope_key="s", checksum="c1")
    top = retriever.retrieve("region", scope_key="s", checksum="c1", k=2)
    assert any(item.name == "region" or item.model == "customers" for item in top)
    assert retriever.effective_name("s") == "embedding"


def test_lancedb_round_trip_persists_across_instances(tmp_path) -> None:
    pytest.importorskip("lancedb")
    embedder = _FakeEmbedder()
    writer = LanceDbRetriever(embedder, path=str(tmp_path / "lancedb"))
    writer.index(_items(), scope_key="proj", checksum="v1")

    # A fresh retriever on the same path finds the persisted index (no re-index).
    reader = LanceDbRetriever(_FakeEmbedder(), path=str(tmp_path / "lancedb"))
    assert reader.has_index("proj", "v1") is True
    top = reader.retrieve("region", scope_key="proj", checksum="v1", k=2)
    assert any(item.name == "region" or item.model == "customers" for item in top)
    assert reader.effective_name("proj") == "embedding"


def test_lancedb_cold_retrieve_uses_native_ann_search_not_rehydrate(tmp_path) -> None:
    # C2 / R-RET-B: the cold path searches LanceDB natively and does NOT load the
    # whole corpus into the in-process index.
    pytest.importorskip("lancedb")
    writer = LanceDbRetriever(_FakeEmbedder(), path=str(tmp_path / "lancedb"))
    writer.index(_items(), scope_key="proj", checksum="v1")

    reader = LanceDbRetriever(_FakeEmbedder(), path=str(tmp_path / "lancedb"))
    top = reader.retrieve("region", scope_key="proj", checksum="v1", k=1)
    assert top
    assert top[0].name == "region" or top[0].model == "customers"
    # Native search path left the in-process index empty (no full rehydrate).
    assert reader._mem.has_index("proj", "v1") is False


def test_create_embedder_null_without_provider() -> None:
    assert isinstance(create_embedder(AgentConfig()), NullEmbedder)


def test_create_embedder_openai_reuses_shared_key() -> None:
    embedder = create_embedder(
        AgentConfig(embedder_provider="openai", openai_api_key="sk-test")
    )
    assert isinstance(embedder, OpenAiEmbedder)
    assert embedder.is_available() is True
    assert embedder.dimensions() == 1536


def test_create_embedder_openai_without_key_is_null() -> None:
    assert isinstance(
        create_embedder(AgentConfig(embedder_provider="openai")), NullEmbedder
    )


def test_create_embedder_ollama() -> None:
    from superset_ai_agent.llm.embeddings import OllamaEmbedder

    embedder = create_embedder(
        AgentConfig(embedder_provider="ollama", embedder_model="nomic-embed-text")
    )
    assert isinstance(embedder, OllamaEmbedder)
    assert embedder.is_available() is True
    assert embedder.signature() == "ollama:nomic-embed-text:1536"


class _CapturingOpenAiClient:
    """Captures kwargs/inputs and returns one vector per input (contract double)."""

    def __init__(self, dim: int = 2) -> None:
        self.calls: list[dict] = []
        self.dim = dim
        self.embeddings = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        batch = kwargs["input"]

        class _Resp:
            data = [
                type("E", (), {"embedding": [0.0] * 1})()  # placeholder, set below
                for _ in batch
            ]

        resp = _Resp()
        resp.data = [
            type("E", (), {"embedding": [float(i)] * self.dim})()
            for i, _ in enumerate(batch)
        ]
        return resp

    @property
    def kwargs(self) -> dict:
        return self.calls[-1] if self.calls else {}


def test_openai_embedder_sends_dimensions_for_v3_models() -> None:
    embedder = OpenAiEmbedder(
        api_key="sk", base_url="x", model="text-embedding-3-small", dimensions=512
    )
    client = _CapturingOpenAiClient()
    embedder._client = client  # inject; skip real network
    embedder.embed(["hello"])
    assert client.kwargs.get("dimensions") == 512


def test_openai_embedder_omits_dimensions_for_ada() -> None:
    embedder = OpenAiEmbedder(
        api_key="sk", base_url="x", model="text-embedding-ada-002", dimensions=1536
    )
    client = _CapturingOpenAiClient()
    embedder._client = client
    embedder.embed(["hello"])
    assert "dimensions" not in client.kwargs


def test_ollama_embedder_omits_dimensions_in_request() -> None:
    from superset_ai_agent.llm.embeddings import OllamaEmbedder

    embedder = OllamaEmbedder(
        api_key="ollama", base_url="x", model="nomic-embed-text", dimensions=768
    )
    client = _CapturingOpenAiClient()
    embedder._client = client
    embedder.embed(["hello"])
    assert "dimensions" not in client.kwargs


def test_openai_embedder_parses_one_vector_per_input_across_batches() -> None:
    embedder = OpenAiEmbedder(
        api_key="sk",
        base_url="x",
        model="text-embedding-3-small",
        dimensions=2,
        batch_size=2,
    )
    client = _CapturingOpenAiClient(dim=2)
    embedder._client = client
    vectors = embedder.embed(["a", "b", "c"])
    # 3 inputs over batch_size 2 → 2 API calls, one vector per input.
    assert len(client.calls) == 2
    assert len(vectors) == 3
    assert all(len(v) == 2 for v in vectors)


def test_openai_embedder_empty_input_makes_no_call() -> None:
    embedder = OpenAiEmbedder(
        api_key="sk", base_url="x", model="text-embedding-3-small", dimensions=2
    )
    client = _CapturingOpenAiClient()
    embedder._client = client
    assert embedder.embed([]) == []
    assert client.calls == []


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="live smoke; set OPENAI_API_KEY to run",
)
def test_openai_embedder_live_smoke() -> None:  # pragma: no cover - network
    embedder = create_embedder(
        AgentConfig(embedder_provider="openai", embedder_dimensions=256)
    )
    vectors = embedder.embed(["quarterly revenue by region"])
    assert len(vectors) == 1
    assert len(vectors[0]) == 256


# --- E6: eager re-index on activation -----------------------------------------


from superset_ai_agent.semantic_layer.schema_retriever import (  # noqa: E402
    ensure_project_indexed,
    reindex_project_mdl,
)
from superset_ai_agent.semantic_layer.schemas import MdlFile  # noqa: E402


def _active_file(content: str = _MDL) -> MdlFile:
    return MdlFile(
        project_id="p1",
        path="models/m.json",
        filename="m.json",
        content=content,
        checksum="c",
        status="active",
    )


class _FakeMdlStore:
    def __init__(self, files):
        self._files = files

    def list(self, project_id, *, owner_id="local"):
        return list(self._files)


class _RaisingMdlStore:
    def list(self, project_id, *, owner_id="local"):
        raise RuntimeError("db down")


def test_ensure_project_indexed_builds_index() -> None:
    retriever = KeywordRetriever()
    store = _FakeMdlStore([_active_file()])

    result = ensure_project_indexed(
        retriever=retriever, project_id="p1", owner_id="u1", mdl_file_store=store
    )

    assert result is not None
    scope_key, checksum = result
    assert scope_key == "u1:p1"
    assert retriever.has_index(scope_key, checksum)  # eagerly indexed


def test_ensure_project_indexed_none_without_active_files() -> None:
    store = _FakeMdlStore([])  # no active MDL
    assert (
        ensure_project_indexed(
            retriever=KeywordRetriever(),
            project_id="p1",
            owner_id="u1",
            mdl_file_store=store,
        )
        is None
    )


def test_reindex_project_mdl_eagerly_indexes_without_a_query() -> None:
    # E6: activation primes the index so retrieval reflects the new MDL with no
    # first-query build cost.
    retriever = KeywordRetriever()
    store = _FakeMdlStore([_active_file()])

    assert (
        reindex_project_mdl(
            retriever=retriever, project_id="p1", owner_id="u1", mdl_file_store=store
        )
        is True
    )
    # Index is present even though retrieve() was never called.
    checksum = retriever._index.get("u1:p1").checksum  # type: ignore[union-attr]
    assert retriever.has_index("u1:p1", checksum)


def test_reindex_project_mdl_swallows_errors() -> None:
    # Activation must never fail because re-indexing hiccupped.
    assert (
        reindex_project_mdl(
            retriever=KeywordRetriever(),
            project_id="p1",
            owner_id="u1",
            mdl_file_store=_RaisingMdlStore(),
        )
        is False
    )
