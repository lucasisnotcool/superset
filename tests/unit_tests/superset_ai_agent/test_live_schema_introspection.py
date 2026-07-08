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


def test_provider_unions_registered_datasets_with_live_introspection() -> None:
    """A registered dataset must never shadow live-only tables (no dataset gating).

    The catalog is datasets ∪ live names: the registered dataset keeps its synced
    columns (enrichment), live-only tables still appear, and a table present in
    both is not duplicated.
    """

    from superset_ai_agent.integrations.superset.client import ColumnSummary

    registered = DatasetMetadata(
        id=42,
        table_name="tbl_registered",
        schema_name="wlos_owner",
        database_id=1,
        columns=[ColumnSummary(name="ID", type="NUMBER")],
        metrics=[],
    )
    overlap = DatasetMetadata(  # same table, introspected — must not duplicate
        id=_synthetic_dataset_id("wlos_owner", "tbl_registered"),
        table_name="tbl_registered",
        schema_name="wlos_owner",
        database_id=1,
        columns=[],
        metrics=[],
    )

    class _MixedClient(_FakeClient):
        def list_datasets(self, **_: Any) -> list[DatasetMetadata]:
            return [registered]

    client = _MixedClient(introspect_result=[_live_dataset(), overlap])
    provider = SupersetMetadataContextProvider(client, config=AgentConfig())

    context = provider.get_full_schema(_request())

    assert client.introspect_calls == 1  # introspection runs DESPITE datasets
    names = sorted(d.table_name for d in context.datasets)
    assert names == ["tbl_fiscal_calendar", "tbl_registered"]
    kept = next(d for d in context.datasets if d.table_name == "tbl_registered")
    assert kept.id == 42  # the registered dataset (with columns) wins the dedupe


def test_provider_skips_introspection_when_flag_off() -> None:
    client = _FakeClient(introspect_result=[_live_dataset()])
    provider = SupersetMetadataContextProvider(
        client, config=AgentConfig(wren_live_schema_introspection=False)
    )

    context = provider.get_full_schema(_request())

    assert client.introspect_calls == 0
    assert context.datasets == []


def test_names_listing_is_cached_across_provider_builds() -> None:
    """The `/tables/` names listing is cached per (db, catalog, schema): a
    catalog rebuild within the TTL reuses it instead of re-listing."""

    client = _FakeClient(introspect_result=[_live_dataset()])
    provider = SupersetMetadataContextProvider(client, config=AgentConfig())

    first = provider.get_full_schema(_request())
    second = provider.get_full_schema(_request())

    assert client.introspect_calls == 1  # second build served from the cache
    assert [d.table_name for d in first.datasets] == [
        d.table_name for d in second.datasets
    ]


def test_empty_names_listing_is_not_cached() -> None:
    """A failed/empty listing must retry next build, not pin emptiness."""

    client = _FakeClient(introspect_result=[])
    provider = SupersetMetadataContextProvider(client, config=AgentConfig())

    provider.get_full_schema(_request())
    provider.get_full_schema(_request())

    assert client.introspect_calls == 2


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


# --- names-first introspection (one call per schema, columns lazy) -------------


def _counting_request_factory(tables: dict[str, list[dict[str, Any]]]):
    """Like ``_fake_request_factory`` but counts per-endpoint calls."""

    calls = {"tables": 0, "table_metadata": 0}

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if path.endswith("/tables/"):
            calls["tables"] += 1
            return {
                "count": len(tables),
                "result": [{"value": name, "type": "table"} for name in sorted(tables)],
            }
        if path.endswith("/table_metadata/"):
            calls["table_metadata"] += 1
            name = kwargs["params"]["name"]
            return {"name": name, "columns": tables.get(name, [])}
        raise AssertionError(f"unexpected path {path}")

    return fake_request, calls


def test_introspect_schema_names_only_is_one_call_and_lists_everything() -> None:
    tables = {f"tbl_{i:03d}": [{"name": "ID", "type": "NUMBER"}] for i in range(50)}
    client = _client()
    fake_request, calls = _counting_request_factory(tables)
    client.request = fake_request  # type: ignore[method-assign]

    datasets = client.introspect_schema(
        database_id=7, schema_name="wlos_owner", limit=2000, names_only=True
    )

    # Every table is surfaced by NAME with zero per-table reflection calls.
    assert len(datasets) == 50
    assert calls == {"tables": 1, "table_metadata": 0}
    assert all(ds.columns == [] for ds in datasets)
    assert all(ds.id < 0 for ds in datasets)


