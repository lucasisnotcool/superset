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
import json
from typing import Protocol

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
    SemanticLayerState,
    SemanticLayerVersion,
    SemanticUpdate,
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

    def save_updates(
        self,
        document_id: str,
        updates: list[SemanticUpdate],
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticUpdate]:
        """Persist proposed or reviewed semantic updates."""

    def list_approved_updates(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[SemanticUpdate]:
        """Return reviewed, approved updates for a scope."""

    def save_version(
        self,
        version: SemanticLayerVersion,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerVersion:
        """Persist a reviewed semantic overlay version."""

    def get_latest_version(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerVersion | None:
        """Return the latest indexed semantic overlay for a scope."""

    def get_state(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> SemanticLayerState:
        """Return aggregate semantic-layer state for a scope."""

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


def scope_hash(scope: ConversationScope) -> str:
    """Return a stable hash for a Superset semantic-layer scope."""

    payload = {
        "database_id": scope.database_id,
        "schema_name": scope.schema_name,
        "dataset_ids": sorted(scope.dataset_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def scope_matches(left: ConversationScope, right: ConversationScope) -> bool:
    """Return whether two scopes identify the same semantic context."""

    return (
        left.database_id == right.database_id
        and left.schema_name == right.schema_name
        and sorted(left.dataset_ids) == sorted(right.dataset_ids)
    )
