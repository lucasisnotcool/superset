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
