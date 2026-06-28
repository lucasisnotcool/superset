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

"""First-class project identity: slug derivation, uniqueness, create/rename."""

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
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    SemanticProjectResolveRequest,
    slugify_project_name,
)

ALL_STORES = ["memory", "sqlalchemy"]


def _store(kind: str):
    if kind == "memory":
        return InMemorySemanticProjectStore()
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
        "supplied_uri": "postgresql://u:p@example.com/sales",
    }
    base.update(overrides)
    return SemanticProjectResolveRequest(**base)


def test_slugify_is_identity_safe():
    assert slugify_project_name("Sales Analytics!") == "sales-analytics"
    assert slugify_project_name("  Mixed__CASE/slug  ") == "mixed-case-slug"
    assert slugify_project_name("") == "project"
    assert slugify_project_name("***") == "project"


def test_project_derives_slug_from_name_when_absent():
    project = SemanticProject(
        name="Sales Analytics",
        owner_id="o",
        database_uri_fingerprint="fp",
        schema_name="sales",
    )
    assert project.slug == "sales-analytics"


@pytest.mark.parametrize("kind", ALL_STORES)
def test_resolve_assigns_unique_slug(kind: str) -> None:
    store = _store(kind)
    # Two explicit projects with the same name in the same DB/catalog → distinct slugs.
    a = store.create(_request(name="Revenue"), owner_id="o")
    b = store.create(_request(name="Revenue"), owner_id="o")
    assert a.slug == "revenue"
    assert b.slug == "revenue-2"
    assert a.id != b.id


@pytest.mark.parametrize("kind", ALL_STORES)
def test_default_resolve_gets_a_slug(kind: str) -> None:
    store = _store(kind)
    project = store.resolve(_request(), owner_id="o")
    assert project.slug  # derived from the default "Sales.prod.sales" name
    assert project.slug == "sales-prod-sales"


@pytest.mark.parametrize("kind", ALL_STORES)
def test_rename_reslugs_uniquely(kind: str) -> None:
    store = _store(kind)
    existing = store.create(_request(name="Existing"), owner_id="o")
    target = store.create(_request(name="Target"), owner_id="o")
    # Rename target to collide with existing's name → suffixed slug.
    renamed = store.rename(target.id, "Existing", owner_id="o")
    assert renamed.name == "Existing"
    assert renamed.slug == "existing-2"
    # The original keeps its slug.
    assert store.get(existing.id, owner_id="o").slug == "existing"


@pytest.mark.parametrize("kind", ALL_STORES)
def test_slug_unique_per_catalog_not_global(kind: str) -> None:
    store = _store(kind)
    a = store.create(_request(name="Shared", catalog_name="prod"), owner_id="o")
    b = store.create(_request(name="Shared", catalog_name="staging"), owner_id="o")
    # Different catalog → the same slug is allowed.
    assert a.slug == "shared"
    assert b.slug == "shared"


@pytest.mark.parametrize("kind", ALL_STORES)
def test_resolve_tolerates_multiple_active_projects_in_one_schema(kind: str) -> None:
    # Post-slug (P1), a schema is no longer a unique key: a database can hold many
    # active projects with the same primary schema (and legacy data created under the
    # old NULL-distinct constraint already does). Resolve must pick one
    # deterministically (newest-updated), not raise MultipleResultsFound (→ 500).
    store = _store(kind)
    store.create(_request(name="Sales One", schema_name="sales"), owner_id="o")
    newest = store.create(_request(name="Sales Two", schema_name="sales"), owner_id="o")

    resolved = store.resolve(
        _request(name="Sales One", schema_name="sales"), owner_id="o"
    )

    # Does not raise, and returns the most recently updated match.
    assert resolved.id == newest.id
    assert resolved.schema_name == "sales"
