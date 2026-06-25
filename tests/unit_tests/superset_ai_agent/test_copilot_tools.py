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
        "delete_mdl_file",
        "validate_project",
        "get_physical_schema",
        # read-only document grounding tools
        "list_documents",
        "search_documents",
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


def test_get_physical_schema_returns_real_tables() -> None:
    toolset = MdlToolset([], schema_index=SCHEMA)

    result = toolset.dispatch("get_physical_schema", {})

    assert result["tables"] == {"orders": ["amount", "id"]}


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
