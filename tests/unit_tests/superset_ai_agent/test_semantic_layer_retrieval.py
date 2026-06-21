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

from __future__ import annotations

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.schemas import AgentQueryRequest
from superset_ai_agent.semantic_layer.retrieval import retrieve_schema_context


def test_retrieve_schema_context_ranks_tables_and_metrics() -> None:
    retrieved = retrieve_schema_context(
        request=AgentQueryRequest(
            question="Show gross moves by stage",
            database_id=1,
            schema_name="sales",
        ),
        context=_context(),
        config=AgentConfig(wren_schema_table_candidate_limit=2),
        project_id="project-1",
    )

    assert [dataset.table_name for dataset in retrieved.context.datasets] == [
        "pipeline_moves",
        "stage_lookup",
    ]
    assert retrieved.retrieval.project_id == "project-1"
    assert retrieved.retrieval.schema_name == "sales"
    assert retrieved.retrieval.candidate_table_names == [
        "pipeline_moves",
        "stage_lookup",
    ]
    assert retrieved.retrieval.candidate_metric_names == ["gross_moves"]
    assert retrieved.retrieval.scanned_table_count == 4
    assert retrieved.retrieval.omitted_table_count == 2


def test_retrieve_schema_context_preserves_explicit_dataset_scope() -> None:
    context = _context()
    retrieved = retrieve_schema_context(
        request=AgentQueryRequest(
            question="Show gross moves by stage",
            database_id=1,
            schema_name="sales",
            dataset_ids=[10, 12],
        ),
        context=context,
        config=AgentConfig(wren_schema_table_candidate_limit=1),
    )

    assert retrieved.context == context
    assert retrieved.retrieval.candidate_table_names == [
        "pipeline_moves",
        "customers",
        "stage_lookup",
        "invoices",
    ]


def test_retrieve_schema_context_honors_token_budget() -> None:
    retrieved = retrieve_schema_context(
        request=AgentQueryRequest(
            question="Show gross moves by stage",
            database_id=1,
            schema_name="sales",
        ),
        context=_context(),
        config=AgentConfig(
            wren_schema_table_candidate_limit=4,
            wren_schema_context_token_budget=20,
        ),
    )

    assert [dataset.table_name for dataset in retrieved.context.datasets] == [
        "pipeline_moves"
    ]
    assert retrieved.retrieval.context_truncated is True
    assert retrieved.retrieval.omitted_table_count == 3


def _context() -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="warehouse", backend="postgresql"),
        datasets=[
            DatasetMetadata(
                id=10,
                table_name="pipeline_moves",
                schema_name="sales",
                database_id=1,
                description="Pipeline movement facts by sales stage.",
                columns=[
                    ColumnSummary(name="stage", type="VARCHAR"),
                    ColumnSummary(name="gross_moves", type="BIGINT"),
                ],
                metrics=[
                    MetricSummary(
                        name="gross_moves",
                        expression="SUM(gross_moves)",
                    )
                ],
            ),
            DatasetMetadata(
                id=11,
                table_name="customers",
                schema_name="sales",
                database_id=1,
                columns=[ColumnSummary(name="customer_name", type="VARCHAR")],
                metrics=[],
            ),
            DatasetMetadata(
                id=12,
                table_name="stage_lookup",
                schema_name="sales",
                database_id=1,
                description="Stage names and ordering.",
                columns=[ColumnSummary(name="stage", type="VARCHAR")],
                metrics=[],
            ),
            DatasetMetadata(
                id=13,
                table_name="invoices",
                schema_name="sales",
                database_id=1,
                columns=[ColumnSummary(name="invoice_amount", type="NUMERIC")],
                metrics=[MetricSummary(name="invoice_total", expression="SUM(total)")],
            ),
        ],
    )
