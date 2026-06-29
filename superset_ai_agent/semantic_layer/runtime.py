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
import logging
from collections.abc import Callable
from typing import Any

from superset_ai_agent.schemas import WrenContextArtifact

logger = logging.getLogger(__name__)


def canonical_model_name(item: dict[str, Any]) -> str | None:
    """The model a context item belongs to, across heterogeneous shapes (C1.1).

    Retriever chunks carry ``model`` as a **name string**; ``fetch_context`` model
    items carry it as a ``{"type": "model", "model": {...body...}}`` dict. Unifying
    the extraction here lets table-selection prune *both* by table (the unified set),
    not just the retriever output — the blocker that previously confined selection to
    the homogeneous retriever items. Returns ``None`` for model-less items
    (relationships, doc notes), which selection always preserves. Non-destructive:
    callers read the name but the item body (e.g. the fetch_context column dict) is
    left intact for the prompt.
    """

    model = item.get("model")
    if isinstance(model, str) and model:
        return model
    if item.get("type") == "model" and isinstance(model, dict):
        name = model.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def candidate_model_names(items: list[dict[str, Any]]) -> list[str]:
    """Distinct model names across the unified items, in rank (document) order."""

    names: list[str] = []
    for item in items:
        name = canonical_model_name(item)
        if name and name not in names:
            names.append(name)
    return names


def _filter_to_models(
    items: list[dict[str, Any]], allowed: set[str]
) -> list[dict[str, Any]]:
    """Keep items belonging to an allowed model; model-less items always survive."""

    return [
        item
        for item in items
        if (name := canonical_model_name(item)) is None or name in allowed
    ]


def select_relevant_models(
    items: list[dict[str, Any]], max_models: int
) -> list[dict[str, Any]]:
    """Heuristic table-selection prune (R2, wren_enrich_and_retrieve.md).

    Operates on relevance-ranked schema items (already ordered most-relevant first).
    Picks the first ``max_models`` distinct model names in rank order — across the
    unified shapes via :func:`canonical_model_name` (C1.1) — and keeps only items
    belonging to a selected model; model-less items (e.g. relationship chunks) are
    always preserved. This mirrors Wren's table-selection step — narrowing to a
    coherent set of tables rather than an arbitrary count cut. A no-op when
    ``max_models <= 0`` or there is no model signal (degrade-closed: never drops
    everything). The degrade-closed fallback for the LLM selector (C1.3).
    """

    if max_models <= 0:
        return items
    candidates = candidate_model_names(items)
    if not candidates:
        return items
    return _filter_to_models(items, set(candidates[:max_models]))


#: A model-selection strategy (C1.3): given the candidate model names (rank order),
#: return the chosen subset, or ``None`` to defer to the heuristic. The callable owns
#: validating its output against the candidates and honoring the selection limit.
ModelSelector = Callable[[list[str]], "list[str] | None"]


def build_unified_context(
    *,
    wren_context: WrenContextArtifact,
    retrieved_items: list[dict[str, Any]],
    table_selection_limit: int,
    max_context_items: int,
    model_selector: ModelSelector | None = None,
    manifest_items: list[dict[str, Any]] | None = None,
    join_closure_limit: int = 0,
) -> WrenContextArtifact:
    """Single post-retrieval context entrypoint (C1.2 / C1.3).

    Collapses the previously-inline merge → select → cap steps into one path: the
    already-fetched ``fetch_context`` context and the retriever chunks are unified
    into **one** list, table-selection runs over that
    unified set (C1.1 — pruning ``fetch_context`` model items too, not just the
    retriever output), then the result is deduped and capped. The three retrieval
    *sources* still feed this one pipeline. ``retrieval_mode``/
    ``retrieved_item_count`` reflect the retriever chunks that survive selection.

    When ``model_selector`` is supplied (C1.3) it picks the relevant model subset
    (Wren's LLM table/column selection); on ``None``/empty/failure selection degrades
    closed to the heuristic :func:`select_relevant_models`.

    Retriever chunks lead the merged list so their relevance-ranked models win the
    table-selection budget ahead of the legacy keyword ``fetch_context`` models —
    consistent with :func:`cap_context_items`, which prioritizes retriever chunks on
    overflow. Avoids regressing the better-ranked source when both are active.
    """

    merged = [*retrieved_items, *wren_context.context_items]
    selected = _select_models(merged, table_selection_limit, model_selector)
    # Cross-schema join-closure: pull in join partners of the selected models so a
    # relevant join never loses one side (the partner's columns/table). Runs for
    # BOTH the heuristic and LLM-selector paths since it follows _select_models.
    selected = apply_join_closure(selected, manifest_items or [], join_closure_limit)
    capped = cap_context_items(selected, max_context_items)
    update: dict[str, Any] = {"context_items": capped}
    if retrieved_items:
        update["retrieval_mode"] = retrieved_items[0].get("retriever")
        update["retrieved_item_count"] = sum(
            1 for item in capped if item.get("source") == "retriever"
        )
    return wren_context.model_copy(update=update)


