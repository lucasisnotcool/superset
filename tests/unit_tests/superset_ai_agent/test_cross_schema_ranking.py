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
