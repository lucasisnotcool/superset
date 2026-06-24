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

"""MDL compilation: merge authored JSON files into one engine manifest.

MDL is authored, stored, and validated in wren-core's **native** manifest shape
(camelCase JSON — ``tableReference``, ``joinType``, ``isCalculated``). There is
no snake_case dialect and no field translation: ``compile_manifest`` merely
parses each file's JSON and merges the native entity lists into the manifest
envelope (catalog/schema/dataSource + models/relationships/views/metrics/cubes).

``CompiledManifest.to_base64_json`` produces exactly what wren-core's
``SessionContext`` / ``to_manifest`` consume. Verified against wren-core-py 0.7.1
by ``test_native_manifest_contract.py``.
"""

from __future__ import annotations

import base64
import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from superset_ai_agent.semantic_layer.schemas import MdlFile

#: Keys under which a JSON file may carry each native entity list.
_MODEL_KEYS: tuple[str, ...] = ("models",)
_RELATIONSHIP_KEYS: tuple[str, ...] = ("relationships",)
_VIEW_KEYS: tuple[str, ...] = ("views",)
_METRIC_KEYS: tuple[str, ...] = ("metrics",)
_CUBE_KEYS: tuple[str, ...] = ("cubes",)


class CompiledManifest(BaseModel):
    """A compiled, engine-ready MDL manifest in wren-core's native shape.

    This is the artifact the ``SemanticEngine`` seam consumes. Its entity bodies
    are the authored native dicts, merged unchanged from the project files.
    """

    model_config = ConfigDict(populate_by_name=True)

    catalog: str = "wren"
    schema_name: str = Field(default="public", alias="schema")
    data_source: dict[str, Any] | None = None
    models: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    views: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    cubes: list[dict[str, Any]] = Field(default_factory=list)

    def to_engine_manifest(self) -> dict[str, Any]:
        """Return the full native manifest dict wren-core's ``to_manifest`` wants."""

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
        if self.cubes:
            out["cubes"] = self.cubes
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
    json_contents: list[str] | None = None,
    catalog: str = "wren",
    schema: str = "public",
    data_source: dict[str, Any] | None = None,
) -> CompiledManifest:
    """Compile authored JSON files into one native engine manifest.

    Pass either ``mdl_files`` (their ``content`` is read) or raw ``json_contents``.
    Files are merged in the given order; same-named entities are deduped **last-wins**
    (so an enrichment file overlaying a base model overrides it rather than producing a
    duplicate that would double-register the physical table). Entity bodies are passed
    through unchanged — they are already native.
    """

    if json_contents is None:
        json_contents = [file.content for file in (mdl_files or [])]
    merged = _merge_json(json_contents)
    return CompiledManifest(
        catalog=catalog,
        schema=schema,
        data_source=data_source,
        models=merged["models"],
        relationships=merged["relationships"],
        views=merged["views"],
        metrics=merged["metrics"],
        cubes=merged["cubes"],
    )


def _merge_json(json_contents: list[str]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {
        "models": [],
        "relationships": [],
        "views": [],
        "metrics": [],
        "cubes": [],
    }
    key_map = {
        "models": _MODEL_KEYS,
        "relationships": _RELATIONSHIP_KEYS,
        "views": _VIEW_KEYS,
        "metrics": _METRIC_KEYS,
        "cubes": _CUBE_KEYS,
    }
    for content in json_contents:
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for target, keys in key_map.items():
            merged[target].extend(_collect(payload, keys))
    # A manifest must have uniquely-named entities: two files defining the same model
    # (e.g. an onboarding per-table file + an enrichment file that overlays the same
    # models) would otherwise register the physical table twice and wren-core rejects
    # it ("table ... already exists"). Dedupe by name, last-wins — callers compile in
    # path-sorted order, so the enrichment/semantics file (later path) overrides the
    # base onboarding file.
    for target in merged:
        merged[target] = dedupe_named_entities(merged[target])
    return merged


def dedupe_named_entities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-``name`` entities, last occurrence winning; keep unnamed as-is."""

    by_name: dict[str, dict[str, Any]] = {}
    unnamed: list[dict[str, Any]] = []
    for item in items:
        name = item.get("name")
        if isinstance(name, str) and name:
            by_name[name] = item  # last-wins; first-seen position preserved
        else:
            unnamed.append(item)
    return [*by_name.values(), *unnamed]


def _collect(payload: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    return items