def test_reflect_table_columns_requests_columns_only_shape() -> None:
    seen_params: list[dict[str, Any]] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        assert path.endswith("/table_metadata/")
        seen_params.append(kwargs["params"])
        return {"name": "t", "columns": [{"name": "ID", "type": "NUMBER"}]}

    client = _client()
    client.request = fake_request  # type: ignore[method-assign]

    columns = client.reflect_table_columns(
        database_id=7, schema_name="wlos_owner", table_name="t"
    )

    assert [c.name for c in columns] == ["ID"]
    assert seen_params[0]["columns_only"] == "true"


def test_reflect_table_columns_falls_back_without_columns_only() -> None:
    """A Superset that predates ``columns_only`` (400s on the unknown param)
    still reflects via the full shape — mixed-version rollouts keep working."""
    from superset_ai_agent.integrations.superset.client import SupersetAdapterError

    calls: list[dict[str, Any]] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["params"])
        if "columns_only" in kwargs["params"]:
            raise SupersetAdapterError("400: unknown field columns_only")
        return {"name": "t", "columns": [{"name": "ID", "type": "NUMBER"}]}

    client = _client()
    client.request = fake_request  # type: ignore[method-assign]

    columns = client.reflect_table_columns(
        database_id=7, schema_name="wlos_owner", table_name="t"
    )

    assert [c.name for c in columns] == ["ID"]
    assert len(calls) == 2
    assert "columns_only" not in calls[1]


def test_reflect_table_columns_reflects_one_table() -> None:
    tables = {
        "tbl_fiscal_calendar": [
            {"name": "FISCAL_YEAR", "type": "NUMBER", "comment": "yr"},
            {"name": "PERIOD", "type": "VARCHAR"},
        ]
    }
    client = _client()
    fake_request, calls = _counting_request_factory(tables)
    client.request = fake_request  # type: ignore[method-assign]

    columns = client.reflect_table_columns(
        database_id=7, schema_name="wlos_owner", table_name="tbl_fiscal_calendar"
    )

    assert calls == {"tables": 0, "table_metadata": 1}
    assert [c.name for c in columns] == ["FISCAL_YEAR", "PERIOD"]
    assert (
        client.reflect_table_columns(database_id=7, schema_name=None, table_name="x")
        == []
    )


def test_provider_requests_names_only_with_names_limit() -> None:
    captured: dict[str, Any] = {}

    class _CapturingClient(_FakeClient):
        def introspect_schema(self, **kwargs: Any) -> list[DatasetMetadata]:
            captured.update(kwargs)
            return super().introspect_schema()

    client = _CapturingClient(introspect_result=[_live_dataset()])
    config = AgentConfig(wren_introspection_names_limit=1234)
    provider = SupersetMetadataContextProvider(client, config=config)

    provider.get_full_schema(_request())

    assert captured["names_only"] is True
    assert captured["limit"] == 1234


# --- SchemaIndex lazy column reflection ----------------------------------------


def _pending_context() -> AgentContext:
    """Two name-only (pending) tables + one fully-loaded table."""

    def _ds(table: str, columns: list[Any]) -> DatasetMetadata:
        return DatasetMetadata(
            id=_synthetic_dataset_id("wlos_owner", table),
            table_name=table,
            schema_name="wlos_owner",
            database_id=1,
            columns=columns,
            metrics=[],
        )

    from superset_ai_agent.integrations.superset.client import ColumnSummary

    return AgentContext(
        database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
        datasets=[
            _ds("tbl_fiscal_calendar", []),
            _ds("mv_wip_analytics", []),
            _ds("tbl_loaded", [ColumnSummary(name="ID", type="NUMBER")]),
        ],
    )


def _make_index_with_loader(
    catalog: dict[str, dict[str, str | None]], *, budget: int = 10
):
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex.from_agent_context(_pending_context())
    calls: list[tuple[str | None, str]] = []

    def loader(schema: str | None, table: str) -> dict[str, str | None]:
        calls.append((schema, table))
        if table not in catalog:
            raise RuntimeError("reflection failed")
        return catalog[table]

    index.attach_column_loader(loader, budget=budget)
    return index, calls


