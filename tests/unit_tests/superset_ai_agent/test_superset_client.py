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

import json  # noqa: TID251 - tests cover standalone adapter wire payloads
from dataclasses import dataclass
from typing import cast

import httpx
import pytest

from superset_ai_agent.auth import SupersetRequestAuth
from superset_ai_agent.config import AgentConfig, SupersetAdapterMode
from superset_ai_agent.integrations.superset.client import (
    LocalSupersetClient,
    SupersetAuthError,
)
from superset_ai_agent.integrations.superset.factory import create_superset_client
from superset_ai_agent.integrations.superset.mcp import SupersetMcpClient
from superset_ai_agent.integrations.superset.rest import SupersetRestClient
from superset_ai_agent.schemas import SqlExecutionSource


@dataclass
class FakeColumn:
    column_name: str
    type: str
    is_dttm: bool = False
    description: str | None = None


@dataclass
class FakeMetric:
    metric_name: str
    expression: str
    description: str | None = None


@dataclass
class FakeDataset:
    id: int
    table_name: str
    schema: str | None
    database_id: int
    description: str | None
    columns: list[FakeColumn]
    metrics: list[FakeMetric]


def test_serialize_dataset_sorts_columns_and_metrics() -> None:
    dataset = FakeDataset(
        id=16,
        table_name="birth_names",
        schema=None,
        database_id=1,
        description="Names by year.",
        columns=[
            FakeColumn(column_name="num", type="BIGINT"),
            FakeColumn(column_name="name", type="VARCHAR"),
        ],
        metrics=[
            FakeMetric(metric_name="sum__num", expression="SUM(num)"),
            FakeMetric(metric_name="count", expression="COUNT(*)"),
        ],
    )

    serialized = LocalSupersetClient._serialize_dataset(dataset)

    assert serialized.id == 16
    assert serialized.table_name == "birth_names"
    assert [column.name for column in serialized.columns] == ["name", "num"]
    assert [metric.name for metric in serialized.metrics] == ["count", "sum__num"]


@pytest.mark.parametrize(
    ("adapter", "expected_type"),
    [
        ("local", LocalSupersetClient),
        ("rest", SupersetRestClient),
        ("mcp", SupersetMcpClient),
    ],
)
def test_create_superset_client_selects_adapter(
    adapter: str,
    expected_type: type[object],
) -> None:
    client = create_superset_client(
        AgentConfig(superset_agent_adapter=cast(SupersetAdapterMode, adapter)),
    )

    assert isinstance(client, expected_type)


def test_create_superset_client_rejects_unknown_adapter() -> None:
    with pytest.raises(ValueError, match="SUPERSET_AGENT_ADAPTER"):
        create_superset_client(
            AgentConfig(
                superset_agent_adapter=cast(SupersetAdapterMode, "unknown"),
            ),
        )


