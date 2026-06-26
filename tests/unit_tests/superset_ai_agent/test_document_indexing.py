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
from superset_ai_agent.semantic_layer.document_retriever import DocumentChunkIndex
from superset_ai_agent.semantic_layer.documents import (
    create_document,
    delete_document_cascade,
    extract_document,
    register_document,
    reindex_document,
)
from superset_ai_agent.semantic_layer.extractors import (
    CompositeDocumentExtractor,
    NeedsOcrError,
)
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.store import SemanticDocumentNotFoundError

_CONTENT = b"Revenue by region.\n\nWeather notes.\n\nCustomer churn drivers."


def _scope() -> ConversationScope:
    return ConversationScope(database_id=1, dataset_ids=[42])


class _RecordingStorage:
    """In-memory DocumentStorage that records deletes (no filesystem)."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def write(self, *, document_id: str, filename: str, content: bytes) -> str:
        uri = f"mem://{document_id}/{filename}"
        self._blobs[uri] = content
        return uri

    def read(self, storage_uri: str) -> bytes:
        return self._blobs[storage_uri]

    def delete(self, storage_uri: str) -> None:
        self.deleted.append(storage_uri)
        self._blobs.pop(storage_uri, None)


class _RecordingIndex(DocumentChunkIndex):
    """A DocumentChunkIndex whose vector cache is a deterministic fake."""

    def __init__(self, ranking: list[str] | None = None) -> None:
        recorder = self

        class _Cache:
            def is_available(self) -> bool:
                return True

            def upsert(self, *, scope_key: str, row_id: str, text: str) -> bool:
                recorder.indexed.append(row_id)
                return True

            def remove(self, *, scope_key: str, row_id: str) -> bool:
                recorder.removed.append(row_id)
                return True

            def search(self, *, scope_key: str, query: str, k: int) -> list[str] | None:
                return (ranking or [])[:k]

        self.indexed: list[str] = []
        self.removed: list[str] = []
        super().__init__(_Cache())


def _create(store, storage, *, config, document_index=None):
    return create_document(
        filename="doc.md",
        content_type="text/markdown",
        content=_CONTENT,
        scope=_scope(),
        project_id="project-1",
        owner_id="user-1",
        config=config,
        store=store,
        storage=storage,
        extractor=CompositeDocumentExtractor(),
        document_index=document_index,
    )


def test_indexing_disabled_persists_no_chunks() -> None:
    store = InMemorySemanticLayerStore()
    document = _create(store, _RecordingStorage(), config=AgentConfig())
    assert store.list_chunks(document.id, owner_id="user-1") == []


def test_indexing_enabled_persists_chunks_without_embedder() -> None:
    store = InMemorySemanticLayerStore()
    document = _create(
        store,
        _RecordingStorage(),
        config=AgentConfig(wren_document_indexing_enabled=True),
    )
    chunks = store.list_chunks(document.id, owner_id="user-1")
    assert len(chunks) == 3
    # No vector index supplied -> persisted but not embedded.
    assert all(chunk.embedded is False for chunk in chunks)


def test_indexing_enabled_embeds_with_index() -> None:
    store = InMemorySemanticLayerStore()
    index = _RecordingIndex()
    document = _create(
        store,
        _RecordingStorage(),
        config=AgentConfig(wren_document_indexing_enabled=True),
        document_index=index,
    )
    chunks = store.list_chunks(document.id, owner_id="user-1")
    assert len(index.indexed) == 3  # every chunk upserted into the vector cache
    assert all(chunk.embedded is True for chunk in chunks)


def test_indexing_failure_never_fails_upload(monkeypatch) -> None:
    # A broken index must not break extraction (best-effort, degrade closed).
    store = InMemorySemanticLayerStore()

    class _BoomIndex(DocumentChunkIndex):
        def __init__(self) -> None:
            super().__init__(None)

        def index(self, chunks, *, scope_key):  # noqa: ANN001, ANN201
            raise RuntimeError("boom")

    document = _create(
        store,
        _RecordingStorage(),
        config=AgentConfig(wren_document_indexing_enabled=True),
        document_index=_BoomIndex(),
    )
    assert document.status == "extracted"  # upload still succeeded


def test_delete_document_cascade_removes_everything() -> None:
    store = InMemorySemanticLayerStore()
    storage = _RecordingStorage()
    index = _RecordingIndex()
    document = _create(
        store,
        storage,
        config=AgentConfig(wren_document_indexing_enabled=True),
        document_index=index,
    )

    deleted = delete_document_cascade(
        document.id,
        owner_id="user-1",
        store=store,
        storage=storage,
        document_index=index,
    )

    assert deleted.id == document.id
    assert index.removed == list(index.indexed)  # vectors evicted
    assert storage.deleted == [document.storage_uri]  # blob removed
    assert store.list_chunks(document.id, owner_id="user-1") == []  # chunk rows gone
    with pytest.raises(SemanticDocumentNotFoundError):
        store.get_document(document.id, owner_id="user-1")


def test_delete_cascade_rejects_wrong_owner() -> None:
    store = InMemorySemanticLayerStore()
    storage = _RecordingStorage()
    document = _create(
        store, storage, config=AgentConfig(wren_document_indexing_enabled=True)
    )
    with pytest.raises(SemanticDocumentNotFoundError):
        delete_document_cascade(
            document.id, owner_id="intruder", store=store, storage=storage
        )
    # Nothing mutated on the rejected delete.
    assert storage.deleted == []
    assert store.get_document(document.id, owner_id="user-1").id == document.id


def test_reindex_is_idempotent() -> None:
    store = InMemorySemanticLayerStore()
    index = _RecordingIndex()
    document = _create(
        store,
        _RecordingStorage(),
        config=AgentConfig(wren_document_indexing_enabled=True),
        document_index=index,
    )
    first = [chunk.id for chunk in store.list_chunks(document.id, owner_id="user-1")]

    chunks = reindex_document(
        document.id, owner_id="user-1", store=store, document_index=index
    )

    # Deterministic ids -> reindex replaces in place, same ids, no duplication.
    assert [chunk.id for chunk in chunks] == first


# --- register/extract split (Step 7) --------------------------------------


def test_register_document_leaves_status_uploaded_and_skips_extraction() -> None:
    store = InMemorySemanticLayerStore()
    document = register_document(
        filename="doc.md",
        content_type="text/markdown",
        content=_CONTENT,
        scope=_scope(),
        project_id="project-1",
        owner_id="user-1",
        config=AgentConfig(wren_document_indexing_enabled=True),
        store=store,
        storage=_RecordingStorage(),
    )
    assert document.status == "uploaded"
    assert document.extracted_text is None
    assert store.list_chunks(document.id, owner_id="user-1") == []


def test_extract_document_advances_registered_document() -> None:
    store = InMemorySemanticLayerStore()
    storage = _RecordingStorage()
    config = AgentConfig(wren_document_indexing_enabled=True)
    registered = register_document(
        filename="doc.md",
        content_type="text/markdown",
        content=_CONTENT,
        scope=_scope(),
        project_id="project-1",
        owner_id="user-1",
        config=config,
        store=store,
        storage=storage,
    )

    extracted = extract_document(
        registered.id,
        owner_id="user-1",
        config=config,
        store=store,
        storage=storage,
        extractor=CompositeDocumentExtractor(),
    )

    assert extracted.status == "extracted"
    assert extracted.extracted_text
    assert len(store.list_chunks(registered.id, owner_id="user-1")) == 3


class _NeedsOcrExtractor:
    """Stub extractor that always signals an image-only document."""

    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> str:
        raise NeedsOcrError("PDF has no extractable text layer; OCR required.")


def test_image_only_document_is_tagged_needs_ocr() -> None:
    store = InMemorySemanticLayerStore()
    storage = _RecordingStorage()
    config = AgentConfig(wren_document_indexing_enabled=True)
    document = create_document(
        filename="scan.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.4 image only",
        scope=_scope(),
        project_id="project-1",
        owner_id="user-1",
        config=config,
        store=store,
        storage=storage,
        extractor=_NeedsOcrExtractor(),
    )

    assert document.status == "needs_ocr"
    assert document.error is None
    # No chunks indexed for a needs-OCR document...
    assert store.list_chunks(document.id, owner_id="user-1") == []
    # ...but the original blob is retained for a future OCR pass / download.
    assert storage.read(document.storage_uri) == b"%PDF-1.4 image only"
    assert any("OCR" in warning for warning in document.warnings)
