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
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


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
