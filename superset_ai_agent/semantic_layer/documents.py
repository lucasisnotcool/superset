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
    NeedsOcrError,
    normalize_content_type,
)
from superset_ai_agent.semantic_layer.file_storage import DocumentStorage
from superset_ai_agent.semantic_layer.schemas import SemanticDocument
from superset_ai_agent.semantic_layer.store import SemanticLayerStore

logger = logging.getLogger(__name__)


def register_document(
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
) -> SemanticDocument:
    """Validate and persist a document's bytes + metadata row, without extracting.

    Fast path: validates, writes the original blob, and saves the row with
    ``status="uploaded"``. Extraction (which may be slow for large PDFs / Office
    files) is performed separately by :func:`extract_document`, inline for small
    files or on a background thread for large ones. Raises ``ValueError`` on a
    rejected (type/size) upload before any blob is written.
    """

    normalized_type = normalize_content_type(content_type)
    _validate_document(
        content_type=normalized_type,
        size_bytes=len(content),
        config=config,
    )
    checksum = hashlib.sha256(content).hexdigest()
    # Content-hash dedup (best-practice idempotent ingestion): if these exact bytes
    # were already ingested into this project, reuse the existing document instead
    # of writing a second blob/row/chunk-set/vector-set. ``deduplicated`` signals
    # the caller to skip (re-)extraction. Only project-scoped uploads dedup; the
    # same bytes in a different project are a distinct artifact.
    if project_id is not None:
        existing = store.find_document_by_checksum(
            project_id,
            checksum,
            owner_id=owner_id,
        )
        if existing is not None:
            return existing.model_copy(update={"deduplicated": True})
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
    return store.save_document(document, owner_id=owner_id)


def extract_document(
    document_id: str,
    *,
    owner_id: str = DEFAULT_OWNER_ID,
    config: AgentConfig,
    store: SemanticLayerStore,
    storage: DocumentStorage,
    extractor: DocumentExtractor,
    document_index: DocumentChunkIndex | None = None,
) -> SemanticDocument:
    """Extract text for an already-registered document and update its status.

    Reads the original bytes back from blob storage, so it is safe to run on a
    background thread without holding the content in memory. Maps
    ``NeedsOcrError`` to ``status="needs_ocr"`` (the OCR seam; no OCR is performed)
    and any other failure to ``status="error"``. On success, when document indexing
    is enabled, chunks are persisted and best-effort embedded. Never raises: a
    failure is recorded on the document row.
    """

    document = store.get_document(document_id, owner_id=owner_id)
    content = storage.read(document.storage_uri)
    try:
        extracted_text = extractor.extract_text(
            filename=document.filename,
            content_type=document.content_type,
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
    except NeedsOcrError as ex:
        # Image-only / no text layer: tag for a future OCR backend rather than
        # storing an empty document. The original bytes remain available.
        document = document.model_copy(
            update={
                "status": "needs_ocr",
                "warnings": [*document.warnings, str(ex)],
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
    """Validate, store, and extract text from a semantic source document (inline).

    Convenience composition of :func:`register_document` + :func:`extract_document`
    used by callers that want synchronous extraction (the text endpoint, small
    uploads, tests). Large uploads route the two phases separately so extraction
    can run in the background; see ``app.py``.
    """

    document = register_document(
        filename=filename,
        content_type=content_type,
        content=content,
        scope=scope,
        project_id=project_id,
        owner_id=owner_id,
        config=config,
        store=store,
        storage=storage,
    )
    if document.deduplicated:
        # Byte-identical to an existing project document: it is already extracted
        # and indexed, so skip re-extraction and return the existing row as-is.
        return document
    return extract_document(
        document.id,
        owner_id=owner_id,
        config=config,
        store=store,
        storage=storage,
        extractor=extractor,
        document_index=document_index,
    )


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
