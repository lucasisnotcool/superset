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

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
from superset_ai_agent.semantic_layer.document_chunks import build_chunk_records
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.schemas import MdlFile, SemanticDocument


class _ScriptedModel:
    """Returns each scripted JSON response per ``chat`` call (coverage stages)."""

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls = 0

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ModelResult:
        self.calls += 1
        return ModelResult(content=self._contents.pop(0))

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[Any]:
        return []


_COVERAGE_MODEL_FILE = MdlFile(
    project_id="p1",
    path="models/orders.json",
    filename="orders.json",
    content=(
        '{"models": [{"name": "orders", '
        '"tableReference": {"table": "orders"}, '
        '"columns": [{"name": "amount", "type": "BIGINT"}]}]}'
    ),
    checksum="x",
)


def _coverage_responses() -> list[str]:
    claims = json.dumps(
        {
            "claims": [
                {
                    "kind": "metric",
                    "subject": "revenue",
                    "statement": "Revenue is grouped by sales region.",
                    "source_quote": "Revenue is grouped by sales region.",
                }
            ]
        }
    )
    findings = json.dumps({"findings": [{"claim_id": "c0", "status": "missing"}]})
    return [claims, findings]


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
    assert "error" in toolset.dispatch("read_document", {"document_id": "x"})


def test_read_document_returns_full_text_via_chunk_fallback() -> None:
    # The fixture document has no flat extracted_text, so read_document rebuilds
    # the text from the document's chunks (in order).
    store, document = _store_with_document()
    result = _toolset(store).dispatch("read_document", {"document_id": document.id})
    assert result["filename"] == "glossary.md"
    assert "Customer churn" in result["text"]
    assert result["truncated"] is False


def test_read_document_prefers_flat_extract_and_truncates() -> None:
    store = InMemorySemanticLayerStore()
    document = store.save_document(
        SemanticDocument(
            project_id="p1",
            filename="spec.md",
            content_type="text/markdown",
            size_bytes=10,
            scope=ConversationScope(database_id=1, dataset_ids=[]),
            checksum="def",
            storage_uri="mem://spec",
            status="extracted",
            extracted_text="ABCDEFGHIJ",
        ),
        owner_id="u1",
    )
    toolset = MdlToolset([], document_store=store, project_id="p1", owner_id="u1")

    result = toolset.dispatch(
        "read_document", {"document_id": document.id, "max_chars": 4}
    )
    assert result["text"] == "ABCD"
    assert result["truncated"] is True


def test_read_document_rejects_unknown_id() -> None:
    store, _ = _store_with_document()
    result = _toolset(store).dispatch("read_document", {"document_id": "nope"})
    assert "error" in result


def test_document_tools_are_project_scoped() -> None:
    # F5/§5.7: document tools are project-scoped, not owner-isolated. Any DB-authorized
    # user's Copilot sees the project's full doc set (the project is DB-bound, so this
    # never crosses a database boundary). Authorization happens before the toolset is
    # built; within the project, every member sees the same documents.
    store, _ = _store_with_document(owner_id="u1")
    other = MdlToolset([], document_store=store, project_id="p1", owner_id="intruder")
    assert len(other.dispatch("list_documents", {})["documents"]) == 1


# -- R-B6: search→write links the written file to its source document ---------

_MODEL = (
    '{"models": [{"name": "orders", '
    '"tableReference": {"schema": "public", "table": "orders"}, '
    '"columns": [{"name": "id", "type": "BIGINT"}]}]}'
)


def test_single_doc_grounding_stamps_source_document_on_written_file() -> None:
    store, document = _store_with_document()
    toolset = _toolset(store)

    # Agent searches one document, then writes a file derived from it.
    toolset.dispatch("search_documents", {"query": "revenue region", "k": 1})
    toolset.dispatch(
        "write_mdl_file", {"path": "models/orders.json", "content": _MODEL}
    )

    changeset = toolset.build_changeset()
    item = changeset.items[0]
    assert item.source_document_id == document.id
    # And the ledger records the grounding on the call too.
    assert changeset.tool_calls[-1].source_document_ids == [document.id]


