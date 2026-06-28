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

"""Seed-robustness regression suite (D-A identity + D-B types).

Locks the two onboarding defect classes from
``plan_onboarding_seed_robustness_spec.md``: non-identifier physical column names
and typeless catalog columns. Every column the ``SchemaIndex`` knows about must be
``has_column``-resolvable from the emitted seed (the round-trip invariant), and a
type must never be guessed.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.integrations.wren.client import (
    deterministic_base_model_proposals,
)
from superset_ai_agent.integrations.wren.mdl_exporter import (
    column_to_field,
    model_from_dataset,
)
from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex, validate_mdl
from superset_ai_agent.semantic_layer.onboarding import onboard_schema_project
from superset_ai_agent.semantic_layer.schemas import SemanticProject


class _StubWrenClient:
    """Deterministic base-model generation (no LLM), like the onboarding path."""

    def generate_base_model(self, *, project, superset_context):
        return deterministic_base_model_proposals(
            project=project, superset_context=superset_context
        )


def _project() -> SemanticProject:
    return SemanticProject(
        name="proj",
        owner_id="owner",
        database_uri_fingerprint="fp",
        schema_name="public",
        schema_names=["public"],
        default_database_id=1,
    )


def _context(columns: list[ColumnSummary], table: str = "wide") -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="examples", backend="postgresql"),
        datasets=[
            DatasetMetadata(
                id=7,
                table_name=table,
                schema_name="public",
                database_id=1,
                description=None,
                columns=columns,
                metrics=[],
            )
        ],
    )


def _validate_seed(context: AgentContext) -> tuple[dict[str, Any], list[str]]:
    """Seed one model deterministically and validate it against the live index."""

    dataset = context.datasets[0]
    model = model_from_dataset(dataset)
    index = SchemaIndex.from_agent_context(context)
    result = validate_mdl(json.dumps({"models": [model]}), schema_index=index)
    errors = [m.message for m in result.messages if m.severity == "error"]
    return model, errors


# --- D-A: identifier round-trip -------------------------------------------------


def test_leading_digit_columns_round_trip() -> None:
    # The historical failure: columns named 2003..2005 became _2003.. and were
    # reported "does not exist". Now they sanitize, carry a physical expression,
    # and validate cleanly.
    cols = [ColumnSummary(name=str(year), type="BIGINT") for year in (2003, 2004, 2005)]
    model, errors = _validate_seed(_context(cols))

    names = [c["name"] for c in model["columns"]]
    assert names == ["_2003", "_2004", "_2005"]
    # Each renamed column carries the quoted physical reference + retained raw name.
    first = model["columns"][0]
    assert first["expression"] == '"2003"'
    assert first["properties"]["superset_column_name"] == "2003"
    assert errors == []  # no false "does not exist"


def test_special_char_columns_round_trip() -> None:
    cols = [
        ColumnSummary(name="% growth", type="DOUBLE"),
        ColumnSummary(name="first-time_developer", type="VARCHAR"),
    ]
    model, errors = _validate_seed(_context(cols))

    assert model["columns"][0]["name"] == "growth"
    assert model["columns"][0]["expression"] == '"% growth"'
    assert model["columns"][1]["name"] == "first_time_developer"
    assert errors == []


def test_plain_columns_emit_no_expression() -> None:
    # A clean name must NOT get a redundant expression (keeps MDL lean).
    field = column_to_field(ColumnSummary(name="created_at", type="TIMESTAMP"))
    assert "expression" not in field
    assert field["name"] == "created_at"


# --- D-B: type resolution / fail-closed ----------------------------------------


def test_typeless_column_resolved_from_generic_family_activates() -> None:
    # Catalog left `type` empty but knows the generic family → concrete type, and
    # the model validates (no "missing a type").
    cols = [ColumnSummary(name="num_california", type=None, type_generic="NUMERIC")]
    model, errors = _validate_seed(_context(cols))

    col = model["columns"][0]
    assert col["type"] == "DOUBLE"
    assert col["properties"]["inferred_type"] == "generic"
    assert errors == []


def test_truly_typeless_column_stays_untyped_and_blocks(monkeypatch) -> None:
    # No real type, no generic family, not a datetime → fail-closed: the column is
    # emitted WITHOUT a type (so validation flags it) and tagged for follow-up.
    cols = [ColumnSummary(name="mystery", type=None, type_generic=None)]
    model, errors = _validate_seed(_context(cols))

    col = model["columns"][0]
    assert "type" not in col  # never guessed
    assert col["properties"]["inferred_type"] == "unknown"
    assert any("is missing a type" in e for e in errors)


def test_round_trip_invariant_every_indexed_column_resolves() -> None:
    # The structural guarantee behind D-A: for any catalog, every column the index
    # knows is resolvable from the emitted seed (no logical/physical drift).
    cols = [
        ColumnSummary(name="2003", type="BIGINT"),
        ColumnSummary(name="net sales", type="DOUBLE"),
        ColumnSummary(name="ok_name", type="VARCHAR"),
    ]
    context = _context(cols)
    model, errors = _validate_seed(context)
    index = SchemaIndex.from_agent_context(context)

    for column in model["columns"]:
        physical = column["properties"]["superset_column_name"]
        assert index.has_column("wide", physical, "public")
    assert errors == []


# --- I4: end-to-end onboarding activates the repaired models --------------------


def _onboard(context: AgentContext):
    store = InMemoryMdlFileStore()
    result = onboard_schema_project(
        project=_project(),
        superset_context=context,
        wren_client=_StubWrenClient(),
        mdl_file_store=store,
        owner_id="owner",
    )
    return result


def test_onboarding_activates_leading_digit_and_generic_typed_table() -> None:
    # The previously-failing shape: leading-digit year columns + a typeless column
    # the catalog can still classify. With the fixes the whole table activates.
    context = _context(
        [
            ColumnSummary(name="2003", type="BIGINT"),
            ColumnSummary(name="region", type="VARCHAR"),
            ColumnSummary(name="num_california", type=None, type_generic="NUMERIC"),
        ],
        table="birth_france_by_region",
    )
    result = _onboard(context)

    assert result.model_count == 1
    assert result.activated_count == 1  # no draft-stranding
    assert result.files[0].status == "active"
    # No "does not exist" / "missing a type" noise in the warnings.
    joined = " ".join(result.warnings)
    assert "does not exist" not in joined
    assert "is missing a type" not in joined


def test_onboarding_leaves_truly_typeless_table_as_draft_with_warning() -> None:
    # Fail-closed (D-B): a column with no resolvable type blocks activation and is
    # surfaced as an actionable per-column warning rather than silently defaulted.
    context = _context(
        [
            ColumnSummary(name="ok", type="VARCHAR"),
            ColumnSummary(name="first_time_developer", type=None, type_generic=None),
        ],
        table="fcc_2018_survey",
    )
    result = _onboard(context)

    assert result.activated_count == 0
    assert result.files[0].status == "draft"
    assert any("is missing a type" in w for w in result.warnings)
    assert any("first_time_developer" in w for w in result.warnings)


# --- D-D: semantics-only authoring (type optional, overlay still applies) -------


def test_authoring_schema_accepts_typeless_semantic_columns() -> None:
    # The slimmed contract: the model may return columns with only name +
    # description (no structural `type`) and the response still validates.
    from superset_ai_agent.semantic_layer.mdl_authoring import MdlProposalResponse

    response = MdlProposalResponse.model_validate(
        {
            "files": [
                {
                    "path": "models/orders.json",
                    "manifest": {
                        "models": [
                            {
                                "name": "orders",
                                "description": "Customer orders.",
                                "columns": [
                                    {"name": "status", "description": "Order state."}
                                ],
                            }
                        ]
                    },
                }
            ]
        }
    )
    column = response.files[0].manifest.models[0].columns[0]
    assert column.type is None
    assert column.description == "Order state."


def test_semantic_overlay_applies_without_authored_type() -> None:
    # A typeless semantic column still enriches the deterministic seed's
    # description, while the seed keeps its authoritative catalog type.
    from superset_ai_agent.integrations.wren.llm_client import (
        _overlay_model_semantics,
    )

    seed = model_from_dataset(
        _context([ColumnSummary(name="status", type="VARCHAR")]).datasets[0]
    )
    _overlay_model_semantics(
        seed,
        {
            "description": "Customer orders.",
            "columns": [{"name": "status", "description": "Order state."}],
        },
    )
    column = seed["columns"][0]
    assert column["type"] == "VARCHAR"  # structure untouched
    assert column["description"] == "Order state."  # semantics overlaid
