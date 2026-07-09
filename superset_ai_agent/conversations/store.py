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

from typing import List, Protocol

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
    ConversationSummary,
    ConversationTruncation,
)

DEFAULT_OWNER_ID = "local"


class ConversationNotFoundError(KeyError):
    """Raised when a conversation cannot be found for the owner."""


class ConversationArtifactNotFoundError(KeyError):
    """Raised when an artifact cannot be found in a conversation."""


class ConversationMessageNotFoundError(KeyError):
    """Raised when a message cannot be found (live) in a conversation."""


class ConversationRewriteError(ValueError):
    """Raised when a rewrite/undo request is structurally invalid.

    E.g. the truncation anchor is not a user message, or an undo targets a
    message that never anchored a rewrite.
    """


class ConversationStore(Protocol):
    """Storage contract for standalone agent conversations."""

    def create(
        self,
        scope: ConversationScope,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        kind: str = "sql",
        project_id: str | None = None,
    ) -> Conversation:
        """Create a conversation.

        ``kind``/``project_id`` tag the owning agent (``"sql"`` vs ``"copilot"``)
        and bind project-scoped threads; both default so existing AI SQL callers
        are unchanged.
        """

    def list(
        self,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        kind: str | None = None,
        project_id: str | None = None,
    ) -> list[ConversationSummary]:
        """List conversations for an owner, optionally filtered by agent/project.

        ``kind=None`` (the default) lists every thread the owner has; pass
        ``kind="copilot"`` + ``project_id`` to list one agent's project threads.
        """

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

    def update_title(
        self,
        conversation_id: str,
        title: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Rename a conversation."""

    def update_project_id(
        self,
        conversation_id: str,
        project_id: str | None,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Pin the semantic-layer project that grounds this conversation.

        Used by the AI SQL agent to record the project it resolved on the first
        grounded turn so later turns reuse it deterministically instead of
        re-racing the most-recently-updated match.
        """

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

    def truncate_from(
        self,
        conversation_id: str,
        message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> ConversationTruncation:
        """Soft-delete a user message and everything after it (rewrite cut).

        The anchor must be a live *user* message (``ConversationRewriteError``
        otherwise). Every removed row is marked with the anchor's id so
        :meth:`undo_truncate` can restore the batch and the attempt pager can
        find superseded assistant turns. Returns the truncated thread plus the
        removed messages (whose provenance the caller may need to clean up).
        """

    def undo_truncate(
        self,
        conversation_id: str,
        anchor_message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Restore the batch removed by ``truncate_from(anchor_message_id)``.

        Messages appended after the truncation (the rewritten turn) are
        soft-deleted in the same marker discipline. Single-step: intended to be
        offered only until the next turn starts.
        """

    def fork(
        self,
        conversation_id: str,
        message_id: str | None = None,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> Conversation:
        """Copy live messages up to (and including) the anchor into a new thread.

        Non-destructive "Branch from here": the source thread is untouched; the
        fork records ``parent_conversation_id``/``forked_from_sequence`` and
        copies scope + project binding. Copied changeset artifacts are marked
        ``inert`` so a fork can never re-apply the parent's proposals.
        ``message_id=None`` forks the whole thread.
        """

    def list_attempts(
        self,
        conversation_id: str,
        anchor_message_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        # ``List`` (not ``list``): the protocol's own ``list`` method shadows
        # the builtin inside this class body.
    ) -> List[ConversationMessage]:
        """Superseded assistant attempts for a rewrite anchor (pager history)."""
