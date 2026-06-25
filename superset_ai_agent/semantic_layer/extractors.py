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
import json  # noqa: TID251 - standalone agent: stdlib JSON for document parsing
from html.parser import HTMLParser
from typing import Protocol

#: Office Open XML Word document MIME type (.docx).
_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class DocumentExtractor(Protocol):
    """Extract safe plain text from supported document types."""

    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> str:
        """Return plain text extracted from uploaded content."""


class CompositeDocumentExtractor:
    """Extractor for text, Markdown, CSV, JSON, HTML, PDF, and DOCX documents.

    HTML extraction is dependency-free (stdlib ``html.parser``). PDF and DOCX rely
    on optional packages (``pypdf`` / ``python-docx``); when a package is missing the
    extractor raises ``ValueError`` so ``create_document`` records the document with
    ``status="error"`` and a clear message, rather than crashing the upload
    (governance: degrade closed).
    """

    def extract_text(self, *, filename: str, content_type: str, content: bytes) -> str:
        normalized_type = normalize_content_type(content_type)
        if normalized_type in {"text/plain", "text/markdown"}:
            return _decode_text(content)
        if normalized_type == "text/csv":
            return _extract_csv(content)
        if normalized_type == "application/json":
            return _extract_json(content)
        if normalized_type == "text/html":
            return _extract_html(content)
        if normalized_type == "application/pdf":
            return _extract_pdf(content)
        if normalized_type == _DOCX_CONTENT_TYPE:
            return _extract_docx(content)
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


class _HtmlTextExtractor(HTMLParser):
    """Collect visible text from HTML, dropping script/style and block-spacing tags."""

    _SKIP = {"script", "style", "head", "title", "meta"}
    _BLOCK = {
        "p",
        "br",
        "div",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        lines = [line.strip() for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _extract_html(content: bytes) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(_decode_text(content))
    parser.close()
    return parser.get_text()


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader  # optional dependency (ignore_missing_imports)
    except ImportError as ex:
        raise ValueError(
            "PDF extraction requires the 'pypdf' package to be installed."
        ) from ex
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return _strip_nul("\n\n".join(page.strip() for page in pages if page.strip()))


def _extract_docx(content: bytes) -> str:
    try:
        import docx  # optional dependency: python-docx (ignore_missing_imports)
    except ImportError as ex:
        raise ValueError(
            "DOCX extraction requires the 'python-docx' package to be installed."
        ) from ex
    document = docx.Document(io.BytesIO(content))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
    return _strip_nul("\n".join(text for text in paragraphs if text))
