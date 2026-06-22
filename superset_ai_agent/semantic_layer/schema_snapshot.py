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

"""Persisted snapshot of a project's permission-filtered physical schema.

Used to keep physical (schema-aware) MDL validation working when live Superset
metadata cannot be fetched (a transient outage), so a hallucinated column is
still caught at activation/save time instead of degrading to structural-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.persistence.models import AiAgentSchemaSnapshot


class SchemaSnapshot(BaseModel):
    """Last-known schema for a semantic project."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    database_uri_fingerprint: str | None = None
    catalog_name: str | None = None
    schema_name: str | None = None
    tables: dict[str, list[str]] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SchemaSnapshotStore(Protocol):
    """Storage contract for project schema snapshots."""

    def upsert(self, snapshot: SchemaSnapshot) -> SchemaSnapshot:
        """Create or replace the snapshot for a project."""

    def get(self, project_id: str) -> SchemaSnapshot | None:
        """Return the latest snapshot for a project, if any."""


class InMemorySchemaSnapshotStore:
    """Process-local schema snapshot store for development and tests."""

    def __init__(self) -> None:
        self._snapshots: dict[str, SchemaSnapshot] = {}

    def upsert(self, snapshot: SchemaSnapshot) -> SchemaSnapshot:
        self._snapshots[snapshot.project_id] = snapshot.model_copy(deep=True)
        return snapshot.model_copy(deep=True)

    def get(self, project_id: str) -> SchemaSnapshot | None:
        snapshot = self._snapshots.get(project_id)
        return snapshot.model_copy(deep=True) if snapshot is not None else None


class SqlAlchemySchemaSnapshotStore:
    """SQLAlchemy-backed schema snapshot store (one row per project)."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def upsert(self, snapshot: SchemaSnapshot) -> SchemaSnapshot:
        with self.session_factory() as session:
            model = self._get_model(session, snapshot.project_id)
            if model is None:
                model = AiAgentSchemaSnapshot(id=snapshot.id)
                session.add(model)
            model.project_id = snapshot.project_id
            model.database_uri_fingerprint = snapshot.database_uri_fingerprint
            model.catalog_name = snapshot.catalog_name
            model.schema_name = snapshot.schema_name
            model.tables = snapshot.tables
            model.captured_at = snapshot.captured_at
            session.commit()
            return _from_model(model)

    def get(self, project_id: str) -> SchemaSnapshot | None:
        with self.session_factory() as session:
            model = self._get_model(session, project_id)
            return _from_model(model) if model is not None else None

    @staticmethod
    def _get_model(
        session: Session,
        project_id: str,
    ) -> AiAgentSchemaSnapshot | None:
        return (
            session.execute(
                select(AiAgentSchemaSnapshot).where(
                    AiAgentSchemaSnapshot.project_id == project_id
                )
            )
            .scalars()
            .one_or_none()
        )


def _from_model(model: AiAgentSchemaSnapshot) -> SchemaSnapshot:
    return SchemaSnapshot(
        id=model.id,
        project_id=model.project_id,
        database_uri_fingerprint=model.database_uri_fingerprint,
        catalog_name=model.catalog_name,
        schema_name=model.schema_name,
        tables=dict(model.tables or {}),
        captured_at=model.captured_at,
    )
