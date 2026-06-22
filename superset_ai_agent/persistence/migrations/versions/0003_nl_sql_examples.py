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

"""Add the NL->SQL example table (memory learning loop).

Revision ID: 0003_nl_sql_examples
Revises: 0002_schema_snapshots_and_jobs
Create Date: 2026-06-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_nl_sql_examples"
down_revision = "0002_schema_snapshots_and_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_nl_sql_examples",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("scope_hash", sa.String(length=128), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("semantic_sql", sa.Text(), nullable=False),
        sa.Column("native_sql", sa.Text(), nullable=False),
        sa.Column("result_meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_nl_sql_examples_owner_id",
        "ai_agent_nl_sql_examples",
        ["owner_id"],
    )
    op.create_index(
        "ix_ai_agent_nl_sql_examples_scope_hash",
        "ai_agent_nl_sql_examples",
        ["scope_hash"],
    )
    op.create_index(
        "ix_ai_agent_nl_sql_examples_project_id",
        "ai_agent_nl_sql_examples",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_agent_nl_sql_examples_project_id",
        table_name="ai_agent_nl_sql_examples",
    )
    op.drop_index(
        "ix_ai_agent_nl_sql_examples_scope_hash",
        table_name="ai_agent_nl_sql_examples",
    )
    op.drop_index(
        "ix_ai_agent_nl_sql_examples_owner_id",
        table_name="ai_agent_nl_sql_examples",
    )
    op.drop_table("ai_agent_nl_sql_examples")
