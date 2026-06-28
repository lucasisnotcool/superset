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

"""Project duplication: structural clone copies files+schemas, not history (DP6/DP8)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
from superset_ai_agent.semantic_layer.mdl_files import (
    InMemoryMdlFileStore,
    SqlAlchemyMdlFileStore,
)
from superset_ai_agent.semantic_layer.projects import (
    InMemorySemanticProjectStore,
    SqlAlchemySemanticProjectStore,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
    SemanticProjectResolveRequest,
)

ALL_STORES = ["memory", "sqlalchemy"]


def _stores(kind: str):
    if kind == "memory":
        return InMemorySemanticProjectStore(), InMemoryMdlFileStore()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    factory = create_session_factory(engine)
    return (
        SqlAlchemySemanticProjectStore(factory),
        SqlAlchemyMdlFileStore(factory),
    )


def _request(**overrides) -> SemanticProjectResolveRequest:
    base = {
        "database_id": 7,
        "database_label": "Sales",
        "catalog_name": "prod",
        "schema_name": "sales",
        "schema_names": ["sales", "crm"],
        "supplied_uri": "postgresql://u:p@example.com/sales",
    }
    base.update(overrides)
    return SemanticProjectResolveRequest(**base)


_VALID_MODEL = (
    '{"models":[{"name":"orders","tableReference":{"schema":"sales",'
    '"table":"orders"},"columns":[{"name":"id","type":"int"}]}]}'
)


def _seed_files(file_store, project_id: str) -> None:
    active = file_store.create(
        project_id,
        MdlFileCreateRequest(path="models/orders.json", content=_VALID_MODEL),
        owner_id="o",
    )
    file_store.update(
        active.id, MdlFileUpdateRequest(status="active"), owner_id="o"
    )
    file_store.create(
        project_id,
        MdlFileCreateRequest(path="models/draft.json", content='{"models":[]}'),
        owner_id="o",
    )


@pytest.mark.parametrize("kind", ALL_STORES)
def test_clone_creates_fresh_identity_with_same_schema_set(kind: str) -> None:
    projects, _ = _stores(kind)
    source = projects.create(_request(name="Sales Analytics"), owner_id="o")

    clone = projects.clone(source.id, owner_id="o")

    assert clone.id != source.id
    assert clone.name == "Sales Analytics (copy)"
    assert clone.slug == "sales-analytics-copy"
    assert clone.schema_names == source.schema_names  # schema set carried
    assert clone.status == "active"


@pytest.mark.parametrize("kind", ALL_STORES)
def test_clone_with_explicit_name(kind: str) -> None:
    projects, _ = _stores(kind)
    source = projects.create(_request(name="Base"), owner_id="o")
    clone = projects.clone(source.id, new_name="My Fork", owner_id="o")
    assert clone.name == "My Fork"
    assert clone.slug == "my-fork"


@pytest.mark.parametrize("kind", ALL_STORES)
def test_duplicate_files_copies_structure_and_preserves_status(kind: str) -> None:
    projects, files = _stores(kind)
    source = projects.create(_request(name="Base"), owner_id="o")
    _seed_files(files, source.id)
    clone = projects.clone(source.id, owner_id="o")

    copied = files.duplicate_files(source.id, clone.id, owner_id="o")
    assert copied == 2

    clone_files = files.list(clone.id, owner_id="o")
    by_path = {f.path: f for f in clone_files}
    assert set(by_path) == {"models/orders.json", "models/draft.json"}
    # Status is preserved (the active file stays queryable in the clone).
    assert by_path["models/orders.json"].status == "active"
    assert by_path["models/draft.json"].status == "draft"
    # New file ids, re-parented to the clone.
    source_ids = {f.id for f in files.list(source.id, owner_id="o")}
    assert all(f.id not in source_ids for f in clone_files)
    assert all(f.project_id == clone.id for f in clone_files)


@pytest.mark.parametrize("kind", ALL_STORES)
def test_clone_is_independent_of_source(kind: str) -> None:
    projects, files = _stores(kind)
    source = projects.create(_request(name="Base"), owner_id="o")
    _seed_files(files, source.id)
    clone = projects.clone(source.id, owner_id="o")
    files.duplicate_files(source.id, clone.id, owner_id="o")

    # Editing the source's files does not affect the clone's (deep copy).
    source_file = files.list(source.id, owner_id="o")[0]
    files.update(
        source_file.id,
        MdlFileUpdateRequest(content='{"models":[{"name":"x"}]}'),
        owner_id="o",
    )
    clone_paths = {f.path: f.content for f in files.list(clone.id, owner_id="o")}
    assert '{"name":"x"}' not in clone_paths["models/orders.json"]
