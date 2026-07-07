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

"""Curation-time consistency linter (C4, plan_sql_agent_doc_grounding_spec.md)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent MDL fixtures
from types import SimpleNamespace

from superset_ai_agent.semantic_layer.consistency import lint_project_consistency


def _file(path: str, content: dict) -> SimpleNamespace:
    return SimpleNamespace(path=path, content=json.dumps(content))


def _model_file() -> SimpleNamespace:
    return _file(
        "models/orders.json",
        {
            "models": [
                {
                    "name": "orders",
                    "tableReference": {"schema": "sales", "table": "orders"},
                    "columns": [
                        {"name": "amount", "type": "DOUBLE"},
                        {"name": "region", "type": "VARCHAR"},
                    ],
                }
            ]
        },
    )


def _golden_file(queries: list[dict]) -> SimpleNamespace:
    return _file("queries.json", {"queries": queries})


def _codes(report) -> list[str]:
    return [finding.code for finding in report.findings]


def test_clean_project_reports_no_findings() -> None:
    report = lint_project_consistency(
        project_id="p1",
        files=[
            _model_file(),
            _golden_file(
                [
                    {
                        "name": "orders by region",
                        "question": "orders by region?",
                        "semantic_sql": (
                            "SELECT region, SUM(amount) FROM orders GROUP BY region"
                        ),
                    }
                ]
            ),
        ],
        instructions=["Always exclude test accounts from orders"],
    )
    assert report.findings == []
    assert report.checked_golden_queries == 1
    assert report.checked_instructions == 1


def test_stale_golden_reference_is_flagged() -> None:
    report = lint_project_consistency(
        project_id="p1",
        files=[
            _model_file(),
            _golden_file(
                [
                    {
                        "name": "legacy",
                        "question": "legacy revenue?",
                        "semantic_sql": "SELECT * FROM retired_revenue_table",
                    }
                ]
            ),
        ],
    )
    assert "golden_unknown_reference" in _codes(report)
    finding = report.findings[0]
    assert finding.severity == "error"
    assert "retired_revenue_table" in finding.message


def test_conflicting_golden_duplicates_flagged() -> None:
    report = lint_project_consistency(
        project_id="p1",
        files=[
            _model_file(),
            _golden_file(
                [
                    {
                        "name": "a",
                        "question": "Orders by region?",
                        "semantic_sql": "SELECT region FROM orders",
                    },
                    {
                        "name": "b",
                        "question": "orders by region?",
                        "semantic_sql": "SELECT region, amount FROM orders",
                    },
                ]
            ),
        ],
    )
    assert "golden_conflicting_duplicates" in _codes(report)


def test_duplicate_metric_conflict_across_files() -> None:
    metric_v1 = {
        "name": "yield_rate",
        "baseObject": "orders",
        "measure": [{"name": "yield_rate", "expression": "SUM(a)/SUM(b)"}],
    }
    metric_v2 = {
        **metric_v1,
        "measure": [{"name": "yield_rate", "expression": "AVG(a)"}],
    }
    report = lint_project_consistency(
        project_id="p1",
        files=[
            _model_file(),
            _file("metrics/one.json", {"metrics": [metric_v1]}),
            _file("metrics/two.json", {"metrics": [metric_v2]}),
        ],
    )
    assert "duplicate_metric_conflict" in _codes(report)
    assert report.checked_metrics == 2
    # Identical re-definition is NOT a conflict.
    clean = lint_project_consistency(
        project_id="p1",
        files=[
            _model_file(),
            _file("metrics/one.json", {"metrics": [metric_v1]}),
            _file("metrics/two.json", {"metrics": [metric_v1]}),
        ],
    )
    assert "duplicate_metric_conflict" not in _codes(clean)


def test_instruction_unknown_identifier_is_warned_known_is_not() -> None:
    report = lint_project_consistency(
        project_id="p1",
        files=[_model_file()],
        instructions=[
            "Join through order_items_legacy for line detail",  # unknown
            "Prefer the orders model and the amount column",  # known words only
        ],
    )
    codes = _codes(report)
    assert codes.count("instruction_unknown_identifier") == 1
    assert report.findings[0].subject == "order_items_legacy"


def test_unparseable_artifacts_degrade_closed() -> None:
    bad_mdl = SimpleNamespace(path="models/bad.json", content="{not json")
    bad_golden = SimpleNamespace(path="queries.json", content="[]")
    report = lint_project_consistency(
        project_id="p1",
        files=[bad_mdl, _model_file(), bad_golden],
    )
    assert "golden_file_unparseable" in _codes(report)
    # The valid model file still contributes its names; no crash.
    assert report.checked_golden_queries == 0
