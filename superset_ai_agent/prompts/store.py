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

"""DB-backed prompt registry (testing platform P2.1, spec F2 / DP-2 hybrid).

Repo ``prompts/*.md`` files stay the git-reviewed default seed; this store
holds **overrides**: immutable, per-name-numbered versions plus mutable labels
(``production`` is what the runtime resolver serves). Edits create candidate
versions; only promotion moves ``production`` — the candidate→promote
discipline that keeps prompt iteration safe (spec §6.3).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.persistence.models import (
    AiAgentPromptLabel,
    AiAgentPromptVersion,
)

#: The label the runtime resolver serves.
PRODUCTION_LABEL = "production"


class PromptVersionNotFoundError(KeyError):
    """Raised when a prompt version id is unknown or belongs to another name."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PromptVersion(BaseModel):
    """One immutable prompt version."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    version: int
    content: str
    comment: str | None = None
    created_by: str | None = None
    created_at: datetime = Field(default_factory=_now)


class PromptStore(Protocol):
    """Storage contract for prompt versions and labels."""

    def create_version(
        self,
        name: str,
        content: str,
        *,
        comment: str | None = None,
        created_by: str | None = None,
    ) -> PromptVersion: ...

    def list_names(self) -> list[str]:
        """Names that have at least one stored version."""

    def list_versions(self, name: str) -> list[PromptVersion]:
        """All versions for a name, newest first."""

    def get_labeled(self, name: str, label: str) -> PromptVersion | None:
        """The version a label points at, or ``None`` (→ file fallback)."""

    def set_label(
        self,
        name: str,
        label: str,
        version_id: str,
        *,
        updated_by: str | None = None,
    ) -> PromptVersion:
        """Point ``label`` at a version of the same name (promote/rollback)."""

    def clear_label(self, name: str, label: str) -> bool:
        """Remove a label (reset to file default). True if it existed."""

    def labels_for(self, name: str) -> dict[str, str]:
        """label → version_id for one prompt name."""


class InMemoryPromptStore:
    """Process-local prompt store (tests/dev)."""

    def __init__(self) -> None:
        self._versions: dict[str, PromptVersion] = {}
        self._labels: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def create_version(
        self,
        name: str,
        content: str,
        *,
        comment: str | None = None,
        created_by: str | None = None,
    ) -> PromptVersion:
        with self._lock:
            existing = [v for v in self._versions.values() if v.name == name]
            next_number = max((v.version for v in existing), default=0) + 1
            version = PromptVersion(
                name=name,
                version=next_number,
                content=content,
                comment=comment,
                created_by=created_by,
            )
            self._versions[version.id] = version
        return version.model_copy(deep=True)

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted({v.name for v in self._versions.values()})

    def list_versions(self, name: str) -> list[PromptVersion]:
        with self._lock:
            versions = [
                v.model_copy(deep=True)
                for v in self._versions.values()
                if v.name == name
            ]
        return sorted(versions, key=lambda v: v.version, reverse=True)

    def get_labeled(self, name: str, label: str) -> PromptVersion | None:
        with self._lock:
            version_id = self._labels.get((name, label))
            if version_id is None:
                return None
            version = self._versions.get(version_id)
            return version.model_copy(deep=True) if version else None

    def set_label(
        self,
        name: str,
        label: str,
        version_id: str,
        *,
        updated_by: str | None = None,
    ) -> PromptVersion:
        with self._lock:
            version = self._versions.get(version_id)
            if version is None or version.name != name:
                raise PromptVersionNotFoundError(version_id)
            self._labels[(name, label)] = version_id
            return version.model_copy(deep=True)

    def clear_label(self, name: str, label: str) -> bool:
        with self._lock:
            return self._labels.pop((name, label), None) is not None

    def labels_for(self, name: str) -> dict[str, str]:
        with self._lock:
            return {
                lbl: version_id
                for (n, lbl), version_id in self._labels.items()
                if n == name
            }


class SqlAlchemyPromptStore:
    """SQLAlchemy-backed prompt store (durable, cross-worker)."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create_version(
        self,
        name: str,
        content: str,
        *,
        comment: str | None = None,
        created_by: str | None = None,
    ) -> PromptVersion:
        with self.session_factory() as session:
            latest = (
                session.execute(
                    select(AiAgentPromptVersion.version)
                    .where(AiAgentPromptVersion.name == name)
                    .order_by(AiAgentPromptVersion.version.desc())
                )
                .scalars()
                .first()
            )
            version = PromptVersion(
                name=name,
                version=(latest or 0) + 1,
                content=content,
                comment=comment,
                created_by=created_by,
            )
            session.add(_to_model(version))
            session.commit()
        return version

    def list_names(self) -> list[str]:
        with self.session_factory() as session:
            rows = session.execute(
                select(AiAgentPromptVersion.name).distinct()
            ).scalars()
            return sorted(rows)

    def list_versions(self, name: str) -> list[PromptVersion]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentPromptVersion)
                    .where(AiAgentPromptVersion.name == name)
                    .order_by(AiAgentPromptVersion.version.desc())
                )
                .scalars()
                .all()
            )
            return [_from_model(m) for m in models]

    def get_labeled(self, name: str, label: str) -> PromptVersion | None:
        with self.session_factory() as session:
            label_row = (
                session.execute(
                    select(AiAgentPromptLabel).where(
                        AiAgentPromptLabel.name == name,
                        AiAgentPromptLabel.label == label,
                    )
                )
                .scalars()
                .first()
            )
            if label_row is None:
                return None
            model = session.get(AiAgentPromptVersion, label_row.version_id)
            return _from_model(model) if model else None

    def set_label(
        self,
        name: str,
        label: str,
        version_id: str,
        *,
        updated_by: str | None = None,
    ) -> PromptVersion:
        with self.session_factory() as session:
            model = session.get(AiAgentPromptVersion, version_id)
            if model is None or model.name != name:
                raise PromptVersionNotFoundError(version_id)
            label_row = (
                session.execute(
                    select(AiAgentPromptLabel).where(
                        AiAgentPromptLabel.name == name,
                        AiAgentPromptLabel.label == label,
                    )
                )
                .scalars()
                .first()
            )
            if label_row is None:
                label_row = AiAgentPromptLabel(
                    id=str(uuid4()),
                    name=name,
                    label=label,
                )
                session.add(label_row)
            label_row.version_id = version_id
            label_row.updated_by = updated_by
            label_row.updated_at = _now()
            session.commit()
            return _from_model(model)

    def clear_label(self, name: str, label: str) -> bool:
        with self.session_factory() as session:
            label_row = (
                session.execute(
                    select(AiAgentPromptLabel).where(
                        AiAgentPromptLabel.name == name,
                        AiAgentPromptLabel.label == label,
                    )
                )
                .scalars()
                .first()
            )
            if label_row is None:
                return False
            session.delete(label_row)
            session.commit()
            return True

    def labels_for(self, name: str) -> dict[str, str]:
        with self.session_factory() as session:
            rows = (
                session.execute(
                    select(AiAgentPromptLabel).where(AiAgentPromptLabel.name == name)
                )
                .scalars()
                .all()
            )
            return {row.label: row.version_id for row in rows}


def _to_model(version: PromptVersion) -> AiAgentPromptVersion:
    return AiAgentPromptVersion(
        id=version.id,
        name=version.name,
        version=version.version,
        content=version.content,
        comment=version.comment,
        created_by=version.created_by,
        created_at=version.created_at,
    )


def _from_model(model: AiAgentPromptVersion) -> PromptVersion:
    return PromptVersion(
        id=model.id,
        name=model.name,
        version=model.version,
        content=model.content,
        comment=model.comment,
        created_by=model.created_by,
        created_at=model.created_at,
    )


# --- API request/response models (admin prompt registry) ---------------------


class PromptSummary(BaseModel):
    """One prompt name in the admin list."""

    name: str
    has_file_default: bool
    versions_count: int = 0
    production_version: int | None = None


class PromptDetail(BaseModel):
    """Full admin view of one prompt."""

    name: str
    file_content: str | None = None
    production_version_id: str | None = None
    versions: list[PromptVersion] = Field(default_factory=list)


class PromptVersionCreateRequest(BaseModel):
    content: str = Field(min_length=1)
    comment: str | None = None


class PromptPromoteRequest(BaseModel):
    version_id: str = Field(min_length=1)
