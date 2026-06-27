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

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
from superset_ai_agent.semantic_layer.document_chunks import build_chunk_records
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.schemas import SemanticDocument
from superset_ai_agent.semantic_layer.sqlalchemy_store import (
    SqlAlchemySemanticLayerStore,
)
from superset_ai_agent.semantic_layer.store import SemanticDocumentNotFoundError

_SCOPE = ConversationScope(
    database_id=1,
    catalog_name="prod",
    schema_name="pipeline",
    dataset_ids=[],
)


def _sqlalchemy_store() -> SqlAlchemySemanticLayerStore:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    return SqlAlchemySemanticLayerStore(create_session_factory(engine))


def _save_document(store, *, owner_id: str = "user-1") -> SemanticDocument:
    return store.save_document(
        SemanticDocument(
            project_id="project-1",
            filename="notes.md",
            content_type="text/markdown",
            size_bytes=10,
            scope=_SCOPE,
            checksum="abc",
            storage_uri="file:///tmp/notes.md",
            status="extracted",
        ),
        owner_id=owner_id,
    )


# Both store implementations must satisfy the same contract.
def _stores():
    return [_sqlalchemy_store(), InMemorySemanticLayerStore()]


@pytest.mark.parametrize("store", _stores())
def test_save_and_list_chunks_round_trip(store) -> None:
    document = _save_document(store)
    records = build_chunk_records(document.id, "alpha\n\nbeta\n\ngamma")
    saved = store.save_chunks(
        document.id, records, owner_id="user-1", project_id="project-1"
    )

    assert [chunk.chunk_index for chunk in saved] == [0, 1, 2]
    listed = store.list_chunks(document.id, owner_id="user-1")
    assert [chunk.text for chunk in listed] == ["alpha", "beta", "gamma"]
    # Owner isolation.
    assert store.list_chunks(document.id, owner_id="other") == []


@pytest.mark.parametrize("store", _stores())
def test_save_chunks_replaces_prior_set(store) -> None:
    document = _save_document(store)
    store.save_chunks(
        document.id,
        build_chunk_records(document.id, "one\n\ntwo\n\nthree"),
        owner_id="user-1",
        project_id="project-1",
    )
    # Reindex with fewer chunks must not leave stragglers.
    store.save_chunks(
        document.id,
        build_chunk_records(document.id, "only"),
        owner_id="user-1",
        project_id="project-1",
    )
    listed = store.list_chunks(document.id, owner_id="user-1")
    assert [chunk.text for chunk in listed] == ["only"]


@pytest.mark.parametrize("store", _stores())
def test_delete_chunks_removes_only_that_document(store) -> None:
    document = _save_document(store)
    store.save_chunks(
        document.id,
        build_chunk_records(document.id, "a\n\nb"),
        owner_id="user-1",
        project_id="project-1",
    )
    store.delete_chunks(document.id, owner_id="user-1")
    assert store.list_chunks(document.id, owner_id="user-1") == []


@pytest.mark.parametrize("store", _stores())
def test_delete_document_cascades_to_chunks(store) -> None:
    document = _save_document(store)
    store.save_chunks(
        document.id,
        build_chunk_records(document.id, "a\n\nb\n\nc"),
        owner_id="user-1",
        project_id="project-1",
    )

    store.delete_document(document.id, owner_id="user-1")

    # Document gone...
    with pytest.raises(SemanticDocumentNotFoundError):
        store.get_document(document.id, owner_id="user-1")
    # ...and no orphan chunks remain.
    assert store.list_chunks(document.id, owner_id="user-1") == []


@pytest.mark.parametrize("store", _stores())
def test_delete_document_rejects_wrong_owner(store) -> None:
    document = _save_document(store)
    with pytest.raises(SemanticDocumentNotFoundError):
        store.delete_document(document.id, owner_id="intruder")


@pytest.mark.parametrize("store", _stores())
def test_list_project_chunks_spans_documents(store) -> None:
    first = _save_document(store)
    second = _save_document(store)
    store.save_chunks(
        first.id,
        build_chunk_records(first.id, "a\n\nb"),
        owner_id="user-1",
        project_id="project-1",
    )
    store.save_chunks(
        second.id,
        build_chunk_records(second.id, "c"),
        owner_id="user-1",
        project_id="project-1",
    )

    project_chunks = store.list_project_chunks("project-1", owner_id="user-1")
    assert len(project_chunks) == 3
    assert {chunk.document_id for chunk in project_chunks} == {first.id, second.id}
    assert store.list_project_chunks("other-project", owner_id="user-1") == []


def _save_document_with(
    store,
    *,
    checksum: str,
    project_id: str = "project-1",
    owner_id: str = "user-1",
    created_at: datetime | None = None,
) -> SemanticDocument:
    fields: dict = {
        "project_id": project_id,
        "filename": "notes.md",
        "content_type": "text/markdown",
        "size_bytes": 10,
        "scope": _SCOPE,
        "checksum": checksum,
        "storage_uri": "file:///tmp/notes.md",
        "status": "extracted",
    }
    if created_at is not None:
        fields["created_at"] = created_at
    return store.save_document(SemanticDocument(**fields), owner_id=owner_id)


@pytest.mark.parametrize("store", _stores())
def test_find_document_by_checksum_returns_match(store) -> None:
    saved = _save_document_with(store, checksum="hash-1")

    found = store.find_document_by_checksum("project-1", "hash-1", owner_id="user-1")
    assert found is not None
    assert found.id == saved.id
    # The persisted/reloaded document never carries the transient dedup flag.
    assert found.deduplicated is False


@pytest.mark.parametrize("store", _stores())
def test_find_document_by_checksum_misses(store) -> None:
    _save_document_with(store, checksum="hash-1")

    assert (
        store.find_document_by_checksum("project-1", "other-hash", owner_id="user-1")
        is None
    )
    # Owner isolation: the same bytes under a different owner do not match.
    assert (
        store.find_document_by_checksum("project-1", "hash-1", owner_id="intruder")
        is None
    )
    # Project isolation: the same bytes in another project are a distinct artifact.
    assert (
        store.find_document_by_checksum("other-project", "hash-1", owner_id="user-1")
        is None
    )


@pytest.mark.parametrize("store", _stores())
def test_find_document_by_checksum_returns_newest(store) -> None:
    older = _save_document_with(
        store,
        checksum="dup",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    newer = _save_document_with(
        store,
        checksum="dup",
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )

    found = store.find_document_by_checksum("project-1", "dup", owner_id="user-1")
    assert found is not None
    assert found.id == newer.id
    assert found.id != older.id
