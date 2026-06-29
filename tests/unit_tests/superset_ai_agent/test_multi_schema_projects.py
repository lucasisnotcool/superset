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

"""Multi-schema semantic project resolution, reconciliation, and persistence."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
from superset_ai_agent.semantic_layer.projects import (
    InMemorySemanticProjectStore,
    SqlAlchemySemanticProjectStore,
)
from superset_ai_agent.semantic_layer.schemas import SemanticProjectResolveRequest


def _sqlalchemy_store() -> SqlAlchemySemanticProjectStore:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    return SqlAlchemySemanticProjectStore(create_session_factory(engine))


def _request(**overrides) -> SemanticProjectResolveRequest:
    base = {
        "database_id": 7,
        "database_label": "Sales",
        "catalog_name": "prod",
        "schema_name": "sales",
        "supplied_uri": "postgresql://user:secret@example.com/sales",
    }
    base.update(overrides)
    return SemanticProjectResolveRequest(**base)


# --- both stores, parameterized -------------------------------------------------

ALL_STORES = ["memory", "sqlalchemy"]


def _store(kind: str):
    return InMemorySemanticProjectStore() if kind == "memory" else _sqlalchemy_store()


@pytest.mark.parametrize("kind", ALL_STORES)
def test_resolve_creates_project_with_full_schema_set(kind: str) -> None:
    store = _store(kind)
    project = store.resolve(
        _request(schema_name="sales", schema_names=["crm", "sales"]),
        owner_id="owner",
    )
    assert project.schema_name == "sales"  # primary
    assert project.schema_names == ["sales", "crm"]


@pytest.mark.parametrize("kind", ALL_STORES)
def test_single_schema_request_yields_one_element_set(kind: str) -> None:
    store = _store(kind)
    project = store.resolve(_request(), owner_id="owner")
    assert project.schema_names == ["sales"]


@pytest.mark.parametrize("kind", ALL_STORES)
def test_resolve_adds_new_schema_to_existing_project(kind: str) -> None:
    store = _store(kind)
    first = store.resolve(_request(), owner_id="owner")
    grown = store.resolve(
        _request(schema_names=["crm"]),
        owner_id="owner",
    )
    assert grown.id == first.id  # same project, not a new one
    assert grown.schema_names == ["sales", "crm"]
    # persistence: a fresh read sees the grown set
    assert store.get(first.id, owner_id="owner").schema_names == ["sales", "crm"]


@pytest.mark.parametrize("kind", ALL_STORES)
def test_resolve_by_non_primary_member_finds_same_project(kind: str) -> None:
    store = _store(kind)
    project = store.resolve(
        _request(schema_name="sales", schema_names=["crm"]),
        owner_id="owner",
    )
    # Reopening the editor on the secondary schema must resolve the same project.
    again = store.resolve(_request(schema_name="crm"), owner_id="owner")
    assert again.id == project.id
    assert again.schema_name == "sales"  # primary preserved


@pytest.mark.parametrize("kind", ALL_STORES)
def test_list_returns_correct_schema_set_per_project(kind: str) -> None:
    # Listing many projects must batch the membership lookup (one query, not
    # 1+N) AND still return each project's own ordered schema set — primary
    # first — plus the single-schema fallback for a project with no extra rows.
    store = _store(kind)
    multi = store.resolve(
        _request(schema_name="sales", schema_names=["crm", "sales"]),
        owner_id="owner",
    )
    single = store.resolve(
        _request(schema_name="hr", schema_names=["hr"]),
        owner_id="owner",
    )

    listed = {p.id: p for p in store.list(owner_id="owner")}
    assert listed[multi.id].schema_names == ["sales", "crm"]
    assert listed[single.id].schema_names == ["hr"]
    # The batched list agrees with the per-project read for each.
    assert (
        listed[multi.id].schema_names
        == store.get(multi.id, owner_id="owner").schema_names
    )


@pytest.mark.parametrize("kind", ALL_STORES)
def test_list_finds_project_by_member_schema(kind: str) -> None:
    store = _store(kind)
    project = store.resolve(
        _request(schema_names=["crm"]),
        owner_id="owner",
    )
    by_primary = store.list(owner_id="owner", schema_name="sales")
    by_member = store.list(owner_id="owner", schema_name="crm")
    assert [p.id for p in by_primary] == [project.id]
    assert [p.id for p in by_member] == [project.id]
    assert store.list(owner_id="owner", schema_name="absent") == []
