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

import hashlib
import logging
from datetime import datetime, timezone

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.document_chunks import (
    build_chunk_records,
    DocumentChunk,
    truncate_to_sections,
)
from superset_ai_agent.semantic_layer.document_retriever import (
    document_scope_key,
    DocumentChunkIndex,
)
from superset_ai_agent.semantic_layer.extractors import (
    DocumentExtractor,
    normalize_content_type,
)
from superset_ai_agent.semantic_layer.file_storage import DocumentStorage
from superset_ai_agent.semantic_layer.schemas import SemanticDocument
from superset_ai_agent.semantic_layer.store import SemanticLayerStore

logger = logging.getLogger(__name__)


def create_document(
    *,
    filename: str,
    content_type: str,
    content: bytes,
    scope: ConversationScope,
    project_id: str | None = None,
    owner_id: str = DEFAULT_OWNER_ID,
    config: AgentConfig,
    store: SemanticLayerStore,
    storage: DocumentStorage,
    extractor: DocumentExtractor,
    document_index: DocumentChunkIndex | None = None,
) -> SemanticDocument:
    """Validate, store, and extract text from a semantic source document.

    When document indexing is enabled, a successfully-extracted document is split
    into persisted chunks and (if a vector cache is available) embedded for
    retrieval. Chunking is best-effort: a failure never fails the upload.
    """

    normalized_type = normalize_content_type(content_type)
    _validate_document(
        content_type=normalized_type,
        size_bytes=len(content),
        config=config,
    )
    checksum = hashlib.sha256(content).hexdigest()
    document = SemanticDocument(
        project_id=project_id,
        filename=filename,
        content_type=normalized_type,
        size_bytes=len(content),
        scope=scope,
        checksum=checksum,
        storage_uri="pending",
    )
    storage_uri = storage.write(
        document_id=document.id,
        filename=filename,
        content=content,
    )
    document = document.model_copy(update={"storage_uri": storage_uri})
    document = store.save_document(document, owner_id=owner_id)

    try:
        extracted_text = extractor.extract_text(
            filename=filename,
            content_type=normalized_type,
            content=content,
        )
        warnings: list[str] = []
        # C4: retain whole sections up to the (large) extract limit instead of a
        # blind head-cut, so late-document content survives ingestion and can be
        # relevance-selected at enrichment time.
        extracted_text, truncated = truncate_to_sections(
            extracted_text, config.wren_document_extract_char_limit
        )
        if truncated:
            warnings.append("Document text was truncated for review.")
        document = document.model_copy(
            update={
                "status": "extracted",
                "summary": _summary(extracted_text),
                "extracted_text": extracted_text,
                "extracted_text_preview": _preview(extracted_text),
                "warnings": [*document.warnings, *warnings],
                "updated_at": _utc_now(),
            }
        )
    except Exception as ex:  # pylint: disable=broad-except
        document = document.model_copy(
            update={
                "status": "error",
                "error": str(ex),
                "updated_at": _utc_now(),
            }
        )
    saved = store.update_document(document, owner_id=owner_id)
    if saved.status == "extracted" and config.wren_document_indexing_enabled:
        _index_document_chunks(
            saved,
            store=store,
            document_index=document_index,
            owner_id=owner_id,
        )
    return saved


def _index_document_chunks(
    document: SemanticDocument,
    *,
    store: SemanticLayerStore,
    document_index: DocumentChunkIndex | None,
    owner_id: str,
) -> None:
    """Persist (and best-effort embed) a document's chunks. Never raises.

    Chunks are persisted even without an embedder — they back the viewer, keyword
    retrieval, and exact-duplicate detection; embedding only adds semantic recall.
    """

    try:
        records = build_chunk_records(document.id, document.extracted_text or "")
        if document_index is not None and records:
            scope_key = document_scope_key(document.project_id, document.scope)
            embedded = set(document_index.index(records, scope_key=scope_key))
            if embedded:
                records = [
                    record.model_copy(update={"embedded": record.id in embedded})
                    for record in records
                ]
        store.save_chunks(
            document.id,
            records,
            owner_id=owner_id,
            project_id=document.project_id,
        )
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Document chunk indexing failed for %s: %s", document.id, ex)


def delete_document_cascade(
    document_id: str,
    *,
    owner_id: str,
    store: SemanticLayerStore,
    storage: DocumentStorage,
    document_index: DocumentChunkIndex | None = None,
) -> SemanticDocument:
    """Delete a document everywhere: vectors → chunk rows + document row → blob.

    The single mutation choke point for document deletion. Raises
    ``SemanticDocumentNotFoundError`` if the document is missing / not owned (before
    any mutation). Vector and blob removal are best-effort; the authoritative DB
    rows are removed transactionally by ``store.delete_document``. Returns the
    deleted document (for the response / audit event).
    """

    document = store.get_document(document_id, owner_id=owner_id)
    chunks = store.list_chunks(document_id, owner_id=owner_id)
    if document_index is not None and chunks:
        scope_key = document_scope_key(document.project_id, document.scope)
        document_index.remove([chunk.id for chunk in chunks], scope_key=scope_key)
    # Removes chunk rows + the document row in one transaction (cascade-in-code).
    store.delete_document(document_id, owner_id=owner_id)
    try:
        storage.delete(document.storage_uri)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning(
            "Document blob delete failed for %s (%s); rows already removed.",
            document_id,
            ex,
        )
    return document


def reindex_document(
    document_id: str,
    *,
    owner_id: str,
    store: SemanticLayerStore,
    document_index: DocumentChunkIndex | None = None,
) -> list[DocumentChunk]:
    """Re-chunk + re-embed a document from its stored extracted text.

    Idempotent: chunk ids are deterministic per ``(document_id, index)`` so vectors
    are replaced in place. Returns the persisted chunks.
    """

    document = store.get_document(document_id, owner_id=owner_id)
    _index_document_chunks(
        document,
        store=store,
        document_index=document_index,
        owner_id=owner_id,
    )
    return store.list_chunks(document_id, owner_id=owner_id)


def _validate_document(
    *,
    content_type: str,
    size_bytes: int,
    config: AgentConfig,
) -> None:
    if content_type not in config.wren_allowed_document_types:
        raise ValueError(f"Unsupported document content type: {content_type}")
    if size_bytes <= 0:
        raise ValueError("Document is empty.")
    if size_bytes > config.wren_max_document_bytes:
        raise ValueError(
            "Document exceeds WREN_MAX_DOCUMENT_BYTES "
            f"({config.wren_max_document_bytes})."
        )


def _summary(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    return " ".join(lines[:3])[:500]


def _preview(text: str) -> str | None:
    preview = text.strip()[:2_000]
    return preview or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
