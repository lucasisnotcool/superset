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

"""SQL-agent document grounding channel (plan_sql_agent_doc_grounding_spec.md A1)."""

from __future__ import annotations

import json  # noqa: TID251 - tests cover the standalone agent JSON contract
from types import SimpleNamespace

from superset_ai_agent.semantic_layer.document_chunks import (
    chunk_checksum,
    chunk_id,
    DocumentChunk,
)
from superset_ai_agent.semantic_layer.document_retriever import (
    DocumentChunkIndex,
    retrieve_document_context,
)


class _FakeDocStore:
    """Two-method store fake matching the DocumentContextStore protocol."""

    def __init__(self, documents, chunks) -> None:
        self.documents = documents
        self.chunks = chunks
        self.calls: list[str] = []

    def list_project_documents(self, project_id, *, owner_id="local"):
        self.calls.append(f"docs:{project_id}:{owner_id}")
        return self.documents

    def list_project_chunks(self, project_id, *, owner_id="local"):
        self.calls.append(f"chunks:{project_id}:{owner_id}")
        return self.chunks


def _chunk(document_id: str, index: int, text: str) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id(document_id, index),
        document_id=document_id,
        chunk_index=index,
        text=text,
        checksum=chunk_checksum(text),
        char_start=0,
        char_end=len(text),
    )


def _store() -> _FakeDocStore:
    return _FakeDocStore(
        documents=[SimpleNamespace(id="doc-1", filename="bi_glossary.md")],
        chunks=[
            _chunk("doc-1", 0, "Diner-Week starts on Wednesday for fiscal reporting."),
            _chunk("doc-1", 1, "Yield rate is good units divided by total units."),
            _chunk("doc-1", 2, "Unrelated section about office parking."),
        ],
    )


def test_retrieve_document_context_returns_ranked_passages() -> None:
    result = retrieve_document_context(
        question="how is yield rate calculated?",
        project_id="p1",
        owner_id="local",
        store=_store(),
        index=DocumentChunkIndex(None),  # keyword fallback
        k=2,
        max_chars=10_000,
    )
    assert result is not None
    assert result["retriever"] == "keyword"
    assert result["truncated"] is False
    assert result["document_ids"] == ["doc-1"]
    texts = [p["text"] for p in result["passages"]]
    assert any("Yield rate" in t for t in texts)
    # Filenames resolved for the explain UI provenance.
    assert result["passages"][0]["filename"] == "bi_glossary.md"


def test_retrieve_document_context_trims_to_char_budget() -> None:
    store = _FakeDocStore(
        documents=[SimpleNamespace(id="doc-1", filename="a.md")],
        chunks=[
            _chunk("doc-1", 0, "yield " + "x" * 200),
            _chunk("doc-1", 1, "yield " + "y" * 200),
        ],
    )
    result = retrieve_document_context(
        question="yield",
        project_id="p1",
        owner_id="local",
        store=store,
        index=DocumentChunkIndex(None),
        k=5,
        max_chars=250,
    )
    assert result is not None
    # Only the first passage fits; the second is dropped, flagged loudly.
    assert len(result["passages"]) == 1
    assert result["truncated"] is True


def test_retrieve_document_context_cuts_oversized_first_passage() -> None:
    store = _FakeDocStore(
        documents=[],
        chunks=[_chunk("doc-1", 0, "yield " + "z" * 500)],
    )
    result = retrieve_document_context(
        question="yield",
        project_id="p1",
        owner_id="local",
        store=store,
        index=DocumentChunkIndex(None),
        k=5,
        max_chars=100,
    )
    # A partial first passage beats none (budget still respected).
    assert result is not None
    assert len(result["passages"]) == 1
    assert len(result["passages"][0]["text"]) == 100
    assert result["truncated"] is True


def test_retrieve_document_context_degrades_closed() -> None:
    store = _store()
    index = DocumentChunkIndex(None)
    common = {
        "question": "yield",
        "owner_id": "local",
        "store": store,
        "index": index,
        "k": 3,
        "max_chars": 1000,
    }
    assert retrieve_document_context(project_id=None, **common) is None
    assert (
        retrieve_document_context(project_id="p1", **{**common, "store": None}) is None
    )
    assert (
        retrieve_document_context(project_id="p1", **{**common, "index": None}) is None
    )
    assert retrieve_document_context(project_id="p1", **{**common, "k": 0}) is None
    assert (
        retrieve_document_context(project_id="p1", **{**common, "max_chars": 0}) is None
    )
    # Empty corpus -> None (channel inert, no trace noise).
    empty = _FakeDocStore(documents=[], chunks=[])
    assert (
        retrieve_document_context(project_id="p1", **{**common, "store": empty}) is None
    )


def test_retrieve_document_context_survives_store_errors() -> None:
    class _Boom:
        def list_project_documents(self, project_id, *, owner_id="local"):
            raise RuntimeError("db down")

        def list_project_chunks(self, project_id, *, owner_id="local"):
            raise RuntimeError("db down")

    assert (
        retrieve_document_context(
            question="yield",
            project_id="p1",
            owner_id="local",
            store=_Boom(),
            index=DocumentChunkIndex(None),
            k=3,
            max_chars=1000,
        )
        is None
    )


class _FakeVectorCache:
    """Deterministic embedding-backed cache (mirrors test_document_retriever)."""

    def __init__(self, ordered_ids: list[str]) -> None:
        self.ordered_ids = ordered_ids

    def is_available(self) -> bool:
        return True

    def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool:
        return True

    def remove(self, *, scope_key: str, row_id: str) -> bool:
        return True

    def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None:
        return self.ordered_ids[:k]


