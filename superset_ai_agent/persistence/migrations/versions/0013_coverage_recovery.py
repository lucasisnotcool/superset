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

"""Add coverage-recovery columns to coverage runs.

Links a coverage run to its chained recovery agent: the persisted recovery
conversation (whose changeset artifact is the gap-closing suggestion set), the
recovery job status, and a durable per-run dismissal of the "suggestions ready"
notification. All nullable: existing rows and runs without recovery carry none.

Revision ID: 0013_coverage_recovery
Revises: 0012_coverage_run_progress
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_coverage_recovery"
down_revision = "0012_coverage_run_progress"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_coverage_runs"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("recovery_conversation_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        _TABLE, sa.Column("recovery_status", sa.String(length=32), nullable=True)
    )
    op.add_column(
        _TABLE,
        sa.Column("recovery_dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "recovery_dismissed_at")
    op.drop_column(_TABLE, "recovery_status")
    op.drop_column(_TABLE, "recovery_conversation_id")
