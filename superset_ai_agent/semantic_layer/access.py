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

from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from superset_ai_agent.auth import AgentIdentity
from superset_ai_agent.config import SemanticAccessMode
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.integrations.superset.client import AgentContext, DatabaseIdentity
from superset_ai_agent.semantic_layer.projects import SemanticProjectStore
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    SemanticProjectResolveRequest,
)


class SemanticPermission(str, Enum):
    """Semantic-layer permission levels."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class SemanticAccessLevel(str, Enum):
    """Strength of a semantic access proof."""

    PARTIAL = "partial"
    FULL = "full"


class SemanticAccessProof(BaseModel):
    """Proof that a user can use semantic assets for a database/schema scope."""

    owner_id: str
    proof_type: Literal["superset_database", "superset_dataset", "validated_uri"]
    database_id: int | None = None
    catalog_names: list[str] = Field(default_factory=list)
    schema_names: list[str] = Field(default_factory=list)
    dataset_ids: list[int] = Field(default_factory=list)
    database_uri_fingerprint: str
    access_level: SemanticAccessLevel
    expires_at: datetime | None = None


class SemanticAccessDecision(BaseModel):
    """Resolved permission decision for one semantic project."""

    project_id: str
    catalog_name: str | None = None
    schema_name: str
    permission: SemanticPermission
    access_level: SemanticAccessLevel
    allowed_dataset_ids: list[int] = Field(default_factory=list)
    reason: str


class SemanticAccessService:
    """Authoritative access policy for schema-scoped semantic projects."""

    def __init__(
        self,
        *,
        project_store: SemanticProjectStore,
        load_context: Callable[[ConversationScope], AgentContext],
        get_database_identity: (
            Callable[[int, str | None], DatabaseIdentity] | None
        ) = None,
        semantic_access_mode: SemanticAccessMode = "superset_or_uri",
        semantic_full_access_grants_write: bool = False,
    ) -> None:
        self.project_store = project_store
        self.load_context = load_context
        self.get_database_identity = get_database_identity
        self.semantic_access_mode = semantic_access_mode
        self.semantic_full_access_grants_write = semantic_full_access_grants_write

    def require_scope_permission(
        self,
        *,
        identity: AgentIdentity,
        scope: ConversationScope,
        permission: SemanticPermission = SemanticPermission.READ,
    ) -> AgentContext:
        """Prove the identity can use semantic assets for a Superset scope."""

        _ = identity, permission
        return self.load_context(scope)

    def resolve_project(
        self,
        *,
        identity: AgentIdentity,
        request: SemanticProjectResolveRequest,
        permission: SemanticPermission = SemanticPermission.READ,
    ) -> SemanticProject:
        """Resolve or create a schema project after proving Superset scope access."""

        self.require_scope_permission(
            identity=identity,
            scope=ConversationScope(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                dataset_ids=[],
            ),
            permission=permission,
        )
        resolved_request = self._request_with_database_identity(request)
        project = self.project_store.resolve(
            resolved_request,
            owner_id=identity.owner_id,
        )
        return self._require_project_permission(
            identity=identity,
            project=project,
            permission=permission,
        )

    def list_projects(
        self,
        *,
        identity: AgentIdentity,
        scope: ConversationScope,
    ) -> list[SemanticProject]:
        """List projects visible for a Superset-proven database/schema scope."""

        context = self.require_scope_permission(
            identity=identity,
            scope=scope,
            permission=SemanticPermission.READ,
        )
        database_identity = self._database_identity(
            database_id=scope.database_id,
            catalog_name=scope.catalog_name,
        )
        projects = self.project_store.list(
            owner_id=identity.owner_id,
            database_id=(
                None if database_identity is not None else scope.database_id
            ),
            database_uri_fingerprint=(
                database_identity.uri_fingerprint
                if database_identity is not None
                else None
            ),
            catalog_name=scope.catalog_name,
            schema_name=scope.schema_name,
        )
        return [
            self._project_with_permission(
                identity=identity,
                project=project,
                context=context,
            )
            for project in projects
        ]

    def require_project_permission(
        self,
        *,
        identity: AgentIdentity,
        project_id: str,
        permission: SemanticPermission,
    ) -> SemanticProject:
        """Return a project or raise when the identity lacks permission."""

        project = self.project_store.get(project_id, owner_id=identity.owner_id)
        return self._require_project_permission(
            identity=identity,
            project=project,
            permission=permission,
        )

    def _require_project_permission(
        self,
        *,
        identity: AgentIdentity,
        project: SemanticProject,
        permission: SemanticPermission,
    ) -> SemanticProject:
        context = None
        if project.default_database_id is not None:
            context = self.require_scope_permission(
                identity=identity,
                scope=ConversationScope(
                    database_id=project.default_database_id,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                    dataset_ids=[],
                ),
                permission=permission,
            )
        resolved = self._project_with_permission(
            identity=identity,
            project=project,
            context=context,
        )
        if not has_permission(resolved.permission, permission):
            raise PermissionError("Insufficient semantic project permission.")
        return resolved

    def _project_with_permission(
        self,
        *,
        identity: AgentIdentity,
        project: SemanticProject,
        context: AgentContext | None,
    ) -> SemanticProject:
        if project.owner_id == identity.owner_id:
            return project.model_copy(update={"permission": "admin"}, deep=True)
        access_level = _access_level_from_context(context)
        permission = (
            "write"
            if (
                project.visibility == "db_access"
                and self.semantic_full_access_grants_write
                and access_level == SemanticAccessLevel.FULL
            )
            else "read"
        )
        return project.model_copy(update={"permission": permission}, deep=True)

    def _request_with_database_identity(
        self,
        request: SemanticProjectResolveRequest,
    ) -> SemanticProjectResolveRequest:
        database_identity = self._database_identity(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
        )
        if database_identity is not None:
            return request.model_copy(
                update={
                    "database_label": request.database_label
                    or database_identity.database_name,
                    "database_backend": request.database_backend
                    or database_identity.backend,
                    "database_uri_fingerprint": database_identity.uri_fingerprint,
                }
            )
        if (
            self.semantic_access_mode == "superset_only"
            and not request.database_uri_fingerprint
        ):
            return request.model_copy(update={"supplied_uri": None})
        return request

    def _database_identity(
        self,
        *,
        database_id: int,
        catalog_name: str | None,
    ) -> DatabaseIdentity | None:
        if self.get_database_identity is None:
            return None
        try:
            identity = self.get_database_identity(database_id, catalog_name)
        except Exception:  # pylint: disable=broad-except
            return None
        return identity if identity.uri_fingerprint else None


def has_permission(
    actual: str | SemanticPermission,
    required: str | SemanticPermission,
) -> bool:
    """Return whether an actual permission satisfies a required permission."""

    order = {
        SemanticPermission.READ: 1,
        SemanticPermission.WRITE: 2,
        SemanticPermission.ADMIN: 3,
    }
    actual_permission = SemanticPermission(actual)
    required_permission = SemanticPermission(required)
    return order[actual_permission] >= order[required_permission]


def _access_level_from_context(context: AgentContext | None) -> SemanticAccessLevel:
    if context is None:
        return SemanticAccessLevel.PARTIAL
    return (
        SemanticAccessLevel.FULL
        if context.datasets
        else SemanticAccessLevel.PARTIAL
    )