def test_write_without_a_preceding_search_has_no_source_link() -> None:
    store, _ = _store_with_document()
    toolset = _toolset(store)

    toolset.dispatch(
        "write_mdl_file", {"path": "models/orders.json", "content": _MODEL}
    )

    assert toolset.build_changeset().items[0].source_document_id is None


def test_grounding_watermark_attributes_only_the_first_write_after_a_search() -> None:
    # Docs searched since the previous mutation ground the next call; a second
    # consecutive write (no fresh search) is left unattributed rather than
    # mis-attributed (best-effort R-B6; the changeset-level ref set stays complete).
    store, document = _store_with_document()
    toolset = _toolset(store)

    toolset.dispatch("search_documents", {"query": "revenue region", "k": 1})
    toolset.dispatch("write_mdl_file", {"path": "models/a.json", "content": _MODEL})
    toolset.dispatch(
        "write_mdl_file",
        {"path": "models/b.json", "content": _MODEL.replace("orders", "orders_b")},
    )

    by_path = {item.path: item for item in toolset.build_changeset().items}
    assert by_path["models/a.json"].source_document_id == document.id
    assert by_path["models/b.json"].source_document_id is None


# -- run_coverage: in-conversation self-review (read-only) ---------------------


def test_run_coverage_without_model_client_degrades() -> None:
    store, _ = _store_with_document()
    toolset = MdlToolset([], document_store=store, project_id="p1", owner_id="u1")
    result = toolset.dispatch("run_coverage", {})
    assert "note" in result
    assert "model client" in result["note"]


def test_run_coverage_without_documents_is_vacuous() -> None:
    toolset = MdlToolset([_COVERAGE_MODEL_FILE], model_client=_ScriptedModel([]))
    result = toolset.dispatch("run_coverage", {})
    assert result["score"] == 1.0


def test_run_coverage_audits_working_set_and_reports_gaps() -> None:
    store, _ = _store_with_document()
    model = _ScriptedModel(_coverage_responses())
    toolset = MdlToolset(
        [_COVERAGE_MODEL_FILE],
        document_store=store,
        project_id="p1",
        owner_id="u1",
        model_client=model,
    )

    result = toolset.dispatch("run_coverage", {})

    assert "score" in result
    assert result["missing"] >= 1
    assert result["findings"][0]["status"] == "missing"
    # Read-only: it produces no changeset tool-call ledger entry.
    assert toolset.build_changeset().tool_calls == []


def test_run_coverage_memoizes_an_unchanged_working_set() -> None:
    store, _ = _store_with_document()
    model = _ScriptedModel(_coverage_responses())
    toolset = MdlToolset(
        [_COVERAGE_MODEL_FILE],
        document_store=store,
        project_id="p1",
        owner_id="u1",
        model_client=model,
    )

    first = toolset.dispatch("run_coverage", {})
    calls_after_first = model.calls
    second = toolset.dispatch("run_coverage", {})

    # Identical working set → cached, no extra model calls.
    assert second == first
    assert model.calls == calls_after_first


def test_run_coverage_caps_self_audits_per_turn() -> None:
    store, _ = _store_with_document()
    # Two audits' worth of responses, but a limit of 1.
    model = _ScriptedModel(_coverage_responses())
    toolset = MdlToolset(
        [_COVERAGE_MODEL_FILE],
        document_store=store,
        project_id="p1",
        owner_id="u1",
        model_client=model,
        coverage_self_audit_limit=1,
    )

    first = toolset.dispatch("run_coverage", {})
    assert "score" in first
    # Change the working set so the memo misses and the cap is what blocks.
    toolset.dispatch(
        "write_mdl_file",
        {"path": "models/extra.json", "content": _MODEL},
    )
    second = toolset.dispatch("run_coverage", {})
    assert "note" in second
    assert "limit reached" in second["note"]
