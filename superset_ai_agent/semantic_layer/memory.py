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
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
    SemanticLayerState,
    SemanticLayerVersion,
    SemanticUpdate,
)
from superset_ai_agent.semantic_layer.store import (
    SemanticDocumentNotFoundError,
    scope_matches,
)


class InMemorySemanticLayerStore:
    """Process-local semantic-layer store for development and tests."""

    def __init__(self) -> None:
        self._documents: dict[str, tuple[str, SemanticDocument]] = {}
        self._versions: list[tuple[str, SemanticLayerVersion]] = []
        self._events: list[tuple[str, SemanticLayerEvent]] = []

    def save_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        self._documents[document.id] = (owner_id, document.model_copy(deep=True))
        return document.model_copy(deep=True)

    def list_documents(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticDocument]:
        return [
            document.model_copy(deep=True)
            for stored_owner_id, document in self._documents.values()
            if stored_owner_id == owner_id and scope_matches(document.scope, scope)
        ]

    def list_project_documents(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticDocument]:
        return [
            document.model_copy(deep=True)
            for stored_owner_id, document in self._documents.values()
            if stored_owner_id == owner_id and document.project_id == project_id
        ]

    def get_document(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        item = self._documents.get(document_id)
        if item is None or item[0] != owner_id:
            raise SemanticDocumentNotFoundError(document_id)
        return item[1].model_copy(deep=True)

    def update_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        self.get_document(document.id, owner_id=owner_id)
        self._documents[document.id] = (owner_id, document.model_copy(deep=True))
        return document.model_copy(deep=True)

    def save_updates(
        self,
        document_id: str,
        updates: list[SemanticUpdate],
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticUpdate]:
        document = self.get_document(document_id, owner_id=owner_id)
        by_id = {update.id: update for update in document.proposed_updates}
        for update in updates:
            by_id[update.id] = update
        document.proposed_updates = list(by_id.values())
        self.update_document(document, owner_id=owner_id)
        return [update.model_copy(deep=True) for update in updates]

    def list_approved_updates(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticUpdate]:
        updates: list[SemanticUpdate] = []
        for document in self.list_documents(scope, owner_id=owner_id):
            updates.extend(
                update.model_copy(deep=True)
                for update in document.proposed_updates
                if update.reviewed and update.approved
            )
        return updates

    def list_project_approved_updates(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticUpdate]:
        updates: list[SemanticUpdate] = []
        for document in self.list_project_documents(project_id, owner_id=owner_id):
            updates.extend(
                update.model_copy(deep=True)
                for update in document.proposed_updates
                if update.reviewed and update.approved
            )
        return updates

    def save_version(
        self,
        version: SemanticLayerVersion,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerVersion:
        self._versions.append((owner_id, version.model_copy(deep=True)))
        return version.model_copy(deep=True)

    def get_latest_version(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerVersion | None:
        matching = [
            version
            for stored_owner_id, version in self._versions
            if stored_owner_id == owner_id and scope_matches(version.scope, scope)
        ]
        if not matching:
            return None
        return sorted(matching, key=lambda item: item.created_at)[-1].model_copy(
            deep=True
        )

    def get_state(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        documents = self.list_documents(scope, owner_id=owner_id)
        latest_version = self.get_latest_version(scope, owner_id=owner_id)
        last_error = next(
            (document.error for document in documents if document.status == "error"),
            None,
        )
        return SemanticLayerState(
            project_id=None,
            database_id=scope.database_id,
            catalog_name=scope.catalog_name,
            schema_name=scope.schema_name,
            dataset_ids=scope.dataset_ids,
            document_count=len(documents),
            approved_document_count=len(
                [
                    document
                    for document in documents
                    if document.status in {"approved", "indexed"}
                ]
            ),
            indexed_document_count=len(
                [document for document in documents if document.status == "indexed"]
            ),
            semantic_layer_version=(
                latest_version.version if latest_version is not None else None
            ),
            indexing_status=latest_version.status if latest_version else "idle",
            last_error=last_error,
        )

    def get_project_state(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        documents = self.list_project_documents(project_id, owner_id=owner_id)
        first_scope = documents[0].scope if documents else ConversationScope(
            database_id=0,
            dataset_ids=[],
        )
        last_error = next(
            (document.error for document in documents if document.status == "error"),
            None,
        )
        return SemanticLayerState(
            project_id=project_id,
            database_id=first_scope.database_id,
            catalog_name=first_scope.catalog_name,
            schema_name=first_scope.schema_name,
            dataset_ids=first_scope.dataset_ids,
            document_count=len(documents),
            approved_document_count=len(
                [
                    document
                    for document in documents
                    if document.status in {"approved", "indexed"}
                ]
            ),
            indexed_document_count=len(
                [document for document in documents if document.status == "indexed"]
            ),
            semantic_layer_version=None,
            indexing_status="idle",
            last_error=last_error,
        )

    def append_event(
        self,
        event: SemanticLayerEvent,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        self._events.append((owner_id, event.model_copy(deep=True)))

    def list_events(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticLayerEvent]:
        return [
            event.model_copy(deep=True)
            for stored_owner_id, event in self._events
            if stored_owner_id == owner_id and scope_matches(event.scope, scope)
        ]

    def list_project_events(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticLayerEvent]:
        return [
            event.model_copy(deep=True)
            for stored_owner_id, event in self._events
            if stored_owner_id == owner_id and event.project_id == project_id
        ]
