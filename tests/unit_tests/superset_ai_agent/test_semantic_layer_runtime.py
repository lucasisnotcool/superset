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

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.runtime import (
    build_unified_context,
    canonical_model_name,
    cap_context_items,
    merge_indexed_semantic_context,
    select_relevant_models,
)
from superset_ai_agent.semantic_layer.schemas import SemanticLayerVersion
from superset_ai_agent.semantic_layer.store import scope_hash


def test_cap_context_items_dedups_and_prioritizes_retriever_chunks() -> None:
    items = [
        {"source": "fetch", "text": "a"},
        {"source": "fetch", "text": "a"},  # exact duplicate → deduped
        {"source": "doc", "text": "b"},
        {"source": "retriever", "text": "r1"},
        {"source": "retriever", "text": "r2"},
    ]
    # Generous cap: only dedup, order preserved.
    assert cap_context_items(items, 0) == [
        {"source": "fetch", "text": "a"},
        {"source": "doc", "text": "b"},
        {"source": "retriever", "text": "r1"},
        {"source": "retriever", "text": "r2"},
    ]
    # Tight cap: retrieval-ranked chunks win, others fill the remainder.
    capped = cap_context_items(items, 2)
    assert capped == [
        {"source": "retriever", "text": "r1"},
        {"source": "retriever", "text": "r2"},
    ]


def test_merge_indexed_semantic_context_combines_runtime_and_indexed_items() -> None:
    scope = ConversationScope(database_id=1, schema_name="sales")
    store = InMemorySemanticLayerStore()
    store.save_version(
        SemanticLayerVersion(
            scope=scope,
            scope_hash=scope_hash(scope),
            version="v1",
            status="idle",
            wren_context=WrenContextArtifact(
                enabled=True,
                available=True,
                document_ids=["doc-2"],
                semantic_layer_version="v1",
                indexing_status="indexed",
                context_items=[{"kind": "indexed"}],
                warnings=["indexed warning"],
            ),
        ),
        owner_id="analyst",
    )

    merged = merge_indexed_semantic_context(
        semantic_layer_store=store,
        scope=scope,
        owner_id="analyst",
        wren_context=WrenContextArtifact(
            enabled=True,
            available=True,
            document_ids=["doc-1"],
            context_items=[{"kind": "runtime"}],
            warnings=["runtime warning"],
        ),
    )

    assert merged.document_ids == ["doc-1", "doc-2"]
    assert merged.semantic_layer_version == "v1"
    assert merged.indexing_status == "indexed"
    assert merged.context_items == [{"kind": "runtime"}, {"kind": "indexed"}]
    assert merged.warnings == ["runtime warning", "indexed warning"]


def test_merge_indexed_semantic_context_noops_without_matching_version() -> None:
    scope = ConversationScope(database_id=1, schema_name="sales")
    context = WrenContextArtifact(enabled=True, available=True)

    assert (
        merge_indexed_semantic_context(
            semantic_layer_store=InMemorySemanticLayerStore(),
            scope=scope,
            owner_id="analyst",
            wren_context=context,
        )
        == context
    )


def test_merge_indexed_semantic_context_skips_overlay_when_disabled() -> None:
    # E1/E6: with the overlay disabled, the doc-update channel is skipped entirely
    # even when an indexed version exists — MDL is the single semantic source.
    scope = ConversationScope(database_id=1, schema_name="sales")
    store = InMemorySemanticLayerStore()
    store.save_version(
        SemanticLayerVersion(
            scope=scope,
            scope_hash=scope_hash(scope),
            version="v1",
            status="idle",
            wren_context=WrenContextArtifact(
                enabled=True,
                available=True,
                context_items=[{"kind": "indexed"}],
            ),
        ),
        owner_id="analyst",
    )
    context = WrenContextArtifact(
        enabled=True, available=True, context_items=[{"kind": "runtime"}]
    )

    merged = merge_indexed_semantic_context(
        semantic_layer_store=store,
        scope=scope,
        owner_id="analyst",
        wren_context=context,
        enabled=False,
    )

    assert merged == context  # overlay not merged
    assert merged.context_items == [{"kind": "runtime"}]


