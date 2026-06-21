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

from typing import Protocol

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
    ConversationSummary,
)

DEFAULT_OWNER_ID = "local"


class ConversationNotFoundError(KeyError):
    """Raised when a conversation cannot be found for the owner."""


class ConversationArtifactNotFoundError(KeyError):
    """Raised when an artifact cannot be found in a conversation."""


class ConversationStore(Protocol):
    """Storage contract for standalone agent conversations."""

    def create(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Create a conversation."""

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[ConversationSummary]:
        """List conversations for an owner."""

    def get(
        self,
        conversation_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Return a conversation."""

    def update_scope(
        self,
        conversation_id: str,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Update the active Superset scope for a conversation."""

    def append(
        self,
        conversation_id: str,
        message: ConversationMessage,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Append a message to a conversation."""

    def replace_artifact(
        self,
        conversation_id: str,
        artifact_id: str,
        artifact: ConversationArtifact,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Replace one artifact in a conversation."""

    def delete(
        self,
        conversation_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        """Delete a conversation."""
