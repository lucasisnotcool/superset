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

import logging
import os
from functools import cached_property
from typing import Any, Protocol

from pydantic import BaseModel, Field

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.schemas import AuditInfo, ExecutionResult, SqlExecutionSource
from superset_ai_agent.semantic_layer.uri_fingerprint import (
    fingerprint_database_uri,
)


class DatabaseSummary(BaseModel):
    id: int
    name: str
    backend: str | None = None


class DatabaseIdentity(BaseModel):
    """Non-secret database identity used for semantic-layer matching."""

    database_id: int
    database_name: str
    backend: str | None = None
    driver: str | None = None
    uri_fingerprint: str
    catalog_name: str | None = None
    schema_names: list[str] = Field(default_factory=list)


class ColumnSummary(BaseModel):
    name: str
    type: str | None = None
    #: Superset ``GenericDataType`` family name (``TEMPORAL``/``NUMERIC``/
    #: ``STRING``/``BOOLEAN``) when known. A deterministic fallback for columns the
    #: catalog left untyped — see ``semantic_layer.column_identity``.
    type_generic: str | None = None
    is_dttm: bool = False
    description: str | None = None


class MetricSummary(BaseModel):
    name: str
    expression: str | None = None
    description: str | None = None


class DatasetSummary(BaseModel):
    id: int
    table_name: str
    schema_name: str | None = None
    database_id: int
    description: str | None = None


class DatasetMetadata(DatasetSummary):
    columns: list[ColumnSummary]
    metrics: list[MetricSummary]


class AgentContext(BaseModel):
    database: DatabaseSummary
    datasets: list[DatasetMetadata]


class SupersetAdapterNotImplementedError(NotImplementedError):
    """Raised when a configured adapter mode is only a transport skeleton."""


class SupersetAdapterError(RuntimeError):
    """Raised when a Superset adapter cannot complete a request."""


class SupersetAuthError(SupersetAdapterError):
    """Raised when Superset rejects request-scoped or service auth."""

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


