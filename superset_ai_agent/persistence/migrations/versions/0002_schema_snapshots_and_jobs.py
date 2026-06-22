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

"""Add schema snapshot and async job tables.

Revision ID: 0002_schema_snapshots_and_jobs
Revises: 0001_initial_agent_tables
Create Date: 2026-06-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_schema_snapshots_and_jobs"
down_revision = "0001_initial_agent_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_schema_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("database_uri_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("catalog_name", sa.String(length=255), nullable=True),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("tables", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            name="uq_ai_agent_schema_snapshot_project",
        ),
    )
    op.create_index(
        "ix_ai_agent_schema_snapshots_project_id",
        "ai_agent_schema_snapshots",
        ["project_id"],
    )

    op.create_table(
        "ai_agent_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_jobs_status",
        "ai_agent_jobs",
        ["status"],
    )
    op.create_index(
        "ix_ai_agent_jobs_project_id",
        "ai_agent_jobs",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_jobs_owner_id",
        "ai_agent_jobs",
        ["owner_id"],
    )


def downgrade() -> None:
    op.drop_table("ai_agent_jobs")
    op.drop_table("ai_agent_schema_snapshots")