def test_rest_adapter_is_wired_as_skeleton() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "rest-token"})
        if request.url.path == "/api/v1/security/csrf_token/":
            assert request.headers["authorization"] == "Bearer rest-token"
            return httpx.Response(
                200,
                headers={"set-cookie": "session=csrf-session; Path=/"},
                json={"result": "csrf-token"},
            )
        if request.url.path == "/api/v1/database/":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "id": 1,
                            "database_name": "examples",
                            "backend": "sqlite",
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/database/1":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "id": 1,
                        "database_name": "examples",
                        "backend": "sqlite",
                    }
                },
            )
        if request.url.path == "/api/v1/dataset/":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "id": 16,
                            "table_name": "birth_names",
                            "schema": None,
                            "database": {"id": 1},
                            "description": "Names by year.",
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/dataset/16":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "id": 16,
                        "table_name": "birth_names",
                        "schema": None,
                        "database": {"id": 1},
                        "description": "Names by year.",
                        "columns": [
                            {"column_name": "num", "type": "BIGINT"},
                            {"column_name": "name", "type": "VARCHAR"},
                        ],
                        "metrics": [
                            {"metric_name": "count", "expression": "COUNT(*)"},
                        ],
                    }
                },
            )
        if request.url.path == "/api/v1/sqllab/execute/":
            assert request.headers["x-csrftoken"] == "csrf-token"
            assert "session=csrf-session" in request.headers["cookie"]
            body = json.loads(request.content)
            assert body["database_id"] == 1
            assert body["catalog"] == "prod"
            assert body["runAsync"] is False
            assert len(body["client_id"]) <= 11
            assert body["client_id"].startswith("ai")
            assert body["sql_editor_id"] == "ai_agent"
            assert body["tab"] == "AI Agent"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [{"name": "Emma", "total_births": 10}],
                    "columns": [
                        {"name": "name"},
                        {"name": "total_births"},
                    ],
                    "query": {
                        "queryId": 123,
                        "resultsKey": "result-key",
                        "executedSql": "select 1",
                        "databaseId": 1,
                        "schema": None,
                        "queryLimit": 1000,
                        "id": body["client_id"],
                        "sqlEditorId": body["sql_editor_id"],
                        "tab": body["tab"],
                    },
                },
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_agent_adapter="rest",
            superset_auth_mode="service_account",
            superset_base_url="http://localhost:8091/",
            superset_username="admin",
            superset_password="admin",  # noqa: S106
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.base_url == "http://localhost:8091"
    assert client.list_databases()[0].name == "examples"
    assert client.get_database_dialect(1) == "sqlite"
    assert client.list_datasets(database_id=1)[0].table_name == "birth_names"

    context = client.get_agent_context(database_id=1, dataset_ids=[16])
    assert context.database.name == "examples"
    assert [column.name for column in context.datasets[0].columns] == ["name", "num"]

    result = client.execute_sql(
        database_id=1,
        catalog_name="prod",
        sql="select 1",
        source=SqlExecutionSource(source="ai_agent", request_id="request-1"),
    )
    assert result.columns == ["name", "total_births"]
    assert result.row_count == 1
    assert result.audit is not None
    assert result.audit.adapter == "rest"
    assert result.audit.query_id == 123
    assert result.audit.results_key == "result-key"
    assert result.audit.executed_sql == "select 1"
    assert result.audit.database_id == 1
    assert result.audit.catalog_name == "prod"
    assert result.audit.client_id is not None
    assert len(result.audit.client_id) <= 11
    assert result.audit.sql_editor_id == "ai_agent"
    assert result.audit.tab == "AI Agent"
    assert result.audit.source == "ai_agent"
    assert result.audit.source_hash is not None
    assert requests[0].url.path == "/api/v1/security/login"


def test_rest_adapter_forwards_user_session_without_service_login() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path != "/api/v1/security/login"
        assert request.headers["cookie"] == "session=user-session"
        return httpx.Response(
            200,
            json={
                "result": {
                    "id": 1,
                    "database_name": "examples",
                    "backend": "sqlite",
                }
            },
        )

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="user_session",
            superset_base_url="http://localhost:8091",
        ),
        transport=httpx.MockTransport(handler),
        request_auth=SupersetRequestAuth(cookie_header="session=user-session"),
    )

    assert client.get_database_dialect(1) == "sqlite"
    assert [request.url.path for request in requests] == ["/api/v1/database/1"]


def test_rest_adapter_gets_database_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path != "/api/v1/security/login"
        assert request.headers["cookie"] == "session=user-session"
        if request.url.path == "/api/v1/ai-agent/database/1/identity":
            assert request.url.params["catalog"] == "prod"
            return httpx.Response(
                200,
                json={
                    "result": {
                        "database_id": 1,
                        "database_name": "warehouse",
                        "backend": "postgresql",
                        "driver": "psycopg2",
                        "uri_fingerprint": "fingerprint-1",
                        "catalog": "prod",
                        "schemas": ["finance", "pipeline"],
                    }
                },
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="user_session",
            superset_base_url="http://localhost:8091",
        ),
        transport=httpx.MockTransport(handler),
        request_auth=SupersetRequestAuth(cookie_header="session=user-session"),
    )

    identity = client.get_database_identity(database_id=1, catalog_name="prod")

    assert identity.database_id == 1
    assert identity.database_name == "warehouse"
    assert identity.backend == "postgresql"
    assert identity.driver == "psycopg2"
    assert identity.uri_fingerprint == "fingerprint-1"
    assert identity.catalog_name == "prod"
    assert identity.schema_names == ["finance", "pipeline"]
    assert client.list_database_schemas(database_id=1, catalog_name="prod") == [
        "finance",
        "pipeline",
    ]
    assert [request.url.path for request in requests] == [
        "/api/v1/ai-agent/database/1/identity",
        "/api/v1/ai-agent/database/1/identity",
    ]


