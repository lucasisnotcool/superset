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
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.review import apply_review
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerReviewRequest,
    SemanticUpdate,
)


def test_apply_review_marks_approved_and_rejected_updates() -> None:
    store = InMemorySemanticLayerStore()
    approved = SemanticUpdate(
        kind="metric",
        target={"field": "gross_moves"},
        value={"definition": "count moves"},
        source_document_id="doc-1",
    )
    rejected = SemanticUpdate(
        kind="synonym",
        target={"field": "stage"},
        value={"text": "phase"},
        source_document_id="doc-1",
    )
    document = SemanticDocument(
        id="doc-1",
        filename="notes.txt",
        content_type="text/plain",
        size_bytes=10,
        scope=ConversationScope(database_id=1, dataset_ids=[42]),
        checksum="abc",
        storage_uri="file:///tmp/notes.txt",
        proposed_updates=[approved, rejected],
    )
    store.save_document(document, owner_id="user-1")

    reviewed = apply_review(
        store,
        document_id=document.id,
        request=SemanticLayerReviewRequest(
            approved_update_ids=[approved.id],
            rejected_update_ids=[rejected.id],
            notes="reviewed",
        ),
        owner_id="user-1",
        reviewer_id="ada",
    )

    assert reviewed.status == "approved"
    updates = {update.id: update for update in reviewed.proposed_updates}
    assert updates[approved.id].approved is True
    assert updates[approved.id].reviewer_id == "ada"
    assert updates[rejected.id].approved is False
    assert updates[rejected.id].reviewed is True
