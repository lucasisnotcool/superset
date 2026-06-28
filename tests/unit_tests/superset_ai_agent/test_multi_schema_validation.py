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

"""Schema-aware MDL validation: cross-schema tables and the R1 invariant."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex, validate_mdl


def _dataset(table: str, schema: str, columns: list[str]) -> DatasetMetadata:
    return DatasetMetadata(
        id=abs(hash((schema, table))) % 100000,
        table_name=table,
        schema_name=schema,
        database_id=1,
        columns=[ColumnSummary(name=name, type="VARCHAR") for name in columns],
        metrics=[],
    )


def _index(*datasets: DatasetMetadata) -> SchemaIndex:
    return SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="db"),
            datasets=list(datasets),
        )
    )


def _model(name: str, schema: str, table: str, columns: list[str]) -> str:
    return json.dumps(
        {
            "models": [
                {
                    "name": name,
                    "tableReference": {"schema": schema, "table": table},
                    "columns": [
                        {"name": col, "type": "VARCHAR"} for col in columns
                    ],
                }
            ]
        }
    )


def test_schema_index_is_schema_qualified() -> None:
    index = _index(
        _dataset("orders", "sales", ["id"]),
        _dataset("orders", "archive", ["id", "archived_at"]),
    )
    assert index.schemas == {"sales", "archive"}
    assert index.has_table("orders", "sales")
    assert index.has_table("orders", "archive")
    # collision: the archive-only column is not visible under the sales schema
    assert index.has_column("orders", "archived_at", "archive")
    assert not index.has_column("orders", "archived_at", "sales")


def test_model_referencing_table_in_a_member_schema_is_valid() -> None:
    index = _index(
        _dataset("orders", "sales", ["id"]),
        _dataset("customers", "crm", ["id", "name"]),
    )
    result = validate_mdl(
        _model("customers", "crm", "customers", ["id", "name"]),
        schema_index=index,
    )
    assert result.valid, [m.message for m in result.messages]


def test_model_referencing_out_of_set_schema_is_rejected() -> None:
    # R1: 'secret' is not among the project's proven schemas.
    index = _index(_dataset("orders", "sales", ["id"]))
    result = validate_mdl(
        _model("leak", "secret", "orders", ["id"]),
        schema_index=index,
    )
    assert not result.valid
    codes = {m.code for m in result.messages}
    assert "schema_not_in_project" in codes


def test_unknown_table_message_is_schema_qualified() -> None:
    index = _index(_dataset("orders", "sales", ["id"]))
    result = validate_mdl(
        _model("ghost", "sales", "nonexistent", ["id"]),
        schema_index=index,
    )
    assert not result.valid
    messages = " ".join(m.message for m in result.messages)
    assert "sales.nonexistent" in messages


def test_column_check_respects_schema_on_table_name_collision() -> None:
    # Same table name in two schemas with different columns; a model under 'sales'
    # must not borrow the 'archive' schema's column.
    index = _index(
        _dataset("orders", "sales", ["id"]),
        _dataset("orders", "archive", ["id", "archived_at"]),
    )
    result = validate_mdl(
        _model("sales_orders", "sales", "orders", ["id", "archived_at"]),
        schema_index=index,
    )
    assert not result.valid
    assert any(m.code == "unknown_column" for m in result.messages)