# --- R2: table-selection prune over ranked retriever chunks -------------------


def _retriever_item(model: str, kind: str, name: str) -> dict:
    return {"source": "retriever", "kind": kind, "name": name, "model": model}


def test_select_relevant_models_keeps_top_models_and_drops_others() -> None:
    # Ranked order: alpha (most relevant), beta, gamma. With a limit of 2, gamma's
    # chunks are dropped; alpha + beta (and their columns) survive in order.
    items = [
        _retriever_item("alpha", "model", "alpha"),
        _retriever_item("alpha", "column", "id"),
        _retriever_item("beta", "model", "beta"),
        {"source": "retriever", "kind": "relationship", "name": "a_b", "model": None},
        _retriever_item("gamma", "model", "gamma"),
        _retriever_item("gamma", "column", "x"),
    ]

    selected = select_relevant_models(items, 2)

    models = {item["model"] for item in selected if isinstance(item["model"], str)}
    assert models == {"alpha", "beta"}  # gamma pruned
    # The model-less relationship chunk is always preserved.
    assert any(item["kind"] == "relationship" for item in selected)
    # Order is preserved (no reshuffle).
    assert [item["name"] for item in selected] == ["alpha", "id", "beta", "a_b"]


def test_select_relevant_models_is_noop_when_limit_zero() -> None:
    items = [
        _retriever_item("alpha", "model", "alpha"),
        _retriever_item("beta", "model", "beta"),
    ]
    assert select_relevant_models(items, 0) == items


def test_select_relevant_models_noop_when_within_limit_mixed_shapes() -> None:
    # One model (a fetch_context dict-model) within the limit + a model-less note →
    # all preserved (never empty). The dict-model is now a recognized signal (C1.1),
    # but with one model under the cap nothing is pruned.
    items = [
        {"source": "doc", "kind": "note", "name": "n1"},
        {"source": "fetch", "type": "model", "model": {"name": "x"}},
    ]
    assert select_relevant_models(items, 3) == items


def test_select_relevant_models_keeps_all_when_within_limit() -> None:
    items = [
        _retriever_item("alpha", "model", "alpha"),
        _retriever_item("beta", "model", "beta"),
    ]
    assert select_relevant_models(items, 5) == items


# --- C1.1: unified model-name extraction + selection over mixed shapes --------


def test_canonical_model_name_across_shapes() -> None:
    # retriever chunk: model is a name string
    assert canonical_model_name(_retriever_item("alpha", "model", "alpha")) == "alpha"
    # fetch_context model item: model is a dict body
    assert (
        canonical_model_name({"type": "model", "model": {"name": "deals"}}) == "deals"
    )
    # model-less items → None (always preserved by selection)
    assert canonical_model_name({"type": "relationships", "items": []}) is None
    assert canonical_model_name({"source": "doc", "kind": "note"}) is None
    # a dict model without a usable name → None
    assert canonical_model_name({"type": "model", "model": {}}) is None


def test_select_prunes_fetch_context_dict_models_too() -> None:
    # The C1.1 win: a fetch_context dict-model now participates in selection and can
    # be pruned by table, not just retriever chunks. alpha (retriever) + beta
    # (fetch_context dict) are kept; gamma (fetch_context dict) is pruned at limit 2.
    items = [
        _retriever_item("alpha", "model", "alpha"),
        {"type": "model", "model": {"name": "beta"}},
        {"type": "model", "model": {"name": "gamma"}},
    ]
    selected = select_relevant_models(items, 2)
    names = {canonical_model_name(item) for item in selected}
    assert names == {"alpha", "beta"}
    # The pruned item kept its body for any item that survived (non-destructive).
    assert {"type": "model", "model": {"name": "beta"}} in selected


