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
        # These tests count live schema fetches per request; the physical-catalog
        # TTL cache would serve activation from the create-time build and hide
        # the very fetch the assertions pin. Non-positive TTL disables it.
        wren_physical_catalog_cache_ttl_seconds=0,
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


# ---------------------------------------------------------------------------
# Cross-schema flat-map collision (same-named table in two member schemas).
#
# The index's flat ``tables`` map is keyed by bare table name; before the fix
# it held whichever schema's copy was built/reflected LAST, so an unqualified
# lookup (a model whose ``tableReference`` omits ``schema``) could check a real
# column against the WRONG schema's copy and read "does not exist" — the
# activation flip-flop. These tests pin the schema-qualified truth.
# ---------------------------------------------------------------------------


def _dataset(
    dataset_id: int, schema: str, table: str, columns: dict[str, str]
) -> DatasetMetadata:
    return DatasetMetadata(
        id=dataset_id,
        table_name=table,
        schema_name=schema,
        database_id=1,
        columns=[
            ColumnSummary(name=name, type=type_) for name, type_ in columns.items()
        ],
        metrics=[],
    )


def _two_schema_index():
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    # `txn` exists in BOTH schemas with different columns; the DIM copy has
    # `txn_dt_key`, the STG copy does not.
    return SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
            datasets=[
                _dataset(
                    1, "dim", "txn", {"txn_dt_key": "NUMBER", "status": "VARCHAR"}
                ),
                _dataset(2, "stg", "txn", {"raw_payload": "CLOB"}),
            ],
        )
    )


def test_same_named_table_in_two_schemas_keeps_per_schema_truth() -> None:
    index = _two_schema_index()

    # Qualified lookups are per-schema exact.
    assert index.has_column("txn", "txn_dt_key", "dim") is True
    assert index.has_column("txn", "txn_dt_key", "stg") is False
    assert index.has_column("txn", "raw_payload", "stg") is True
    # Unqualified lookups consult EVERY schema's copy (fail-open) — a real
    # column must never read "does not exist" because the other schema's
    # same-named table was indexed last.
    assert index.has_column("txn", "txn_dt_key") is True
    assert index.has_column("txn", "raw_payload") is True
    assert index.has_column("txn", "made_up") is False
    assert index.columns_for("txn") == ["raw_payload", "status", "txn_dt_key"]


def test_reflection_order_does_not_flip_unqualified_lookups() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    per_schema = {
        "dim": {"TXN_DT_KEY": "NUMBER"},
        "stg": {"RAW_PAYLOAD": "CLOB"},
    }

    def _build(order: list[str]) -> SchemaIndex:
        # Names-first live introspection: both copies pending (negative ids).
        index = SchemaIndex.from_agent_context(
            AgentContext(
                database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
                datasets=[
                    _dataset(-1, "dim", "txn", {}),
                    _dataset(-2, "stg", "txn", {}),
                ],
            )
        )
        index.attach_column_loader(
            lambda schema, table: per_schema[schema or ""], budget=10
        )
        for schema in order:
            index.ensure_columns("txn", schema)
        return index

    for order in (["dim", "stg"], ["stg", "dim"]):
        index = _build(order)
        # Whichever copy was reflected LAST, both columns resolve unqualified.
        assert index.has_column("txn", "txn_dt_key") is True, order
        assert index.has_column("txn", "raw_payload") is True, order


def test_unqualified_ensure_reflects_every_pending_schema_copy() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
            datasets=[
                _dataset(-1, "dim", "txn", {}),
                _dataset(-2, "stg", "txn", {}),
            ],
        )
    )
    calls: list[str | None] = []

    def loader(schema: str | None, table: str) -> dict[str, str | None]:
        calls.append(schema)
        return {"dim": {"TXN_DT_KEY": "NUMBER"}, "stg": {"RAW_PAYLOAD": "CLOB"}}[
            schema or ""
        ]

    index.attach_column_loader(loader, budget=10)
    # An unqualified touch must resolve BOTH schemas' pending copies — leaving
    # one pending would keep the unqualified lookup permanently unknown.
    assert index.ensure_columns("txn") is True
    assert sorted(c or "" for c in calls) == ["dim", "stg"]
    assert index.has_column("txn", "txn_dt_key") is True
    assert index.has_column("txn", "raw_payload") is True


def test_column_type_cross_schema_disagreement_reads_unknown() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

    index = SchemaIndex.from_agent_context(
        AgentContext(
            database=DatabaseSummary(id=1, name="oracle", backend="oracle"),
            datasets=[
                _dataset(1, "dim", "txn", {"status": "VARCHAR", "amt": "NUMBER"}),
                _dataset(2, "stg", "txn", {"status": "NUMBER"}),
            ],
        )
    )
    # Qualified: each schema's own type.
    assert index.column_type("txn", "status", "dim") == "VARCHAR"
    assert index.column_type("txn", "status", "stg") == "NUMBER"
    # Unqualified + disagreement: unknown (skip type checks), never the wrong
    # schema's type.
    assert index.column_type("txn", "status") is None
    # Unqualified + single home: that copy's type.
    assert index.column_type("txn", "amt") == "NUMBER"


