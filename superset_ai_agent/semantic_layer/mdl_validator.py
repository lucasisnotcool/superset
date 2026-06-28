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

"""Schema-aware MDL validation.

This module provides structural validation of MDL JSON (grammar, required
fields, relationship resolution) and optional *physical* validation against a
:class:`SchemaIndex` built from permission-filtered Superset metadata. Physical
validation is what code-enforces "never invent columns/tables" (risk R3): an MDL
model that references a table or column absent from the real schema fails
validation and therefore cannot be activated (risk R1).
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from dataclasses import dataclass, field
from typing import Any

from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.semantic_layer.mdl_schema import (
    JOIN_TYPES,
    MODEL_CONTAINER_KEYS,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlValidationMessage,
    MdlValidationResult,
)


@dataclass
class SchemaIndex:
    """Permission-filtered physical schema used for MDL validation.

    ``tables`` carries column **names**; ``column_types`` (table → {column: type})
    carries catalog **types** when they are available (C3). Types come only from the
    *live* ``from_agent_context`` path — the persisted ``from_snapshot`` is names-only,
    so type grounding/checking degrades closed to the names-only behavior on a
    Superset outage (snapshot uniformity is intentionally preserved).
    """

    tables: dict[str, set[str]] = field(default_factory=dict)
    column_types: dict[str, dict[str, str]] = field(default_factory=dict)
    #: schema (lowercased) → table (lowercased) → column names. Populated from the
    #: live ``from_agent_context`` path only; empty on the names-only snapshot path,
    #: so schema-aware checks degrade closed (to the bare-table behaviour) there.
    tables_by_schema: dict[str, dict[str, set[str]]] = field(default_factory=dict)

    @classmethod
    def from_agent_context(cls, context: AgentContext) -> "SchemaIndex":
        tables: dict[str, set[str]] = {}
        column_types: dict[str, dict[str, str]] = {}
        tables_by_schema: dict[str, dict[str, set[str]]] = {}
        for dataset in context.datasets:
            if not dataset.table_name:
                continue
            table = dataset.table_name.lower()
            columns = {column.name.lower() for column in dataset.columns if column.name}
            tables[table] = columns
            types = {
                column.name.lower(): column.type
                for column in dataset.columns
                if column.name and column.type
            }
            if types:
                column_types[table] = types
            schema = (dataset.schema_name or "").lower()
            if schema:
                tables_by_schema.setdefault(schema, {})[table] = columns
        return cls(
            tables=tables,
            column_types=column_types,
            tables_by_schema=tables_by_schema,
        )

    @classmethod
    def from_snapshot(
        cls,
        tables: dict[str, list[str]],
        types: dict[str, dict[str, str]] | None = None,
    ) -> "SchemaIndex":
        index = cls(
            tables={
                str(table).lower(): {str(column).lower() for column in columns}
                for table, columns in tables.items()
            }
        )
        if types:
            index.column_types = {
                str(table).lower(): {
                    str(column).lower(): str(type_) for column, type_ in cols.items()
                }
                for table, cols in types.items()
            }
        return index

    def to_tables(self) -> dict[str, list[str]]:
        """Serialize for persistence (sets become sorted lists)."""

        return {table: sorted(columns) for table, columns in self.tables.items()}

    def typed_tables(self) -> dict[str, dict[str, str]]:
        """Table → {column: type} for prompt grounding; empty when no types known."""

        return {table: dict(cols) for table, cols in self.column_types.items() if cols}

    def has_types(self) -> bool:
        return any(self.column_types.values())

    @property
    def schemas(self) -> set[str]:
        """Physical schemas this index knows about (empty on the snapshot path)."""

        return set(self.tables_by_schema)

    def has_schema(self, schema: str) -> bool:
        return schema.lower() in self.tables_by_schema

    def has_table(self, table: str, schema: str | None = None) -> bool:
        if schema and self.tables_by_schema:
            return table.lower() in self.tables_by_schema.get(schema.lower(), {})
        return table.lower() in self.tables

    def has_column(self, table: str, column: str, schema: str | None = None) -> bool:
        if schema and self.tables_by_schema:
            scoped = self.tables_by_schema.get(schema.lower(), {})
            return column.lower() in scoped.get(table.lower(), set())
        return column.lower() in self.tables.get(table.lower(), set())

    def column_type(self, table: str, column: str) -> str | None:
        return self.column_types.get(table.lower(), {}).get(column.lower())

    def columns_for(self, table: str, schema: str | None = None) -> list[str]:
        """Sorted column names for a (schema, table); empty when unknown."""

        if schema and self.tables_by_schema:
            scoped = self.tables_by_schema.get(schema.lower(), {})
            return sorted(scoped.get(table.lower(), set()))
        return sorted(self.tables.get(table.lower(), set()))

    def search(
        self, query: str, *, schema: str | None = None, limit: int = 10
    ) -> list[tuple[str | None, str, float]]:
        """Rank physical tables by keyword overlap with ``query`` (best first).

        Returns ``(schema, table, score)`` triples — the physical ``schema`` is
        carried when known (live/multi-schema index) else ``None`` (names-only
        snapshot). Keyword scoring only (no embeddings), so it degrades
        gracefully everywhere and **never surfaces a table outside the
        permission-filtered index**. An empty result is the honest "no table in
        this database matches" signal the agent should report rather than invent.
        """

        terms = _tokenize(query)
        if not terms:
            return []
        candidates: list[tuple[str | None, str, set[str]]] = []
        if schema and self.tables_by_schema:
            scoped = self.tables_by_schema.get(schema.lower(), {})
            candidates = [
                (schema.lower(), table, cols) for table, cols in scoped.items()
            ]
        elif self.tables_by_schema:
            for schema_name, tables in self.tables_by_schema.items():
                for table, cols in tables.items():
                    candidates.append((schema_name, table, cols))
        else:
            # Names-only snapshot (or single-schema live index without schema map).
            candidates = [(None, table, cols) for table, cols in self.tables.items()]
        scored: list[tuple[float, str, str | None]] = []
        for schema_name, table, columns in candidates:
            score = _table_match_score(terms, table, columns)
            if score > 0:
                scored.append((score, table, schema_name))
        scored.sort(key=lambda row: (-row[0], row[1]))
        capped = scored[: max(1, limit)]
        return [(schema_name, table, score) for score, table, schema_name in capped]


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens (``order_items`` → ``order``, ``items``)."""

    tokens: list[str] = []
    current: list[str] = []
    for char in text.lower():
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _table_match_score(terms: list[str], table: str, columns: set[str]) -> float:
    """Keyword-overlap score of a query against one table's name + columns.

    A table-name hit weighs most, then a name substring (handles ``orders`` ↔
    ``order``), then a column hit. Returns ``0.0`` when nothing matches so the
    caller can drop the table from the candidate set.
    """

    name_tokens = set(_tokenize(table))
    column_tokens: set[str] = set()
    for column in columns:
        column_tokens.update(_tokenize(column))
    score = 0.0
    for term in terms:
        if term in name_tokens:
            score += 3.0
        elif any(term in token or token in term for token in name_tokens):
            score += 2.0
        elif term in column_tokens:
            score += 1.0
        elif any(term in token or token in term for token in column_tokens):
            score += 0.5
    return score