def test_rest_adapter_fetches_csrf_with_user_session_for_post() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["cookie"] == "session=user-session"
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "user-csrf"})
        if request.url.path == "/api/v1/sqllab/execute/":
            assert request.headers["x-csrftoken"] == "user-csrf"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [{"value": 1}],
                    "columns": [{"name": "value"}],
                },
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="user_session",
            superset_base_url="http://localhost:8091",
        ),
        transport=httpx.MockTransport(handler),
        request_auth=SupersetRequestAuth(cookie_header="session=user-session"),
    )

    result = client.execute_sql(
        database_id=1,
        catalog_name="prod",
        sql="select 1",
    )

    assert result.row_count == 1
    assert result.audit is not None
    assert result.audit.catalog_name == "prod"
    assert [request.url.path for request in requests] == [
        "/api/v1/security/csrf_token/",
        "/api/v1/sqllab/execute/",
    ]


def test_rest_adapter_preserves_query_audit_when_polling_results() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "csrf"})
        if request.url.path == "/api/v1/sqllab/execute/":
            return httpx.Response(
                200,
                json={
                    "query": {
                        "id": 456,
                        "resultsKey": "async-key",
                        "executedSql": "select 1",
                        "databaseId": 1,
                        "schema": "sales",
                        "queryLimit": 25,
                    },
                },
            )
        if request.url.path == "/api/v1/sqllab/results/":
            assert request.url.params["q"] == "(key:async-key)"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [{"value": 1}],
                    "columns": [{"name": "value"}],
                },
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="user_session",
            superset_base_url="http://localhost:8091",
            superset_sql_poll_interval_seconds=0,
        ),
        transport=httpx.MockTransport(handler),
        request_auth=SupersetRequestAuth(cookie_header="session=user-session"),
    )

    result = client.execute_sql(
        database_id=1,
        sql="select 1",
        schema_name="sales",
        limit=25,
    )

    assert result.audit is not None
    assert result.audit.query_id == 456
    assert result.audit.results_key == "async-key"
    assert result.audit.executed_sql == "select 1"
    assert result.audit.database_id == 1
    assert result.audit.schema_name == "sales"
    assert result.audit.row_limit == 25
    assert [request.url.path for request in requests] == [
        "/api/v1/security/csrf_token/",
        "/api/v1/sqllab/execute/",
        "/api/v1/sqllab/results/",
    ]


def test_rest_adapter_semantic_layer_bridge_uses_superset_rest() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "csrf"})
        if request.url.path == "/api/v1/semantic_layer/":
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "uuid": "layer-1",
                                "name": "Sales semantic layer",
                            }
                        ]
                    },
                )
            if request.method == "POST":
                body = json.loads(request.content)
                assert body["name"] == "Sales semantic layer"
                return httpx.Response(201, json={"result": {"uuid": "layer-1"}})
        if request.url.path == "/api/v1/semantic_layer/layer-1":
            if request.method == "PUT":
                body = json.loads(request.content)
                assert body["description"] == "Updated"
                return httpx.Response(200, json={"result": {"uuid": "layer-1"}})
            if request.method == "DELETE":
                return httpx.Response(200, json={"message": "OK"})
        if request.url.path == "/api/v1/semantic_view/":
            body = json.loads(request.content)
            assert body["views"][0]["semantic_layer_uuid"] == "layer-1"
            return httpx.Response(
                201,
                json={"result": {"created": [{"uuid": "view-1", "name": "Deals"}]}},
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="user_session",
            superset_base_url="http://localhost:8091",
        ),
        transport=httpx.MockTransport(handler),
        request_auth=SupersetRequestAuth(cookie_header="session=user-session"),
    )

    assert client.list_semantic_layers()[0]["uuid"] == "layer-1"
    assert client.create_semantic_layer(
        {
            "name": "Sales semantic layer",
            "type": "wren",
            "configuration": {},
        }
    ) == {"uuid": "layer-1"}
    assert client.update_semantic_layer(
        "layer-1",
        {"description": "Updated"},
    ) == {"uuid": "layer-1"}
    assert client.create_semantic_views(
        [{"name": "Deals", "semantic_layer_uuid": "layer-1", "configuration": {}}]
    ) == {"created": [{"uuid": "view-1", "name": "Deals"}]}
    client.delete_semantic_layer("layer-1")

    assert [request.url.path for request in requests] == [
        "/api/v1/semantic_layer/",
        "/api/v1/security/csrf_token/",
        "/api/v1/semantic_layer/",
        "/api/v1/semantic_layer/layer-1",
        "/api/v1/semantic_view/",
        "/api/v1/semantic_layer/layer-1",
    ]


