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

"""Create initial Superset AI agent persistence tables.

Revision ID: 0001_initial_agent_tables
Revises:
Create Date: 2026-06-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_agent_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("database_id", sa.Integer(), nullable=False),
        sa.Column("catalog_name", sa.String(length=255), nullable=True),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_conversations_owner_id",
        "ai_agent_conversations",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_conversations_updated_at",
        "ai_agent_conversations",
        ["updated_at"],
    )

    op.create_table(
        "ai_agent_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_agent_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_messages_conversation_id",
        "ai_agent_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_ai_agent_messages_owner_id",
        "ai_agent_messages",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_message_conversation_seq",
        "ai_agent_messages",
        ["conversation_id", "sequence"],
    )

    op.create_table(
        "ai_agent_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("sql", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["ai_agent_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_artifacts_message_id",
        "ai_agent_artifacts",
        ["message_id"],
    )
    op.create_index(
        "ix_ai_agent_artifacts_owner_id",
        "ai_agent_artifacts",
        ["owner_id"],
    )

    op.create_table(
        "ai_agent_semantic_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("database_id", sa.Integer(), nullable=False),
        sa.Column("catalog_name", sa.String(length=255), nullable=True),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("dataset_ids", sa.JSON(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("storage_uri", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_text_preview", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_semantic_documents_project_id",
        "ai_agent_semantic_documents",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_documents_owner_id",
        "ai_agent_semantic_documents",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_documents_database_id",
        "ai_agent_semantic_documents",
        ["database_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_documents_checksum",
        "ai_agent_semantic_documents",
        ["checksum"],
    )
    op.create_index(
        "ix_ai_agent_semantic_documents_status",
        "ai_agent_semantic_documents",
        ["status"],
    )

    op.create_table(
        "ai_agent_semantic_updates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("reviewer_id", sa.String(length=255), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["ai_agent_semantic_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_semantic_updates_project_id",
        "ai_agent_semantic_updates",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_updates_document_id",
        "ai_agent_semantic_updates",
        ["document_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_updates_owner_id",
        "ai_agent_semantic_updates",
        ["owner_id"],
    )

    op.create_table(
        "ai_agent_semantic_layer_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("database_id", sa.Integer(), nullable=False),
        sa.Column("catalog_name", sa.String(length=255), nullable=True),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("dataset_ids", sa.JSON(), nullable=False),
        sa.Column("scope_hash", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("mdl", sa.JSON(), nullable=True),
        sa.Column("wren_context", sa.JSON(), nullable=True),
        sa.Column("source_update_ids", sa.JSON(), nullable=False),
        sa.Column("published_semantic_layer_uuid", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_semantic_layer_versions_project_id",
        "ai_agent_semantic_layer_versions",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_layer_versions_owner_id",
        "ai_agent_semantic_layer_versions",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_layer_versions_database_id",
        "ai_agent_semantic_layer_versions",
        ["database_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_layer_versions_scope_hash",
        "ai_agent_semantic_layer_versions",
        ["scope_hash"],
    )

    op.create_table(
        "ai_agent_wren_context_cache",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("scope_hash", sa.String(length=128), nullable=False),
        sa.Column("question_hash", sa.String(length=128), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_wren_context_cache_project_id",
        "ai_agent_wren_context_cache",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_wren_context_cache_owner_id",
        "ai_agent_wren_context_cache",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_wren_context_cache_scope_hash",
        "ai_agent_wren_context_cache",
        ["scope_hash"],
    )
    op.create_index(
        "ix_ai_agent_wren_context_cache_question_hash",
        "ai_agent_wren_context_cache",
        ["question_hash"],
    )
    op.create_index(
        "ix_ai_agent_wren_context_cache_expires_at",
        "ai_agent_wren_context_cache",
        ["expires_at"],
    )

    op.create_table(
        "ai_agent_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_events_project_id",
        "ai_agent_events",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_events_owner_id",
        "ai_agent_events",
        ["owner_id"],
    )
    op.create_index("ix_ai_agent_events_type", "ai_agent_events", ["type"])
    op.create_index(
        "ix_ai_agent_events_created_at",
        "ai_agent_events",
        ["created_at"],
    )

    op.create_table(
        "ai_agent_semantic_projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("database_uri_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("database_backend", sa.String(length=255), nullable=True),
        sa.Column("database_label", sa.String(length=255), nullable=True),
        sa.Column("catalog_name", sa.String(length=255), nullable=True),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("schema_display_name", sa.String(length=255), nullable=True),
        sa.Column("default_database_id", sa.Integer(), nullable=True),
        sa.Column("visibility", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("current_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "database_uri_fingerprint",
            "catalog_name",
            "schema_name",
            "deleted_at",
            name="uq_ai_agent_semantic_project_scope_deleted",
        ),
    )
    op.create_index(
        "ix_ai_agent_semantic_projects_owner_id",
        "ai_agent_semantic_projects",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_projects_database_uri_fingerprint",
        "ai_agent_semantic_projects",
        ["database_uri_fingerprint"],
    )
    op.create_index(
        "ix_ai_agent_semantic_projects_schema_name",
        "ai_agent_semantic_projects",
        ["schema_name"],
    )

    op.create_table(
        "ai_agent_semantic_project_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("grantee_type", sa.String(length=64), nullable=False),
        sa.Column("grantee_id", sa.String(length=255), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_semantic_project_grants_project_id",
        "ai_agent_semantic_project_grants",
        ["project_id"],
    )

    op.create_table(
        "ai_agent_semantic_access_proofs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("proof_type", sa.String(length=64), nullable=False),
        sa.Column("database_id", sa.Integer(), nullable=True),
        sa.Column("catalog_names", sa.JSON(), nullable=False),
        sa.Column("schema_names", sa.JSON(), nullable=False),
        sa.Column("dataset_ids", sa.JSON(), nullable=False),
        sa.Column("database_uri_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("access_level", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_semantic_access_proofs_owner_id",
        "ai_agent_semantic_access_proofs",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_access_proofs_database_uri_fingerprint",
        "ai_agent_semantic_access_proofs",
        ["database_uri_fingerprint"],
    )

    op.create_table(
        "ai_agent_semantic_mdl_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=True),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_semantic_mdl_files_project_id",
        "ai_agent_semantic_mdl_files",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_semantic_mdl_files_status",
        "ai_agent_semantic_mdl_files",
        ["status"],
    )
    op.create_index(
        "ix_ai_agent_semantic_mdl_files_checksum",
        "ai_agent_semantic_mdl_files",
        ["checksum"],
    )
    op.create_index(
        "ix_ai_agent_semantic_mdl_project_path",
        "ai_agent_semantic_mdl_files",
        ["project_id", "path"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_agent_semantic_mdl_project_path",
        table_name="ai_agent_semantic_mdl_files",
    )
    op.drop_index(
        "ix_ai_agent_semantic_mdl_files_checksum",
        table_name="ai_agent_semantic_mdl_files",
    )
    op.drop_index(
        "ix_ai_agent_semantic_mdl_files_status",
        table_name="ai_agent_semantic_mdl_files",
    )
    op.drop_index(
        "ix_ai_agent_semantic_mdl_files_project_id",
        table_name="ai_agent_semantic_mdl_files",
    )
    op.drop_table("ai_agent_semantic_mdl_files")
    op.drop_index(
        "ix_ai_agent_semantic_access_proofs_database_uri_fingerprint",
        table_name="ai_agent_semantic_access_proofs",
    )
    op.drop_index(
        "ix_ai_agent_semantic_access_proofs_owner_id",
        table_name="ai_agent_semantic_access_proofs",
    )
    op.drop_table("ai_agent_semantic_access_proofs")
    op.drop_index(
        "ix_ai_agent_semantic_project_grants_project_id",
        table_name="ai_agent_semantic_project_grants",
    )
    op.drop_table("ai_agent_semantic_project_grants")
    op.drop_index(
        "ix_ai_agent_semantic_projects_schema_name",
        table_name="ai_agent_semantic_projects",
    )
    op.drop_index(
        "ix_ai_agent_semantic_projects_database_uri_fingerprint",
        table_name="ai_agent_semantic_projects",
    )
    op.drop_index(
        "ix_ai_agent_semantic_projects_owner_id",
        table_name="ai_agent_semantic_projects",
    )
    op.drop_table("ai_agent_semantic_projects")
    op.drop_index("ix_ai_agent_events_created_at", table_name="ai_agent_events")
    op.drop_index("ix_ai_agent_events_type", table_name="ai_agent_events")
    op.drop_index("ix_ai_agent_events_owner_id", table_name="ai_agent_events")
    op.drop_index("ix_ai_agent_events_project_id", table_name="ai_agent_events")
    op.drop_table("ai_agent_events")
    op.drop_index(
        "ix_ai_agent_wren_context_cache_expires_at",
        table_name="ai_agent_wren_context_cache",
    )
    op.drop_index(
        "ix_ai_agent_wren_context_cache_question_hash",
        table_name="ai_agent_wren_context_cache",
    )
    op.drop_index(
        "ix_ai_agent_wren_context_cache_scope_hash",
        table_name="ai_agent_wren_context_cache",
    )
    op.drop_index(
        "ix_ai_agent_wren_context_cache_owner_id",
        table_name="ai_agent_wren_context_cache",
    )
    op.drop_index(
        "ix_ai_agent_wren_context_cache_project_id",
        table_name="ai_agent_wren_context_cache",
    )
    op.drop_table("ai_agent_wren_context_cache")
    op.drop_index(
        "ix_ai_agent_semantic_layer_versions_scope_hash",
        table_name="ai_agent_semantic_layer_versions",
    )
    op.drop_index(
        "ix_ai_agent_semantic_layer_versions_database_id",
        table_name="ai_agent_semantic_layer_versions",
    )
    op.drop_index(
        "ix_ai_agent_semantic_layer_versions_owner_id",
        table_name="ai_agent_semantic_layer_versions",
    )
    op.drop_index(
        "ix_ai_agent_semantic_layer_versions_project_id",
        table_name="ai_agent_semantic_layer_versions",
    )
    op.drop_table("ai_agent_semantic_layer_versions")
    op.drop_index(
        "ix_ai_agent_semantic_updates_owner_id",
        table_name="ai_agent_semantic_updates",
    )
    op.drop_index(
        "ix_ai_agent_semantic_updates_document_id",
        table_name="ai_agent_semantic_updates",
    )
    op.drop_index(
        "ix_ai_agent_semantic_updates_project_id",
        table_name="ai_agent_semantic_updates",
    )
    op.drop_table("ai_agent_semantic_updates")
    op.drop_index(
        "ix_ai_agent_semantic_documents_status",
        table_name="ai_agent_semantic_documents",
    )
    op.drop_index(
        "ix_ai_agent_semantic_documents_checksum",
        table_name="ai_agent_semantic_documents",
    )
    op.drop_index(
        "ix_ai_agent_semantic_documents_database_id",
        table_name="ai_agent_semantic_documents",
    )
    op.drop_index(
        "ix_ai_agent_semantic_documents_owner_id",
        table_name="ai_agent_semantic_documents",
    )
    op.drop_index(
        "ix_ai_agent_semantic_documents_project_id",
        table_name="ai_agent_semantic_documents",
    )
    op.drop_table("ai_agent_semantic_documents")
    op.drop_index("ix_ai_agent_artifacts_owner_id", table_name="ai_agent_artifacts")
    op.drop_index("ix_ai_agent_artifacts_message_id", table_name="ai_agent_artifacts")
    op.drop_table("ai_agent_artifacts")
    op.drop_index(
        "ix_ai_agent_message_conversation_seq",
        table_name="ai_agent_messages",
    )
    op.drop_index("ix_ai_agent_messages_owner_id", table_name="ai_agent_messages")
    op.drop_index(
        "ix_ai_agent_messages_conversation_id",
        table_name="ai_agent_messages",
    )
    op.drop_table("ai_agent_messages")
    op.drop_index(
        "ix_ai_agent_conversations_updated_at",
        table_name="ai_agent_conversations",
    )
    op.drop_index(
        "ix_ai_agent_conversations_owner_id",
        table_name="ai_agent_conversations",
    )
    op.drop_table("ai_agent_conversations")
