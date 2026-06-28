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
from typing import cast
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.persistence.models import (
    AiAgentDocumentChunk,
    AiAgentEvent,
    AiAgentSemanticDocument,
)
from superset_ai_agent.semantic_layer.document_chunks import chunk_id, DocumentChunk
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticDocumentStatus,
    SemanticLayerEvent,
    SemanticLayerState,
)
from superset_ai_agent.semantic_layer.store import (
    scope_matches,
    SemanticDocumentNotFoundError,
)


class SqlAlchemySemanticLayerStore:
    """SQLAlchemy-backed semantic-layer store."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def save_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        with self.session_factory() as session:
            session.add(_document_to_model(document, owner_id=owner_id))
            session.commit()
        return self.get_document(document.id, owner_id=owner_id)

    def list_documents(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticDocument]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentSemanticDocument)
                    .where(
                        AiAgentSemanticDocument.owner_id == owner_id,
                        AiAgentSemanticDocument.database_id == scope.database_id,
                        AiAgentSemanticDocument.catalog_name == scope.catalog_name,
                        AiAgentSemanticDocument.schema_name == scope.schema_name,
                    )
                    .order_by(AiAgentSemanticDocument.created_at.desc())
                )
                .scalars()
                .all()
            )
            documents = [_document_from_model(model) for model in models]
            return [
                document
                for document in documents
                if scope_matches(document.scope, scope)
            ]

    def list_project_documents(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticDocument]:
        # Project-scoped (F5/§5.7): every DB-authorized user sees the project's full
        # BI-doc set, not only their own uploads. ``project_id`` is DB-bound, so this
        # never widens beyond the database boundary. ``owner_id`` kept for signature
        # compat (the write paths still stamp it); not a read filter.
        del owner_id
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentSemanticDocument)
                    .where(AiAgentSemanticDocument.project_id == project_id)
                    .order_by(AiAgentSemanticDocument.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_document_from_model(model) for model in models]

    def duplicate_documents(
        self,
        source_project_id: str,
        target_project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        """Copy a project's documents + chunks into another project (DP6 include-docs).

        Reads are project-scoped (every uploader's docs come along, matching the
        shared-project model); each document is re-parented under a fresh id and its
        chunks re-derived with deterministic ids for the new document. Vectors are
        NOT touched here — they are keyed by project scope, so the caller re-embeds
        the returned chunks under the target's scope key in one pass.
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

    def find_document_by_checksum(
        self,
        project_id: str,
        checksum: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument | None:
        # Project-scoped dedup (DP11): identical bytes from any user dedup to the one
        # project document; the first uploader's row (its ``created_by``) is reused.
        del owner_id
        with self.session_factory() as session:
            model = (
                session.execute(
                    select(AiAgentSemanticDocument)
                    .where(
                        AiAgentSemanticDocument.project_id == project_id,
                        AiAgentSemanticDocument.checksum == checksum,
                    )
                    .order_by(AiAgentSemanticDocument.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            return _document_from_model(model) if model is not None else None

    def get_document(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        with self.session_factory() as session:
            model = self._get_document_model(
                session,
                document_id,
                owner_id=owner_id,
            )
            return _document_from_model(model)

    def update_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        with self.session_factory() as session:
            model = self._get_document_model(
                session,
                document.id,
                owner_id=owner_id,
            )
            model.project_id = document.project_id
            model.database_id = document.scope.database_id
            model.catalog_name = document.scope.catalog_name
            model.schema_name = document.scope.schema_name
            model.dataset_ids = document.scope.dataset_ids
            model.filename = document.filename
            model.content_type = document.content_type
            model.size_bytes = document.size_bytes
            model.checksum = document.checksum
            model.storage_uri = document.storage_uri
            model.status = document.status
            model.summary = document.summary
            model.extracted_text = document.extracted_text
            model.extracted_text_preview = document.extracted_text_preview
            model.warnings = document.warnings
            model.error = document.error
            model.updated_at = _utc_now()
            session.commit()
        return self.get_document(document.id, owner_id=owner_id)

    def delete_document(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        with self.session_factory() as session:
            model = self._get_document_model(
                session,
                document_id,
                owner_id=owner_id,
            )
            # Cascade-in-code: drop the document's chunks in the same transaction
            # so a deleted document never leaves orphan chunk rows.
            session.execute(
                delete(AiAgentDocumentChunk).where(
                    AiAgentDocumentChunk.document_id == document_id,
                    AiAgentDocumentChunk.owner_id == owner_id,
                )
            )
            session.delete(model)
            session.commit()

    def save_chunks(
        self,
        document_id: str,
        chunks: list[DocumentChunk],
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        project_id: str | None = None,
    ) -> list[DocumentChunk]:
        with self.session_factory() as session:
            # Replace-on-write: a reindex regenerates the full chunk set, so clear
            # the prior rows first (idempotent per document).
            session.execute(
                delete(AiAgentDocumentChunk).where(
                    AiAgentDocumentChunk.document_id == document_id,
                    AiAgentDocumentChunk.owner_id == owner_id,
                )
            )
            for chunk in chunks:
                session.add(
                    _chunk_to_model(
                        chunk,
                        owner_id=owner_id,
                        project_id=project_id,
                    )
                )
            session.commit()
        return self.list_chunks(document_id, owner_id=owner_id)

    def list_chunks(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentDocumentChunk)
                    .where(
                        AiAgentDocumentChunk.owner_id == owner_id,
                        AiAgentDocumentChunk.document_id == document_id,
                    )
                    .order_by(AiAgentDocumentChunk.chunk_index.asc())
                )
                .scalars()
                .all()
            )
            return [_chunk_from_model(model) for model in models]

    def delete_chunks(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        with self.session_factory() as session:
            session.execute(
                delete(AiAgentDocumentChunk).where(
                    AiAgentDocumentChunk.document_id == document_id,
                    AiAgentDocumentChunk.owner_id == owner_id,
                )
            )
            session.commit()

    def list_project_chunks(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        # Project-scoped RAG corpus (§5.7.1): the Copilot retrieves over every
        # project chunk regardless of uploader. Vectors are already keyed
        # ``doc:{project_id}``, so this aligns the candidate set to the vector scope.
        del owner_id
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentDocumentChunk)
                    .where(AiAgentDocumentChunk.project_id == project_id)
                    .order_by(
                        AiAgentDocumentChunk.document_id.asc(),
                        AiAgentDocumentChunk.chunk_index.asc(),
                    )
                )
                .scalars()
                .all()
            )
            return [_chunk_from_model(model) for model in models]

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
            project_id=documents[0].project_id if documents else None,
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
            else ConversationScope(database_id=0, dataset_ids=[])
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
        with self.session_factory() as session:
            session.add(
                AiAgentEvent(
                    id=event.id,
                    project_id=event.project_id,
                    owner_id=owner_id,
                    scope=event.scope.model_dump(mode="json"),
                    type=event.type,
                    payload=event.model_dump(mode="json"),
                    created_at=event.created_at,
                )
            )
            session.commit()

    def list_events(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticLayerEvent]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentEvent)
                    .where(AiAgentEvent.owner_id == owner_id)
                    .order_by(AiAgentEvent.created_at.asc())
                )
                .scalars()
                .all()
            )
            events = [
                SemanticLayerEvent.model_validate(model.payload) for model in models
            ]
            return [event for event in events if scope_matches(event.scope, scope)]

    def list_project_events(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticLayerEvent]:
        # Project-scoped provenance (§5.6.1): the timeline is the project's whole
        # history; every DB-authorized user sees the same entries (each event keeps
        # its own ``owner_id`` actor for attribution). ``project_id`` is DB-bound.
        del owner_id
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentEvent)
                    .where(AiAgentEvent.project_id == project_id)
                    .order_by(AiAgentEvent.created_at.asc())
                )
                .scalars()
                .all()
            )
            return [
                SemanticLayerEvent.model_validate(model.payload) for model in models
            ]

    def delete_project_events(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        types: frozenset[str] | None = None,
    ) -> int:
        # Reset clears the project's provenance for all actors (project-scoped).
        del owner_id
        with self.session_factory() as session:
            statement = delete(AiAgentEvent).where(
                AiAgentEvent.project_id == project_id,
            )
            if types is not None:
                statement = statement.where(AiAgentEvent.type.in_(types))
            result = session.execute(statement)
            session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _get_document_model(
        session: Session,
        document_id: str,
        *,
        owner_id: str,
    ) -> AiAgentSemanticDocument:
        model = session.get(AiAgentSemanticDocument, document_id)
        if model is None or model.owner_id != owner_id:
            raise SemanticDocumentNotFoundError(document_id)
        return model


