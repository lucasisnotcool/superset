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

import itertools
import json  # noqa: TID251 - keep the standalone agent independent of Superset
from typing import Any

import httpx

from superset_ai_agent.auth import SupersetRequestAuth
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseIdentity,
    DatabaseSummary,
    DatasetMetadata,
    SupersetAdapterError,
    SupersetAdapterNotImplementedError,
    SupersetAuthError,
)
from superset_ai_agent.integrations.superset.rest import (
    _items,
    _normalize_database,
    _normalize_dataset,
    _normalize_execution_result,
)
from superset_ai_agent.semantic_layer.uri_fingerprint import fingerprint_database_identity
from superset_ai_agent.schemas import ExecutionResult, SqlExecutionSource


class SupersetMcpClient:
    """Superset MCP adapter with high-level and low-level controls."""

    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
        request_auth: SupersetRequestAuth | None = None,
    ):
        self.config = config
        self.mcp_url = config.superset_mcp_url
        self.transport = transport
        self.timeout = httpx.Timeout(60.0)
        self.request_auth = request_auth
        self._request_ids = itertools.count(1)

    def call_json_rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a low-level JSON-RPC request against the MCP endpoint."""

        request_id = f"agent-{next(self._request_ids)}"
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        with httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = client.post(self.mcp_url, json=payload)
        self._raise_for_status(response)
        data = response.json()
        if not isinstance(data, dict):
            raise SupersetAdapterError("Superset MCP returned a non-object payload.")
        if data.get("error"):
            raise SupersetAdapterError(f"Superset MCP error: {data['error']}")
        return data.get("result")

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a low-level MCP tool and unwrap the result content."""

        result = self.call_json_rpc(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )
        return _unwrap_mcp_result(result)

    def list_tools(self) -> list[dict[str, Any]]:
        """Return raw MCP tool metadata."""

        result = self.call_json_rpc("tools/list")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            return [tool for tool in result["tools"] if isinstance(tool, dict)]
        if isinstance(result, list):
            return [tool for tool in result if isinstance(tool, dict)]
        return []

    def get_tool_schema(self, name: str) -> dict[str, Any] | None:
        """Return an MCP tool input schema by name when exposed."""

        for tool in self.list_tools():
            if tool.get("name") == name:
                schema = tool.get("inputSchema") or tool.get("input_schema")
                return schema if isinstance(schema, dict) else None
        return None

    def read_resource(self, uri: str) -> Any:
        """Read a low-level MCP resource."""

        return self.call_json_rpc("resources/read", {"uri": uri})

    def list_databases_raw(self, *, page_size: int = 100) -> Any:
        """Return raw `list_databases` MCP tool payload."""

        return self.call_tool(
            "list_databases",
            {
                "request": {
                    "page": 1,
                    "page_size": page_size,
                    "order_column": "database_name",
                    "order_direction": "asc",
                    "select_columns": ["id", "database_name", "backend"],
                }
            },
        )

    def get_database_raw(self, database_id: int) -> Any:
        """Return raw `get_database_info` MCP tool payload."""

        return self.call_tool(
            "get_database_info",
            {"request": {"identifier": database_id}},
        )

    def list_datasets_raw(
        self,
        *,
        database_id: int,
        schema_name: str | None = None,
        limit: int,
    ) -> Any:
        """Return raw `list_datasets` MCP tool payload for a database."""

        database = _normalize_database(_as_dict(self.get_database_raw(database_id)))
        filters = []
        if database.name:
            filters.append(
                {
                    "col": "database_name",
                    "opr": "eq",
                    "value": database.name,
                }
            )
        if schema_name is not None:
            filters.append({"col": "schema", "opr": "eq", "value": schema_name})
        return self.call_tool(
            "list_datasets",
            {
                "request": {
                    "page": 1,
                    "page_size": limit,
                    "order_column": "table_name",
                    "order_direction": "asc",
                    "select_columns": [
                        "id",
                        "table_name",
                        "schema",
                        "database_id",
                        "description",
                    ],
                    "filters": filters,
                }
            },
        )

    def get_dataset_raw(self, dataset_id: int) -> Any:
        """Return raw `get_dataset_info` MCP tool payload."""

        return self.call_tool(
            "get_dataset_info",
            {
                "request": {
                    "identifier": dataset_id,
                    "select_columns": [
                        "id",
                        "table_name",
                        "schema",
                        "database_id",
                        "description",
                        "columns",
                        "metrics",
                    ],
                    "column_fields": [
                        "column_name",
                        "type",
                        "is_dttm",
                        "description",
                    ],
                }
            },
        )

    def execute_sql_raw(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        limit: int = 1000,
    ) -> Any:
        """Return raw `execute_sql` MCP tool payload."""

        return self.call_tool(
            "execute_sql",
            {
                "request": {
                    "database_id": database_id,
                    "sql": sql,
                    "catalog": catalog_name,
                    "schema": schema_name,
                    "limit": limit,
                    "timeout": 30,
                    "dry_run": False,
                    "force_refresh": False,
                }
            },
        )

    def list_databases(self) -> list[DatabaseSummary]:
        """List databases through MCP."""

        payload = _as_dict(self.list_databases_raw())
        return [_normalize_database(item) for item in _items(payload, "databases")]

    def get_database_identity(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> DatabaseIdentity:
        """Return non-secret database identity from MCP metadata."""

        database = _normalize_database(_as_dict(self.get_database_raw(database_id)))
        return DatabaseIdentity(
            database_id=database.id,
            database_name=database.name,
            backend=database.backend,
            uri_fingerprint=fingerprint_database_identity(database_id=database.id),
            catalog_name=catalog_name,
            schema_names=self.list_database_schemas(
                database_id=database_id,
                catalog_name=catalog_name,
            ),
        )

    def list_database_schemas(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> list[str]:
        """List schemas visible through dataset metadata when MCP lacks schema tool."""

        datasets = self.list_datasets(
            database_id=database_id,
            catalog_name=catalog_name,
            limit=self.config.max_context_datasets,
        )
        return sorted(
            {
                dataset.schema_name
                for dataset in datasets
                if dataset.schema_name is not None
            }
        )

    def list_datasets(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ) -> list[DatasetMetadata]:
        """List dataset metadata through MCP."""

        if dataset_ids:
            datasets = [
                _normalize_dataset(_as_dict(self.get_dataset_raw(dataset_id)))
                for dataset_id in dataset_ids
            ]
            # An explicit id selection is authoritative: bound it to the requested
            # database, but do NOT narrow by a single schema_name — that would
            # silently drop datasets in the project's other schemas (cross-schema
            # onboarding). The database bound prevents cross-database leakage.
            return [
                dataset
                for dataset in datasets
                if dataset.database_id in {0, database_id}
            ]
        payload = _as_dict(
            self.list_datasets_raw(
                database_id=database_id,
                schema_name=schema_name,
                limit=limit,
            )
        )
        datasets = [_normalize_dataset(item) for item in _items(payload, "datasets")]
        return [
            _normalize_dataset(_as_dict(self.get_dataset_raw(dataset.id)))
            for dataset in datasets
            if dataset.id and dataset.database_id in {0, database_id}
            and (schema_name is None or dataset.schema_name == schema_name)
        ]

    def get_agent_context(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        include_datasets: bool = True,
    ) -> AgentContext:
        """Build compact metadata context from MCP tools.

        ``include_datasets=False`` returns just the database shell (no dataset
        scan), for callers that replace ``datasets`` with their own candidates.
        """

        database = _normalize_database(_as_dict(self.get_database_raw(database_id)))
        if not include_datasets:
            return AgentContext(database=database, datasets=[])
        datasets = self.list_datasets(
            database_id=database_id,
            catalog_name=catalog_name,
            schema_name=schema_name,
            dataset_ids=dataset_ids,
            limit=self.config.max_context_datasets,
        )
        return AgentContext(database=database, datasets=datasets)

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        limit: int = 1000,
        source: SqlExecutionSource | None = None,
    ) -> ExecutionResult:
        """Execute SQL through MCP and normalize the result."""

        _ = source
        payload = _as_dict(
            self.execute_sql_raw(
                database_id=database_id,
                sql=sql,
                catalog_name=catalog_name,
                schema_name=schema_name,
                limit=limit,
            )
        )
        if payload.get("success") is False:
            raise SupersetAdapterError(
                f"Superset MCP execute_sql failed: {payload.get('error')}"
            )
        result = _normalize_execution_result(payload, adapter="mcp")
        audit = result.audit
        if audit is None:
            return result
        return result.model_copy(
            update={
                "audit": audit.model_copy(
                    update={
                        "database_id": audit.database_id or database_id,
                        "catalog_name": audit.catalog_name or catalog_name,
                        "schema_name": audit.schema_name or schema_name,
                        "row_limit": audit.row_limit or limit,
                    }
                )
            }
        )

    def get_database_dialect(self, database_id: int) -> str | None:
        """Return database backend from MCP metadata."""

        return _normalize_database(_as_dict(self.get_database_raw(database_id))).backend

    def list_semantic_layers(self) -> list[dict[str, Any]]:
        raise SupersetAdapterNotImplementedError(
            "The MCP adapter does not publish semantic layers."
        )

    def create_semantic_layer(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise SupersetAdapterNotImplementedError(
            "The MCP adapter does not publish semantic layers."
        )

    def update_semantic_layer(
        self,
        uuid: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise SupersetAdapterNotImplementedError(
            "The MCP adapter does not publish semantic layers."
        )

    def delete_semantic_layer(self, uuid: str) -> None:
        raise SupersetAdapterNotImplementedError(
            "The MCP adapter does not publish semantic layers."
        )

    def create_semantic_views(
        self,
        views: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise SupersetAdapterNotImplementedError(
            "The MCP adapter does not publish semantic layers."
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.config.superset_auth_mode == "user_session":
            if not self.request_auth or not self.request_auth.has_credentials():
                raise SupersetAuthError(
                    "Superset MCP user-session auth requires request cookies or "
                    "Authorization.",
                    status_code=401,
                )
            headers.update(self.request_auth.headers())
            if self.request_auth.cookie_header:
                headers["Cookie"] = self.request_auth.cookie_header
            return headers
        token = self.config.superset_mcp_auth_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as ex:
            body = ex.response.text[:500]
            if ex.response.status_code in {401, 403}:
                raise SupersetAuthError(
                    f"Superset MCP auth failed with HTTP "
                    f"{ex.response.status_code}: {body}",
                    status_code=ex.response.status_code,
                ) from ex
            raise SupersetAdapterError(
                f"Superset MCP request failed with HTTP "
                f"{ex.response.status_code}: {body}"
            ) from ex


def _unwrap_mcp_result(result: Any) -> Any:
    if isinstance(result, dict):
        if "structuredContent" in result:
            return result["structuredContent"]
        if "structured_content" in result:
            return result["structured_content"]
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                if first.get("type") == "text" and isinstance(first.get("text"), str):
                    return _parse_json_text(first["text"])
                if "json" in first:
                    return first["json"]
        if "result" in result:
            return result["result"]
    return result


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"text": text}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise SupersetAdapterError(f"Expected object payload from Superset MCP: {value!r}")
