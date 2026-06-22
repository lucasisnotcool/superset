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
    manifest_to_schema_items,
)

_YAML = """
models:
  - name: deals
    table_reference:
      schema: sales
      table: deals
    columns:
      - name: amount
        type: DOUBLE
      - name: stage
        type: VARCHAR
  - name: customers
    table_reference:
      schema: sales
      table: customers
    columns:
      - name: region
        type: VARCHAR
relationships:
  - name: deal_customer
    models: [deals, customers]
    join_type: MANY_TO_ONE
"""


def _items():
    return manifest_to_schema_items(compile_manifest(yaml_contents=[_YAML]))


class _FakeEmbedder:
    """Deterministic embedder: vector keyed on whether 'region' appears."""

    def is_available(self) -> bool:
        return True

    def dimensions(self) -> int:
        return 2

    def embed(self, texts):
        return [[1.0, 0.0] if "region" in t.lower() else [0.0, 1.0] for t in texts]


def test_manifest_chunks_into_models_columns_relationships() -> None:
    items = _items()
    kinds = {item.kind for item in items}
    assert kinds == {"model", "column", "relationship"}
    assert any(i.kind == "model" and i.name == "deals" for i in items)
    assert any(i.kind == "relationship" and i.name == "deal_customer" for i in items)


def test_keyword_retriever_ranks_by_overlap() -> None:
    top = KeywordRetriever().retrieve("which region are customers in", _items(), k=1)
    assert top[0].name == "region" or top[0].model == "customers"


def test_embedding_retriever_uses_cosine() -> None:
    top = EmbeddingRetriever(_FakeEmbedder()).retrieve("region", _items(), k=2)
    # Region-related items (customers model + region column) embed to [1,0] and
    # rank above the deals/amount/stage items which embed to [0,1].
    assert any(item.name == "region" or item.model == "customers" for item in top)
    assert all(item.name not in {"amount", "stage"} for item in top)


def test_embedding_retriever_degrades_to_keyword_when_embedder_absent() -> None:
    retriever = EmbeddingRetriever(NullEmbedder())
    top = retriever.retrieve("stage", _items(), k=1)
    assert top  # falls back to keyword, still returns a result


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