#: Coarse type families for the C3 cross-family mismatch check. Substring match on
#: the base type (params stripped), so VARCHAR(255)→string, BIGINT→numeric. Anything
#: unrecognized maps to ``None`` and is never flagged — the check fires only on an
#: unambiguous family mismatch (e.g. a numeric MDL type on a VARCHAR column), keeping
#: false positives near zero given catalog vs. MDL type-vocabulary differences.
_TYPE_FAMILIES: dict[str, tuple[str, ...]] = {
    "temporal": ("DATE", "TIME", "TIMESTAMP"),
    "boolean": ("BOOL",),
    "string": ("CHAR", "TEXT", "STRING", "CLOB", "UUID"),
    # "BIT" is intentionally excluded — it is boolean in some dialects, numeric in
    # others, so flagging it cross-family would be a false positive.
    "numeric": (
        "INT",
        "DEC",
        "NUMERIC",
        "NUMBER",
        "FLOAT",
        "DOUBLE",
        "REAL",
        "SERIAL",
        "MONEY",
    ),
}


def _type_family(type_str: str | None) -> str | None:
    """Map a SQL/MDL type to a coarse family, or ``None`` when unrecognized.

    ``temporal``/``boolean`` are checked before ``numeric`` so ``TIMESTAMP`` (which
    contains ``TIME``) and ``BOOLEAN`` are not misread; ``string`` before ``numeric``
    is irrelevant but kept ordered for clarity.
    """

    if not type_str:
        return None
    base = type_str.upper().split("(", 1)[0].strip()
    if not base:
        return None
    for family, keywords in _TYPE_FAMILIES.items():
        if any(keyword in base for keyword in keywords):
            return family
    return None


