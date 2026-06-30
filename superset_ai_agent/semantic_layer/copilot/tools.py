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
from types import SimpleNamespace
from typing import Any, Callable, Protocol

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.llm.base import ModelClient, ToolSpec
from superset_ai_agent.llm.embeddings import Embedder
from superset_ai_agent.semantic_layer.copilot.coverage import (
    CoverageDocument,
    run_directory_coverage,
)
from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetItem,
    ToolActionKind,
    ToolCallRecord,
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
from superset_ai_agent.semantic_layer.golden_queries import (
    GOLDEN_QUERIES_PATH,
    GoldenQuery,
    is_golden_queries_path,
    upsert_golden_query,
    validate_golden_queries,
)
from superset_ai_agent.semantic_layer.mdl_files import normalize_mdl_path
from superset_ai_agent.semantic_layer.mdl_merge import (
    merge_manifest_sections,
    MERGE_SECTIONS,
    remove_manifest_entities,
)
from superset_ai_agent.semantic_layer.mdl_schema import JOIN_TYPES
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

#: Default cap on ``read_document`` output. Bounds context cost on a large BI doc
#: while still letting the agent see the whole spec; the agent can raise it via
#: ``max_chars`` or page with ``search_documents``. Aligned with the frontend's
#: 200KB attach slice (this is the read-side equivalent, kept a touch smaller).
_READ_DOCUMENT_MAX_CHARS = 100_000


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
        model_client: ModelClient | None = None,
        embedder: Embedder | None = None,
        instructions: list[str] | None = None,
        coverage_self_audit_limit: int = 2,
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
        #: Document ids the agent pulled passages from (search_documents) — the
        #: enrichment-provenance signal, stamped onto the built changeset.
        self._referenced_document_ids: list[str] = []
        #: Per-mutating-call provenance ledger (write/delete/onboard/relate),
        #: stamped onto the built changeset's ``tool_calls``.
        self._tool_calls: list[ToolCallRecord] = []
        #: Watermark into ``_referenced_document_ids``: docs searched *since the
        #: previous mutating call* are the grounding for the next one. Lets a
        #: search→write pair attribute the searched doc to the written file (R-B6).
        self._grounding_watermark = 0
        #: path → source document id, when a single doc grounded the write. Read by
        #: ``build_changeset`` to stamp each item's ``source_document_id``.
        self._file_grounding: dict[str, str] = {}
        #: Coverage self-review deps (read-only ``run_coverage`` tool). The model
        #: client drives claim extraction/judging; the audit is per-turn capped and
        #: memoized so the agent can review-then-refine without unbounded LLM cost.
        self._model_client = model_client
        self._embedder = embedder
        self._instructions = instructions or []
        self._coverage_self_audit_limit = coverage_self_audit_limit
        self._coverage_audits_done = 0
        self._coverage_memo: dict[str, dict[str, Any]] = {}

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
                name="patch_mdl_file",
                description=(
                    "Refine an EXISTING MDL file by merging a partial overlay — emit "
                    "only the models/columns/relationships/metrics you change, keyed "
                    "by name, not the whole file. Omitted entities/columns and their "
                    "existing properties are preserved automatically. Prefer this for "
                    "adding descriptions, synonyms, new calculated columns, metrics, "
                    "and relationships. It only refines description/properties on an "
                    "existing column and appends new ones; to change an existing "
                    "column's type/expression or to remove/restructure an entity, use "
                    "write_mdl_file (a full overwrite). Returns validation."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": path_param,
                        "overlay": {
                            "type": "string",
                            "description": (
                                "Partial MDL JSON with ONLY the changed entities/"
                                "columns, keyed by name, e.g. "
                                '{"models":[{"name":"orders","columns":'
                                '[{"name":"revenue","description":"Gross USD"}]}]}.'
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short human label for this edit.",
                        },
                    },
                    "required": ["path", "overlay"],
                },
            ),
            ToolSpec(
                name="add_golden_query",
                description=(
                    "Add a curated, verified question->SQL example ('golden query') "
                    "to the project's queries.json. Use when a known-good answer to a "
                    "recurring business question should steer future SQL generation. "
                    "Write the SQL against the semantic model's logical (model) names, "
                    "not physical tables — like a metric or view. Idempotent on the "
                    "question. Lands as a reviewable draft; a human accepts it."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": (
                                "Natural-language question, phrased as a user would "
                                "ask it."
                            ),
                        },
                        "semantic_sql": {
                            "type": "string",
                            "description": (
                                "Known-good SQL answering the question, written "
                                "against MDL model names."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Short descriptive name (defaults to the question)."
                            ),
                        },
                        "usage_guidance": {
                            "type": "string",
                            "description": "Optional note on when this applies.",
                        },
                        "use_as_onboarding": {
                            "type": "boolean",
                            "description": (
                                "Surface as a project starter question (default false)."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": "Short human label for this edit.",
                        },
                    },
                    "required": ["question", "semantic_sql"],
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
                name="remove_mdl_entity",
                description=(
                    "Remove named entities from an existing MDL file: a model, "
                    "relationship, metric, view, or a CALCULATED column. Prefer this "
                    "over rewriting the whole file with write_mdl_file just to drop "
                    "something. Physical columns cannot be removed (physical "
                    "authority — they come from the catalog). If a removal empties "
                    "the file, the file is deleted. Returns validation."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": path_param,
                        "removals": {
                            "type": "array",
                            "description": (
                                "Entities to remove. Each item is "
                                "{section, name, column?} where section is one of "
                                "models|relationships|metrics|views; set 'column' "
                                "(with section=models, name=<model>) to remove a "
                                "calculated column."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "section": {"type": "string"},
                                    "name": {"type": "string"},
                                    "column": {"type": "string"},
                                },
                                "required": ["section", "name"],
                            },
                        },
                        "summary": {"type": "string"},
                    },
                    "required": ["path", "removals"],
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
                    "Return the real tables/columns/types available to this project. "
                    "Use to ground edits; never reference anything absent here. For a "
                    "single-schema project the result is {tables, column_types}; for a "
                    "MULTI-SCHEMA project it is {schemas: {schema: {table: {columns, "
                    "types}}}} — author each model's tableReference with the schema "
                    "the table is listed under."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSpec(
                name="find_tables",
                description=(
                    "Find the physical tables in this database that match a "
                    "free-text query — use it to map an entity a BI doc names "
                    "(e.g. 'customer orders') to the real tables to onboard, "
                    "instead of reading the whole schema. Returns only the top "
                    "candidates with their columns/types; an empty result means "
                    "no table in the project's accessible schemas matches."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Entity / table name to look for.",
                        },
                        "schema": {
                            "type": "string",
                            "description": (
                                "Restrict to one physical schema (multi-schema "
                                "project); omit to search every accessible schema."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max candidate tables (default 10).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="propose_onboard_table",
                description=(
                    "Onboard one physical table as a base MDL model in one step: "
                    "generates a model from the real columns/types and stages it. "
                    "Use this to bring a table referenced by a BI doc into the "
                    "project (optionally specifying its schema for a multi-schema "
                    "project). The table must exist in the project's accessible "
                    "schemas; relationships are added separately with write_mdl_file."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {
                            "type": "string",
                            "description": "Physical table name to onboard.",
                        },
                        "schema": {
                            "type": "string",
                            "description": (
                                "Physical schema of the table (for a multi-schema "
                                "project); omit for a single-schema project."
                            ),
                        },
                    },
                    "required": ["table"],
                },
            ),
            ToolSpec(
                name="propose_onboard_tables",
                description=(
                    "Onboard several physical tables in one step — the cross-schema "
                    "BI-doc flow: pass the tables a document names and each is staged "
                    "as a base model from its real columns/types. Every table must "
                    "exist in the project's accessible schemas; unknown tables are "
                    "rejected per-item. Add the joins afterwards with "
                    "propose_relationships."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tables": {
                            "type": "array",
                            "description": (
                                "Tables to onboard. Each item is an object "
                                "{table, schema?} (schema for a multi-schema project)."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "table": {"type": "string"},
                                    "schema": {"type": "string"},
                                },
                                "required": ["table"],
                            },
                        },
                    },
                    "required": ["tables"],
                },
            ),
            ToolSpec(
                name="propose_relationships",
                description=(
                    "Stage joins between already-onboarded models as a reviewable "
                    "changeset — use after onboarding tables to wire up the "
                    "cross-schema relationships a BI doc describes. Both endpoint "
                    "models must already exist in the project."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "relationships": {
                            "type": "array",
                            "description": "Relationships (joins) to add.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "description": "Optional relationship name.",
                                    },
                                    "models": {
                                        "type": "array",
                                        "description": (
                                            "Exactly two model names to join."
                                        ),
                                        "items": {"type": "string"},
                                    },
                                    "joinType": {
                                        "type": "string",
                                        "description": (
                                            "ONE_TO_ONE | ONE_TO_MANY | MANY_TO_ONE "
                                            "| MANY_TO_MANY."
                                        ),
                                    },
                                    "condition": {
                                        "type": "string",
                                        "description": (
                                            "Join condition, e.g. "
                                            "'orders.customer_id = customers.id'."
                                        ),
                                    },
                                },
                                "required": ["models", "joinType", "condition"],
                            },
                        },
                    },
                    "required": ["relationships"],
                },
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
                name="read_document",
                description=(
                    "Read the full text of one uploaded document by id — use it to "
                    "extract the complete set of entities, joins, and metric "
                    "definitions a BI doc describes (search_documents only returns "
                    "top passages and can miss the section that lists them). Text "
                    "is truncated past 'max_chars'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "document_id": {
                            "type": "string",
                            "description": "Id of the document (see list_documents).",
                        },
                        "max_chars": {
                            "type": "integer",
                            "description": (
                                "Max characters to return (default "
                                f"{_READ_DOCUMENT_MAX_CHARS})."
                            ),
                        },
                    },
                    "required": ["document_id"],
                },
            ),
            ToolSpec(
                name="run_coverage",
                description=(
                    "Audit how well the current (staged) MDL captures the "
                    "information in the project's documents — your self-review step "
                    "after onboarding/enriching. Returns a score and the specific "
                    "claims that are missing or only partially modeled, so you can "
                    "add what's left before handing the changeset to the user. "
                    "Read-only; capped per turn."
                ),
                parameters={"type": "object", "properties": {}},
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
            "patch_mdl_file": self._patch_mdl_file,
            "add_golden_query": self._add_golden_query,
            "delete_mdl_file": self._delete_mdl_file,
            "remove_mdl_entity": self._remove_mdl_entity,
            "validate_project": self._validate_project,
            "get_physical_schema": self._get_physical_schema,
            "find_tables": self._find_tables,
            "read_document": self._read_document,
            "propose_onboard_table": self._propose_onboard_table,
            "propose_onboard_tables": self._propose_onboard_tables,
            "propose_relationships": self._propose_relationships,
            "list_documents": self._list_documents,
            "search_documents": self._search_documents,
            "run_coverage": self._run_coverage,
            "find_duplicate_documents": self._find_duplicate_documents,
        }.get(name)
        if handler is None:
            return {"error": f"Unknown tool {name!r}."}
        try:
            result = handler(arguments or {})
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Copilot tool %s failed: %s", name, ex)
            result = {"error": str(ex)}
        if name in _MUTATING_ACTIONS:
            self._record_mutation(name, arguments or {}, result)
        return result

    # -- provenance ledger -------------------------------------------------

    def _record_mutation(
        self, name: str, args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Append one ``ToolCallRecord`` for a mutating tool call (best-effort).

        Records the verb, the files touched (from the tool result, which the
        diff later treats as authoritative for paths), the docs that grounded
        this call (the ``search_documents`` delta since the previous mutation —
        a single-doc grounding is stamped per-file for R-B6), and a
        sensitivity-aware argument *shape* (names/counts only, never MDL JSON).
        """

        # Grounding = docs searched since the previous mutating call.
        grounding = self._referenced_document_ids[self._grounding_watermark :]
        self._grounding_watermark = len(self._referenced_document_ids)

        action = _MUTATING_ACTIONS[name]
        paths, args_summary, detail = _summarize_mutation(name, args, result)
        status = "error" if isinstance(result, dict) and "error" in result else "ok"
        if status == "error":
            detail = str(result.get("error")) if isinstance(result, dict) else detail
        self._tool_calls.append(
            ToolCallRecord(
                tool=name,
                action=action,
                paths=paths,
                source_document_ids=list(grounding),
                args_summary=args_summary,
                status=status,
                detail=detail,
            )
        )
        # R-B6: a single grounding doc links the written file(s) to their source.
        if status == "ok" and len(grounding) == 1:
            for path in paths:
                self._file_grounding[path] = grounding[0]

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
        return self._stage_content(path, content, args.get("summary"))

    def _add_golden_query(self, args: dict[str, Any]) -> dict[str, Any]:
        """Stage a curated golden query into the project's ``queries.json`` (F3).

        Idempotent on the normalized question (re-adding refreshes in place). The
        SQL must be written against the semantic model's logical names. The staged
        change lands as a reviewable draft like any other file edit — a human
        accepts it; activation stays separate.
        """

        question = args.get("question")
        semantic_sql = args.get("semantic_sql")
        name = args.get("name") or question
        if not isinstance(question, str) or not question.strip():
            return {"error": "add_golden_query requires a non-empty 'question'."}
        if not isinstance(semantic_sql, str) or not semantic_sql.strip():
            return {"error": "add_golden_query requires non-empty 'semantic_sql'."}
        entry = GoldenQuery(
            name=str(name),
            question=question,
            semantic_sql=semantic_sql,
            usage_guidance=(
                str(args["usage_guidance"]) if args.get("usage_guidance") else None
            ),
            use_as_onboarding=bool(args.get("use_as_onboarding", False)),
        )
        current = self._working.get(GOLDEN_QUERIES_PATH)
        try:
            content = upsert_golden_query(current, entry)
        except (ValueError, TypeError) as ex:
            return {"error": f"Existing queries.json is invalid: {ex}"}
        result = self._stage_content(
            GOLDEN_QUERIES_PATH, content, args.get("summary") or f"Golden: {name}"
        )
        result["golden_query"] = entry.name
        return result

    def _stage_content(self, path: str, content: str, summary: Any) -> dict[str, Any]:
        """Stage full file content into the working copy and validate it.

        Shared by ``write_mdl_file`` (full overwrite) and ``patch_mdl_file`` (merged
        overlay). Full-content overwrite is the code-editor model, but an LLM that
        re-emits a file can silently drop the Superset-extension ``properties``
        (displayName/alias/synonyms) that back governance + retrieval. wren-core
        tolerates the omission, so validation never catches it. Restore any dropped
        keys against the prior version of this file (additive; the agent can still
        *edit* a property, just not silently *delete* one). For ``patch_mdl_file``
        the merge already preserved them, so this guard is a defensive no-op there.
        """

        # Golden queries (queries.json) are a sibling knowledge artifact, not an MDL
        # manifest: skip the MDL property-preservation guard and validate as golden.
        if is_golden_queries_path(path):
            self._working[path] = content
            if summary:
                self._summaries[path] = str(summary)
            return {
                "path": path,
                "validation": validate_golden_queries(content).model_dump(
                    mode="json"
                ),
            }
        prior = self._working.get(path)
        restored = False
        if prior is not None:
            preserved = _preserve_superset_properties(prior, content)
            restored = preserved != content
            content = preserved
        self._working[path] = content
        if summary:
            self._summaries[path] = str(summary)
        validation = validate_mdl(
            content, schema_index=self._schema_index, strict_models=True
        )
        result: dict[str, Any] = {
            "path": path,
            "validation": validation.model_dump(mode="json"),
        }
        if restored:
            result["note"] = (
                "Restored Superset `properties` (displayName/alias/synonyms) that "
                "the new content omitted — these back governance and retrieval and "
                "must be preserved."
            )
        return result

    def _patch_mdl_file(self, args: dict[str, Any]) -> dict[str, Any]:
        """Merge a sparse, name-keyed overlay onto an existing file (token-efficient).

        The agent emits only the entities/columns it changes; the overlay is merged
        onto the file's *current working* content via the shared structure-preserving
        merge, so omitted entities/columns — and their ``properties`` — survive by
        construction. Refines existing files only: creating, restructuring, or
        removing an entity stays with ``write_mdl_file`` (P4/D2).
        """

        path = normalize_mdl_path(self._require_path(args))
        if path not in self._working:
            return {
                "error": (
                    f"No MDL file at {path!r} to patch. patch_mdl_file refines an "
                    "existing file by merging a partial overlay; use write_mdl_file "
                    "to create a new file."
                )
            }
        overlay = _parse_overlay(args.get("overlay"))
        if overlay is None:
            return {
                "error": (
                    "patch_mdl_file requires a non-empty 'overlay' — an MDL JSON "
                    "object (or JSON string) carrying only the changed entities/"
                    "columns, keyed by name."
                )
            }
        try:
            base = json.loads(self._working[path])
        except (ValueError, TypeError):
            return {"error": f"Working copy of {path!r} is not valid JSON."}
        if not isinstance(base, dict):
            return {"error": f"Working copy of {path!r} is not a JSON object."}

        merged = merge_manifest_sections(base, overlay)
        result = self._stage_content(
            path, json.dumps(merged, indent=2), args.get("summary")
        )
        matched, appended = _overlay_entity_names(base, overlay)
        ignored = _ignored_structural_edits(base, overlay)
        result["patched"] = {
            "matched": sorted(matched),
            "appended": sorted(appended),
            "ignored_structural": ignored,
        }
        notes: list[str] = []
        if appended:
            notes.append(
                "Overlay introduced new entit(ies) not in the base file: "
                f"{sorted(appended)}. If you meant to refine an existing entity, "
                "check the name; otherwise this is an intentional addition."
            )
        if ignored:
            # The additive merge keeps a column's physical/structural fields from
            # the base (E4), so these overlay changes were silently NOT applied.
            # Surface them so the agent re-issues a structural edit the right way.
            notes.append(
                "patch_mdl_file preserves existing physical/structural column "
                f"fields, so these overlay changes were NOT applied: {ignored}. To "
                "change a column's type/expression/isCalculated/notNull, use "
                "write_mdl_file (full-content overwrite)."
            )
        if notes:
            result["note"] = f"{result.get('note', '')} {' '.join(notes)}".strip()
        return result

    def _delete_mdl_file(self, args: dict[str, Any]) -> dict[str, Any]:
        path = self._require_path(args)
        if path not in self._working:
            # delete_mdl_file is whole-file by path. To remove one entity inside a
            # file (a model, relationship, metric, or calculated column) use
            # remove_mdl_entity; to relocate one, remove it here and write it back
            # elsewhere with write_mdl_file.
            return {
                "error": (
                    f"No MDL file at {path!r} to delete. delete_mdl_file removes "
                    "whole files by path; to remove a single model/relationship/"
                    "metric/calculated column, use remove_mdl_entity."
                )
            }
        del self._working[path]
        if args.get("summary"):
            self._summaries[path] = str(args["summary"])
        return {"path": path, "deleted": True}

    def _remove_mdl_entity(self, args: dict[str, Any]) -> dict[str, Any]:
        """Remove named entities (or a calculated column) from an existing file.

        The inverse of ``patch_mdl_file``: instead of re-emitting the whole file to
        drop something, name what to remove. Upholds physical authority — a physical
        (non-``isCalculated``) column is refused, since it comes from the catalog.
        Each removal is validated and applied to the working copy; a removal that
        empties the file deletes the file (DC-D4). Invalid/absent targets are
        reported per-item so a batch with one bad entry still applies the rest.
        """

        path = normalize_mdl_path(self._require_path(args))
        if path not in self._working:
            return {
                "error": (
                    f"No MDL file at {path!r} to edit. remove_mdl_entity removes "
                    "entities from an existing file; there is nothing to remove here."
                )
            }
        removals = args.get("removals")
        if not isinstance(removals, list) or not removals:
            return {
                "error": (
                    "remove_mdl_entity requires a non-empty 'removals' list of "
                    "{section, name, column?} objects."
                )
            }
        try:
            base = json.loads(self._working[path])
        except (ValueError, TypeError):
            return {"error": f"Working copy of {path!r} is not valid JSON."}
        if not isinstance(base, dict):
            return {"error": f"Working copy of {path!r} is not a JSON object."}

        valid, rejected = self._validate_removals(base, removals)
        if not valid:
            return {"removed": [], "rejected": rejected}

        new_base, removed, missing = remove_manifest_entities(base, valid)
        if _manifest_is_empty(new_base):
            # The last entity was removed — drop the now-empty file (DC-D4) rather
            # than stage an empty-root manifest the activation gate would reject.
            del self._working[path]
            if args.get("summary"):
                self._summaries[path] = str(args["summary"])
            result: dict[str, Any] = {"path": path, "deleted": True, "removed": removed}
        else:
            result = self._stage_content(
                path, json.dumps(new_base, indent=2), args.get("summary")
            )
            result["removed"] = removed
        if missing:
            result["missing"] = missing
        if rejected:
            result["rejected"] = rejected
        return result

    def _validate_removals(
        self, base: dict[str, Any], removals: list[Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split a removal batch into (valid, rejected), enforcing physical authority.

        A removal targets a known section and names an entity; a column removal must
        target a *calculated* column (a physical column comes from the catalog and
        cannot be removed, DC-D3). Order/shape problems are rejected per-item, never
        raised, so one bad entry doesn't drop the whole batch.
        """

        valid: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for removal in removals:
            if not isinstance(removal, dict):
                rejected.append({"removal": removal, "error": "Invalid entry."})
                continue
            section = removal.get("section")
            name = removal.get("name")
            column = removal.get("column")
            if not isinstance(section, str) or section not in MERGE_SECTIONS:
                rejected.append(
                    {
                        "removal": removal,
                        "error": f"'section' must be one of {sorted(MERGE_SECTIONS)}.",
                    }
                )
                continue
            if not isinstance(name, str) or not name.strip():
                rejected.append({"removal": removal, "error": "A 'name' is required."})
                continue
            if column is not None and (
                not isinstance(column, str) or not column.strip()
            ):
                rejected.append(
                    {"removal": removal, "error": "'column' must be a string."}
                )
                continue
            if column:
                existing = _find_column(base, name, column)
                if existing is not None and not existing.get("isCalculated"):
                    rejected.append(
                        {
                            "removal": removal,
                            "error": (
                                f"Column '{column}' on '{name}' is a physical column; "
                                "physical columns come from the catalog and cannot be "
                                "removed. Only calculated columns (isCalculated) can."
                            ),
                        }
                    )
                    continue
            entry: dict[str, Any] = {"section": section, "name": name}
            if column:
                entry["column"] = column
            valid.append(entry)
        return valid, rejected

    def _validate_project(self, _args: dict[str, Any]) -> dict[str, Any]:
        return self.validate_working().model_dump(mode="json")

    def _get_physical_schema(self, _args: dict[str, Any]) -> dict[str, Any]:
        if self._schema_index is None:
            return {"tables": {}, "note": "No physical schema available."}
        index = self._schema_index
        if index.is_multi_schema():
            # F1: a cross-schema project must surface each table UNDER its schema so
            # the agent can author a correct tableReference.schema, and same-named
            # tables across schemas don't collide. The flat `tables` shape drops the
            # schema and silently hides one of any collision.
            return {
                "schemas": index.schema_qualified_view(),
                "note": (
                    "This project spans multiple schemas. Each table is listed under "
                    "its physical schema; set that schema in the model's "
                    'tableReference ({"schema": ..., "table": ...}).'
                ),
            }
        result: dict[str, Any] = {"tables": index.to_tables()}
        if index.has_types():
            result["column_types"] = index.typed_tables()
        return result

    def _find_tables(self, args: dict[str, Any]) -> dict[str, Any]:
        """Rank the project's physical tables against a free-text query (read-only).

        Targeted discovery for the doc→table mapping step: returns only the top
        candidates with their columns, never the whole schema (which floods the
        context on a real warehouse). Ranks over the permission-filtered
        ``SchemaIndex``, so a table outside the project's accessible schemas is
        never surfaced — and an empty result is the honest "no match" signal.
        """

        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"error": "find_tables requires a 'query' string."}
        if self._schema_index is None:
            return {"tables": [], "note": "No physical schema available."}
        schema = args.get("schema")
        schema = schema if isinstance(schema, str) and schema.strip() else None
        raw_limit = args.get("limit")
        limit = raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else 10
        matches = self._schema_index.search(query, schema=schema, limit=limit)
        tables: list[dict[str, Any]] = []
        for match_schema, table, score in matches:
            columns: list[dict[str, Any]] = []
            for column in self._schema_index.columns_for(table, match_schema):
                entry: dict[str, Any] = {"name": column}
                column_type = self._schema_index.column_type(
                    table, column, match_schema
                )
                if column_type:
                    entry["type"] = column_type
                columns.append(entry)
            candidate: dict[str, Any] = {
                "table": table,
                "columns": columns,
                "score": round(score, 2),
            }
            if match_schema:
                candidate["schema"] = match_schema
            tables.append(candidate)
        return {"tables": tables}

    def _read_document(self, args: dict[str, Any]) -> dict[str, Any]:
        """Return one document's full extracted text (read-only, bounded).

        Complements ``search_documents`` (top-k passages) for *extraction* tasks
        where the agent needs the whole BI doc to enumerate every entity/join/
        metric. Falls back to the document's chunks when the flat extract is
        absent; truncates past ``max_chars`` and flags it.
        """

        document_id = args.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            return {"error": "read_document requires a 'document_id' string."}
        if not self._documents_available:
            return {"error": "No documents available."}
        assert self._document_store is not None
        assert self._project_id is not None
        raw_max = args.get("max_chars")
        max_chars = (
            raw_max
            if isinstance(raw_max, int) and raw_max > 0
            else _READ_DOCUMENT_MAX_CHARS
        )
        documents = self._document_store.list_project_documents(
            self._project_id, owner_id=self._owner_id
        )
        document = next((doc for doc in documents if doc.id == document_id), None)
        if document is None:
            return {"error": f"No document {document_id!r} in this project."}
        text = document.extracted_text or ""
        if not text:
            chunks = [
                chunk
                for chunk in self._document_store.list_project_chunks(
                    self._project_id, owner_id=self._owner_id
                )
                if chunk.document_id == document_id
            ]
            chunks.sort(key=lambda chunk: chunk.chunk_index)
            text = "\n".join(chunk.text for chunk in chunks)
        return {
            "filename": document.filename,
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    def _propose_onboard_table(self, args: dict[str, Any]) -> dict[str, Any]:
        """Generate + stage a base model for one physical table (R1-safe).

        Builds the model from the permission-filtered ``SchemaIndex`` only — a table
        absent from the project's accessible schemas is rejected, never invented —
        then routes through ``write_mdl_file`` so the same validation + staging apply.
        """

        table = args.get("table")
        if not isinstance(table, str) or not table.strip():
            return {"error": "propose_onboard_table requires a 'table' name."}
        schema = args.get("schema")
        schema = schema if isinstance(schema, str) and schema.strip() else None
        return self._stage_onboard_table(table, schema)

    def _propose_onboard_tables(self, args: dict[str, Any]) -> dict[str, Any]:
        """Onboard several physical tables in one call (the BI-doc, cross-schema flow).

        Each item ``{table, schema?}`` is staged through the same R1-safe path as the
        singular tool: every table is checked against the project's accessible schemas
        and rejected (never invented) if absent. Valid tables are staged; rejected ones
        are reported per-table so the agent can correct the batch — partial success is
        intentional (one bad name shouldn't drop the rest of a multi-schema onboard).
        """

        items = args.get("tables")
        if not isinstance(items, list) or not items:
            return {
                "error": (
                    "propose_onboard_tables requires a non-empty 'tables' list of "
                    "{table, schema?} objects."
                )
            }
        onboarded: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                table, schema = item, None
            elif isinstance(item, dict):
                raw_table = item.get("table")
                table = raw_table if isinstance(raw_table, str) else ""
                raw_schema = item.get("schema")
                schema = (
                    raw_schema
                    if isinstance(raw_schema, str) and raw_schema.strip()
                    else None
                )
            else:
                rejected.append({"table": item, "error": "Invalid table entry."})
                continue
            if not table.strip():
                rejected.append({"table": table, "error": "Missing table name."})
                continue
            result = self._stage_onboard_table(table, schema)
            if "error" in result:
                rejected.append({"table": table, "error": result["error"]})
            else:
                onboarded.append({"table": table, "path": result.get("path")})
        return {"onboarded": onboarded, "rejected": rejected}

    def _stage_onboard_table(self, table: str, schema: str | None) -> dict[str, Any]:
        """Shared R1-safe onboarding core for the singular + plural onboard tools."""

        if self._schema_index is None:
            return {"error": "No physical schema is available to onboard from."}
        if not self._schema_index.has_table(table, schema):
            qualified = f"{schema}.{table}" if schema else table
            return {
                "error": (
                    f"Table '{qualified}' is not in the project's accessible "
                    "schemas; add the schema to the project before onboarding it."
                )
            }
        table_ref: dict[str, Any] = {"table": table}
        if schema:
            table_ref["schema"] = schema
        columns_payload: list[dict[str, Any]] = []
        for column in self._schema_index.columns_for(table, schema):
            entry: dict[str, Any] = {"name": column}
            column_type = self._schema_index.column_type(table, column, schema)
            if column_type:
                entry["type"] = column_type
            columns_payload.append(entry)
        model: dict[str, Any] = {
            "name": _safe_model_name(table),
            "tableReference": table_ref,
            "columns": columns_payload,
        }
        content = json.dumps({"models": [model]}, indent=2)
        path = f"models/{_safe_model_name(table)}.json"
        result = self._write_mdl_file(
            {"path": path, "content": content, "summary": f"Onboard {table}"}
        )
        result["onboarded_table"] = table
        return result

    def _propose_relationships(self, args: dict[str, Any]) -> dict[str, Any]:
        """Stage cross-model relationships (joins) as a reviewable changeset.

        Relationships connect models that were *already* onboarded through the
        access-proof path, so the R1 schema-in-set invariant is upheld upstream: this
        tool only joins existing logical models, never reaches a physical table. Each
        ``{models:[m1,m2], joinType, condition, name?}`` is checked — both endpoints
        must be defined models, ``joinType`` must be a known wren-core join type, and a
        non-empty ``condition`` is required — then staged under ``relationships/``.
        """

        items = args.get("relationships")
        if not isinstance(items, list) or not items:
            return {
                "error": (
                    "propose_relationships requires a non-empty 'relationships' list "
                    "of {models:[m1,m2], joinType, condition, name?} objects."
                )
            }
        known_models = self._working_model_names()
        staged: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                rejected.append({"relationship": item, "error": "Invalid entry."})
                continue
            endpoints = item.get("models")
            if (
                not isinstance(endpoints, list)
                or len(endpoints) != 2
                or not all(isinstance(m, str) and m.strip() for m in endpoints)
            ):
                rejected.append(
                    {"relationship": item, "error": "Need exactly two model names."}
                )
                continue
            missing = [m for m in endpoints if m not in known_models]
            if missing:
                rejected.append(
                    {
                        "relationship": item,
                        "error": (
                            f"Unknown model(s) {missing}; onboard them first "
                            "(propose_onboard_table) before relating them."
                        ),
                    }
                )
                continue
            join_type = item.get("joinType")
            if not isinstance(join_type, str) or join_type.upper() not in JOIN_TYPES:
                rejected.append(
                    {
                        "relationship": item,
                        "error": (
                            f"Invalid joinType {join_type!r}; expected one of "
                            f"{sorted(JOIN_TYPES)}."
                        ),
                    }
                )
                continue
            condition = item.get("condition")
            if not isinstance(condition, str) or not condition.strip():
                rejected.append(
                    {"relationship": item, "error": "A 'condition' is required."}
                )
                continue
            name = item.get("name")
            name = (
                name
                if isinstance(name, str) and name.strip()
                else f"{endpoints[0]}_{endpoints[1]}"
            )
            relationship: dict[str, Any] = {
                "name": name,
                "models": endpoints,
                "joinType": join_type.upper(),
                "condition": condition,
            }
            content = json.dumps({"relationships": [relationship]}, indent=2)
            path = f"relationships/{_safe_model_name(name)}.json"
            result = self._write_mdl_file(
                {
                    "path": path,
                    "content": content,
                    "summary": f"Relate {endpoints[0]} ↔ {endpoints[1]}",
                }
            )
            staged.append({"name": name, "path": result.get("path")})
        return {"staged": staged, "rejected": rejected}

    def _working_model_names(self) -> set[str]:
        """Collect the names of every model defined across the working set."""

        names: set[str] = set()
        for content in self._working.values():
            try:
                parsed = json.loads(content)
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            for model in parsed.get("models", []):
                if isinstance(model, dict) and isinstance(model.get("name"), str):
                    names.add(model["name"])
        return names

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
        for chunk in ranked:
            if chunk.document_id not in self._referenced_document_ids:
                self._referenced_document_ids.append(chunk.document_id)
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

    def _coverage_documents(self) -> list[CoverageDocument]:
        """Build the audit corpus from the project's documents (chunks→text)."""

        if self._document_store is None or self._project_id is None:
            return []
        chunks_by_doc: dict[str, list[DocumentChunk]] = {}
        for chunk in self._document_store.list_project_chunks(
            self._project_id, owner_id=self._owner_id
        ):
            chunks_by_doc.setdefault(chunk.document_id, []).append(chunk)
        documents: list[CoverageDocument] = []
        for document in self._document_store.list_project_documents(
            self._project_id, owner_id=self._owner_id
        ):
            doc_chunks = sorted(
                chunks_by_doc.get(document.id, []), key=lambda c: c.chunk_index
            )
            text = "\n\n".join(c.text for c in doc_chunks) or (
                document.extracted_text or ""
            )
            if text.strip():
                documents.append(
                    CoverageDocument(
                        document_id=document.id,
                        filename=document.filename,
                        text=text,
                    )
                )
        return documents

    def _run_coverage(self, _args: dict[str, Any]) -> dict[str, Any]:
        """Self-review the staged MDL against the project documents (read-only).

        Audits the *working* set (the agent's drafts), so the agent can find gaps
        and refine before the user reviews — the in-conversation analogue of the
        background coverage run (which audits only *active* MDL post-activation).
        Per-turn capped + memoized: identical working sets re-audit free and do not
        count against the cap. Creates no provenance and persists nothing.
        """

        if self._model_client is None:
            return {"note": "Coverage is unavailable (no model client)."}
        documents = self._coverage_documents()
        if not documents:
            return {
                "note": "No documents to audit; upload a BI doc to run coverage.",
                "score": 1.0,
            }
        # Memo key over the working content + the doc corpus shape — a re-audit of
        # an unchanged set is free and does not consume the per-turn budget.
        memo_key = _coverage_memo_key(self._working, documents)
        cached = self._coverage_memo.get(memo_key)
        if cached is not None:
            return cached
        if self._coverage_audits_done >= self._coverage_self_audit_limit:
            return {
                "note": (
                    "Coverage self-audit limit reached for this turn; apply your "
                    "changes and the background audit will run on activation."
                )
            }
        self._coverage_audits_done += 1
        files = [
            SimpleNamespace(content=content, status="active")
            for content in self._working.values()
        ]
        report = run_directory_coverage(
            self._model_client,
            documents=documents,
            files=files,
            instructions=self._instructions,
            embedder=self._embedder,
        )
        result = {
            "score": report.score,
            "total": report.total,
            "covered": report.covered,
            "partial": report.partial,
            "missing": report.missing,
            "findings": [
                {
                    "kind": finding.claim.kind,
                    "subject": finding.claim.subject,
                    "statement": finding.claim.statement,
                    "status": finding.status,
                    "suggestion": finding.suggestion,
                    "document": finding.document_filename,
                }
                for finding in report.findings
                if finding.status != "covered"
            ],
            "warnings": report.warnings,
        }
        self._coverage_memo[memo_key] = result
        return result

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
                        source_document_id=self._file_grounding.get(path),
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
                        source_document_id=self._file_grounding.get(path),
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
            referenced_document_ids=list(self._referenced_document_ids),
            tool_calls=list(self._tool_calls),
        )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _require_path(args: dict[str, Any]) -> str:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Tool call requires a 'path' string.")
        return path


