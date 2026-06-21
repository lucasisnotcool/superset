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

from typing import Any

import yaml

from superset_ai_agent.semantic_layer.schemas import (
    MdlValidationMessage,
    MdlValidationResult,
)


def validate_mdl_yaml(content: str) -> MdlValidationResult:
    """Validate that a file contains non-empty YAML suitable for MDL review."""

    if not content.strip():
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="MDL YAML is empty.",
                    code="empty_yaml",
                )
            ],
        )

    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as ex:
        return MdlValidationResult(
            valid=False,
            messages=[_yaml_error_message(ex)],
        )

    if not isinstance(parsed, dict | list):
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="MDL YAML must parse to an object or list.",
                    code="invalid_root",
                )
            ],
        )
    if _is_empty(parsed):
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message="MDL YAML must contain at least one model entry.",
                    code="empty_root",
                )
            ],
        )
    return MdlValidationResult(valid=True)


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


def _is_empty(parsed: Any) -> bool:
    if isinstance(parsed, dict | list):
        return len(parsed) == 0
    return parsed is None