def validate_mdl(
    content: str,
    *,
    schema_index: SchemaIndex | None = None,
    strict_relationships: bool = False,
    strict_models: bool = False,
) -> MdlValidationResult:
    """Validate one MDL JSON document (wren-core native shape).

    ``schema_index`` enables physical validation (R3). ``strict_relationships``
    turns unresolved relationship endpoints into errors instead of warnings;
    use it for merged project manifests where every model must be present.
    ``strict_models`` turns a model with neither a physical mapping nor columns
    (almost always a relationship emitted as a model) into an error instead of
    two warnings; the Copilot proposal path sets it so the correction loop catches
    the mistake before activation, while the default stays lenient for drafts.
    """

    parsed, parse_message = _parse_json(content)
    if parse_message is not None:
        return MdlValidationResult(valid=False, messages=[parse_message])

    models = _extract_models(parsed)
    relationships = _extract_list(parsed, "relationships")
    views = _extract_list(parsed, "views")
    metrics = _extract_list(parsed, "metrics")
    cubes = _extract_list(parsed, "cubes")
    if not models and not views and not metrics and not cubes:
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="MDL must contain at least one model, view, metric, "
                    "or cube.",
                    code="empty_root",
                )
            ],
        )

    messages: list[MdlValidationMessage] = []
    model_names = _validate_models(
        models, schema_index, messages, strict_models=strict_models
    )
    _validate_views(views, messages)
    _validate_relationships(
        relationships,
        model_names,
        strict_relationships=strict_relationships,
        messages=messages,
    )
    # Metrics/cubes resolve their base object against models, views, and cubes.
    base_object_names = model_names | _names(views) | _names(cubes)
    _validate_metrics(
        metrics,
        base_object_names,
        strict=strict_relationships,
        messages=messages,
    )
    _validate_cubes(
        cubes,
        base_object_names,
        strict=strict_relationships,
        messages=messages,
    )

    valid = not any(message.severity == "error" for message in messages)
    return MdlValidationResult(valid=valid, messages=messages)


