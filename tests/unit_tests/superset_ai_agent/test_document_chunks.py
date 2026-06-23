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

"""Plan C4 — document chunking + relevance-aware section selection."""

from __future__ import annotations

from superset_ai_agent.semantic_layer.document_chunks import (
    chunk_sections,
    select_relevant_sections,
    truncate_to_sections,
)


def test_chunk_sections_splits_on_blank_lines() -> None:
    text = "First section line.\n\nSecond section line.\n\n\nThird."
    assert chunk_sections(text) == [
        "First section line.",
        "Second section line.",
        "Third.",
    ]


def test_chunk_sections_hard_splits_oversized_block() -> None:
    block = " ".join(["word"] * 50)  # 50 * 5 = ~250 chars, no blank lines
    chunks = chunk_sections(block, max_chars=40)
    assert len(chunks) > 1
    assert all(len(chunk) <= 40 for chunk in chunks)
    # No content is lost across the hard split.
    assert " ".join(chunks).split() == block.split()


def test_truncate_to_sections_keeps_short_text_unchanged() -> None:
    text = "alpha\n\nbeta"
    out, truncated = truncate_to_sections(text, 1000)
    assert out == text
    assert truncated is False


def test_truncate_to_sections_keeps_whole_sections_to_limit() -> None:
    text = "aaaa\n\nbbbb\n\ncccc"  # three 4-char sections
    out, truncated = truncate_to_sections(text, 10)
    assert truncated is True
    # "aaaa" (4) + "\n\n" + "bbbb" (4) = 10; "cccc" would overflow → dropped.
    assert out == "aaaa\n\nbbbb"
    assert "cccc" not in out


def test_truncate_to_sections_hard_caps_oversized_first_section() -> None:
    out, truncated = truncate_to_sections("x" * 100, 10)
    assert truncated is True
    assert out == "x" * 10


def test_truncate_to_sections_zero_limit_is_unlimited() -> None:
    text = "a" * 5000
    out, truncated = truncate_to_sections(text, 0)
    assert out == text
    assert truncated is False


def test_select_relevant_sections_returns_unchanged_within_budget() -> None:
    text = "alpha\n\nbeta"
    assert select_relevant_sections(text, terms={"alpha"}, budget=1000) == text


def test_select_relevant_sections_keeps_relevant_late_section() -> None:
    # A long head of irrelevant filler, then a late section about `revenue`.
    filler = "\n\n".join(
        f"filler paragraph number {i} about nothing" for i in range(40)
    )
    late = "Quarterly revenue is computed from the orders table."
    text = f"{filler}\n\n{late}"
    assert len(text) > 200

    out = select_relevant_sections(text, terms={"revenue", "orders"}, budget=120)

    # The schema-relevant late section survives despite being past a head-cut point.
    assert "Quarterly revenue" in out
    assert len(out) <= 120


def test_select_relevant_sections_preserves_document_order() -> None:
    text = "intro about orders\n\nmiddle filler\n\norders revenue summary"
    out = select_relevant_sections(text, terms={"orders", "revenue"}, budget=60)
    # Both orders-mentioning sections selected; kept in original order.
    assert out.index("intro about orders") < out.index("orders revenue summary")


def test_select_relevant_sections_degrades_to_head_without_terms() -> None:
    text = "head section one\n\nsecond\n\nthird tail section"
    out = select_relevant_sections(text, terms=set(), budget=20)
    # No relevance signal → section-aware head selection (never empty).
    assert out.startswith("head section one")
    assert len(out) <= 20
