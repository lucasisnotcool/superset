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

"""Project-selection logic for the AI SQL agent's semantic grounding.

Covers the resolution order added for F1/F2: an explicit ``project_id`` pin is
honored only when it appears in the access- and schema-filtered candidate set;
otherwise the resolver falls back to the most-recently-updated match and warns.
"""

from __future__ import annotations

import pytest

import superset_ai_agent.semantic_layer.wren_runtime as wren_runtime
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.semantic_layer.projects import SemanticProjectNotFoundError
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.wren_runtime import (
    materialize_request_semantic_project,
    resolve_effective_schema,
)


def _project(
    project_id: str,
    name: str,
    *,
    schema_name: str = "sales",
    schema_names: list[str] | None = None,
    default_database_id: int | None = 1,
) -> SemanticProject:
    return SemanticProject(
        id=project_id,
        name=name,
        owner_id="owner",
        database_uri_fingerprint="fp",
        schema_name=schema_name,
        schema_names=schema_names or [],
        default_database_id=default_database_id,
    )


class _FakeProjectStore:
    """Returns a fixed, already access/schema-filtered candidate list.

    Mirrors ``SemanticProjectStore.list`` ordering (most-recent first); a project
    absent from this list stands in for "unauthorized / wrong schema / archived".
    ``get`` is the owner-filtered by-id lookup the schema inference uses.
    """

    def __init__(self, projects: list[SemanticProject]) -> None:
        self._projects = projects

    def list(self, **_kwargs) -> list[SemanticProject]:
        return list(self._projects)

    def get(self, project_id: str, *, owner_id: str = "owner") -> SemanticProject:
        for project in self._projects:
            if project.id == project_id:
                return project
        raise SemanticProjectNotFoundError(project_id)


class _FakeMdlStore:
    def list(self, _project_id: str, *, owner_id: str = "owner") -> list:
        return []


@pytest.fixture(autouse=True)
def _patch_materializer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolate selection logic from disk materialization.
    def _fake(*, project, mdl_files, base_path):  # noqa: ANN001, ARG001
        return WrenMaterializationResult(
            project_id=project.id,
            path=f"/wren/{project.id}",
            file_count=0,
            checksum="checksum",
        )

    monkeypatch.setattr(wren_runtime, "materialize_wren_project", _fake)


def _resolve(project_id, projects):
    return materialize_request_semantic_project(
        config=AgentConfig(),
        semantic_project_store=_FakeProjectStore(projects),
        mdl_file_store=_FakeMdlStore(),
        owner_id="owner",
        database_id=1,
        catalog_name=None,
        schema_name="sales",
        project_id=project_id,
    )


def test_no_pin_uses_most_recent_match() -> None:
    # No explicit pin → the heuristic first (most-recently-updated) project wins.
    result = _resolve(None, [_project("p1", "Recent"), _project("p2", "Older")])
    assert result is not None
    project, _materialization, warnings = result
    assert project.id == "p1"
    assert warnings == []


def test_explicit_pin_is_honored_when_in_candidate_set() -> None:
    # An explicit pin for a non-default-but-available project overrides the [0] pick.
    result = _resolve("p2", [_project("p1", "Recent"), _project("p2", "Pinned")])
    assert result is not None
    project, _materialization, warnings = result
    assert project.id == "p2"
    assert warnings == []


def test_unavailable_pin_falls_back_with_warning() -> None:
    # A pin NOT in the access/schema-filtered set (unauthorized, wrong schema, or
    # archived) must never be used — fall back to the heuristic + warn (R1/R2/R3).
    result = _resolve("ghost", [_project("p1", "Recent"), _project("p2", "Older")])
    assert result is not None
    project, _materialization, warnings = result
    assert project.id == "p1"
    assert len(warnings) == 1
    assert "unavailable" in warnings[0].lower()


def test_no_projects_returns_none() -> None:
    assert _resolve("anything", []) is None


def test_missing_schema_infers_from_pinned_project() -> None:
    # No tab schema but a pinned project: the materializer infers the project's
    # schema and grounds on it instead of returning None (the AI SQL dropdown bug).
    result = materialize_request_semantic_project(
        config=AgentConfig(),
        semantic_project_store=_FakeProjectStore([_project("p1", "Recent")]),
        mdl_file_store=_FakeMdlStore(),
        owner_id="owner",
        database_id=1,
        catalog_name=None,
        schema_name=None,
        project_id="p1",
    )
    assert result is not None
    project, _materialization, _warnings = result
    assert project.id == "p1"


def test_missing_schema_and_no_project_returns_none() -> None:
    result = materialize_request_semantic_project(
        config=AgentConfig(),
        semantic_project_store=_FakeProjectStore([_project("p1", "Recent")]),
        mdl_file_store=_FakeMdlStore(),
        owner_id="owner",
        database_id=1,
        catalog_name=None,
        schema_name=None,
        project_id=None,
    )
    assert result is None


# --- resolve_effective_schema (project-wins schema inference) ---------------- #
def test_resolve_infers_schema_from_project_when_absent() -> None:
    store = _FakeProjectStore([_project("p1", "Sales", schema_name="sales")])
    schema_name, schema_names = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="owner",
        database_id=1,
        schema_name=None,
        project_id="p1",
    )
    assert schema_name == "sales"
    assert schema_names == ["sales"]


def test_resolve_project_wins_over_tab_schema() -> None:
    # "Project always wins": a pinned project overrides an explicit tab schema.
    store = _FakeProjectStore([_project("p1", "Sales", schema_name="sales")])
    schema_name, _ = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="owner",
        database_id=1,
        schema_name="other_tab_schema",
        project_id="p1",
    )
    assert schema_name == "sales"


def test_resolve_returns_full_set_for_multi_schema_project() -> None:
    store = _FakeProjectStore(
        [_project("p1", "Multi", schema_name="a", schema_names=["a", "b"])]
    )
    schema_name, schema_names = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="owner",
        database_id=1,
        schema_name=None,
        project_id="p1",
    )
    assert schema_name == "a"
    assert schema_names == ["a", "b"]


def test_resolve_falls_back_when_project_unresolvable() -> None:
    store = _FakeProjectStore([_project("p1", "Sales")])
    schema_name, schema_names = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="owner",
        database_id=1,
        schema_name="tab_schema",
        project_id="ghost",
    )
    assert schema_name == "tab_schema"
    assert schema_names == ["tab_schema"]


def test_resolve_ignores_project_on_other_database() -> None:
    # A project pinned for a different database must not infer its schema onto
    # this request (avoid grounding on the wrong DB).
    store = _FakeProjectStore(
        [_project("p1", "Sales", schema_name="sales", default_database_id=999)]
    )
    schema_name, _ = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="owner",
        database_id=1,
        schema_name=None,
        project_id="p1",
    )
    assert schema_name is None


def test_resolve_no_project_returns_passed_schema() -> None:
    schema_name, schema_names = resolve_effective_schema(
        semantic_project_store=None,
        owner_id="owner",
        database_id=1,
        schema_name="sales",
        project_id=None,
    )
    assert schema_name == "sales"
    assert schema_names == ["sales"]
