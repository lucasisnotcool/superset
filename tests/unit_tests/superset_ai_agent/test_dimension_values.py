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

"""Dimension-value probing (C2, plan_sql_agent_doc_grounding_spec.md)."""

from __future__ import annotations

from types import SimpleNamespace

from superset_ai_agent.semantic_layer import dimension_values as dv
from superset_ai_agent.semantic_layer.dimension_values import (
    candidate_columns,
    extract_quoted_literals,
    probe_dimension_values,
    probe_sql,
)


def _dataset(table: str, columns: list[tuple[str, str]], schema: str = "main"):
    return SimpleNamespace(
        table_name=table,
        schema_name=schema,
        columns=[SimpleNamespace(name=n, type=t) for n, t in columns],
    )


class _ProbeClient:
    def __init__(self, rows_by_sql_fragment: dict[str, list[dict]]) -> None:
        self.rows_by_sql_fragment = rows_by_sql_fragment
        self.executed: list[str] = []

    def execute_sql(self, *, sql: str, **kwargs):
        self.executed.append(sql)
        for fragment, rows in self.rows_by_sql_fragment.items():
            if fragment in sql:
                return SimpleNamespace(rows=rows)
        return SimpleNamespace(rows=[])


def _fresh_cache(monkeypatch):
    from superset_ai_agent.persistence.ttl_cache import TtlCache

    cache: TtlCache = TtlCache(ttl_seconds=900.0)
    monkeypatch.setattr(dv, "_probe_cache", cache)
    return cache


def test_extract_quoted_literals_dedups_and_orders() -> None:
    q = """orders for 'Chicken Biryani' and "west region" and 'Chicken Biryani'"""
    assert extract_quoted_literals(q) == ["Chicken Biryani", "west region"]
    assert extract_quoted_literals("no quotes here") == []


def test_candidate_columns_prefers_name_overlap_and_string_types() -> None:
    datasets = [
        _dataset(
            "orders",
            [("amount", "DOUBLE"), ("region", "VARCHAR"), ("status", "TEXT")],
        ),
        _dataset("items", [("dish_name", "VARCHAR")]),
    ]
    pairs = candidate_columns(datasets, "orders by region 'west'", max_columns=2)
    names = [c.name for _, c in pairs]
    assert names[0] == "region"  # name overlap wins
    assert "amount" not in names  # numeric excluded


def test_probe_sql_is_bounded_and_escapes_quotes() -> None:
    dataset = _dataset("orders", [("status", "VARCHAR")])
    sql = probe_sql(dataset, dataset.columns[0], "o'brien")
    assert "LIKE '%o''brien%'" in sql
    assert sql.endswith("LIMIT 5")
    assert sql.startswith("SELECT DISTINCT status FROM main.orders")


def test_probe_returns_hint_and_caches(monkeypatch) -> None:
    _fresh_cache(monkeypatch)
    client = _ProbeClient({"LIKE '%biryani%'": [{"dish_name": "Biryani (Chicken)"}]})
    datasets = [_dataset("items", [("dish_name", "VARCHAR")])]
    kwargs = {
        "question": "revenue for 'biryani'",
        "datasets": datasets,
        "superset_client": client,
        "database_id": 1,
        "catalog_name": None,
        "schema_name": "main",
        "max_queries": 3,
    }
    hints = probe_dimension_values(**kwargs)
    assert hints == [
        {
            "literal": "biryani",
            "table": "main.items",
            "column": "dish_name",
            "values": ["Biryani (Chicken)"],
        }
    ]
    # Second call served from the TTL cache — no new warehouse query.
    executed_before = len(client.executed)
    hints2 = probe_dimension_values(**kwargs)
    assert hints2 == hints
    assert len(client.executed) == executed_before


def test_probe_respects_query_budget(monkeypatch) -> None:
    _fresh_cache(monkeypatch)
    client = _ProbeClient({})  # nothing ever matches
    datasets = [
        _dataset("a", [("c1", "VARCHAR"), ("c2", "VARCHAR"), ("c3", "VARCHAR")])
    ]
    probe_dimension_values(
        question="find 'ghost value'",
        datasets=datasets,
        superset_client=client,
        database_id=1,
        catalog_name=None,
        schema_name="main",
        max_queries=2,
    )
    assert len(client.executed) == 2  # hard budget, not one per column


def test_probe_inert_without_literals_or_string_columns(monkeypatch) -> None:
    _fresh_cache(monkeypatch)
    client = _ProbeClient({})
    assert (
        probe_dimension_values(
            question="total revenue by month",  # no quoted literal
            datasets=[_dataset("a", [("c", "VARCHAR")])],
            superset_client=client,
            database_id=1,
            catalog_name=None,
            schema_name="main",
            max_queries=3,
        )
        == []
    )
    assert (
        probe_dimension_values(
            question="find 'x'",
            datasets=[_dataset("a", [("n", "BIGINT")])],  # no string columns
            superset_client=client,
            database_id=1,
            catalog_name=None,
            schema_name="main",
            max_queries=3,
        )
        == []
    )
    assert client.executed == []


def test_probe_survives_execution_errors(monkeypatch) -> None:
    _fresh_cache(monkeypatch)

    class _Boom:
        def execute_sql(self, **kwargs):
            raise RuntimeError("timeout")

    assert (
        probe_dimension_values(
            question="find 'x-value'",
            datasets=[_dataset("a", [("c", "VARCHAR")])],
            superset_client=_Boom(),
            database_id=1,
            catalog_name=None,
            schema_name="main",
            max_queries=3,
        )
        == []
    )


def test_graph_probe_node_gated_and_traced(monkeypatch) -> None:
    _fresh_cache(monkeypatch)
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.graph import TextToSqlGraph
    from superset_ai_agent.schemas import AgentQueryRequest
    from tests.unit_tests.superset_ai_agent.test_graph import (
        FakeContextProvider,
        FakeModelClient,
        FakeSupersetClient,
    )

    class _ValueClient(FakeSupersetClient):
        def __init__(self) -> None:
            super().__init__()
            self.probes: list[str] = []

        def execute_sql(self, *, sql: str = "", **kwargs):
            if sql.startswith("SELECT DISTINCT"):
                self.probes.append(sql)
                return SimpleNamespace(rows=[{"name": "Michael"}])
            return super().execute_sql(sql=sql, **kwargs)

    client = _ValueClient()
    graph = TextToSqlGraph(
        config=AgentConfig(wren_dimension_value_probe_enabled=True),
        model_client=FakeModelClient("SELECT name FROM birth_names"),
        context_provider=FakeContextProvider(),
        superset_client=client,
    )
    response = graph.run(
        AgentQueryRequest(
            question="births for 'michael'",
            database_id=1,
            schema_name="main",
        )
    )
    events = [e for e in response.trace if e.step == "probe_dimension_values"]
    assert len(events) == 1
    assert events[0].details["hints"][0]["values"] == ["Michael"]
    steps = [s for s in response.timeline if s.kind == "probe_dimension_values"]
    assert steps
    assert steps[0].detail.kind == "dimension_values"
    assert steps[0].detail.hints[0].literal == "michael"

    # Flag off (default): no probe node output, no warehouse probes.
    graph_off = TextToSqlGraph(
        config=AgentConfig(),
        model_client=FakeModelClient("SELECT 1"),
        context_provider=FakeContextProvider(),
        superset_client=client,
    )
    before = len(client.probes)
    response_off = graph_off.run(
        AgentQueryRequest(
            question="births for 'michael'", database_id=1, schema_name="main"
        )
    )
    assert all(e.step != "probe_dimension_values" for e in response_off.trace)
    assert len(client.probes) == before
