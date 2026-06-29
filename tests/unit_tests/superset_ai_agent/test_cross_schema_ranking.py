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

"""Cross-schema context: structured relationship endpoints (Phase 1) and the
schema-neutral matched-models parity boost (Phase 3)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.integrations.wren.client import FileWrenClient
from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
from superset_ai_agent.semantic_layer.mdl_files import (
    InMemoryMdlFileStore,
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
)
from superset_ai_agent.semantic_layer.schema_retriever import (
    manifest_to_schema_items,
    project_schema_items,
)

_CONTENT = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "customer_id", "type": "BIGINT"},
                ],
            },
            {
                "name": "crm_customers",
                "tableReference": {"schema": "crm", "table": "customers"},
                "columns": [{"name": "id", "type": "BIGINT"}],
            },
        ],
        "relationships": [
            {
                "name": "orders_customer",
                "models": ["orders", "crm_customers"],
                "joinType": "MANY_TO_ONE",
                "condition": "orders.customer_id = crm_customers.id",
            }
        ],
    }
)


# --- Phase 1: structured endpoints -------------------------------------------


def test_relationship_item_carries_related_models() -> None:
    items = manifest_to_schema_items(compile_manifest(json_contents=[_CONTENT]))
    rels = [item for item in items if item.kind == "relationship"]
    assert len(rels) == 1
    assert rels[0].related_models == ["orders", "crm_customers"]
    # Models/columns carry none.
    assert all(
        item.related_models == [] for item in items if item.kind != "relationship"
    )


def test_project_schema_items_returns_full_manifest_with_endpoints() -> None:
    store = InMemoryMdlFileStore()
    created = store.create(
        "proj-1",
        MdlFileCreateRequest(path="models/all.json", content=_CONTENT),
        owner_id="owner",
    )
    store.update(created.id, MdlFileUpdateRequest(status="active"), owner_id="owner")

    items = project_schema_items(
        project_id="proj-1", owner_id="owner", mdl_file_store=store
    )
    kinds = {item["kind"] for item in items}
    assert {"model", "column", "relationship"} <= kinds
    assert all(item["source"] == "manifest" for item in items)
    rel = next(item for item in items if item["kind"] == "relationship")
    assert rel["related_models"] == ["orders", "crm_customers"]
    # Degrade-closed: no project → empty (never raises).
    assert (
        project_schema_items(project_id=None, owner_id="owner", mdl_file_store=store)
        == []
    )


# --- Phase 3: schema-neutral matched-models boost -----------------------------


def _context(table_names: list[str]) -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="db"),
        datasets=[
            DatasetMetadata(
                id=i,
                table_name=name,
                schema_name="public",
                database_id=1,
                columns=[ColumnSummary(name="id", type="BIGINT")],
                metrics=[],
            )
            for i, name in enumerate(table_names)
        ],
    )


def test_matched_models_ranks_question_named_cross_schema_model_first() -> None:
    client = FileWrenClient(AgentConfig())
    mdl = json.loads(_CONTENT)
    # The current schema only knows `orders` (single-schema dataset context), but
    # the question is about customers — a model whose physical table is in `crm`.
    matched = client._matched_models(
        question="how many customers are there",
        superset_context=_context(["orders"]),
        mdl=mdl,
    )
    # The cross-schema model wins on the schema-neutral parity boost, despite not
    # being in the current schema's dataset set.
    assert matched[0] == "crm_customers"


def test_matched_models_still_boosts_current_schema_browsing() -> None:
    client = FileWrenClient(AgentConfig())
    mdl = json.loads(_CONTENT)
    # A neutral question: the actively-browsed current-schema table still ranks up.
    matched = client._matched_models(
        question="show data",
        superset_context=_context(["orders"]),
        mdl=mdl,
    )
    assert matched[0] == "orders"


# --- Phase 3: dataset boost reaches secondary-schema models in the union ------

_TWO_SCHEMA_MDL = {
    "models": [
        {
            "name": "orders",
            "tableReference": {"schema": "public", "table": "orders"},
            "columns": [{"name": "id", "type": "BIGINT"}],
        },
        {
            "name": "customers",
            "tableReference": {"schema": "crm", "table": "customers"},
            "columns": [{"name": "id", "type": "BIGINT"}],
        },
    ]
}


def _ctx_tables(tables: list[tuple[str, str]]) -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="db"),
        datasets=[
            DatasetMetadata(
                id=i,
                table_name=name,
                schema_name=schema,
                database_id=1,
                columns=[ColumnSummary(name="id", type="BIGINT")],
                metrics=[],
            )
            for i, (name, schema) in enumerate(tables)
        ],
    )


def test_dataset_boost_reaches_secondary_schema_only_in_union() -> None:
    client = FileWrenClient(AgentConfig())
    # Single-schema context (pre-Fix-C): only public.orders is loaded, so the crm
    # model misses the "actively browsing" +3 dataset boost and the question word.
    single = _ctx_tables([("orders", "public")])
    assert "customers" not in client._matched_models(
        question="orders", superset_context=single, mdl=_TWO_SCHEMA_MDL
    )
    # Unioned multi-schema context (Fix C, Phase 2): crm.customers is now loaded,
    # so its model earns the +3 dataset boost and reaches matched_models.
    union = _ctx_tables([("orders", "public"), ("customers", "crm")])
    assert "customers" in client._matched_models(
        question="orders", superset_context=union, mdl=_TWO_SCHEMA_MDL
    )


def test_scope_hash_distinguishes_multi_schema_scope() -> None:
    from superset_ai_agent.conversations.schemas import ConversationScope
    from superset_ai_agent.semantic_layer.store import scope_hash

    single = ConversationScope(database_id=1, schema_name="public")
    multi = ConversationScope(
        database_id=1, schema_name="public", schema_names=["public", "crm"]
    )
    # A multi-schema scope hashes distinctly (memory/instructions key on the set)...
    assert scope_hash(single) != scope_hash(multi)
    # ...order-independently...
    multi_reordered = ConversationScope(
        database_id=1, schema_name="public", schema_names=["crm", "public"]
    )
    assert scope_hash(multi) == scope_hash(multi_reordered)
    # ...and a single-schema set stays byte-identical to the legacy scalar shape.
    same = ConversationScope(
        database_id=1, schema_name="public", schema_names=["public"]
    )
    assert scope_hash(same) == scope_hash(single)


# --- Phase 4: direct-SQL prompt instructs schema qualification -----------------


def test_text_to_sql_prompt_instructs_cross_schema_qualification() -> None:
    # Regression guard: the passthrough/direct-SQL path relies on the prompt to
    # qualify tables when the datasets span schemas (the wren_core path qualifies
    # via the engine rewrite instead).
    from superset_ai_agent.prompts.registry import get_prompt

    prompt = get_prompt("text_to_sql").lower()
    assert "schema.table" in prompt
    assert "more than one schema" in prompt or "span" in prompt
