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

import pytest

from superset_ai_agent.auth import AgentIdentity
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseIdentity,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.semantic_layer.access import (
    SemanticAccessService,
    SemanticPermission,
)
from superset_ai_agent.semantic_layer.projects import InMemorySemanticProjectStore
from superset_ai_agent.semantic_layer.schemas import SemanticProjectResolveRequest


def _context(scope: ConversationScope) -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=scope.database_id, name="Sales"),
        datasets=[
            DatasetMetadata(
                id=42,
                table_name="moves",
                schema_name=scope.schema_name,
                database_id=scope.database_id,
                columns=[],
                metrics=[],
            )
        ],
    )


def _service(
    store: InMemorySemanticProjectStore,
    *,
    full_access_grants_write: bool = False,
    database_identity: DatabaseIdentity | None = None,
) -> SemanticAccessService:
    return SemanticAccessService(
        project_store=store,
        load_context=_context,
        get_database_identity=(
            (lambda database_id, catalog_name: database_identity)
            if database_identity is not None
            else None
        ),
        semantic_full_access_grants_write=full_access_grants_write,
    )


def _request() -> SemanticProjectResolveRequest:
    return SemanticProjectResolveRequest(
        database_id=7,
        database_label="Sales",
        catalog_name="prod",
        schema_name="pipeline",
        supplied_uri="postgresql://owner:secret@example.com/sales",
    )


def test_owner_receives_admin_permission() -> None:
    store = InMemorySemanticProjectStore()
    service = _service(store)
    owner = AgentIdentity(owner_id="owner")

    project = service.resolve_project(identity=owner, request=_request())

    assert project.permission == "admin"
    assert service.require_project_permission(
        identity=owner,
        project_id=project.id,
        permission=SemanticPermission.ADMIN,
    ).permission == "admin"


def test_db_derived_visibility_grants_read_by_default() -> None:
    store = InMemorySemanticProjectStore()
    owner_project = _service(store).resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request(),
    )
    analyst = AgentIdentity(owner_id="analyst")
    service = _service(store)

    project = service.require_project_permission(
        identity=analyst,
        project_id=owner_project.id,
        permission=SemanticPermission.READ,
    )

    assert project.permission == "read"
    with pytest.raises(PermissionError):
        service.require_project_permission(
            identity=analyst,
            project_id=owner_project.id,
            permission=SemanticPermission.WRITE,
        )


def test_db_derived_write_requires_explicit_flag_and_full_context() -> None:
    store = InMemorySemanticProjectStore()
    owner_project = _service(store).resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request(),
    )
    analyst = AgentIdentity(owner_id="analyst")
    service = _service(store, full_access_grants_write=True)

    project = service.require_project_permission(
        identity=analyst,
        project_id=owner_project.id,
        permission=SemanticPermission.WRITE,
    )

    assert project.permission == "write"


def test_list_projects_returns_permission_from_access_service() -> None:
    store = InMemorySemanticProjectStore()
    _service(store).resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request(),
    )
    analyst = AgentIdentity(owner_id="analyst")

    projects = _service(store).list_projects(
        identity=analyst,
        scope=ConversationScope(
            database_id=7,
            catalog_name="prod",
            schema_name="pipeline",
        ),
    )

    assert len(projects) == 1
    assert projects[0].permission == "read"


def test_project_resolution_prefers_superset_database_fingerprint() -> None:
    store = InMemorySemanticProjectStore()
    identity = DatabaseIdentity(
        database_id=7,
        database_name="Sales",
        backend="postgresql",
        uri_fingerprint="physical-db",
        catalog_name="prod",
        schema_names=["pipeline"],
    )
    service = _service(store, database_identity=identity)

    owner_project = service.resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request(),
    )
    analyst_project = service.resolve_project(
        identity=AgentIdentity(owner_id="analyst"),
        request=SemanticProjectResolveRequest(
            database_id=99,
            database_label="Different Superset DB",
            catalog_name="prod",
            schema_name="pipeline",
            supplied_uri="postgresql://different@example.com/other",
        ),
    )

    assert analyst_project.id == owner_project.id
    assert owner_project.database_uri_fingerprint == "physical-db"
    assert analyst_project.permission == "read"


def test_list_projects_uses_superset_database_fingerprint() -> None:
    store = InMemorySemanticProjectStore()
    service = _service(
        store,
        database_identity=DatabaseIdentity(
            database_id=99,
            database_name="Sales",
            backend="postgresql",
            uri_fingerprint="physical-db",
            catalog_name="prod",
            schema_names=["pipeline"],
        ),
    )
    service.resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request().model_copy(
            update={
                "database_uri_fingerprint": "physical-db",
                "database_id": 7,
            }
        ),
    )

    projects = service.list_projects(
        identity=AgentIdentity(owner_id="analyst"),
        scope=ConversationScope(
            database_id=99,
            catalog_name="prod",
            schema_name="pipeline",
        ),
    )

    assert [project.database_uri_fingerprint for project in projects] == [
        "physical-db"
    ]