def test_lazy_index_reflects_on_first_touch_and_memoizes() -> None:
    index, calls = _make_index_with_loader(
        {"tbl_fiscal_calendar": {"FISCAL_YEAR": "NUMBER", "PERIOD": "VARCHAR"}}
    )

    assert index.columns_loaded("tbl_fiscal_calendar", "wlos_owner") is False
    assert index.has_column("tbl_fiscal_calendar", "fiscal_year", "wlos_owner")
    assert index.column_type("tbl_fiscal_calendar", "period", "wlos_owner") == (
        "VARCHAR"
    )
    assert index.columns_for("tbl_fiscal_calendar", "wlos_owner") == [
        "fiscal_year",
        "period",
    ]
    # One reflection despite three accessor touches (memoized on the index).
    assert calls == [("wlos_owner", "tbl_fiscal_calendar")]
    assert index.columns_loaded("tbl_fiscal_calendar", "wlos_owner") is True
    # The loaded table never triggers the loader.
    assert index.has_column("tbl_loaded", "id", "wlos_owner")
    assert len(calls) == 1


def test_lazy_index_resolves_schema_when_unqualified() -> None:
    index, calls = _make_index_with_loader(
        {"tbl_fiscal_calendar": {"FISCAL_YEAR": "NUMBER"}}
    )

    assert index.has_column("tbl_fiscal_calendar", "fiscal_year")
    assert calls == [("wlos_owner", "tbl_fiscal_calendar")]


def test_lazy_index_budget_bounds_reflections() -> None:
    index, calls = _make_index_with_loader(
        {
            "tbl_fiscal_calendar": {"FISCAL_YEAR": "NUMBER"},
            "mv_wip_analytics": {"WIP_QTY": "NUMBER"},
        },
        budget=1,
    )

    assert index.ensure_columns("tbl_fiscal_calendar", "wlos_owner") is True
    # Budget exhausted: the second pending table stays columns-unknown.
    assert index.ensure_columns("mv_wip_analytics", "wlos_owner") is False
    assert calls == [("wlos_owner", "tbl_fiscal_calendar")]


def test_lazy_index_failure_is_memoized_until_reattach() -> None:
    index, calls = _make_index_with_loader({})  # loader raises for everything

    assert index.ensure_columns("tbl_fiscal_calendar", "wlos_owner") is False
    assert index.ensure_columns("tbl_fiscal_calendar", "wlos_owner") is False
    # Failed once, not retried within the same attachment.
    assert len(calls) == 1

    def good_loader(schema: str | None, table: str) -> dict[str, str | None]:
        return {"FISCAL_YEAR": "NUMBER"}

    index.attach_column_loader(good_loader, budget=10)
    assert index.ensure_columns("tbl_fiscal_calendar", "wlos_owner") is True


def test_lazy_index_without_loader_reports_unknown_not_empty() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex.from_agent_context(_pending_context())

    assert index.ensure_columns("tbl_fiscal_calendar", "wlos_owner") is False
    assert index.ensure_columns("missing_table", "wlos_owner") is False
    assert index.pending_tables_by_schema() == {
        "wlos_owner": ["mv_wip_analytics", "tbl_fiscal_calendar"]
    }


# --- validation on a names-first index ------------------------------------------


_MDL_WITH_COLUMNS = {
    "models": [
        {
            "name": "fiscal_calendar",
            "tableReference": {"schema": "wlos_owner", "table": "tbl_fiscal_calendar"},
            "columns": [
                {"name": "fiscal_year", "type": "NUMBER"},
                {"name": "bogus_col", "type": "NUMBER"},
            ],
        }
    ]
}


def test_validation_degrades_to_warning_when_columns_unknown() -> None:
    import json  # noqa: TID251 - standalone agent JSON contract

    from superset_ai_agent.semantic_layer.mdl_validator import (
        SchemaIndex,
        validate_mdl,
    )

    index = SchemaIndex.from_agent_context(_pending_context())  # no loader

    result = validate_mdl(json.dumps(_MDL_WITH_COLUMNS), schema_index=index)

    codes = {m.code for m in result.messages}
    assert "columns_unverified" in codes
    assert "unknown_column" not in codes  # unknown ≠ authoritative empty