def test_retrieve_document_context_reports_embedding_retriever() -> None:
    store = _store()
    cache = _FakeVectorCache([chunk.id for chunk in store.chunks])
    result = retrieve_document_context(
        question="anything",
        project_id="p1",
        owner_id="local",
        store=store,
        index=DocumentChunkIndex(cache),
        k=2,
        max_chars=10_000,
    )
    assert result is not None
    assert result["retriever"] == "embedding"
    assert len(result["passages"]) == 2


# --- graph integration: the channel feeds the prompt, trace, and explain UI ----


def _doc_grounded_graph(tmp_path, doc_store):
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.graph import TextToSqlGraph
    from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
    from superset_ai_agent.semantic_layer.projects import InMemorySemanticProjectStore
    from superset_ai_agent.semantic_layer.schemas import (
        MdlFileCreateRequest,
        MdlFileUpdateRequest,
        SemanticProjectResolveRequest,
    )
    from tests.unit_tests.superset_ai_agent.test_graph import (
        FakeContextProvider,
        FakeModelClient,
        FakeSupersetClient,
        FakeWrenClient,
    )

    project_store = InMemorySemanticProjectStore()
    mdl_store = InMemoryMdlFileStore()
    project = project_store.resolve(
        SemanticProjectResolveRequest(
            database_id=1,
            database_label="Examples",
            schema_name="main",
        ),
        owner_id="analyst",
    )
    file = mdl_store.create(
        project.id,
        MdlFileCreateRequest(
            path="models/birth_names.json",
            content=json.dumps({"models": [{"name": "birth_names"}]}),
        ),
        owner_id="analyst",
    )
    mdl_store.update(file.id, MdlFileUpdateRequest(status="active"), owner_id="analyst")
    model_client = FakeModelClient("SELECT name FROM birth_names")
    graph = TextToSqlGraph(
        config=AgentConfig(agent_storage_dir=str(tmp_path)),
        model_client=model_client,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        wren_client=FakeWrenClient(),
        semantic_project_store=project_store,
        mdl_file_store=mdl_store,
        semantic_layer_store=doc_store,
        document_index=DocumentChunkIndex(None),
    )
    return graph, model_client, project


def test_graph_injects_document_context_into_prompt_and_trace(tmp_path) -> None:
    from superset_ai_agent.schemas import AgentQueryRequest

    doc_store = _FakeDocStore(
        documents=[SimpleNamespace(id="doc-1", filename="bi_glossary.md")],
        chunks=[
            _chunk("doc-1", 0, "Total births are summed per name from birth_names."),
        ],
    )
    graph, model_client, project = _doc_grounded_graph(tmp_path, doc_store)

    response = graph.run(
        AgentQueryRequest(
            question="show total births by name",
            database_id=1,
            schema_name="main",
        ),
        owner_id="analyst",
    )

    # Trace + explain timeline carry the RAG step with its typed detail.
    doc_events = [e for e in response.trace if e.step == "load_document_context"]
    assert len(doc_events) == 1
    assert doc_events[0].details["passage_count"] == 1
    assert doc_events[0].details["retriever"] == "keyword"
    doc_steps = [s for s in response.timeline if s.kind == "load_document_context"]
    assert len(doc_steps) == 1
    assert doc_steps[0].detail is not None
    assert doc_steps[0].detail.kind == "document_context"
    assert doc_steps[0].detail.passage_count == 1
    assert doc_steps[0].detail.passages[0].filename == "bi_glossary.md"
    # Document provenance is stamped onto the context artifact.
    assert response.wren_context is not None
    assert response.wren_context.document_ids == ["doc-1"]
    # The passage text reaches the SQL prompt payload.
    prompt_text = "\n".join(
        message.content for messages in model_client.messages for message in messages
    )
    assert "Total births are summed per name" in prompt_text
    assert '"document_context"' in prompt_text


def test_graph_document_channel_inert_without_project(tmp_path) -> None:
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.graph import TextToSqlGraph
    from superset_ai_agent.schemas import AgentQueryRequest
    from tests.unit_tests.superset_ai_agent.test_graph import (
        FakeContextProvider,
        FakeModelClient,
        FakeSupersetClient,
    )

    doc_store = _store()
    graph = TextToSqlGraph(
        config=AgentConfig(agent_storage_dir=str(tmp_path)),
        model_client=FakeModelClient("SELECT 1"),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        semantic_layer_store=doc_store,
        document_index=DocumentChunkIndex(None),
    )

    response = graph.run(
        AgentQueryRequest(question="anything", database_id=1, schema_name="main")
    )

    # No resolved project -> the channel is inert: no trace step, no store scan.
    assert all(e.step != "load_document_context" for e in response.trace)
    assert doc_store.calls == []


def test_graph_document_channel_disabled_by_flag(tmp_path) -> None:
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.schemas import AgentQueryRequest

    doc_store = _FakeDocStore(
        documents=[SimpleNamespace(id="doc-1", filename="bi_glossary.md")],
        chunks=[_chunk("doc-1", 0, "Total births are summed per name.")],
    )
    graph, _, _ = _doc_grounded_graph(tmp_path, doc_store)
    graph.config = AgentConfig(
        agent_storage_dir=graph.config.agent_storage_dir,
        wren_sql_doc_context_enabled=False,
    )

    response = graph.run(
        AgentQueryRequest(
            question="show total births", database_id=1, schema_name="main"
        ),
        owner_id="analyst",
    )

    assert all(e.step != "load_document_context" for e in response.trace)
    assert doc_store.calls == []
