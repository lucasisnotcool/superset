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
"""Prepare step 2d: infer the minimal target Oracle schemas from the inputs.

Given the business context + the list of schemas actually available on the
connection (from ``introspect_schema``), asks the model (prompt §9.3) for the minimal
set the questions require, with a rationale. Pure core (injected chat).
"""

from __future__ import annotations

from typing import Any, Callable

from prepare import _agent_pass as ap

TARGETS_SYSTEM = (
    "From the business context and the list of available database schemas/tables "
    "below, select the MINIMAL set of schemas the evaluation questions require. Do not "
    "include a schema unless the context references its data. Output ONLY a JSON "
    'object: {"schemas": ["..."], "rationale": {"schema": "why"}}.'
)


def schemas_from_reply(reply: str) -> tuple[list[str], dict[str, str]]:
    """Parse the model reply into ``(schemas, rationale)``. Raises on bad JSON."""

    data = ap.extract_json(reply)
    if not isinstance(data, dict) or "schemas" not in data:
        raise ValueError("targets reply must be an object with a 'schemas' array")
    schemas = [str(s).strip() for s in data.get("schemas", []) if str(s).strip()]
    rationale = {str(k): str(v) for k, v in (data.get("rationale") or {}).items()}
    return schemas, rationale


def generate_targets(
    inputs_text: str,
    available_schemas: list[str],
    *,
    chat: Callable[[str, str], str],
) -> tuple[list[str], dict[str, str]]:
    """Infer target schemas; keep only those actually available (case-insensitive)."""

    user = (
        f"BUSINESS CONTEXT:\n{inputs_text}\n\n"
        f"AVAILABLE SCHEMAS: {', '.join(available_schemas) or '(none)'}\n\n"
        "Select the minimal target schemas now."
    )
    schemas, rationale = schemas_from_reply(chat(TARGETS_SYSTEM, user))
    have = {s.lower(): s for s in available_schemas}
    kept = [have[s.lower()] for s in schemas if s.lower() in have]
    return kept, rationale


def as_manifest_dict(
    fixture_id: str,
    schemas: list[str],
    *,
    database_name: str | None = None,
    connection_uri_env: str | None = None,
    onboard_mode: str = "auto",
    corpus: str = "questions.csv",
    context_docs: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble a fixture manifest dict (written to fixture.yaml by main)."""

    manifest: dict[str, Any] = {
        "id": fixture_id,
        "schemas": schemas,
        "onboard_mode": onboard_mode,
        "corpus": corpus,
        "context_docs": context_docs or ["context/*.md"],
    }
    if database_name:
        manifest["database_name"] = database_name
    if connection_uri_env:
        manifest["connection_uri_env"] = connection_uri_env
    return manifest
