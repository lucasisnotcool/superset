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
from datetime import datetime, timezone

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.extractors import (
    DocumentExtractor,
    normalize_content_type,
)
from superset_ai_agent.semantic_layer.file_storage import DocumentStorage
from superset_ai_agent.semantic_layer.review import propose_updates
from superset_ai_agent.semantic_layer.schemas import SemanticDocument
from superset_ai_agent.semantic_layer.store import SemanticLayerStore


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
) -> SemanticDocument:
    """Validate, store, extract, and create review updates for a document."""

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
        if len(extracted_text) > 20_000:
            warnings.append("Document text was truncated for review.")
            extracted_text = extracted_text[:20_000]
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
        updates = propose_updates(document)
        if updates:
            store.save_updates(document.id, updates, owner_id=owner_id)
        document = document.model_copy(
            update={
                "status": "needs_review" if updates else "extracted",
                "proposed_updates": updates,
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
    return store.update_document(document, owner_id=owner_id)


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