def _parse_overlay(value: Any) -> dict[str, Any] | None:
    """Parse a ``patch_mdl_file`` overlay leniently (D1/R11).

    Accepts an already-decoded object (some providers hand back parsed JSON) or a
    JSON string (matches ``write_mdl_file``'s ``content`` convention). Returns
    ``None`` for anything empty/unparseable so the caller can return a correctable
    error the loop feeds back to the model.
    """

    if isinstance(value, dict):
        return value or None
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) and parsed else None
    return None


#: Column fields the structure-preserving merge takes from the BASE, never the
#: overlay (physical authority, E4). A patch that sets one to a new value silently
#: no-ops; we surface that so the agent re-issues it via ``write_mdl_file``.
_MERGE_IGNORED_COLUMN_FIELDS: tuple[str, ...] = (
    "type",
    "expression",
    "isCalculated",
    "notNull",
    "relationship",
)


def _ignored_structural_edits(
    base: dict[str, Any], overlay: dict[str, Any]
) -> list[str]:
    """List overlay column fields the additive merge drops (a silent no-op).

    The merge refines only ``description`` and ``properties`` on an *existing*
    column; an overlay that sets ``type``/``expression``/etc. to a value differing
    from the base is silently ignored (E4 physical authority). Returns
    ``model.column.field`` labels for those, so ``patch_mdl_file`` can warn the
    agent to make a structural edit through ``write_mdl_file`` instead. A brand-new
    column (or model) appends whole, so its fields are kept — not reported here.
    """

    ignored: list[str] = []
    base_models = {
        model["name"]: model
        for model in (base.get("models") or [])
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }
    for model in overlay.get("models") or []:
        if not isinstance(model, dict):
            continue
        base_model = base_models.get(model.get("name"))
        if base_model is None:
            continue
        base_cols = {
            col["name"]: col
            for col in (base_model.get("columns") or [])
            if isinstance(col, dict) and isinstance(col.get("name"), str)
        }
        for col in model.get("columns") or []:
            if not isinstance(col, dict):
                continue
            base_col = base_cols.get(col.get("name"))
            if base_col is None:
                continue
            for field in _MERGE_IGNORED_COLUMN_FIELDS:
                if field in col and col[field] != base_col.get(field):
                    ignored.append(f"{model['name']}.{col['name']}.{field}")
    return ignored


