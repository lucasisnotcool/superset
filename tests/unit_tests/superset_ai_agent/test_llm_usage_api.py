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

"""GET /agent/admin/llm-usage: aggregates for admins, 403 for everyone else."""

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.requests import Request

from superset_ai_agent.app import create_app
from superset_ai_agent.auth import AgentIdentity
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.usage_store import InMemoryLlmUsageStore
from superset_ai_agent.schemas import ModelInfo


class _FakeClient:
    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return []


class _Provider:
    def __init__(self, *, admin: bool) -> None:
        self._admin = admin

    def get_identity(self, request: Request) -> AgentIdentity:
        return AgentIdentity(owner_id="u1")

    def is_admin(self, request: Request) -> bool:
        return self._admin


def _config() -> AgentConfig:
    return AgentConfig(
        identity_provider="static",
        superset_auth_mode="service_account",
        conversation_store="memory",
        semantic_layer_store="memory",
        wren_engine="passthrough",
        wren_core_validation_enabled=False,
    )


def _app(*, admin: bool, store: InMemoryLlmUsageStore) -> TestClient:
    app = create_app(
        config=_config(),
        ollama_client=_FakeClient(),
        identity_provider=_Provider(admin=admin),
        llm_call_store=store,
    )
    return TestClient(app)


def _seeded_store() -> InMemoryLlmUsageStore:
    store = InMemoryLlmUsageStore()
    store.record(
        provider="openai",
        model="gpt-5.2",
        duration_ms=1200,
        ok=True,
        prompt_tokens=80,
        completion_tokens=10,
    )
    store.record(provider="openai", model="gpt-5.2", duration_ms=800, ok=False)
    return store


def test_admin_gets_usage_aggregates() -> None:
    client = _app(admin=True, store=_seeded_store())

    response = client.get("/agent/admin/llm-usage")

    assert response.status_code == 200
    body = response.json()
    assert body["total_calls"] == 2
    assert body["total_failures"] == 1
    assert body["total_duration_ms"] == 2000
    assert body["by_model"][0]["key"] == "gpt-5.2"
    assert body["kinds"] == ["chat"]


def test_non_admin_is_forbidden() -> None:
    client = _app(admin=False, store=_seeded_store())

    response = client.get("/agent/admin/llm-usage")

    assert response.status_code == 403


def test_days_window_param_is_accepted() -> None:
    client = _app(admin=True, store=_seeded_store())

    # A 7-day window still includes the just-seeded rows.
    response = client.get("/agent/admin/llm-usage", params={"days": 7})

    assert response.status_code == 200
    assert response.json()["total_calls"] == 2


def test_empty_store_returns_zeroed_summary() -> None:
    client = _app(admin=True, store=InMemoryLlmUsageStore())

    response = client.get("/agent/admin/llm-usage")

    assert response.status_code == 200
    assert response.json()["total_calls"] == 0
