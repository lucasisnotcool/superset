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

"""Canonical typed MDL (Modeling Definition Language) schema.

These models mirror the snake_case Wren MDL spec and serve as the canonical
structural reference for :mod:`superset_ai_agent.semantic_layer.mdl_validator`.
They are intentionally lenient (``extra="allow"``) so unknown/forward-compatible
keys such as ``properties`` do not fail parsing, while required fields are still
enforced.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

JOIN_TYPES: frozenset[str] = frozenset(
    {
        "ONE_TO_ONE",
        "ONE_TO_MANY",
        "MANY_TO_ONE",
        "MANY_TO_MANY",
    }
)

#: Keys under which a manifest/file may carry model definitions.
MODEL_CONTAINER_KEYS: tuple[str, ...] = ("models", "semantic_models")


class MdlColumn(BaseModel):
    """A model column, calculated field, or relationship column."""

    model_config = ConfigDict(extra="allow")

    name: str
    type: str | None = None
    is_calculated: bool = False
    expression: str | None = None
    relationship: str | None = None
    not_null: bool = False
    is_primary_key: bool = False
    is_hidden: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlTableReference(BaseModel):
    """Physical table mapping for a model."""

    model_config = ConfigDict(extra="allow")

    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str | None = None


class MdlModel(BaseModel):
    """A logical dataset backed by a physical table or SQL definition."""

    model_config = ConfigDict(extra="allow")

    name: str
    table_reference: MdlTableReference | None = None
    ref_sql: str | None = None
    columns: list[MdlColumn] = Field(default_factory=list)
    primary_key: str | None = None
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlRelationship(BaseModel):
    """A reusable join between two models."""

    model_config = ConfigDict(extra="allow")

    name: str
    models: list[str] = Field(default_factory=list)
    join_type: str | None = None
    condition: str | None = None


class MdlView(BaseModel):
    """A named SQL statement that behaves like a stable virtual table."""

    model_config = ConfigDict(extra="allow")

    name: str
    statement: str
    properties: dict[str, Any] = Field(default_factory=dict)


class MdlManifest(BaseModel):
    """A full MDL manifest (one file or a merged project manifest)."""

    model_config = ConfigDict(extra="allow")

    models: list[MdlModel] = Field(default_factory=list)
    relationships: list[MdlRelationship] = Field(default_factory=list)
    views: list[MdlView] = Field(default_factory=list)
