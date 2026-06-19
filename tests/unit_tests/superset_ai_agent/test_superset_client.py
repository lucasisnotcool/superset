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

from superset_ai_agent.config import AgentConfig, SupersetAdapterMode
from superset_ai_agent.integrations.superset.client import (
    LocalSupersetClient,
)
from superset_ai_agent.integrations.superset.factory import create_superset_client
from superset_ai_agent.integrations.superset.mcp import SupersetMcpClient
from superset_ai_agent.integrations.superset.rest import SupersetRestClient


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
            return httpx.Response(200, json={"result": "csrf-token"})
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
            body = json.loads(request.content)
            assert body["database_id"] == 1
            assert body["runAsync"] is False
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": [{"name": "Emma", "total_births": 10}],
                    "columns": [
                        {"name": "name"},
                        {"name": "total_births"},
                    ],
                },
            )
        return httpx.Response(404, text=request.url.path)

    client = SupersetRestClient(
        AgentConfig(
            superset_agent_adapter="rest",
            superset_base_url="http://localhost:8088/",
            superset_username="admin",
            superset_password="admin",  # noqa: S106
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.base_url == "http://localhost:8088"
    assert client.list_databases()[0].name == "examples"
    assert client.get_database_dialect(1) == "sqlite"
    assert client.list_datasets(database_id=1)[0].table_name == "birth_names"

    context = client.get_agent_context(database_id=1, dataset_ids=[16])
    assert context.database.name == "examples"
    assert [column.name for column in context.datasets[0].columns] == ["name", "num"]

    result = client.execute_sql(database_id=1, sql="select 1")
    assert result.columns == ["name", "total_births"]
    assert result.row_count == 1
    assert requests[0].url.path == "/api/v1/security/login"


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
                    }
                },
            )
        return _json_rpc_error(payload["id"], "unknown tool")

    client = SupersetMcpClient(
        AgentConfig(
            superset_agent_adapter="mcp",
            superset_mcp_url="http://localhost:5008/mcp",
            superset_mcp_auth_token="mcp-token",  # noqa: S106
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.mcp_url == "http://localhost:5008/mcp"
    assert client.get_tool_schema("execute_sql") == {"type": "object"}
    assert client.list_databases()[0].name == "examples"

    context = client.get_agent_context(database_id=1, dataset_ids=[16])
    assert context.database.backend == "sqlite"
    assert context.datasets[0].table_name == "birth_names"
    assert [column.name for column in context.datasets[0].columns] == ["name", "num"]

    result = client.execute_sql(database_id=1, sql="select 1")
    assert result.columns == ["name", "total_births"]
    assert result.row_count == 1


def test_mcp_adapter_raises_on_tool_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        return _json_rpc_error(payload["id"], "permission denied")

    client = SupersetMcpClient(
        AgentConfig(
            superset_agent_adapter="mcp",
            superset_mcp_url="http://localhost:5008/mcp",
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
