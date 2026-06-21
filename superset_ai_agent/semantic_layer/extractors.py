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

import csv
import io
import json
from typing import Protocol


class DocumentExtractor(Protocol):
    """Extract safe plain text from supported document types."""

    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> str:
        """Return plain text extracted from uploaded content."""


class CompositeDocumentExtractor:
    """Extractor for text, Markdown, CSV, and JSON documents."""

    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> str:
        normalized_type = normalize_content_type(content_type)
        if normalized_type in {"text/plain", "text/markdown"}:
            return _decode_text(content)
        if normalized_type == "text/csv":
            return _extract_csv(content)
        if normalized_type == "application/json":
            return _extract_json(content)
        raise ValueError(f"Unsupported document content type: {content_type}")


def normalize_content_type(content_type: str) -> str:
    """Return a comparable content type without parameters."""

    return content_type.split(";", 1)[0].strip().lower()


def _decode_text(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    return _strip_nul(text)


def _extract_csv(content: bytes) -> str:
    text = _decode_text(content)
    reader = csv.reader(io.StringIO(text))
    rows = [" | ".join(cell.strip() for cell in row) for row in reader]
    return "\n".join(row for row in rows if row)


def _extract_json(content: bytes) -> str:
    parsed = json.loads(_decode_text(content))
    return json.dumps(parsed, indent=2, sort_keys=True)


def _strip_nul(text: str) -> str:
    return text.replace("\x00", "")
