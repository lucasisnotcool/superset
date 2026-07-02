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

"""Add ``ai_agent_eval_*`` — Project Benchmarks (testing platform P0.1).

Five tables: benchmarks (project-scoped test sets), items (question + typed
answer spec), runs (scored executions with a CAS claim lifecycle), results
(per-item/per-trial outcomes with frozen specs + human override trail), and
scores (OTel ``gen_ai.evaluation.result``-shaped rows). Logical FKs only, per
the agent-table convention (cascades live in the store).

Revision ID: 0019_eval_benchmarks
Revises: 0018_db_tied_artifacts
Create Date: 2026-07-03 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_eval_benchmarks"
down_revision = "0018_db_tied_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_agent_eval_benchmarks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_agent_eval_benchmarks_project_id",
        "ai_agent_eval_benchmarks",
        ["project_id"],
    )
    op.create_index(
        "ix_ai_agent_eval_benchmarks_owner_id",
        "ai_agent_eval_benchmarks",
        ["owner_id"],
    )

    op.create_table(
        "ai_agent_eval_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("benchmark_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_type", sa.String(length=32), nullable=False),
        sa.Column("answer_spec", sa.JSON(), nullable=False),
        sa.Column("capability_tags", sa.JSON(), nullable=False),
        sa.Column("use_as_example", sa.Boolean(), nullable=False),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ai_agent_eval_items_benchmark_id",
        "ai_agent_eval_items",
        ["benchmark_id"],
    )

    op.create_table(
        "ai_agent_eval_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("benchmark_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trials", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("mdl_checksum", sa.String(length=128), nullable=True),
        sa.Column(
            "benchmark_checksum",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
        sa.Column("database_id", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("totals", sa.JSON(), nullable=True),
        sa.Column("progress", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_agent_eval_runs_benchmark_id", "ai_agent_eval_runs", ["benchmark_id"]
    )
    op.create_index(
        "ix_ai_agent_eval_runs_project_id", "ai_agent_eval_runs", ["project_id"]
    )
    op.create_index(
        "ix_ai_agent_eval_runs_owner_id", "ai_agent_eval_runs", ["owner_id"]
    )
    op.create_index("ix_ai_agent_eval_runs_status", "ai_agent_eval_runs", ["status"])
    op.create_index(
        "ix_ai_agent_eval_runs_created_at", "ai_agent_eval_runs", ["created_at"]
    )

    op.create_table(
        "ai_agent_eval_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("trial_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_type", sa.String(length=32), nullable=False),
        sa.Column("answer_spec", sa.JSON(), nullable=False),
        sa.Column("agent_sql", sa.Text(), nullable=True),
        sa.Column("agent_status", sa.String(length=32), nullable=True),
        sa.Column("agent_rows_preview", sa.JSON(), nullable=True),
        sa.Column("gold_rows_preview", sa.JSON(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column(
            "verdict_source",
            sa.String(length=32),
            nullable=False,
            server_default="code",
        ),
        sa.Column("reasons", sa.JSON(), nullable=True),
        sa.Column("matched_models", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("override_verdict", sa.String(length=32), nullable=True),
        sa.Column("override_by", sa.String(length=255), nullable=True),
        sa.Column("override_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("override_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_agent_eval_results_run_id", "ai_agent_eval_results", ["run_id"]
    )
    op.create_index(
        "ix_ai_agent_eval_results_item_id", "ai_agent_eval_results", ["item_id"]
    )
    op.create_index(
        "ix_ai_agent_eval_results_verdict", "ai_agent_eval_results", ["verdict"]
    )
    op.create_index(
        "ix_ai_agent_eval_result_run_item",
        "ai_agent_eval_results",
        ["run_id", "item_id"],
    )

    op.create_table(
        "ai_agent_eval_scores",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("result_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "source", sa.String(length=32), nullable=False, server_default="code"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_ai_agent_eval_scores_result_id", "ai_agent_eval_scores", ["result_id"]
    )
    op.create_index(
        "ix_ai_agent_eval_scores_name", "ai_agent_eval_scores", ["name"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_agent_eval_scores_name", table_name="ai_agent_eval_scores")
    op.drop_index(
        "ix_ai_agent_eval_scores_result_id", table_name="ai_agent_eval_scores"
    )
    op.drop_table("ai_agent_eval_scores")
    op.drop_index(
        "ix_ai_agent_eval_result_run_item", table_name="ai_agent_eval_results"
    )
    op.drop_index(
        "ix_ai_agent_eval_results_verdict", table_name="ai_agent_eval_results"
    )
    op.drop_index(
        "ix_ai_agent_eval_results_item_id", table_name="ai_agent_eval_results"
    )
    op.drop_index(
        "ix_ai_agent_eval_results_run_id", table_name="ai_agent_eval_results"
    )
    op.drop_table("ai_agent_eval_results")
    op.drop_index("ix_ai_agent_eval_runs_created_at", table_name="ai_agent_eval_runs")
    op.drop_index("ix_ai_agent_eval_runs_status", table_name="ai_agent_eval_runs")
    op.drop_index("ix_ai_agent_eval_runs_owner_id", table_name="ai_agent_eval_runs")
    op.drop_index("ix_ai_agent_eval_runs_project_id", table_name="ai_agent_eval_runs")
    op.drop_index(
        "ix_ai_agent_eval_runs_benchmark_id", table_name="ai_agent_eval_runs"
    )
    op.drop_table("ai_agent_eval_runs")
    op.drop_index(
        "ix_ai_agent_eval_items_benchmark_id", table_name="ai_agent_eval_items"
    )
    op.drop_table("ai_agent_eval_items")
    op.drop_index(
        "ix_ai_agent_eval_benchmarks_owner_id", table_name="ai_agent_eval_benchmarks"
    )
    op.drop_index(
        "ix_ai_agent_eval_benchmarks_project_id",
        table_name="ai_agent_eval_benchmarks",
    )
    op.drop_table("ai_agent_eval_benchmarks")
