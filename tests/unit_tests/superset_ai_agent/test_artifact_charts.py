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

from superset_ai_agent.artifacts.charts import infer_chart_spec
from superset_ai_agent.artifacts.insights import profile_result
from superset_ai_agent.schemas import ExecutionResult


def test_infers_bar_chart_for_category_metric_result() -> None:
    result = ExecutionResult(
        columns=["stage", "gross_moves"],
        rows=[
            {"stage": "Qualified", "gross_moves": 120},
            {"stage": "Closed", "gross_moves": 20},
        ],
        row_count=2,
    )
    analysis = profile_result(
        result,
        question="Show gross moves by stage",
        row_limit=100,
    )

    spec = infer_chart_spec(
        question="Show gross moves by stage",
        result=result,
        analysis=analysis,
    )

    assert spec is not None
    assert spec.type == "bar"
    assert spec.encoding.x == "stage"
    assert spec.encoding.y == "gross_moves"


def test_infers_line_chart_for_time_metric_result() -> None:
    result = ExecutionResult(
        columns=["ds", "gross_moves"],
        rows=[
            {"ds": "2026-01-01", "gross_moves": 120},
            {"ds": "2026-01-02", "gross_moves": 140},
        ],
        row_count=2,
    )
    analysis = profile_result(
        result,
        question="Show gross moves by day",
        row_limit=100,
    )

    spec = infer_chart_spec(
        question="Show gross moves by day",
        result=result,
        analysis=analysis,
    )

    assert spec is not None
    assert spec.type == "line"
    assert spec.encoding.time == "ds"
    assert spec.encoding.y == "gross_moves"


def test_chart_spec_falls_back_to_table_when_no_dimension_metric_pair() -> None:
    result = ExecutionResult(
        columns=["stage"],
        rows=[{"stage": "Qualified"}],
        row_count=1,
    )
    analysis = profile_result(result, question="Show stages", row_limit=100)

    spec = infer_chart_spec(
        question="Show stages",
        result=result,
        analysis=analysis,
    )

    assert spec is not None
    assert spec.type == "table"


def test_chart_spec_is_none_for_empty_result() -> None:
    result = ExecutionResult(columns=["stage", "gross_moves"], rows=[], row_count=0)
    analysis = profile_result(result, question="Show stages", row_limit=100)

    spec = infer_chart_spec(
        question="Show stages",
        result=result,
        analysis=analysis,
    )

    assert spec is None
