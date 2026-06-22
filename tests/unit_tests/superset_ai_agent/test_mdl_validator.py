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
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: stage\n"
        "        type: VARCHAR\n"
    )
    assert result.valid is True
    assert result.messages == []


def test_invalid_join_type_is_error() -> None:
    result = validate_mdl(
        "models:\n"
        "  - name: deals\n"
        "    columns:\n"
        "      - name: stage\n"
        "relationships:\n"
        "  - name: deals_sites\n"
        "    models: [deals, sites]\n"
        "    join_type: SIDEWAYS\n"
    )
    assert result.valid is False
    assert any(m.code == "invalid_join_type" for m in result.messages)


def test_duplicate_column_is_error() -> None:
    result = validate_mdl(
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: stage\n"
        "      - name: stage\n"
    )
    assert result.valid is False
    assert any(m.code == "duplicate_column" for m in result.messages)


def test_calculated_column_requires_expression() -> None:
    result = validate_mdl(
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: derived\n"
        "        is_calculated: true\n"
    )
    assert result.valid is False
    assert any(m.code == "calculated_requires_expression" for m in result.messages)


def test_physical_validation_flags_unknown_table() -> None:
    result = validate_mdl(
        "models:\n"
        "  - name: ghosts\n"
        "    table_reference:\n"
        "      table: ghosts\n"
        "    columns:\n"
        "      - name: stage\n",
        schema_index=_schema_index(),
    )
    assert result.valid is False
    assert any(m.code == "unknown_table" for m in result.messages)


def test_physical_validation_flags_hallucinated_column() -> None:
    result = validate_mdl(
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: stage\n"
        "      - name: invented_total\n",
        schema_index=_schema_index(),
    )
    assert result.valid is False
    unknown = [m for m in result.messages if m.code == "unknown_column"]
    assert unknown
    assert "invented_total" in unknown[0].message


def test_physical_validation_allows_calculated_column() -> None:
    result = validate_mdl(
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: stage\n"
        "      - name: total\n"
        "        is_calculated: true\n"
        "        expression: SUM(gross_moves)\n",
        schema_index=_schema_index(),
    )
    assert result.valid is True


def test_schema_index_from_snapshot_validates_like_live() -> None:
    index = _schema_index()
    snapshot_index = SchemaIndex.from_snapshot(index.to_tables())

    result = validate_mdl(
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: ghost\n",
        schema_index=snapshot_index,
    )
    assert result.valid is False
    assert any(m.code == "unknown_column" for m in result.messages)


def test_relationship_unresolved_is_warning_per_file_error_in_project() -> None:
    deals = (
        "models:\n"
        "  - name: deals\n"
        "    table_reference:\n"
        "      table: deals\n"
        "    columns:\n"
        "      - name: stage\n"
        "relationships:\n"
        "  - name: deals_sites\n"
        "    models: [deals, sites]\n"
        "    join_type: MANY_TO_ONE\n"
    )
    per_file = validate_mdl(deals)
    assert per_file.valid is True
    assert any(
        m.code == "unresolved_relationship" and m.severity == "warning"
        for m in per_file.messages
    )

    sites = (
        "models:\n"
        "  - name: sites\n"
        "    table_reference:\n"
        "      table: sites\n"
        "    columns:\n"
        "      - name: site_id\n"
    )
    project = validate_project_manifest([deals, sites])
    assert project.valid is True

    project_missing = validate_project_manifest([deals])
    assert project_missing.valid is False
    assert any(
        m.code == "unresolved_relationship" and m.severity == "error"
        for m in project_missing.messages
    )


_DEALS_MODEL = (
    "models:\n"
    "  - name: deals\n"
    "    table_reference:\n"
    "      table: deals\n"
    "    columns:\n"
    "      - name: amount\n"
    "        type: DOUBLE\n"
)


def test_valid_metric_passes_structural_validation() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "metrics:\n"
        "  - name: total_amount\n"
        "    base_object: deals\n"
        "    expression: SUM(amount)\n"
    )
    assert result.valid is True
    assert result.messages == []