# ---------------------------------------------------------------------------
# Deterministic activation gate: the projected manifest's tables are reflected
# eagerly and budget-EXEMPT before validation, so whether a phantom column is
# caught no longer depends on the per-turn reflect budget's walk order, the
# background warm daemon's progress, or prior cache state. Reflect budget is
# pinned to 0 here — only the gate's exempt reflection can resolve columns.
# ---------------------------------------------------------------------------


class PendingSchemaProvider:
    """Names-first live introspection: `txn` is listed WITHOUT columns."""

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=-1,
                    table_name="txn",
                    schema_name=request.schema_name,
                    database_id=request.database_id,
                    columns=[],
                    metrics=[],
                )
            ],
        )


class ReflectingSupersetClient:
    """Fake Superset client exposing only per-table column reflection."""

    def __init__(self, catalog: dict[str, dict[str, str]]) -> None:
        self.catalog = catalog
        self.reflect_calls: list[tuple[str | None, str]] = []

    def reflect_table_columns(
        self,
        *,
        database_id: int,
        schema_name: str | None,
        table_name: str,
        catalog_name: str | None = None,
    ) -> list[ColumnSummary]:
        self.reflect_calls.append((schema_name, table_name))
        return [
            ColumnSummary(name=name, type=type_)
            for name, type_ in self.catalog.get(table_name, {}).items()
        ]


def _pending_activation_client(
    tmp_path,
) -> tuple[TestClient, ReflectingSupersetClient]:
    config = AgentConfig(
        identity_provider="static",
        superset_auth_mode="service_account",
        conversation_store="memory",
        semantic_layer_store="memory",
        wren_engine="passthrough",
        wren_core_validation_enabled=False,
        # Zero per-turn budget: lazy reflection inside validation can never
        # run, so column resolution happens ONLY via the activation gate's
        # budget-exempt eager pass — the behavior under test.
        wren_introspection_column_reflect_budget=0,
        agent_storage_dir=str(tmp_path),
    )
    fake = ReflectingSupersetClient({"txn": {"TXN_DT_KEY": "NUMBER"}})
    app = create_app(
        config=config,
        model_client=_FakeModelClient(),
        text_to_sql_graph=object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=PendingSchemaProvider(),
        superset_client=fake,
        job_runner=InlineJobRunner(),
    )
    return TestClient(app), fake


def _create_txn_model(client: TestClient, pid: str, column: str) -> str:
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/mdl-files",
        json={
            "path": "models/txn.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": "txn",
                            "tableReference": {"schema": "ops", "table": "txn"},
                            "columns": [{"name": column, "type": "integer"}],
                        }
                    ]
                }
            ),
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def test_activation_catches_phantom_column_with_zero_reflect_budget(tmp_path) -> None:
    client, fake = _pending_activation_client(tmp_path)
    project = _resolve_single_schema(client, "ops")
    pid = project["id"]
    _create_txn_model(client, pid, "made_up_col")
    bulk = f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status"

    # The gate must reflect `txn` budget-exempt and catch the phantom — with
    # budget 0, only the eager gate reflection could have resolved columns.
    first = client.post(bulk, json={"status": "active"})
    assert first.status_code == 422, first.text
    detail = first.json()["detail"]
    codes = {m.get("code") for m in detail["validation"]["messages"]}
    assert "unknown_column" in codes, detail
    assert ("ops", "txn") in fake.reflect_calls

    # Deterministic: the second attempt (memoized columns, no timing effects)
    # fails identically instead of flip-flopping to a pass.
    second = client.post(bulk, json={"status": "active"})
    assert second.status_code == 422, second.text


def test_activation_of_real_column_is_stable_across_toggles(tmp_path) -> None:
    client, _fake = _pending_activation_client(tmp_path)
    project = _resolve_single_schema(client, "ops")
    pid = project["id"]
    _create_txn_model(client, pid, "txn_dt_key")
    bulk = f"/agent/semantic-layer/projects/{pid}/mdl-files/bulk-status"

    for _ in range(2):
        activate = client.post(bulk, json={"status": "active"})
        assert activate.status_code == 200, activate.text
        deactivate = client.post(bulk, json={"status": "draft"})
        assert deactivate.status_code == 200, deactivate.text


def test_single_file_activation_uses_the_same_deterministic_gate(tmp_path) -> None:
    client, _fake = _pending_activation_client(tmp_path)
    project = _resolve_single_schema(client, "ops")
    pid = project["id"]
    file_id = _create_txn_model(client, pid, "made_up_col")

    response = client.patch(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/{file_id}",
        json={"status": "active"},
    )
    assert response.status_code == 422, response.text


def test_unqualified_model_validates_against_any_schema_copy() -> None:
    from superset_ai_agent.semantic_layer.mdl_validator import validate_mdl

    index = _two_schema_index()
    content = json.dumps(
        {
            "models": [
                {
                    "name": "txn",
                    # No schema in the tableReference — the collidable case.
                    "tableReference": {"table": "txn"},
                    "columns": [{"name": "txn_dt_key", "type": "integer"}],
                }
            ]
        }
    )
    validation = validate_mdl(content, schema_index=index)
    codes = {message.code for message in validation.messages}
    assert "unknown_column" not in codes, validation
    assert validation.valid is True, validation