def test_validation_reflects_lazily_and_still_catches_hallucinations() -> None:
    import json  # noqa: TID251 - standalone agent JSON contract

    from superset_ai_agent.semantic_layer.mdl_validator import validate_mdl

    index, calls = _make_index_with_loader(
        {"tbl_fiscal_calendar": {"FISCAL_YEAR": "NUMBER"}}
    )

    result = validate_mdl(json.dumps(_MDL_WITH_COLUMNS), schema_index=index)

    codes = {m.code for m in result.messages}
    assert "unknown_column" in codes  # bogus_col rejected against real columns
    assert "columns_unverified" not in codes
    assert calls == [("wlos_owner", "tbl_fiscal_calendar")]


# --- copilot tools on a names-first index ----------------------------------------


def test_onboard_tool_refuses_when_columns_cannot_load() -> None:
    from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex.from_agent_context(_pending_context())  # no loader
    toolset = MdlToolset([], schema_index=index)

    result = toolset.dispatch(
        "propose_onboard_table",
        {"table": "tbl_fiscal_calendar", "schema": "wlos_owner"},
    )

    assert "could not be loaded" in result["error"]


def test_onboard_tool_reflects_and_stages_real_columns() -> None:
    import json  # noqa: TID251 - standalone agent JSON contract

    from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset

    index, _calls = _make_index_with_loader(
        {"tbl_fiscal_calendar": {"FISCAL_YEAR": "NUMBER", "PERIOD": "VARCHAR"}}
    )
    toolset = MdlToolset([], schema_index=index)

    result = toolset.dispatch(
        "propose_onboard_table",
        {"table": "tbl_fiscal_calendar", "schema": "wlos_owner"},
    )

    assert result.get("onboarded_table") == "tbl_fiscal_calendar"
    staged = toolset.dispatch("read_mdl_file", {"path": result["path"]})
    model = json.loads(staged["content"])["models"][0]
    assert [c["name"] for c in model["columns"]] == ["fiscal_year", "period"]


def test_find_tables_reflects_only_top_candidates() -> None:
    from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    def _ds(table: str) -> DatasetMetadata:
        return DatasetMetadata(
            id=_synthetic_dataset_id("wlos_owner", table),
            table_name=table,
            schema_name="wlos_owner",
            database_id=1,
            columns=[],
            metrics=[],
        )

    context = AgentContext(
        database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
        datasets=[_ds(f"wip_stage_{i}") for i in range(8)],
    )
    index = SchemaIndex.from_agent_context(context)
    calls: list[str] = []

    def loader(schema: str | None, table: str) -> dict[str, str | None]:
        calls.append(table)
        return {"ID": "NUMBER"}

    index.attach_column_loader(loader, budget=100)
    toolset = MdlToolset([], schema_index=index)

    result = toolset.dispatch("find_tables", {"query": "wip stage", "limit": 8})

    assert len(result["tables"]) == 8
    # Only the strongest candidates are reflected; the rest stay names-only.
    assert len(calls) == 5
    pending = [t for t in result["tables"] if t.get("columns_pending")]
    assert len(pending) == 3


def test_get_physical_schema_compacts_large_catalogs() -> None:
    from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    tables = {f"tbl_{i:04d}": set() for i in range(500)}
    index = SchemaIndex(
        tables=dict(tables),
        tables_by_schema={"wlos": dict(tables)},
        pending_by_schema={"wlos": set(tables)},
    )
    toolset = MdlToolset([], schema_index=index)

    result = toolset.dispatch("get_physical_schema", {})

    assert result["truncated"] is True
    assert result["total_tables"] == 500
    summary = result["schemas_summary"]["wlos"]
    assert summary["table_count"] == 500
    assert len(summary["tables_sample"]) == 60
    assert "find_tables" in result["note"]


def test_get_physical_schema_flags_pending_tables() -> None:
    from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex.from_agent_context(_pending_context())
    toolset = MdlToolset([], schema_index=index)

    result = toolset.dispatch("get_physical_schema", {})

    assert result["columns_pending"] == ["mv_wip_analytics", "tbl_fiscal_calendar"]
    assert "never assume or invent columns" in result["note"]


# --- bulk onboarding skips names-only tables --------------------------------------


