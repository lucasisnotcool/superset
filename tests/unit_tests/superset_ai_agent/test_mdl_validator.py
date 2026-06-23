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

"""Structural + physical MDL validation over wren-core's native JSON shape."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_mdl,
    validate_project_manifest,
)


def mdl(**sections: Any) -> str:
    """Serialize a native MDL manifest (camelCase) to JSON content."""

    return json.dumps(sections)


def _schema_index() -> SchemaIndex:
    return SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=1,
                    table_name="deals",
                    database_id=1,
                    columns=[
                        ColumnSummary(name="stage"),
                        ColumnSummary(name="gross_moves"),
                    ],
                    metrics=[],
                ),
                DatasetMetadata(
                    id=2,
                    table_name="sites",
                    database_id=1,
                    columns=[ColumnSummary(name="site_id")],
                    metrics=[],
                ),
            ],
        )
    )


def test_valid_model_passes_structural_validation() -> None:
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [{"name": "stage", "type": "VARCHAR"}],
                }
            ]
        )
    )
    assert result.valid is True
    assert result.messages == []


def test_invalid_join_type_is_error() -> None:
    result = validate_mdl(
        mdl(
            models=[{"name": "deals", "columns": [{"name": "stage", "type": "v"}]}],
            relationships=[
                {
                    "name": "deals_sites",
                    "models": ["deals", "sites"],
                    "joinType": "SIDEWAYS",
                }
            ],
        )
    )
    assert result.valid is False
    assert any(m.code == "invalid_join_type" for m in result.messages)


def test_duplicate_column_is_error() -> None:
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [
                        {"name": "stage", "type": "v"},
                        {"name": "stage", "type": "v"},
                    ],
                }
            ]
        )
    )
    assert result.valid is False
    assert any(m.code == "duplicate_column" for m in result.messages)


def test_calculated_column_requires_expression() -> None:
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [{"name": "derived", "isCalculated": True}],
                }
            ]
        )
    )
    assert result.valid is False
    assert any(m.code == "calculated_requires_expression" for m in result.messages)


def test_physical_validation_flags_unknown_table() -> None:
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "ghosts",
                    "tableReference": {"table": "ghosts"},
                    "columns": [{"name": "stage", "type": "v"}],
                }
            ]
        ),
        schema_index=_schema_index(),
    )
    assert result.valid is False
    assert any(m.code == "unknown_table" for m in result.messages)


def test_physical_validation_flags_hallucinated_column() -> None:
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [
                        {"name": "stage", "type": "v"},
                        {"name": "invented_total", "type": "v"},
                    ],
                }
            ]
        ),
        schema_index=_schema_index(),
    )
    assert result.valid is False
    unknown = [m for m in result.messages if m.code == "unknown_column"]
    assert unknown
    assert "invented_total" in unknown[0].message


def test_physical_validation_allows_calculated_column() -> None:
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [
                        {"name": "stage", "type": "v"},
                        {
                            "name": "total",
                            "type": "DOUBLE",
                            "isCalculated": True,
                            "expression": "SUM(gross_moves)",
                        },
                    ],
                }
            ]
        ),
        schema_index=_schema_index(),
    )
    assert result.valid is True


def test_schema_index_from_snapshot_validates_like_live() -> None:
    index = _schema_index()
    snapshot_index = SchemaIndex.from_snapshot(index.to_tables())

    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [{"name": "ghost", "type": "v"}],
                }
            ]
        ),
        schema_index=snapshot_index,
    )
    assert result.valid is False
    assert any(m.code == "unknown_column" for m in result.messages)


def test_column_without_type_is_flagged_structurally() -> None:
    # W5: a typeless column is caught structurally with a readable message,
    # before it can reach wren-core's opaque "missing field `type`" serde error.
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [{"name": "stage"}],  # no type
                }
            ]
        )
    )
    assert result.valid is False
    type_errors = [m for m in result.messages if m.code == "column_without_type"]
    assert type_errors
    assert "deals.stage" in type_errors[0].message


def test_relationship_column_may_omit_type() -> None:
    # A relationship column references another model and legitimately has no type.
    result = validate_mdl(
        mdl(
            models=[
                {
                    "name": "deals",
                    "tableReference": {"table": "deals"},
                    "columns": [
                        {"name": "amount", "type": "DOUBLE"},
                        {"name": "customer", "relationship": "deal_customer"},
                    ],
                }
            ]
        )
    )
    assert not any(m.code == "column_without_type" for m in result.messages)


def test_json_parse_error_is_reported() -> None:
    result = validate_mdl('{"models": [')
    assert result.valid is False
    assert result.messages[0].code == "json_parse_error"


_DEALS = {
    "name": "deals",
    "tableReference": {"table": "deals"},
    "columns": [{"name": "amount", "type": "DOUBLE"}],
}


def test_relationship_unresolved_is_warning_per_file_error_in_project() -> None:
    deals = mdl(
        models=[
            {
                "name": "deals",
                "tableReference": {"table": "deals"},
                "columns": [{"name": "stage", "type": "v"}],
            }
        ],
        relationships=[
            {
                "name": "deals_sites",
                "models": ["deals", "sites"],
                "joinType": "MANY_TO_ONE",
            }
        ],
    )
    per_file = validate_mdl(deals)
    assert per_file.valid is True
    assert any(
        m.code == "unresolved_relationship" and m.severity == "warning"
        for m in per_file.messages
    )

    sites = mdl(
        models=[
            {
                "name": "sites",
                "tableReference": {"table": "sites"},
                "columns": [{"name": "site_id", "type": "v"}],
            }
        ]
    )
    project = validate_project_manifest([deals, sites])
    assert project.valid is True

    project_missing = validate_project_manifest([deals])
    assert project_missing.valid is False
    assert any(
        m.code == "unresolved_relationship" and m.severity == "error"
        for m in project_missing.messages
    )


def test_dedup_models_supersedes_older_copy_instead_of_erroring() -> None:
    # W4: re-emitting an existing model (the enrichment cascade) must not fail as
    # duplicate_model when dedup is on; the newest definition wins, with an info.
    older = mdl(
        models=[
            {
                "name": "deals",
                "tableReference": {"table": "deals"},
                "columns": [{"name": "amount", "type": "DOUBLE"}],
            }
        ]
    )
    newer = mdl(
        models=[
            {
                "name": "deals",
                "description": "Enriched deals",
                "tableReference": {"table": "deals"},
                "columns": [{"name": "amount", "type": "DOUBLE"}],
            }
        ]
    )

    without_dedup = validate_project_manifest([older, newer])
    assert without_dedup.valid is False
    assert any(m.code == "duplicate_model" for m in without_dedup.messages)

    with_dedup = validate_project_manifest([older, newer], dedup_models=True)
    assert with_dedup.valid is True
    assert any(m.code == "model_superseded" for m in with_dedup.messages)
    assert not any(m.code == "duplicate_model" for m in with_dedup.messages)


def test_valid_metric_passes_structural_validation() -> None:
    result = validate_mdl(
        mdl(
            models=[_DEALS],
            metrics=[
                {
                    "name": "total_amount",
                    "baseObject": "deals",
                    "expression": "SUM(amount)",
                }
            ],
        )
    )
    assert result.valid is True
    assert result.messages == []


def test_metric_only_file_is_not_empty_root() -> None:
    result = validate_mdl(mdl(metrics=[{"name": "total", "expression": "SUM(amount)"}]))
    assert not any(m.code == "empty_root" for m in result.messages)


def test_metric_without_measure_is_warning() -> None:
    result = validate_mdl(
        mdl(models=[_DEALS], metrics=[{"name": "total_amount", "baseObject": "deals"}])
    )
    assert result.valid is True
    assert any(m.code == "metric_without_measure" for m in result.messages)


def test_metric_unresolved_base_is_warning_per_file_error_in_project() -> None:
    content = mdl(
        models=[_DEALS],
        metrics=[
            {"name": "total_amount", "baseObject": "ghost", "expression": "SUM(amount)"}
        ],
    )
    per_file = validate_mdl(content)
    assert per_file.valid is True
    assert any(
        m.code == "unresolved_metric_base" and m.severity == "warning"
        for m in per_file.messages
    )
    project = validate_project_manifest([content])
    assert project.valid is False
    assert any(
        m.code == "unresolved_metric_base" and m.severity == "error"
        for m in project.messages
    )


def test_duplicate_metric_name_is_error() -> None:
    result = validate_mdl(
        mdl(
            models=[_DEALS],
            metrics=[
                {"name": "total", "expression": "SUM(amount)"},
                {"name": "total", "expression": "COUNT(*)"},
            ],
        )
    )
    assert result.valid is False
    assert any(m.code == "duplicate_metric" for m in result.messages)


def test_cube_without_measures_is_warning() -> None:
    result = validate_mdl(
        mdl(models=[_DEALS], cubes=[{"name": "deal_cube", "baseObject": "deals"}])
    )
    assert result.valid is True
    assert any(m.code == "cube_without_measures" for m in result.messages)


def test_cube_without_base_is_error() -> None:
    # wren-core requires every cube to declare a baseObject (F4).
    result = validate_mdl(
        mdl(
            models=[_DEALS],
            cubes=[
                {
                    "name": "deal_cube",
                    "measures": [
                        {
                            "name": "total",
                            "type": "DOUBLE",
                            "expression": "SUM(amount)",
                        }
                    ],
                }
            ],
        )
    )
    assert result.valid is False
    assert any(m.code == "cube_without_base" for m in result.messages)


def test_cube_measure_requires_type_and_expression() -> None:
    # A measure must carry {name, type, expression}; missing fields make the
    # manifest unloadable by wren-core, so they are errors (not warnings).
    result = validate_mdl(
        mdl(
            models=[_DEALS],
            cubes=[
                {
                    "name": "deal_cube",
                    "baseObject": "deals",
                    "measures": [{"name": "total"}],
                }
            ],
        )
    )
    assert result.valid is False
    codes = {m.code for m in result.messages}
    assert "cube_measure_without_type" in codes
    assert "cube_measure_without_expression" in codes


def test_cube_unresolved_base_is_error_in_project() -> None:
    content = mdl(
        models=[_DEALS],
        cubes=[
            {
                "name": "deal_cube",
                "baseObject": "ghost",
                "measures": [
                    {"name": "total", "type": "DOUBLE", "expression": "SUM(amount)"}
                ],
            }
        ],
    )
    project = validate_project_manifest([content])
    assert project.valid is False
    assert any(
        m.code == "unresolved_cube_base" and m.severity == "error"
        for m in project.messages
    )


def _cube(**extra: Any) -> dict[str, Any]:
    cube = {
        "name": "deal_cube",
        "baseObject": "deals",
        "measures": [
            {"name": "total", "type": "DOUBLE", "expression": "SUM(amount)"}
        ],
    }
    cube.update(extra)
    return cube


def test_valid_cube_passes_clean() -> None:
    # A cube shaped exactly as wren-core requires raises no cube findings.
    result = validate_mdl(
        mdl(
            models=[_DEALS],
            cubes=[
                _cube(
                    dimensions=[
                        {"name": "region", "type": "VARCHAR", "expression": "region"}
                    ],
                    timeDimensions=[
                        {
                            "name": "closed_at",
                            "type": "DATE",
                            "expression": "closed_at",
                        }
                    ],
                )
            ],
        )
    )
    assert result.valid is True
    assert not any(m.code.startswith("cube_") for m in result.messages)


def test_cube_dimension_without_name_is_flagged() -> None:
    result = validate_mdl(
        mdl(
            models=[_DEALS],
            cubes=[_cube(dimensions=[{"description": "a dimension with no name"}])],
        )
    )
    assert any(m.code == "cube_entry_without_name" for m in result.messages)


def test_cube_dimension_requires_type_and_expression() -> None:
    # wren-core dimensions carry {name, type, expression}; missing → error (F4).
    result = validate_mdl(
        mdl(models=[_DEALS], cubes=[_cube(dimensions=[{"name": "region"}])])
    )
    assert result.valid is False
    codes = {m.code for m in result.messages}
    assert "cube_entry_without_type" in codes
    assert "cube_entry_without_expression" in codes


def test_cube_time_dimension_requires_type_and_expression() -> None:
    result = validate_mdl(
        mdl(models=[_DEALS], cubes=[_cube(timeDimensions=[{"name": "closed_at"}])])
    )
    assert result.valid is False
    codes = {m.code for m in result.messages}
    assert "cube_entry_without_type" in codes
    assert "cube_entry_without_expression" in codes


def test_cube_dimensions_must_be_a_list() -> None:
    result = validate_mdl(
        mdl(models=[_DEALS], cubes=[_cube(dimensions={"region": "not-a-list"})])
    )
    assert any(m.code == "cube_invalid_entries" for m in result.messages)
