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

import importlib.util
import io

import pytest

from superset_ai_agent.semantic_layer.extractors import (
    _DOCX_CONTENT_TYPE,
    CompositeDocumentExtractor,
)

_PDF_AVAILABLE = importlib.util.find_spec("pypdf") is not None
_DOCX_AVAILABLE = importlib.util.find_spec("docx") is not None


def _extract(content_type: str, content: bytes) -> str:
    return CompositeDocumentExtractor().extract_text(
        filename="doc",
        content_type=content_type,
        content=content,
    )


def test_html_extraction_strips_tags_and_scripts() -> None:
    html = (
        b"<html><head><title>x</title><style>p{}</style></head>"
        b"<body><h1>Revenue</h1><p>Orders join customers.</p>"
        b"<script>evil()</script></body></html>"
    )
    text = _extract("text/html", html)
    assert "Revenue" in text
    assert "Orders join customers." in text
    assert "evil" not in text  # script content dropped
    assert "{}" not in text  # style content dropped


def test_html_content_type_parameters_are_normalized() -> None:
    text = _extract("text/html; charset=utf-8", b"<p>hello</p>")
    assert text == "hello"


def test_unsupported_type_still_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported document content type"):
        _extract("image/png", b"\x89PNG")


def test_pdf_missing_dependency_raises_clear_error() -> None:
    if _PDF_AVAILABLE:
        pytest.skip("pypdf installed; missing-dependency path not exercised")
    with pytest.raises(ValueError, match="pypdf"):
        _extract("application/pdf", b"%PDF-1.4 ...")


def test_docx_missing_dependency_raises_clear_error() -> None:
    if _DOCX_AVAILABLE:
        pytest.skip("python-docx installed; missing-dependency path not exercised")
    with pytest.raises(ValueError, match="python-docx"):
        _extract(_DOCX_CONTENT_TYPE, b"PK\x03\x04 ...")


@pytest.mark.skipif(not _DOCX_AVAILABLE, reason="python-docx not installed")
def test_docx_round_trip_extracts_paragraphs() -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("First line.")
    document.add_paragraph("Second line.")
    buffer = io.BytesIO()
    document.save(buffer)

    text = _extract(_DOCX_CONTENT_TYPE, buffer.getvalue())
    assert "First line." in text
    assert "Second line." in text


@pytest.mark.skipif(
    not (_PDF_AVAILABLE and importlib.util.find_spec("reportlab")),
    reason="pypdf and reportlab required to build and read a text PDF",
)
def test_pdf_round_trip_extracts_text() -> None:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Hello from PDF")
    pdf.save()

    text = _extract("application/pdf", buffer.getvalue())
    assert "Hello from PDF" in text