def test_bulk_onboarding_skips_names_only_datasets() -> None:
    from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
    from superset_ai_agent.semantic_layer.onboarding import onboard_schema_project
    from superset_ai_agent.semantic_layer.schemas import SemanticProject

    seen: dict[str, Any] = {}

    class _WrenClient:
        def generate_base_model(self, *, project: Any, superset_context: Any):
            seen["datasets"] = [d.table_name for d in superset_context.datasets]
            return []

    project = SemanticProject(
        name="p",
        schema_name="wlos_owner",
        default_database_id=1,
        owner_id="owner-1",
        database_uri_fingerprint="fp",
    )
    result = onboard_schema_project(
        project=project,
        superset_context=_pending_context(),
        wren_client=_WrenClient(),
        mdl_file_store=InMemoryMdlFileStore(),
    )

    # Only the column-bearing table reaches bulk generation.
    assert seen["datasets"] == ["tbl_loaded"]
    assert any("skipped by bulk onboarding" in w for w in result.warnings)


# --- R1 anchored to the project's proven schema set (no dataset gating) ----------


def _mdl(schema: str, table: str) -> str:
    import json  # noqa: TID251 - standalone agent JSON contract

    return json.dumps(
        {
            "models": [
                {
                    "name": table,
                    "tableReference": {"schema": schema, "table": table},
                    "columns": [{"name": "id", "type": "NUMBER"}],
                }
            ]
        }
    )


def _index_with_one_listed_schema():
    """Index where only schema 'a' yielded tables; 'b' is a proven member whose
    listing yielded nothing (live-only schema on a failed/empty scan)."""

    from superset_ai_agent.integrations.superset.client import ColumnSummary
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    context = AgentContext(
        database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
        datasets=[
            DatasetMetadata(
                id=7,
                table_name="orders",
                schema_name="a",
                database_id=1,
                columns=[ColumnSummary(name="id", type="NUMBER")],
                metrics=[],
            )
        ],
    )
    index = SchemaIndex.from_agent_context(context)
    index.known_schemas.update({"a", "b"})  # the project's PROVEN schema set
    return index


def test_member_schema_without_listing_is_not_rejected() -> None:
    """The reported bug: MDL referencing a proven member schema must not fail
    R1 just because that schema contributed no datasets/listing this build."""

    from superset_ai_agent.semantic_layer.mdl_validator import validate_mdl

    result = validate_mdl(
        _mdl("b", "tbl_live_only"), schema_index=_index_with_one_listed_schema()
    )

    codes = {m.code for m in result.messages}
    assert "schema_not_in_project" not in codes
    assert "unknown_table" not in codes
    assert "table_unverified" in codes  # honest warning, not a hard block
    assert result.valid is True


def test_bogus_table_in_listed_schema_still_hard_fails() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import validate_mdl

    result = validate_mdl(
        _mdl("a", "no_such_table"), schema_index=_index_with_one_listed_schema()
    )

    assert "unknown_table" in {m.code for m in result.messages}
    assert result.valid is False


def test_ensure_columns_many_reflects_concurrently_and_memoizes() -> None:
    import threading
    import time

    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    tables = {f"tbl_{i}" for i in range(5)}
    index = SchemaIndex(
        tables={t: set() for t in tables},
        tables_by_schema={"wlos": {t: set() for t in tables}},
        pending_by_schema={"wlos": set(tables)},
    )
    in_flight = {"now": 0, "max": 0}
    gauge = threading.Lock()

    def loader(_schema: str | None, _table: str) -> dict[str, str | None]:
        with gauge:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.05)
        with gauge:
            in_flight["now"] -= 1
        return {"ID": "NUMBER"}

    index.attach_column_loader(loader, budget=10)
    index.ensure_columns_many([(t, "wlos") for t in sorted(tables)])

    # All five reflected, and they actually overlapped (parallel, not serial).
    assert index.pending_tables_by_schema() == {}
    assert in_flight["max"] > 1
    # Memoized: a second batch does not re-fetch.
    calls: list[str] = []
    index.attach_column_loader(
        lambda _s, t: calls.append(t) or {"ID": "NUMBER"}, budget=10
    )
    index.ensure_columns_many([(t, "wlos") for t in sorted(tables)])
    assert calls == []


