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

from __future__ import annotations

import pytest

from superset_ai_agent.semantic_layer.projects import (
    InMemorySemanticProjectStore,
    SemanticProjectNotFoundError,
)
from superset_ai_agent.semantic_layer.schemas import SemanticProjectResolveRequest
from superset_ai_agent.semantic_layer.uri_fingerprint import (
    fingerprint_database_uri,
    normalize_database_uri,
)


def test_database_uri_fingerprint_removes_credentials_and_default_port() -> None:
    left = fingerprint_database_uri(
        "postgresql://user:secret@Example.com:5432/warehouse?sslmode=require"
    )
    right = fingerprint_database_uri(
        "postgresql://analyst:other@example.com/warehouse?sslmode=require"
    )

    assert left == right
    assert (
        normalize_database_uri(
            "postgresql://user:secret@Example.com:5432/warehouse?password=secret"
        )
        == "postgresql://example.com/warehouse"
    )


def test_project_resolution_is_one_project_per_database_catalog_schema() -> None:
    store = InMemorySemanticProjectStore()
    request = SemanticProjectResolveRequest(
        database_id=7,
        database_label="Sales",
        catalog_name="prod",
        schema_name="pipeline",
        supplied_uri="postgresql://user:secret@example.com/sales",
    )

    owner_project = store.resolve(request, owner_id="owner")
    shared_project = store.resolve(
        request.model_copy(
            update={
                "supplied_uri": "postgresql://user:other@example.com/sales",
            }
        ),
        owner_id="analyst",
    )

    assert shared_project.id == owner_project.id
    assert shared_project.name == "Sales.prod.pipeline"
    # F5/DP2: access is database-derived, not ownership. The store baseline grants a
    # db_access project "write" to any caller (the access service refines per the
    # caller's DB-access level); there is no owner-admin tier.
    assert shared_project.permission == "write"
    assert store.get(owner_project.id, owner_id="owner").permission == "write"


def test_project_resolution_can_refuse_creation() -> None:
    store = InMemorySemanticProjectStore()

    with pytest.raises(SemanticProjectNotFoundError):
        store.resolve(
            SemanticProjectResolveRequest(
                database_id=7,
                schema_name="pipeline",
                create_if_missing=False,
            ),
            owner_id="owner",
        )
