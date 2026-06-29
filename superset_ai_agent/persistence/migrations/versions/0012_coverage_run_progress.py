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

"""Add a ``progress`` column to coverage runs for live progress (Feature C).

Holds the coarse in-flight progress (stage, detail, current/total, phase) of a
running background coverage audit so the editor badge can surface "extracting
2/5" / "judging 142 claims" instead of a bare "analysing…". Nullable: existing
rows and terminal runs carry no progress.

Revision ID: 0012_coverage_run_progress
Revises: 0011_project_slug_identity
Create Date: 2026-06-29 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_coverage_run_progress"
down_revision = "0011_project_slug_identity"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_coverage_runs"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("progress", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "progress")