def relationship_partners(
    selected_names: set[str], manifest_items: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Join partners of the selected models, one hop, in first-seen order.

    Scans the manifest's relationship items (``kind == "relationship"``, endpoints
    in ``related_models``); a relationship that crosses the selected/unselected
    boundary contributes its unselected endpoint(s) as partners and is returned as
    a *connecting* relationship (so its join condition reaches the prompt). Pure;
    applies no budget — the caller caps.
    """

    partners: list[str] = []
    connecting: list[dict[str, Any]] = []
    for item in manifest_items:
        if item.get("kind") != "relationship":
            continue
        ends = [str(name) for name in (item.get("related_models") or []) if name]
        in_sel = [name for name in ends if name in selected_names]
        out_sel = [name for name in ends if name not in selected_names]
        if not in_sel or not out_sel:
            continue  # fully-in (already covered) or fully-out (irrelevant) join
        connecting.append(item)
        for name in out_sel:
            if name not in partners:
                partners.append(name)
    return partners, connecting


def apply_join_closure(
    selected: list[dict[str, Any]],
    manifest_items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Append join-partner items (model + columns + connecting relationship) for
    partners reachable from the selected models in one hop.

    No-op (returns ``selected`` unchanged) when ``limit <= 0``, there are no
    manifest items, or no relationship crosses the selection boundary — so
    single-schema / small projects are unaffected. Partners are capped at
    ``limit`` (logged on truncation — no silent cap). Injected items are tagged
    ``source="closure"`` so :func:`cap_context_items` protects them on overflow.
    """

    if limit <= 0 or not manifest_items:
        return selected
    selected_names = set(candidate_model_names(selected))
    if not selected_names:
        return selected
    partners, connecting = relationship_partners(selected_names, manifest_items)
    if not partners:
        return selected
    if len(partners) > limit:
        logger.info(
            "Join-closure truncated: %d partner models found, keeping %d "
            "(wren_join_closure_limit). Dropped: %s",
            len(partners),
            limit,
            partners[limit:],
        )
        partners = partners[:limit]
    kept = set(partners)
    items_by_model: dict[str, list[dict[str, Any]]] = {}
    for item in manifest_items:
        name = canonical_model_name(item)
        if name in kept:
            items_by_model.setdefault(name, []).append(item)
    injected: list[dict[str, Any]] = []
    for partner in partners:
        injected.extend(items_by_model.get(partner, []))
    for rel in connecting:
        ends = {str(name) for name in (rel.get("related_models") or []) if name}
        if ends & kept:
            injected.append(rel)
    tagged = [{**item, "source": "closure"} for item in injected]
    return [*selected, *tagged]


def _select_models(
    items: list[dict[str, Any]],
    limit: int,
    model_selector: ModelSelector | None,
) -> list[dict[str, Any]]:
    """Apply the LLM selector when present, else the heuristic (degrade-closed)."""

    if model_selector is not None:
        candidates = candidate_model_names(items)
        if candidates:
            chosen = model_selector(candidates)
            if chosen:
                return _filter_to_models(items, set(chosen))
    return select_relevant_models(items, limit)


def cap_context_items(
    items: list[dict[str, Any]], max_items: int
) -> list[dict[str, Any]]:
    """Dedup + bound merged prompt context items (wren_full.md R-RET-E).

    The context sources (MDL retriever chunks and ``fetch_context``) concatenate
    with no combined budget, so a wide schema could inflate the prompt.
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
    # Keep the relevance-ranked retriever chunks first, then join-closure items
    # (both sides of a relevant join must survive overflow), then the rest.
    priority = {"retriever": 0, "closure": 1}
    ranked = [item for item in deduped if item.get("source") in priority]
    ranked.sort(key=lambda item: priority.get(str(item.get("source") or ""), 9))
    other = [item for item in deduped if item.get("source") not in priority]
    return (ranked + other)[:max_items]
