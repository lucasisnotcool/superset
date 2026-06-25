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

"""Create the document-chunk table backing uploaded-document RAG.

Persists per-document chunk records (text + offsets + checksum) so uploaded
``raw/`` documents can be embedded, retrieved, viewed, and de-duplicated. Vectors
themselves live in the document vector store (LanceDB), keyed by chunk id; this
table is the durable system-of-record.

NOTE (cross-plan interlock): this is the next migration after
``0006_drop_semantic_overlay``. The parallel Wren MDL Copilot versioning migration
must chain *after* this one (``down_revision = "0007_document_chunks"``), keeping a
single linear alembic history.

Revision ID: 0007_document_chunks
Revises: 0006_drop_semantic_overlay
Create Date: 2026-06-25 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_document_chunks"
down_revision = "0006_drop_semantic_overlay"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_document_chunks"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("embedded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_ai_agent_document_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_ai_agent_document_chunks_document_id",
        _TABLE,
        ["document_id"],
    )
    op.create_index(
        "ix_ai_agent_document_chunks_owner_id",
        _TABLE,
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_document_chunks_project_id",
        _TABLE,
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_document_chunks_checksum",
        _TABLE,
        ["checksum"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_agent_document_chunks_checksum", table_name=_TABLE)
    op.drop_index("ix_ai_agent_document_chunks_project_id", table_name=_TABLE)
    op.drop_index("ix_ai_agent_document_chunks_owner_id", table_name=_TABLE)
    op.drop_index("ix_ai_agent_document_chunks_document_id", table_name=_TABLE)
    op.drop_table(_TABLE)
