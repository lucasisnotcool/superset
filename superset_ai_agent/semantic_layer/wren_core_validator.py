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

MDL is authored and stored in wren-core's native shape (camelCase), so there is
no field mapping here: ``to_wren_core_manifest`` simply wraps the native entity
lists in the manifest envelope. The native shape is pinned to the installed
wheel by ``test_native_manifest_contract.py``.
"""

from __future__ import annotations

import base64
import json  # noqa: TID251 - standalone agent JSON contract
import re
from typing import Any

from superset_ai_agent.semantic_layer.schemas import (
    MdlValidationMessage,
    MdlValidationResult,
)

try:  # pragma: no cover - exercised only when wren-core is installed
    from wren_core import SessionContext  # type: ignore

    _WREN_CORE_IMPORT_ERROR: str | None = None
except Exception as ex:  # pylint: disable=broad-except
    SessionContext = None  # type: ignore[assignment,misc]
    _WREN_CORE_IMPORT_ERROR = str(ex)


def wren_core_available() -> bool:
    """Return whether the optional wren-core engine is importable."""

    return SessionContext is not None


def validate_with_wren_core(
    models: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> MdlValidationResult:
    """Deep-validate a manifest with wren-core, if available.

    Returns ``valid=True`` with an informational message when wren-core is not
    installed, so callers can merge this result unconditionally.
    """

    return validate_engine_manifest(to_wren_core_manifest(models, relationships))


def validate_engine_manifest(engine_manifest: dict[str, Any]) -> MdlValidationResult:
    """Deep-validate an already-compiled (camelCase) engine manifest with wren-core.

    Use this when the manifest is already in wren-core shape (e.g. from
    ``CompiledManifest.to_engine_manifest``); ``validate_with_wren_core`` is the
    snake_case-input wrapper that maps then delegates here.
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

    encoded = base64.b64encode(
        json.dumps(engine_manifest).encode("utf-8")
    ).decode("ascii")
    try:
        # Constructing a SessionContext loads + validates the manifest.
        SessionContext(encoded)  # type: ignore[misc]
    except Exception as ex:  # pylint: disable=broad-except
        return MdlValidationResult(
            valid=False,
            messages=[_friendly_engine_error(str(ex))],
        )
    return MdlValidationResult(valid=True)


def _friendly_engine_error(raw: str) -> MdlValidationMessage:
    """Translate wren-core's serde errors into field-anchored guidance.

    wren-core reports schema violations as Rust serde errors with a byte offset
    (e.g. ``missing field `type` at line 1 column 4109``) that is meaningless to a
    user. Recognize the common shapes and surface an actionable message instead.
    """

    missing = re.search(r"missing field `([^`]+)`", raw)
    if missing is not None:
        field = missing.group(1)
        return MdlValidationMessage(
            message=(
                f"The manifest is missing the required field '{field}'. Every "
                f"column needs a 'type'; check that no column omits it."
                if field == "type"
                else f"The manifest is missing the required field '{field}'."
            ),
            code="wren_core_missing_field",
        )
    variant = re.search(r"unknown variant `([^`]+)`", raw)
    if variant is not None:
        return MdlValidationMessage(
            message=(
                f"The manifest uses an unsupported value '{variant.group(1)}' "
                "(for example an invalid joinType). Use one of the documented "
                "enum values."
            ),
            code="wren_core_unknown_variant",
        )
    return MdlValidationMessage(
        message=f"wren-core rejected the manifest: {raw}",
        code="wren_core_error",
    )


def to_wren_core_manifest(
    models: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """Wrap native models/relationships in the wren-core manifest envelope.

    MDL is already authored in wren-core's native shape, so this is a pass-through
    that only adds the catalog/schema envelope — no field translation.
    """

    return {
        "catalog": "wren",
        "schema": "public",
        "models": models,
        "relationships": relationships,
    }
