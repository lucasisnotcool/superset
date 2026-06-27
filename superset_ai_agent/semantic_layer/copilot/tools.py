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
from typing import Any, Callable, Protocol

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.llm.base import ToolSpec
from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetItem,
)
from superset_ai_agent.semantic_layer.document_chunks import (
    DocumentChunk,
    keyword_rank_chunks,
)
from superset_ai_agent.semantic_layer.document_retriever import (
    document_scope_key,
    DocumentChunkIndex,
    find_exact_duplicate_matches,
)
from superset_ai_agent.semantic_layer.mdl_files import normalize_mdl_path
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_mdl,
    validate_project_manifest,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlValidationResult,
    SemanticDocument,
)

logger = logging.getLogger(__name__)


class DocumentReader(Protocol):
    """The read-only document access the copilot toolset needs (project-scoped)."""

    def list_project_documents(
        self, project_id: str, *, owner_id: str = ...
    ) -> list[SemanticDocument]: ...

    def list_project_chunks(
        self, project_id: str, *, owner_id: str = ...
    ) -> list[DocumentChunk]: ...


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
        document_store: DocumentReader | None = None,
        document_index: DocumentChunkIndex | None = None,
        project_id: str | None = None,
        owner_id: str | None = None,
        retrieve_k: int = 8,
    ) -> None:
        self._originals: dict[str, MdlFile] = {f.path: f for f in files}
        #: Mutable staging copy seeded with every original file's content.
        self._working: dict[str, str] = {f.path: f.content for f in files}
        self._summaries: dict[str, str] = {}
        self._schema_index = schema_index
        self._deep_validate = deep_validate
        # Read-only document corpus the agent grounds MDL authoring on. Mutating
        # document ops (delete/summarize) are deliberately NOT exposed here — they
        # persist immediately and so break the "propose, don't persist" contract;
        # they remain explicit user-driven endpoints.
        self._document_store = document_store
        self._document_index = document_index
        self._project_id = project_id
        self._owner_id = owner_id or DEFAULT_OWNER_ID
        self._retrieve_k = retrieve_k

    @property
    def _documents_available(self) -> bool:
        return self._document_store is not None and self._project_id is not None

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
            ToolSpec(
                name="list_documents",
                description=(
                    "List the uploaded reference documents (glossaries, specs) for "
                    "this project — filename, status, and summary."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="search_documents",
                description=(
                    "Search the uploaded documents for passages relevant to a query "
                    "(business definitions, metric rules, synonyms) to ground MDL "
                    "edits in the operator's own docs."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to look for in the documents.",
                        },
                        "k": {
                            "type": "integer",
                            "description": "Max passages to return.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="find_duplicate_documents",
                description=(
                    "Find exact-duplicate passages across the uploaded documents "
                    "(redundant or conflicting context to reconcile)."
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
            "list_documents": self._list_documents,
            "search_documents": self._search_documents,
            "find_duplicate_documents": self._find_duplicate_documents,
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
        # Full-content overwrite is the code-editor model, but an LLM that
        # re-emits a file can silently drop the Superset-extension `properties`
        # (displayName/alias/synonyms) that back governance + retrieval. wren-core
        # tolerates the omission, so validation never catches it. Restore any
        # dropped keys against the prior version of this file (additive; the agent
        # can still *edit* a property, just not silently *delete* one).
        prior = self._working.get(path)
        restored = False
        if prior is not None:
            preserved = _preserve_superset_properties(prior, content)
            restored = preserved != content
            content = preserved
        self._working[path] = content
        if args.get("summary"):
            self._summaries[path] = str(args["summary"])
        validation = validate_mdl(
            content, schema_index=self._schema_index, strict_models=True
        )
        result = {"path": path, "validation": validation.model_dump(mode="json")}
        if restored:
            result["note"] = (
                "Restored Superset `properties` (displayName/alias/synonyms) that "
                "the new content omitted — these back governance and retrieval and "
                "must be preserved."
            )
        return result

    def _delete_mdl_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._require_path(args)
        if path not in self._working:
            # Deletion is whole-file by path; there is no per-model delete. A model
            # lives inside a file's models[]/relationships[], so removing or moving
            # one means rewriting that file with write_mdl_file (P4).
            return {
                "error": (
                    f"No MDL file at {path!r} to delete. Deletion removes whole "
                    "files by path; to remove or relocate a model (for example a "
                    "join wrongly placed in models[]), rewrite its containing file "
                    "with write_mdl_file."
                )
            }
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

    # -- document tools (read-only RAG over the uploaded corpus) -----------

    def _list_documents(self, _args: dict[str, Any]) -> dict[str, Any]:
        if not self._documents_available:
            return {"documents": [], "note": "No documents available."}
        assert self._document_store is not None
        assert self._project_id is not None
        documents = self._document_store.list_project_documents(
            self._project_id, owner_id=self._owner_id
        )
        return {
            "documents": [
                {
                    "id": document.id,
                    "filename": document.filename,
                    "status": document.status,
                    "summary": document.summary,
                }
                for document in documents
            ]
        }

    def _search_documents(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"error": "search_documents requires a 'query' string."}
        if not self._documents_available:
            return {"passages": [], "note": "No documents available."}
        assert self._document_store is not None
        assert self._project_id is not None
        k = args.get("k")
        limit = k if isinstance(k, int) and k > 0 else self._retrieve_k
        chunks = self._document_store.list_project_chunks(
            self._project_id, owner_id=self._owner_id
        )
        if self._document_index is not None:
            scope_key = document_scope_key(self._project_id)
            ranked = self._document_index.retrieve(
                query, chunks, scope_key=scope_key, k=limit
            )
        else:
            ranked = keyword_rank_chunks(query, chunks, limit)
        return {
            "passages": [
                {
                    "document_id": chunk.document_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                }
                for chunk in ranked
            ]
        }

    def _find_duplicate_documents(self, _args: dict[str, Any]) -> dict[str, Any]:
        if not self._documents_available:
            return {"duplicates": [], "note": "No documents available."}
        assert self._document_store is not None
        assert self._project_id is not None
        chunks = self._document_store.list_project_chunks(
            self._project_id, owner_id=self._owner_id
        )
        matches = find_exact_duplicate_matches(chunks)
        return {
            "duplicates": [
                {
                    "document_id": match.document_id,
                    "other_document_id": match.other_document_id,
                    "exact": match.exact,
                }
                for match in matches
            ]
        }

    # -- changeset rendering ----------------------------------------------

    def validate_working(self) -> MdlValidationResult:
        """Validate the merged working set as one project manifest."""

        return validate_project_manifest(
            list(self._working.values()),
            schema_index=self._schema_index,
            deep_validate=self._deep_validate,
            dedup_models=True,
            # Catch relationships-emitted-as-models in-loop (dep-free) so the
            # Copilot self-corrects before the user accepts/activates (P1).
            strict_models=True,
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
                            content,
                            schema_index=self._schema_index,
                            strict_models=True,
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
                            content,
                            schema_index=self._schema_index,
                            strict_models=True,
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


#: Manifest sections whose named entities carry the Superset-extension
#: ``properties`` bag (displayName / alias / synonyms and other governance
#: metadata). The copilot must never silently drop these; this list mirrors the
#: enrichment path's ``_MERGE_SECTIONS`` (see ``integrations/wren/llm_client.py``).
_PROPERTIES_SECTIONS: tuple[str, ...] = (
    "models",
    "relationships",
    "views",
    "metrics",
    "cubes",
)


def _restore_dropped_properties(
    base_entity: dict[str, Any], new_entity: dict[str, Any]
) -> bool:
    """Additively restore base ``properties`` keys missing from ``new_entity``.

    Returns True if ``new_entity`` was mutated. New values win on key collisions,
    so the agent can still *edit* a property — it just cannot silently *drop* one.
    """

    base_props = base_entity.get("properties")
    if not isinstance(base_props, dict) or not base_props:
        return False
    new_props = new_entity.get("properties")
    new_props = new_props if isinstance(new_props, dict) else {}
    merged = {**base_props, **new_props}
    if merged == new_props:
        return False
    new_entity["properties"] = merged
    return True


def _restore_column_properties(
    base_model: dict[str, Any], new_model: dict[str, Any]
) -> bool:
    """Restore dropped column ``properties`` within a model, matched by column name."""

    base_cols = base_model.get("columns")
    new_cols = new_model.get("columns")
    if not isinstance(base_cols, list) or not isinstance(new_cols, list):
        return False
    base_by_name = {
        col["name"]: col
        for col in base_cols
        if isinstance(col, dict) and isinstance(col.get("name"), str)
    }
    changed = False
    for new_col in new_cols:
        if not isinstance(new_col, dict):
            continue
        base_col = base_by_name.get(new_col.get("name"))
        if base_col is not None and _restore_dropped_properties(base_col, new_col):
            changed = True
    return changed


def _preserve_superset_properties(prior_content: str, new_content: str) -> str:
    """Re-inject ``properties`` the new content dropped vs the prior file version.

    Entities (and columns within models) are matched by ``name``. Formatting is
    preserved verbatim unless a restore is actually needed, in which case the file
    is re-serialized. Mirrors the enrichment path's structure-preserving merge so
    both authoring streams give the same governance guarantee.
    """

    try:
        prior = json.loads(prior_content)
        new = json.loads(new_content)
    except (ValueError, TypeError):
        return new_content
    if not isinstance(prior, dict) or not isinstance(new, dict):
        return new_content

    changed = False
    for section in _PROPERTIES_SECTIONS:
        base_items = prior.get(section)
        new_items = new.get(section)
        if not isinstance(base_items, list) or not isinstance(new_items, list):
            continue
        base_by_name = {
            item["name"]: item
            for item in base_items
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        for new_item in new_items:
            if not isinstance(new_item, dict):
                continue
            base_item = base_by_name.get(new_item.get("name"))
            if base_item is None:
                continue
            if _restore_dropped_properties(base_item, new_item):
                changed = True
            if section == "models" and _restore_column_properties(base_item, new_item):
                changed = True

    return json.dumps(new, indent=2) if changed else new_content
