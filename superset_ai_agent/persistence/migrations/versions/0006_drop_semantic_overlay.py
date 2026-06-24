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

"""Drop the legacy semantic-overlay tables (C6 removal).

The heuristic document-review -> approved-updates -> rebuilt-overlay-version ->
query-time merge pathway is removed in favor of the single MDL/enrichment source.
This drops its three now-unused tables: proposed/reviewed updates, overlay
versions, and the never-populated wren-context cache.

Revision ID: 0006_drop_semantic_overlay
Revises: 0005_instructions
Create Date: 2026-06-24 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_drop_semantic_overlay"
down_revision = "0005_instructions"
branch_labels = None
depends_on = None

_TABLES = (
    "ai_agent_semantic_updates",
    "ai_agent_semantic_layer_versions",
    "ai_agent_wren_context_cache",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in _TABLES:
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
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
        sa.Column(
            "published_semantic_layer_uuid", sa.String(length=36), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
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