def _find_column(
    base: dict[str, Any], model_name: str, column_name: str
) -> dict[str, Any] | None:
    """Return the named column dict on the named model, or ``None`` if absent."""

    for model in base.get("models", []) or []:
        if isinstance(model, dict) and model.get("name") == model_name:
            for column in model.get("columns", []) or []:
                if isinstance(column, dict) and column.get("name") == column_name:
                    return column
    return None


def _manifest_is_empty(manifest: dict[str, Any]) -> bool:
    """True when a manifest holds no entities in any mergeable section."""

    return not any(
        isinstance(manifest.get(section), list) and manifest.get(section)
        for section in MERGE_SECTIONS
    )


def _overlay_entity_names(
    base: dict[str, Any], overlay: dict[str, Any]
) -> tuple[set[str], set[str]]:
    """Split overlay entity names into matched-in-base vs newly-appended (D8).

    Surfaced in the tool result so a typo'd name (which the additive merge would
    otherwise append silently) is visible to the agent and the reviewer.
    """

    matched: set[str] = set()
    appended: set[str] = set()
    for section in MERGE_SECTIONS:
        base_names = {
            item["name"]
            for item in (base.get(section) or [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        for item in overlay.get(section) or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                target = matched if item["name"] in base_names else appended
                target.add(item["name"])
    return matched, appended


def _safe_model_name(table: str) -> str:
    """Identifier-safe model name from a physical table name."""

    lowered = table.strip().lower()
    cleaned = "".join(char if char.isalnum() else "_" for char in lowered)
    name = "_".join(part for part in cleaned.split("_") if part)
    return name or "model"


#: Mutating tool name → its semantic provenance verb. Read-only tools are absent
#: (not recorded in the ledger). Extend additively as the toolset grows.
_MUTATING_ACTIONS: dict[str, ToolActionKind] = {
    "write_mdl_file": "write",
    "patch_mdl_file": "write",
    "add_golden_query": "curate",
    "delete_mdl_file": "delete",
    "remove_mdl_entity": "remove",
    "propose_onboard_table": "onboard",
    "propose_onboard_tables": "onboard",
    "propose_relationships": "relate",
}


def _collect(rows: object, *keys: str) -> dict[str, list[str]]:
    """Collect string values per key across a list of ``{key: value}`` dicts."""

    out: dict[str, list[str]] = {key: [] for key in keys}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                for key in keys:
                    value = row.get(key)
                    if isinstance(value, str):
                        out[key].append(value)
    return out


def _summarize_mutation(
    name: str, args: dict[str, Any], result: dict[str, Any]
) -> tuple[list[str], dict[str, object], str | None]:
    """Derive (paths, sensitivity-aware arg shape, human note) for one mutation.

    Reads affected paths from the tool *result* (authoritative once applied) and
    captures only argument *shapes* — names/counts, never the raw MDL JSON the
    file content already carries.
    """

    if not isinstance(result, dict):
        return [], {}, None
    if name in ("write_mdl_file", "patch_mdl_file", "delete_mdl_file"):
        path = result.get("path")
        paths = [path] if isinstance(path, str) else []
        return paths, {"path": path} if paths else {}, None
    if name == "remove_mdl_entity":
        path = result.get("path")
        paths = [path] if isinstance(path, str) else []
        removed = result.get("removed")
        removed = removed if isinstance(removed, list) else []
        summary = {"removed": removed, "removed_count": len(removed)} if removed else {}
        detail = f"{len(removed)} entit(ies)" if removed else None
        return paths, summary, detail
    if name == "propose_onboard_table":
        path = result.get("path")
        paths = [path] if isinstance(path, str) else []
        table = result.get("onboarded_table") or args.get("table")
        summary = {"tables": [table]} if isinstance(table, str) else {}
        return paths, summary, table if isinstance(table, str) else None
    if name == "propose_onboard_tables":
        collected = _collect(result.get("onboarded"), "path", "table")
        names = collected["table"]
        summary = {"tables": names, "table_count": len(names)} if names else {}
        detail = f"{len(names)} table(s)" if names else None
        return collected["path"], summary, detail
    if name == "propose_relationships":
        collected = _collect(result.get("staged"), "path", "name")
        names = collected["name"]
        summary = (
            {"relationships": names, "relationship_count": len(names)} if names else {}
        )
        detail = f"{len(names)} relationship(s)" if names else None
        return collected["path"], summary, detail
    return [], {}, None


def _coverage_memo_key(
    working: dict[str, str], documents: list[CoverageDocument]
) -> str:
    """Stable key over the working MDL + the document corpus shape (per-turn memo)."""

    mdl = sorted(f"{path}:{_normalize(content)}" for path, content in working.items())
    docs = sorted(f"{doc.document_id}:{len(doc.text)}" for doc in documents)
    return json.dumps([mdl, docs], separators=(",", ":"))


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
