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

import hashlib
import json  # noqa: TID251 - standalone agent: stable scope-hash serialization
from typing import Protocol

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.document_chunks import DocumentChunk
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
    SemanticLayerState,
)


class SemanticDocumentNotFoundError(KeyError):
    """Raised when a semantic-layer document cannot be found for the owner."""


class SemanticLayerStore(Protocol):
    """Storage contract for document-driven semantic-layer state."""

    def save_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        """Persist a new semantic-layer document."""

    def list_documents(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticDocument]:
        """List documents for a scope."""

    def list_project_documents(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticDocument]:
        """List documents for a semantic project."""

    def get_document(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        """Return one semantic-layer document."""

    def update_document(
        self,
        document: SemanticDocument,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticDocument:
        """Update an existing semantic-layer document."""

    def delete_document(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        """Delete a document and its chunk rows (cascade-in-code).

        Removes the chunk rows owned by the document in the same transaction so a
        deleted document never leaves orphan chunks. Vector-store eviction and blob
        removal are orchestrated by the caller (the cascade helper).
        """

    def save_chunks(
        self,
        document_id: str,
        chunks: list[DocumentChunk],
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        project_id: str | None = None,
    ) -> list[DocumentChunk]:
        """Replace the persisted chunk set for a document (idempotent reindex)."""

    def list_chunks(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        """List a document's chunks in document order."""

    def delete_chunks(
        self,
        document_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        """Delete a document's chunk rows (used by reindex; never the document)."""

    def list_project_chunks(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[DocumentChunk]:
        """List every chunk in a project (cross-document duplicate scans)."""

    def get_state(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        """Return aggregate semantic-layer state for a scope."""

    def get_project_state(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        """Return aggregate semantic-layer state for a semantic project."""

    def append_event(
        self,
        event: SemanticLayerEvent,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        """Persist a semantic-layer event."""

    def list_events(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticLayerEvent]:
        """List semantic-layer events for a scope."""

    def list_project_events(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticLayerEvent]:
        """List semantic-layer events for a semantic project."""

    def delete_project_events(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        types: frozenset[str] | None = None,
    ) -> int:
        """Delete a project's events (optionally only the given ``types``).

        Returns the number deleted. Used by MDL reset to purge the provenance log
        while leaving document events (which outlive a reset) intact.
        """


def scope_hash(scope: ConversationScope) -> str:
    """Return a stable hash for a Superset semantic-layer scope."""

    payload = {
        "database_id": scope.database_id,
        "catalog_name": scope.catalog_name,
        "schema_name": scope.schema_name,
        "dataset_ids": sorted(scope.dataset_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def instruction_scope_hash(scope: ConversationScope) -> str:
    """Scope hash for **instructions** — schema-level (dataset selection ignored).

    Instructions are operator guidance for a database/catalog/schema, not for a
    transient per-query dataset selection. The authoring UI scopes them at the schema
    level (``dataset_ids=[]``), but a chat query carries the user's selected
    ``dataset_ids``; hashing those in would give the two a different ``scope_hash`` and
    silently hide every authored instruction whenever a query has datasets selected.
    (``is_global`` does not rescue it — recall filters by ``scope_hash`` *before* the
    global split.) Excluding ``dataset_ids`` makes authoring and recall agree.

    Distinct from :func:`scope_hash` (used for NL→SQL memory, which is legitimately
    dataset-scoped) — do not unify the two.
    """

    if scope.dataset_ids:
        scope = scope.model_copy(update={"dataset_ids": []})
    return scope_hash(scope)


def scope_matches(left: ConversationScope, right: ConversationScope) -> bool:
    """Return whether two scopes identify the same semantic context."""

    return (
        left.database_id == right.database_id
        and left.catalog_name == right.catalog_name
        and left.schema_name == right.schema_name
        and sorted(left.dataset_ids) == sorted(right.dataset_ids)
    )
