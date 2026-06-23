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

"""Typed authoring contract for LLM-generated MDL.

The model never hand-writes serialized text. It fills this **typed, native-shape**
object (camelCase, matching wren-core), and *we* serialize it to canonical JSON
with :func:`serialize_manifest`. Two failure classes are eliminated at the source:

- *parse errors from hand-written text* (colons, quoting, indentation): impossible,
  because the model returns structured fields, not a string;
- *missing column ``type``*: impossible, because ``AuthoredColumn.type`` is a
  required field — the response schema will not validate without it.

The schema is passed to the model via ``model_json_schema(by_alias=True)`` so the
provider sees the native camelCase field names. ``extra="allow"`` keeps room for
forward-compatible keys (e.g. ``properties`` synonyms) that wren-core tolerates.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

_AUTHORING_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    extra="allow",
)

JoinType = Literal["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"]


class AuthoredTableReference(BaseModel):
    """Physical table a model maps to."""

    model_config = _AUTHORING_CONFIG

    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    table: str


class AuthoredColumn(BaseModel):
    """A model column. ``type`` is required — wren-core rejects a typeless column."""

    model_config = _AUTHORING_CONFIG

    name: str
    type: str
    description: str | None = None
    is_calculated: bool = False
    expression: str | None = None
    relationship: str | None = None
    not_null: bool = False
    properties: dict[str, Any] = Field(default_factory=dict)


class AuthoredModel(BaseModel):
    """A logical dataset backed by a physical table (or SQL)."""

    model_config = _AUTHORING_CONFIG

    name: str
    description: str | None = None
    table_reference: AuthoredTableReference | None = None
    ref_sql: str | None = None
    columns: list[AuthoredColumn] = Field(default_factory=list)
    primary_key: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class AuthoredRelationship(BaseModel):
    """A reusable join between two models."""

    model_config = _AUTHORING_CONFIG

    name: str
    models: list[str]
    join_type: JoinType
    condition: str | None = None


class AuthoredMetric(BaseModel):
    """A reusable aggregation over a base object."""

    model_config = _AUTHORING_CONFIG

    name: str
    base_object: str | None = None
    expression: str | None = None
    description: str | None = None


class AuthoredManifest(BaseModel):
    """One MDL file's content in native shape."""

    model_config = _AUTHORING_CONFIG

    models: list[AuthoredModel] = Field(default_factory=list)
    relationships: list[AuthoredRelationship] = Field(default_factory=list)
    metrics: list[AuthoredMetric] = Field(default_factory=list)


class ProposedMdlFile(BaseModel):
    """One MDL file the model proposes: a path plus a native manifest."""

    model_config = _AUTHORING_CONFIG

    path: str
    manifest: AuthoredManifest
    notes: str | None = None


class MdlProposalResponse(BaseModel):
    """Structured response envelope for MDL generation/enrichment."""

    model_config = _AUTHORING_CONFIG

    files: list[ProposedMdlFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def proposal_response_schema() -> dict[str, Any]:
    """JSON schema (native camelCase) handed to the model as ``format_schema``."""

    return MdlProposalResponse.model_json_schema(by_alias=True)


def serialize_manifest(manifest: AuthoredManifest) -> str:
    """Serialize an authored manifest to canonical native JSON content.

    ``by_alias`` emits camelCase; ``exclude_none`` keeps the file lean; the result
    is exactly what storage, validation, and wren-core consume.
    """

    payload = manifest.model_dump(by_alias=True, exclude_none=True)
    return json.dumps(payload, indent=2)
