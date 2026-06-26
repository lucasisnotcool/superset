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

"""Add ``kind`` + ``project_id`` discriminators to conversations.

Lets the shared conversation store distinguish AI SQL threads (``kind="sql"``)
from MDL Copilot threads (``kind="copilot"``) and bind the latter to a semantic
project. ``server_default="sql"`` backfills every pre-existing row so the AI SQL
agent's history is untouched. ``project_id`` is a plain indexed column, not a FK —
conversations outlive projects.

Revision ID: 0008_conversation_kind_project
Revises: 0007_document_chunks
Create Date: 2026-06-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_conversation_kind_project"
down_revision = "0007_document_chunks"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_conversations"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="sql",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column("project_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_ai_agent_conversations_kind", _TABLE, ["kind"])
    op.create_index(
        "ix_ai_agent_conversations_project_id",
        _TABLE,
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_agent_conversations_project_id", table_name=_TABLE)
    op.drop_index("ix_ai_agent_conversations_kind", table_name=_TABLE)
    op.drop_column(_TABLE, "project_id")
    op.drop_column(_TABLE, "kind")
