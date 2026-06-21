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

from superset_ai_agent.artifacts.insights import ResultAnalysis
from superset_ai_agent.schemas import ChartEncoding, ChartSpec, ExecutionResult


def infer_chart_spec(
    *,
    question: str,
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> ChartSpec | None:
    """Infer a small chart spec from returned rows."""

    if analysis.is_empty:
        return None
    title = _title_from_question(question)
    if can_render_line(analysis):
        return ChartSpec(
            type="line",
            title=title,
            encoding=ChartEncoding(
                x=analysis.time_column,
                y=analysis.primary_metric,
                time=analysis.time_column,
                label=analysis.time_column,
            ),
            options={"sort": "asc"},
        )
    if can_render_bar(analysis):
        category_count = _distinct_count(result, analysis.category_column)
        if category_count > 30:
            return ChartSpec(
                type="table",
                title=title,
                options={"reason": "too_many_categories"},
            )
        return ChartSpec(
            type="bar",
            title=title,
            encoding=ChartEncoding(
                x=analysis.category_column,
                y=analysis.primary_metric,
                label=analysis.category_column,
            ),
            options={"max_categories": 20, "sort": "desc"},
        )
    return ChartSpec(
        type="table",
        title=title,
        options={"reason": "no_chartable_dimension_metric_pair"},
    )


def can_render_bar(analysis: ResultAnalysis) -> bool:
    """Return true when categorical x and numeric y columns exist."""

    return bool(analysis.category_column and analysis.primary_metric)


def can_render_line(analysis: ResultAnalysis) -> bool:
    """Return true when time x and numeric y columns exist."""

    return bool(analysis.time_column and analysis.primary_metric)


def _distinct_count(result: ExecutionResult, column: str | None) -> int:
    if not column:
        return 0
    return len({row.get(column) for row in result.rows if row.get(column) is not None})


def _title_from_question(question: str) -> str | None:
    text = " ".join(question.strip().split())
    if not text:
        return None
    return text[:1].upper() + text[1:]
