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

import time
from typing import Any

import httpx

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
    SupersetAdapterError,
)
from superset_ai_agent.schemas import ExecutionResult


class SupersetRestClient:
    """Superset REST adapter with high-level and low-level controls."""

    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
    ):
        self.config = config
        self.base_url = config.superset_base_url.rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(60.0)
        self._access_token = config.superset_auth_token
        self._csrf_token = config.superset_csrf_token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform a low-level authenticated REST request."""

        self._ensure_authenticated()
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            request_headers["X-CSRFToken"] = self._ensure_csrf_token()

        with self._client() as client:
            response = client.request(
                method,
                self._url(path),
                params=params,
                json=json,
                headers=request_headers,
            )
        self._raise_for_status(response)
        data = response.json()
        if not isinstance(data, dict):
            raise SupersetAdapterError(
                f"Superset REST {method} {path} returned a non-object payload."
            )
        return data

    def list_databases_raw(
        self,
        *,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Return raw `GET /api/v1/database/` payload."""

        return self.request(
            "GET",
            "/api/v1/database/",
            params={
                "q": (
                    "(page:0,page_size:%s,order_column:database_name,"
                    "order_direction:asc)"
                )
                % page_size
            },
        )

    def get_database_raw(self, database_id: int) -> dict[str, Any]:
        """Return raw `GET /api/v1/database/{id}` payload."""

        return self.request("GET", f"/api/v1/database/{database_id}")

    def list_datasets_raw(
        self,
        *,
        database_id: int,
        limit: int,
    ) -> dict[str, Any]:
        """Return raw `GET /api/v1/dataset/` payload for a database."""

        return self.request(
            "GET",
            "/api/v1/dataset/",
            params={
                "q": (
                    "(page:0,page_size:%s,order_column:table_name,"
                    "order_direction:asc,filters:!((col:database,opr:rel_o_m,"
                    "value:%s)))"
                )
                % (limit, database_id)
            },
        )

    def get_dataset_raw(self, dataset_id: int) -> dict[str, Any]:
        """Return raw `GET /api/v1/dataset/{id}` payload."""

        return self.request("GET", f"/api/v1/dataset/{dataset_id}")

    def execute_sql_raw(
        self,
        *,
        database_id: int,
        sql: str,
        schema_name: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Return raw SQL Lab execution payload."""

        payload = {
            "database_id": database_id,
            "sql": sql,
            "catalog": None,
            "schema": schema_name,
            "queryLimit": limit,
            "runAsync": False,
            "expand_data": True,
        }
        response = self.request("POST", "/api/v1/sqllab/execute/", json=payload)
        query = response.get("query")
        if (
            isinstance(query, dict)
            and query.get("resultsKey")
            and "data" not in response
        ):
            return self.get_sqllab_results_raw(str(query["resultsKey"]))
        return response

    def get_sqllab_results_raw(self, key: str) -> dict[str, Any]:
        """Poll and return raw SQL Lab results for an async results key."""

        last_payload: dict[str, Any] | None = None
        for _ in range(max(self.config.superset_sql_poll_attempts, 1)):
            last_payload = self.request(
                "GET",
                "/api/v1/sqllab/results/",
                params={"q": f"(key:{key})"},
            )
            if "data" in last_payload or last_payload.get("status") == "success":
                return last_payload
            time.sleep(self.config.superset_sql_poll_interval_seconds)
        raise SupersetAdapterError(
            f"Timed out waiting for SQL Lab results for key {key!r}: {last_payload}"
        )

    def list_databases(self) -> list[DatabaseSummary]:
        """List databases through Superset REST."""

        payload = self.list_databases_raw()
        return [_normalize_database(item) for item in _items(payload, "databases")]

    def list_datasets(
        self,
        *,
        database_id: int,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ) -> list[DatasetMetadata]:
        """List dataset metadata through Superset REST."""

        if dataset_ids:
            return [
                _normalize_dataset(self.get_dataset_raw(dataset_id))
                for dataset_id in dataset_ids
            ]
        payload = self.list_datasets_raw(database_id=database_id, limit=limit)
        summaries = [_normalize_dataset(item) for item in _items(payload, "datasets")]
        return [
            _normalize_dataset(self.get_dataset_raw(summary.id))
            for summary in summaries
            if summary.id
        ]

    def get_agent_context(
        self,
        *,
        database_id: int,
        dataset_ids: list[int] | None = None,
    ) -> AgentContext:
        """Build compact metadata context from REST database and dataset payloads."""

        database = _normalize_database(self.get_database_raw(database_id))
        datasets = self.list_datasets(
            database_id=database_id,
            dataset_ids=dataset_ids,
            limit=self.config.max_context_datasets,
        )
        return AgentContext(database=database, datasets=datasets)

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        schema_name: str | None = None,
        limit: int = 1000,
    ) -> ExecutionResult:
        """Execute SQL through SQL Lab REST and normalize the result."""

        return _normalize_execution_result(
            self.execute_sql_raw(
                database_id=database_id,
                sql=sql,
                schema_name=schema_name,
                limit=limit,
            )
        )

    def get_database_dialect(self, database_id: int) -> str | None:
        """Return database backend from REST metadata."""

        return _normalize_database(self.get_database_raw(database_id)).backend

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self.transport)

    def _ensure_authenticated(self) -> None:
        if self._access_token or not self.config.superset_username:
            return
        if not self.config.superset_password:
            raise SupersetAdapterError(
                "SUPERSET_PASSWORD is required when SUPERSET_USERNAME is set."
            )
        with self._client() as client:
            response = client.post(
                self._url("/api/v1/security/login"),
                json={
                    "username": self.config.superset_username,
                    "password": self.config.superset_password,
                    "provider": self.config.superset_auth_provider,
                    "refresh": True,
                },
            )
        self._raise_for_status(response)
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise SupersetAdapterError("Superset login did not return access_token.")
        self._access_token = access_token

    def _ensure_csrf_token(self) -> str:
        if self._csrf_token:
            return self._csrf_token
        payload = self.request("GET", "/api/v1/security/csrf_token/")
        token = payload.get("result")
        if not isinstance(token, str) or not token:
            raise SupersetAdapterError("Superset did not return a CSRF token.")
        self._csrf_token = token
        return token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as ex:
            body = ex.response.text[:500]
            raise SupersetAdapterError(
                f"Superset REST request failed with HTTP "
                f"{ex.response.status_code}: {body}"
            ) from ex


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("result", payload)
    return value if isinstance(value, dict) else payload


def _items(payload: dict[str, Any], list_key: str) -> list[dict[str, Any]]:
    result = payload.get("result", payload)
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        value = result.get(list_key) or result.get("result")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_database(payload: dict[str, Any]) -> DatabaseSummary:
    data = _result(payload)
    name = data.get("database_name") or data.get("name") or ""
    return DatabaseSummary(
        id=int(data.get("id") or 0),
        name=str(name),
        backend=data.get("backend"),
    )


def _normalize_dataset(payload: dict[str, Any]) -> DatasetMetadata:
    data = _result(payload)
    database = data.get("database")
    database_id = data.get("database_id")
    if database_id is None and isinstance(database, dict):
        database_id = database.get("id")
    raw_columns = data.get("columns")
    columns: list[Any] = raw_columns if isinstance(raw_columns, list) else []
    raw_metrics = data.get("metrics")
    metrics: list[Any] = raw_metrics if isinstance(raw_metrics, list) else []
    return DatasetMetadata(
        id=int(data.get("id") or 0),
        table_name=str(data.get("table_name") or ""),
        schema_name=data.get("schema") or data.get("schema_name"),
        database_id=int(database_id or 0),
        description=data.get("description"),
        columns=sorted(
            [
                _normalize_column(column)
                for column in columns
                if isinstance(column, dict)
            ],
            key=lambda column: column.name,
        ),
        metrics=sorted(
            [
                _normalize_metric(metric)
                for metric in metrics
                if isinstance(metric, dict)
            ],
            key=lambda metric: metric.name,
        ),
    )


def _normalize_column(data: dict[str, Any]) -> ColumnSummary:
    return ColumnSummary(
        name=str(data.get("column_name") or data.get("name") or ""),
        type=data.get("type"),
        is_dttm=bool(data.get("is_dttm") or False),
        description=data.get("description"),
    )


def _normalize_metric(data: dict[str, Any]) -> MetricSummary:
    return MetricSummary(
        name=str(data.get("metric_name") or data.get("name") or ""),
        expression=data.get("expression"),
        description=data.get("description"),
    )


def _normalize_execution_result(payload: dict[str, Any]) -> ExecutionResult:
    result = _result(payload)
    rows = result.get("data") or result.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    columns = result.get("columns") or []
    row_count = result.get("row_count")
    if row_count is None:
        raw_rows_count = result.get("rows")
        row_count = raw_rows_count if isinstance(raw_rows_count, int) else len(rows)
    return ExecutionResult(
        columns=_normalize_column_names(columns, rows),
        rows=[row for row in rows if isinstance(row, dict)],
        row_count=int(row_count),
    )


def _normalize_column_names(
    columns: Any,
    rows: list[Any],
) -> list[str]:
    if isinstance(columns, list) and columns:
        names: list[str] = []
        for column in columns:
            if isinstance(column, dict):
                name = (
                    column.get("name") or column.get("column_name") or column.get("key")
                )
                if name:
                    names.append(str(name))
            elif isinstance(column, str):
                names.append(column)
        if names:
            return names
    first_row = next((row for row in rows if isinstance(row, dict)), None)
    return list(first_row.keys()) if first_row else []