def validate_project_manifest(
    contents: list[str],
    *,
    schema_index: SchemaIndex | None = None,
    deep_validate: bool = False,
    dedup_models: bool = False,
    strict_models: bool = False,
) -> MdlValidationResult:
    """Validate a merged set of MDL files as one project manifest.

    Relationship resolution is strict here because every referenced model should
    be present once all project files are combined. ``deep_validate`` additionally
    runs the optional wren-core engine and merges its findings.

    ``dedup_models`` collapses models re-declared across files to their **last**
    occurrence (the W4 safety net): an enrichment that re-emits an existing model
    supersedes the older copy instead of failing as a ``duplicate_model``. Each
    collapse is surfaced as an informational message, never silently.
    """

    merged_models: list[Any] = []
    merged_relationships: list[Any] = []
    merged_views: list[Any] = []
    merged_metrics: list[Any] = []
    merged_cubes: list[Any] = []
    for content in contents:
        parsed, parse_message = _parse_json(content)
        if parse_message is not None:
            return MdlValidationResult(valid=False, messages=[parse_message])
        merged_models.extend(_extract_models(parsed))
        merged_relationships.extend(_extract_list(parsed, "relationships"))
        merged_views.extend(_extract_list(parsed, "views"))
        merged_metrics.extend(_extract_list(parsed, "metrics"))
        merged_cubes.extend(_extract_list(parsed, "cubes"))

    dedup_messages: list[MdlValidationMessage] = []
    if dedup_models:
        merged_models, dedup_messages = _dedup_models_keep_last(merged_models)

    merged_json = json.dumps(
        {
            "models": merged_models,
            "relationships": merged_relationships,
            "views": merged_views,
            "metrics": merged_metrics,
            "cubes": merged_cubes,
        }
    )
    result = validate_mdl(
        merged_json,
        schema_index=schema_index,
        strict_relationships=True,
        strict_models=strict_models,
    )
    result = MdlValidationResult(
        valid=result.valid,
        messages=[*dedup_messages, *result.messages],
    )
    if not deep_validate:
        return result
    # Imported lazily so the optional wren-core import guard only runs on demand.
    from superset_ai_agent.semantic_layer.wren_core_validator import (
        validate_with_wren_core,
    )

    deep = validate_with_wren_core(
        [model for model in merged_models if isinstance(model, dict)],
        [rel for rel in merged_relationships if isinstance(rel, dict)],
    )
    merged_messages = [*result.messages, *deep.messages]
    return MdlValidationResult(
        valid=result.valid and deep.valid,
        messages=merged_messages,
    )


def _validate_models(
    models: list[Any],
    schema_index: SchemaIndex | None,
    messages: list[MdlValidationMessage],
    *,
    strict_models: bool = False,
) -> set[str]:
    seen_names: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            messages.append(
                MdlValidationMessage(
                    message=f"Model #{index + 1} must be a mapping.",
                    code="invalid_model",
                )
            )
            continue
        name = model.get("name")
        if not name or not isinstance(name, str):
            messages.append(
                MdlValidationMessage(
                    message=f"Model #{index + 1} is missing a name.",
                    code="missing_model_name",
                )
            )
            continue
        if name in seen_names:
            messages.append(
                MdlValidationMessage(
                    message=f"Duplicate model name: {name}.",
                    code="duplicate_model",
                )
            )
        seen_names.add(name)

        table = _table_name(model)
        schema = _table_schema(model)
        model_columns = model.get("columns")
        has_columns = isinstance(model_columns, list) and bool(model_columns)
        if strict_models and table is None and not has_columns:
            # A model with neither a physical mapping nor columns is invalid (and
            # wren-core rejects it for the missing 'columns' field). The common
            # cause is a join/relationship emitted as a model — point there. Only
            # an ERROR under ``strict_models`` (the Copilot proposal path) so the
            # correction loop catches it before the user accepts/activates; the
            # default stays lenient (warnings) for incomplete drafts.
            messages.append(
                MdlValidationMessage(
                    severity="error",
                    message=(
                        f"Model {name} has neither a physical mapping "
                        "(tableReference/refSql) nor columns. If it represents a "
                        "join, define it under relationships[] instead of models[]."
                    ),
                    code="model_missing_mapping_and_columns",
                )
            )
            continue
        if table is None:
            messages.append(
                MdlValidationMessage(
                    severity="warning",
                    message=(
                        f"Model {name} has no tableReference or refSql; it "
                        "cannot be mapped to a physical table."
                    ),
                    code="model_without_mapping",
                )
            )
        elif (
            schema is not None
            and schema_index is not None
            and schema_index.schemas
            and not schema_index.has_schema(schema)
        ):
            # R1: a model may only physically reference a schema in the project's
            # proven set. ``schema_index.schemas`` is non-empty only on the live
            # path, so this degrades closed on the names-only snapshot.
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Model {name} references schema '{schema}' that is not "
                        "part of the project's schema set."
                    ),
                    code="schema_not_in_project",
                )
            )
        elif schema_index is not None and not schema_index.has_table(table, schema):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Model {name} references table "
                        f"'{_qualified_table(schema, table)}' that does not "
                        "exist in the schema."
                    ),
                    code="unknown_table",
                )
            )

        _validate_columns(name, model, table, schema, schema_index, messages)
    return seen_names


