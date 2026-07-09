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

"""Conversation rewrites: edit & resend, regenerate, fork, feedback, apply snapshots.

Substrate for plan_conversation_management_spec.md:

- ``ai_agent_messages.deleted_at`` — message soft-delete, so an edit/regenerate
  truncates the visible thread without destroying history (restore stays possible).
- ``ai_agent_messages.superseded_by_message_id`` — batch marker: every row
  soft-deleted by one rewrite carries the anchor user message's id, which is what
  undo and the prior-attempt pager key on.
- ``ai_agent_conversations.parent_conversation_id`` + ``forked_from_sequence`` —
  fork back-link ("Branch from here").
- ``ai_agent_nl_sql_examples.source_conversation_id`` + ``source_message_id`` —
  provenance for the learning-loop memory write, so truncating a turn can remove
  the example it produced instead of silently keeping/duplicating it.
- ``ai_agent_message_feedback`` — persisted per-message thumbs feedback.
- ``ai_agent_mdl_apply_snapshots`` — before-images captured when a Copilot
  changeset is applied, enabling "revert applied drafts".

All columns are additive and nullable, so a lagging deployment can run older code
against the migrated schema.

Revision ID: 0021_conversation_rewrites
Revises: 0020_prompt_registry
Create Date: 2026-07-08 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_conversation_rewrites"
down_revision = "0020_prompt_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_agent_messages",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_agent_messages",
        sa.Column("superseded_by_message_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_ai_agent_messages_superseded_by",
        "ai_agent_messages",
        ["superseded_by_message_id"],
    )

    op.add_column(
        "ai_agent_conversations",
        sa.Column("parent_conversation_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "ai_agent_conversations",
        sa.Column("forked_from_sequence", sa.Integer(), nullable=True),
    )

    op.add_column(
        "ai_agent_nl_sql_examples",
        sa.Column("source_conversation_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "ai_agent_nl_sql_examples",
        sa.Column("source_message_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_ai_agent_nl_sql_examples_source_message",
        "ai_agent_nl_sql_examples",
        ["source_message_id"],
    )

    op.create_table(
        "ai_agent_message_feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_agent_message_feedback_message",
        "ai_agent_message_feedback",
        ["message_id"],
    )
    op.create_index(
        "ix_ai_agent_message_feedback_conversation",
        "ai_agent_message_feedback",
        ["conversation_id"],
    )

    op.create_table(
        "ai_agent_mdl_apply_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("apply_group_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=True),
        sa.Column("before_content", sa.Text(), nullable=True),
        sa.Column("before_status", sa.String(length=32), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_agent_mdl_apply_snapshots_group",
        "ai_agent_mdl_apply_snapshots",
        ["apply_group_id"],
    )
    op.create_index(
        "ix_ai_agent_mdl_apply_snapshots_conversation",
        "ai_agent_mdl_apply_snapshots",
        ["conversation_id"],
    )
    op.create_index(
        "ix_ai_agent_mdl_apply_snapshots_project",
        "ai_agent_mdl_apply_snapshots",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_agent_mdl_apply_snapshots_project",
        table_name="ai_agent_mdl_apply_snapshots",
    )
    op.drop_index(
        "ix_ai_agent_mdl_apply_snapshots_conversation",
        table_name="ai_agent_mdl_apply_snapshots",
    )
    op.drop_index(
        "ix_ai_agent_mdl_apply_snapshots_group",
        table_name="ai_agent_mdl_apply_snapshots",
    )
    op.drop_table("ai_agent_mdl_apply_snapshots")
    op.drop_index(
        "ix_ai_agent_message_feedback_conversation",
        table_name="ai_agent_message_feedback",
    )
    op.drop_index(
        "ix_ai_agent_message_feedback_message",
        table_name="ai_agent_message_feedback",
    )
    op.drop_table("ai_agent_message_feedback")
    op.drop_index(
        "ix_ai_agent_nl_sql_examples_source_message",
        table_name="ai_agent_nl_sql_examples",
    )
    op.drop_column("ai_agent_nl_sql_examples", "source_message_id")
    op.drop_column("ai_agent_nl_sql_examples", "source_conversation_id")
    op.drop_column("ai_agent_conversations", "forked_from_sequence")
    op.drop_column("ai_agent_conversations", "parent_conversation_id")
    op.drop_index(
        "ix_ai_agent_messages_superseded_by",
        table_name="ai_agent_messages",
    )
    op.drop_column("ai_agent_messages", "superseded_by_message_id")
    op.drop_column("ai_agent_messages", "deleted_at")
