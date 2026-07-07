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
    LargeBinary,
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
    #: Uploader audit stamp only — documents are DB-tied (shared by everyone
    #: who can reach the database), never owner-filtered on read.
    owner_id = Column(String(255), index=True, nullable=False)
    database_id = Column(Integer, index=True, nullable=False)
    #: Credential-free physical-database identity (DB-tied sharing key).
    #: ``database_id`` identifies one Superset connection row; two users'
    #: separate connections to the same physical DB share this fingerprint.
    #: NULL on legacy rows (reads fall back to ``database_id``).
    database_uri_fingerprint = Column(String(128), index=True, nullable=True)
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


class AiAgentDocumentBlob(Base):
    """Raw uploaded-document bytes for ``AI_AGENT_DOCUMENT_STORAGE=postgres``.

    The Postgres twin of the local-FS/S3 object stores: one row per stored file,
    keyed by the same ``document_id``/``filename`` pair the other backends encode
    in their storage URIs. Bytes ride a ``LargeBinary`` (Postgres ``bytea`` —
    TOASTed transparently, deleted with the row) sized by the existing upload cap
    (``wren_max_document_bytes``), so no large-object bookkeeping is needed.
    """

    __tablename__ = "ai_agent_document_blobs"

    #: ``<document_id>/<safe_filename>`` — the same identity the URI carries.
    storage_key = Column(String(1024), primary_key=True)
    document_id = Column(String(36), index=True, nullable=False)
    filename = Column(String(512), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


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
    # Schema-qualified twin of ``tables`` (schema → table → columns) so a
    # multi-schema project's outage-fallback validation stays schema-aware instead
    # of collapsing to the flat, collidable map. Nullable: old rows + single-schema
    # snapshots leave it empty and degrade closed (F3).
    tables_by_schema = Column(JSON, nullable=True)
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
    """A confirmed NL->SQL pair recalled as few-shot (memory learning loop).

    Keyed by ``database_uri_fingerprint`` when present (DB-tied: shared across
    every user's own connection to the same physical database), falling back to
    ``database_id`` for legacy rows (see F1); ``owner_id`` is retained as
    authorship metadata only, no longer a scoping key. ``scope_hash`` is kept
    for back-compat / legacy rows. ``referenced_tables`` /
    ``referenced_schemas`` capture the physical references the pair touches,
    used to RBAC-filter recall (F2).
    """

    __tablename__ = "ai_agent_nl_sql_examples"

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=True)
    database_id = Column(Integer, index=True, nullable=True)
    #: DB-tied sharing key (see AiAgentSemanticDocument). NULL on legacy rows.
    database_uri_fingerprint = Column(String(128), index=True, nullable=True)
    scope_hash = Column(String(128), index=True, nullable=False)
    question = Column(Text, nullable=False)
    semantic_sql = Column(Text, nullable=False)
    native_sql = Column(Text, nullable=False)
    referenced_tables = Column(JSON, nullable=True)
    referenced_schemas = Column(JSON, nullable=True)
    result_meta = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentInstruction(Base):
    """A user-authored instruction injected into prompts (Wren `instructions`).

    DB-tied: keyed by ``scope_hash`` alone (computed with the database
    fingerprint substituted for ``database_id`` when resolvable), so every user
    who can reach the database shares the same instruction set. ``owner_id`` is
    an authorship audit stamp only, never a read filter. ``is_global``
    instructions always apply for the scope; non-global ones are retrieved by
    similarity to the question.
    """

    __tablename__ = "ai_agent_instructions"

    id = Column(String(36), primary_key=True)
    owner_id = Column(String(255), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=True)
    scope_hash = Column(String(128), index=True, nullable=False)
    #: DB-tied sharing key (see AiAgentSemanticDocument); denormalized for
    #: querying/debugging — reads key on ``scope_hash``. NULL on legacy rows.
    database_uri_fingerprint = Column(String(128), index=True, nullable=True)
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
    # Coverage recovery agent (chained Copilot turn that proposes gap-closing
    # edits). ``recovery_conversation_id`` links the persisted recovery thread (its
    # changeset artifact is the suggestion set); ``recovery_status`` tracks that
    # chained job; ``recovery_dismissed_at`` is the durable, per-run dismissal of
    # the "suggestions ready" notification.
    recovery_conversation_id = Column(String(36), nullable=True)
    recovery_status = Column(String(32), nullable=True)
    recovery_dismissed_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), index=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), index=True, nullable=False)


