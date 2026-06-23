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

"""Canonical typed MDL schema — wren-core's *native* manifest shape.

This is the single source of truth for the MDL vocabulary. There is **no
snake_case authoring dialect and no translation layer**: the field names here are
exactly what wren-core deserializes (camelCase: ``tableReference``, ``joinType``,
``isCalculated``, ``notNull``, ``refSql``, ``baseObject``, ``timeDimensions``).

Verified empirically against ``wren-core-py`` 0.7.1 (2026-06-23): a manifest in
this shape loads into ``SessionContext`` and rewrites SQL; a column missing
``type`` is rejected with ``missing field 'type'``; a snake_case
``table_reference`` is silently ignored (the model is treated as having no
source). The golden round-trip test ``test_native_manifest_contract.py`` pins
this shape to the installed wheel.

Python attributes stay snake_case for readability; the camelCase JSON form is
produced/consumed via aliases (``populate_by_name=True``), so
``model_dump(by_alias=True)`` and ``model_json_schema(by_alias=True)`` both emit
the native shape. ``extra="allow"`` keeps forward-compatible keys such as
``properties`` (synonyms, business notes) — wren-core tolerates unknown fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

JOIN_TYPES: frozenset[str] = frozenset(
    {
        "ONE_TO_ONE",
        "ONE_TO_MANY",
        "MANY_TO_ONE",
        "MANY_TO_MANY",
    }
)

#: Key under which a manifest/file carries model definitions (native shape).
MODEL_CONTAINER_KEYS: tuple[str, ...] = ("models",)

_NATIVE_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="allow",
)


class MdlColumn(BaseModel):
    """A model column, calculated field, or relationship column."""

    model_config = _NATIVE_CONFIG

    name: str
    #: Physical/logical type. Required by wren-core for non-relationship columns;
    #: validation enforces presence so a missing type cannot reach the engine.
    type: str | None = None
    is_calculated: bool = False
    expression: str | None = None
    relationship: str | None = None
    not_null: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlTableReference(BaseModel):
    """Physical table mapping for a model."""

    model_config = _NATIVE_CONFIG

    catalog: str | None = None
    # wren-core uses the bare key ``schema`` here (not ``schemaName``).
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None


class MdlModel(BaseModel):
    """A logical dataset backed by a physical table or SQL definition."""

    model_config = _NATIVE_CONFIG

    name: str
    table_reference: MdlTableReference | None = None
    ref_sql: str | None = None
    columns: list[MdlColumn] = Field(default_factory=list)
    primary_key: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlRelationship(BaseModel):
    """A reusable join between two models."""

    model_config = _NATIVE_CONFIG

    name: str
    models: list[str] = Field(default_factory=list)
    join_type: str | None = None
    condition: str | None = None


class MdlView(BaseModel):
    """A named SQL statement that behaves like a stable virtual table."""

    model_config = _NATIVE_CONFIG

    name: str
    statement: str
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlMetric(BaseModel):
    """A reusable aggregation defined once and referenced across queries."""

    model_config = _NATIVE_CONFIG

    name: str
    base_object: str | None = None
    expression: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlCube(BaseModel):
    """A structured aggregation object (measures, dimensions, time dimensions)."""

    model_config = _NATIVE_CONFIG

    name: str
    measures: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    time_dimensions: list[dict[str, Any]] = Field(default_factory=list)
    hierarchies: list[dict[str, Any]] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlManifest(BaseModel):
    """A full MDL manifest (one file or a merged project manifest)."""

    model_config = _NATIVE_CONFIG

    catalog: str = "wren"
    schema_name: str = Field(default="public", alias="schema")
    models: list[MdlModel] = Field(default_factory=list)
    relationships: list[MdlRelationship] = Field(default_factory=list)
    views: list[MdlView] = Field(default_factory=list)
    metrics: list[MdlMetric] = Field(default_factory=list)
    cubes: list[MdlCube] = Field(default_factory=list)
