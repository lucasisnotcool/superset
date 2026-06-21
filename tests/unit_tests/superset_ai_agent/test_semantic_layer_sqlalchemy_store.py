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

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.persistence.database import create_session_factory
from superset_ai_agent.persistence.models import Base
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
    SemanticLayerVersion,
    SemanticUpdate,
)
from superset_ai_agent.semantic_layer.sqlalchemy_store import (
    SqlAlchemySemanticLayerStore,
)
from superset_ai_agent.semantic_layer.store import scope_hash


def _store() -> SqlAlchemySemanticLayerStore:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return SqlAlchemySemanticLayerStore(create_session_factory(engine))


def test_sqlalchemy_semantic_layer_store_round_trips_state() -> None:
    scope = ConversationScope(
        database_id=1,
        catalog_name="prod",
        schema_name="pipeline",
        dataset_ids=[42],
    )
    store = _store()
    document = store.save_document(
        SemanticDocument(
            project_id="project-1",
            filename="notes.txt",
            content_type="text/plain",
            size_bytes=10,
            scope=scope,
            checksum="abc",
            storage_uri="file:///tmp/notes.txt",
            status="needs_review",
        ),
        owner_id="user-1",
    )
    update = SemanticUpdate(
        kind="metric",
        target={"field": "gross_moves"},
        value={"definition": "count moves"},
        source_document_id=document.id,
        reviewed=True,
        approved=True,
    )

    store.save_updates(document.id, [update], owner_id="user-1")
    document = store.get_document(document.id, owner_id="user-1")
    version = store.save_version(
        SemanticLayerVersion(
            project_id="project-1",
            scope=scope,
            scope_hash=scope_hash(scope),
            version="v1",
            source_update_ids=[update.id],
        ),
        owner_id="user-1",
    )
    store.append_event(
        SemanticLayerEvent(
            project_id="project-1",
            type="review_saved",
            scope=scope,
            document_id=document.id,
            message="Saved review.",
        ),
        owner_id="user-1",
    )

    assert document.proposed_updates[0].approved is True
    assert document.project_id == "project-1"
    assert document.scope.catalog_name == "prod"
    assert store.list_approved_updates(scope, owner_id="user-1")[0].id == update.id
    assert (
        store.list_project_approved_updates("project-1", owner_id="user-1")[0].id
        == update.id
    )
    assert store.get_latest_version(scope, owner_id="user-1").id == version.id
    latest_version = store.get_latest_version(scope, owner_id="user-1")
    assert latest_version is not None
    assert latest_version.project_id == "project-1"
    state = store.get_state(scope, owner_id="user-1")
    assert state.document_count == 1
    assert state.catalog_name == "prod"
    assert state.project_id == "project-1"
    assert store.get_project_state("project-1", owner_id="user-1").document_count == 1
    assert store.list_events(scope, owner_id="user-1")[0].type == "review_saved"
    assert store.list_project_events("project-1", owner_id="user-1")[0].type == (
        "review_saved"
    )
    assert store.list_documents(scope, owner_id="user-2") == []
