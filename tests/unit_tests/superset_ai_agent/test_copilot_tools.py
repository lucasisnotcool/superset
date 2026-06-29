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

"""MDL Copilot toolset (working-set CRUD → reviewable changeset) — Phase 0.3."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex
from superset_ai_agent.semantic_layer.schemas import MdlFile

ORDERS = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [
                    {"name": "id", "type": "BIGINT"},
                    {"name": "amount", "type": "BIGINT"},
                ],
            }
        ]
    }
)

SCHEMA = SchemaIndex.from_snapshot({"orders": ["id", "amount"]})


def _file(path: str, content: str) -> MdlFile:
    return MdlFile(
        project_id="p1",
        path=path,
        filename=path.rsplit("/", 1)[-1],
        content=content,
        checksum="x",
    )


def test_specs_expose_the_tool_surface() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    names = {spec.name for spec in toolset.specs()}

    assert names == {
        # MDL authoring primitives
        "list_mdl_files",
        "read_mdl_file",
        "write_mdl_file",
        "patch_mdl_file",
        "delete_mdl_file",
        "validate_project",
        "get_physical_schema",
        "propose_onboard_table",
        "propose_onboard_tables",
        "propose_relationships",
        # read-only discovery
        "find_tables",
        # read-only document grounding tools
        "list_documents",
        "search_documents",
        "read_document",
        "run_coverage",
        "find_duplicate_documents",
    }


def test_write_new_file_produces_create_changeset_item() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    result = toolset.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": ORDERS, "summary": "add orders"},
    )
    assert result["validation"]["valid"] is True

    changeset = toolset.build_changeset(message="done")
    assert len(changeset.items) == 1
    item = changeset.items[0]
    assert item.op == "create"
    assert item.path == "models/orders.json"
    assert item.current_content is None
    assert item.summary == "add orders"
    assert changeset.manifest_validation is not None


def test_propose_onboard_table_generates_a_valid_base_model() -> None:
    # F4 convenience tool: one call onboards a physical table as a base model,
    # grounded in the (typed) schema index so it passes validation immediately.
    schema = SchemaIndex.from_snapshot(
        {"orders": ["id", "amount"]},
        {"orders": {"id": "INTEGER", "amount": "DECIMAL"}},
    )
    toolset = MdlToolset([], schema_index=schema)

    result = toolset.dispatch("propose_onboard_table", {"table": "orders"})
    assert "error" not in result, result
    assert result["onboarded_table"] == "orders"
    assert result["validation"]["valid"] is True

    changeset = toolset.build_changeset()
    assert len(changeset.items) == 1
    assert changeset.items[0].op == "create"
    assert changeset.items[0].path == "models/orders.json"
    body = json.loads(changeset.items[0].proposed_content or "{}")
    model = body["models"][0]
    assert model["tableReference"]["table"] == "orders"
    assert {c["name"] for c in model["columns"]} == {"id", "amount"}
    assert all(c.get("type") for c in model["columns"])  # types carried


def test_propose_onboard_table_rejects_a_table_outside_the_schema_set() -> None:
    # R1: the tool never invents a table absent from the project's accessible schemas.
    toolset = MdlToolset([], schema_index=SCHEMA)
    result = toolset.dispatch("propose_onboard_table", {"table": "ghosts"})
    assert "error" in result
    assert "not in the project" in result["error"]


def _multi_schema_index() -> SchemaIndex:
    """A live-shape index spanning two schemas (orders↔customers join-ready)."""

    return SchemaIndex(
        tables={
            "orders": {"id", "amount", "customer_id"},
            "customers": {"id", "name"},
        },
        column_types={
            "orders": {"id": "BIGINT", "amount": "DECIMAL", "customer_id": "BIGINT"},
            "customers": {"id": "BIGINT", "name": "VARCHAR"},
        },
        tables_by_schema={
            "sales": {"orders": {"id", "amount", "customer_id"}},
            "crm": {"customers": {"id", "name"}},
        },
    )


def test_propose_onboard_tables_onboards_each_across_schemas() -> None:
    # FP1: the cross-schema BI-doc flow — one call onboards several named tables,
    # each as its own staged base model. Tables span two schemas.
    toolset = MdlToolset([], schema_index=_multi_schema_index())

    result = toolset.dispatch(
        "propose_onboard_tables",
        {
            "tables": [
                {"table": "orders", "schema": "sales"},
                {"table": "customers", "schema": "crm"},
            ]
        },
    )
    assert result["rejected"] == [], result
    assert {row["table"] for row in result["onboarded"]} == {"orders", "customers"}

    changeset = toolset.build_changeset()
    assert {item.path for item in changeset.items} == {
        "models/orders.json",
        "models/customers.json",
    }


def test_propose_onboard_tables_rejects_unknown_tables_per_item() -> None:
    # R1/R-G2: a bad name is rejected per-item and never invented; valid tables in
    # the same batch still onboard (partial success).
    toolset = MdlToolset([], schema_index=SCHEMA)

    result = toolset.dispatch(
        "propose_onboard_tables",
        {"tables": [{"table": "orders"}, {"table": "ghosts"}]},
    )
    assert {row["table"] for row in result["onboarded"]} == {"orders"}
    assert len(result["rejected"]) == 1
    assert result["rejected"][0]["table"] == "ghosts"
    assert "not in the project" in result["rejected"][0]["error"]


def _onboarded_pair() -> MdlToolset:
    """A toolset with two onboarded models ready to be related."""

    toolset = MdlToolset([], schema_index=_multi_schema_index())
    toolset.dispatch(
        "propose_onboard_tables",
        {
            "tables": [
                {"table": "orders", "schema": "sales"},
                {"table": "customers", "schema": "crm"},
            ]
        },
    )
    return toolset


def test_propose_relationships_stages_a_valid_join() -> None:
    # FP1: the "+ relationships" half of the worked example — wire two onboarded
    # models into a cross-schema join as a reviewable changeset.
    toolset = _onboarded_pair()

    result = toolset.dispatch(
        "propose_relationships",
        {
            "relationships": [
                {
                    "models": ["orders", "customers"],
                    "joinType": "many_to_one",
                    "condition": "orders.customer_id = customers.id",
                }
            ]
        },
    )
    assert result["rejected"] == [], result
    assert len(result["staged"]) == 1

    changeset = toolset.build_changeset()
    rel_item = next(
        item for item in changeset.items if item.path.startswith("relationships/")
    )
    body = json.loads(rel_item.proposed_content or "{}")
    relationship = body["relationships"][0]
    assert relationship["models"] == ["orders", "customers"]
    assert relationship["joinType"] == "MANY_TO_ONE"
    assert relationship["condition"] == "orders.customer_id = customers.id"


def test_propose_relationships_rejects_unknown_model() -> None:
    # A relationship can only join models that were onboarded through the access
    # proof; an undefined endpoint is rejected (the R1 invariant holds upstream).
    toolset = _onboarded_pair()

    result = toolset.dispatch(
        "propose_relationships",
        {
            "relationships": [
                {
                    "models": ["orders", "ghosts"],
                    "joinType": "many_to_one",
                    "condition": "orders.x = ghosts.y",
                }
            ]
        },
    )
    assert result["staged"] == []
    assert "Unknown model" in result["rejected"][0]["error"]


def test_propose_relationships_rejects_bad_join_type_and_missing_condition() -> None:
    toolset = _onboarded_pair()

    bad_join = toolset.dispatch(
        "propose_relationships",
        {
            "relationships": [
                {
                    "models": ["orders", "customers"],
                    "joinType": "SIDEWAYS",
                    "condition": "orders.customer_id = customers.id",
                }
            ]
        },
    )
    assert "Invalid joinType" in bad_join["rejected"][0]["error"]

    no_condition = toolset.dispatch(
        "propose_relationships",
        {
            "relationships": [
                {"models": ["orders", "customers"], "joinType": "many_to_one"}
            ]
        },
    )
    assert "condition" in no_condition["rejected"][0]["error"]


def test_update_existing_file_produces_update_item() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    updated = json.loads(ORDERS)
    updated["models"][0]["description"] = "Customer orders"
    toolset.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": json.dumps(updated)},
    )

    changeset = toolset.build_changeset()
    assert len(changeset.items) == 1
    assert changeset.items[0].op == "update"
    assert changeset.items[0].file_id is not None
    assert "Customer orders" in (changeset.items[0].proposed_content or "")


def test_unchanged_file_is_not_in_changeset() -> None:
    # Re-writing identical content (even reformatted) yields no diff.
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    reformatted = json.dumps(json.loads(ORDERS), indent=2)
    toolset.dispatch(
        "write_mdl_file", {"path": "models/orders.json", "content": reformatted}
    )

    assert toolset.build_changeset().items == []


def test_delete_file_produces_delete_item() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    result = toolset.dispatch("delete_mdl_file", {"path": "models/orders.json"})
    assert result["deleted"] is True

    changeset = toolset.build_changeset()
    assert len(changeset.items) == 1
    assert changeset.items[0].op == "delete"
    assert changeset.items[0].proposed_content is None


def test_delete_missing_file_returns_actionable_guidance() -> None:
    # Deleting a non-existent path (e.g. a model name treated as a file) steers the
    # agent toward rewriting the containing file rather than delete-by-name (P4).
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    result = toolset.dispatch(
        "delete_mdl_file", {"path": "models/sites_to_production_lines.json"}
    )

    assert "error" in result
    assert "write_mdl_file" in result["error"]
    assert "whole files by path" in result["error"]


def test_get_physical_schema_returns_real_tables() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    result = toolset.dispatch("get_physical_schema", {})

    assert result["tables"] == {"orders": ["amount", "id"]}


def test_find_tables_ranks_matches_and_returns_columns() -> None:
    # Targeted discovery: a doc entity ("customer orders") should surface the
    # orders table with its columns — not the whole schema.
    toolset = MdlToolset([], schema_index=_multi_schema_index())

    result = toolset.dispatch("find_tables", {"query": "customer orders"})

    tables = result["tables"]
    assert tables, result
    top = tables[0]
    assert top["table"] == "orders"
    assert top["schema"] == "sales"
    assert {col["name"] for col in top["columns"]} == {"id", "amount", "customer_id"}
    assert all(col.get("type") for col in top["columns"])  # types carried through


def test_find_tables_respects_schema_filter_and_limit() -> None:
    toolset = MdlToolset([], schema_index=_multi_schema_index())

    scoped = toolset.dispatch("find_tables", {"query": "id", "schema": "crm"})
    assert {t["table"] for t in scoped["tables"]} == {"customers"}

    capped = toolset.dispatch("find_tables", {"query": "id", "limit": 1})
    assert len(capped["tables"]) == 1


def test_find_tables_empty_on_no_match_never_invents() -> None:
    # The honest "no table in this database matches" signal (R9): an empty list,
    # never a fabricated table.
    toolset = MdlToolset([], schema_index=SCHEMA)

    result = toolset.dispatch("find_tables", {"query": "spaceships"})

    assert result["tables"] == []


def test_find_tables_requires_a_query() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)
    assert "error" in toolset.dispatch("find_tables", {})


def test_find_tables_and_read_document_are_not_in_the_ledger() -> None:
    # Read-only tools must not produce ToolCallRecords (provenance is for mutations).
    toolset = MdlToolset([], schema_index=SCHEMA)

    toolset.dispatch("find_tables", {"query": "orders"})
    toolset.dispatch("read_document", {"document_id": "missing"})

    assert toolset.build_changeset().tool_calls == []


def test_read_and_list_reflect_working_set() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    listing = toolset.dispatch("list_mdl_files", {})
    assert listing["files"][0]["path"] == "models/orders.json"

    read = toolset.dispatch("read_mdl_file", {"path": "models/orders.json"})
    assert json.loads(read["content"])["models"][0]["name"] == "orders"


def test_write_rejects_empty_content_and_bad_path() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    assert "error" in toolset.dispatch(
        "write_mdl_file", {"path": "models/x.json", "content": "  "}
    )
    assert "error" in toolset.dispatch(
        "write_mdl_file", {"path": "../escape.json", "content": ORDERS}
    )


def test_unknown_tool_returns_error() -> None:
    assert "error" in MdlToolset([]).dispatch("nope", {})


# -- Superset `properties` preservation guard (governance metadata) ----------

ORDERS_WITH_PROPS = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "properties": {"displayName": "Orders"},
                "columns": [
                    {
                        "name": "id",
                        "type": "BIGINT",
                        "properties": {
                            "displayName": "Order ID",
                            "synonyms": "order number",
                        },
                    },
                    {"name": "amount", "type": "BIGINT"},
                ],
            }
        ]
    }
)


def _strip_all_properties() -> str:
    payload = json.loads(ORDERS_WITH_PROPS)
    model = payload["models"][0]
    model.pop("properties", None)
    for column in model["columns"]:
        column.pop("properties", None)
    return json.dumps(payload)


def test_write_restores_dropped_column_and_model_properties() -> None:
    # The agent re-emits the file but omits every `properties` block — the guard
    # must silently restore them so governance/retrieval metadata survives.
    toolset = MdlToolset(
        [_file("models/orders.json", ORDERS_WITH_PROPS)], schema_index=SCHEMA
    )

    result = toolset.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": _strip_all_properties()},
    )
    assert "note" in result  # agent is told the drop was corrected

    stored = json.loads(
        toolset.dispatch("read_mdl_file", {"path": "models/orders.json"})["content"]
    )
    model = stored["models"][0]
    assert model["properties"] == {"displayName": "Orders"}
    id_col = next(c for c in model["columns"] if c["name"] == "id")
    assert id_col["properties"] == {
        "displayName": "Order ID",
        "synonyms": "order number",
    }


def test_write_keeps_agent_edits_while_restoring_dropped_keys() -> None:
    # New values win on collision; omitted keys are restored (additive merge).
    toolset = MdlToolset(
        [_file("models/orders.json", ORDERS_WITH_PROPS)], schema_index=SCHEMA
    )
    edited = json.loads(ORDERS_WITH_PROPS)
    id_col = edited["models"][0]["columns"][0]
    id_col["properties"] = {"displayName": "Identifier"}  # change one, drop synonyms

    toolset.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": json.dumps(edited)},
    )

    stored = json.loads(
        toolset.dispatch("read_mdl_file", {"path": "models/orders.json"})["content"]
    )
    stored_id = stored["models"][0]["columns"][0]
    assert stored_id["properties"] == {
        "displayName": "Identifier",  # agent's edit preserved
        "synonyms": "order number",  # dropped key restored
    }


def test_write_dropping_properties_yields_no_spurious_changeset_item() -> None:
    # Restoring properties back to the original means the file is unchanged.
    toolset = MdlToolset(
        [_file("models/orders.json", ORDERS_WITH_PROPS)], schema_index=SCHEMA
    )
    toolset.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": _strip_all_properties()},
    )

    assert toolset.build_changeset().items == []


def test_write_without_prior_file_does_not_invent_properties() -> None:
    # A brand-new file has no prior version, so nothing is restored.
    toolset = MdlToolset([], schema_index=SCHEMA)

    result = toolset.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": _strip_all_properties()},
    )
    assert "note" not in result
    stored = json.loads(
        toolset.dispatch("read_mdl_file", {"path": "models/orders.json"})["content"]
    )
    assert "properties" not in stored["models"][0]


# -- Tool-call provenance ledger (per-call capture, R-B6 grounding) -----------


def test_mutating_tool_calls_are_recorded_in_the_ledger() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    toolset.dispatch(
        "write_mdl_file", {"path": "models/orders.json", "content": ORDERS}
    )

    changeset = toolset.build_changeset()
    assert len(changeset.tool_calls) == 1
    record = changeset.tool_calls[0]
    assert record.tool == "write_mdl_file"
    assert record.action == "write"
    assert record.paths == ["models/orders.json"]
    assert record.status == "ok"


def test_read_only_tool_calls_are_not_recorded() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    toolset.dispatch("list_mdl_files", {})
    toolset.dispatch("read_mdl_file", {"path": "models/orders.json"})
    toolset.dispatch("validate_project", {})

    assert toolset.build_changeset().tool_calls == []


def test_onboard_tables_records_an_onboard_verb_with_shapes() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    toolset.dispatch("propose_onboard_tables", {"tables": [{"table": "orders"}]})

    record = toolset.build_changeset().tool_calls[0]
    assert record.action == "onboard"
    assert record.args_summary["tables"] == ["orders"]
    assert record.args_summary["table_count"] == 1
    assert record.paths == ["models/orders.json"]


def test_failed_mutation_is_recorded_with_error_status() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    # A table not in the accessible schema is rejected by the onboard core.
    toolset.dispatch("propose_onboard_table", {"table": "nope"})

    record = toolset.build_changeset().tool_calls[0]
    assert record.action == "onboard"
    assert record.status == "error"
    assert record.detail is not None
    assert "accessible schemas" in record.detail


def test_relationships_record_a_relate_verb() -> None:
    two = SchemaIndex.from_snapshot({"orders": ["id"], "customers": ["id"]})
    toolset = MdlToolset([], schema_index=two)
    toolset.dispatch(
        "propose_onboard_tables",
        {"tables": [{"table": "orders"}, {"table": "customers"}]},
    )

    toolset.dispatch(
        "propose_relationships",
        {
            "relationships": [
                {
                    "models": ["orders", "customers"],
                    "joinType": "MANY_TO_ONE",
                    "condition": "orders.id = customers.id",
                }
            ]
        },
    )

    relate = [r for r in toolset.build_changeset().tool_calls if r.action == "relate"]
    assert len(relate) == 1
    assert relate[0].args_summary["relationship_count"] == 1


# --- patch_mdl_file: sparse name-keyed overlay edits (A) ---------------------


def test_patch_adds_column_description_without_dropping_siblings() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    result = toolset.dispatch(
        "patch_mdl_file",
        {
            "path": "models/orders.json",
            "overlay": json.dumps(
                {
                    "models": [
                        {
                            "name": "orders",
                            "columns": [
                                {"name": "amount", "description": "Order total"}
                            ],
                        }
                    ]
                }
            ),
        },
    )

    assert result["validation"]["valid"] is True
    assert result["patched"] == {
        "matched": ["orders"],
        "appended": [],
        "ignored_structural": [],
    }
    merged = json.loads(toolset._working["models/orders.json"])
    columns = {c["name"]: c for c in merged["models"][0]["columns"]}
    assert set(columns) == {"id", "amount"}  # sibling column kept
    assert columns["amount"]["type"] == "BIGINT"  # type never dropped
    assert columns["amount"]["description"] == "Order total"


def test_patch_preserves_properties_the_overlay_omits() -> None:
    seeded = json.dumps(
        {
            "models": [
                {
                    "name": "orders",
                    "tableReference": {"schema": "public", "table": "orders"},
                    "properties": {"displayName": "Orders", "superset_dataset_id": "9"},
                    "columns": [{"name": "id", "type": "BIGINT"}],
                }
            ]
        }
    )
    toolset = MdlToolset([_file("models/orders.json", seeded)], schema_index=SCHEMA)

    toolset.dispatch(
        "patch_mdl_file",
        {
            "path": "models/orders.json",
            "overlay": json.dumps(
                {"models": [{"name": "orders", "properties": {"synonyms": ["sales"]}}]}
            ),
        },
    )

    model = json.loads(toolset._working["models/orders.json"])["models"][0]
    assert model["properties"] == {
        "displayName": "Orders",
        "superset_dataset_id": "9",
        "synonyms": ["sales"],
    }


def test_patch_composes_on_prior_working_edit() -> None:
    # A write earlier in the turn, then a patch on top — patch must see the
    # staged working copy, not the original file.
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)
    toolset.dispatch(
        "write_mdl_file",
        {
            "path": "models/orders.json",
            "content": json.dumps(
                {
                    "models": [
                        {
                            "name": "orders",
                            "tableReference": {"schema": "public", "table": "orders"},
                            "description": "Sales orders",
                            "columns": [
                                {"name": "id", "type": "BIGINT"},
                                {"name": "amount", "type": "BIGINT"},
                            ],
                        }
                    ]
                }
            ),
        },
    )
    toolset.dispatch(
        "patch_mdl_file",
        {
            "path": "models/orders.json",
            "overlay": json.dumps(
                {
                    "models": [
                        {
                            "name": "orders",
                            "columns": [{"name": "id", "description": "PK"}],
                        }
                    ]
                }
            ),
        },
    )

    model = json.loads(toolset._working["models/orders.json"])["models"][0]
    assert model["description"] == "Sales orders"  # earlier write survives
    columns = {c["name"]: c for c in model["columns"]}
    assert columns["id"]["description"] == "PK"  # patch applied on top


def test_patch_missing_file_points_to_write() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    result = toolset.dispatch(
        "patch_mdl_file",
        {"path": "models/new.json", "overlay": json.dumps({"models": [{"name": "x"}]})},
    )

    assert "error" in result
    assert "write_mdl_file" in result["error"]


def test_patch_malformed_overlay_errors() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    result = toolset.dispatch(
        "patch_mdl_file", {"path": "models/orders.json", "overlay": "{not json"}
    )

    assert "error" in result
    assert "overlay" in result["error"]


def test_patch_typo_name_appends_and_flags_note() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)

    result = toolset.dispatch(
        "patch_mdl_file",
        {
            "path": "models/orders.json",
            "overlay": json.dumps(
                {"models": [{"name": "ordrs", "description": "typo"}]}
            ),
        },
    )

    assert result["patched"] == {
        "matched": [],
        "appended": ["ordrs"],
        "ignored_structural": [],
    }
    assert "ordrs" in result["note"]


def test_patch_provenance_records_write_verb() -> None:
    toolset = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)
    toolset.dispatch(
        "patch_mdl_file",
        {
            "path": "models/orders.json",
            "overlay": json.dumps({"models": [{"name": "orders", "description": "d"}]}),
        },
    )

    calls = toolset.build_changeset().tool_calls
    assert len(calls) == 1
    assert calls[0].tool == "patch_mdl_file"
    assert calls[0].action == "write"
    assert calls[0].paths == ["models/orders.json"]


def test_patch_and_write_yield_equivalent_changeset(  # R6 invariant
) -> None:
    overlay = {
        "models": [
            {"name": "orders", "columns": [{"name": "amount", "description": "Total"}]}
        ]
    }

    patched = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)
    patched.dispatch(
        "patch_mdl_file",
        {"path": "models/orders.json", "overlay": json.dumps(overlay)},
    )
    patch_item = [
        i for i in patched.build_changeset().items if i.path == "models/orders.json"
    ][0]

    # Equivalent whole-file write: the merged result re-emitted by hand.
    full = json.loads(ORDERS)
    full["models"][0]["columns"][1]["description"] = "Total"
    written = MdlToolset([_file("models/orders.json", ORDERS)], schema_index=SCHEMA)
    written.dispatch(
        "write_mdl_file",
        {"path": "models/orders.json", "content": json.dumps(full)},
    )
    write_item = [
        i for i in written.build_changeset().items if i.path == "models/orders.json"
    ][0]

    # Reviewer sees the same logical file regardless of how it was produced.
    assert patch_item.op == write_item.op == "update"
    assert json.loads(patch_item.proposed_content) == json.loads(
        write_item.proposed_content
    )


def test_patch_flags_silently_ignored_structural_edits() -> None:
    # The additive merge keeps a column's type/expression from the base (E4), so a
    # patch that tries to change them is a no-op — the tool must surface that so the
    # agent re-issues the edit via write_mdl_file instead of silently losing it.
    seeded = json.dumps(
        {
            "models": [
                {
                    "name": "orders",
                    "tableReference": {"schema": "public", "table": "orders"},
                    "columns": [
                        {"name": "id", "type": "BIGINT"},
                        {
                            "name": "tax",
                            "type": "DOUBLE",
                            "isCalculated": True,
                            "expression": "amount * 0.1",
                        },
                    ],
                }
            ]
        }
    )
    toolset = MdlToolset([_file("models/orders.json", seeded)], schema_index=SCHEMA)

    result = toolset.dispatch(
        "patch_mdl_file",
        {
            "path": "models/orders.json",
            "overlay": json.dumps(
                {
                    "models": [
                        {
                            "name": "orders",
                            "columns": [{"name": "tax", "expression": "amount * 0.2"}],
                        }
                    ]
                }
            ),
        },
    )

    assert result["patched"]["ignored_structural"] == ["orders.tax.expression"]
    assert "write_mdl_file" in result["note"]
    # And the base expression is genuinely unchanged (the no-op is real).
    merged = json.loads(toolset._working["models/orders.json"])
    tax = {c["name"]: c for c in merged["models"][0]["columns"]}["tax"]
    assert tax["expression"] == "amount * 0.1"
