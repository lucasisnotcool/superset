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

"""DB-access scoping (F5): provenance/coverage/RAG are project-level, not per-user.

These are the security-acceptance gates for P2 — two DB-authorized users must see
the same project context (R9/R12), and project scoping must never widen beyond the
database boundary (R1).
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
from superset_ai_agent.semantic_layer.document_chunks import DocumentChunk
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
)
from superset_ai_agent.semantic_layer.sqlalchemy_store import (
    SqlAlchemySemanticLayerStore,
)


def _store() -> SqlAlchemySemanticLayerStore:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    create_all_for_tests(engine)
    return SqlAlchemySemanticLayerStore(create_session_factory(engine))


def _scope() -> ConversationScope:
    return ConversationScope(database_id=1, schema_name="sales", dataset_ids=[])


def _doc(project_id: str, checksum: str, filename: str) -> SemanticDocument:
    return SemanticDocument(
        project_id=project_id,
        filename=filename,
        content_type="text/plain",
        size_bytes=10,
        scope=_scope(),
        checksum=checksum,
        storage_uri=f"file:///{filename}",
        status="extracted",
    )


def _chunk(project_id: str, document_id: str, index: int, text: str) -> DocumentChunk:
    return DocumentChunk(
        id=f"{document_id}-{index}",
        document_id=document_id,
        project_id=project_id,
        chunk_index=index,
        text=text,
        checksum=f"c{index}",
        char_start=0,
        char_end=len(text),
    )


def test_provenance_is_project_level_across_users() -> None:
    store = _store()
    # User A and user B each record an event on the same project.
    for owner in ("user-a", "user-b"):
        store.append_event(
            SemanticLayerEvent(
                project_id="proj-1",
                type="mdl_updated",
                scope=_scope(),
                message=f"edit by {owner}",
            ),
            owner_id=owner,
        )
    # Either user reading the project's provenance sees BOTH entries.
    seen_by_a = store.list_project_events("proj-1", owner_id="user-a")
    seen_by_b = store.list_project_events("proj-1", owner_id="user-b")
    assert len(seen_by_a) == 2
    assert {e.message for e in seen_by_a} == {"edit by user-a", "edit by user-b"}
    assert [e.message for e in seen_by_a] == [e.message for e in seen_by_b]


def test_coverage_doc_set_is_project_level_across_users() -> None:
    store = _store()
    store.save_document(_doc("proj-1", "h1", "a.txt"), owner_id="user-a")
    store.save_document(_doc("proj-1", "h2", "b.txt"), owner_id="user-b")
    # Coverage reads the project's docs regardless of which user uploaded them.
    docs = store.list_project_documents("proj-1", owner_id="user-a")
    assert {d.filename for d in docs} == {"a.txt", "b.txt"}


def test_rag_corpus_is_project_level_across_users() -> None:
    store = _store()
    doc_a = store.save_document(_doc("proj-1", "h1", "a.txt"), owner_id="user-a")
    doc_b = store.save_document(_doc("proj-1", "h2", "b.txt"), owner_id="user-b")
    store.save_chunks(
        doc_a.id,
        [_chunk("proj-1", doc_a.id, 0, "alpha")],
        owner_id="user-a",
        project_id="proj-1",
    )
    store.save_chunks(
        doc_b.id,
        [_chunk("proj-1", doc_b.id, 0, "beta")],
        owner_id="user-b",
        project_id="proj-1",
    )
    # User B's Copilot retrieves over user A's chunks too (the whole project corpus).
    chunks = store.list_project_chunks("proj-1", owner_id="user-b")
    assert {c.text for c in chunks} == {"alpha", "beta"}


def test_project_scoping_does_not_cross_the_project_boundary() -> None:
    store = _store()
    store.append_event(
        SemanticLayerEvent(
            project_id="proj-A", type="mdl_updated", scope=_scope(), message="A"
        ),
        owner_id="user-a",
    )
    store.append_event(
        SemanticLayerEvent(
            project_id="proj-B", type="mdl_updated", scope=_scope(), message="B"
        ),
        owner_id="user-a",
    )
    store.save_document(_doc("proj-A", "h1", "a.txt"), owner_id="user-a")
    store.save_document(_doc("proj-B", "h2", "b.txt"), owner_id="user-a")
    # A project's reads never leak another project's (and thus another DB's) data.
    assert [e.message for e in store.list_project_events("proj-A")] == ["A"]
    assert [d.filename for d in store.list_project_documents("proj-B")] == ["b.txt"]


def test_dedup_is_project_scoped_across_users() -> None:
    store = _store()
    first = store.save_document(_doc("proj-1", "same-hash", "a.txt"), owner_id="user-a")
    # User B looks up by the same checksum in the same project → finds A's document.
    found = store.find_document_by_checksum(
        "proj-1", "same-hash", owner_id="user-b"
    )
    assert found is not None
    assert found.id == first.id


def test_dedup_does_not_cross_projects() -> None:
    store = _store()
    store.save_document(_doc("proj-1", "h", "a.txt"), owner_id="user-a")
    # Same bytes, different project → not a duplicate (distinct artifact).
    assert store.find_document_by_checksum("proj-2", "h", owner_id="user-a") is None


def test_duplicate_documents_copies_all_uploaders_docs_with_fresh_ids() -> None:
    # DP6 include-documents: copying a project's docs into a clone re-parents every
    # uploader's documents/chunks under fresh ids (project-scoped reads), so the
    # clone's RAG corpus matches the source without sharing rows.
    store = _store()
    doc_a = store.save_document(_doc("src", "h1", "a.txt"), owner_id="user-a")
    doc_b = store.save_document(_doc("src", "h2", "b.txt"), owner_id="user-b")
    store.save_chunks(
        doc_a.id,
        [_chunk("src", doc_a.id, 0, "alpha")],
        owner_id="user-a",
        project_id="src",
    )
    store.save_chunks(
        doc_b.id,
        [_chunk("src", doc_b.id, 0, "beta")],
        owner_id="user-b",
        project_id="src",
    )

    new_chunks = store.duplicate_documents("src", "clone", owner_id="user-a")

    # Both uploaders' documents came across, re-parented to the clone …
    clone_docs = store.list_project_documents("clone")
    assert {d.filename for d in clone_docs} == {"a.txt", "b.txt"}
    assert all(d.project_id == "clone" for d in clone_docs)
    assert {d.id for d in clone_docs}.isdisjoint({doc_a.id, doc_b.id})
    # … with the full chunk corpus copied under fresh ids …
    clone_chunks = store.list_project_chunks("clone")
    assert {c.text for c in clone_chunks} == {"alpha", "beta"}
    assert {c.id for c in clone_chunks} == {c.id for c in new_chunks}
    assert {c.id for c in clone_chunks}.isdisjoint({doc_a.id + "-0", doc_b.id + "-0"})
    # … and the source is untouched.
    assert {d.filename for d in store.list_project_documents("src")} == {
        "a.txt",
        "b.txt",
    }