class AiAgentEvalBenchmark(Base):
    """A project-scoped benchmark (Dataset): a named set of NL test questions.

    The user-facing test set for "score MY MDL project" (F11). Soft-deleted so
    historical runs keep resolving their parent. ``owner_id`` is an authorship
    audit stamp; access is derived from the project (db-access), never
    owner-filtered on read.
    """

    __tablename__ = "ai_agent_eval_benchmarks"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), index=True, nullable=False)
    owner_id = Column(String(255), index=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class AiAgentEvalItem(Base):
    """One benchmark test case: NL question + typed expected answer.

    ``answer_type`` discriminates the ``answer_spec`` JSON payload:
    ``gold_sql`` (ground-truth SQL, compared by executed result sets),
    ``expected_values`` (typed value assertions: nums/names/absent/trap/zero),
    or ``eval_note`` (free-text rubric; scores ``needs_review`` until the
    LLM-judge evaluator lands). Soft-deleted so past results keep their
    reference; results additionally freeze the question/spec they ran against.
    """

    __tablename__ = "ai_agent_eval_items"

    id = Column(String(36), primary_key=True)
    benchmark_id = Column(String(36), index=True, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    question = Column(Text, nullable=False)
    answer_type = Column(String(32), nullable=False)
    answer_spec = Column(JSON, nullable=False)
    capability_tags = Column(JSON, nullable=False, default=list)
    #: Dual-use flywheel (DP-18): item doubles as a recallable golden example.
    use_as_example = Column(Boolean, nullable=False, default=False)
    verified_by = Column(String(255), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class AiAgentEvalRun(Base):
    """One scored execution of a benchmark against the agent + project.

    Immutable once terminal: ``benchmark_checksum`` pins the exact item set and
    ``mdl_checksum`` the MDL version it ran against, so run-vs-run comparison is
    apples-to-apples. Lifecycle mirrors coverage runs (pending → running via a
    compare-and-set claim; a new submission supersedes in-flight runs for the
    same benchmark).
    """

    __tablename__ = "ai_agent_eval_runs"

    id = Column(String(36), primary_key=True)
    benchmark_id = Column(String(36), index=True, nullable=False)
    project_id = Column(String(36), index=True, nullable=False)
    owner_id = Column(String(255), index=True, nullable=False)
    status = Column(String(32), index=True, nullable=False)
    trials = Column(Integer, nullable=False, default=1)
    #: Snapshot of submit-time options (item subset, execute, recall mode…).
    config = Column(JSON, nullable=False, default=dict)
    mdl_checksum = Column(String(128), nullable=True)
    benchmark_checksum = Column(String(128), nullable=False, default="")
    database_id = Column(Integer, nullable=True)
    #: Headline pass rate (0..1); pass^k when ``trials`` > 1 lives in totals.
    score = Column(Float, nullable=True)
    totals = Column(JSON, nullable=True)
    progress = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), index=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentEvalResult(Base):
    """Per-item, per-trial outcome inside a run.

    Freezes the item's ``question``/``answer_spec`` at execution time (runs stay
    truthful after later item edits — I-1). Row previews are capped copies for
    the UI; verdict math ran on the full result sets. ``override_*`` columns are
    the human review trail (HUMAN-source verdict wins over the automated one).
    """

    __tablename__ = "ai_agent_eval_results"
    __table_args__ = (Index("ix_ai_agent_eval_result_run_item", "run_id", "item_id"),)

    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), index=True, nullable=False)
    item_id = Column(String(36), index=True, nullable=False)
    trial_index = Column(Integer, nullable=False, default=0)
    question = Column(Text, nullable=False)
    answer_type = Column(String(32), nullable=False)
    answer_spec = Column(JSON, nullable=False)
    agent_sql = Column(Text, nullable=True)
    agent_status = Column(String(32), nullable=True)
    agent_rows_preview = Column(JSON, nullable=True)
    gold_rows_preview = Column(JSON, nullable=True)
    #: Three-way automated verdict: pass | fail | needs_review | error.
    verdict = Column(String(32), index=True, nullable=False)
    verdict_source = Column(String(32), nullable=False, default="code")
    #: Named score reasons ("Column count mismatch…"), Wren-style.
    reasons = Column(JSON, nullable=True)
    matched_models = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    override_verdict = Column(String(32), nullable=True)
    override_by = Column(String(255), nullable=True)
    override_at = Column(DateTime(timezone=True), nullable=True)
    override_comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentEvalScore(Base):
    """One measured score on a result, shaped like OTel ``gen_ai.evaluation.result``.

    ``name``/``value`` (numeric) / ``label`` (categorical) / ``explanation`` map
    1:1 onto the OTel GenAI semconv attributes so a later OTLP exporter is a
    serializer, not a remodel. ``source`` follows the cross-tool consensus enum:
    code | llm_judge | human | api.
    """

    __tablename__ = "ai_agent_eval_scores"

    id = Column(String(36), primary_key=True)
    result_id = Column(String(36), index=True, nullable=False)
    name = Column(String(128), index=True, nullable=False)
    value = Column(Float, nullable=True)
    label = Column(String(255), nullable=True)
    explanation = Column(Text, nullable=True)
    source = Column(String(32), nullable=False, default="code")
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentPromptVersion(Base):
    """One immutable version of a named agent prompt (testing platform P2.1).

    Versions are append-only and monotonically numbered per ``name`` (the
    cross-tool consensus: versions are immutable, labels are the only mutable
    thing). The repo's ``prompts/*.md`` files remain the seed/default — a name
    with no promoted version resolves to its file (fail-safe fallback).
    """

    __tablename__ = "ai_agent_prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "name", "version", name="uq_ai_agent_prompt_version_name_version"
        ),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(128), index=True, nullable=False)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    comment = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentPromptLabel(Base):
    """A mutable label pointing at one prompt version (deploy = move label).

    ``production`` is the label the runtime resolver serves; promotion moves it,
    rollback moves it back, deleting it resets the prompt to its file default.
    """

    __tablename__ = "ai_agent_prompt_labels"
    __table_args__ = (
        UniqueConstraint("name", "label", name="uq_ai_agent_prompt_label"),
    )

    id = Column(String(36), primary_key=True)
    name = Column(String(128), index=True, nullable=False)
    label = Column(String(64), nullable=False)
    version_id = Column(String(36), nullable=False)
    updated_by = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class AiAgentLlmCall(Base):
    """One LLM call, appended per invocation (count + timing telemetry).

    Append-only metering: one row per ``ModelClient.chat`` invocation. The write
    is fail-open at the call site, so a missing row means a metering hiccup, never
    a dropped agent response. ``kind`` carries the call class ("chat" today) so a
    future embedding meter can share this table without a migration. Token columns
    are nullable — only providers that report usage (e.g. OpenAI) populate them.
    """

    __tablename__ = "ai_agent_llm_calls"

    id = Column(String(36), primary_key=True)
    # Indexed: every aggregate read filters/orders on the call time window.
    created_at = Column(DateTime(timezone=True), index=True, nullable=False)
    # Call class — "chat" today; the embedding meter seam writes "embedding" later.
    kind = Column(String(32), index=True, nullable=False, default="chat")
    provider = Column(String(32), nullable=False)
    model = Column(String(255), nullable=True)
    duration_ms = Column(Integer, nullable=False)
    ok = Column(Boolean, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
