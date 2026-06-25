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

"""Document chunking + relevance-aware section selection (plan C4).

Replaces the blind head-truncate (``text[:20_000]``) that dropped everything past
the cut. Two seams:

- ``truncate_to_sections`` — ingestion retention: keep whole sections up to a (large)
  character limit instead of slicing mid-section, so most documents are retained
  intact and a very large one is cut on a section boundary.
- ``select_relevant_sections`` — enrichment prompt budgeting: from the retained text,
  assemble the **schema-relevant** sections within the prompt budget (keyword overlap
  with the project's table/column/model names), so late-document content about real
  tables survives instead of only the head. Degrades closed: a document already within
  budget is returned unchanged, and with no relevance terms it falls back to
  section-aware head selection.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from pydantic import BaseModel, Field

#: Default per-section character cap; sections larger than this are hard-split on
#: whitespace so one giant block cannot dominate selection or blow the budget.
_DEFAULT_MAX_SECTION_CHARS = 2_000

_SECTION_BOUNDARY = re.compile(r"\n\s*\n")

#: Stable namespace so a chunk's id is deterministic per ``(document_id, index)``.
#: Re-indexing the same document reuses the same row ids, making the vector-store
#: upsert a clean in-place replace (no orphan vectors) — plan R4.
_CHUNK_ID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


def _tokens(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {token for token in normalized.split() if token}


def chunk_sections(
    text: str, *, max_chars: int = _DEFAULT_MAX_SECTION_CHARS
) -> list[str]:
    """Split text into coherent sections (blank-line blocks, size-capped)."""

    sections: list[str] = []
    for block in _SECTION_BOUNDARY.split(text):
        stripped = block.strip()
        if not stripped:
            continue
        if len(stripped) <= max_chars:
            sections.append(stripped)
        else:
            sections.extend(_hard_split(stripped, max_chars))
    return sections


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split an oversized section on whitespace into <= max_chars pieces."""

    pieces: list[str] = []
    current: list[str] = []
    length = 0
    for word in text.split():
        addition = len(word) + (1 if current else 0)
        if current and length + addition > max_chars:
            pieces.append(" ".join(current))
            current, length = [], 0
            addition = len(word)
        current.append(word)
        length += addition
    if current:
        pieces.append(" ".join(current))
    return pieces


def truncate_to_sections(text: str, limit: int) -> tuple[str, bool]:
    """Retain whole sections up to ``limit`` chars; returns ``(text, truncated)``.

    Used at ingestion so a long document is cut on a section boundary rather than
    mid-sentence. ``limit <= 0`` means no limit. An oversized first section is hard
    character-capped so the result never exceeds ``limit``.
    """

    if limit <= 0 or len(text) <= limit:
        return text, False
    sections = chunk_sections(text)
    kept: list[str] = []
    length = 0
    for section in sections:
        addition = len(section) + (2 if kept else 0)
        if length + addition > limit:
            break
        kept.append(section)
        length += addition
    if not kept:
        # First section alone exceeds the limit — hard-cap it.
        return text[:limit], True
    return "\n\n".join(kept), True


def select_relevant_sections(text: str, *, terms: set[str], budget: int) -> str:
    """Assemble the most schema-relevant sections of ``text`` within ``budget`` chars.

    A document already within ``budget`` (or ``budget <= 0``) is returned unchanged.
    Otherwise sections are ranked by keyword overlap with ``terms`` (the project's
    table/column/model names); the highest-scoring sections that fit are kept and
    re-joined **in original order** for coherence. With no ``terms`` (or no overlap)
    it degrades to section-aware head selection — never worse than the old head-cut.
    """

    if budget <= 0 or len(text) <= budget:
        return text
    sections = chunk_sections(text)
    if not sections:
        return text[:budget]
    if terms:
        order = _ranked_indices(sections, terms)
    else:
        order = list(range(len(sections)))  # head order
    selected: list[int] = []
    length = 0
    for index in order:
        section = sections[index]
        addition = len(section) + 2
        if length + addition > budget:
            continue
        selected.append(index)
        length += addition
    if not selected:
        return text[:budget]
    selected.sort()  # restore document order for coherent reading
    return "\n\n".join(sections[index] for index in selected)


def _ranked_indices(sections: list[str], terms: set[str]) -> list[int]:
    """Section indices ordered by term-overlap score (desc), ties by document order."""

    lowered = {term.lower() for term in terms if term}
    scored = [
        (index, len(_tokens(section) & lowered))
        for index, section in enumerate(sections)
    ]
    # Sort by score desc, then original index asc (stable, document-order tiebreak).
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return [index for index, _ in scored]


# --- Persistent chunk records (RAG data layer, plan §3.1) ---------------------
#
# ``chunk_sections`` above produces ephemeral strings used once for enrichment
# prompt budgeting. ``DocumentChunk`` is the *durable* record persisted per
# document so chunks can be embedded, retrieved, viewed, and de-duplicated. The
# row is the system-of-record; vectors live in the document vector store
# (``document_retriever``), keyed by these chunk ids.


class DocumentChunk(BaseModel):
    """One persisted, retrievable slice of an extracted document."""

    id: str
    document_id: str
    chunk_index: int
    text: str
    checksum: str
    char_start: int
    char_end: int
    embedded: bool = False


class DocumentChunkMatch(BaseModel):
    """A near/exact duplicate relationship between two chunks (plan R5)."""

    chunk_id: str
    other_chunk_id: str
    document_id: str
    other_document_id: str
    score: float = Field(ge=0.0, le=1.0)
    exact: bool = False


def chunk_id(document_id: str, index: int) -> str:
    """Deterministic chunk id for ``(document_id, index)`` (stable across reindex)."""

    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, f"{document_id}:{index}"))


def chunk_checksum(text: str) -> str:
    """SHA256 of chunk text — the cheap exact-duplicate key (plan R5)."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_chunk_records(
    document_id: str,
    text: str,
    *,
    max_chars: int = _DEFAULT_MAX_SECTION_CHARS,
) -> list[DocumentChunk]:
    """Split ``text`` into persistable :class:`DocumentChunk` records.

    Character offsets point back into ``text`` so the viewer can scroll to a chunk;
    they are best-effort (a stripped/hard-split section is located from a moving
    cursor and falls back to the cursor position if not found verbatim).
    """

    records: list[DocumentChunk] = []
    cursor = 0
    for index, section in enumerate(chunk_sections(text, max_chars=max_chars)):
        start = text.find(section, cursor)
        if start < 0:
            start = cursor
        end = start + len(section)
        cursor = end
        records.append(
            DocumentChunk(
                id=chunk_id(document_id, index),
                document_id=document_id,
                chunk_index=index,
                text=section,
                checksum=chunk_checksum(section),
                char_start=start,
                char_end=end,
            )
        )
    return records


def keyword_rank_chunks(
    query: str, chunks: list[DocumentChunk], k: int
) -> list[DocumentChunk]:
    """Rank chunks by query-token overlap (the degrade-closed retrieval fallback).

    Returns up to ``k`` chunks with non-zero overlap, ordered by overlap (desc)
    then document order. An empty/termless query returns the head ``k`` in order.
    """

    if k <= 0:
        return []
    terms = _tokens(query)
    if not terms:
        return chunks[:k]
    scored = [
        (len(_tokens(chunk.text) & terms), index, chunk)
        for index, chunk in enumerate(chunks)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for score, _, chunk in scored if score > 0][:k]
