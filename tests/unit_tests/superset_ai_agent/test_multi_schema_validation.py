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
                    "columns": [{"name": col, "type": "VARCHAR"} for col in columns],
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


def _typed_dataset(table: str, schema: str, columns: dict[str, str]) -> DatasetMetadata:
    return DatasetMetadata(
        id=abs(hash((schema, table))) % 100000,
        table_name=table,
        schema_name=schema,
        database_id=1,
        columns=[
            ColumnSummary(name=name, type=type_) for name, type_ in columns.items()
        ],
        metrics=[],
    )


def test_column_type_resolves_by_schema_on_collision() -> None:
    # F2: same table+column in two schemas with DIFFERENT types. column_type with a
    # schema must return that schema's type, not whichever won the flat overwrite.
    index = SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="db"),
            datasets=[
                _typed_dataset("orders", "sales", {"amount": "DOUBLE"}),
                _typed_dataset("orders", "archive", {"amount": "BIGINT"}),
            ],
        )
    )
    assert index.column_type("orders", "amount", "sales") == "DOUBLE"
    assert index.column_type("orders", "amount", "archive") == "BIGINT"
    # Without a schema it falls back to the flat map (single-schema/snapshot behaviour).
    assert index.column_type("orders", "amount") in {"DOUBLE", "BIGINT"}


def test_schema_qualified_view_groups_tables_under_schemas() -> None:
    # F1: the surfacing view keeps each table under its own schema, with types.
    index = SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="db"),
            datasets=[
                _typed_dataset("orders", "sales", {"id": "BIGINT"}),
                _typed_dataset("orders", "archive", {"id": "BIGINT", "ts": "DATE"}),
            ],
        )
    )
    assert index.is_multi_schema() is True
    view = index.schema_qualified_view()
    assert set(view) == {"sales", "archive"}
    assert view["sales"]["orders"]["columns"] == ["id"]
    assert view["archive"]["orders"]["columns"] == ["id", "ts"]
    assert view["sales"]["orders"]["types"] == {"id": "BIGINT"}


def test_single_schema_index_is_not_multi_schema() -> None:
    index = _index(_dataset("orders", "sales", ["id"]))
    assert index.is_multi_schema() is False


def test_from_snapshot_restores_qualified_maps() -> None:
    # F3 plumbing: a multi-schema snapshot round-trips the qualified maps so
    # cross-schema validation survives a Superset outage.
    index = SchemaIndex.from_snapshot(
        tables={"orders": ["id"]},
        tables_by_schema={
            "sales": {"orders": ["id"]},
            "archive": {"orders": ["id", "ts"]},
        },
        types_by_schema={"sales": {"orders": {"id": "BIGINT"}}},
    )
    assert index.is_multi_schema() is True
    assert index.has_column("orders", "ts", "archive")
    assert not index.has_column("orders", "ts", "sales")
    assert index.column_type("orders", "id", "sales") == "BIGINT"


def test_type_mismatch_resolves_by_schema_on_collision() -> None:
    # `code` is VARCHAR in 'sales' but BIGINT in 'archive'. A model under 'archive'
    # typed VARCHAR must mismatch (archive is BIGINT); the same under 'sales' must
    # NOT (sales is VARCHAR) — proving the type check resolves per-schema (F2).
    index = SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="db"),
            datasets=[
                _typed_dataset("orders", "sales", {"code": "VARCHAR"}),
                _typed_dataset("orders", "archive", {"code": "BIGINT"}),
            ],
        )
    )
    archive = json.dumps(
        {
            "models": [
                {
                    "name": "a",
                    "tableReference": {"schema": "archive", "table": "orders"},
                    "columns": [{"name": "code", "type": "VARCHAR"}],
                }
            ]
        }
    )
    sales = json.dumps(
        {
            "models": [
                {
                    "name": "b",
                    "tableReference": {"schema": "sales", "table": "orders"},
                    "columns": [{"name": "code", "type": "VARCHAR"}],
                }
            ]
        }
    )
    assert any(
        m.code == "column_type_mismatch"
        for m in validate_mdl(archive, schema_index=index).messages
    )
    assert not any(
        m.code == "column_type_mismatch"
        for m in validate_mdl(sales, schema_index=index).messages
    )
