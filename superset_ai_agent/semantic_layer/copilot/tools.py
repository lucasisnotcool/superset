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

"""Copilot toolset — deterministic CRUD primitives over a staged MDL project.

The toolset is the agent's hands. Crucially it mutates an **in-memory working
copy** of the project's files, never the store: the agent can freely create /
update / delete and validate, and only when the user accepts the resulting
``Changeset`` do the existing per-file endpoints persist drafts. This is the
"propose, don't persist" contract (see ``wren_mdl_copilot.md`` §3).
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
import logging
from typing import Any, Callable

from superset_ai_agent.llm.base import ToolSpec
from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetItem,
)
from superset_ai_agent.semantic_layer.mdl_files import normalize_mdl_path
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_mdl,
    validate_project_manifest,
)
from superset_ai_agent.semantic_layer.schemas import MdlFile, MdlValidationResult

logger = logging.getLogger(__name__)


class MdlToolset:
    """Stages MDL edits in memory and renders a reviewable changeset.

    Parameters
    ----------
    files:
        The project's current (non-deleted) MDL files — the changeset diff base.
    schema_index:
        Physical schema for the "never invent columns/tables" rule. Optional.
    deep_validate:
        When True, ``validate_project`` and the final changeset run wren-core
        deep validation in addition to structural/physical checks.
    """

    def __init__(
        self,
        files: list[MdlFile],
        *,
        schema_index: SchemaIndex | None = None,
        deep_validate: bool = False,
    ) -> None:
        self._originals: dict[str, MdlFile] = {f.path: f for f in files}
        #: Mutable staging copy seeded with every original file's content.
        self._working: dict[str, str] = {f.path: f.content for f in files}
        self._summaries: dict[str, str] = {}
        self._schema_index = schema_index
        self._deep_validate = deep_validate

    # -- LLM-facing surface ------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        """Tool specs handed to the model for function calling."""

        path_param = {"type": "string", "description": "Project-relative .json path"}
        return [
            ToolSpec(
                name="list_mdl_files",
                description="List the project's MDL files (path + status).",
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="read_mdl_file",
                description="Read the full JSON content of one MDL file.",
                parameters={
                    "type": "object",
                    "properties": {"path": path_param},
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="write_mdl_file",
                description=(
                    "Create or replace an MDL file's full JSON content. Returns "
                    "validation. Never invent physical tables/columns."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": path_param,
                        "content": {
                            "type": "string",
                            "description": "Full MDL JSON document for the file.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short human label for this edit.",
                        },
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolSpec(
                name="delete_mdl_file",
                description="Delete an MDL file from the project.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": path_param,
                        "summary": {"type": "string"},
                    },
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="validate_project",
                description=(
                    "Validate the whole MDL project (structural + physical + "
                    "engine). Call before finishing to confirm a clean manifest."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="get_physical_schema",
                description=(
                    "Return the real tables/columns available in this schema. Use "
                    "to ground edits; never reference anything absent here."
                ),
                parameters={"type": "object", "properties": {}},
            ),
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute one tool call against the working set; returns a JSON-able dict."""

        handler: Callable[[dict[str, Any]], dict[str, Any]] | None = {
            "list_mdl_files": self._list_mdl_files,
            "read_mdl_file": self._read_mdl_file,
            "write_mdl_file": self._write_mdl_file,
            "delete_mdl_file": self._delete_mdl_file,
            "validate_project": self._validate_project,
            "get_physical_schema": self._get_physical_schema,
        }.get(name)
        if handler is None:
            return {"error": f"Unknown tool {name!r}."}
        try:
            return handler(arguments or {})
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Copilot tool %s failed: %s", name, ex)
            return {"error": str(ex)}

    # -- tool implementations ---------------------------------------------

    def _list_mdl_files(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {
            "files": [
                {
                    "path": path,
                    "status": (
                        self._originals[path].status
                        if path in self._originals
                        else "new"
                    ),
                }
                for path in sorted(self._working)
            ]
        }

    def _read_mdl_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._require_path(args)
        if path not in self._working:
            return {"error": f"No MDL file at {path!r}."}
        return {"path": path, "content": self._working[path]}

    def _write_mdl_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = normalize_mdl_path(self._require_path(args))
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return {"error": "write_mdl_file requires non-empty 'content'."}
        self._working[path] = content
        if args.get("summary"):
            self._summaries[path] = str(args["summary"])
        validation = validate_mdl(content, schema_index=self._schema_index)
        return {"path": path, "validation": validation.model_dump(mode="json")}

    def _delete_mdl_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._require_path(args)
        if path not in self._working:
            return {"error": f"No MDL file at {path!r} to delete."}
        del self._working[path]
        if args.get("summary"):
            self._summaries[path] = str(args["summary"])
        return {"path": path, "deleted": True}

    def _validate_project(self, _args: dict[str, Any]) -> dict[str, Any]:
        return self.validate_working().model_dump(mode="json")

    def _get_physical_schema(self, _args: dict[str, Any]) -> dict[str, Any]:
        if self._schema_index is None:
            return {"tables": {}, "note": "No physical schema available."}
        result: dict[str, Any] = {"tables": self._schema_index.to_tables()}
        if self._schema_index.has_types():
            result["column_types"] = self._schema_index.typed_tables()
        return result

    # -- changeset rendering ----------------------------------------------

    def validate_working(self) -> MdlValidationResult:
        """Validate the merged working set as one project manifest."""

        return validate_project_manifest(
            list(self._working.values()),
            schema_index=self._schema_index,
            deep_validate=self._deep_validate,
            dedup_models=True,
        )

    def build_changeset(self, *, message: str = "") -> Changeset:
        """Diff the working set against the originals into a reviewable changeset."""

        items: list[ChangesetItem] = []
        for path, content in sorted(self._working.items()):
            original = self._originals.get(path)
            if original is None:
                items.append(
                    ChangesetItem(
                        op="create",
                        path=path,
                        proposed_content=content,
                        validation=validate_mdl(
                            content, schema_index=self._schema_index
                        ),
                        summary=self._summaries.get(path, f"Create {path}"),
                    )
                )
            elif _normalize(original.content) != _normalize(content):
                items.append(
                    ChangesetItem(
                        op="update",
                        path=path,
                        file_id=original.id,
                        current_content=original.content,
                        proposed_content=content,
                        validation=validate_mdl(
                            content, schema_index=self._schema_index
                        ),
                        summary=self._summaries.get(path, f"Update {path}"),
                    )
                )
        for path, original in sorted(self._originals.items()):
            if path not in self._working:
                items.append(
                    ChangesetItem(
                        op="delete",
                        path=path,
                        file_id=original.id,
                        current_content=original.content,
                        summary=self._summaries.get(path, f"Delete {path}"),
                    )
                )
        return Changeset(
            items=items,
            manifest_validation=self.validate_working(),
            message=message,
        )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _require_path(args: dict[str, Any]) -> str:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Tool call requires a 'path' string.")
        return path


def _normalize(content: str) -> str:
    """Compare MDL by parsed JSON when possible so formatting noise is ignored."""

    try:
        return json.dumps(json.loads(content), sort_keys=True)
    except (ValueError, TypeError):
        return content.strip()
