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

"""Deterministic column identity + type resolution for the MDL seed builder.

The onboarding seed is built deterministically from the Superset catalog (the
"W3" split: structure from the catalog, semantics from the model). Two catalog
realities used to leak through as wren-core errors:

- **Non-identifier physical names** (a column literally named ``2003``, ``%
  growth``) need a clean *logical* handle, but wren-core resolves a plain column
  by its ``name`` — so a sanitized handle with no physical back-reference both
  fails validation and would generate invalid SQL. :func:`safe_identifier` +
  :func:`physical_column_reference` produce the handle **and** the wren-core
  ``expression`` that maps it back to the real column (Wren's documented
  "expression-as-physical-rename"). This is ``D-A`` in the spec.
- **Typeless catalog columns** (``ColumnSummary.type is None``) must not reach
  wren-core untyped, but must also never be *guessed* into a wrong type that
  silently breaks aggregations. :func:`resolve_column_type` is a fail-closed
  ladder: real type → generic-family fallback → datetime flag → unresolved
  (``None``), the last of which stays untyped so validation blocks activation
  until a human/Copilot supplies a type. This is ``D-B``/``D-C`` in the spec.

This module is the single source of truth so the seed builder
(``mdl_exporter``) and any matching layer agree on logical-vs-physical naming
rather than diverging (the original ``_2003`` bug).
"""

from __future__ import annotations

import re

from superset_ai_agent.integrations.superset.client import ColumnSummary

#: Superset ``GenericDataType`` family name → a concrete wren-core type. Family
#: names mirror Superset's enum (``TEMPORAL``/``NUMERIC``/``STRING``/``BOOLEAN``);
#: the targets are chosen to land in the validator's matching type-family so a
#: generic-resolved column never trips a cross-family mismatch.
_GENERIC_TYPE_MAP: dict[str, str] = {
    "TEMPORAL": "TIMESTAMP",
    "NUMERIC": "DOUBLE",
    "STRING": "VARCHAR",
    "BOOLEAN": "BOOLEAN",
}


def safe_identifier(value: str) -> str:
    """Sanitize an arbitrary catalog name into a wren-core-safe logical handle.

    Non-``[A-Za-z0-9_]`` runs collapse to ``_``; a leading digit is prefixed with
    ``_`` (``2003`` → ``_2003``) because a bare digit is not a valid identifier.
    Pure-punctuation names degrade to ``unnamed`` rather than the empty string.
    """

    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    normalized = normalized.strip("_")
    if not normalized:
        return "unnamed"
    if normalized[0].isdigit():
        return f"_{normalized}"
    return normalized


def physical_column_reference(name: str) -> str:
    """A wren-core column ``expression`` that targets a physical column by name.

    Double-quoted (with embedded quotes doubled) so non-identifier physical names
    — leading digits, spaces, punctuation — resolve at query time. Used only when
    :func:`safe_identifier` changed the name, so the logical handle still maps to
    the real column.
    """

    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def resolve_column_type(column: ColumnSummary) -> tuple[str | None, bool]:
    """Resolve a column's type via a deterministic, fail-closed ladder.

    Returns ``(type, inferred)``:

    - the **real catalog type** when present (``inferred=False``);
    - else a concrete type from the column's **generic family** (``inferred=True``);
    - else ``TIMESTAMP`` when the column is flagged a **datetime** (``inferred=True``);
    - else ``(None, False)`` — genuinely unresolved. The caller leaves the column
      untyped so wren-core/validation blocks activation; the type is never guessed
      (``D-B``: silent coercion corrupts data; ``D-C``: the model never invents
      structure).
    """

    if column.type:
        return column.type, False
    generic = (column.type_generic or "").upper()
    if mapped := _GENERIC_TYPE_MAP.get(generic):
        return mapped, True
    if column.is_dttm:
        return "TIMESTAMP", True
    return None, False
