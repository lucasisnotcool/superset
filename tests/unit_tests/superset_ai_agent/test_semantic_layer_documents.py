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
