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
    text,
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
    #: Agent discriminator (``sql`` for the AI SQL agent, ``copilot`` for the MDL
    #: Copilot). ``server_default`` backfills pre-existing rows to ``sql``.
    kind = Column(
        String(32),
        nullable=False,
        server_default="sql",
        index=True,
    )
    #: Semantic project binding for project-scoped agents (the Copilot). Plain
    #: column, not a FK — conversations outlive projects.
    project_id = Column(String(36), nullable=True, index=True)
    database_id = Column(Integer, nullable=False)
    catalog_name = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    scope = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
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
    created_at = Column(DateTime(timezone=True), nullable=False)

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
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

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
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentDocumentChunk(Base):
    """Persisted, retrievable slice of an extracted semantic-layer document.

    The durable system-of-record for document RAG: chunk text + offsets live here,
    while the derived vectors live in the document vector store (keyed by ``id``).
    Wholly owned by its parent document — deleted with it (cascade-in-code).
    """

    __tablename__ = "ai_agent_document_chunks"

    id = Column(String(36), primary_key=True)
    # Logical FK to ai_agent_semantic_documents.id. The codebase models do not use
    # DB-level foreign keys for agent tables; the parent cascade is enforced in the
    # store (delete_document removes chunks in the same transaction).
    document_id = Column(String(36), index=True, nullable=False)
    owner_id = Column(String(255), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    checksum = Column(String(128), index=True, nullable=False)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)
    embedded = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_ai_agent_document_chunks_document_index",
        ),
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
    """A Wren semantic project over one database (may span its schemas).

    Identity is ``(database_uri_fingerprint, catalog_name, slug)`` — a database can
    hold many named projects, and a project can be duplicated. ``schema_name`` is the
    primary schema (the wren-core namespace) but no longer part of project identity.
    ``owner_id`` is retained as ``created_by`` audit only; access is DB-access-derived.
    """

    __tablename__ = "ai_agent_semantic_projects"
    __table_args__ = (
        # Partial unique index: one **active** project per (database, catalog, slug).
        # A plain unique constraint over a nullable ``deleted_at`` would not enforce
        # this (SQL treats NULL as distinct), so soft-deleted rows are excluded via
        # the ``deleted_at IS NULL`` predicate — real DB-level identity enforcement.
        Index(
            "uq_ai_agent_semantic_project_slug_active",
            "database_uri_fingerprint",
            "catalog_name",
            "slug",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    #: URL/identity-safe unique handle within (database, catalog); derived from name.
    slug = Column(String(255), index=True, nullable=False)
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


class AiAgentSemanticProjectSchema(Base):
    """One schema a semantic project is scoped to (multi-schema membership).

    Normalized membership set for a project. The project's ``schema_name`` column is
    retained as the *primary* schema (the wren-core logical namespace); this table is
    authoritative for the *full* set a project may reference via per-model
    ``tableReference.schema``. A row per (project, schema); ``position`` preserves the
    authored order with the primary at 0.
    """

    __tablename__ = "ai_agent_semantic_project_schemas"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "schema_name",
            name="uq_ai_agent_semantic_project_schema",
        ),
    )

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=False)
    schema_name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)


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


class AiAgentCoverageRun(Base):
    """A background MDL-directory coverage run and its stored report (Feature B).

    Doubles as the supersession state row: ``status`` + ``mdl_checksum`` let a
    new active-set change cancel a stale in-flight run and start one on the
    latest version. ``report`` holds the full ``CoverageReport`` JSON; ``score``
    is denormalized for a cheap latest-score badge.
    """

    __tablename__ = "ai_agent_coverage_runs"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=False)
    owner_id = Column(String(255), index=True, nullable=False)
    mdl_checksum = Column(String(128), index=True, nullable=False)
    docs_checksum = Column(String(128), nullable=False, default="")
    status = Column(String(32), index=True, nullable=False)
    score = Column(Float, nullable=True)
    report = Column(JSON, nullable=True)
    # Live, coarse progress while ``running`` (Feature C); null before the first
    # stage tick and once the run reaches a terminal state.
    progress = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), index=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), index=True, nullable=False)