class SupersetClient(Protocol):
    """Handoff-friendly Superset integration contract for agent builders."""

    def list_databases(self) -> list[DatabaseSummary]:
        """List databases available to the current integration context."""

    def get_database_identity(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> DatabaseIdentity:
        """Return non-secret database identity for semantic-layer matching."""

    def list_database_schemas(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> list[str]:
        """List schemas visible to the current integration context."""

    def list_datasets(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        limit: int = 8,
    ) -> list[DatasetMetadata]:
        """List dataset metadata for text-to-SQL context."""

    def get_agent_context(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
        include_datasets: bool = True,
    ) -> AgentContext:
        """Build compact metadata context for the agent.

        ``include_datasets=False`` returns just the database shell (no dataset
        scan), for callers that replace ``datasets`` with their own candidates.
        """

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
        """Execute validated SQL and return a capped result."""

    def get_database_dialect(self, database_id: int) -> str | None:
        """Return the SQL dialect/backend for validation and prompting."""

    def list_semantic_layers(self) -> list[dict[str, Any]]:
        """List Superset semantic layers through the governed adapter."""

    def create_semantic_layer(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a Superset semantic layer through the governed adapter."""

    def update_semantic_layer(
        self,
        uuid: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a Superset semantic layer through the governed adapter."""

    def delete_semantic_layer(self, uuid: str) -> None:
        """Delete a Superset semantic layer through the governed adapter."""

    def create_semantic_views(
        self,
        views: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create Superset semantic views through the governed adapter."""


class LocalSupersetClient:
    """Local development adapter that imports Superset in this process.

    This keeps the POC easy to run. Production agents should implement the same
    SupersetClient protocol via Superset MCP or authenticated REST calls.
    """

    def __init__(self, config: AgentConfig):
        self.config = config

    @cached_property
    def _app(self) -> Any:
        # Local dev Superset often uses the default SECRET_KEY. A POC-only
        # fallback lets the local adapter initialize without enabling Flask
        # debug mode. Real deployments should use the REST/MCP adapter instead.
        os.environ.setdefault(
            "SUPERSET_SECRET_KEY",
            self.config.local_superset_secret_key,
        )
        self._configure_superset_logging()
        from superset.app import create_app

        app = create_app()
        self._configure_superset_logging()
        return app

    def list_databases(self) -> list[DatabaseSummary]:
        with self._app.app_context():
            from superset import db
            from superset.models.core import Database

            databases = db.session.query(Database).order_by(Database.id).all()
            return [
                DatabaseSummary(
                    id=database.id,
                    name=database.database_name,
                    backend=getattr(database, "backend", None),
                )
                for database in databases
            ]

    def get_database_identity(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
    ) -> DatabaseIdentity:
        with self._app.app_context():
            from superset import db
            from superset.models.core import Database

            database = db.session.query(Database).filter_by(id=database_id).one()
            return DatabaseIdentity(
                database_id=database.id,
                database_name=database.database_name,
                backend=getattr(database, "backend", None),
                driver=getattr(database, "driver", None),
                uri_fingerprint=fingerprint_database_uri(
                    database.sqlalchemy_uri_decrypted
                ),
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
        with self._app.app_context():
            from superset import db
            from superset.models.core import Database

            database = db.session.query(Database).filter_by(id=database_id).one()
            return sorted(
                database.get_all_schema_names(
                    catalog=catalog_name,
                    cache=database.schema_cache_enabled,
                    cache_timeout=database.schema_cache_timeout or None,
                    force=False,
                )
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
        with self._app.app_context():
            from superset import db
            from superset.connectors.sqla.models import SqlaTable

            query = db.session.query(SqlaTable).filter_by(database_id=database_id)
            if dataset_ids:
                # An explicit id selection is authoritative: it identifies datasets
                # by primary key, so it must NOT be further narrowed by schema_name
                # (that would silently drop a valid cross-schema selection). The
                # database_id filter above still bounds it to the requested DB.
                query = query.filter(SqlaTable.id.in_(dataset_ids))
            else:
                if schema_name is not None:
                    query = query.filter_by(schema=schema_name)
                query = query.order_by(SqlaTable.id).limit(limit)
            return [self._serialize_dataset(dataset) for dataset in query.all()]

    def get_agent_context(
        self,
        *,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: list[int] | None = None,
    ) -> AgentContext:
        databases = {database.id: database for database in self.list_databases()}
        database = databases.get(database_id)
        if not database:
            raise ValueError(f"Database {database_id} was not found.")

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
        with self._app.app_context():
            from superset import db
            from superset.models.core import Database
            from superset_ai_agent.tools.sql_policy import apply_limit, classify_sql

            database = db.session.query(Database).filter_by(id=database_id).one()
            engine = getattr(database, "backend", None)

            # R6/R-LOCAL: the local adapter executes directly against the engine
            # with no raise_for_access/RLS backstop, so it must defend itself —
            # independently of whatever the graph validated — and fail closed on
            # anything not classified read-only. Honours the same policy mode as
            # the graph so a permissive multi-statement read-only script is not
            # refused here after the graph allowed it.
            classification = classify_sql(
                sql, engine=engine, policy_mode=self.config.sql_policy_mode
            )
            if not classification.is_read_only:
                raise ValueError(
                    "Refusing to execute non-read-only SQL on the local adapter: "
                    f"{classification.reason}"
                )

            # Push the cap into the query (apply_limit is a no-op when a
            # top-level LIMIT already exists) so we do not materialise an
            # unbounded result set into memory before truncating.
            bounded_sql = apply_limit(sql, engine=engine, default_limit=limit)
            dataframe = database.get_df(bounded_sql, schema=schema_name)
            if len(dataframe) > limit:
                dataframe = dataframe.head(limit)
            return ExecutionResult(
                columns=list(dataframe.columns),
                rows=dataframe.to_dict(orient="records"),
                row_count=len(dataframe),
                audit=AuditInfo(
                    adapter="local",
                    executed_sql=bounded_sql,
                    database_id=database_id,
                    catalog_name=catalog_name,
                    schema_name=schema_name,
                    row_limit=limit,
                    client_id=source.client_id if source else None,
                    sql_editor_id=source.sql_editor_id if source else None,
                    tab=source.tab if source else None,
                    source_hash=source.request_id if source else None,
                    source=source.source if source else "local_superset_client",
                ),
            )

    def get_database_dialect(self, database_id: int) -> str | None:
        with self._app.app_context():
            from superset import db
            from superset.models.core import Database

            database = db.session.query(Database).filter_by(id=database_id).one()
            backend = getattr(database, "backend", None)
            if backend == "sqlite":
                return "sqlite"
            return backend

    def list_semantic_layers(self) -> list[dict[str, Any]]:
        raise SupersetAdapterNotImplementedError(
            "The local Superset adapter does not publish semantic layers."
        )

    def create_semantic_layer(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise SupersetAdapterNotImplementedError(
            "The local Superset adapter does not publish semantic layers."
        )

    def update_semantic_layer(
        self,
        uuid: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise SupersetAdapterNotImplementedError(
            "The local Superset adapter does not publish semantic layers."
        )

    def delete_semantic_layer(self, uuid: str) -> None:
        raise SupersetAdapterNotImplementedError(
            "The local Superset adapter does not publish semantic layers."
        )

    def create_semantic_views(
        self,
        views: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise SupersetAdapterNotImplementedError(
            "The local Superset adapter does not publish semantic layers."
        )

    @staticmethod
    def _generic_type_name(column: Any) -> str | None:
        """Superset ``GenericDataType`` family name for a column, or ``None``.

        ``type_generic`` is an ``IntEnum`` (``TEMPORAL``/``NUMERIC``/``STRING``/
        ``BOOLEAN``); we keep just its name so the wren layer never imports
        Superset enums. Tolerant of columns/adapters that don't expose it.
        """

        generic = getattr(column, "type_generic", None)
        if generic is None:
            return None
        return getattr(generic, "name", None) or str(generic)

    @staticmethod
    def _serialize_dataset(dataset: Any) -> DatasetMetadata:
        columns = [
            ColumnSummary(
                name=column.column_name,
                type=column.type,
                type_generic=LocalSupersetClient._generic_type_name(column),
                is_dttm=bool(column.is_dttm),
                description=column.description,
            )
            for column in sorted(dataset.columns, key=lambda col: col.column_name or "")
        ]
        metrics = [
            MetricSummary(
                name=metric.metric_name,
                expression=metric.expression,
                description=metric.description,
            )
            for metric in sorted(dataset.metrics, key=lambda met: met.metric_name or "")
        ]
        return DatasetMetadata(
            id=dataset.id,
            table_name=dataset.table_name,
            schema_name=dataset.schema,
            database_id=dataset.database_id,
            description=dataset.description,
            columns=columns,
            metrics=metrics,
        )

    def _configure_superset_logging(self) -> None:
        if not self.config.suppress_superset_logs:
            return
        for logger_name in [
            "superset",
            "superset.core.mcp",
            "superset.mcp_service",
            "alembic",
            "httpcore",
        ]:
            logging.getLogger(logger_name).setLevel(logging.WARNING)
