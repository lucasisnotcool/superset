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

"""Cross-schema onboarding: an explicit ``include`` selection that spans more than
one of a project's schemas must onboard **every** chosen dataset, not just the
ones in the primary schema.

Regression for the silent-narrowing bug: the onboarding fetch passed a single
``schema_name`` alongside the cross-schema ``dataset_ids``; providers intersect
schema AND ids, so datasets in the project's secondary schemas were dropped and
only the primary schema got onboarded. The fetch now passes ``schema_name=None``
(ids are authoritative) and an explicit project-schema-set boundary guard.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from fastapi.testclient import TestClient

from superset_ai_agent.app import create_app
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import AgentQueryRequest
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.jobs import InlineJobRunner
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore


class _FakeModelClient:
    def generate(self, *_args, **_kwargs) -> ModelResult:
        return ModelResult(message=ChatMessage(role="assistant", content="{}"))


class CrossSchemaProvider:
    """Models the (fixed) provider contract: an explicit id selection returns the
    matching datasets across ALL schemas — ``schema_name`` is a fallback scope for
    the no-ids case, never a narrowing filter on an id selection."""

    #: dataset_id -> (table, schema, columns)
    DATASETS = {
        101: ("moves", "pipeline", ["stage"]),
        202: ("invoices", "archive", ["amount"]),
        303: ("secrets", "vault", ["token"]),  # in DB, but NOT in the project
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, tuple[int, ...]]] = []

    def get_full_schema(self, request: AgentQueryRequest) -> AgentContext:
        ids = tuple(request.dataset_ids or [])
        self.calls.append((request.schema_name, ids))
        if ids:
            rows = [
                (dataset_id, *self.DATASETS[dataset_id])
                for dataset_id in self.DATASETS
                if dataset_id in set(ids)
            ]
        else:
            rows = [
                (dataset_id, table, schema, columns)
                for dataset_id, (table, schema, columns) in self.DATASETS.items()
                if schema == request.schema_name
            ]
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="db"),
            datasets=[
                DatasetMetadata(
                    id=dataset_id,
                    table_name=table,
                    schema_name=schema,
                    database_id=request.database_id,
                    columns=[ColumnSummary(name=column) for column in columns],
                    metrics=[],
                )
                for dataset_id, table, schema, columns in rows
            ],
        )

    # Onboarding uses get_full_schema when present; alias keeps get_context valid.
    get_context = get_full_schema


def _client(tmp_path) -> tuple[TestClient, CrossSchemaProvider]:
    provider = CrossSchemaProvider()
    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
            agent_storage_dir=str(tmp_path),
        ),
        model_client=_FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=provider,
        job_runner=InlineJobRunner(),
    )
    return TestClient(app), provider


def _resolve(client: TestClient) -> dict:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={
            "database_id": 1,
            "database_label": "Sales",
            "schema_name": "pipeline",
            "schema_names": ["pipeline", "archive"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _onboard(client: TestClient, project_id: str, dataset_ids: list[int]) -> dict:
    response = client.post(
        f"/agent/semantic-layer/projects/{project_id}/onboard",
        json={
            "mode": "include",
            "dataset_ids": dataset_ids,
            "exclude_dataset_ids": [],
            "search": None,
        },
    )
    assert response.status_code in (200, 202), response.text
    return response.json()


def _onboarded_schemas(client: TestClient, project_id: str) -> set[str]:
    response = client.get(f"/agent/semantic-layer/projects/{project_id}/mdl-files")
    assert response.status_code == 200, response.text
    schemas: set[str] = set()
    for mdl_file in response.json():
        for model in json.loads(mdl_file["content"]).get("models", []):
            ref = model.get("tableReference") or {}
            if ref.get("schema"):
                schemas.add(ref["schema"])
    return schemas


def test_include_onboarding_spans_every_selected_schema(tmp_path) -> None:
    client, provider = _client(tmp_path)
    project = _resolve(client)

    _onboard(client, project["id"], [101, 202])  # pipeline + archive

    # Both schemas are onboarded — not just the primary (`pipeline`).
    assert _onboarded_schemas(client, project["id"]) == {"pipeline", "archive"}
    # The fetch was id-driven with NO single-schema narrowing (the fix).
    assert (None, (101, 202)) in provider.calls


def test_include_onboarding_drops_ids_outside_the_project_schema_set(tmp_path) -> None:
    client, _provider = _client(tmp_path)
    project = _resolve(client)

    # 303 (schema `vault`) is in the database but NOT in the project's schema set;
    # the boundary guard must drop it even though its id was supplied.
    _onboard(client, project["id"], [101, 202, 303])

    schemas = _onboarded_schemas(client, project["id"])
    assert schemas == {"pipeline", "archive"}
    assert "vault" not in schemas
