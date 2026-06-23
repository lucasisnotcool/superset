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

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class AiAgentConversation(Base):
    """Persisted conversation header."""

    __tablename__ = "ai_agent_conversations"

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    database_id = Column(Integer, nullable=False)
    catalog_name = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    scope = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    messages = relationship(
        "AiAgentMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiAgentMessage.sequence",
    )


class AiAgentMessage(Base):
    """Persisted conversation message."""

    __tablename__ = "ai_agent_messages"
    __table_args__ = (
        Index("ix_ai_agent_message_conversation_seq", "conversation_id", "sequence"),
    )

    id = Column(String(36), primary_key=True)
    conversation_id = Column(
        String(36),
        ForeignKey("ai_agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id = Column(String(255), index=True, nullable=False)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    sequence = Column(Integer, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )

    conversation = relationship(
        "AiAgentConversation",
        back_populates="messages",
    )
    artifacts = relationship(
        "AiAgentArtifact",
        back_populates="message",
        cascade="all, delete-orphan",
    )


class AiAgentArtifact(Base):
    """Persisted assistant artifact."""

    __tablename__ = "ai_agent_artifacts"

    id = Column(String(36), primary_key=True)
    message_id = Column(
        String(36),
        ForeignKey("ai_agent_messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id = Column(String(255), index=True, nullable=False)
    type = Column(String(64), nullable=False)
    sql = Column(Text, nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False
    )

    message = relationship(
        "AiAgentMessage",
        back_populates="artifacts",
    )


class AiAgentSemanticDocument(Base):
    """Persisted semantic-layer source document."""

    __tablename__ = "ai_agent_semantic_documents"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=True)
    owner_id = Column(String(255), index=True, nullable=False)
    database_id = Column(Integer, index=True, nullable=False)
    catalog_name = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    dataset_ids = Column(JSON, nullable=False)
    filename = Column(String(512), nullable=False)
    content_type = Column(String(255), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    checksum = Column(String(128), index=True, nullable=False)
    storage_uri = Column(String(1024), nullable=False)
    status = Column(String(64), index=True, nullable=False)
    summary = Column(Text, nullable=True)
    extracted_text = Column(Text, nullable=True)
    extracted_text_preview = Column(Text, nullable=True)
    warnings = Column(JSON, nullable=False)
    error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False
    )


class AiAgentSemanticUpdate(Base):
    """Persisted proposed or reviewed semantic-layer update."""

    __tablename__ = "ai_agent_semantic_updates"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=True)
    document_id = Column(
        String(36),
        ForeignKey("ai_agent_semantic_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    owner_id = Column(String(255), index=True, nullable=False)
    kind = Column(String(64), nullable=False)
    target = Column(JSON, nullable=False)
    value = Column(JSON, nullable=False)
    confidence = Column(Float, nullable=True)
    reviewed = Column(Boolean, nullable=False, default=False)
    approved = Column(Boolean, nullable=False, default=False)
    reviewer_id = Column(String(255), nullable=True)
    review_notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False
    )
    reviewed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )


class AiAgentSemanticLayerVersion(Base):
    """Versioned semantic overlay generated from reviewed updates."""

    __tablename__ = "ai_agent_semantic_layer_versions"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=True)
    owner_id = Column(String(255), index=True, nullable=False)
    database_id = Column(Integer, index=True, nullable=False)
    catalog_name = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    dataset_ids = Column(JSON, nullable=False)
    scope_hash = Column(String(128), index=True, nullable=False)
    version = Column(String(64), nullable=False)
    status = Column(String(64), nullable=False)
    mdl = Column(JSON, nullable=True)
    wren_context = Column(JSON, nullable=True)
    source_update_ids = Column(JSON, nullable=False)
    published_semantic_layer_uuid = Column(
        String(36),
        nullable=True,
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )


class AiAgentWrenContextCache(Base):
    """Cached Wren context for a scope/question pair."""

    __tablename__ = "ai_agent_wren_context_cache"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=True)
    owner_id = Column(String(255), index=True, nullable=False)
    scope_hash = Column(String(128), index=True, nullable=False)
    question_hash = Column(String(128), index=True, nullable=False)
    context = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False
    )
    expires_at = Column(
        DateTime(timezone=True),
        index=True,
        nullable=True,
    )


