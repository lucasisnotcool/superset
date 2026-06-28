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

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from pathlib import Path
from typing import Any

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.semantic_layer.column_identity import (
    physical_column_reference,
    resolve_column_type,
    safe_identifier as _safe_identifier,
)


def export_agent_context_to_mdl(
    context: AgentContext,
    *,
    semantic_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert permission-filtered Superset context into a minimal Wren MDL."""

    mdl = {
        "catalog": "superset",
        "dataSource": {
            "name": context.database.name,
            "type": context.database.backend,
            "properties": {
                "superset_database_id": context.database.id,
            },
        },
        "models": [model_from_dataset(dataset) for dataset in context.datasets],
    }
    if semantic_overlay:
        mdl["semanticOverlay"] = semantic_overlay
    return mdl


def write_mdl(
    context: AgentContext,
    output_path: Path,
    *,
    semantic_overlay: dict[str, Any] | None = None,
) -> None:
    """Write a minimal mdl.json for the supplied permission-filtered context."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            export_agent_context_to_mdl(
                context,
                semantic_overlay=semantic_overlay,
            ),
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def model_from_dataset(dataset: DatasetMetadata) -> dict[str, Any]:
    """Map a Superset dataset to a Wren model."""

    model = {
        "name": _safe_identifier(dataset.table_name or f"dataset_{dataset.id}"),
        "tableReference": {
            "schema": dataset.schema_name,
            "table": dataset.table_name,
        },
        "description": dataset.description,
        "columns": [column_to_field(column) for column in dataset.columns],
        "metrics": [measure_from_metric(metric) for metric in dataset.metrics],
        "properties": {
            "superset_dataset_id": dataset.id,
            "superset_database_id": dataset.database_id,
            "source": "superset_ai_agent",
        },
    }
    return _drop_none(model)


def measure_from_metric(metric: MetricSummary) -> dict[str, Any]:
    """Map a Superset metric to a Wren measure."""

    return _drop_none(
        {
            "name": _safe_identifier(metric.name),
            "expression": metric.expression,
            "description": metric.description,
            "properties": {
                "superset_metric_name": metric.name,
            },
        }
    )


def column_to_field(column: ColumnSummary) -> dict[str, Any]:
    """Map a Superset column to a Wren column (wren-core native shape).

    Structure is authoritative from the catalog. Two robustness rules (see
    ``semantic_layer.column_identity``):

    - **Identity (D-A):** the logical ``name`` is sanitized to a wren-core-safe
      handle; when that changes the name (e.g. ``2003`` → ``_2003``, ``% growth``
      → ``growth``) an ``expression`` is emitted that maps the handle back to the
      real physical column, so it both validates and queries correctly.
    - **Type (D-B):** the type is resolved via the deterministic fail-closed
      ladder. A column whose type can't be resolved stays **untyped** (validation
      then blocks activation) and is tagged ``inferred_type=unknown`` so it never
      silently activates with a guessed type; a generic-resolved type is tagged
      ``inferred_type=generic`` for auditability.
    """

    logical_name = _safe_identifier(column.name)
    resolved_type, inferred = resolve_column_type(column)
    properties: dict[str, Any] = {
        "superset_column_name": column.name,
        "is_time": True if column.is_dttm else None,
    }
    if resolved_type is None:
        properties["inferred_type"] = "unknown"
    elif inferred:
        properties["inferred_type"] = "generic"
    return _drop_none(
        {
            "name": logical_name,
            "type": resolved_type,
            "isCalculated": False,
            # Only present when sanitizing renamed the column; wren-core then
            # resolves the handle to the quoted physical column.
            "expression": (
                physical_column_reference(column.name)
                if logical_name != column.name
                else None
            ),
            "description": column.description,
            "properties": _drop_none(properties),
        }
    )


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
