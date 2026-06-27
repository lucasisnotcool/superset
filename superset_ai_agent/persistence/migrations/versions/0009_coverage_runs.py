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

"""Create the coverage-run table backing background MDL coverage (Feature B).

Persists each background coverage run over the active MDL directory: its target
``mdl_checksum``/``docs_checksum`` (idempotency + supersession key), ``status``
(pending/running/complete/failed/superseded), denormalized ``score``, and the
full ``CoverageReport`` JSON. The latest ``complete`` row is what the provenance
dialog opens and the editor badge reads.

Revision ID: 0009_coverage_runs
Revises: 0008_conversation_kind_project
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_coverage_runs"
down_revision = "0008_conversation_kind_project"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_coverage_runs"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("mdl_checksum", sa.String(length=128), nullable=False),
        sa.Column("docs_checksum", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_agent_coverage_runs_project_id", _TABLE, ["project_id"]
    )
    op.create_index("ix_ai_agent_coverage_runs_owner_id", _TABLE, ["owner_id"])
    op.create_index(
        "ix_ai_agent_coverage_runs_mdl_checksum", _TABLE, ["mdl_checksum"]
    )
    op.create_index("ix_ai_agent_coverage_runs_status", _TABLE, ["status"])
    op.create_index(
        "ix_ai_agent_coverage_runs_created_at", _TABLE, ["created_at"]
    )
    op.create_index(
        "ix_ai_agent_coverage_runs_updated_at", _TABLE, ["updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_agent_coverage_runs_updated_at", table_name=_TABLE)
    op.drop_index("ix_ai_agent_coverage_runs_created_at", table_name=_TABLE)
    op.drop_index("ix_ai_agent_coverage_runs_status", table_name=_TABLE)
    op.drop_index("ix_ai_agent_coverage_runs_mdl_checksum", table_name=_TABLE)
    op.drop_index("ix_ai_agent_coverage_runs_owner_id", table_name=_TABLE)
    op.drop_index("ix_ai_agent_coverage_runs_project_id", table_name=_TABLE)
    op.drop_table(_TABLE)
