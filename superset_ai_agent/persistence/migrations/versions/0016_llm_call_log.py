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

"""Add ``ai_agent_llm_calls`` — append-one-row-per-call LLM telemetry.

One row per ``ModelClient.chat`` invocation (count + duration + outcome, plus
token counts when the provider reports them). ``kind`` defaults to "chat" and
leaves a seam for a future embedding meter. ``created_at`` is indexed because
every aggregate read filters/orders on the time window.

Revision ID: 0016_llm_call_log
Revises: 0015_nl_sql_example_db_scope_and_refs
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_llm_call_log"
down_revision = "0015_nl_sql_example_db_scope_and_refs"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_llm_calls"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            server_default="chat",
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
    )
    op.create_index(f"ix_{_TABLE}_created_at", _TABLE, ["created_at"], unique=False)
    op.create_index(f"ix_{_TABLE}_kind", _TABLE, ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_kind", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_created_at", table_name=_TABLE)
    op.drop_table(_TABLE)
