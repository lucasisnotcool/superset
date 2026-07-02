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
"""DB-tied artifacts (D1b): sharing keyed by database URI fingerprint.

Two users bring their *own* Superset connections to the same physical database
(different ``database_id``, same fingerprint). Documents, NL->SQL memory,
instructions, and project access must be shared across those connections —
and stay invisible to anyone with no connection to that database (R5/R6).
"""

from types import SimpleNamespace

import pytest

from superset_ai_agent.auth import AgentIdentity
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.semantic_layer.access import SemanticAccessService
from superset_ai_agent.semantic_layer.instructions import InMemoryInstructionStore
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.memory_store import InMemoryMemory
from superset_ai_agent.semantic_layer.schemas import SemanticDocument
from superset_ai_agent.semantic_layer.store import (
    scope_hash,
    scope_hashes,
    scope_matches,
)

FP = "a" * 64  # fingerprint of the shared physical database

# User A's and user B's own connections to the same physical database.
SCOPE_A = ConversationScope(
    database_id=5, schema_name="sales", database_uri_fingerprint=FP
)
SCOPE_B = ConversationScope(
    database_id=9, schema_name="sales", database_uri_fingerprint=FP
)


# --- scope identity ----------------------------------------------------------


def test_scope_hash_shares_across_connections_with_same_fingerprint() -> None:
    assert scope_hash(SCOPE_A) == scope_hash(SCOPE_B)


def test_scope_hash_without_fingerprint_stays_per_connection() -> None:
    a = SCOPE_A.model_copy(update={"database_uri_fingerprint": None})
    b = SCOPE_B.model_copy(update={"database_uri_fingerprint": None})
    assert scope_hash(a) != scope_hash(b)


def test_scope_hashes_carries_legacy_hash_for_back_compat() -> None:
    hashes = scope_hashes(SCOPE_A)
    legacy = scope_hash(SCOPE_A.model_copy(update={"database_uri_fingerprint": None}))
    assert hashes[0] == scope_hash(SCOPE_A)
    assert legacy in hashes


def test_scope_matches_is_fingerprint_aware() -> None:
    assert scope_matches(SCOPE_A, SCOPE_B)
    # Different physical databases never match, even with equal connection ids.
    other = SCOPE_A.model_copy(update={"database_uri_fingerprint": "b" * 64})
    assert not scope_matches(other, SCOPE_B.model_copy(update={"database_id": 5}))
    # No fingerprints at all → legacy connection-id matching.
    bare_a = SCOPE_A.model_copy(update={"database_uri_fingerprint": None})
    bare_b = SCOPE_B.model_copy(update={"database_uri_fingerprint": None})
    assert not scope_matches(bare_a, bare_b)
    assert scope_matches(bare_a, bare_a)


# --- documents (R5/R6) --------------------------------------------------------


def _document(scope: ConversationScope) -> SemanticDocument:
    return SemanticDocument(
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=10,
        scope=scope,
        checksum="abc",
        storage_uri="file:///tmp/notes.txt",
        status="extracted",
    )


def test_documents_shared_across_users_own_connections() -> None:
    store = InMemorySemanticLayerStore()
    saved = store.save_document(_document(SCOPE_A), owner_id="user-a")

    # User B, through their own connection (different database_id, same
    # fingerprint), sees user A's document.
    listed = store.list_documents(SCOPE_B, owner_id="user-b")
    assert [item.id for item in listed] == [saved.id]

    # A user scoped to a different physical database sees nothing (R6).
    elsewhere = SCOPE_B.model_copy(
        update={"database_uri_fingerprint": "b" * 64, "database_id": 77}
    )
    assert store.list_documents(elsewhere, owner_id="user-c") == []


# --- NL->SQL memory (DP-B) ----------------------------------------------------


