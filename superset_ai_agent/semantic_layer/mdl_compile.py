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

"""Canonical MDL compilation: authoring YAML (snake_case) -> engine manifest.

This module is the **single source of camelCase truth** for the agent. MDL is
authored and stored as readable snake_case YAML (the `mdl_schema` spec); the
semantic engine (wren-core) consumes a camelCase manifest (`tableReference`,
`joinType`, `isCalculated`, ...). `compile_manifest` performs that mapping once,
so no other module hand-rolls camelCase (resolves wren_full.md R9/R16).

The compiled manifest mirrors Wren's own model: source YAML -> compiled
`mdl.json`. `CompiledManifest.to_base64_json` produces exactly what wren-core's
`to_manifest` expects.
"""

from __future__ import annotations

import base64
import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from superset_ai_agent.semantic_layer.schemas import MdlFile

#: Keys under which a YAML file may carry model definitions (snake_case spec).
_MODEL_KEYS: tuple[str, ...] = ("models", "semantic_models")
_RELATIONSHIP_KEYS: tuple[str, ...] = ("relationships",)
_VIEW_KEYS: tuple[str, ...] = ("views",)
_METRIC_KEYS: tuple[str, ...] = ("metrics",)


class CompiledManifest(BaseModel):
    """A compiled, engine-ready MDL manifest (camelCase model/relationship bodies).

    This is the artifact the `SemanticEngine` seam consumes. It is intentionally
    decoupled from the authoring YAML so the engine never sees snake_case.
    """

    model_config = ConfigDict(populate_by_name=True)

    catalog: str = "wren"
    schema_name: str = Field(default="public", alias="schema")
    data_source: dict[str, Any] | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    views: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)

    def to_engine_manifest(self) -> dict[str, Any]:
        """Return the full camelCase manifest dict wren-core's ``to_manifest`` wants."""

        out: dict[str, Any] = {
            "catalog": self.catalog,
            "schema": self.schema_name,
            "models": self.models,
            "relationships": self.relationships,
        }
        if self.views:
            out["views"] = self.views
        if self.metrics:
            out["metrics"] = self.metrics
        if self.data_source:
            out["dataSource"] = self.data_source
        return out

    def to_base64_json(self) -> str:
        """Return base64(JSON) of the engine manifest (wren-core input shape)."""

        return base64.b64encode(
            json.dumps(self.to_engine_manifest()).encode("utf-8")
        ).decode("ascii")

    @property
    def model_names(self) -> list[str]:
        return [str(model.get("name")) for model in self.models if model.get("name")]


def compile_manifest(
    mdl_files: list[MdlFile] | None = None,
    *,
    yaml_contents: list[str] | None = None,
    catalog: str = "wren",
    schema: str = "public",
    data_source: dict[str, Any] | None = None,
) -> CompiledManifest:
    """Compile authoring YAML files into a camelCase engine manifest.

    Pass either ``mdl_files`` (their ``content`` is read) or raw ``yaml_contents``.
    Files are merged in the given order; later files append, they do not override.
    """

    if yaml_contents is None:
        yaml_contents = [file.content for file in (mdl_files or [])]
    models, relationships, views, metrics = _merge_yaml(yaml_contents)
    return CompiledManifest(
        catalog=catalog,
        schema=schema,
        data_source=data_source,
        models=[model_to_camel(model) for model in models],
        relationships=[relationship_to_camel(rel) for rel in relationships],
        views=[view_to_camel(view) for view in views],
        metrics=[metric for metric in metrics if isinstance(metric, dict)],
    )


def _merge_yaml(
    yaml_contents: list[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    models: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for content in yaml_contents:
        try:
            payload = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if not isinstance(payload, dict):
            continue
        models.extend(_collect(payload, _MODEL_KEYS))
        relationships.extend(_collect(payload, _RELATIONSHIP_KEYS))
        views.extend(_collect(payload, _VIEW_KEYS))
        metrics.extend(_collect(payload, _METRIC_KEYS))
    return models, relationships, views, metrics


def _collect(payload: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items


# --- snake_case authoring -> camelCase engine mapping (single source) --------


def model_to_camel(model: dict[str, Any]) -> dict[str, Any]:
    """Map a snake_case authoring model to the wren-core camelCase shape."""

    reference = model.get("table_reference") or {}
    out: dict[str, Any] = {
        "name": model.get("name"),
        "columns": [column_to_camel(column) for column in model.get("columns", [])],
    }
    if isinstance(reference, dict) and reference.get("table"):
        out["tableReference"] = _drop_none(
            {
                "catalog": reference.get("catalog"),
                "schema": reference.get("schema") or reference.get("schema_name"),
                "table": reference.get("table"),
            }
        )
    if model.get("ref_sql"):
        out["refSql"] = model["ref_sql"]
    if model.get("primary_key"):
        out["primaryKey"] = model["primary_key"]
    return _drop_none(out)


def column_to_camel(column: dict[str, Any]) -> dict[str, Any]:
    return _drop_none(
        {
            "name": column.get("name"),
            "type": column.get("type"),
            "isCalculated": bool(column.get("is_calculated", False)),
            "expression": column.get("expression"),
            "relationship": column.get("relationship"),
            "notNull": bool(column.get("not_null", False)),
        }
    )


def relationship_to_camel(relationship: dict[str, Any]) -> dict[str, Any]:
    return _drop_none(
        {
            "name": relationship.get("name"),
            "models": relationship.get("models"),
            "joinType": relationship.get("join_type"),
            "condition": relationship.get("condition"),
        }
    )


def view_to_camel(view: dict[str, Any]) -> dict[str, Any]:
    return _drop_none(
        {
            "name": view.get("name"),
            "statement": view.get("statement"),
        }
    )


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}
