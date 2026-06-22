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

This module provides structural validation of MDL YAML (grammar, required
fields, relationship resolution) and optional *physical* validation against a
:class:`SchemaIndex` built from permission-filtered Superset metadata. Physical
validation is what code-enforces "never invent columns/tables" (risk R3): an MDL
model that references a table or column absent from the real schema fails
validation and therefore cannot be activated (risk R1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

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
    """Permission-filtered physical schema used for MDL validation."""

    tables: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_agent_context(cls, context: AgentContext) -> "SchemaIndex":
        tables: dict[str, set[str]] = {}
        for dataset in context.datasets:
            if not dataset.table_name:
                continue
            tables[dataset.table_name.lower()] = {
                column.name.lower() for column in dataset.columns if column.name
            }
        return cls(tables=tables)

    def has_table(self, table: str) -> bool:
        return table.lower() in self.tables

    def has_column(self, table: str, column: str) -> bool:
        return column.lower() in self.tables.get(table.lower(), set())


def validate_mdl_yaml(content: str) -> MdlValidationResult:
    """Structural-only MDL validation (no physical schema check).

    Kept as the drop-in replacement for the original shallow validator so all
    existing call sites gain real structural validation.
    """

    return validate_mdl(content)


def validate_mdl(
    content: str,
    *,
    schema_index: SchemaIndex | None = None,
    strict_relationships: bool = False,
) -> MdlValidationResult:
    """Validate one MDL YAML document.

    ``schema_index`` enables physical validation (R3). ``strict_relationships``
    turns unresolved relationship endpoints into errors instead of warnings;
    use it for merged project manifests where every model must be present.
    """

    parsed, parse_message = _parse_yaml(content)
    if parse_message is not None:
        return MdlValidationResult(valid=False, messages=[parse_message])

    models = _extract_models(parsed)
    relationships = _extract_list(parsed, "relationships")
    views = _extract_list(parsed, "views")
    if not models and not views:
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="MDL must contain at least one model or view.",
                    code="empty_root",
                )
            ],
        )

    messages: list[MdlValidationMessage] = []
    model_names = _validate_models(models, schema_index, messages)
    _validate_views(views, messages)
    _validate_relationships(
        relationships,
        model_names,
        strict_relationships=strict_relationships,
        messages=messages,
    )

    valid = not any(message.severity == "error" for message in messages)
    return MdlValidationResult(valid=valid, messages=messages)


def validate_project_manifest(
    contents: list[str],
    *,
    schema_index: SchemaIndex | None = None,
) -> MdlValidationResult:
    """Validate a merged set of MDL files as one project manifest.

    Relationship resolution is strict here because every referenced model should
    be present once all project files are combined.
    """

    merged_models: list[Any] = []
    merged_relationships: list[Any] = []
    merged_views: list[Any] = []
    for content in contents:
        parsed, parse_message = _parse_yaml(content)
        if parse_message is not None:
            return MdlValidationResult(valid=False, messages=[parse_message])
        merged_models.extend(_extract_models(parsed))
        merged_relationships.extend(_extract_list(parsed, "relationships"))
        merged_views.extend(_extract_list(parsed, "views"))

    merged_yaml = yaml.safe_dump(
        {
            "models": merged_models,
            "relationships": merged_relationships,
            "views": merged_views,
        },
        sort_keys=False,
        allow_unicode=False,
    )
    return validate_mdl(
        merged_yaml,
        schema_index=schema_index,
        strict_relationships=True,
    )


def _validate_models(
    models: list[Any],
    schema_index: SchemaIndex | None,
    messages: list[MdlValidationMessage],
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
        if table is None:
            messages.append(
                MdlValidationMessage(
                    severity="warning",
                    message=(
                        f"Model {name} has no table_reference or ref_sql; it "
                        "cannot be mapped to a physical table."
                    ),
                    code="model_without_mapping",
                )
            )
        elif schema_index is not None and not schema_index.has_table(table):
            messages.append(
                MdlValidationMessage(
                    message=(
                        f"Model {name} references table '{table}' that does not "
                        "exist in the schema."
                    ),
                    code="unknown_table",
                )
            )

        _validate_columns(name, model, table, schema_index, messages)
    return seen_names


def _validate_columns(
    model_name: str,
    model: dict[str, Any],
    table: str | None,
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
        schema_index is not None and table is not None and schema_index.has_table(table)
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
                    message=(
                        f"Duplicate column {model_name}.{column_name}."
                    ),
                    code="duplicate_column",
                )
            )
        seen_columns.add(column_name)

        is_calculated = bool(column.get("is_calculated"))
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
        if (
            table_known
            and table is not None
            and not is_calculated
            and not column.get("relationship")
            and not schema_index.has_column(table, column_name)  # type: ignore[union-attr]
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
                        f"View {view.get('name') or index + 1} is missing a "
                        "statement."
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
        join_type = relationship.get("join_type")
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


def _table_name(model: dict[str, Any]) -> str | None:
    reference = model.get("table_reference")
    if isinstance(reference, dict):
        table = reference.get("table")
        if isinstance(table, str) and table:
            return table
    if model.get("ref_sql"):
        # SQL-backed models are not mapped to a single physical table.
        return None
    return None


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


def _parse_yaml(content: str) -> tuple[Any, MdlValidationMessage | None]:
    if not content.strip():
        return None, MdlValidationMessage(
            message="MDL YAML is empty.",
            code="empty_yaml",
        )
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as ex:
        return None, _yaml_error_message(ex)
    if not isinstance(parsed, dict | list):
        return None, MdlValidationMessage(
            message="MDL YAML must parse to an object or list.",
            code="invalid_root",
        )
    if (isinstance(parsed, dict | list) and len(parsed) == 0) or parsed is None:
        return None, MdlValidationMessage(
            message="MDL YAML must contain at least one entry.",
            code="empty_root",
        )
    return parsed, None


def _yaml_error_message(ex: yaml.YAMLError) -> MdlValidationMessage:
    line: int | None = None
    column: int | None = None
    mark = getattr(ex, "problem_mark", None)
    if mark is not None:
        line = mark.line + 1
        column = mark.column + 1
    return MdlValidationMessage(
        line=line,
        column=column,
        message=str(ex),
        code="yaml_parse_error",
    )
