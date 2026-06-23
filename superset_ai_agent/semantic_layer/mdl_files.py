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
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import cast, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.persistence.models import AiAgentSemanticMdlFile
from superset_ai_agent.semantic_layer.mdl_validation import validate_mdl
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlFileCreateRequest,
    MdlFileSourceType,
    MdlFileStatus,
    MdlFileUpdateRequest,
    MdlValidationResult,
)


class MdlFileNotFoundError(KeyError):
    """Raised when an MDL file cannot be found."""


class MdlFileValidationError(ValueError):
    """Raised when an MDL file cannot be activated due to validation errors."""


def _assert_activatable(status: str | None, content: str) -> None:
    """Block the draft->active transition when the file has validation errors.

    This is a structural defense-in-depth gate. Project-level and physical
    (schema-aware) validation is enforced at the API activation route.
    """

    if status != "active":
        return
    validation = validate_mdl(content)
    if validation.valid:
        return
    errors = "; ".join(
        message.message
        for message in validation.messages
        if message.severity == "error"
    )
    raise MdlFileValidationError(
        f"Cannot activate an MDL file with validation errors: {errors}"
    )


class MdlFileStore(Protocol):
    """Storage contract for project-scoped MDL JSON files."""

    def list(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[MdlFile]:
        """List MDL files for a semantic project."""

    def get(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> MdlFile:
        """Return one MDL file."""

    def create(
        self,
        project_id: str,
        request: MdlFileCreateRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        validation: MdlValidationResult | None = None,
    ) -> MdlFile:
        """Create an MDL JSON file.

        ``validation`` overrides the structural-only check with a precomputed
        (e.g. schema-aware) result so physical findings are persisted.
        """

    def update(
        self,
        file_id: str,
        request: MdlFileUpdateRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        validation: MdlValidationResult | None = None,
    ) -> MdlFile:
        """Update one MDL JSON file."""

    def delete(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        """Soft-delete one MDL JSON file."""

    def validate(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> MdlValidationResult:
        """Validate one MDL JSON file and persist validation output."""


class InMemoryMdlFileStore:
    """Process-local MDL file store for development and tests."""

    def __init__(self) -> None:
        self._files: dict[str, tuple[str, MdlFile]] = {}

    def list(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[MdlFile]:
        files = [
            file
            for _, file in self._files.values()
            if file.project_id == project_id
            and file.deleted_at is None
            and file.status != "deleted"
        ]
        return [
            file.model_copy(deep=True)
            for file in sorted(files, key=lambda item: item.path)
        ]

    def get(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> MdlFile:
        stored = self._files.get(file_id)
        if stored is None:
            raise MdlFileNotFoundError(file_id)
        file = stored[1]
        if file.deleted_at is not None or file.status == "deleted":
            raise MdlFileNotFoundError(file_id)
        return file.model_copy(deep=True)

    def create(
        self,
        project_id: str,
        request: MdlFileCreateRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        validation: MdlValidationResult | None = None,
    ) -> MdlFile:
        path = normalize_mdl_path(request.path)
        existing = self._find_by_path(project_id, path, owner_id=owner_id)
        if existing is not None:
            raise ValueError(f"MDL file already exists: {path}")
        file = _new_file(
            project_id=project_id,
            path=path,
            request=request,
            owner_id=owner_id,
            validation=validation,
        )
        self._files[file.id] = (owner_id, file)
        return file.model_copy(deep=True)

    def update(
        self,
        file_id: str,
        request: MdlFileUpdateRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        validation: MdlValidationResult | None = None,
    ) -> MdlFile:
        file = self.get(file_id, owner_id=owner_id)
        updates: dict[str, object] = {
            "updated_at": _utc_now(),
            "updated_by": owner_id,
        }
        if request.path is not None:
            path = normalize_mdl_path(request.path)
            existing = self._find_by_path(file.project_id, path, owner_id=owner_id)
            if existing is not None and existing.id != file.id:
                raise ValueError(f"MDL file already exists: {path}")
            updates["path"] = path
            updates["filename"] = PurePosixPath(path).name
        if request.content is not None:
            updates["content"] = request.content
            updates["checksum"] = _checksum(request.content)
            updates["validation"] = (
                validation
                if validation is not None
                else validate_mdl(request.content)
            )
        elif validation is not None:
            updates["validation"] = validation
        if request.status is not None:
            updates["status"] = request.status
        _assert_activatable(
            request.status,
            request.content if request.content is not None else file.content,
        )
        updated = file.model_copy(update=updates)
        self._files[file.id] = (owner_id, updated)
        return updated.model_copy(deep=True)

    def delete(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        file = self.get(file_id, owner_id=owner_id)
        deleted = file.model_copy(
            update={
                "status": "deleted",
                "deleted_at": _utc_now(),
                "updated_at": _utc_now(),
                "updated_by": owner_id,
            }
        )
        self._files[file_id] = (owner_id, deleted)

    def validate(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> MdlValidationResult:
        file = self.get(file_id, owner_id=owner_id)
        validation = validate_mdl(file.content)
        self.update(
            file_id,
            MdlFileUpdateRequest(content=file.content),
            owner_id=owner_id,
        )
        return validation

    def _find_by_path(
        self,
        project_id: str,
        path: str,
        *,
        owner_id: str,
    ) -> MdlFile | None:
        for _, file in self._files.values():
            if (
                file.project_id == project_id
                and file.path == path
                and file.deleted_at is None
                and file.status != "deleted"
            ):
                return file
        return None


class SqlAlchemyMdlFileStore:
    """SQLAlchemy-backed MDL file store."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def list(
        self,
        project_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> list[MdlFile]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentSemanticMdlFile)
                    .where(
                        AiAgentSemanticMdlFile.project_id == project_id,
                        AiAgentSemanticMdlFile.deleted_at.is_(None),
                        AiAgentSemanticMdlFile.status != "deleted",
                    )
                    .order_by(AiAgentSemanticMdlFile.path.asc())
                )
                .scalars()
                .all()
            )
            return [_file_from_model(model) for model in models]

    def get(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> MdlFile:
        with self.session_factory() as session:
            model = self._get_model(session, file_id, owner_id=owner_id)
            return _file_from_model(model)

    def create(
        self,
        project_id: str,
        request: MdlFileCreateRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        validation: MdlValidationResult | None = None,
    ) -> MdlFile:
        path = normalize_mdl_path(request.path)
        with self.session_factory() as session:
            existing = self._find_model_by_path(
                session,
                project_id,
                path,
                owner_id=owner_id,
                include_deleted=True,
            )
            if existing is not None and existing.deleted_at is None:
                raise ValueError(f"MDL file already exists: {path}")
            file = _new_file(
                project_id=project_id,
                path=path,
                request=request,
                owner_id=owner_id,
                validation=validation,
            )
            if existing is not None:
                _apply_file_to_model(existing, file)
                existing.deleted_at = None
                model = existing
            else:
                model = _file_to_model(file)
                session.add(model)
            session.commit()
            return _file_from_model(model)

    def update(
        self,
        file_id: str,
        request: MdlFileUpdateRequest,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
        validation: MdlValidationResult | None = None,
    ) -> MdlFile:
        with self.session_factory() as session:
            model = self._get_model(session, file_id, owner_id=owner_id)
            if request.path is not None:
                path = normalize_mdl_path(request.path)
                existing = self._find_model_by_path(
                    session,
                    model.project_id,
                    path,
                    owner_id=owner_id,
                    include_deleted=False,
                )
                if existing is not None and existing.id != file_id:
                    raise ValueError(f"MDL file already exists: {path}")
                model.path = path
                model.filename = PurePosixPath(path).name
            if request.content is not None:
                model.content = request.content
                model.checksum = _checksum(request.content)
                effective = (
                    validation
                    if validation is not None
                    else validate_mdl(request.content)
                )
                model.validation = effective.model_dump(mode="json")
            elif validation is not None:
                model.validation = validation.model_dump(mode="json")
            if request.status is not None:
                model.status = request.status
            _assert_activatable(request.status, model.content)
            model.updated_by = owner_id
            model.updated_at = _utc_now()
            session.commit()
            return _file_from_model(model)

    def delete(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> None:
        with self.session_factory() as session:
            model = self._get_model(session, file_id, owner_id=owner_id)
            model.status = "deleted"
            model.deleted_at = _utc_now()
            model.updated_at = model.deleted_at
            model.updated_by = owner_id
            session.commit()

    def validate(
        self,
        file_id: str,
        *,
        owner_id: str = DEFAULT_OWNER_ID,
    ) -> MdlValidationResult:
        with self.session_factory() as session:
            model = self._get_model(session, file_id, owner_id=owner_id)
            validation = validate_mdl(model.content)
            model.validation = validation.model_dump(mode="json")
            model.updated_at = _utc_now()
            model.updated_by = owner_id
            session.commit()
            return validation

    @staticmethod
    def _get_model(
        session: Session,
        file_id: str,
        *,
        owner_id: str,
    ) -> AiAgentSemanticMdlFile:
        model = session.get(AiAgentSemanticMdlFile, file_id)
        if (
            model is None
            or model.deleted_at is not None
            or model.status == "deleted"
        ):
            raise MdlFileNotFoundError(file_id)
        return model

    @staticmethod
    def _find_model_by_path(
        session: Session,
        project_id: str,
        path: str,
        *,
        owner_id: str,
        include_deleted: bool,
    ) -> AiAgentSemanticMdlFile | None:
        query = select(AiAgentSemanticMdlFile).where(
            AiAgentSemanticMdlFile.project_id == project_id,
            AiAgentSemanticMdlFile.path == path,
        )
        if not include_deleted:
            query = query.where(
                AiAgentSemanticMdlFile.deleted_at.is_(None),
                AiAgentSemanticMdlFile.status != "deleted",
            )
        return session.execute(query).scalars().one_or_none()


def normalize_mdl_path(path: str) -> str:
    """Normalize a project-relative MDL JSON path."""

    normalized = path.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("MDL path is empty.")
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute() or ".." in posix_path.parts:
        raise ValueError("MDL path must stay within the semantic project.")
    if posix_path.suffix.lower() != ".json":
        raise ValueError("MDL files must use a .json extension.")
    return str(posix_path)


def _new_file(
    *,
    project_id: str,
    path: str,
    request: MdlFileCreateRequest,
    owner_id: str,
    validation: MdlValidationResult | None = None,
) -> MdlFile:
    if validation is None:
        validation = validate_mdl(request.content)
    return MdlFile(
        project_id=project_id,
        path=path,
        filename=PurePosixPath(path).name,
        content=request.content,
        source_type=request.source_type,
        validation=validation,
        checksum=_checksum(request.content),
        source_document_id=request.source_document_id,
        created_by=owner_id,
        updated_by=owner_id,
    )


def _file_to_model(file: MdlFile) -> AiAgentSemanticMdlFile:
    return AiAgentSemanticMdlFile(
        id=file.id,
        project_id=file.project_id,
        path=file.path,
        filename=file.filename,
        content=file.content,
        content_type=file.content_type,
        source_type=file.source_type,
        status=file.status,
        validation=(
            file.validation.model_dump(mode="json")
            if file.validation is not None
            else None
        ),
        checksum=file.checksum,
        source_document_id=file.source_document_id,
        created_by=file.created_by,
        updated_by=file.updated_by,
        created_at=file.created_at,
        updated_at=file.updated_at,
        deleted_at=file.deleted_at,
    )


def _file_from_model(model: AiAgentSemanticMdlFile) -> MdlFile:
    return MdlFile(
        id=model.id,
        project_id=model.project_id,
        path=model.path,
        filename=model.filename,
        content=model.content,
        content_type=model.content_type,
        source_type=cast(MdlFileSourceType, model.source_type),
        status=cast(MdlFileStatus, model.status),
        validation=(
            MdlValidationResult.model_validate(model.validation)
            if model.validation is not None
            else None
        ),
        checksum=model.checksum,
        source_document_id=model.source_document_id,
        created_by=model.created_by,
        updated_by=model.updated_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
        deleted_at=model.deleted_at,
    )


def _apply_file_to_model(
    model: AiAgentSemanticMdlFile,
    file: MdlFile,
) -> None:
    model.path = file.path
    model.filename = file.filename
    model.content = file.content
    model.content_type = file.content_type
    model.source_type = file.source_type
    model.status = file.status
    model.validation = (
        file.validation.model_dump(mode="json") if file.validation is not None else None
    )
    model.checksum = file.checksum
    model.source_document_id = file.source_document_id
    model.updated_by = file.updated_by
    model.updated_at = file.updated_at


def _checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
