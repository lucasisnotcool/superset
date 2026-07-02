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

"""DB-tied artifacts: database_uri_fingerprint on documents, memory, instructions.

Self-service connections give each user their own Superset ``Database`` row for
the same physical database, so ``database_id`` no longer identifies the shared
context. RAG documents, NL->SQL memory, and instructions become **DB-tied** —
keyed by the credential-free URI fingerprint (the key semantic projects already
use) so every user who can reach the physical database shares them.

All columns nullable: legacy rows (no fingerprint) keep their per-connection
``database_id`` / legacy ``scope_hash`` behavior, and reads match both keys.
No backfill — fingerprints require resolving each connection's URI through
Superset, which a migration cannot do; rows written after this revision carry
the fingerprint, and legacy rows converge as they are next updated.

Revision ID: 0018_db_tied_artifacts
Revises: 0017_document_blobs
Create Date: 2026-07-02 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_db_tied_artifacts"
down_revision = "0017_document_blobs"
branch_labels = None
depends_on = None

_COLUMN = "database_uri_fingerprint"
_TABLES = (
    "ai_agent_semantic_documents",
    "ai_agent_nl_sql_examples",
    "ai_agent_instructions",
)


def _index_name(table: str) -> str:
    return f"ix_{table}_{_COLUMN}"


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column(_COLUMN, sa.String(128), nullable=True))
        op.create_index(_index_name(table), table, [_COLUMN])


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(_index_name(table), table_name=table)
        op.drop_column(table, _COLUMN)
