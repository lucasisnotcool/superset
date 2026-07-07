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
"""Live schema introspection: dataset-free physical catalog for BYO connections.

See docs/plans/plan_live_schema_introspection_spec.md.
"""

from __future__ import annotations

from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.superset_metadata import SupersetMetadataContextProvider
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.integrations.superset.rest import (
    _synthetic_dataset_id,
    SupersetRestClient,
)
from superset_ai_agent.schemas import AgentQueryRequest


def _client() -> SupersetRestClient:
    return SupersetRestClient(AgentConfig())


# A minimal fake Superset REST surface: /tables/ then /table_metadata/ per table.
def _fake_request_factory(tables: dict[str, list[dict[str, Any]]]):
    """`tables`: {table_name: columns} for schema 'wlos_owner'; returns tables+views."""

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path.endswith("/tables/"):
            return {
                "count": len(tables),
                "result": [
                    {"value": "tbl_fiscal_calendar", "type": "table"},
                    {"value": "mv_wip_analytics", "type": "materialized_view"},
                    {"value": "v_legacy", "type": "view"},
                ],
            }
        if path.endswith("/table_metadata/"):
            name = kwargs["params"]["name"]
            return {"name": name, "columns": tables.get(name, [])}
        raise AssertionError(f"unexpected path {path}")

    return fake_request


def test_introspect_schema_builds_synthetic_datasets_with_views() -> None:
    tables = {
        "tbl_fiscal_calendar": [
            {"name": "FISCAL_YEAR", "type": "NUMBER", "comment": "yr"},
            {"name": "PERIOD", "type": "VARCHAR"},
        ],
        "mv_wip_analytics": [{"name": "WIP_QTY", "type": "NUMBER"}],
        "v_legacy": [{"name": "ID", "type": "NUMBER"}],
    }
    client = _client()
    client.request = _fake_request_factory(tables)  # type: ignore[method-assign]

    datasets = client.introspect_schema(database_id=7, schema_name="wlos_owner")

    by_name = {ds.table_name: ds for ds in datasets}
    # Tables, views AND materialized views are all surfaced.
    assert set(by_name) == {"tbl_fiscal_calendar", "mv_wip_analytics", "v_legacy"}
    cal = by_name["tbl_fiscal_calendar"]
    assert cal.schema_name == "wlos_owner"
    assert [c.name for c in cal.columns] == ["FISCAL_YEAR", "PERIOD"]
    assert {c.type for c in cal.columns} == {"NUMBER", "VARCHAR"}
    # Synthetic ids are negative (never collide with real, positive dataset ids).
    assert all(ds.id < 0 for ds in datasets)


def test_introspect_schema_excludes_views_when_requested() -> None:
    tables = {"tbl_fiscal_calendar": [{"name": "FISCAL_YEAR", "type": "NUMBER"}]}
    client = _client()
    client.request = _fake_request_factory(tables)  # type: ignore[method-assign]

    datasets = client.introspect_schema(
        database_id=7, schema_name="wlos_owner", include_views=False
    )

    assert [ds.table_name for ds in datasets] == ["tbl_fiscal_calendar"]


def test_introspect_schema_skips_tables_that_fail_reflection() -> None:
    # mv_wip_analytics returns no columns → skipped, not fatal.
    tables = {"tbl_fiscal_calendar": [{"name": "FISCAL_YEAR", "type": "NUMBER"}]}
    client = _client()
    client.request = _fake_request_factory(tables)  # type: ignore[method-assign]

    datasets = client.introspect_schema(database_id=7, schema_name="wlos_owner")

    assert [ds.table_name for ds in datasets] == ["tbl_fiscal_calendar"]


def test_synthetic_ids_are_stable_and_unique_across_schemas() -> None:
    a1 = _synthetic_dataset_id("wlos_owner", "orders")
    a2 = _synthetic_dataset_id("WLOS_OWNER", "Orders")  # casing-insensitive
    b = _synthetic_dataset_id("wlos_apps_owner", "orders")  # same table, other schema
    assert a1 == a2
    assert a1 != b
    assert a1 < 0
    assert b < 0


def test_introspect_schema_without_schema_returns_empty() -> None:
    client = _client()
    client.request = _fake_request_factory({})  # type: ignore[method-assign]
    assert client.introspect_schema(database_id=7, schema_name=None) == []


# --- provider fallback wiring -------------------------------------------------


class _FakeClient:
    """Empty dataset catalog; introspection yields one live table."""

    def __init__(self, *, introspect_result: list[DatasetMetadata]):
        self._introspect_result = introspect_result
        self.introspect_calls = 0

    def get_agent_context(self, **_: Any) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
            datasets=[],
        )

    def list_datasets(self, **_: Any) -> list[DatasetMetadata]:
        return []

    def introspect_schema(self, **_: Any) -> list[DatasetMetadata]:
        self.introspect_calls += 1
        return self._introspect_result


def _live_dataset() -> DatasetMetadata:
    return DatasetMetadata(
        id=_synthetic_dataset_id("wlos_owner", "tbl_fiscal_calendar"),
        table_name="tbl_fiscal_calendar",
        schema_name="wlos_owner",
        database_id=1,
        description=None,
        columns=[],
        metrics=[],
    )


def _request() -> AgentQueryRequest:
    return AgentQueryRequest(question="q", database_id=1, schema_name="wlos_owner")


def test_provider_falls_back_to_introspection_when_no_datasets() -> None:
    client = _FakeClient(introspect_result=[_live_dataset()])
    provider = SupersetMetadataContextProvider(client, config=AgentConfig())

    context = provider.get_full_schema(_request())

    assert client.introspect_calls == 1
    assert [d.table_name for d in context.datasets] == ["tbl_fiscal_calendar"]


def test_provider_skips_introspection_when_flag_off() -> None:
    client = _FakeClient(introspect_result=[_live_dataset()])
    provider = SupersetMetadataContextProvider(
        client, config=AgentConfig(wren_live_schema_introspection=False)
    )

    context = provider.get_full_schema(_request())

    assert client.introspect_calls == 0
    assert context.datasets == []


def test_provider_ignores_non_list_introspection_result() -> None:
    # A misbehaving adapter returning a non-list degrades to empty, not a crash.
    class _BadClient(_FakeClient):
        def introspect_schema(self, **_: Any) -> Any:
            self.introspect_calls += 1
            return object()

    client = _BadClient(introspect_result=[])
    provider = SupersetMetadataContextProvider(client, config=AgentConfig())

    context = provider.get_full_schema(_request())

    assert client.introspect_calls == 1
    assert context.datasets == []
