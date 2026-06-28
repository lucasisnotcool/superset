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

"""Multi-schema onboarding: cross-schema base models and D4 name collisions."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.integrations.wren.client import (
    deterministic_base_model_proposals,
)
from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
from superset_ai_agent.semantic_layer.onboarding import onboard_schema_project
from superset_ai_agent.semantic_layer.schemas import SemanticProject


class _StubWrenClient:
    """Minimal client satisfying SupportsBaseModelGeneration for onboarding."""

    def generate_base_model(self, *, project, superset_context):
        return deterministic_base_model_proposals(
            project=project, superset_context=superset_context
        )


def _dataset(
    ident: int, table: str, schema: str, columns: list[str]
) -> DatasetMetadata:
    return DatasetMetadata(
        id=ident,
        table_name=table,
        schema_name=schema,
        database_id=1,
        columns=[ColumnSummary(name=name, type="VARCHAR") for name in columns],
        metrics=[],
    )


def _context(*datasets: DatasetMetadata) -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="db"), datasets=list(datasets)
    )


def _project() -> SemanticProject:
    return SemanticProject(
        name="proj",
        owner_id="owner",
        database_uri_fingerprint="fp",
        schema_name="sales",
        schema_names=["sales", "crm"],
        default_database_id=1,
    )


def test_base_models_carry_per_schema_table_reference() -> None:
    context = _context(
        _dataset(1, "orders", "sales", ["id"]),
        _dataset(2, "customers", "crm", ["id", "name"]),
    )
    proposals = deterministic_base_model_proposals(
        project=_project(), superset_context=context
    )
    schemas = {
        json.loads(p.proposed_content)["models"][0]["tableReference"]["schema"]
        for p in proposals
    }
    assert schemas == {"sales", "crm"}


def test_colliding_table_names_get_schema_disambiguated_model_names() -> None:
    # Same physical table name in two schemas — logical names must not collapse.
    context = _context(
        _dataset(1, "orders", "sales", ["id"]),
        _dataset(2, "orders", "archive", ["id"]),
    )
    proposals = deterministic_base_model_proposals(
        project=_project(), superset_context=context
    )
    names = sorted(
        json.loads(p.proposed_content)["models"][0]["name"] for p in proposals
    )
    assert names == ["archive_orders", "sales_orders"]
    # Both physical tables remain addressable via their tableReference.
    table_refs = {
        (
            json.loads(p.proposed_content)["models"][0]["tableReference"]["schema"],
            json.loads(p.proposed_content)["models"][0]["tableReference"]["table"],
        )
        for p in proposals
    }
    assert table_refs == {("sales", "orders"), ("archive", "orders")}


def test_single_schema_names_stay_unprefixed() -> None:
    context = _context(_dataset(1, "orders", "sales", ["id"]))
    proposals = deterministic_base_model_proposals(
        project=_project(), superset_context=context
    )
    assert json.loads(proposals[0].proposed_content)["models"][0]["name"] == "orders"


def test_onboard_activates_cross_schema_models() -> None:
    context = _context(
        _dataset(1, "orders", "sales", ["id"]),
        _dataset(2, "customers", "crm", ["id", "name"]),
    )
    store = InMemoryMdlFileStore()
    result = onboard_schema_project(
        project=_project(),
        superset_context=context,
        wren_client=_StubWrenClient(),
        mdl_file_store=store,
        owner_id="owner",
    )
    assert result.model_count == 2
    # Both cross-schema base models validate against the union SchemaIndex and
    # auto-activate.
    assert result.activated_count == 2
