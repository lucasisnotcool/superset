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

"""Access control proves *every* schema in a multi-schema project's set (R1)."""

from __future__ import annotations

from superset_ai_agent.auth import AgentIdentity
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.semantic_layer.access import SemanticAccessService
from superset_ai_agent.semantic_layer.projects import InMemorySemanticProjectStore
from superset_ai_agent.semantic_layer.schemas import SemanticProjectResolveRequest


class _RecordingLoader:
    """Records the schema of every scope it is asked to authorize."""

    def __init__(self) -> None:
        self.proven_schemas: list[str | None] = []

    def __call__(self, scope: ConversationScope) -> AgentContext:
        self.proven_schemas.append(scope.schema_name)
        datasets = []
        if scope.schema_name:
            datasets = [
                DatasetMetadata(
                    id=hash(scope.schema_name) % 100000,
                    table_name=f"{scope.schema_name}_orders",
                    schema_name=scope.schema_name,
                    database_id=scope.database_id,
                    columns=[],
                    metrics=[],
                )
            ]
        return AgentContext(
            database=DatabaseSummary(id=scope.database_id, name="Sales"),
            datasets=datasets,
        )


def _service(
    loader: _RecordingLoader,
) -> tuple[SemanticAccessService, InMemorySemanticProjectStore]:
    store = InMemorySemanticProjectStore()
    return (
        SemanticAccessService(project_store=store, load_context=loader),
        store,
    )


def _request(**overrides) -> SemanticProjectResolveRequest:
    base = {
        "database_id": 7,
        "database_label": "Sales",
        "catalog_name": "prod",
        "schema_name": "sales",
        "supplied_uri": "postgresql://owner:secret@example.com/sales",
    }
    base.update(overrides)
    return SemanticProjectResolveRequest(**base)


def test_resolve_proves_every_requested_schema() -> None:
    loader = _RecordingLoader()
    service, _ = _service(loader)

    project = service.resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request(schema_names=["crm", "sales"]),
    )

    assert project.schema_names == ["sales", "crm"]
    # Every member schema must have been proven (resolve proof + project proof).
    assert set(loader.proven_schemas) == {"sales", "crm"}


def test_reopening_project_reproves_full_set() -> None:
    loader = _RecordingLoader()
    service, _ = _service(loader)
    project = service.resolve_project(
        identity=AgentIdentity(owner_id="owner"),
        request=_request(schema_names=["crm"]),
    )

    loader.proven_schemas.clear()
    service.require_project_permission(
        identity=AgentIdentity(owner_id="owner"),
        project_id=project.id,
        permission=service_permission_read(),
    )
    # Both schemas re-proven on reopen — not just the primary.
    assert set(loader.proven_schemas) == {"sales", "crm"}


def test_require_schema_set_permission_returns_union_context() -> None:
    loader = _RecordingLoader()
    service, _ = _service(loader)

    context = service.require_schema_set_permission(
        identity=AgentIdentity(owner_id="owner"),
        database_id=7,
        catalog_name="prod",
        schema_names=["sales", "crm"],
    )

    schemas = {dataset.schema_name for dataset in context.datasets}
    assert schemas == {"sales", "crm"}


def service_permission_read():
    from superset_ai_agent.semantic_layer.access import SemanticPermission

    return SemanticPermission.READ
