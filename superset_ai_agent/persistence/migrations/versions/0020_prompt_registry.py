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

"""Add prompt registry tables — DB-backed prompt versions + labels (P2.1).

Immutable ``ai_agent_prompt_versions`` (append-only, per-name numbering) plus
mutable ``ai_agent_prompt_labels`` (``production`` served by the runtime
resolver; delete = reset to the repo file default).

Revision ID: 0020_prompt_registry
Revises: 0019_eval_benchmarks
Create Date: 2026-07-03 00:00:01.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_prompt_registry"
down_revision = "0019_eval_benchmarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_prompt_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "name", "version", name="uq_ai_agent_prompt_version_name_version"
        ),
    )
    op.create_index(
        "ix_ai_agent_prompt_versions_name", "ai_agent_prompt_versions", ["name"]
    )
    op.create_table(
        "ai_agent_prompt_labels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "label", name="uq_ai_agent_prompt_label"),
    )
    op.create_index(
        "ix_ai_agent_prompt_labels_name", "ai_agent_prompt_labels", ["name"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_agent_prompt_labels_name", table_name="ai_agent_prompt_labels")
    op.drop_table("ai_agent_prompt_labels")
    op.drop_index(
        "ix_ai_agent_prompt_versions_name", table_name="ai_agent_prompt_versions"
    )
    op.drop_table("ai_agent_prompt_versions")
