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

"""Project-scoped golden queries (F3) — Wren ``queries.yml`` / Cortex VQR analogue.

A *golden query* is a curated, human-verified ``question -> SQL`` example stored as
one entry in a project's ``queries.json`` MDL file (a sibling of the model files,
**not** part of the wren-core manifest — the assembler ignores its ``queries`` key).
SQL is authored in **semantic** (model-name) form, like Wren and Cortex; the
referenced physical tables used by the access filter are derived from the active
manifest's ``tableReference`` (see ``golden_query_refs``), not parsed from native SQL.

This module owns the on-disk shape (parse/dump), structural validation, and the
recall helpers that merge golden queries into the few-shot prompt alongside the
database-scoped runtime memory (F1/F2). Field names mirror the Cortex VQR proto.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent MDL file contract
from typing import Any

from pydantic import BaseModel, Field

from superset_ai_agent.semantic_layer.schemas import (
    MdlValidationMessage,
    MdlValidationResult,
)

#: Reserved project-relative path that marks a file as the golden-query store.
#: Used as the file-kind discriminator (DP-8): a file at this path is validated as
#: golden queries, never as an MDL manifest.
GOLDEN_QUERIES_PATH = "queries.json"


def is_golden_queries_path(path: str | None) -> bool:
    """Whether ``path`` is the project's golden-query file (kind discriminator)."""

    if not path:
        return False
    return path.strip().lstrip("/").lower() == GOLDEN_QUERIES_PATH


class GoldenQuery(BaseModel):
    """One curated, human-verified NL->SQL example (Cortex VQR field names)."""

    name: str
    question: str
    #: SQL written against the semantic model's logical names (Wren/Cortex rule).
    semantic_sql: str
    verified_by: str | None = None
    #: Seconds since the UNIX epoch (Cortex ``verified_at``); ``None`` until verified.
    verified_at: int | None = None
    #: Surface deterministically as a project starter question (Cortex onboarding).
    use_as_onboarding: bool = False
    #: Optional "when is this relevant" hint (Genie "usage guidance").
    usage_guidance: str | None = None


class GoldenQueriesFile(BaseModel):
    """The full ``queries.json`` document: a list of golden-query entries."""

    queries: list[GoldenQuery] = Field(default_factory=list)


class GoldenQueryPromoteRequest(BaseModel):
    """Promote a (runtime or hand-written) NL->SQL pair into the project's golden set.

    A **copy**, never a move: promoting does not touch the database-scoped runtime
    memory — the learned pair stays in its shared pool (see the copy-not-move
    invariant). ``semantic_sql`` is preferred; ``native_sql`` is accepted as a
    fallback when a promotion originates from a passthrough (non-semantic) answer.
    """

    question: str
    semantic_sql: str | None = None
    native_sql: str | None = None
    name: str | None = None
    use_as_onboarding: bool = False
    usage_guidance: str | None = None


def find_golden_queries_file(files: list[Any]) -> Any | None:
    """Return the project's ``queries.json`` file (any status) from a file list."""

    return next(
        (f for f in files if is_golden_queries_path(getattr(f, "path", None))), None
    )


def parse_golden_queries(content: str) -> GoldenQueriesFile:
    """Parse ``queries.json`` content; raises on malformed JSON/shape."""

    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("queries.json must be a JSON object with a 'queries' list.")
    return GoldenQueriesFile.model_validate(data)


def dump_golden_queries(file: GoldenQueriesFile) -> str:
    """Serialize a golden-query file to stable, human-diffable JSON."""

    return json.dumps(
        file.model_dump(mode="json"), indent=2, sort_keys=False, ensure_ascii=False
    )


def upsert_golden_query(content: str | None, entry: GoldenQuery) -> str:
    """Insert or refresh ``entry`` in ``content`` keyed by normalized question.

    Promotion/authoring is idempotent on the question text, so re-promoting the
    same question refreshes the entry in place rather than duplicating it.
    """

    file = (
        parse_golden_queries(content)
        if content and content.strip()
        else GoldenQueriesFile()
    )
    key = " ".join(entry.question.lower().split())
    kept = [
        existing
        for existing in file.queries
        if " ".join(existing.question.lower().split()) != key
    ]
    kept.append(entry)
    return dump_golden_queries(GoldenQueriesFile(queries=kept))


def validate_golden_queries(content: str) -> MdlValidationResult:
    """Structural validation for a ``queries.json`` file (kind-aware gate, F3/2A).

    This is the validation run *instead of* ``validate_mdl`` for the golden-query
    file kind — ``validate_mdl`` would reject it for having no models. Checks: valid
    JSON object, a ``queries`` list, and that each entry carries ``name``,
    ``question``, and ``semantic_sql``. Manifest resolution of the semantic SQL's
    model names is enforced separately at activation (validation-on-verify).
    """

    try:
        data = json.loads(content)
    except (ValueError, TypeError) as ex:
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(message=f"Invalid JSON: {ex}", code="parse_error")
            ],
        )
    if not isinstance(data, dict) or "queries" not in data:
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="queries.json must be an object with a 'queries' list.",
                    code="missing_queries",
                )
            ],
        )
    queries = data.get("queries")
    if not isinstance(queries, list):
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="'queries' must be a list.", code="queries_not_list"
                )
            ],
        )
    messages: list[MdlValidationMessage] = []
    for index, entry in enumerate(queries):
        if not isinstance(entry, dict):
            messages.append(
                MdlValidationMessage(
                    message=f"Golden query #{index + 1} must be an object.",
                    code="invalid_golden_query",
                )
            )
            continue
        for required in ("name", "question", "semantic_sql"):
            if not entry.get(required):
                label = entry.get("name") or f"#{index + 1}"
                messages.append(
                    MdlValidationMessage(
                        message=f"Golden query {label} is missing '{required}'.",
                        code=f"missing_{required}",
                    )
                )
    return MdlValidationResult(
        valid=not any(m.severity == "error" for m in messages), messages=messages
    )


