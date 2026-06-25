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

"""Unified Wren-style workspace tree.

Surfaces the whole workspace (MDL files in folders, plus the sibling artifacts
``instructions.md``, ``queries.yml``, ``raw/`` documents, and the compiled
``target/mdl.json``) as one tree for the file browser. Each artifact keeps its
own backing store; this is presentation-layer unification only (see
``wren_mdl_copilot.md`` §1, declared change 4).
"""

from __future__ import annotations

from superset_ai_agent.semantic_layer.copilot.schemas import WorkspaceNode
from superset_ai_agent.semantic_layer.schemas import MdlFile


def build_workspace_tree(
    files: list[MdlFile],
    *,
    instruction_count: int = 0,
    document_count: int = 0,
    has_compiled: bool = False,
    has_memory: bool = False,
) -> WorkspaceNode:
    """Build the unified workspace tree from the project's stores."""

    root = WorkspaceNode(path="", name="workspace", kind="folder", editable=False)
    folders: dict[str, WorkspaceNode] = {"": root}

    def ensure_folder(path: str) -> WorkspaceNode:
        if path in folders:
            return folders[path]
        parent_path, _, name = path.rpartition("/")
        parent = ensure_folder(parent_path)
        node = WorkspaceNode(path=path, name=name, kind="folder", editable=False)
        parent.children.append(node)
        folders[path] = node
        return node

    active_files = [f for f in files if f.status != "deleted"]
    for file in sorted(active_files, key=lambda f: f.path):
        parent_path = file.path.rpartition("/")[0]
        parent = ensure_folder(parent_path)
        parent.children.append(
            WorkspaceNode(
                path=file.path,
                name=file.path.rsplit("/", 1)[-1],
                kind="mdl",
                editable=True,
                status=file.status,
                file_id=file.id,
                validation=file.validation,
            )
        )

    # Sibling virtual artifacts (each backed by its own store).
    root.children.append(
        WorkspaceNode(
            path="instructions.md",
            name="instructions.md",
            kind="instructions",
            editable=True,
            status=f"{instruction_count} rule(s)",
        )
    )
    root.children.append(
        WorkspaceNode(
            path="queries.yml", name="queries.yml", kind="queries", editable=True
        )
    )
    if document_count:
        root.children.append(
            WorkspaceNode(
                path="raw",
                name="raw",
                kind="folder",
                editable=False,
                status=f"{document_count} document(s)",
            )
        )
    if has_compiled:
        root.children.append(
            WorkspaceNode(
                path="target/mdl.json",
                name="target/mdl.json",
                kind="compiled",
                editable=False,
            )
        )
    if has_memory:
        root.children.append(
            WorkspaceNode(
                path=".wren/memory", name=".wren/memory", kind="memory", editable=False
            )
        )
    return root
