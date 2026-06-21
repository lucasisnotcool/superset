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
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.persistence.models import (
    AiAgentEvent,
    AiAgentSemanticDocument,
    AiAgentSemanticLayerVersion,
    AiAgentSemanticUpdate,
)
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.schemas import (
    IndexingStatus,
    SemanticDocument,
    SemanticDocumentStatus,
    SemanticLayerEvent,
    SemanticLayerState,
    SemanticLayerVersion,
    SemanticUpdate,
    SemanticUpdateKind,
)
from superset_ai_agent.semantic_layer.store import (
    SemanticDocumentNotFoundError,
    scope_hash,
    scope_matches,
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
                        AiAgentSemanticDocument.schema_name == scope.schema_name,
                    )
                    .order_by(AiAgentSemanticDocument.created_at.desc())
                )
                .scalars()
                .all()
            )
            documents = [
                _document_from_model(
                    model, updates=_updates_for_document(session, model.id)
                )
                for model in models
            ]
            return [
                document
                for document in documents
                if scope_matches(document.scope, scope)
            ]

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
            return _document_from_model(
                model,
                updates=_updates_for_document(session, model.id),
            )

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
            model.database_id = document.scope.database_id
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

    def save_updates(
        self,
        document_id: str,
        updates: list[SemanticUpdate],
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticUpdate]:
        with self.session_factory() as session:
            self._get_document_model(session, document_id, owner_id=owner_id)
            for update in updates:
                existing = session.get(AiAgentSemanticUpdate, update.id)
                if existing is None:
                    session.add(_update_to_model(update, owner_id=owner_id))
                elif (
                    existing.owner_id == owner_id
                    and existing.document_id == document_id
                ):
                    _apply_update_to_model(existing, update)
            session.commit()
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
                update
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
        with self.session_factory() as session:
            session.add(
                AiAgentSemanticLayerVersion(
                    id=version.id,
                    owner_id=owner_id,
                    database_id=version.scope.database_id,
                    schema_name=version.scope.schema_name,
                    dataset_ids=version.scope.dataset_ids,
                    scope_hash=version.scope_hash,
                    version=version.version,
                    status=version.status,
                    mdl=version.mdl,
                    wren_context=(
                        version.wren_context.model_dump(mode="json")
                        if version.wren_context is not None
                        else None
                    ),
                    source_update_ids=version.source_update_ids,
                    published_semantic_layer_uuid=(
                        version.published_semantic_layer_uuid
                    ),
                    created_at=version.created_at,
                )
            )
            session.commit()
        return version.model_copy(deep=True)

    def get_latest_version(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerVersion | None:
        target_hash = scope_hash(scope)
        with self.session_factory() as session:
            model = (
                session.execute(
                    select(AiAgentSemanticLayerVersion)
                    .where(
                        AiAgentSemanticLayerVersion.owner_id == owner_id,
                        AiAgentSemanticLayerVersion.scope_hash == target_hash,
                    )
                    .order_by(AiAgentSemanticLayerVersion.created_at.desc())
                    .limit(1)
                )
                .scalars()
                .one_or_none()
            )
            if model is None:
                return None
            return _version_from_model(model)

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
            database_id=scope.database_id,
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
        owner_id=owner_id,
        database_id=document.scope.database_id,
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
    *,
    updates: list[SemanticUpdate],
) -> SemanticDocument:
    return SemanticDocument(
        id=model.id,
        filename=model.filename,
        content_type=model.content_type,
        size_bytes=model.size_bytes,
        status=cast(SemanticDocumentStatus, model.status),
        scope=ConversationScope(
            database_id=model.database_id,
            schema_name=model.schema_name,
            dataset_ids=model.dataset_ids,
        ),
        checksum=model.checksum,
        storage_uri=model.storage_uri,
        summary=model.summary,
        extracted_text=model.extracted_text,
        extracted_text_preview=model.extracted_text_preview,
        proposed_updates=updates,
        warnings=model.warnings,
        error=model.error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _updates_for_document(session: Session, document_id: str) -> list[SemanticUpdate]:
    models = (
        session.execute(
            select(AiAgentSemanticUpdate)
            .where(AiAgentSemanticUpdate.document_id == document_id)
            .order_by(AiAgentSemanticUpdate.created_at.asc())
        )
        .scalars()
        .all()
    )
    return [_update_from_model(model) for model in models]


def _update_to_model(
    update: SemanticUpdate,
    *,
    owner_id: str,
) -> AiAgentSemanticUpdate:
    return AiAgentSemanticUpdate(
        id=update.id,
        document_id=update.source_document_id,
        owner_id=owner_id,
        kind=update.kind,
        target=update.target,
        value=update.value,
        confidence=update.confidence,
        reviewed=update.reviewed,
        approved=update.approved,
        reviewer_id=update.reviewer_id,
        review_notes=update.review_notes,
        created_at=update.created_at,
        updated_at=update.updated_at,
        reviewed_at=update.reviewed_at,
    )


def _apply_update_to_model(
    model: AiAgentSemanticUpdate,
    update: SemanticUpdate,
) -> None:
    model.kind = update.kind
    model.target = update.target
    model.value = update.value
    model.confidence = update.confidence
    model.reviewed = update.reviewed
    model.approved = update.approved
    model.reviewer_id = update.reviewer_id
    model.review_notes = update.review_notes
    model.updated_at = update.updated_at
    model.reviewed_at = update.reviewed_at


def _update_from_model(model: AiAgentSemanticUpdate) -> SemanticUpdate:
    return SemanticUpdate(
        id=model.id,
        kind=cast(SemanticUpdateKind, model.kind),
        target=model.target,
        value=model.value,
        confidence=model.confidence,
        source_document_id=model.document_id,
        reviewed=model.reviewed,
        approved=model.approved,
        reviewer_id=model.reviewer_id,
        review_notes=model.review_notes,
        created_at=model.created_at,
        updated_at=model.updated_at,
        reviewed_at=model.reviewed_at,
    )


def _version_from_model(model: AiAgentSemanticLayerVersion) -> SemanticLayerVersion:
    return SemanticLayerVersion(
        id=model.id,
        scope=ConversationScope(
            database_id=model.database_id,
            schema_name=model.schema_name,
            dataset_ids=model.dataset_ids,
        ),
        scope_hash=model.scope_hash,
        version=model.version,
        status=cast(IndexingStatus, model.status),
        mdl=model.mdl,
        wren_context=(
            WrenContextArtifact.model_validate(model.wren_context)
            if model.wren_context is not None
            else None
        ),
        source_update_ids=model.source_update_ids,
        published_semantic_layer_uuid=model.published_semantic_layer_uuid,
        created_at=model.created_at,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
