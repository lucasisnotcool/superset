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

"""Model layer for multi-schema MDL projects (schema set + scope hashing)."""

from __future__ import annotations

from superset_ai_agent.conversations.schemas import (
    ConversationScope,
    normalize_schema_names,
)
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    SemanticProjectResolveRequest,
)
from superset_ai_agent.semantic_layer.store import scope_hash, scope_matches


def _project(**overrides) -> SemanticProject:
    base = {
        "name": "proj",
        "owner_id": "owner",
        "database_uri_fingerprint": "fp",
        "schema_name": "sales",
    }
    base.update(overrides)
    return SemanticProject(**base)


def test_normalize_schema_names_orders_primary_first_and_dedupes():
    assert normalize_schema_names("sales", ["crm", "sales", "crm"]) == [
        "sales",
        "crm",
    ]
    assert normalize_schema_names(None, ["a", "", "a", "b"]) == ["a", "b"]
    assert normalize_schema_names("only", None) == ["only"]
    assert normalize_schema_names(None, None) == []


def test_single_schema_project_gets_one_element_set():
    project = _project()
    assert project.schema_name == "sales"
    assert project.schema_names == ["sales"]


def test_multi_schema_project_keeps_primary_first():
    project = _project(schema_name="sales", schema_names=["crm", "sales"])
    # primary stays element 0; extras follow, de-duplicated
    assert project.schema_name == "sales"
    assert project.schema_names == ["sales", "crm"]


def test_project_schema_names_supersedes_when_primary_absent_from_set():
    # primary is always present and first, even if omitted from the extras list
    project = _project(schema_name="warehouse", schema_names=["a", "b"])
    assert project.schema_names == ["warehouse", "a", "b"]


def test_resolve_request_resolved_schema_names():
    request = SemanticProjectResolveRequest(
        database_id=1, schema_name="sales", schema_names=["crm", "sales"]
    )
    assert request.resolved_schema_names() == ["sales", "crm"]
    scalar = SemanticProjectResolveRequest(database_id=1, schema_name="sales")
    assert scalar.resolved_schema_names() == ["sales"]


def test_scope_effective_schema_names():
    scope = ConversationScope(
        database_id=1, schema_name="sales", schema_names=["crm", "sales"]
    )
    assert scope.effective_schema_names == ["sales", "crm"]
    scalar = ConversationScope(database_id=1, schema_name="sales")
    assert scalar.effective_schema_names == ["sales"]


def test_scope_hash_single_schema_is_backward_compatible():
    # A scope that only sets schema_name must hash identically whether or not the
    # new schema_names field is touched — protects existing NL→SQL memory.
    legacy = ConversationScope(database_id=1, catalog_name=None, schema_name="sales")
    with_empty_list = ConversationScope(
        database_id=1, catalog_name=None, schema_name="sales", schema_names=["sales"]
    )
    assert scope_hash(legacy) == scope_hash(with_empty_list)


def test_scope_hash_multi_schema_is_distinct_and_order_independent():
    single = ConversationScope(database_id=1, schema_name="sales")
    multi_a = ConversationScope(
        database_id=1, schema_name="sales", schema_names=["sales", "crm"]
    )
    multi_b = ConversationScope(
        database_id=1, schema_name="sales", schema_names=["crm", "sales"]
    )
    assert scope_hash(multi_a) != scope_hash(single)
    # order of the schema set must not change identity
    assert scope_hash(multi_a) == scope_hash(multi_b)


def test_scope_matches_accounts_for_schema_set():
    left = ConversationScope(
        database_id=1, schema_name="sales", schema_names=["sales", "crm"]
    )
    right = ConversationScope(
        database_id=1, schema_name="sales", schema_names=["crm", "sales"]
    )
    narrow = ConversationScope(database_id=1, schema_name="sales")
    assert scope_matches(left, right)
    assert not scope_matches(left, narrow)
