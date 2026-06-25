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
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from superset_ai_agent.conversations.schemas import ConversationScope

# Active values are "uploaded"/"extracted"/"error". "needs_review"/"approved"/
# "indexed" are legacy (the removed document-review/overlay flow) and are retained
# ONLY so pre-existing persisted rows still validate on read; nothing produces them.
SemanticDocumentStatus = Literal[
    "uploaded",
    "extracted",
    "needs_review",
    "approved",
    "indexed",
    "error",
]
# "review_*"/"index_*" are legacy event types from the removed review/overlay flow,
# retained only for read-compat with persisted rows; current code emits the
# "document_*"/"onboarding_*" types (plus "index_failed" reused for extraction errors).
SemanticLayerEventType = Literal[
    "document_uploaded",
    "document_extracted",
    "review_required",
    "review_saved",
    "index_started",
    "index_completed",
    "index_failed",
    "onboarding_started",
    "onboarding_completed",
    "onboarding_failed",
]
SemanticJobStatus = Literal["running", "completed", "failed"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class SemanticDocument(BaseModel):
    """Uploaded semantic-layer source document."""

    id: str = Field(default_factory=_new_id)
    project_id: str | None = None
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
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class SemanticLayerState(BaseModel):
    """Semantic-layer document state for one Superset scope."""

    project_id: str | None = None
    database_id: int
    catalog_name: str | None = None
    schema_name: str | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    document_count: int
    last_error: str | None = None


class InstructionCreateRequest(BaseModel):
    """Request to add a user-authored instruction for a scope (Wren guidance)."""

    scope: ConversationScope
    instruction: str
    is_global: bool = False


class SemanticLayerEvent(BaseModel):
    """Semantic-layer event for polling or server-sent events."""

    id: str = Field(default_factory=_new_id)
    project_id: str | None = None
    type: SemanticLayerEventType
    scope: ConversationScope
    document_id: str | None = None
    state: SemanticLayerState | None = None
    message: str
    created_at: datetime = Field(default_factory=_utc_now)


SemanticProjectVisibility = Literal["private", "db_access", "custom"]
SemanticProjectStatus = Literal["active", "archived"]
SemanticPermission = Literal["read", "write", "admin"]
MdlFileStatus = Literal["draft", "active", "deleted"]
MdlFileSourceType = Literal[
    "uploaded_mdl",
    "manual",
    "enriched_markdown",
    "onboarding",
    "copilot",
]
MdlContentType = Literal["application/json"]


class SemanticProject(BaseModel):
    """Schema-scoped Wren semantic project."""

    id: str = Field(default_factory=_new_id)
    name: str
    description: str | None = None
    owner_id: str
    database_uri_fingerprint: str
    database_backend: str | None = None
    database_label: str | None = None
    catalog_name: str | None = None
    schema_name: str
    schema_display_name: str | None = None
    default_database_id: int | None = None
    visibility: SemanticProjectVisibility = "db_access"
    current_version_id: str | None = None
    status: SemanticProjectStatus = "active"
    permission: SemanticPermission = "admin"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    deleted_at: datetime | None = None


class SemanticProjectResolveRequest(BaseModel):
    """Resolve or create the schema project for a database/catalog/schema."""

    database_id: int
    database_label: str | None = None
    database_backend: str | None = None
    catalog_name: str | None = None
    schema_name: str
    supplied_uri: str | None = None
    database_uri_fingerprint: str | None = None
    create_if_missing: bool = True


class MdlValidationMessage(BaseModel):
    """MDL validation message for editor annotations."""

    line: int | None = None
    column: int | None = None
    severity: Literal["error", "warning", "info"] = "error"
    message: str
    code: str | None = None


class MdlValidationResult(BaseModel):
    """Validation result for one MDL JSON file."""

    valid: bool
    messages: list[MdlValidationMessage] = Field(default_factory=list)


class MdlFile(BaseModel):
    """One JSON file in a schema-scoped Wren MDL project (native manifest shape)."""

    id: str = Field(default_factory=_new_id)
    project_id: str
    path: str
    filename: str
    content: str
    content_type: MdlContentType = "application/json"
    source_type: MdlFileSourceType = "manual"
    status: MdlFileStatus = "draft"
    validation: MdlValidationResult | None = None
    checksum: str
    source_document_id: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    deleted_at: datetime | None = None


class MdlFileCreateRequest(BaseModel):
    """Create a draft MDL JSON file."""

    path: str
    content: str
    source_type: MdlFileSourceType = "manual"
    source_document_id: str | None = None


class MdlFileUpdateRequest(BaseModel):
    """Update an MDL JSON file."""

    path: str | None = None
    content: str | None = None
    status: MdlFileStatus | None = None


class MdlEnrichmentProposal(BaseModel):
    """Proposed MDL (native JSON) generated from a source document."""

    source_document_id: str
    proposed_path: str
    proposed_content: str
    validation: MdlValidationResult
    warnings: list[str] = Field(default_factory=list)


class WrenMaterializationResult(BaseModel):
    """Result of writing active MDL files into a Wren project directory."""

    project_id: str
    path: str
    file_count: int
    checksum: str
    warnings: list[str] = Field(default_factory=list)


class OnboardingResult(BaseModel):
    """Result of generating base MDL from schema introspection."""

    project_id: str
    files: list[MdlFile] = Field(default_factory=list)
    model_count: int = 0
    activated_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class SemanticDocumentTextRequest(BaseModel):
    """Create a semantic source document from pasted text."""

    filename: str = "document.md"
    text: str
    content_type: str = "text/markdown"


class SemanticJob(BaseModel):
    """Async semantic-layer job (e.g. schema onboarding) for polling."""

    id: str = Field(default_factory=_new_id)
    kind: str
    status: SemanticJobStatus = "running"
    project_id: str | None = None
    result: OnboardingResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
