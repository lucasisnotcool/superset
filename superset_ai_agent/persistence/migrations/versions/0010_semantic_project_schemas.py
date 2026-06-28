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

"""Schema-set membership for multi-schema semantic projects (expand step).

Adds the normalized ``ai_agent_semantic_project_schemas`` table and backfills one
row per existing project from its (primary) ``schema_name``. This is the *expand*
step of an expand/contract migration: reads still come from the project's
``schema_name`` mirror, so the change is reversible and the project's existing
unique constraint is left untouched (its replacement is a later, separate step).

Revision ID: 0010_semantic_project_schemas
Revises: 0009_coverage_runs
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0010_semantic_project_schemas"
down_revision = "0009_coverage_runs"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_semantic_project_schemas"
_PROJECTS = "ai_agent_semantic_projects"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("schema_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "schema_name",
            name="uq_ai_agent_semantic_project_schema",
        ),
    )
    op.create_index(
        "ix_ai_agent_semantic_project_schemas_project_id", _TABLE, ["project_id"]
    )

    # Backfill: one membership row per existing project, from its primary schema.
    # Uses the project's own id as a stable, deterministic membership id (one
    # primary row per project) so re-running is idempotent under the unique key.
    bind = op.get_bind()
    projects = sa.table(
        _PROJECTS,
        sa.column("id", sa.String),
        sa.column("schema_name", sa.String),
    )
    # Read only the values we copy through; the project's stored ``created_at`` is
    # left uninterpreted (mixed tz formats across backends would break a typed read).
    rows = bind.execute(
        sa.select(projects.c.id, projects.c.schema_name)
    ).fetchall()
    if rows:
        created_at = datetime.now(timezone.utc)
        memberships = sa.table(
            _TABLE,
            sa.column("id", sa.String),
            sa.column("project_id", sa.String),
            sa.column("schema_name", sa.String),
            sa.column("position", sa.Integer),
            sa.column("created_at", sa.DateTime),
        )
        op.bulk_insert(
            memberships,
            [
                {
                    "id": row.id,
                    "project_id": row.id,
                    "schema_name": row.schema_name,
                    "position": 0,
                    "created_at": created_at,
                }
                for row in rows
                if row.schema_name
            ],
        )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_agent_semantic_project_schemas_project_id", table_name=_TABLE
    )
    op.drop_table(_TABLE)
