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

"""Add ``ai_agent_document_blobs`` — raw upload bytes for postgres blob storage.

One row per stored file for ``AI_AGENT_DOCUMENT_STORAGE=postgres`` (deployments
with no writable disk and no S3). Dialect-agnostic ``LargeBinary`` (``bytea`` on
Postgres, BLOB on SQLite) so the mode also works against a dev SQLite database.

Revision ID: 0017_document_blobs
Revises: 0016_llm_call_log
Create Date: 2026-07-02 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_document_blobs"
down_revision = "0016_llm_call_log"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_document_blobs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("storage_key", sa.String(length=1024), primary_key=True),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        f"ix_{_TABLE}_document_id",
        _TABLE,
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_document_id", table_name=_TABLE)
    op.drop_table(_TABLE)