def _validate_columns(
    model_name: str,
    model: dict[str, Any],
    table: str | None,
    schema: str | None,
    schema_index: SchemaIndex | None,
    messages: list[MdlValidationMessage],
) -> None:
    columns = model.get("columns")
    if not isinstance(columns, list) or not columns:
        messages.append(
            MdlValidationMessage(
                severity="warning",
                message=f"Model {model_name} has no columns.",
                code="model_without_columns",
            )
        )
        return
    seen_columns: set[str] = set()
    table_known = (
        schema_index is not None
        and table is not None
        and schema_index.has_table(table, schema)
    )
    for column in columns:
        if not isinstance(column, dict):
            continue
        column_name = column.get("name")
        if not column_name or not isinstance(column_name, str):
            messages.append(
                MdlValidationMessage(
                    message=f"Model {model_name} has a column without a name.",
                    code="missing_column_name",
                )
            )
            continue
        if column_name in seen_columns:
            messages.append(
                MdlValidationMessage(
                    message=(f"Duplicate column {model_name}.{column_name}."),
                    code="duplicate_column",
                )
            )
        seen_columns.add(column_name)
        _validate_column_semantics(
            model_name=model_name,
            column=column,
            column_name=column_name,
            table=table,
            schema=schema,
            table_known=table_known,
            schema_index=schema_index,
            messages=messages,
        )


def _validate_column_semantics(
    *,
    model_name: str,
    column: dict[str, Any],
    column_name: str,
    table: str | None,
    schema: str | None,
    table_known: bool,
    schema_index: SchemaIndex | None,
    messages: list[MdlValidationMessage],
) -> None:
    """Per-column semantic checks: calculated, type presence, physical mapping (C3)."""

    is_calculated = bool(column.get("isCalculated"))
    is_relationship = bool(column.get("relationship"))
    # Match physical existence/type on the column's PHYSICAL name, not its logical
    # handle: the seed may sanitize a non-identifier name (``2003`` → ``_2003``)
    # and map it back via ``expression`` + ``properties.superset_column_name``. The
    # SchemaIndex is keyed by raw physical names, so resolve to that here (D-A).
    properties = column.get("properties")
    physical_name = column_name
    if isinstance(properties, dict):
        mapped = properties.get("superset_column_name")
        if isinstance(mapped, str) and mapped:
            physical_name = mapped
    if is_calculated and not column.get("expression"):
        messages.append(
            MdlValidationMessage(
                message=(
                    f"Calculated column {model_name}.{column_name} requires "
                    "an expression."
                ),
                code="calculated_requires_expression",
            )
        )
    # W5: wren-core requires every non-relationship column to carry a `type`.
    # Catch it here with a readable, field-anchored message instead of letting
    # it surface as the engine's opaque "missing field `type`" serde offset.
    if not is_relationship and not column.get("type"):
        messages.append(
            MdlValidationMessage(
                message=(
                    f"Column {model_name}.{column_name} is missing a type; "
                    "wren-core requires a type on every column."
                ),
                code="column_without_type",
            )
        )
    if not (table_known and table is not None and not is_relationship):
        return
    if (
        not is_calculated and not schema_index.has_column(table, physical_name, schema)  # type: ignore[union-attr]
    ):
        messages.append(
            MdlValidationMessage(
                message=(
                    f"Column {model_name}.{column_name} does not exist in "
                    f"table '{table}'."
                ),
                code="unknown_column",
            )
        )
    # C3: reject an unambiguous cross-family type mismatch on a physical column.
    mismatch = _type_mismatch_message(
        schema_index,
        table,
        model_name,
        column_name,
        column,
        is_calculated,
        physical_name=physical_name,
    )
    if mismatch is not None:
        messages.append(mismatch)