def test_rest_adapter_reauthenticates_service_account_once_after_401() -> None:
    requests: list[httpx.Request] = []
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        requests.append(request)
        if request.url.path == "/api/v1/security/login":
            login_count += 1
            return httpx.Response(200, json={"access_token": f"token-{login_count}"})
        if request.url.path == "/api/v1/database/1":
            if request.headers["authorization"] == "Bearer token-1":
                return httpx.Response(401, text="expired")
            return httpx.Response(
                200,
                json={
                    "result": {
                        "id": 1,
                        "database_name": "examples",
                        "backend": "sqlite",
                    }
                },
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="service_account",
            superset_base_url="http://localhost:8091",
            superset_username="admin",
            superset_password="admin",  # noqa: S106
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.get_database_dialect(1) == "sqlite"
    assert [request.url.path for request in requests] == [
        "/api/v1/security/login",
        "/api/v1/database/1",
        "/api/v1/security/login",
        "/api/v1/database/1",
    ]


def test_rest_adapter_static_token_401_does_not_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, text="expired")

    client = SupersetRestClient(
        AgentConfig(
            superset_auth_mode="service_account",
            superset_base_url="http://localhost:8091",
            superset_auth_token="static-token",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SupersetAuthError):
        client.get_database_dialect(1)

    assert [request.url.path for request in requests] == ["/api/v1/database/1"]


def test_mcp_adapter_calls_tools_and_normalizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "tools/list":
            return _json_rpc_result(
                payload["id"],
                {"tools": [{"name": "execute_sql", "inputSchema": {"type": "object"}}]},
            )

        tool_name = payload["params"]["name"]
        if tool_name == "list_databases":
            return _json_rpc_result(
                payload["id"],
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "databases": [
                                        {
                                            "id": 1,
                                            "database_name": "examples",
                                            "backend": "sqlite",
                                        }
                                    ]
                                }
                            ),
                        }
                    ]
                },
            )
        if tool_name == "get_database_info":
            return _json_rpc_result(
                payload["id"],
                {
                    "structuredContent": {
                        "id": 1,
                        "database_name": "examples",
                        "backend": "sqlite",
                    }
                },
            )
        if tool_name == "list_datasets":
            return _json_rpc_result(
                payload["id"],
                {
                    "structuredContent": {
                        "datasets": [
                            {
                                "id": 16,
                                "table_name": "birth_names",
                                "schema": None,
                                "database_id": 1,
                                "description": "Names by year.",
                            }
                        ]
                    }
                },
            )
        if tool_name == "get_dataset_info":
            return _json_rpc_result(
                payload["id"],
                {
                    "structuredContent": {
                        "id": 16,
                        "table_name": "birth_names",
                        "schema": None,
                        "database_id": 1,
                        "description": "Names by year.",
                        "columns": [
                            {"column_name": "num", "type": "BIGINT"},
                            {"column_name": "name", "type": "VARCHAR"},
                        ],
                        "metrics": [
                            {"metric_name": "count", "expression": "COUNT(*)"},
                        ],
                    }
                },
            )
        if tool_name == "execute_sql":
            return _json_rpc_result(
                payload["id"],
                {
                    "structuredContent": {
                        "success": True,
                        "rows": [{"name": "Emma", "total_births": 10}],
                        "columns": [
                            {"name": "name", "type": "VARCHAR"},
                            {"name": "total_births", "type": "BIGINT"},
                        ],
                        "row_count": 1,
                        "query_id": "mcp-query-1",
                        "database_id": 1,
                        "limit": 1000,
                    }
                },
            )
        return _json_rpc_error(payload["id"], "unknown tool")

    client = SupersetMcpClient(
        AgentConfig(
            superset_agent_adapter="mcp",
            superset_auth_mode="service_account",
            superset_mcp_url="http://localhost:8098/mcp",
            superset_mcp_auth_token="mcp-token",  # noqa: S106
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.mcp_url == "http://localhost:8098/mcp"
    assert client.get_tool_schema("execute_sql") == {"type": "object"}
    assert client.list_databases()[0].name == "examples"

    context = client.get_agent_context(database_id=1, dataset_ids=[16])
    assert context.database.backend == "sqlite"
    assert context.datasets[0].table_name == "birth_names"
    assert [column.name for column in context.datasets[0].columns] == ["name", "num"]

    result = client.execute_sql(database_id=1, sql="select 1")
    assert result.columns == ["name", "total_births"]
    assert result.row_count == 1
    assert result.audit is not None
    assert result.audit.adapter == "mcp"
    assert result.audit.query_id == "mcp-query-1"
    assert result.audit.database_id == 1


def test_mcp_adapter_forwards_user_session_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == "session=user-session"
        payload = json.loads(request.content)
        return _json_rpc_result(
            payload["id"],
            {
                "structuredContent": {
                    "id": 1,
                    "database_name": "examples",
                    "backend": "sqlite",
                }
            },
        )

    client = SupersetMcpClient(
        AgentConfig(
            superset_agent_adapter="mcp",
            superset_auth_mode="user_session",
            superset_mcp_url="http://localhost:8098/mcp",
        ),
        transport=httpx.MockTransport(handler),
        request_auth=SupersetRequestAuth(cookie_header="session=user-session"),
    )

    assert client.get_database_dialect(1) == "sqlite"


def test_mcp_adapter_raises_on_tool_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return _json_rpc_error(payload["id"], "permission denied")

    client = SupersetMcpClient(
        AgentConfig(
            superset_agent_adapter="mcp",
            superset_auth_mode="service_account",
            superset_mcp_url="http://localhost:8098/mcp",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="permission denied"):
        client.list_databases()


def _json_rpc_result(request_id: str, result: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "id": request_id, "result": result},
    )


def _json_rpc_error(request_id: str, message: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": message},
        },
    )


# --- LocalSupersetClient self-defense (R6/R-LOCAL) ------------------------
class _FakeApp:
    def app_context(self):  # noqa: D401 - test stub
        from contextlib import nullcontext

        return nullcontext()


class _FakeDatabase:
    backend = "postgresql"

    def __init__(self) -> None:
        self.queried: list[str] = []

    def get_df(self, sql: str, schema: str | None = None):
        import pandas as pd

        self.queried.append(sql)
        return pd.DataFrame([{"a": 1}])


class _FakeQuery:
    def __init__(self, database: _FakeDatabase) -> None:
        self._database = database

    def filter_by(self, **_: object) -> "_FakeQuery":
        return self

    def one(self) -> _FakeDatabase:
        return self._database


class _FakeSession:
    def __init__(self, database: _FakeDatabase) -> None:
        self._database = database

    def query(self, _model: object) -> _FakeQuery:
        return _FakeQuery(self._database)


class _FakeDb:
    def __init__(self, database: _FakeDatabase) -> None:
        self.session = _FakeSession(database)


def _local_client_with_fake_db(
    monkeypatch, database: _FakeDatabase
) -> LocalSupersetClient:
    import superset

    client = LocalSupersetClient(AgentConfig())
    client.__dict__["_app"] = _FakeApp()  # bypass the cached_property app boot
    monkeypatch.setattr(superset, "db", _FakeDb(database), raising=False)
    return client


def test_local_adapter_refuses_mutating_sql(monkeypatch) -> None:
    database = _FakeDatabase()
    client = _local_client_with_fake_db(monkeypatch, database)

    with pytest.raises(ValueError, match="non-read-only"):
        client.execute_sql(database_id=1, sql="DELETE FROM birth_names")

    # Fails closed before ever touching the engine.
    assert database.queried == []


def test_local_adapter_runs_read_only_and_pushes_limit(monkeypatch) -> None:
    database = _FakeDatabase()
    client = _local_client_with_fake_db(monkeypatch, database)

    result = client.execute_sql(database_id=1, sql="SELECT a FROM t", limit=50)

    assert database.queried == ["SELECT a FROM t\nLIMIT 50"]
    assert result.audit is not None
    assert result.audit.executed_sql == "SELECT a FROM t\nLIMIT 50"
