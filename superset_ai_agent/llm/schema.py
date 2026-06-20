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

from copy import deepcopy
from typing import Any


def to_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON Schema compatible with strict structured outputs."""

    strict_schema = deepcopy(schema)
    _mark_objects_closed(strict_schema)
    return strict_schema


def _mark_objects_closed(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _mark_objects_closed(item)
        return

    if not isinstance(value, dict):
        return

    _close_object_schema(value)
    _mark_dict_children(value, ("$defs", "definitions", "properties"))
    _mark_schema_children(
        value, ("items", "prefixItems", "anyOf", "allOf", "oneOf", "not")
    )
    _strip_null_default(value)


def _close_object_schema(value: dict[str, Any]) -> None:
    properties = value.get("properties")
    if value.get("type") == "object" or isinstance(properties, dict):
        value["additionalProperties"] = False
        if isinstance(properties, dict):
            value["required"] = list(properties.keys())


def _mark_dict_children(value: dict[str, Any], child_keys: tuple[str, ...]) -> None:
    for child_key in child_keys:
        if child_key in value and isinstance(value[child_key], dict):
            for child in value[child_key].values():
                _mark_objects_closed(child)


def _mark_schema_children(value: dict[str, Any], child_keys: tuple[str, ...]) -> None:
    for child_key in child_keys:
        if child_key in value:
            _mark_objects_closed(value[child_key])


def _strip_null_default(value: dict[str, Any]) -> None:
    if value.get("default") is None:
        value.pop("default", None)
