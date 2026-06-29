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

"""The validation/Copilot schema index must span ALL of a project's schemas.

``_schema_index_for_project`` previously fetched only the project's primary
schema, so a model that physically references a *secondary* member schema was
wrongly rejected (R1 ``schema_not_in_project``) and the Copilot was blind to it.
The index now unions every member schema (mirroring onboarding). These tests
pin that down at the API boundary, plus the negative control that a truly
out-of-set schema is still rejected.
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


class PerSchemaContextProvider:
    """Returns distinct datasets per requested schema and records the schemas
    asked for, so the validation index's scope coverage is observable."""

    #: schema -> [(dataset_id, table, [columns])]; ids are globally unique, as
    #: real Superset dataset ids are (the union dedups by id).
    TABLES = {
        "pipeline": [(101, "moves", ["stage"])],
        "archive": [(202, "invoices", ["amount"])],
    }

    def __init__(self) -> None:
        self.schemas_requested: list[str | None] = []

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        self.schemas_requested.append(request.schema_name)
        rows = self.TABLES.get(request.schema_name or "", [])
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=dataset_id,
                    table_name=table,
                    schema_name=request.schema_name,
                    database_id=request.database_id,
                    columns=[ColumnSummary(name=column) for column in columns],
                    metrics=[],
                )
                for dataset_id, table, columns in rows
            ],
        )


def _config(tmp_path) -> AgentConfig:
    return AgentConfig(
        identity_provider="static",
        superset_auth_mode="service_account",
        conversation_store="memory",
        semantic_layer_store="memory",
        wren_engine="passthrough",
        wren_core_validation_enabled=False,
        agent_storage_dir=str(tmp_path),
    )


def _client(tmp_path) -> tuple[TestClient, PerSchemaContextProvider]:
    provider = PerSchemaContextProvider()
    app = create_app(
        config=_config(tmp_path),
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


def _resolve_multi_schema(client: TestClient) -> dict:
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


def _create_model(
    client: TestClient, project_id: str, *, name: str, schema: str, table: str
) -> dict:
    """Create an MDL file and return its create-time ``validation`` block.

    Create-time validation is where R1 (the physical schema index) runs; the
    standalone ``/validate`` endpoint is structural-only.
    """

    response = client.post(
        f"/agent/semantic-layer/projects/{project_id}/mdl-files",
        json={
            "path": f"models/{name}.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": name,
                            "tableReference": {"schema": schema, "table": table},
                            "columns": [{"name": "amount", "type": "varchar"}],
                        }
                    ]
                }
            ),
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["validation"]


def test_model_in_a_secondary_member_schema_validates(tmp_path) -> None:
    client, provider = _client(tmp_path)
    project = _resolve_multi_schema(client)
    # `invoices` lives in the project's SECONDARY schema (`archive`).
    validation = _create_model(
        client, project["id"], name="invoices", schema="archive", table="invoices"
    )

    assert validation["valid"] is True, validation
    # The index union actually fetched the secondary schema (the fix); before it,
    # only the primary schema was indexed and `archive` was wrongly rejected.
    assert "archive" in provider.schemas_requested
    assert "pipeline" in provider.schemas_requested


def _resolve_single_schema(client: TestClient, schema: str) -> dict:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={
            "database_id": 1,
            "database_label": "Sales",
            "schema_name": schema,
            "schema_names": [schema],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_bulk_activate_fetches_live_schema_once_and_deactivate_zero(tmp_path) -> None:
    # Perceived-latency fix: on top of the unavoidable per-request auth fetch,
    # activation must resolve the live schema exactly ONCE (manifest enforcement
    # hands its index to the per-file validation — previously it fetched twice),
    # and deactivation must add ZERO schema fetches (it neither enforces nor
    # re-validates — previously it fetched one and threw it away).
    client, provider = _client(tmp_path)
    project = _resolve_single_schema(client, "archive")
    pid = project["id"]
    bulk = f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status"
    # A valid draft model whose column matches `archive.invoices`.
    _create_model(client, pid, name="invoices", schema="archive", table="invoices")

    # Auth-only baseline: a no-op bulk-status (already draft) authorizes but does
    # no schema work, isolating the per-request auth fetch from activation's.
    provider.schemas_requested.clear()
    noop = client.post(bulk, json={"status": "draft"})
    assert noop.status_code == 200, noop.text
    assert noop.json()["changed_count"] == 0
    baseline = len(provider.schemas_requested)

    # Activate adds exactly ONE schema fetch (enforce + per-file validation share).
    provider.schemas_requested.clear()
    activate = client.post(bulk, json={"status": "active"})
    assert activate.status_code == 200, activate.text
    assert activate.json()["changed_count"] == 1
    assert len(provider.schemas_requested) == baseline + 1

    # Deactivate adds ZERO schema fetches beyond the auth baseline.
    provider.schemas_requested.clear()
    deactivate = client.post(bulk, json={"status": "draft"})
    assert deactivate.status_code == 200, deactivate.text
    assert deactivate.json()["changed_count"] == 1
    assert len(provider.schemas_requested) == baseline


def test_model_in_an_out_of_set_schema_is_still_rejected(tmp_path) -> None:
    client, _provider = _client(tmp_path)
    project = _resolve_multi_schema(client)
    # `secret` is not part of the project's schema set → R1 must still reject.
    validation = _create_model(
        client, project["id"], name="leak", schema="secret", table="invoices"
    )

    assert validation["valid"] is False
    codes = {message.get("code") for message in validation["messages"]}
    assert "schema_not_in_project" in codes, validation
