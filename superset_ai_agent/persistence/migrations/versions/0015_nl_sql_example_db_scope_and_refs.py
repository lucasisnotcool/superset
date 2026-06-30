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

"""Database-scope key + referenced-table provenance for NL->SQL memory (F1/F2).

NL->SQL memory moves from a per-user key ``(owner_id, scope_hash)`` to a shared
**database-level** pool, and recall becomes access-aware: a recalled pair is only
surfaced when the requesting user can reach every table it references. This adds:

- ``database_id`` — the new sharing key (one pool per database connection).
- ``referenced_tables`` / ``referenced_schemas`` — the physical references a pair
  touches, captured at store time, used to RBAC-filter recall (fail closed).

All nullable. Legacy rows (opaque ``scope_hash``, no ``database_id``, no refs)
cannot be deterministically re-keyed; they degrade closed — excluded from recall
by the fail-closed access filter — and the pool re-accumulates from new runs.
``owner_id`` is retained as authorship metadata only (no longer a key).

Revision ID: 0015_nl_sql_example_db_scope_and_refs
Revises: 0014_schema_snapshot_by_schema
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_nl_sql_example_db_scope_and_refs"
down_revision = "0014_schema_snapshot_by_schema"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_nl_sql_examples"
_INDEX = "ix_ai_agent_nl_sql_examples_database_id"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("database_id", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("referenced_tables", sa.JSON(), nullable=True))
    op.add_column(_TABLE, sa.Column("referenced_schemas", sa.JSON(), nullable=True))
    op.create_index(_INDEX, _TABLE, ["database_id"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "referenced_schemas")
    op.drop_column(_TABLE, "referenced_tables")
    op.drop_column(_TABLE, "database_id")
