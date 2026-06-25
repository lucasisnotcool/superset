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

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
from superset_ai_agent.semantic_layer.document_chunks import build_chunk_records
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.schemas import SemanticDocument

_TEXT = (
    "Revenue is grouped by sales region.\n\n"
    "Customer churn is driven by support latency.\n\n"
    "Revenue is grouped by sales region.\n"  # exact dup of section 0
)


def _store_with_document(owner_id="u1", project_id="p1"):
    store = InMemorySemanticLayerStore()
    document = store.save_document(
        SemanticDocument(
            project_id=project_id,
            filename="glossary.md",
            content_type="text/markdown",
            size_bytes=len(_TEXT),
            scope=ConversationScope(database_id=1, dataset_ids=[]),
            checksum="abc",
            storage_uri="mem://glossary",
            status="extracted",
            summary="Business glossary.",
        ),
        owner_id=owner_id,
    )
    store.save_chunks(
        document.id,
        build_chunk_records(document.id, _TEXT),
        owner_id=owner_id,
        project_id=project_id,
    )
    return store, document


def _toolset(store=None, **kwargs) -> MdlToolset:
    return MdlToolset(
        [],
        document_store=store,
        project_id=kwargs.get("project_id", "p1" if store else None),
        owner_id="u1",
    )


def test_document_tools_are_advertised() -> None:
    names = {spec.name for spec in MdlToolset([]).specs()}
    assert {"list_documents", "search_documents", "find_duplicate_documents"} <= names


def test_list_documents_returns_corpus() -> None:
    store, document = _store_with_document()
    result = _toolset(store).dispatch("list_documents", {})
    assert [doc["id"] for doc in result["documents"]] == [document.id]
    assert result["documents"][0]["filename"] == "glossary.md"


def test_search_documents_keyword_ranks_relevant_passage() -> None:
    store, _ = _store_with_document()
    result = _toolset(store).dispatch(
        "search_documents", {"query": "customer churn", "k": 1}
    )
    assert result["passages"]
    assert "churn" in result["passages"][0]["text"]


def test_search_documents_requires_query() -> None:
    store, _ = _store_with_document()
    result = _toolset(store).dispatch("search_documents", {})
    assert "error" in result


def test_find_duplicate_documents_reports_exact_pair() -> None:
    store, _ = _store_with_document()
    result = _toolset(store).dispatch("find_duplicate_documents", {})
    assert len(result["duplicates"]) == 1
    assert result["duplicates"][0]["exact"] is True


def test_document_tools_degrade_when_unavailable() -> None:
    # No document store wired -> tools return a note, never crash.
    toolset = MdlToolset([])
    assert toolset.dispatch("list_documents", {})["documents"] == []
    assert "note" in toolset.dispatch("search_documents", {"query": "x"})
    assert toolset.dispatch("find_duplicate_documents", {})["duplicates"] == []


def test_owner_isolation_on_document_tools() -> None:
    store, _ = _store_with_document(owner_id="u1")
    # A toolset for a different owner sees nothing.
    other = MdlToolset([], document_store=store, project_id="p1", owner_id="intruder")
    assert other.dispatch("list_documents", {})["documents"] == []
