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
from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_session_factory,
)
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
            status="extracted",
        ),
        owner_id="user-1",
    )
    store.append_event(
        SemanticLayerEvent(
            project_id="project-1",
            type="document_extracted",
            scope=scope,
            document_id=document.id,
            message="Extracted document.",
        ),
        owner_id="user-1",
    )

    document = store.get_document(document.id, owner_id="user-1")
    assert document.project_id == "project-1"
    assert document.scope.catalog_name == "prod"
    state = store.get_state(scope, owner_id="user-1")
    assert state.document_count == 1
    assert state.catalog_name == "prod"
    assert state.project_id == "project-1"
    assert store.get_project_state("project-1", owner_id="user-1").document_count == 1
    assert store.list_events(scope, owner_id="user-1")[0].type == "document_extracted"
    assert store.list_project_events("project-1", owner_id="user-1")[0].type == (
        "document_extracted"
    )
    assert store.list_documents(scope, owner_id="user-2") == []
