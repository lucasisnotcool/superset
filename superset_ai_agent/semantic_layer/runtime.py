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

import json  # noqa: TID251 - standalone agent context-item dedup key
from typing import Any

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.store import SemanticLayerStore


def merge_indexed_semantic_context(
    *,
    semantic_layer_store: SemanticLayerStore | None,
    scope: ConversationScope,
    owner_id: str = DEFAULT_OWNER_ID,
    wren_context: WrenContextArtifact,
) -> WrenContextArtifact:
    """Merge the latest indexed semantic overlay into a Wren context artifact."""

    if semantic_layer_store is None:
        return wren_context
    latest_version = semantic_layer_store.get_latest_version(scope, owner_id=owner_id)
    if latest_version is None or latest_version.wren_context is None:
        return wren_context

    return _merge(wren_context, latest_version.wren_context)


def cap_context_items(
    items: list[dict[str, Any]], max_items: int
) -> list[dict[str, Any]]:
    """Dedup + bound merged prompt context items (wren_full.md R-RET-E).

    The three context sources (doc overlay, MDL retriever chunks, ``fetch_context``)
    concatenate with no combined budget, so a wide schema could inflate the prompt.
    This dedups exact duplicates and, on overflow, keeps the **retrieval-ranked**
    chunks (``source == "retriever"``) first since those are the most relevant.
    ``max_items <= 0`` is unlimited.
    """

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = json.dumps(item, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    if max_items <= 0 or len(deduped) <= max_items:
        return deduped
    ranked = [item for item in deduped if item.get("source") == "retriever"]
    other = [item for item in deduped if item.get("source") != "retriever"]
    return (ranked + other)[:max_items]


def _merge(
    wren_context: WrenContextArtifact, indexed_context: WrenContextArtifact
) -> WrenContextArtifact:
    return wren_context.model_copy(
        update={
            "enabled": wren_context.enabled or indexed_context.enabled,
            "available": wren_context.available or indexed_context.available,
            "document_ids": sorted(
                {
                    *wren_context.document_ids,
                    *indexed_context.document_ids,
                }
            ),
            "semantic_layer_version": indexed_context.semantic_layer_version,
            "indexing_status": indexed_context.indexing_status,
            "context_items": [
                *wren_context.context_items,
                *indexed_context.context_items,
            ],
            "warnings": [
                *wren_context.warnings,
                *indexed_context.warnings,
            ],
        }
    )
