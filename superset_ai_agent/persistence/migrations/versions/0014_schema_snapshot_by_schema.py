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

"""Add a schema-qualified ``tables_by_schema`` column to schema snapshots (F3).

The snapshot keeps physical (schema-aware) MDL validation working when live
Superset metadata is unavailable. The flat ``tables`` map collapses same-named
tables across schemas, so a multi-schema project's outage fallback was
schema-blind. ``tables_by_schema`` (schema → table → columns) restores
schema-aware validation. Nullable: existing rows and single-schema snapshots
leave it empty and degrade closed to the flat behaviour.

Revision ID: 0014_schema_snapshot_by_schema
Revises: 0013_coverage_recovery
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_schema_snapshot_by_schema"
down_revision = "0013_coverage_recovery"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_schema_snapshots"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("tables_by_schema", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "tables_by_schema")