def golden_query_refs(
    semantic_sql: str,
    *,
    model_table_index: dict[str, tuple[str | None, str]],
    dialect: str | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve a golden query's referenced physical tables from the manifest (F2).

    The query references **model** names; ``model_table_index`` maps each model name
    (lowercased) to its physical ``(schema, table)`` from the manifest's
    ``tableReference``. Returns ``(referenced_tables, referenced_schemas)`` in the
    same lowercased ``"schema.table"`` shape the runtime memory uses, so the access
    filter (F2) treats golden and learned pairs identically. A model that does not
    resolve (e.g. a SQL-backed model) contributes nothing — the entry is then only
    surfaced when its resolvable tables are all reachable.
    """

    from superset_ai_agent.semantic_layer.engine.base import extract_qualified_tables
    from superset_ai_agent.semantic_layer.memory_store import qualify_table_refs

    extracted = extract_qualified_tables(semantic_sql, dialect=dialect)
    names = {name for _, name in extracted}
    resolved: list[tuple[str | None, str]] = []
    for name in names:
        mapped = model_table_index.get(name.lower())
        if mapped is not None:
            resolved.append(mapped)
    return qualify_table_refs(resolved)


def build_model_table_index(
    manifest: dict[str, Any],
) -> dict[str, tuple[str | None, str]]:
    """Map each manifest model name (lowercased) -> physical ``(schema, table)``."""

    index: dict[str, tuple[str | None, str]] = {}
    for model in manifest.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        reference = model.get("tableReference")
        if not name or not isinstance(reference, dict):
            continue
        table = reference.get("table")
        if not isinstance(table, str) or not table:
            continue
        schema = reference.get("schema")
        index[name.lower()] = (
            schema.lower() if isinstance(schema, str) and schema else None,
            table.lower(),
        )
    return index


def _normalized_question(question: str) -> str:
    return " ".join((question or "").lower().split())


def recall_golden_queries(
    *,
    mdl_file_store: Any,
    project_id: str | None,
    owner_id: str,
    question: str,
    k: int,
    embedder: Any = None,
    access: Any = None,
) -> list[Any]:
    """Recall project golden queries as ``NlSqlPair`` few-shot examples (F3/2C).

    Loads the project's active ``queries.json``, derives each entry's physical
    references from the active model files' ``tableReference`` (so the F2 access
    filter treats golden and learned pairs identically — Stage A drops a golden
    query whose tables the user cannot reach), ranks by similarity, and returns up
    to ``k`` pairs. Golden pairs carry ``result_meta.golden`` so the UI can badge a
    "verified" answer. Returns ``[]`` when there is no project or no golden file —
    so the runtime memory recall stands alone. ``mdl_file_store`` is duck-typed to
    avoid an import cycle with ``mdl_files`` (which imports this module).
    """

    if mdl_file_store is None or not project_id:
        return []

    from superset_ai_agent.semantic_layer.engine.base import extract_qualified_tables
    from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest
    from superset_ai_agent.semantic_layer.memory_store import (
        _pair_is_accessible,
        _recall_rank,
        NlSqlPair,
    )

    try:
        files = mdl_file_store.list(project_id, owner_id=owner_id)
    except Exception:  # pylint: disable=broad-except - recall is best-effort
        return []
    active = [
        f for f in files if getattr(f, "status", None) == "active"
    ]
    queries_file = next(
        (f for f in active if is_golden_queries_path(getattr(f, "path", None))), None
    )
    if queries_file is None:
        return []
    try:
        parsed = parse_golden_queries(queries_file.content)
    except (ValueError, TypeError):
        return []
    if not parsed.queries:
        return []

    model_files = [
        f for f in active if not is_golden_queries_path(getattr(f, "path", None))
    ]
    manifest = compile_manifest(model_files)
    index = build_model_table_index({"models": manifest.models})

    _ = extract_qualified_tables  # referenced by golden_query_refs
    pairs: list[Any] = []
    for entry in parsed.queries:
        tables, schemas = golden_query_refs(
            entry.semantic_sql, model_table_index=index
        )
        pair = NlSqlPair(
            question=entry.question,
            semantic_sql=entry.semantic_sql,
            native_sql=entry.semantic_sql,
            referenced_tables=tables,
            referenced_schemas=schemas,
            result_meta={
                "golden": True,
                "name": entry.name,
                "verified": entry.verified_at is not None,
            },
        )
        # Stage A: a golden query referencing an unreachable table is dropped.
        if access is not None and not _pair_is_accessible(pair, access):
            continue
        pairs.append(pair)
    if not pairs:
        return []
    return _recall_rank(question, pairs, k, embedder)


def merge_recalled_examples(golden: list[Any], memory: list[Any], k: int) -> list[Any]:
    """Merge golden (priority) + runtime memory into the few-shot set (F3/2C).

    Golden queries lead; runtime pairs fill the remaining slots. Dedup is by
    normalized question — a golden query **supersedes** its runtime twin **in the
    prompt only** (the memory row is never deleted; see the copy-not-move invariant).
    """

    merged = list(golden)
    seen = {_normalized_question(getattr(p, "question", "")) for p in merged}
    for pair in memory:
        if len(merged) >= k:
            break
        key = _normalized_question(getattr(pair, "question", ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(pair)
    return merged[:k]