def _document_to_model(
    document: SemanticDocument,
    *,
    owner_id: str,
) -> AiAgentSemanticDocument:
    return AiAgentSemanticDocument(
        id=document.id,
        project_id=document.project_id,
        owner_id=owner_id,
        database_id=document.scope.database_id,
        catalog_name=document.scope.catalog_name,
        schema_name=document.scope.schema_name,
        dataset_ids=document.scope.dataset_ids,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        checksum=document.checksum,
        storage_uri=document.storage_uri,
        status=document.status,
        summary=document.summary,
        extracted_text=document.extracted_text,
        extracted_text_preview=document.extracted_text_preview,
        warnings=document.warnings,
        error=document.error,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _document_from_model(
    model: AiAgentSemanticDocument,
) -> SemanticDocument:
    return SemanticDocument(
        id=model.id,
        project_id=model.project_id,
        filename=model.filename,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        status=cast(SemanticDocumentStatus, model.status),
        scope=ConversationScope(
            database_id=model.database_id,
            catalog_name=model.catalog_name,
            schema_name=model.schema_name,
            dataset_ids=model.dataset_ids,
        ),
        checksum=model.checksum,
        storage_uri=model.storage_uri,
        summary=model.summary,
        extracted_text=model.extracted_text,
        extracted_text_preview=model.extracted_text_preview,
        warnings=model.warnings,
        error=model.error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _chunk_to_model(
    chunk: DocumentChunk,
    *,
    owner_id: str,
    project_id: str | None,
) -> AiAgentDocumentChunk:
    return AiAgentDocumentChunk(
        id=chunk.id,
        document_id=chunk.document_id,
        owner_id=owner_id,
        project_id=project_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        checksum=chunk.checksum,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        embedded=chunk.embedded,
        created_at=_utc_now(),
    )


def _chunk_from_model(model: AiAgentDocumentChunk) -> DocumentChunk:
    return DocumentChunk(
        id=model.id,
        document_id=model.document_id,
        chunk_index=model.chunk_index,
        text=model.text,
        checksum=model.checksum,
        char_start=model.char_start,
        char_end=model.char_end,
        embedded=model.embedded,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
