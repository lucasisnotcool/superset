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

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.schemas import WrenContextArtifact

SemanticDocumentStatus = Literal[
    "uploaded",
    "extracted",
    "needs_review",
    "approved",
    "indexed",
    "error",
]
SemanticUpdateKind = Literal[
    "model_description",
    "field_description",
    "metric",
    "synonym",
    "example",
    "relationship",
]
IndexingStatus = Literal["idle", "running", "error"]
SemanticLayerEventType = Literal[
    "document_uploaded",
    "document_extracted",
    "review_required",
    "review_saved",
    "index_started",
    "index_completed",
    "index_failed",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class SemanticUpdate(BaseModel):
    """Proposed or reviewed semantic-layer update derived from a document."""

    id: str = Field(default_factory=_new_id)
    kind: SemanticUpdateKind
    target: dict[str, Any]
    value: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_document_id: str
    reviewed: bool = False
    approved: bool = False
    reviewer_id: str | None = None
    review_notes: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    reviewed_at: datetime | None = None


class SemanticDocument(BaseModel):
    """Uploaded semantic-layer source document."""

    id: str = Field(default_factory=_new_id)
    filename: str
    content_type: str
    size_bytes: int
    status: SemanticDocumentStatus = "uploaded"
    scope: ConversationScope
    checksum: str
    storage_uri: str
    summary: str | None = None
    extracted_text: str | None = None
    extracted_text_preview: str | None = None
    proposed_updates: list[SemanticUpdate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class SemanticLayerReviewRequest(BaseModel):
    """Review decision for proposed semantic-layer updates."""

    approved_update_ids: list[str] = Field(default_factory=list)
    rejected_update_ids: list[str] = Field(default_factory=list)
    edited_updates: list[SemanticUpdate] = Field(default_factory=list)
    notes: str | None = None


class SemanticLayerState(BaseModel):
    """Semantic-layer document and indexing state for one Superset scope."""

    database_id: int
    schema_name: str | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    document_count: int
    approved_document_count: int
    indexed_document_count: int
    semantic_layer_version: str | None = None
    indexing_status: IndexingStatus = "idle"
    last_error: str | None = None


class SemanticLayerVersion(BaseModel):
    """Versioned reviewed semantic overlay for a Superset scope."""

    id: str = Field(default_factory=_new_id)
    scope: ConversationScope
    scope_hash: str
    version: str
    status: IndexingStatus = "idle"
    mdl: dict[str, Any] | None = None
    wren_context: WrenContextArtifact | None = None
    source_update_ids: list[str] = Field(default_factory=list)
    published_semantic_layer_uuid: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class SemanticLayerIndexRequest(BaseModel):
    """Request to rebuild the reviewed semantic overlay for a scope."""

    scope: ConversationScope


class SemanticLayerEvent(BaseModel):
    """Semantic-layer event for polling or server-sent events."""

    id: str = Field(default_factory=_new_id)
    type: SemanticLayerEventType
    scope: ConversationScope
    document_id: str | None = None
    state: SemanticLayerState | None = None
    message: str
    created_at: datetime = Field(default_factory=_utc_now)
