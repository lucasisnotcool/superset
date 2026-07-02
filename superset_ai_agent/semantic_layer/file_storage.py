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

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING
from urllib.parse import quote, unquote, urlparse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


class DocumentStorage(Protocol):
    """Storage contract for raw semantic-layer document bytes."""

    def write(self, *, document_id: str, filename: str, content: bytes) -> str:
        """Persist bytes and return a storage URI."""

    def read(self, storage_uri: str) -> bytes:
        """Read raw bytes by storage URI."""

    def delete(self, storage_uri: str) -> None:
        """Delete raw bytes by storage URI."""


class LocalDocumentStorage:
    """Store uploaded documents under a local agent storage directory."""

    def __init__(self, base_dir: str):
        self.documents_dir = Path(base_dir).expanduser().resolve() / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def write(self, *, document_id: str, filename: str, content: bytes) -> str:
        safe_filename = _safe_filename(filename)
        path = self.documents_dir / document_id / safe_filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path.resolve().as_uri()

    def read(self, storage_uri: str) -> bytes:
        return _path_from_file_uri(storage_uri).read_bytes()

    def delete(self, storage_uri: str) -> None:
        path = _path_from_file_uri(storage_uri)
        if path.exists():
            path.unlink()


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "document"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:180] or "document"


def _path_from_file_uri(storage_uri: str) -> Path:
    parsed = urlparse(storage_uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported document storage URI: {storage_uri}")
    return Path(parsed.path)


class S3DocumentStorage:
    """Store uploaded documents in an S3-compatible object store."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("AI_AGENT_DOCUMENT_S3_BUCKET is required for S3 storage.")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client or _create_s3_client(
            endpoint_url=endpoint_url,
            region_name=region_name,
        )

    def write(self, *, document_id: str, filename: str, content: bytes) -> str:
        key = self._key(document_id=document_id, filename=filename)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return f"s3://{self.bucket}/{quote(key)}"

    def read(self, storage_uri: str) -> bytes:
        bucket, key = _bucket_key_from_s3_uri(storage_uri)
        response = self.client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        return body.read()

    def delete(self, storage_uri: str) -> None:
        bucket, key = _bucket_key_from_s3_uri(storage_uri)
        self.client.delete_object(Bucket=bucket, Key=key)

    def _key(self, *, document_id: str, filename: str) -> str:
        safe_document_id = re.sub(r"[^A-Za-z0-9._-]+", "_", document_id)
        parts = [part for part in (self.prefix, safe_document_id) if part]
        return "/".join([*parts, _safe_filename(filename)])


def _bucket_key_from_s3_uri(storage_uri: str) -> tuple[str, str]:
    parsed = urlparse(storage_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Unsupported document storage URI: {storage_uri}")
    return parsed.netloc, unquote(parsed.path.lstrip("/"))


#: URI scheme for rows in ``ai_agent_document_blobs`` (the agent's own database).
_AGENT_DB_SCHEME = "agent-db"
_AGENT_DB_NETLOC = "documents"


class PostgresDocumentStorage:
    """Store uploaded documents as rows in the agent database.

    The postgres-only twin of `LocalDocumentStorage`/`S3DocumentStorage` for
    deployments with no writable disk and no object store: bytes land in the
    ``ai_agent_document_blobs`` table next to the rest of the agent's relational
    state, bounded per-file by the existing upload cap. URIs are
    ``agent-db://documents/<document_id>/<filename>`` and round-trip through the
    same ``storage_uri`` column the other backends use.
    """

    def __init__(self, session_factory: "sessionmaker[Session]") -> None:
        self._session_factory = session_factory

    def write(self, *, document_id: str, filename: str, content: bytes) -> str:
        from superset_ai_agent.persistence.models import AiAgentDocumentBlob

        safe_filename = _safe_filename(filename)
        storage_key = f"{document_id}/{safe_filename}"
        with self._session_factory() as session:
            blob = session.get(AiAgentDocumentBlob, storage_key)
            if blob is None:
                blob = AiAgentDocumentBlob(
                    storage_key=storage_key,
                    document_id=document_id,
                    filename=safe_filename,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(blob)
            blob.size_bytes = len(content)
            blob.data = content
            session.commit()
        return (
            f"{_AGENT_DB_SCHEME}://{_AGENT_DB_NETLOC}/"
            f"{quote(document_id)}/{quote(safe_filename)}"
        )

    def read(self, storage_uri: str) -> bytes:
        from superset_ai_agent.persistence.models import AiAgentDocumentBlob

        storage_key = _storage_key_from_agent_db_uri(storage_uri)
        with self._session_factory() as session:
            blob = session.get(AiAgentDocumentBlob, storage_key)
            if blob is None:
                raise FileNotFoundError(f"No stored document for URI: {storage_uri}")
            return bytes(blob.data)

    def delete(self, storage_uri: str) -> None:
        from superset_ai_agent.persistence.models import AiAgentDocumentBlob

        storage_key = _storage_key_from_agent_db_uri(storage_uri)
        with self._session_factory() as session:
            blob = session.get(AiAgentDocumentBlob, storage_key)
            if blob is not None:
                session.delete(blob)
                session.commit()


def _storage_key_from_agent_db_uri(storage_uri: str) -> str:
    parsed = urlparse(storage_uri)
    if (
        parsed.scheme != _AGENT_DB_SCHEME
        or parsed.netloc != _AGENT_DB_NETLOC
        or not parsed.path.strip("/")
    ):
        raise ValueError(f"Unsupported document storage URI: {storage_uri}")
    return unquote(parsed.path.lstrip("/"))


def _create_s3_client(
    *,
    endpoint_url: str | None,
    region_name: str | None,
) -> Any:
    try:
        import boto3  # pylint: disable=import-outside-toplevel
    except ImportError as ex:
        raise RuntimeError(
            "boto3 is required when AI_AGENT_DOCUMENT_STORAGE=s3."
        ) from ex
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region_name,
    )
