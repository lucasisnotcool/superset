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

from superset_ai_agent.artifacts.insights import (
    build_artifact_bundle,
    compute_category_stats,
    profile_result,
)
from superset_ai_agent.schemas import ExecutionResult


def test_insight_generation_for_empty_result() -> None:
    result = ExecutionResult(columns=["stage", "gross_moves"], rows=[], row_count=0)

    bundle = build_artifact_bundle(
        question="Show gross moves by stage",
        result=result,
        row_limit=100,
    )

    assert bundle.answer_summary == "No rows were returned for this query."
    assert bundle.insight_cards[0].title == "No rows returned"
    assert bundle.insight_cards[0].severity == "warning"
    assert bundle.recommended_followups


def test_insight_generation_for_category_metric_result() -> None:
    result = ExecutionResult(
        columns=["stage", "gross_moves"],
        rows=[
            {"stage": "Qualified", "gross_moves": 120},
            {"stage": "Proposal", "gross_moves": 80},
            {"stage": "Closed", "gross_moves": 20},
        ],
        row_count=3,
    )

    bundle = build_artifact_bundle(
        question="Show gross moves by stage",
        result=result,
        row_limit=100,
    )

    assert "Qualified has the highest gross moves" in bundle.answer_summary
    assert [card.title for card in bundle.insight_cards] == [
        "Top category",
        "Spread",
        "Lowest category",
    ]
    assert bundle.insight_cards[0].category == "Qualified"
    assert bundle.insight_cards[0].value == "120"
    assert bundle.insight_cards[1].value == "100"


def test_insight_generation_for_single_row_result() -> None:
    result = ExecutionResult(
        columns=["gross_moves", "net_moves"],
        rows=[{"gross_moves": 120, "net_moves": 75}],
        row_count=1,
    )

    bundle = build_artifact_bundle(
        question="Show gross moves",
        result=result,
        row_limit=100,
    )

    assert [card.title for card in bundle.insight_cards] == [
        "gross moves",
        "net moves",
    ]
    assert bundle.insight_cards[0].value == "120"


def test_profile_marks_result_truncated_at_limit() -> None:
    result = ExecutionResult(
        columns=["stage", "gross_moves"],
        rows=[{"stage": "Qualified", "gross_moves": 120}],
        row_count=100,
    )

    analysis = profile_result(
        result,
        question="Show gross moves by stage",
        row_limit=100,
    )

    assert analysis.is_likely_truncated is True


def test_compute_category_stats_ignores_null_and_non_numeric_values() -> None:
    result = ExecutionResult(
        columns=["stage", "gross_moves"],
        rows=[
            {"stage": "Qualified", "gross_moves": "120"},
            {"stage": "Qualified", "gross_moves": None},
            {"stage": "Closed", "gross_moves": "not numeric"},
            {"stage": "Closed", "gross_moves": 20},
        ],
        row_count=4,
    )

    stats = compute_category_stats(
        result,
        category_column="stage",
        metric_column="gross_moves",
    )

    assert stats == [
        {"category": "Qualified", "value": 120},
        {"category": "Closed", "value": 20},
    ]
