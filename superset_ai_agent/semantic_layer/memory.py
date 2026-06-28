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

from uuid import uuid4

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.document_chunks import chunk_id, DocumentChunk
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
        # Project-scoped (F5/§5.7) — parity with the SQLAlchemy store: every
        # DB-authorized user sees the project's full doc set, not only their own.
        del owner_id
        return [
            document.model_copy(deep=True)
            for _stored_owner_id, document in self._documents.values()
            if document.project_id == project_id
        ]

    def find_document_by_checksum(
        self,
        project_id: str,
        checksum: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument | None:
        # Project-scoped dedup (DP11) — identical bytes from any user dedup.
        del owner_id
        matches = [
            document
            for _stored_owner_id, document in self._documents.values()
            if document.project_id == project_id and document.checksum == checksum
        ]
        if not matches:
            return None
        newest = max(matches, key=lambda document: document.created_at)
        return newest.model_copy(deep=True)

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
        # Project-scoped RAG corpus (§5.7.1) — across all uploaders, parity with
        # the SQLAlchemy store.
        del owner_id
        result: list[DocumentChunk] = []
        for owner_chunks in self._chunks.values():
            for document_id, chunks in owner_chunks.items():
                if self._chunk_projects.get(document_id) != project_id:
                    continue
                result.extend(chunk.model_copy(deep=True) for chunk in chunks)
        return sorted(result, key=lambda chunk: (chunk.document_id, chunk.chunk_index))

    def duplicate_documents(
        self,
        source_project_id: str,
        target_project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        """Copy a project's documents + chunks into another project (DP6).

        Parity with the SQLAlchemy store: project-scoped reads (every uploader's
        docs), each document re-parented under a fresh id with deterministic new
        chunk ids; returns the new chunks for the caller to re-embed.
        """

        chunks_by_doc: dict[str, list[DocumentChunk]] = {}
        for chunk in self.list_project_chunks(source_project_id):
            chunks_by_doc.setdefault(chunk.document_id, []).append(chunk)
        new_chunks: list[DocumentChunk] = []
        for document in self.list_project_documents(source_project_id):
            new_document = document.model_copy(
                update={
                    "id": str(uuid4()),
                    "project_id": target_project_id,
                    "deduplicated": False,
                }
            )
            self.save_document(new_document, owner_id=owner_id)
            rebuilt = [
                chunk.model_copy(
                    update={
                        "id": chunk_id(new_document.id, chunk.chunk_index),
                        "document_id": new_document.id,
                    }
                )
                for chunk in sorted(
                    chunks_by_doc.get(document.id, []),
                    key=lambda c: c.chunk_index,
                )
            ]
            if rebuilt:
                self.save_chunks(
                    new_document.id,
                    rebuilt,
                    owner_id=owner_id,
                    project_id=target_project_id,
                )
            new_chunks.extend(rebuilt)
        return new_chunks

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
        # Project-scoped provenance (§5.6.1) — across all actors, parity with the
        # SQLAlchemy store.
        del owner_id
        return [
            event.model_copy(deep=True)
            for _stored_owner_id, event in self._events
            if event.project_id == project_id
        ]

    def delete_project_events(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        types: frozenset[str] | None = None,
    ) -> int:
        del owner_id
        kept: list[tuple[str, SemanticLayerEvent]] = []
        deleted = 0
        for stored_owner_id, event in self._events:
            matches = event.project_id == project_id and (
                types is None or event.type in types
            )
            if matches:
                deleted += 1
            else:
                kept.append((stored_owner_id, event))
        self._events = kept
        return deleted
