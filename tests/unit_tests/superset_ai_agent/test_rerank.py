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

"""LLM listwise rerank seam (B3, plan_sql_agent_doc_grounding_spec.md)."""

from __future__ import annotations

import json  # noqa: TID251 - tests cover the standalone agent JSON contract

from superset_ai_agent.llm.base import ModelResult
from superset_ai_agent.llm.rerank import llm_rerank


class _FixedModelClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def chat(self, messages, *, model=None, format_schema=None):
        self.calls += 1
        return ModelResult(content=self.content)


def test_llm_rerank_returns_validated_order() -> None:
    model = _FixedModelClient(json.dumps({"order": [2, 0, 9, 2]}))
    # index 9 is out of range and the duplicate 2 is dropped.
    order = llm_rerank(model, "q", ["a", "b", "c"], 3)
    assert order == [2, 0]


def test_llm_rerank_caps_to_k() -> None:
    model = _FixedModelClient(json.dumps({"order": [0, 1, 2]}))
    assert llm_rerank(model, "q", ["a", "b", "c"], 2) == [0, 1]


def test_llm_rerank_defers_on_bad_output() -> None:
    assert llm_rerank(_FixedModelClient("not json"), "q", ["a"], 1) is None
    assert (
        llm_rerank(_FixedModelClient(json.dumps({"order": "nope"})), "q", ["a"], 1)
        is None
    )
    assert (
        llm_rerank(_FixedModelClient(json.dumps({"order": [7]})), "q", ["a"], 1) is None
    )


def test_llm_rerank_skips_call_on_empty_candidates() -> None:
    model = _FixedModelClient(json.dumps({"order": [0]}))
    assert llm_rerank(model, "q", [], 3) is None
    assert llm_rerank(model, "q", ["a"], 0) is None
    assert model.calls == 0


def test_llm_rerank_defers_on_provider_error() -> None:
    class _Boom:
        def chat(self, *args, **kwargs):
            raise RuntimeError("provider down")

    assert llm_rerank(_Boom(), "q", ["a", "b"], 2) is None


def test_retrieve_document_context_applies_reranker() -> None:
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

    def _chunk(index: int, text: str) -> DocumentChunk:
        return DocumentChunk(
            id=chunk_id("doc-1", index),
            document_id="doc-1",
            chunk_index=index,
            text=text,
            checksum=chunk_checksum(text),
            char_start=0,
            char_end=len(text),
        )

    class _Store:
        def list_project_documents(self, project_id, *, owner_id="local"):
            return [SimpleNamespace(id="doc-1", filename="a.md")]

        def list_project_chunks(self, project_id, *, owner_id="local"):
            return [
                _chunk(0, "yield notes alpha"),
                _chunk(1, "yield notes beta"),
                _chunk(2, "yield definition: good units over total units"),
            ]

    calls: list[tuple[str, int, int]] = []

    def reranker(question: str, texts: list[str], k: int) -> list[int] | None:
        calls.append((question, len(texts), k))
        return [len(texts) - 1]  # promote the last first-stage candidate

    result = retrieve_document_context(
        question="yield",
        project_id="p1",
        owner_id="local",
        store=_Store(),
        index=DocumentChunkIndex(None),
        k=2,
        max_chars=10_000,
        reranker=reranker,
    )
    assert result is not None
    assert result["reranked"] is True
    assert len(result["passages"]) == 1
    assert calls
    assert calls[0][2] == 2  # asked for top-k of the over-fetched pool


def test_retrieve_document_context_keeps_order_when_reranker_defers() -> None:
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

    chunk = DocumentChunk(
        id=chunk_id("doc-1", 0),
        document_id="doc-1",
        chunk_index=0,
        text="yield rate definition",
        checksum=chunk_checksum("yield rate definition"),
        char_start=0,
        char_end=21,
    )

    class _Store:
        def list_project_documents(self, project_id, *, owner_id="local"):
            return [SimpleNamespace(id="doc-1", filename="a.md")]

        def list_project_chunks(self, project_id, *, owner_id="local"):
            return [chunk]

    result = retrieve_document_context(
        question="yield rate",
        project_id="p1",
        owner_id="local",
        store=_Store(),
        index=DocumentChunkIndex(None),
        k=2,
        max_chars=10_000,
        reranker=lambda q, t, k: None,  # defers
    )
    assert result is not None
    assert result["reranked"] is False
    assert len(result["passages"]) == 1
