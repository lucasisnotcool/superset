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

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.persistence.models import (
    AiAgentSemanticProject,
    AiAgentSemanticProjectSchema,
)
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    SemanticProjectResolveRequest,
)
from superset_ai_agent.semantic_layer.uri_fingerprint import (
    fingerprint_database_identity,
    fingerprint_database_uri,
)


class SemanticProjectNotFoundError(KeyError):
    """Raised when a semantic project cannot be found for an identity."""


class SemanticProjectStore(Protocol):
    """Storage contract for schema-scoped semantic projects."""

    def resolve(
        self,
        request: SemanticProjectResolveRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        """Resolve or create the project for a database/catalog/schema."""

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        database_id: int | None = None,
        database_uri_fingerprint: str | None = None,
        catalog_name: str | None = None,
        schema_name: str | None = None,
    ) -> list[SemanticProject]:
        """List projects visible to an owner."""

    def get(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        """Return one project visible to an owner."""

    def update(
        self,
        project: SemanticProject,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        """Update project metadata."""

    def delete(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        """Archive a project owned by an owner."""


class InMemorySemanticProjectStore:
    """Process-local semantic project store for development and tests."""

    def __init__(self) -> None:
        self._projects: dict[str, SemanticProject] = {}

    def resolve(
        self,
        request: SemanticProjectResolveRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        fingerprint = _request_fingerprint(request)
        catalog_key = _catalog_key(request.catalog_name)
        requested = request.resolved_schema_names()
        candidates = [
            project
            for project in self._projects.values()
            if project.database_uri_fingerprint == fingerprint
            and _catalog_key(project.catalog_name) == catalog_key
            and project.deleted_at is None
            and project.status == "active"
        ]
        # Prefer a project where the requested schema is the primary, then any
        # project whose membership set already covers it (so reopening on a
        # non-primary schema finds the same project rather than fragmenting).
        match = next(
            (p for p in candidates if p.schema_name == request.schema_name), None
        ) or next(
            (p for p in candidates if request.schema_name in p.schema_names), None
        )
        if match is not None:
            merged = _merge_schema_names(match.schema_names, requested)
            if merged != match.schema_names:
                match = match.model_copy(update={"schema_names": merged})
                self._projects[match.id] = match
            return _with_permission(match, owner_id)
        if not request.create_if_missing:
            raise SemanticProjectNotFoundError(request.schema_name)
        project = _project_from_request(
            request,
            owner_id=owner_id,
            database_uri_fingerprint=fingerprint,
        )
        self._projects[project.id] = project
        return project.model_copy(deep=True)

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        database_id: int | None = None,
        database_uri_fingerprint: str | None = None,
        catalog_name: str | None = None,
        schema_name: str | None = None,
    ) -> list[SemanticProject]:
        projects = [
            _with_permission(project, owner_id)
            for project in self._projects.values()
            if _is_visible(project, owner_id)
            and project.deleted_at is None
            and project.status == "active"
            and (database_id is None or project.default_database_id == database_id)
            and (
                database_uri_fingerprint is None
                or project.database_uri_fingerprint == database_uri_fingerprint
            )
            and (
                catalog_name is None
                or _catalog_key(project.catalog_name) == _catalog_key(catalog_name)
            )
            and (schema_name is None or schema_name in project.schema_names)
        ]
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def get(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        project = self._projects.get(project_id)
        if (
            project is None
            or project.deleted_at is not None
            or project.status != "active"
            or not _is_visible(project, owner_id)
        ):
            raise SemanticProjectNotFoundError(project_id)
        return _with_permission(project, owner_id)

    def update(
        self,
        project: SemanticProject,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        existing = self.get(project.id, owner_id=owner_id)
        if existing.permission != "admin":
            raise PermissionError("Only project owners can update project metadata.")
        project = project.model_copy(update={"updated_at": _utc_now()})
        self._projects[project.id] = project
        return project.model_copy(deep=True)

    def delete(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        project = self.get(project_id, owner_id=owner_id)
        if project.permission != "admin":
            raise PermissionError("Only project owners can archive semantic projects.")
        self._projects[project_id] = project.model_copy(
            update={
                "status": "archived",
                "deleted_at": _utc_now(),
                "updated_at": _utc_now(),
            }
        )


class SqlAlchemySemanticProjectStore:
    """SQLAlchemy-backed semantic project store."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def resolve(
        self,
        request: SemanticProjectResolveRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        fingerprint = _request_fingerprint(request)
        catalog_key = _catalog_key(request.catalog_name)
        requested = request.resolved_schema_names()
        with self.session_factory() as session:
            scope = (
                AiAgentSemanticProject.database_uri_fingerprint == fingerprint,
                AiAgentSemanticProject.catalog_name == catalog_key,
                AiAgentSemanticProject.deleted_at.is_(None),
                AiAgentSemanticProject.status == "active",
            )
            # Prefer a project whose primary schema matches; fall back to one whose
            # membership set already covers the requested schema.
            model = (
                session.execute(
                    select(AiAgentSemanticProject).where(
                        *scope,
                        AiAgentSemanticProject.schema_name == request.schema_name,
                    )
                )
                .scalars()
                .one_or_none()
            )
            if model is None:
                membership = select(AiAgentSemanticProjectSchema.project_id).where(
                    AiAgentSemanticProjectSchema.schema_name == request.schema_name
                )
                model = (
                    session.execute(
                        select(AiAgentSemanticProject)
                        .where(*scope, AiAgentSemanticProject.id.in_(membership))
                        .order_by(AiAgentSemanticProject.updated_at.desc())
                    )
                    .scalars()
                    .first()
                )
            if model is not None:
                project = _project_from_model(
                    model,
                    schema_names=_load_schema_names(
                        session, model.id, model.schema_name
                    ),
                )
                merged = _merge_schema_names(project.schema_names, requested)
                if merged != project.schema_names:
                    project = project.model_copy(update={"schema_names": merged})
                    _sync_membership_rows(session, project)
                    model.updated_at = _utc_now()
                    session.commit()
                return _with_permission(project, owner_id)
            if not request.create_if_missing:
                raise SemanticProjectNotFoundError(request.schema_name)
            project = _project_from_request(
                request,
                owner_id=owner_id,
                database_uri_fingerprint=fingerprint,
            )
            session.add(_project_to_model(project))
            _sync_membership_rows(session, project)
            session.commit()
            return project

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        database_id: int | None = None,
        database_uri_fingerprint: str | None = None,
        catalog_name: str | None = None,
        schema_name: str | None = None,
    ) -> list[SemanticProject]:
        with self.session_factory() as session:
            query = select(AiAgentSemanticProject).where(
                AiAgentSemanticProject.deleted_at.is_(None),
                AiAgentSemanticProject.status == "active",
                or_(
                    AiAgentSemanticProject.owner_id == owner_id,
                    AiAgentSemanticProject.visibility == "db_access",
                ),
            )
            if database_id is not None:
                query = query.where(
                    AiAgentSemanticProject.default_database_id == database_id
                )
            if database_uri_fingerprint is not None:
                query = query.where(
                    AiAgentSemanticProject.database_uri_fingerprint
                    == database_uri_fingerprint
                )
            if catalog_name is not None:
                query = query.where(
                    AiAgentSemanticProject.catalog_name == _catalog_key(catalog_name)
                )
            if schema_name is not None:
                membership = select(AiAgentSemanticProjectSchema.project_id).where(
                    AiAgentSemanticProjectSchema.schema_name == schema_name
                )
                query = query.where(
                    or_(
                        AiAgentSemanticProject.schema_name == schema_name,
                        AiAgentSemanticProject.id.in_(membership),
                    )
                )
            models = (
                session.execute(query.order_by(AiAgentSemanticProject.updated_at.desc()))
                .scalars()
                .all()
            )
            return [
                _with_permission(
                    _project_from_model(
                        model,
                        schema_names=_load_schema_names(
                            session, model.id, model.schema_name
                        ),
                    ),
                    owner_id,
                )
                for model in models
            ]

    def get(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        with self.session_factory() as session:
            model = session.get(AiAgentSemanticProject, project_id)
            if model is None:
                raise SemanticProjectNotFoundError(project_id)
            project = _project_from_model(
                model,
                schema_names=_load_schema_names(session, model.id, model.schema_name),
            )
            if (
                project.deleted_at is not None
                or project.status != "active"
                or not _is_visible(project, owner_id)
            ):
                raise SemanticProjectNotFoundError(project_id)
            return _with_permission(project, owner_id)

    def update(
        self,
        project: SemanticProject,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticProject:
        existing = self.get(project.id, owner_id=owner_id)
        if existing.permission != "admin":
            raise PermissionError("Only project owners can update project metadata.")
        with self.session_factory() as session:
            model = session.get(AiAgentSemanticProject, project.id)
            if model is None:
                raise SemanticProjectNotFoundError(project.id)
            model.name = project.name
            model.description = project.description
            model.visibility = project.visibility
            model.current_version_id = project.current_version_id
            model.updated_at = _utc_now()
            session.commit()
        return self.get(project.id, owner_id=owner_id)

    def delete(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        existing = self.get(project_id, owner_id=owner_id)
        if existing.permission != "admin":
            raise PermissionError("Only project owners can archive semantic projects.")
        with self.session_factory() as session:
            model = session.get(AiAgentSemanticProject, project_id)
            if model is None:
                raise SemanticProjectNotFoundError(project_id)
            model.status = "archived"
            model.deleted_at = _utc_now()
            model.updated_at = model.deleted_at
            session.commit()


def _request_fingerprint(request: SemanticProjectResolveRequest) -> str:
    if request.database_uri_fingerprint:
        return request.database_uri_fingerprint
    if request.supplied_uri:
        return fingerprint_database_uri(request.supplied_uri)
    return fingerprint_database_identity(database_id=request.database_id)


def _project_from_request(
    request: SemanticProjectResolveRequest,
    *,
    owner_id: str,
    database_uri_fingerprint: str,
) -> SemanticProject:
    database_label = request.database_label or f"Database {request.database_id}"
    catalog_suffix = f".{request.catalog_name}" if request.catalog_name else ""
    name = f"{database_label}{catalog_suffix}.{request.schema_name}"
    return SemanticProject(
        name=name,
        owner_id=owner_id,
        database_uri_fingerprint=database_uri_fingerprint,
        database_backend=request.database_backend,
        database_label=request.database_label,
        catalog_name=request.catalog_name,
        schema_name=request.schema_name,
        schema_names=request.resolved_schema_names(),
        schema_display_name=request.schema_name,
        default_database_id=request.database_id,
    )


def _project_to_model(project: SemanticProject) -> AiAgentSemanticProject:
    return AiAgentSemanticProject(
        id=project.id,
        name=project.name,
        description=project.description,
        owner_id=project.owner_id,
        database_uri_fingerprint=project.database_uri_fingerprint,
        database_backend=project.database_backend,
        database_label=project.database_label,
        catalog_name=_catalog_key(project.catalog_name),
        schema_name=project.schema_name,
        schema_display_name=project.schema_display_name,
        default_database_id=project.default_database_id,
        visibility=project.visibility,
        status=project.status,
        current_version_id=project.current_version_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
        deleted_at=project.deleted_at,
    )


def _project_from_model(
    model: AiAgentSemanticProject,
    *,
    schema_names: list[str] | None = None,
) -> SemanticProject:
    return SemanticProject(
        id=model.id,
        name=model.name,
        description=model.description,
        owner_id=model.owner_id,
        database_uri_fingerprint=model.database_uri_fingerprint,
        database_backend=model.database_backend,
        database_label=model.database_label,
        catalog_name=model.catalog_name or None,
        schema_name=model.schema_name,
        schema_names=schema_names or [model.schema_name],
        schema_display_name=model.schema_display_name,
        default_database_id=model.default_database_id,
        visibility=model.visibility,
        status=model.status,
        current_version_id=model.current_version_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _merge_schema_names(existing: list[str], requested: list[str]) -> list[str]:
    """Append any requested schemas not already in ``existing`` (primary unchanged)."""

    merged = list(existing)
    for name in requested:
        if name and name not in merged:
            merged.append(name)
    return merged


def _load_schema_names(
    session: Session, project_id: str, primary: str
) -> list[str]:
    """Load a project's ordered schema set, falling back to its primary schema.

    The fallback keeps projects readable before the backfill migration has run
    (or in test databases created straight from metadata without memberships).
    """

    rows = (
        session.execute(
            select(AiAgentSemanticProjectSchema)
            .where(AiAgentSemanticProjectSchema.project_id == project_id)
            .order_by(AiAgentSemanticProjectSchema.position)
        )
        .scalars()
        .all()
    )
    names = [row.schema_name for row in rows if row.schema_name]
    return names or [primary]


def _sync_membership_rows(session: Session, project: SemanticProject) -> None:
    """Insert membership rows for any new schemas; keep ``position`` aligned.

    Additive only — removing a schema from a project is an explicit operation
    (D3), never a side effect of resolve, so no rows are deleted here.
    """

    existing = {
        row.schema_name: row
        for row in session.execute(
            select(AiAgentSemanticProjectSchema).where(
                AiAgentSemanticProjectSchema.project_id == project.id
            )
        )
        .scalars()
        .all()
    }
    for position, schema in enumerate(project.schema_names):
        row = existing.get(schema)
        if row is None:
            session.add(
                AiAgentSemanticProjectSchema(
                    id=str(uuid4()),
                    project_id=project.id,
                    schema_name=schema,
                    position=position,
                    created_at=_utc_now(),
                )
            )
        elif row.position != position:
            row.position = position


def _is_visible(project: SemanticProject, owner_id: str) -> bool:
    return project.owner_id == owner_id or project.visibility == "db_access"


def _with_permission(project: SemanticProject, owner_id: str) -> SemanticProject:
    if project.owner_id == owner_id:
        return project.model_copy(update={"permission": "admin"}, deep=True)
    if project.visibility == "db_access":
        return project.model_copy(update={"permission": "read"}, deep=True)
    return project.model_copy(update={"permission": "read"}, deep=True)


def _catalog_key(catalog_name: str | None) -> str:
    return catalog_name or ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