def test_memory_pool_shared_across_users_own_connections() -> None:
    memory = InMemoryMemory()
    memory.store_confirmed(
        question="total sales?",
        semantic_sql="SELECT 1",
        native_sql="SELECT 1",
        database_id=5,
        created_by="user-a",
        database_uri_fingerprint=FP,
    )

    recalled = memory.recall_examples(
        "total sales?", database_id=9, k=3, database_uri_fingerprint=FP
    )
    assert [pair.question for pair in recalled] == ["total sales?"]

    # No fingerprint → per-connection pool; a different connection id misses.
    assert memory.recall_examples("total sales?", database_id=9, k=3) == []


# --- instructions (R8 parity source) -------------------------------------------


def test_instructions_shared_across_users_own_connections() -> None:
    store = InMemoryInstructionStore()
    # Authored by user A under their connection's fingerprinted scope hash.
    store.add(
        instruction="Exclude test rows",
        scope_hash=scope_hash(SCOPE_A),
        owner_id="user-a",
        is_global=True,
    )
    # User B recalls through their own connection's scope hashes.
    recalled = store.recall(
        "anything", scope_hashes=scope_hashes(SCOPE_B), owner_id="user-b", k=3
    )
    assert [item.instruction for item in recalled] == ["Exclude test rows"]


def test_instructions_legacy_rows_still_recalled_by_original_connection() -> None:
    store = InMemoryInstructionStore()
    legacy_scope = SCOPE_A.model_copy(update={"database_uri_fingerprint": None})
    store.add(
        instruction="legacy rule",
        scope_hash=scope_hash(legacy_scope),
        owner_id="user-a",
        is_global=True,
    )
    # The fingerprinted read carries the legacy hash second, so the row is
    # still found from the same connection.
    recalled = store.recall(
        "anything", scope_hashes=scope_hashes(SCOPE_A), owner_id="user-b", k=3
    )
    assert [item.instruction for item in recalled] == ["legacy rule"]


# --- project access translation (R5 write / R6 deny) ---------------------------


def _dataset(database_id: int):
    from superset_ai_agent.integrations.superset.client import DatasetMetadata

    return DatasetMetadata(
        id=1, table_name="orders", database_id=database_id, columns=[], metrics=[]
    )


class _DeniedForForeignConnection:
    """load_context stub: only the caller's own connection resolves."""

    def __init__(self, allowed_database_id: int, *, with_datasets: bool) -> None:
        self.allowed_database_id = allowed_database_id
        self.with_datasets = with_datasets
        self.loads: list[int] = []

    def __call__(self, scope: ConversationScope):
        from superset_ai_agent.integrations.superset.client import (
            AgentContext,
            DatabaseSummary,
        )

        self.loads.append(scope.database_id)
        if scope.database_id != self.allowed_database_id:
            raise RuntimeError("404: database not visible to this session")
        return AgentContext(
            database=DatabaseSummary(id=scope.database_id, name="db"),
            datasets=([_dataset(scope.database_id)] if self.with_datasets else []),
        )


def _project() -> SimpleNamespace:
    from superset_ai_agent.semantic_layer.schemas import SemanticProject

    return SemanticProject(
        id="p1",
        name="Sales",
        slug="sales",
        owner_id="user-a",
        database_uri_fingerprint=FP,
        schema_name="sales",
        schema_names=["sales"],
        default_database_id=5,  # user A's connection
        visibility="db_access",
    )


class _SingleProjectStore:
    def __init__(self, project) -> None:
        self.project = project

    def get(self, project_id: str, *, owner_id: str = "local"):
        assert project_id == self.project.id
        return self.project


def _access_service(load_context, caller_databases):
    def get_database_identity(database_id: int, catalog_name):
        for db in caller_databases:
            if db["id"] == database_id:
                return SimpleNamespace(uri_fingerprint=db["fingerprint"])
        raise RuntimeError("not visible")

    return SemanticAccessService(
        project_store=_SingleProjectStore(_project()),
        load_context=load_context,
        get_database_identity=get_database_identity,
        list_databases=lambda: [
            SimpleNamespace(id=db["id"]) for db in caller_databases
        ],
    )


