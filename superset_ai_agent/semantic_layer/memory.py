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
from superset_ai_agent.semantic_layer.document_chunks import DocumentChunk
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
    SemanticLayerState,
)
from superset_ai_agent.semantic_layer.store import (
    scope_matches,
    SemanticDocumentNotFoundError,
)


class InMemorySemanticLayerStore:
    """Process-local semantic-layer store for development and tests."""

    def __init__(self) -> None:
        self._documents: dict[str, tuple[str, SemanticDocument]] = {}
        self._events: list[tuple[str, SemanticLayerEvent]] = []
        # owner_id -> document_id -> ordered chunks
        self._chunks: dict[str, dict[str, list[DocumentChunk]]] = {}
        # document_id -> project_id (for cross-document project scans)
        self._chunk_projects: dict[str, str | None] = {}

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

    def delete_document(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        self.get_document(document_id, owner_id=owner_id)
        self._documents.pop(document_id, None)
        self._chunks.get(owner_id, {}).pop(document_id, None)
        self._chunk_projects.pop(document_id, None)

    def save_chunks(
        self,
        document_id: str,
        chunks: list[DocumentChunk],
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        project_id: str | None = None,
    ) -> list[DocumentChunk]:
        owned = self._chunks.setdefault(owner_id, {})
        owned[document_id] = [chunk.model_copy(deep=True) for chunk in chunks]
        self._chunk_projects[document_id] = project_id
        return self.list_chunks(document_id, owner_id=owner_id)

    def list_chunks(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        chunks = self._chunks.get(owner_id, {}).get(document_id, [])
        return [
            chunk.model_copy(deep=True)
            for chunk in sorted(chunks, key=lambda chunk: chunk.chunk_index)
        ]

    def delete_chunks(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        self._chunks.get(owner_id, {}).pop(document_id, None)

    def list_project_chunks(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        result: list[DocumentChunk] = []
        for document_id, chunks in self._chunks.get(owner_id, {}).items():
            if self._chunk_projects.get(document_id) != project_id:
                continue
            result.extend(chunk.model_copy(deep=True) for chunk in chunks)
        return sorted(result, key=lambda chunk: (chunk.document_id, chunk.chunk_index))

    def get_state(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        documents = self.list_documents(scope, owner_id=owner_id)
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
            last_error=last_error,
        )

    def get_project_state(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        documents = self.list_project_documents(project_id, owner_id=owner_id)
        first_scope = (
            documents[0].scope
            if documents
            else ConversationScope(
                database_id=0,
                dataset_ids=[],
            )
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