def test_ensure_columns_many_respects_budget_and_dedupes() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex(
        tables={"a": set(), "b": set(), "c": set()},
        tables_by_schema={"wlos": {"a": set(), "b": set(), "c": set()}},
        pending_by_schema={"wlos": {"a", "b", "c"}},
    )
    calls: list[str] = []

    def loader(_schema: str | None, table: str) -> dict[str, str | None]:
        calls.append(table)
        return {"ID": "NUMBER"}

    index.attach_column_loader(loader, budget=2)
    # Duplicate refs collapse; only 2 fetches happen (budget), 1 stays pending.
    index.ensure_columns_many(
        [("a", "wlos"), ("a", "wlos"), ("b", "wlos"), ("c", "wlos")]
    )

    assert len(calls) == 2
    assert index.pending_tables_by_schema() == {"wlos": ["c"]}


def test_mdl_referenced_tables_extracts_and_dedupes() -> None:
    import json  # noqa: TID251 - standalone agent JSON contract
    from types import SimpleNamespace

    from superset_ai_agent.app import _mdl_referenced_tables

    files = [
        SimpleNamespace(
            status="active",
            content=json.dumps(
                {
                    "models": [
                        {
                            "name": "orders",
                            "tableReference": {"schema": "wlos", "table": "orders"},
                        },
                        {
                            "name": "dup",
                            "tableReference": {"schema": "WLOS", "table": "ORDERS"},
                        },
                        {"name": "no_ref"},
                    ]
                }
            ),
        ),
        SimpleNamespace(status="deleted", content=json.dumps({"models": []})),
        SimpleNamespace(status="draft", content="not json"),
    ]

    refs = _mdl_referenced_tables(files)

    assert refs == [("orders", "wlos")]


def test_warm_mdl_referenced_columns_reflects_without_budget() -> None:
    import json  # noqa: TID251 - standalone agent JSON contract
    import time
    from types import SimpleNamespace

    from superset_ai_agent.app import _warm_mdl_referenced_columns
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex(
        tables={"orders": set()},
        tables_by_schema={"wlos": {"orders": set()}},
        pending_by_schema={"wlos": {"orders"}},
    )
    # Budget 0: the warm must still reflect (budget-exempt by design).
    index.attach_column_loader(lambda _s, _t: {"ID": "NUMBER"}, budget=0)
    files = [
        SimpleNamespace(
            status="active",
            content=json.dumps(
                {
                    "models": [
                        {
                            "name": "orders",
                            "tableReference": {"schema": "wlos", "table": "orders"},
                        }
                    ]
                }
            ),
        )
    ]

    _warm_mdl_referenced_columns(index, files)

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not index.columns_loaded("orders", "wlos"):
        time.sleep(0.01)
    assert index.columns_loaded("orders", "wlos") is True
    assert index.columns_for("orders", "wlos") == ["id"]


def test_adopt_resolved_from_carries_reflections_across_rebuilds() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    previous = SchemaIndex(
        tables={"orders": set(), "moves": set(), "gone": set()},
        tables_by_schema={"wlos": {"orders": set(), "moves": set(), "gone": set()}},
        pending_by_schema={"wlos": {"orders", "moves", "gone"}},
    )
    previous.attach_column_loader(
        lambda _s, t: {"ID": "NUMBER"} if t == "orders" else {}, budget=10
    )
    assert previous.ensure_columns("orders", "wlos") is True  # reflected
    assert previous.ensure_columns("moves", "wlos") is False  # loader empty

    # Fresh rebuild: 'gone' dropped from the live listing, the rest pending.
    fresh = SchemaIndex(
        tables={"orders": set(), "moves": set()},
        tables_by_schema={"wlos": {"orders": set(), "moves": set()}},
        pending_by_schema={"wlos": {"orders", "moves"}},
    )

    adopted = fresh.adopt_resolved_from(previous)

    assert adopted == 1  # only the genuinely resolved table carries over
    assert fresh.columns_loaded("orders", "wlos") is True
    assert fresh.columns_for("orders", "wlos") == ["id"]
    assert fresh.column_type("orders", "id", "wlos") == "NUMBER"
    assert fresh.pending_tables_by_schema() == {"wlos": ["moves"]}
    # A dropped table never resurrects.
    assert fresh.has_table("gone", "wlos") is False


def test_schema_outside_project_set_still_rejected() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import validate_mdl

    result = validate_mdl(
        _mdl("z", "orders"), schema_index=_index_with_one_listed_schema()
    )

    assert "schema_not_in_project" in {m.code for m in result.messages}
    assert result.valid is False