class AiAgentEvent(Base):
    """Persisted semantic-layer and workflow event."""

    __tablename__ = "ai_agent_events"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=True)
    owner_id = Column(String(255), index=True, nullable=False)
    scope = Column(JSON, nullable=False)
    type = Column(String(128), index=True, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )


class AiAgentSemanticProject(Base):
    """Schema-scoped Wren semantic project."""

    __tablename__ = "ai_agent_semantic_projects"
    __table_args__ = (
        UniqueConstraint(
            "database_uri_fingerprint",
            "catalog_name",
            "schema_name",
            "deleted_at",
            name="uq_ai_agent_semantic_project_scope_deleted",
        ),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(String(255), index=True, nullable=False)
    database_uri_fingerprint = Column(String(128), index=True, nullable=False)
    database_backend = Column(String(255), nullable=True)
    database_label = Column(String(255), nullable=True)
    catalog_name = Column(String(255), nullable=True, default="")
    schema_name = Column(String(255), index=True, nullable=False)
    schema_display_name = Column(String(255), nullable=True)
    default_database_id = Column(Integer, nullable=True)
    visibility = Column(String(64), nullable=False, default="db_access")
    status = Column(String(64), nullable=False, default="active")
    current_version_id = Column(String(36), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class AiAgentSemanticProjectGrant(Base):
    """Explicit semantic project grant."""

    __tablename__ = "ai_agent_semantic_project_grants"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=False)
    grantee_type = Column(String(64), nullable=False)
    grantee_id = Column(String(255), nullable=False)
    permission = Column(String(64), nullable=False)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentSemanticAccessProof(Base):
    """Semantic access proof derived from Superset or URI validation."""

    __tablename__ = "ai_agent_semantic_access_proofs"

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), index=True, nullable=False)
    proof_type = Column(String(64), nullable=False)
    database_id = Column(Integer, nullable=True)
    catalog_names = Column(JSON, nullable=False)
    schema_names = Column(JSON, nullable=False)
    dataset_ids = Column(JSON, nullable=False)
    database_uri_fingerprint = Column(String(128), index=True, nullable=False)
    access_level = Column(String(64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentSchemaSnapshot(Base):
    """Last-known permission-filtered schema for outage-resilient validation."""

    __tablename__ = "ai_agent_schema_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            name="uq_ai_agent_schema_snapshot_project",
        ),
    )

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=False)
    database_uri_fingerprint = Column(String(128), nullable=True)
    catalog_name = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    tables = Column(JSON, nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentJob(Base):
    """Async semantic-layer job (e.g. onboarding) durable across workers."""

    __tablename__ = "ai_agent_jobs"

    id = Column(String(36), primary_key=True)
    kind = Column(String(64), nullable=False)
    status = Column(String(32), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=True)
    owner_id = Column(String(255), index=True, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentSemanticMdlFile(Base):
    """JSON MDL file belonging to a semantic project."""

    __tablename__ = "ai_agent_semantic_mdl_files"
    __table_args__ = (
        Index(
            "ix_ai_agent_semantic_mdl_project_path",
            "project_id",
            "path",
            unique=True,
        ),
    )

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=False)
    path = Column(String(1024), nullable=False)
    filename = Column(String(512), nullable=False)
    content = Column(Text, nullable=False)
    content_type = Column(String(255), nullable=False)
    source_type = Column(String(64), nullable=False)
    status = Column(String(64), index=True, nullable=False)
    validation = Column(JSON, nullable=True)
    checksum = Column(String(128), index=True, nullable=False)
    source_document_id = Column(String(36), nullable=True)
    created_by = Column(String(255), nullable=True)
    updated_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class AiAgentNlSqlExample(Base):
    """A confirmed NL->SQL pair recalled as few-shot (memory learning loop)."""

    __tablename__ = "ai_agent_nl_sql_examples"

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=True)
    scope_hash = Column(String(128), index=True, nullable=False)
    question = Column(Text, nullable=False)
    semantic_sql = Column(Text, nullable=False)
    native_sql = Column(Text, nullable=False)
    result_meta = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentInstruction(Base):
    """A user-authored instruction injected into prompts (Wren `instructions`).

    ``is_global`` instructions always apply for the scope; non-global ones are
    retrieved by similarity to the question.
    """

    __tablename__ = "ai_agent_instructions"

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=True)
    scope_hash = Column(String(128), index=True, nullable=False)
    instruction = Column(Text, nullable=False)
    is_global = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
