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

"""Prompt registry (P2.1): store lifecycle, admin API, runtime resolution."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from superset_ai_agent.app import create_app
from superset_ai_agent.auth import AgentIdentity
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.models import Base
from superset_ai_agent.prompts import registry
from superset_ai_agent.prompts.store import (
    InMemoryPromptStore,
    PromptVersionNotFoundError,
    SqlAlchemyPromptStore,
)
from superset_ai_agent.schemas import ModelInfo


def _sqlalchemy_store() -> SqlAlchemyPromptStore:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return SqlAlchemyPromptStore(sessionmaker(bind=engine))


@pytest.fixture(params=["memory", "sqlalchemy"])
def store(request):
    if request.param == "memory":
        return InMemoryPromptStore()
    return _sqlalchemy_store()


@pytest.fixture(autouse=True)
def _reset_resolver():
    yield
    registry.set_prompt_resolver(None)


# --- store -------------------------------------------------------------------


def test_versions_are_append_only_and_numbered_per_name(store) -> None:
    first = store.create_version("text_to_sql", "v1 content", created_by="kim")
    second = store.create_version("text_to_sql", "v2 content")
    other = store.create_version("conversation", "other content")

    assert (first.version, second.version, other.version) == (1, 2, 1)
    versions = store.list_versions("text_to_sql")
    assert [v.version for v in versions] == [2, 1]
    assert store.list_names() == ["conversation", "text_to_sql"]


def test_label_promote_rollback_and_clear(store) -> None:
    v1 = store.create_version("text_to_sql", "v1")
    v2 = store.create_version("text_to_sql", "v2")

    assert store.get_labeled("text_to_sql", "production") is None
    store.set_label("text_to_sql", "production", v2.id)
    assert store.get_labeled("text_to_sql", "production").content == "v2"

    # Rollback = move the label back.
    store.set_label("text_to_sql", "production", v1.id)
    assert store.get_labeled("text_to_sql", "production").content == "v1"

    assert store.clear_label("text_to_sql", "production") is True
    assert store.get_labeled("text_to_sql", "production") is None
    assert store.clear_label("text_to_sql", "production") is False


def test_label_rejects_foreign_version(store) -> None:
    other = store.create_version("conversation", "x")
    with pytest.raises(PromptVersionNotFoundError):
        store.set_label("text_to_sql", "production", other.id)


# --- runtime resolution ------------------------------------------------------


def test_get_prompt_prefers_override_and_falls_back_to_file() -> None:
    file_default = registry.get_file_prompt("text_to_sql")

    registry.set_prompt_resolver(lambda name: None)
    assert registry.get_prompt("text_to_sql") == file_default

    registry.set_prompt_resolver(
        lambda name: "OVERRIDE" if name == "text_to_sql" else None
    )
    assert registry.get_prompt("text_to_sql") == "OVERRIDE"
    # Other names keep their file defaults.
    assert registry.get_prompt("conversation") == registry.get_file_prompt(
        "conversation"
    )


def test_resolver_errors_degrade_to_file_default() -> None:
    def broken(name: str) -> str:
        raise RuntimeError("db down")

    registry.set_prompt_resolver(broken)
    assert registry.get_prompt("text_to_sql") == registry.get_file_prompt(
        "text_to_sql"
    )


def test_file_prompt_names_include_known_prompts() -> None:
    names = registry.list_file_prompt_names()
    assert "text_to_sql" in names
    assert "mdl_copilot" in names


# --- admin API ---------------------------------------------------------------


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


def _client(*, admin: bool, store: InMemoryPromptStore | None = None) -> TestClient:
    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
        ),
        ollama_client=_FakeClient(),
        identity_provider=_Provider(admin=admin),
        prompt_store=store or InMemoryPromptStore(),
    )
    return TestClient(app)


def test_non_admin_is_forbidden() -> None:
    client = _client(admin=False)
    assert client.get("/agent/admin/prompts").status_code == 403
    assert (
        client.post(
            "/agent/admin/prompts/text_to_sql/versions", json={"content": "x"}
        ).status_code
        == 403
    )


def test_list_includes_file_defaults() -> None:
    client = _client(admin=True)
    body = client.get("/agent/admin/prompts").json()
    names = {p["name"] for p in body}
    assert "text_to_sql" in names
    row = next(p for p in body if p["name"] == "text_to_sql")
    assert row["has_file_default"] is True
    assert row["versions_count"] == 0
    assert row["production_version"] is None


def test_candidate_promote_resolve_and_reset_flow() -> None:
    store = InMemoryPromptStore()
    client = _client(admin=True, store=store)

    created = client.post(
        "/agent/admin/prompts/text_to_sql/versions",
        json={"content": "You are a careful SQL analyst.", "comment": "tighter"},
    )
    assert created.status_code == 200, created.text
    version = created.json()
    assert version["version"] == 1

    # A candidate alone does NOT change what the runtime serves.
    assert registry.get_prompt("text_to_sql") == registry.get_file_prompt(
        "text_to_sql"
    )

    promoted = client.post(
        "/agent/admin/prompts/text_to_sql/promote",
        json={"version_id": version["id"]},
    )
    assert promoted.status_code == 200, promoted.text
    assert registry.get_prompt("text_to_sql") == "You are a careful SQL analyst."

    detail = client.get("/agent/admin/prompts/text_to_sql").json()
    assert detail["production_version_id"] == version["id"]
    assert detail["file_content"]

    reset = client.delete("/agent/admin/prompts/text_to_sql/promotion")
    assert reset.json() == {"reset": True}
    assert registry.get_prompt("text_to_sql") == registry.get_file_prompt(
        "text_to_sql"
    )


def test_unknown_prompt_name_is_404() -> None:
    client = _client(admin=True)
    assert client.get("/agent/admin/prompts/nope").status_code == 404
    assert (
        client.post(
            "/agent/admin/prompts/nope/versions", json={"content": "x"}
        ).status_code
        == 404
    )


def test_promote_unknown_version_is_404() -> None:
    client = _client(admin=True)
    response = client.post(
        "/agent/admin/prompts/text_to_sql/promote",
        json={"version_id": "missing"},
    )
    assert response.status_code == 404
