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

"""Retriever seam — rank MDL schema items for a question (Wren parity).

`KeywordRetriever` (default, zero-dependency) ranks by token overlap;
`EmbeddingRetriever` ranks by cosine similarity over an `Embedder`. Both operate
on `SchemaItem` chunks derived from a `CompiledManifest`, mirroring Wren's
`schema_items` collection. The embedding path degrades to keyword when the
embedder is unavailable (governance: degrade closed).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Protocol

from pydantic import BaseModel

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.embeddings import Embedder, NullEmbedder
from superset_ai_agent.semantic_layer.mdl_compile import (
    compile_manifest,
    CompiledManifest,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore

logger = logging.getLogger(__name__)


class SchemaItem(BaseModel):
    """One retrievable MDL chunk (a model, column, or relationship)."""

    kind: str
    name: str
    model: str | None = None
    text: str


class Retriever(Protocol):
    name: str

    def retrieve(
        self, question: str, items: list[SchemaItem], k: int
    ) -> list[SchemaItem]:
        """Return the top-k items most relevant to the question."""


def manifest_to_schema_items(manifest: CompiledManifest) -> list[SchemaItem]:
    """Chunk a compiled manifest into retrievable schema items."""

    items: list[SchemaItem] = []
    for model in manifest.models:
        model_name = str(model.get("name") or "")
        if not model_name:
            continue
        columns = model.get("columns") or []
        col_names = ", ".join(
            str(col.get("name")) for col in columns if col.get("name")
        )
        items.append(
            SchemaItem(
                kind="model",
                name=model_name,
                model=model_name,
                text=f"model {model_name} columns: {col_names}",
            )
        )
        for col in columns:
            col_name = str(col.get("name") or "")
            if not col_name:
                continue
            items.append(
                SchemaItem(
                    kind="column",
                    name=col_name,
                    model=model_name,
                    text=f"{model_name}.{col_name} {col.get('type') or ''}".strip(),
                )
            )
    for rel in manifest.relationships:
        rel_name = str(rel.get("name") or "")
        if not rel_name:
            continue
        models = ", ".join(str(m) for m in (rel.get("models") or []))
        items.append(
            SchemaItem(
                kind="relationship",
                name=rel_name,
                text=f"relationship {rel_name} joins {models} "
                f"({rel.get('joinType') or ''})",
            )
        )
    return items


def _tokens(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {token for token in normalized.split() if token}


class KeywordRetriever:
    """Default retriever: rank by question/item token overlap."""

    name = "keyword"

    def retrieve(
        self, question: str, items: list[SchemaItem], k: int
    ) -> list[SchemaItem]:
        q_tokens = _tokens(question)
        if not q_tokens:
            return items[:k]
        scored = sorted(
            items,
            key=lambda item: len(q_tokens & _tokens(f"{item.name} {item.text}")),
            reverse=True,
        )
        return scored[:k]


class EmbeddingRetriever:
    """Embedding retriever: rank by cosine similarity over an Embedder.

    Embeddings are computed in-memory per call; a LanceDB-backed persistent
    index is an optional optimization (wren_full.md RV1). Falls back to keyword
    when the embedder is unavailable.
    """

    name = "embedding"

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._keyword = KeywordRetriever()

    def retrieve(
        self, question: str, items: list[SchemaItem], k: int
    ) -> list[SchemaItem]:
        if not items or not self.embedder.is_available():
            return self._keyword.retrieve(question, items, k)
        try:
            vectors = self.embedder.embed([item.text for item in items])
            query_vector = self.embedder.embed([question])[0]
        except Exception:  # pylint: disable=broad-except
            return self._keyword.retrieve(question, items, k)
        scored = sorted(
            zip(items, vectors, strict=True),
            key=lambda pair: _cosine(query_vector, pair[1]),
            reverse=True,
        )
        return [item for item, _ in scored[:k]]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def create_retriever(
    config: AgentConfig, embedder: Embedder | None = None
) -> Retriever:
    """Build the configured retriever; degrade to keyword when embedding is off."""

    if config.wren_retriever == "embedding":
        active = embedder or NullEmbedder()
        if active.is_available():
            return EmbeddingRetriever(active)
    return KeywordRetriever()


def retrieve_mdl_context(
    *,
    config: AgentConfig,
    retriever: Retriever,
    question: str,
    project_id: str | None,
    owner_id: str,
    mdl_file_store: MdlFileStore | None,
) -> list[dict[str, Any]]:
    """Rank a project's active MDL into prompt context-item dicts for a question.

    Distinct from [`retrieval.retrieve_schema_context`](retrieval.py), which ranks
    physical Superset *datasets*; this ranks compiled *MDL* schema items (models/
    columns/relationships) via the configured `Retriever` seam.

    Compiles the project's active MDL files, chunks them into ``SchemaItem``s
    (Wren ``schema_items`` parity), ranks them with the configured retriever, and
    returns the top-k as ``context_items`` dicts the SQL prompt already consumes.

    Degrades closed: returns ``[]`` when there is no project, no MDL file store,
    no active MDL, or on any error — so the existing keyword context path is
    never disrupted when the retriever has nothing to add.
    """

    if project_id is None or mdl_file_store is None:
        return []
    try:
        active_files = [
            file
            for file in mdl_file_store.list(project_id, owner_id=owner_id)
            if file.status == "active" and file.deleted_at is None
        ]
        if not active_files:
            return []
        manifest = compile_manifest(active_files)
        items = manifest_to_schema_items(manifest)
        if not items:
            return []
        top = retriever.retrieve(question, items, config.wren_context_limit)
        return [
            {
                "source": "retriever",
                "retriever": retriever.name,
                "kind": item.kind,
                "name": item.name,
                "model": item.model,
                "text": item.text,
            }
            for item in top
        ]
    except Exception as ex:  # pylint: disable=broad-except - retrieval is best-effort
        logger.warning("Schema retrieval failed; using keyword context only: %s", ex)
        return []