def _type_mismatch_message(
    schema_index: SchemaIndex | None,
    table: str,
    model_name: str,
    column_name: str,
    column: dict[str, Any],
    is_calculated: bool,
    *,
    physical_name: str | None = None,
) -> MdlValidationMessage | None:
    """Cross-family type-mismatch error for a physical-mapped column (C3), or None.

    Conservative: fires only when the catalog type and the proposed type both resolve
    to a known, *different* family. Calculated columns are derived (a CAST may change
    family legitimately), so they are skipped. Degrades to ``None`` for unknown types
    or the names-only snapshot path (no catalog type).
    """

    if schema_index is None or is_calculated:
        return None
    lookup = physical_name or column_name
    catalog_family = _type_family(schema_index.column_type(table, lookup))
    proposed_type = column.get("type")
    proposed_family = (
        _type_family(proposed_type) if isinstance(proposed_type, str) else None
    )
    if (
        catalog_family is None
        or proposed_family is None
        or catalog_family == proposed_family
    ):
        return None
    catalog_type = schema_index.column_type(table, lookup)
    return MdlValidationMessage(
        message=(
            f"Column {model_name}.{column_name} is typed '{proposed_type}' "
            f"({proposed_family}) but the physical column is '{catalog_type}' "
            f"({catalog_family}). Use a type matching the physical column."
        ),
        code="column_type_mismatch",
    )


def _validate_views(
    views: list[Any],
    messages: list[MdlValidationMessage],
) -> None:
    for index, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        if not view.get("name"):
            messages.append(
                MdlValidationMessage(
                    message=f"View #{index + 1} is missing a name.",
                    code="missing_view_name",
                )
            )
        if not view.get("statement"):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"View {view.get('name') or index + 1} is missing a statement."
                    ),
                    code="missing_view_statement",
                )
            )


def _validate_relationships(
    relationships: list[Any],
    model_names: set[str],
    *,
    strict_relationships: bool,
    messages: list[MdlValidationMessage],
) -> None:
    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            continue
        label = relationship.get("name") or f"#{index + 1}"
        join_type = relationship.get("joinType")
        if join_type is None or str(join_type).upper() not in JOIN_TYPES:
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Relationship {label} has an invalid join_type: "
                        f"{join_type!r}. Expected one of {sorted(JOIN_TYPES)}."
                    ),
                    code="invalid_join_type",
                )
            )
        endpoints = relationship.get("models")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Relationship {label} must reference exactly two models."
                    ),
                    code="relationship_arity",
                )
            )
            continue
        for endpoint in endpoints:
            if endpoint not in model_names:
                messages.append(
                    MdlValidationMessage(
                        severity="error" if strict_relationships else "warning",
                        message=(
                            f"Relationship {label} references model "
                            f"'{endpoint}' that is not defined"
                            + ("." if strict_relationships else " in this file.")
                        ),
                        code="unresolved_relationship",
                    )
                )


def _validate_metrics(
    metrics: list[Any],
    base_object_names: set[str],
    *,
    strict: bool,
    messages: list[MdlValidationMessage],
) -> None:
    seen_names: set[str] = set()
    for index, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            messages.append(
                MdlValidationMessage(
                    message=f"Metric #{index + 1} must be a mapping.",
                    code="invalid_metric",
                )
            )
            continue
        name = metric.get("name")
        if not name or not isinstance(name, str):
            messages.append(
                MdlValidationMessage(
                    message=f"Metric #{index + 1} is missing a name.",
                    code="missing_metric_name",
                )
            )
            continue
        if name in seen_names:
            messages.append(
                MdlValidationMessage(
                    message=f"Duplicate metric name: {name}.",
                    code="duplicate_metric",
                )
            )
        seen_names.add(name)

        base = metric.get("baseObject")
        if base and base not in base_object_names:
            messages.append(
                MdlValidationMessage(
                    severity="error" if strict else "warning",
                    message=(
                        f"Metric {name} references base object '{base}' that is "
                        "not defined" + ("." if strict else " in this file.")
                    ),
                    code="unresolved_metric_base",
                )
            )
        # Wren-native metrics carry a singular ``measure`` array (wren-core shape);
        # tolerate a legacy ``measures`` alias. Checking only the plural form would
        # false-warn on a correctly-formed metric.
        measures = metric.get("measure")
        if measures is None:
            measures = metric.get("measures")
        has_measures = isinstance(measures, list) and bool(measures)
        if not metric.get("expression") and not has_measures:
            messages.append(
                MdlValidationMessage(
                    severity="warning",
                    message=(
                        f"Metric {name} has no expression or measures; it "
                        "computes nothing."
                    ),
                    code="metric_without_measure",
                )
            )


