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

"""Optional deep MDL validation via Wren's Rust semantic engine (wren-core).

This augments — never replaces — the always-on structural/physical validator in
:mod:`superset_ai_agent.semantic_layer.mdl_validator`. ``wren-core`` is an
optional native dependency; its absence is import-guarded and degrades to a
no-op (returns valid with an informational message).

Validation is performed by loading the MDL manifest into wren-core: a malformed
or semantically inconsistent manifest raises when ``to_manifest`` parses it or
when ``SessionContext`` is constructed, which we surface as validation errors.

NOTE: wren-core consumes a manifest in ``wren-core-base``'s exact serde shape
(camelCase: ``tableReference``, ``joinType``, ``isCalculated``). The
``to_wren_core_manifest`` mapping below targets that shape and MUST be
re-verified against the installed wren-core version before relying on it (see
``wren_model.md`` risk R9/R11).
"""

from __future__ import annotations

import base64
import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.semantic_layer.mdl_compile import (
    model_to_camel,
    relationship_to_camel,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlValidationMessage,
    MdlValidationResult,
)

try:  # pragma: no cover - exercised only when wren-core is installed
    from wren_core import SessionContext, to_manifest  # type: ignore

    _WREN_CORE_IMPORT_ERROR: str | None = None
except Exception as ex:  # pylint: disable=broad-except
    SessionContext = None  # type: ignore[assignment,misc]
    to_manifest = None  # type: ignore[assignment]
    _WREN_CORE_IMPORT_ERROR = str(ex)


def wren_core_available() -> bool:
    """Return whether the optional wren-core engine is importable."""

    return SessionContext is not None and to_manifest is not None


def validate_with_wren_core(
    models: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> MdlValidationResult:
    """Deep-validate a manifest with wren-core, if available.

    Returns ``valid=True`` with an informational message when wren-core is not
    installed, so callers can merge this result unconditionally.
    """

    if not wren_core_available():
        return MdlValidationResult(
            valid=True,
            messages=[
                MdlValidationMessage(
                    severity="info",
                    message="wren-core is not installed; deep validation skipped.",
                    code="wren_core_unavailable",
                )
            ],
        )

    manifest_dict = to_wren_core_manifest(models, relationships)
    encoded = base64.b64encode(
        json.dumps(manifest_dict).encode("utf-8")
    ).decode("ascii")
    try:
        manifest = to_manifest(encoded)  # type: ignore[misc]
        SessionContext(manifest, [])  # type: ignore[misc]
    except Exception as ex:  # pylint: disable=broad-except
        return MdlValidationResult(
            valid=False,
            messages=[
                MdlValidationMessage(
                    message=f"wren-core rejected the manifest: {ex}",
                    code="wren_core_error",
                )
            ],
        )
    return MdlValidationResult(valid=True)


def to_wren_core_manifest(
    models: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """Map snake_case MDL models/relationships to the wren-core manifest shape.

    Delegates to :mod:`superset_ai_agent.semantic_layer.mdl_compile`, the single
    source of the snake->camel mapping, so deep validation and engine
    compilation can never drift apart (wren_full.md R9/R16).
    """

    return {
        "catalog": "wren",
        "schema": "public",
        "models": [model_to_camel(model) for model in models],
        "relationships": [relationship_to_camel(rel) for rel in relationships],
    }
