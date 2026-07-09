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

"""Apply-time before-images for Copilot changeset applies.

The Copilot's analog of an IDE agent's file checkpoint (VS Code / Cursor /
Claude Code pattern): when a changeset item is applied (persisted as a draft),
record what the touched MDL file looked like *before* the apply. This powers

- the rewrite preview ("editing this message leaves these applied drafts
  behind"), attributed to a thread turn via ``message_id``; and
- "revert applied drafts" (Phase 2), which restores the before-images.

See plan_conversation_management_spec.md §3.0/§3.4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.persistence.models import AiAgentMdlApplySnapshot


class ApplySnapshotEntry(BaseModel):
    """Before-image of one file for one applied changeset item."""

    op: str
    path: str
    file_id: str | None = None
    before_content: str | None = None
    before_status: str | None = None


class ApplySnapshot(ApplySnapshotEntry):
    """A persisted before-image row."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    apply_group_id: str
    project_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    applied_at: datetime
    reverted_at: datetime | None = None


class ApplyGroupSummary(BaseModel):
    """One apply action's snapshots, grouped for the thread UI."""

    apply_group_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    applied_at: datetime
    reverted: bool = False
    items: list[ApplySnapshotEntry] = Field(default_factory=list)


def group_snapshots(snapshots: list[ApplySnapshot]) -> list[ApplyGroupSummary]:
    """Fold snapshot rows into per-apply groups (ordered by applied_at)."""

    groups: dict[str, ApplyGroupSummary] = {}
    for snapshot in snapshots:
        group = groups.get(snapshot.apply_group_id)
        if group is None:
            group = ApplyGroupSummary(
                apply_group_id=snapshot.apply_group_id,
                conversation_id=snapshot.conversation_id,
                message_id=snapshot.message_id,
                applied_at=snapshot.applied_at,
                reverted=True,
            )
            groups[snapshot.apply_group_id] = group
        group.items.append(
            ApplySnapshotEntry(
                op=snapshot.op,
                path=snapshot.path,
                file_id=snapshot.file_id,
            )
        )
        # A group counts as reverted only when every row is consumed.
        if snapshot.reverted_at is None:
            group.reverted = False
    return sorted(groups.values(), key=lambda group: group.applied_at)


class ApplySnapshotStore:
    """SQLAlchemy-backed store for apply before-images."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def record(
        self,
        *,
        project_id: str,
        owner_id: str,
        entries: list[ApplySnapshotEntry],
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> str:
        """Persist one apply action's before-images; returns the group id."""

        group_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            for entry in entries:
                session.add(
                    AiAgentMdlApplySnapshot(
                        id=str(uuid4()),
                        apply_group_id=group_id,
                        project_id=project_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        owner_id=owner_id,
                        op=entry.op,
                        path=entry.path,
                        file_id=entry.file_id,
                        before_content=entry.before_content,
                        before_status=entry.before_status,
                        applied_at=now,
                        reverted_at=None,
                    )
                )
            session.commit()
        return group_id

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        include_reverted: bool = False,
    ) -> list[ApplySnapshot]:
        with self.session_factory() as session:
            query = select(AiAgentMdlApplySnapshot).where(
                AiAgentMdlApplySnapshot.conversation_id == conversation_id
            )
            if not include_reverted:
                query = query.where(AiAgentMdlApplySnapshot.reverted_at.is_(None))
            rows = session.scalars(
                query.order_by(AiAgentMdlApplySnapshot.applied_at)
            ).all()
            return [_from_row(row) for row in rows]

    def list_group(self, apply_group_id: str) -> list[ApplySnapshot]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AiAgentMdlApplySnapshot)
                .where(AiAgentMdlApplySnapshot.apply_group_id == apply_group_id)
                .order_by(AiAgentMdlApplySnapshot.applied_at)
            ).all()
            return [_from_row(row) for row in rows]

    def mark_reverted(self, apply_group_id: str) -> int:
        """Stamp a group's snapshots as consumed by a revert."""

        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            rows = session.scalars(
                select(AiAgentMdlApplySnapshot).where(
                    AiAgentMdlApplySnapshot.apply_group_id == apply_group_id,
                    AiAgentMdlApplySnapshot.reverted_at.is_(None),
                )
            ).all()
            for row in rows:
                row.reverted_at = now
            session.commit()
            return len(rows)


class NullApplySnapshotStore:
    """No-op snapshot store (persistence disabled / stateless deployments)."""

    def record(
        self,
        *,
        project_id: str,
        owner_id: str,
        entries: list[ApplySnapshotEntry],
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> str:
        return ""

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        include_reverted: bool = False,
    ) -> list[ApplySnapshot]:
        return []

    def list_group(self, apply_group_id: str) -> list[ApplySnapshot]:
        return []

    def mark_reverted(self, apply_group_id: str) -> int:
        return 0


def _from_row(row: AiAgentMdlApplySnapshot) -> ApplySnapshot:
    return ApplySnapshot(
        id=row.id,
        apply_group_id=row.apply_group_id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        message_id=row.message_id,
        op=row.op,
        path=row.path,
        file_id=row.file_id,
        before_content=row.before_content,
        before_status=row.before_status,
        applied_at=row.applied_at,
        reverted_at=row.reverted_at,
    )
