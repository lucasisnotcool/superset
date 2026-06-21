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

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.semantic_layer.indexer import rebuild_index
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.schemas import SemanticDocument, SemanticUpdate


def test_rebuild_index_uses_only_reviewed_approved_updates() -> None:
    scope = ConversationScope(database_id=1, dataset_ids=[42])
    store = InMemorySemanticLayerStore()
    approved = SemanticUpdate(
        kind="metric",
        target={"field": "gross_moves"},
        value={"definition": "count moves"},
        source_document_id="doc-1",
        reviewed=True,
        approved=True,
    )
    unreviewed = SemanticUpdate(
        kind="synonym",
        target={"field": "stage"},
        value={"text": "phase"},
        source_document_id="doc-1",
    )
    store.save_document(
        SemanticDocument(
            id="doc-1",
            filename="notes.txt",
            content_type="text/plain",
            size_bytes=10,
            scope=scope,
            checksum="abc",
            storage_uri="file:///tmp/notes.txt",
            status="approved",
            proposed_updates=[approved, unreviewed],
        ),
        owner_id="user-1",
    )

    version = rebuild_index(store, scope=scope, owner_id="user-1")

    assert version.wren_context is not None
    assert version.wren_context.available is True
    assert version.wren_context.document_ids == ["doc-1"]
    assert [item["id"] for item in version.wren_context.context_items] == [approved.id]
    assert store.get_document("doc-1", owner_id="user-1").status == "indexed"
