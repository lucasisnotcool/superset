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

"""Rewrite preview: the side-effect manifest behind edit & resend / regenerate.

Computes, read-only, what truncating a thread from an anchor message would
remove and which durable side effects would stay behind — the data the confirm
dialog renders (name exactly what is and isn't undoable; Claude Code's
limitations-first pattern). See plan_conversation_management_spec.md §3.0.
"""

from __future__ import annotations

import re
from typing import Protocol

from superset_ai_agent.conversations.schemas import (
    AppliedChangesetItemPreview,
    Conversation,
    ConversationMessage,
    RewritePreview,
)
from superset_ai_agent.conversations.store import ConversationMessageNotFoundError
from superset_ai_agent.semantic_layer.apply_snapshots import ApplySnapshot

#: The assistant turn the apply route commits. Legacy applies (before apply
#: snapshots existed) are only detectable through this marker.
_APPLIED_TURN_PATTERN = re.compile(r"^Applied \d+ drafts?\.$")


class _MemoryCounter(Protocol):
    def count_by_source(self, source_message_ids: list[str]) -> int: ...


class _SnapshotReader(Protocol):
    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        include_reverted: bool = False,
    ) -> list[ApplySnapshot]: ...


def cut_range_messages(
    conversation: Conversation,
    from_message_id: str,
) -> list[ConversationMessage]:
    """The live messages a rewrite anchored at ``from_message_id`` removes."""

    cut_index = next(
        (
            index
            for index, message in enumerate(conversation.messages)
            if message.id == from_message_id
        ),
        None,
    )
    if cut_index is None:
        raise ConversationMessageNotFoundError(from_message_id)
    return conversation.messages[cut_index:]


def build_rewrite_preview(
    conversation: Conversation,
    from_message_id: str,
    *,
    memory: _MemoryCounter | None = None,
    snapshots: _SnapshotReader | None = None,
) -> RewritePreview:
    """Assemble the side-effect manifest for a prospective rewrite."""

    removed = cut_range_messages(conversation, from_message_id)
    removed_ids = {message.id for message in removed}
    user_message_ids = [message.id for message in removed if message.role == "user"]

    memory_write_count = (
        memory.count_by_source(user_message_ids) if memory is not None else 0
    )

    executed_sql_count = sum(
        1
        for message in removed
        for artifact in message.artifacts
        if artifact.type == "sql" and artifact.execution_result is not None
    )

    applied_items: list[AppliedChangesetItemPreview] = []
    apply_group_ids: list[str] = []
    snapshot_message_ids: set[str] = set()
    if snapshots is not None:
        for snapshot in snapshots.list_for_conversation(conversation.id):
            if snapshot.message_id not in removed_ids:
                continue
            snapshot_message_ids.add(snapshot.message_id or "")
            applied_items.append(
                AppliedChangesetItemPreview(
                    op=snapshot.op,
                    path=snapshot.path,
                    file_id=snapshot.file_id,
                )
            )
            if snapshot.apply_group_id not in apply_group_ids:
                apply_group_ids.append(snapshot.apply_group_id)

    # An "Applied N drafts." turn with no snapshot rows means something was
    # applied before snapshots existed (or snapshots are disabled): the items
    # are unknown, and the dialog must say so rather than pretend it's clean.
    unknown_applies = any(
        message.role == "assistant"
        and _APPLIED_TURN_PATTERN.match(message.content.strip())
        and message.id not in snapshot_message_ids
        for message in removed
    )

    return RewritePreview(
        removed_message_count=len(removed),
        memory_write_count=memory_write_count,
        applied_changeset_items=applied_items,
        apply_group_ids=apply_group_ids,
        unknown_applies=unknown_applies,
        executed_sql_count=executed_sql_count,
    )
