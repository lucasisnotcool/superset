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
"""Dataset-fetch efficiency (F2): projected detail + no wasted base scan."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.superset_metadata import SupersetMetadataContextProvider
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.integrations.superset.rest import SupersetRestClient
from superset_ai_agent.schemas import AgentQueryRequest


def _client() -> SupersetRestClient:
    return SupersetRestClient(AgentConfig())


def test_get_dataset_raw_projects_to_agent_fields() -> None:
    """F2b: the per-dataset detail is column-projected, not the full payload."""
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"result": {"id": 32, "table_name": "t"}}

    client = _client()
    client.request = fake_request  # type: ignore[method-assign]

    client.get_dataset_raw(32)

    assert len(calls) == 1
    _, path, kwargs = calls[0]
    assert path == "/api/v1/dataset/32"
    query = kwargs["params"]["q"]
    assert query.startswith("(columns:!(")
    # The fields the agent actually normalizes are present...
    for needed in ("table_name", "columns.column_name", "metrics.metric_name"):
        assert needed in query
    # ...and the heavy unused fields are NOT requested.
    assert "columns.advanced_data_type" not in query
    assert "owners" not in query


def test_get_agent_context_skips_dataset_scan_when_excluded() -> None:
    """F2a: include_datasets=False fetches only the database, no dataset N+1."""
    paths: list[str] = []

    def fake_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        paths.append(path)
        return {"result": {"id": 1, "database_name": "db", "backend": "postgresql"}}

    client = _client()
    client.request = fake_request  # type: ignore[method-assign]

    context = client.get_agent_context(database_id=1, include_datasets=False)

    assert context.datasets == []
    assert any("/api/v1/database/1" in path for path in paths)
    # No dataset list or detail calls at all.
    assert all("/api/v1/dataset" not in path for path in paths)


def _provider_with_mock_client() -> tuple[SupersetMetadataContextProvider, MagicMock]:
    client = MagicMock()
    client.get_agent_context.return_value = AgentContext(
        database=DatabaseSummary(id=1, name="db", backend="postgresql"),
        datasets=[],
    )
    client.list_datasets.return_value = [
        DatasetMetadata(
            id=10,
            table_name="orders",
            schema_name="public",
            database_id=1,
            description=None,
            columns=[],
            metrics=[],
        )
    ]
    provider = SupersetMetadataContextProvider(client, config=AgentConfig())
    return provider, client


def test_get_full_schema_does_not_double_fetch_datasets() -> None:
    """F2a wiring: the base context is fetched WITHOUT datasets (the candidate
    scan is the single dataset fetch)."""
    provider, client = _provider_with_mock_client()
    request = AgentQueryRequest(
        question="q",
        database_id=1,
        schema_name="public",
    )

    context = provider.get_full_schema(request)

    # Base context fetched as a database-only shell.
    client.get_agent_context.assert_called_once()
    assert client.get_agent_context.call_args.kwargs.get("include_datasets") is False
    # Exactly one dataset scan (the candidates), not two.
    client.list_datasets.assert_called_once()
    assert [dataset.table_name for dataset in context.datasets] == ["orders"]


def test_get_context_does_not_double_fetch_datasets() -> None:
    """Same single-scan guarantee on the question-ranked ``get_context`` path."""
    provider, client = _provider_with_mock_client()
    request = AgentQueryRequest(
        question="top customers",
        database_id=1,
        schema_name="public",
    )

    provider.get_context(request)

    client.get_agent_context.assert_called_once()
    assert client.get_agent_context.call_args.kwargs.get("include_datasets") is False
    client.list_datasets.assert_called_once()
