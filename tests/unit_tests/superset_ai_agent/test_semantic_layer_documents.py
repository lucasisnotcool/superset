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

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.semantic_layer.documents import create_document
from superset_ai_agent.semantic_layer.extractors import CompositeDocumentExtractor
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore


def _scope() -> ConversationScope:
    return ConversationScope(database_id=1, dataset_ids=[42])


def test_create_document_extracts_text(tmp_path) -> None:
    store = InMemorySemanticLayerStore()
    document = create_document(
        filename="gross_moves.md",
        content_type="text/markdown",
        content=(
            b"Gross moves are grouped by sales stage.\n"
            b"Metric gross_moves = count of moves.\n"
            b"Show gross moves by stage?"
        ),
        scope=_scope(),
        owner_id="user-1",
        config=AgentConfig(),
        store=store,
        storage=LocalDocumentStorage(str(tmp_path)),
        extractor=CompositeDocumentExtractor(),
    )

    # The document is extracted for enrichment; the legacy review/overlay flow that
    # produced "proposed_updates" / "needs_review" was removed in C6.
    assert document.status == "extracted"
    assert document.extracted_text_preview is not None
    assert "gross moves" in (document.extracted_text or "").lower()
    assert store.get_document(document.id, owner_id="user-1").checksum


def test_create_document_rejects_disallowed_content_type(tmp_path) -> None:
    # image/png is not in wren_allowed_document_types (html/pdf/docx now are).
    with pytest.raises(ValueError, match="Unsupported document content type"):
        create_document(
            filename="logo.png",
            content_type="image/png",
            content=b"\x89PNG\r\n",
            scope=_scope(),
            owner_id="user-1",
            config=AgentConfig(),
            store=InMemorySemanticLayerStore(),
            storage=LocalDocumentStorage(str(tmp_path)),
            extractor=CompositeDocumentExtractor(),
        )


def test_create_document_extracts_html_now_allowed(tmp_path) -> None:
    document = create_document(
        filename="notes.html",
        content_type="text/html",
        content=b"<html><body><p>Gross moves by stage.</p></body></html>",
        scope=_scope(),
        owner_id="user-1",
        config=AgentConfig(),
        store=InMemorySemanticLayerStore(),
        storage=LocalDocumentStorage(str(tmp_path)),
        extractor=CompositeDocumentExtractor(),
    )
    assert document.status == "extracted"
    assert "Gross moves by stage." in (document.extracted_text or "")


def test_create_document_rejects_oversized_content(tmp_path) -> None:
    with pytest.raises(ValueError, match="exceeds WREN_MAX_DOCUMENT_BYTES"):
        create_document(
            filename="notes.txt",
            content_type="text/plain",
            content=b"123456",
            scope=_scope(),
            owner_id="user-1",
            config=AgentConfig(wren_max_document_bytes=5),
            store=InMemorySemanticLayerStore(),
            storage=LocalDocumentStorage(str(tmp_path)),
            extractor=CompositeDocumentExtractor(),
        )


def _create(store, tmp_path, content: bytes, *, project_id: str | None = "project-1"):
    return create_document(
        filename="notes.md",
        content_type="text/markdown",
        content=content,
        scope=_scope(),
        project_id=project_id,
        owner_id="user-1",
        config=AgentConfig(),
        store=store,
        storage=LocalDocumentStorage(str(tmp_path)),
        extractor=CompositeDocumentExtractor(),
    )


def test_create_document_dedups_identical_bytes(tmp_path) -> None:
    store = InMemorySemanticLayerStore()
    first = _create(store, tmp_path, b"Gross moves by stage.")
    assert first.deduplicated is False

    second = _create(store, tmp_path, b"Gross moves by stage.")
    # The second upload reuses the first document; no new row is created.
    assert second.deduplicated is True
    assert second.id == first.id
    assert len(store.list_project_documents("project-1", owner_id="user-1")) == 1


def test_create_document_does_not_reindex_on_dedup(tmp_path) -> None:
    # A spy index proves vectorization runs once for the first upload and is
    # skipped entirely on the deduplicated second upload (R5 cost control).
    class _SpyIndex:
        def __init__(self) -> None:
            self.calls = 0

        def index(self, records, *, scope_key):  # noqa: ANN001, ANN201
            self.calls += 1
            return []  # no embedder; returns the ids that were embedded

    store = InMemorySemanticLayerStore()
    index = _SpyIndex()

    def _create_indexed(content: bytes):
        return create_document(
            filename="notes.md",
            content_type="text/markdown",
            content=content,
            scope=_scope(),
            project_id="project-1",
            owner_id="user-1",
            config=AgentConfig(),
            store=store,
            storage=LocalDocumentStorage(str(tmp_path)),
            extractor=CompositeDocumentExtractor(),
            document_index=index,  # type: ignore[arg-type]
        )

    _create_indexed(b"Gross moves by stage.")
    calls_after_first = index.calls
    second = _create_indexed(b"Gross moves by stage.")

    assert second.deduplicated is True
    assert index.calls == calls_after_first  # no re-index on the dedup hit


def test_create_document_distinct_projects_do_not_dedup(tmp_path) -> None:
    store = InMemorySemanticLayerStore()
    first = _create(store, tmp_path, b"shared bytes", project_id="project-1")
    second = _create(store, tmp_path, b"shared bytes", project_id="project-2")

    assert second.deduplicated is False
    assert second.id != first.id


def test_create_document_without_project_does_not_dedup(tmp_path) -> None:
    # Scope-only uploads (no project) skip dedup: there is no project to scope it.
    store = InMemorySemanticLayerStore()
    first = _create(store, tmp_path, b"shared bytes", project_id=None)
    second = _create(store, tmp_path, b"shared bytes", project_id=None)

    assert first.deduplicated is False
    assert second.deduplicated is False
    assert second.id != first.id