# --- C1.2: one post-retrieval entrypoint --------------------------------------


def test_build_unified_context_merges_selects_and_caps() -> None:
    wren_context = WrenContextArtifact(
        enabled=True,
        available=True,
        context_items=[
            {"type": "model", "model": {"name": "alpha"}},
            {"type": "model", "model": {"name": "zeta"}},  # pruned at limit 1
        ],
    )
    retrieved = [
        {"source": "retriever", "retriever": "embedding", "kind": "column",
         "name": "id", "model": "alpha", "text": "alpha.id"},
    ]
    out = build_unified_context(
        wren_context=wren_context,
        retrieved_items=retrieved,
        table_selection_limit=1,  # keep only the top model (alpha, seen first)
        max_context_items=0,
    )
    names = {canonical_model_name(item) for item in out.context_items}
    assert names == {"alpha"}  # zeta pruned across the unified set
    # Retrieval metadata reflects the surviving retriever chunk.
    assert out.retrieval_mode == "embedding"
    assert out.retrieved_item_count == 1


def test_build_unified_context_overlay_only_passthrough() -> None:
    # Overlay-only context (no retriever items) is passed through unchanged, and no
    # retrieval metadata is stamped (parity with the prior inline behavior).
    wren_context = WrenContextArtifact(
        enabled=True,
        available=True,
        context_items=[{"kind": "document", "name": "terms"}],
    )
    out = build_unified_context(
        wren_context=wren_context,
        retrieved_items=[],
        table_selection_limit=5,
        max_context_items=0,
    )
    assert out.context_items == [{"kind": "document", "name": "terms"}]
    assert out.retrieval_mode is None
    assert out.retrieved_item_count == 0


# --- C1.3: LLM table/column selection -----------------------------------------


def _three_model_context() -> WrenContextArtifact:
    return WrenContextArtifact(
        enabled=True,
        available=True,
        context_items=[
            {"type": "model", "model": {"name": "alpha"}},
            {"type": "model", "model": {"name": "beta"}},
            {"type": "model", "model": {"name": "gamma"}},
            {
                "source": "retriever",
                "kind": "relationship",
                "name": "rel",
                "model": None,
            },
        ],
    )


def test_build_unified_context_uses_model_selector() -> None:
    # The LLM selector picks beta only; alpha/gamma pruned, model-less rel kept.
    seen: list[list[str]] = []

    def selector(candidates: list[str]) -> list[str] | None:
        seen.append(candidates)
        return ["beta"]

    out = build_unified_context(
        wren_context=_three_model_context(),
        retrieved_items=[],
        table_selection_limit=5,
        max_context_items=0,
        model_selector=selector,
    )
    names = {canonical_model_name(item) for item in out.context_items}
    assert names == {"beta", None}  # beta + the model-less relationship
    # The selector saw all distinct candidate names in order.
    assert seen == [["alpha", "beta", "gamma"]]


def test_build_unified_context_selector_none_falls_back_to_heuristic() -> None:
    def selector(candidates: list[str]) -> list[str] | None:
        return None  # selector unavailable/failed

    out = build_unified_context(
        wren_context=_three_model_context(),
        retrieved_items=[],
        table_selection_limit=1,  # heuristic keeps only the first model
        max_context_items=0,
        model_selector=selector,
    )
    names = {canonical_model_name(item) for item in out.context_items}
    assert names == {"alpha", None}  # heuristic top-1 + relationship


def test_build_unified_context_selector_empty_falls_back() -> None:
    out = build_unified_context(
        wren_context=_three_model_context(),
        retrieved_items=[],
        table_selection_limit=2,
        max_context_items=0,
        model_selector=lambda _candidates: [],  # empty → defer to heuristic
    )
    names = {canonical_model_name(item) for item in out.context_items}
    assert names == {"alpha", "beta", None}  # heuristic top-2 + relationship
