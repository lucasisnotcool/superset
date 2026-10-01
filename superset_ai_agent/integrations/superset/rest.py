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

import hashlib
import json as json_lib
import time
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

import httpx

from superset_ai_agent.auth import SupersetRequestAuth
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseIdentity,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
    SupersetAdapterError,
    SupersetAuthError,
)
from superset_ai_agent.schemas import AuditInfo, ExecutionResult, SqlExecutionSource


@dataclass(frozen=True)
class _SourceMarker:
    payload: dict[str, str]
    source_hash: str | None = None
    source: str | None = None


class SupersetRestClient:
    """Superset REST adapter with high-level and low-level controls."""

    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
        request_auth: SupersetRequestAuth | None = None,
    ):
        self.config = config
        self.base_url = config.superset_base_url.rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(60.0)
        self.request_auth = request_auth
        self._access_token = (
            None if self._uses_user_session_auth else config.superset_auth_token
        )
        self._csrf_token = (
            request_auth.csrf_token
            if self._uses_user_session_auth and request_auth
            else config.superset_csrf_token
        )
        self._http_client: httpx.Client | None = None

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

        return self._request(method, path, params=params, json=json, headers=headers)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_auth_retry: bool = True,
    ) -> dict[str, Any]:
        self._ensure_authenticated()
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            request_headers["X-CSRFToken"] = self._ensure_csrf_token()

        response = self._client().request(
            method,
            self._url(path),
            params=params,
            json=json,
            headers=request_headers,
        )
        try:
            self._raise_for_status(response)
        except SupersetAuthError:
            if allow_auth_retry and self._can_retry_service_auth():
                self._reset_service_auth()
                return self._request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers=headers,
                    allow_auth_retry=False,
                )
            raise
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

    def get_database_identity_raw(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> dict[str, Any]:
        """Return raw AI-agent database identity payload from Superset REST."""

        return self.request(
            "GET",
            f"/api/v1/ai-agent/database/{database_id}/identity",
            params={"catalog": catalog_name} if catalog_name else None,
        )

    def list_datasets_raw(
        self,
        *,
        database_id: int,
        schema_name: str | None = None,
        limit: int,
    ) -> dict[str, Any]:
        """Return raw `GET /api/v1/dataset/` payload for a database."""

        filters = (
            "((col:database,opr:rel_o_m,value:%s))" % database_id
            if schema_name is None
            else (
                "((col:database,opr:rel_o_m,value:%s),"
                "(col:schema,opr:eq,value:'%s'))"
            )
            % (database_id, schema_name.replace("'", "\\'"))
        )
        return self.request(
            "GET",
            "/api/v1/dataset/",
            params={
                "q": (
                    "(page:0,page_size:%s,order_column:table_name,"
                    "order_direction:asc,filters:!%s)"
                )
                % (limit, filters)
            },
        )

    #: Only the fields ``_normalize_dataset``/``_normalize_column``/
    #: ``_normalize_metric`` actually read. Projecting the dataset detail to these
    #: shrinks the per-dataset payload ~10x vs. the full columns+metrics dump
    #: (Superset honors this ``columns`` projection — see the dataset ``get``
    #: override). Pull this out of the hot ``list_datasets`` N+1 loop.
    _DATASET_DETAIL_COLUMNS = (
        "id",
        "table_name",
        "schema",
        "description",
        "database.id",
        "columns.column_name",
        "columns.type",
        "columns.is_dttm",
        "columns.description",
        "metrics.metric_name",
        "metrics.expression",
        "metrics.description",
    )

    def get_dataset_raw(self, dataset_id: int) -> dict[str, Any]:
        """Return raw `GET /api/v1/dataset/{id}` payload (projected).

        Only the fields the agent normalizes are requested, so a wide dataset
        doesn't ship its entire columns+metrics serialization on every call.
        """

        projection = ",".join(self._DATASET_DETAIL_COLUMNS)
        return self.request(
            "GET",
            f"/api/v1/dataset/{dataset_id}",
            params={"q": "(columns:!(%s))" % projection},
        )

    def execute_sql_raw(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        limit: int = 1000,
        source: SqlExecutionSource | None = None,
        marker: _SourceMarker | None = None,
    ) -> dict[str, Any]:
        """Return raw SQL Lab execution payload."""

        marker = marker or _source_marker(source)
        payload = {
            "database_id": database_id,
            "sql": sql,
            "catalog": catalog_name,
            "schema": schema_name,
            "queryLimit": limit,
            "runAsync": False,
            "expand_data": True,
            **marker.payload,
        }
        response = self.request("POST", "/api/v1/sqllab/execute/", json=payload)
        query = response.get("query")
        if (
            isinstance(query, dict)
            and query.get("resultsKey")
            and "data" not in response
        ):
            results = self.get_sqllab_results_raw(str(query["resultsKey"]))
            if isinstance(query, dict):
                results.setdefault("query", query)
            return results
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

    def get_database_identity(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> DatabaseIdentity:
        """Return non-secret database identity through Superset REST."""

        return _normalize_database_identity(
            self.get_database_identity_raw(
                database_id=database_id,
                catalog_name=catalog_name,
            )
        )

    def list_database_schemas(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> list[str]:
        """List schemas through the AI-agent database identity endpoint."""

        return self.get_database_identity(
            database_id=database_id,
            catalog_name=catalog_name,
        ).schema_names

    def list_datasets(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ) -> list[DatasetMetadata]:
        """List dataset metadata through Superset REST."""

        if dataset_ids:
            datasets = [
                _normalize_dataset(self.get_dataset_raw(dataset_id))
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
        payload = self.list_datasets_raw(
            database_id=database_id,
            schema_name=schema_name,
            limit=limit,
        )
        summaries = [_normalize_dataset(item) for item in _items(payload, "datasets")]
        return [
            _normalize_dataset(self.get_dataset_raw(summary.id))
            for summary in summaries
            if summary.id and _dataset_matches_scope(summary, schema_name=schema_name)
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
        """Build compact metadata context from REST database and dataset payloads.

        ``include_datasets=False`` returns just the database shell (no dataset
        scan) — for callers that immediately replace ``datasets`` with their own
        candidate set, so the per-dataset N+1 isn't paid twice.
        """

        database = _normalize_database(self.get_database_raw(database_id))
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
        """Execute SQL through SQL Lab REST and normalize the result."""

        marker = _source_marker(source)
        result = _normalize_execution_result(
            self.execute_sql_raw(
                database_id=database_id,
                sql=sql,
                catalog_name=catalog_name,
                schema_name=schema_name,
                limit=limit,
                source=source,
                marker=marker,
            ),
            adapter="rest",
        )
        return _with_request_audit(
            result,
            database_id=database_id,
            catalog_name=catalog_name,
            schema_name=schema_name,
            limit=limit,
            marker=marker,
        )

    def get_database_dialect(self, database_id: int) -> str | None:
        """Return database backend from REST metadata."""

        return _normalize_database(self.get_database_raw(database_id)).backend

    def list_semantic_layers(self) -> list[dict[str, Any]]:
        """List Superset semantic layers through REST."""

        payload = self.request("GET", "/api/v1/semantic_layer/")
        result = payload.get("result")
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        return []

    def create_semantic_layer(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a Superset semantic layer through REST."""

        return _result(
            self.request("POST", "/api/v1/semantic_layer/", json=payload)
        )

    def update_semantic_layer(
        self,
        uuid: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a Superset semantic layer through REST."""

        return _result(
            self.request("PUT", f"/api/v1/semantic_layer/{uuid}", json=payload)
        )

    def delete_semantic_layer(self, uuid: str) -> None:
        """Delete a Superset semantic layer through REST."""

        self.request("DELETE", f"/api/v1/semantic_layer/{uuid}")

    def create_semantic_views(
        self,
        views: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Bulk create Superset semantic views through REST."""

        return _result(
            self.request("POST", "/api/v1/semantic_view/", json={"views": views})
        )

    def _client(self) -> httpx.Client:
        if self._http_client is None:
            client_headers: dict[str, str] = {}
            client_cookies: dict[str, str] = {}
            if self._uses_user_session_auth:
                auth = self._required_request_auth()
                client_headers.update(auth.headers())
                client_cookies.update(auth.cookies())
            self._http_client = httpx.Client(
                timeout=self.timeout,
                transport=self.transport,
                headers=client_headers,
                cookies=client_cookies,
            )
        return self._http_client

    def close(self) -> None:
        """Close the underlying HTTP client when the adapter is disposed."""

        if self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def _ensure_authenticated(self) -> None:
        if self._uses_user_session_auth:
            self._required_request_auth()
            return
        if self._access_token or not self.config.superset_username:
            return
        if not self.config.superset_password:
            raise SupersetAdapterError(
                "SUPERSET_PASSWORD is required when SUPERSET_USERNAME is set."
            )
        response = self._client().post(
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
        if not self._uses_user_session_auth and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    @property
    def _uses_user_session_auth(self) -> bool:
        return self.config.superset_auth_mode == "user_session"

    def _required_request_auth(self) -> SupersetRequestAuth:
        if self.request_auth and self.request_auth.has_credentials():
            return self.request_auth
        raise SupersetAuthError(
            "Superset user-session auth requires request cookies or Authorization.",
            status_code=401,
        )

    def _can_retry_service_auth(self) -> bool:
        return (
            not self._uses_user_session_auth
            and not self.config.superset_auth_token
            and bool(self.config.superset_username)
        )

    def _reset_service_auth(self) -> None:
        self._access_token = None
        self._csrf_token = None
        self.close()

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
            if ex.response.status_code in {401, 403}:
                raise SupersetAuthError(
                    f"Superset REST auth failed with HTTP "
                    f"{ex.response.status_code}: {body}",
                    status_code=ex.response.status_code,
                ) from ex
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


def _normalize_database_identity(payload: dict[str, Any]) -> DatabaseIdentity:
    data = _result(payload)
    schemas = data.get("schemas")
    return DatabaseIdentity(
        database_id=int(data.get("database_id") or data.get("id") or 0),
        database_name=str(data.get("database_name") or data.get("name") or ""),
        backend=data.get("backend"),
        driver=data.get("driver"),
        uri_fingerprint=str(data.get("uri_fingerprint") or ""),
        catalog_name=data.get("catalog") or data.get("catalog_name"),
        schema_names=(
            [str(schema) for schema in schemas]
            if isinstance(schemas, list)
            else []
        ),
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


def _dataset_matches_scope(
    dataset: DatasetMetadata,
    *,
    schema_name: str | None,
) -> bool:
    return schema_name is None or dataset.schema_name == schema_name


def _normalize_execution_result(
    payload: dict[str, Any],
    *,
    adapter: Literal["rest", "mcp"] = "rest",
) -> ExecutionResult:
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
        audit=_normalize_audit_info(payload, adapter=adapter),
        is_truncated=int(row_count) > len(rows),
    )


def _normalize_audit_info(
    payload: dict[str, Any],
    *,
    adapter: Literal["rest", "mcp"],
) -> AuditInfo:
    result = _result(payload)
    query = result.get("query")
    if not isinstance(query, dict):
        query = payload.get("query")
    if not isinstance(query, dict):
        query = {}

    query_id = (
        result.get("query_id")
        or result.get("queryId")
        or query.get("query_id")
        or query.get("queryId")
        or query.get("id")
    )
    results_key = (
        result.get("resultsKey")
        or result.get("results_key")
        or query.get("resultsKey")
        or query.get("results_key")
    )
    executed_sql = (
        result.get("executed_sql")
        or result.get("executedSql")
        or query.get("executed_sql")
        or query.get("executedSql")
        or query.get("sql")
    )
    database_id = _optional_int(
        result.get("database_id")
        or result.get("databaseId")
        or query.get("database_id")
        or query.get("databaseId")
    )
    row_limit = _optional_int(
        result.get("queryLimit")
        or result.get("limit")
        or query.get("queryLimit")
        or query.get("limit")
    )
    timeout_seconds = _optional_int(
        result.get("timeout")
        or result.get("timeout_seconds")
        or query.get("timeout")
        or query.get("timeout_seconds")
    )
    client_id = (
        result.get("client_id")
        or result.get("clientId")
        or result.get("id")
        or query.get("client_id")
        or query.get("clientId")
        or query.get("id")
    )
    sql_editor_id = (
        result.get("sql_editor_id")
        or result.get("sqlEditorId")
        or query.get("sql_editor_id")
        or query.get("sqlEditorId")
    )
    tab = result.get("tab") or query.get("tab")
    return AuditInfo(
        adapter=adapter,
        query_id=query_id,
        results_key=str(results_key) if results_key is not None else None,
        executed_sql=str(executed_sql) if executed_sql is not None else None,
        database_id=database_id,
        catalog_name=result.get("catalog") or query.get("catalog"),
        schema_name=result.get("schema") or query.get("schema"),
        row_limit=row_limit,
        timeout_seconds=timeout_seconds,
        client_id=str(client_id) if client_id is not None else None,
        sql_editor_id=str(sql_editor_id) if sql_editor_id is not None else None,
        tab=str(tab) if tab is not None else None,
        source="sqllab_rest" if adapter == "rest" else "superset_mcp",
    )


def _with_request_audit(
    result: ExecutionResult,
    *,
    database_id: int,
    catalog_name: str | None,
    schema_name: str | None,
    limit: int,
    marker: _SourceMarker | None = None,
) -> ExecutionResult:
    audit = result.audit or AuditInfo(adapter="rest")
    payload = marker.payload if marker is not None else {}
    return result.model_copy(
        update={
            "audit": audit.model_copy(
                update={
                    "database_id": audit.database_id or database_id,
                    "catalog_name": audit.catalog_name or catalog_name,
                    "schema_name": audit.schema_name or schema_name,
                    "row_limit": audit.row_limit or limit,
                    "client_id": audit.client_id or payload.get("client_id"),
                    "sql_editor_id": audit.sql_editor_id
                    or payload.get("sql_editor_id"),
                    "tab": audit.tab or payload.get("tab"),
                    "source_hash": audit.source_hash
                    or (marker.source_hash if marker else None),
                    "source": marker.source if marker and marker.source else audit.source,
                }
            )
        }
    )


def _source_marker(source: SqlExecutionSource | None) -> _SourceMarker:
    if source is None:
        return _SourceMarker(payload={})

    source_hash = _source_hash(source)
    client_id = source.client_id or f"ai{uuid4().hex[:9]}"
    payload = {
        "client_id": client_id[:11],
        "sql_editor_id": source.sql_editor_id or source.source,
        "tab": source.tab or "AI Agent",
    }
    return _SourceMarker(
        payload=payload,
        source_hash=source_hash,
        source=source.source,
    )


def _source_hash(source: SqlExecutionSource) -> str:
    payload = source.model_dump(exclude_none=True, exclude={"client_id"})
    serialized = json_lib.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