def _validate_cubes(
    cubes: list[Any],
    base_object_names: set[str],
    *,
    strict: bool,
    messages: list[MdlValidationMessage],
) -> None:
    seen_names: set[str] = set()
    for index, cube in enumerate(cubes):
        if not isinstance(cube, dict):
            messages.append(
                MdlValidationMessage(
                    message=f"Cube #{index + 1} must be a mapping.",
                    code="invalid_cube",
                )
            )
            continue
        name = cube.get("name")
        if not name or not isinstance(name, str):
            messages.append(
                MdlValidationMessage(
                    message=f"Cube #{index + 1} is missing a name.",
                    code="missing_cube_name",
                )
            )
            continue
        if name in seen_names:
            messages.append(
                MdlValidationMessage(
                    message=f"Duplicate cube name: {name}.",
                    code="duplicate_cube",
                )
            )
        seen_names.add(name)

        base = cube.get("baseObject")
        if not base:
            # wren-core requires every cube to declare its baseObject.
            messages.append(
                MdlValidationMessage(
                    message=f"Cube {name} is missing a baseObject.",
                    code="cube_without_base",
                )
            )
        elif base not in base_object_names:
            messages.append(
                MdlValidationMessage(
                    severity="error" if strict else "warning",
                    message=(
                        f"Cube {name} references base object '{base}' that is "
                        "not defined" + ("." if strict else " in this file.")
                    ),
                    code="unresolved_cube_base",
                )
            )
        _validate_cube_measures(name, cube.get("measures"), messages)
        # wren-core requires each dimension / time dimension to carry
        # {name, type, expression}. Enforce that structurally so a malformed
        # entry fails with a readable message rather than the engine's opaque
        # serde byte-offset at activation. (hierarchies is an engine map, not a
        # list, and the agent does not author cubes — left to deep validation.)
        _validate_cube_field_entries(
            name, "dimension", cube.get("dimensions"), messages
        )
        _validate_cube_field_entries(
            name, "time dimension", cube.get("timeDimensions"), messages
        )


def _validate_cube_field_entries(
    cube_name: str,
    kind: str,
    entries: Any,
    messages: list[MdlValidationMessage],
) -> None:
    """Validate a cube's dimensions / time dimensions against wren-core's shape.

    wren-core requires each entry to be a mapping carrying ``{name, type,
    expression}``. A missing field is an error (it makes the manifest unloadable),
    surfaced with a readable message instead of the engine's serde byte offset.
    """

    if entries is None:
        return
    if not isinstance(entries, list):
        messages.append(
            MdlValidationMessage(
                message=f"Cube {cube_name} {kind}s must be a list.",
                code="cube_invalid_entries",
            )
        )
        return
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            messages.append(
                MdlValidationMessage(
                    message=f"Cube {cube_name} has a {kind} without a name.",
                    code="cube_entry_without_name",
                )
            )
            continue
        entry_name = entry["name"]
        if not entry.get("type"):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Cube {cube_name} {kind} {entry_name} is missing a type."
                    ),
                    code="cube_entry_without_type",
                )
            )
        if not entry.get("expression"):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Cube {cube_name} {kind} {entry_name} is missing an "
                        "expression."
                    ),
                    code="cube_entry_without_expression",
                )
            )


