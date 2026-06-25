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

from superset_ai_agent.semantic_layer.document_chunks import (
    build_chunk_records,
    chunk_checksum,
    chunk_id,
    DocumentChunk,
    keyword_rank_chunks,
)


def test_build_chunk_records_indexes_offsets_and_checksums() -> None:
    text = "Orders table holds revenue.\n\nCustomers join on customer_id.\n\nNotes."
    records = build_chunk_records("doc-1", text)

    assert [record.chunk_index for record in records] == [0, 1, 2]
    assert all(record.document_id == "doc-1" for record in records)
    # Offsets point back into the original text so the viewer can scroll to a chunk.
    for record in records:
        assert text[record.char_start : record.char_end] == record.text
    assert records[0].checksum == chunk_checksum(records[0].text)
    assert records[0].embedded is False


def test_chunk_id_is_deterministic_per_document_and_index() -> None:
    # Stable ids make a reindex a clean vector upsert (no orphaned vectors).
    assert chunk_id("doc-1", 0) == chunk_id("doc-1", 0)
    assert chunk_id("doc-1", 0) != chunk_id("doc-1", 1)
    assert chunk_id("doc-1", 0) != chunk_id("doc-2", 0)
    records = build_chunk_records("doc-1", "a\n\nb")
    assert records[0].id == chunk_id("doc-1", 0)


def test_build_chunk_records_empty_text_yields_no_chunks() -> None:
    assert build_chunk_records("doc-1", "   \n\n  ") == []


def test_keyword_rank_chunks_orders_by_overlap_and_drops_zero() -> None:
    chunks = [
        DocumentChunk(
            id="a",
            document_id="d",
            chunk_index=0,
            text="weather forecast for tomorrow",
            checksum="x",
            char_start=0,
            char_end=1,
        ),
        DocumentChunk(
            id="b",
            document_id="d",
            chunk_index=1,
            text="revenue by customer and region",
            checksum="y",
            char_start=1,
            char_end=2,
        ),
    ]
    ranked = keyword_rank_chunks("customer revenue", chunks, k=5)
    assert [chunk.id for chunk in ranked] == ["b"]  # zero-overlap chunk dropped


def test_keyword_rank_chunks_termless_query_returns_head() -> None:
    chunks = [
        DocumentChunk(
            id=str(index),
            document_id="d",
            chunk_index=index,
            text=f"section {index}",
            checksum="c",
            char_start=index,
            char_end=index + 1,
        )
        for index in range(3)
    ]
    ranked = keyword_rank_chunks("", chunks, k=2)
    assert [chunk.id for chunk in ranked] == ["0", "1"]
