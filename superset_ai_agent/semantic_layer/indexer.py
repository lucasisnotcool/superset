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

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.schemas import SemanticLayerVersion
from superset_ai_agent.semantic_layer.store import SemanticLayerStore, scope_hash


def rebuild_index(
    store: SemanticLayerStore,
    *,
    scope: ConversationScope,
    owner_id: str = DEFAULT_OWNER_ID,
) -> SemanticLayerVersion:
    """Build a reviewed semantic overlay from approved document updates."""

    updates = store.list_approved_updates(scope, owner_id=owner_id)
    created_at = _utc_now()
    version_name = created_at.strftime("%Y%m%d%H%M%S")
    document_ids = sorted({update.source_document_id for update in updates})
    context_items = [
        {
            "id": update.id,
            "kind": update.kind,
            "target": update.target,
            "value": update.value,
            "source_document_id": update.source_document_id,
        }
        for update in updates
    ]
    wren_context = WrenContextArtifact(
        enabled=True,
        available=bool(context_items),
        document_ids=document_ids,
        semantic_layer_version=version_name,
        indexing_status="idle",
        context_items=context_items,
        warnings=[] if context_items else ["No approved semantic updates to index."],
    )
    version = SemanticLayerVersion(
        scope=scope,
        scope_hash=scope_hash(scope),
        version=version_name,
        status="idle",
        mdl={
            "version": version_name,
            "scope": scope.model_dump(mode="json"),
            "semantic_updates": context_items,
        },
        wren_context=wren_context,
        source_update_ids=[update.id for update in updates],
        created_at=created_at,
    )
    saved_version = store.save_version(version, owner_id=owner_id)
    for document_id in document_ids:
        document = store.get_document(document_id, owner_id=owner_id)
        store.update_document(
            document.model_copy(
                update={
                    "status": "indexed",
                    "updated_at": created_at,
                }
            ),
            owner_id=owner_id,
        )
    return saved_version


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