def test_metric_only_file_is_not_empty_root() -> None:
    result = validate_mdl(
        "metrics:\n  - name: total\n    expression: SUM(amount)\n"
    )
    assert not any(m.code == "empty_root" for m in result.messages)


def test_metric_without_measure_is_warning() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "metrics:\n  - name: total_amount\n    base_object: deals\n"
    )
    assert result.valid is True
    assert any(m.code == "metric_without_measure" for m in result.messages)


def test_metric_unresolved_base_is_warning_per_file_error_in_project() -> None:
    metric = (
        "metrics:\n"
        "  - name: total_amount\n"
        "    base_object: ghost\n"
        "    expression: SUM(amount)\n"
    )
    per_file = validate_mdl(_DEALS_MODEL + metric)
    assert per_file.valid is True
    assert any(
        m.code == "unresolved_metric_base" and m.severity == "warning"
        for m in per_file.messages
    )
    project = validate_project_manifest([_DEALS_MODEL + metric])
    assert project.valid is False
    assert any(
        m.code == "unresolved_metric_base" and m.severity == "error"
        for m in project.messages
    )


def test_duplicate_metric_name_is_error() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "metrics:\n"
        "  - name: total\n    expression: SUM(amount)\n"
        "  - name: total\n    expression: COUNT(*)\n"
    )
    assert result.valid is False
    assert any(m.code == "duplicate_metric" for m in result.messages)


def test_cube_without_measures_is_warning() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n  - name: deal_cube\n    base_object: deals\n"
    )
    assert result.valid is True
    assert any(m.code == "cube_without_measures" for m in result.messages)


def test_cube_measure_requires_expression() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
    )
    assert result.valid is True
    assert any(m.code == "cube_measure_without_expression" for m in result.messages)


def test_cube_unresolved_base_is_error_in_project() -> None:
    cube = (
        "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: ghost\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
    )
    project = validate_project_manifest([_DEALS_MODEL + cube])
    assert project.valid is False
    assert any(
        m.code == "unresolved_cube_base" and m.severity == "error"
        for m in project.messages
    )


def test_cube_dimension_without_name_is_flagged() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    dimensions:\n"
        "      - description: a dimension with no name\n"
    )
    assert any(m.code == "cube_entry_without_name" for m in result.messages)


def test_cube_time_dimension_and_hierarchy_names_pass() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    time_dimensions:\n"
        "      - name: closed_at\n"
        "    hierarchies:\n"
        "      - name: geography\n"
    )
    assert not any(m.code == "cube_entry_without_name" for m in result.messages)


def test_cube_unknown_granularity_is_warning() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    time_dimensions:\n"
        "      - name: closed_at\n"
        "        granularity: fortnight\n"
    )
    assert result.valid is True
    assert any(m.code == "cube_unknown_granularity" for m in result.messages)


def test_cube_known_granularity_passes() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    time_dimensions:\n"
        "      - name: closed_at\n"
        "        granularity: Month\n"
    )
    assert not any(m.code == "cube_unknown_granularity" for m in result.messages)


def test_cube_hierarchy_level_must_resolve_to_a_dimension() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    dimensions:\n"
        "      - name: region\n"
        "    hierarchies:\n"
        "      - name: geo\n"
        "        levels: [region, ghost_level]\n"
    )
    codes = [m.code for m in result.messages]
    assert "cube_hierarchy_unknown_level" in codes
    # The defined dimension `region` is not flagged.
    unknown = [
        m
        for m in result.messages
        if m.code == "cube_hierarchy_unknown_level"
    ]
    assert all("ghost_level" in m.message for m in unknown)


def test_cube_hierarchy_without_levels_is_warning() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    hierarchies:\n"
        "      - name: geo\n"
    )
    assert any(m.code == "cube_hierarchy_without_levels" for m in result.messages)


def test_cube_dimensions_must_be_a_list() -> None:
    result = validate_mdl(
        _DEALS_MODEL + "cubes:\n"
        "  - name: deal_cube\n"
        "    base_object: deals\n"
        "    measures:\n"
        "      - name: total\n"
        "        expression: SUM(amount)\n"
        "    dimensions:\n"
        "      region: not-a-list\n"
    )
    assert any(m.code == "cube_invalid_entries" for m in result.messages)
