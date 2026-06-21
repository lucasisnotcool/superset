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

from pydantic import BaseModel

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.schemas import AuditInfo, ExecutionResult


class DatabaseSummary(BaseModel):
    id: int
    name: str
    backend: str | None = None


class ColumnSummary(BaseModel):
    name: str
    type: str | None = None
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
    ) -> AgentContext:
        """Build compact metadata context for the agent."""

    def execute_sql(
        self,
        *,
        database_id: int,
        sql: str,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        limit: int = 1000,
    ) -> ExecutionResult:
        """Execute validated SQL and return a capped result."""

    def get_database_dialect(self, database_id: int) -> str | None:
        """Return the SQL dialect/backend for validation and prompting."""


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
            if schema_name is not None:
                query = query.filter_by(schema=schema_name)
            if dataset_ids:
                query = query.filter(SqlaTable.id.in_(dataset_ids))
            else:
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
    ) -> ExecutionResult:
        with self._app.app_context():
            from superset import db
            from superset.models.core import Database

            database = db.session.query(Database).filter_by(id=database_id).one()
            dataframe = database.get_df(sql, schema=schema_name)
            if len(dataframe) > limit:
                dataframe = dataframe.head(limit)
            return ExecutionResult(
                columns=list(dataframe.columns),
                rows=dataframe.to_dict(orient="records"),
                row_count=len(dataframe),
                audit=AuditInfo(
                    adapter="local",
                    executed_sql=sql,
                    database_id=database_id,
                    catalog_name=catalog_name,
                    schema_name=schema_name,
                    row_limit=limit,
                    source="local_superset_client",
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

    @staticmethod
    def _serialize_dataset(dataset: Any) -> DatasetMetadata:
        columns = [
            ColumnSummary(
                name=column.column_name,
                type=column.type,
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
