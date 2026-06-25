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

"""Copilot workspace tree + service apply/inspector — Phases 1 & 5 (backend)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetItem,
    InstructionView,
)
from superset_ai_agent.semantic_layer.copilot.service import (
    apply_changeset_items,
    build_deploy_preview,
    build_inspector,
)
from superset_ai_agent.semantic_layer.copilot.workspace import build_workspace_tree
from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlFileCreateRequest,
)

VALID = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [{"name": "id", "type": "BIGINT"}],
            }
        ]
    }
)


def _file(path: str, status: str = "draft") -> MdlFile:
    return MdlFile(
        project_id="p1",
        path=path,
        filename=path.rsplit("/", 1)[-1],
        content=VALID,
        checksum="x",
        status=status,  # type: ignore[arg-type]
    )


def test_workspace_tree_nests_folders_and_appends_siblings() -> None:
    tree = build_workspace_tree(
        [
            _file("models/orders.json"),
            _file("models/sub/items.json"),
            _file("rel.json"),
        ],
        instruction_count=2,
        document_count=1,
        has_compiled=True,
    )

    names = {child.name: child for child in tree.children}
    assert "models" in names
    assert names["models"].kind == "folder"
    models_children = {c.name: c for c in names["models"].children}
    assert models_children["orders.json"].kind == "mdl"
    assert models_children["orders.json"].editable is True
    assert "sub" in models_children
    assert models_children["sub"].kind == "folder"
    # siblings
    assert names["instructions.md"].kind == "instructions"
    assert names["queries.yml"].kind == "queries"
    assert names["raw"].status == "1 document(s)"
    assert names["target/mdl.json"].editable is False


def test_workspace_tree_excludes_deleted_files() -> None:
    tree = build_workspace_tree([_file("models/orders.json", status="deleted")])

    folder_names = {c.name for c in tree.children}
    assert "models" not in folder_names


def test_apply_changeset_creates_updates_and_deletes_drafts() -> None:
    store = InMemoryMdlFileStore()
    existing = store.create(
        "p1",
        MdlFileCreateRequest(path="models/orders.json", content=VALID),
        owner_id="local",
    )

    updated = json.loads(VALID)
    updated["models"][0]["description"] = "Orders"
    changeset = Changeset(
        items=[
            ChangesetItem(
                op="create", path="models/customers.json", proposed_content=VALID
            ),
            ChangesetItem(
                op="update",
                path="models/orders.json",
                file_id=existing.id,
                proposed_content=json.dumps(updated),
            ),
        ]
    )

    applied = apply_changeset_items(
        store, project_id="p1", items=changeset.items, owner_id="local"
    )

    assert len(applied) == 2
    files = {f.path: f for f in store.list("p1", owner_id="local")}
    assert "models/customers.json" in files
    assert files["models/customers.json"].source_type == "copilot"
    assert files["models/customers.json"].status == "draft"
    assert "Orders" in files["models/orders.json"].content


def test_apply_changeset_delete_removes_file() -> None:
    store = InMemoryMdlFileStore()
    existing = store.create(
        "p1",
        MdlFileCreateRequest(path="models/orders.json", content=VALID),
        owner_id="local",
    )

    apply_changeset_items(
        store,
        project_id="p1",
        items=[
            ChangesetItem(op="delete", path="models/orders.json", file_id=existing.id)
        ],
        owner_id="local",
    )

    remaining = [f for f in store.list("p1", owner_id="local") if f.status != "deleted"]
    assert remaining == []


def test_deploy_preview_diffs_drafts_against_active() -> None:
    active = _file("models/orders.json", status="active")
    draft_update = MdlFile(
        project_id="p1",
        path="models/orders.json",
        filename="orders.json",
        content=json.dumps(
            {
                "models": [
                    {
                        "name": "orders",
                        "tableReference": {"schema": "public", "table": "orders"},
                        "columns": [{"name": "id", "type": "BIGINT"}],
                        "description": "Orders v2",
                    }
                ]
            }
        ),
        checksum="y",
        status="draft",
    )
    draft_new = _file("models/customers.json", status="draft")

    preview = build_deploy_preview([active, draft_update, draft_new])

    ops = {item.path: item.op for item in preview.items}
    assert ops["models/orders.json"] == "update"
    assert ops["models/customers.json"] == "create"
    # the update item diffs against the active content
    orders = next(i for i in preview.items if i.path == "models/orders.json")
    assert "Orders v2" in (orders.proposed_content or "")
    assert orders.current_content == active.content


def test_deploy_preview_empty_when_no_drafts() -> None:
    preview = build_deploy_preview([_file("models/orders.json", status="active")])

    assert preview.items == []
    assert "No drafts" in preview.message


def test_build_inspector_includes_prompt_skills_tools_instructions() -> None:
    inspector = build_inspector(
        instructions=[InstructionView(id="i1", instruction="Prefer revenue over sales")]
    )

    assert "MDL Copilot" in inspector.system_prompt
    assert "Prefer revenue over sales" in inspector.system_prompt
    tool_names = {t.name for t in inspector.tools}
    assert "write_mdl_file" in tool_names
    assert {s.name for s in inspector.skills} <= {"generate-mdl", "enrich-context"}
    assert inspector.instructions[0].instruction == "Prefer revenue over sales"