def _validate_cube_measures(
    cube_name: str,
    measures: Any,
    messages: list[MdlValidationMessage],
) -> None:
    """Validate a cube's measures against wren-core's shape.

    An empty measure list is accepted by the engine (the cube computes nothing) so
    it is a warning; a present measure must carry ``{name, type, expression}`` —
    each missing field is an error, matching wren-core's hard requirement.
    """

    if not isinstance(measures, list) or not measures:
        messages.append(
            MdlValidationMessage(
                severity="warning",
                message=f"Cube {cube_name} has no measures.",
                code="cube_without_measures",
            )
        )
        return
    for measure in measures:
        if not isinstance(measure, dict):
            continue
        measure_name = measure.get("name")
        if not measure_name or not isinstance(measure_name, str):
            messages.append(
                MdlValidationMessage(
                    message=f"Cube {cube_name} has a measure without a name.",
                    code="cube_measure_without_name",
                )
            )
            continue
        if not measure.get("type"):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Cube measure {cube_name}.{measure_name} is missing a type."
                    ),
                    code="cube_measure_without_type",
                )
            )
        if not measure.get("expression"):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Cube measure {cube_name}.{measure_name} is missing an "
                        "expression."
                    ),
                    code="cube_measure_without_expression",
                )
            )


def _names(items: list[Any]) -> set[str]:
    """Collect the ``name`` field of each mapping in a list."""

    return {
        item["name"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
    }


def _table_name(model: dict[str, Any]) -> str | None:
    reference = model.get("tableReference")
    if isinstance(reference, dict):
        table = reference.get("table")
        if isinstance(table, str) and table:
            return table
    if model.get("refSql"):
        # SQL-backed models are not mapped to a single physical table.
        return None
    return None


def _table_schema(model: dict[str, Any]) -> str | None:
    """Physical schema a model's ``tableReference`` points at, if declared."""

    reference = model.get("tableReference")
    if isinstance(reference, dict):
        schema = reference.get("schema")
        if isinstance(schema, str) and schema:
            return schema
    return None


def _qualified_table(schema: str | None, table: str) -> str:
    return f"{schema}.{table}" if schema else table


def _dedup_models_keep_last(
    models: list[Any],
) -> tuple[list[Any], list[MdlValidationMessage]]:
    """Collapse models re-declared by name to their last occurrence.

    Returns the deduplicated list (in last-occurrence order) plus one info message
    per collapsed name so the supersede is visible to the reviewer.
    """

    by_name: dict[str, Any] = {}
    passthrough: list[Any] = []
    duplicated: set[str] = set()
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            passthrough.append(model)
            continue
        name = model["name"]
        if name in by_name:
            duplicated.add(name)
        by_name[name] = model
    messages = [
        MdlValidationMessage(
            severity="info",
            message=(
                f"Model {name} is defined more than once; using the newest "
                "definition and dropping the older copy."
            ),
            code="model_superseded",
        )
        for name in duplicated
    ]
    return [*passthrough, *by_name.values()], messages


def _extract_models(parsed: Any) -> list[Any]:
    if isinstance(parsed, list):
        return list(parsed)
    if isinstance(parsed, dict):
        for key in MODEL_CONTAINER_KEYS:
            value = parsed.get(key)
            if isinstance(value, list):
                return list(value)
    return []


def _extract_list(parsed: Any, key: str) -> list[Any]:
    if isinstance(parsed, dict):
        value = parsed.get(key)
        if isinstance(value, list):
            return list(value)
    return []


def _parse_json(content: str) -> tuple[Any, MdlValidationMessage | None]:
    if not content.strip():
        return None, MdlValidationMessage(
            message="MDL JSON is empty.",
            code="empty_json",
        )
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError) as ex:
        return None, _json_error_message(ex)
    if not isinstance(parsed, dict | list):
        return None, MdlValidationMessage(
            message="MDL JSON must parse to an object or list.",
            code="invalid_root",
        )
    if (isinstance(parsed, dict | list) and len(parsed) == 0) or parsed is None:
        return None, MdlValidationMessage(
            message="MDL JSON must contain at least one entry.",
            code="empty_root",
        )
    return parsed, None


def _json_error_message(ex: Exception) -> MdlValidationMessage:
    line = getattr(ex, "lineno", None)
    column = getattr(ex, "colno", None)
    return MdlValidationMessage(
        line=line,
        column=column,
        message=str(ex),
        code="json_parse_error",
    )