def test_project_write_via_fingerprint_translation() -> None:
    # User B cannot see connection 5 (owner-scoped in Superset) but owns
    # connection 9 to the same physical database.
    load_context = _DeniedForForeignConnection(9, with_datasets=True)
    service = _access_service(
        load_context, caller_databases=[{"id": 9, "fingerprint": FP}]
    )
    from superset_ai_agent.semantic_layer.access import SemanticPermission

    resolved = service.require_project_permission(
        identity=AgentIdentity(owner_id="superset:b"),
        project_id="p1",
        permission=SemanticPermission.WRITE,
    )
    # FULL access through B's own connection → write (D1a: both can edit).
    assert resolved.permission == "write"
    # It re-proved through the translated connection id.
    assert 9 in load_context.loads


def test_project_denied_without_any_connection_to_that_database() -> None:
    # User C has a connection, but to a DIFFERENT physical database (R6).
    load_context = _DeniedForForeignConnection(11, with_datasets=True)
    service = _access_service(
        load_context, caller_databases=[{"id": 11, "fingerprint": "b" * 64}]
    )
    from superset_ai_agent.semantic_layer.access import SemanticPermission

    # The stub's 404 propagates untranslated — no fingerprint match exists.
    with pytest.raises(RuntimeError, match="not visible"):
        service.require_project_permission(
            identity=AgentIdentity(owner_id="superset:c"),
            project_id="p1",
            permission=SemanticPermission.READ,
        )


# --- grounding resolution across connections (goldens/projects in the graphs) --


def test_resolve_effective_schema_accepts_fingerprint_matched_project() -> None:
    from superset_ai_agent.semantic_layer.projects import (
        InMemorySemanticProjectStore,
    )
    from superset_ai_agent.semantic_layer.schemas import (
        SemanticProjectResolveRequest,
    )
    from superset_ai_agent.semantic_layer.wren_runtime import (
        resolve_effective_schema,
    )

    store = InMemorySemanticProjectStore()
    project = store.create(
        SemanticProjectResolveRequest(
            database_id=5,  # authored under user A's connection
            database_label="Sales",
            database_uri_fingerprint=FP,
            schema_name="sales",
        ),
        owner_id="user-a",
    )

    # User B pins the project from their own connection (database_id=9): the
    # DB guard must treat the fingerprint match as "same database" and infer
    # the project's schema instead of passing through.
    schema, schemas = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="user-b",
        database_id=9,
        schema_name=None,
        project_id=project.id,
        database_uri_fingerprint=FP,
    )
    assert schema == "sales"
    assert schemas == ["sales"]

    # Without the fingerprint (legacy behavior) the guard still rejects the
    # cross-connection pin — never infer onto the wrong database.
    schema, schemas = resolve_effective_schema(
        semantic_project_store=store,
        owner_id="user-b",
        database_id=9,
        schema_name=None,
        project_id=project.id,
    )
    assert schema is None
    assert schemas == []


def test_copilot_parity_scope_hashes_match_sql_agent() -> None:
    """R8: both agents derive identical DB-tied instruction hashes."""

    from superset_ai_agent.conversation_graph import ConversationGraph
    from superset_ai_agent.graph import TextToSqlGraph
    from superset_ai_agent.schemas import AgentQueryRequest

    sql_graph = TextToSqlGraph.__new__(TextToSqlGraph)
    copilot = ConversationGraph.__new__(ConversationGraph)

    request = AgentQueryRequest(
        question="q",
        database_id=SCOPE_A.database_id,
        schema_name=SCOPE_A.schema_name,
        database_uri_fingerprint=FP,
    )
    assert sql_graph._instruction_scope_hashes(
        request
    ) == copilot._instruction_scope_hashes(SCOPE_A)
