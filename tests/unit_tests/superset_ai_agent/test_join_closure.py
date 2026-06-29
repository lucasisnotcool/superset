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

"""Cross-schema join-closure (plan_cross_schema_context_ranking_impl.md, Fix A).

A relevant join partner whose own items ranked out of selection must still reach
the prompt (model + columns + the connecting relationship), so the LLM can write
the cross-schema join instead of seeing a dangling "A joins B".
"""

from __future__ import annotations

from typing import Any

from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.runtime import (
    apply_join_closure,
    build_unified_context,
    relationship_partners,
)


def _model(name: str, source: str = "manifest") -> dict[str, Any]:
    return {
        "source": source,
        "kind": "model",
        "name": name,
        "model": name,
        "text": f"model {name}",
        "related_models": [],
    }


def _col(model: str, col: str, source: str = "manifest") -> dict[str, Any]:
    return {
        "source": source,
        "kind": "column",
        "name": col,
        "model": model,
        "text": f"{model}.{col}",
        "related_models": [],
    }


def _rel(name: str, a: str, b: str, source: str = "manifest") -> dict[str, Any]:
    return {
        "source": source,
        "kind": "relationship",
        "name": name,
        "model": None,
        "text": f"relationship {name} joins {a}, {b}",
        "related_models": [a, b],
    }


# A 2-schema project: orders/order_items in `public`, customers in `crm`.
# Manifest = the full unranked set; the retriever only surfaced `orders`.
MANIFEST: list[dict[str, Any]] = [
    _model("orders"),
    _col("orders", "id"),
    _col("orders", "customer_id"),
    _model("crm_customers"),
    _col("crm_customers", "id"),
    _col("crm_customers", "region"),
    _rel("orders_customer", "orders", "crm_customers"),
]


def _names(items: list[dict[str, Any]]) -> set[str]:
    return {i.get("model") or i.get("name") for i in items}


def _ctx() -> WrenContextArtifact:
    return WrenContextArtifact(enabled=True, available=True, context_items=[])


def _build(manifest, limit):
    # The retriever surfaced only `orders` (+ a col) and the relationship chunk;
    # the partner `crm_customers` is NOT in the retrieved set.
    retrieved = [
        _model("orders", source="retriever"),
        _col("orders", "customer_id", source="retriever"),
        _rel("orders_customer", "orders", "crm_customers", source="retriever"),
    ]
    return build_unified_context(
        wren_context=_ctx(),
        retrieved_items=retrieved,
        table_selection_limit=5,
        max_context_items=0,  # unlimited — isolate closure from the count cap
        manifest_items=manifest,
        join_closure_limit=limit,
    )


def test_without_closure_partner_is_pruned() -> None:
    # Baseline (closure off): the cross-schema partner's columns never appear.
    result = _build(MANIFEST, limit=0)
    names = _names(result.context_items)
    assert "orders" in names
    assert "crm_customers" not in names


def test_closure_injects_cross_schema_partner() -> None:
    result = _build(MANIFEST, limit=5)
    items = result.context_items
    names = _names(items)
    # The partner model AND its columns are now present...
    assert "crm_customers" in names
    cols = {i["name"] for i in items if i.get("model") == "crm_customers"}
    assert {"id", "region"} <= cols
    # ...injected items are tagged so the cap protects them.
    injected = [i for i in items if i.get("model") == "crm_customers"]
    assert all(i["source"] == "closure" for i in injected)
    # ...and the connecting relationship is present for the join condition.
    assert any(i["kind"] == "relationship" for i in items)


def test_single_schema_no_op() -> None:
    # No relationship crosses the selection boundary → output unchanged (R3).
    manifest = [_model("orders"), _col("orders", "id")]
    retrieved = [_model("orders", source="retriever")]
    base = build_unified_context(
        wren_context=_ctx(),
        retrieved_items=retrieved,
        table_selection_limit=5,
        max_context_items=0,
        manifest_items=manifest,
        join_closure_limit=5,
    )
    assert _names(base.context_items) == {"orders"}


def test_relationship_partners_one_hop_boundary() -> None:
    partners, connecting = relationship_partners({"orders"}, MANIFEST)
    assert partners == ["crm_customers"]
    assert [c["name"] for c in connecting] == ["orders_customer"]
    # Fully-selected or fully-unselected relationships contribute nothing.
    assert relationship_partners({"orders", "crm_customers"}, MANIFEST) == ([], [])
    assert relationship_partners({"unrelated"}, MANIFEST) == ([], [])


def test_closure_budget_caps_partners() -> None:
    # orders joins three partners; limit=2 keeps two and drops the rest.
    manifest = [
        _model("orders"),
        _col("orders", "id"),
        *[_model(f"dim_{n}") for n in ("a", "b", "c")],
        *[_col(f"dim_{n}", "id") for n in ("a", "b", "c")],
        _rel("r_a", "orders", "dim_a"),
        _rel("r_b", "orders", "dim_b"),
        _rel("r_c", "orders", "dim_c"),
    ]
    selected = [_model("orders", source="retriever")]
    result = apply_join_closure(selected, manifest, limit=2)
    partner_names = {i.get("model") for i in result if i["source"] == "closure"}
    assert len([n for n in partner_names if n and n.startswith("dim_")]) == 2


def test_closure_runs_after_llm_selector_path() -> None:
    # An LLM selector that picks only `orders` still gets its partner closed in.
    retrieved = [
        _model("orders", source="retriever"),
        _model("crm_customers", source="retriever"),
        _col("crm_customers", "region", source="retriever"),
        _rel("orders_customer", "orders", "crm_customers", source="retriever"),
    ]
    result = build_unified_context(
        wren_context=_ctx(),
        retrieved_items=retrieved,
        table_selection_limit=5,
        max_context_items=0,
        model_selector=lambda candidates: ["orders"],  # prune crm_customers
        manifest_items=MANIFEST,
        join_closure_limit=5,
    )
    assert "crm_customers" in _names(result.context_items)
