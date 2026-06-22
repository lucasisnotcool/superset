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

"""WrenCoreEngine: real semantic SQL rewriting via the optional wren-core engine.

This is the parity keystone. ``plan_sql`` loads the compiled manifest into
wren-core and calls ``transform_sql`` to expand models into CTEs and turn
relationships/calculated columns into native joins/expressions — producing SQL
the source database runs. It **never executes**: the rewritten native SQL still
flows through ``validate_read_only_sql`` and the Superset executor.

``wren-core`` is an optional native dependency. When it is absent, every method
degrades to passthrough behavior with a warning, so the service runs unchanged
(governance invariant: every seam degrades closed).

Verified against wren-core-py 0.7.1: `SessionContext(mdl_base64, data_source=
<dialect>)` then `transform_sql(...)`. The dialect is the constructor argument
(not a manifest field), and the manifest must omit a typed `dataSource`. Re-verify
the dialect token map (`base.BACKEND_TO_WREN_DIALECT`) on a wren-core upgrade.
"""

from __future__ import annotations

from superset_ai_agent.semantic_layer.engine.base import (
    extract_referenced_tables,
    PlannedSql,
)
from superset_ai_agent.semantic_layer.engine.passthrough import PassthroughEngine
from superset_ai_agent.semantic_layer.mdl_compile import CompiledManifest
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex
from superset_ai_agent.semantic_layer.schemas import MdlFile, MdlValidationResult
from superset_ai_agent.semantic_layer.wren_core_validator import (
    validate_engine_manifest,
    wren_core_available,
)

try:  # pragma: no cover - exercised only when wren-core is installed
    from wren_core import SessionContext  # type: ignore
except Exception:  # pylint: disable=broad-except
    SessionContext = None  # type: ignore[assignment,misc]


class WrenCoreEngine:
    """Semantic engine backed by wren-core; degrades to passthrough when absent."""

    name = "wren_core"

    def __init__(self) -> None:
        self._passthrough = PassthroughEngine()

    def is_available(self) -> bool:
        return wren_core_available()

    def compile(self, mdl_files: list[MdlFile]) -> CompiledManifest:
        return self._passthrough.compile(mdl_files)

    def validate(
        self,
        manifest: CompiledManifest,
        *,
        deep: bool = False,
        schema_index: SchemaIndex | None = None,
    ) -> MdlValidationResult:
        if not deep:
            return self._passthrough.validate(manifest, schema_index=schema_index)
        return validate_engine_manifest(manifest.to_engine_manifest())

    def plan_sql(
        self,
        semantic_sql: str,
        manifest: CompiledManifest,
        *,
        dialect: str | None = None,
    ) -> PlannedSql:
        if not self.is_available():
            return _degraded(
                semantic_sql,
                dialect,
                "wren-core is not installed; semantic rewrite skipped.",
            )
        resolved = dialect
        if resolved is None:
            return _degraded(
                semantic_sql,
                dialect,
                "Unknown/unmapped SQL dialect; semantic rewrite skipped.",
            )
        try:
            # wren-core 0.7.x: SessionContext loads the base64 manifest directly,
            # and the target dialect is the ``data_source`` constructor argument
            # (NOT a manifest field). The manifest must omit a typed dataSource.
            ctx = SessionContext(  # type: ignore[misc]
                manifest.to_base64_json(),
                data_source=resolved,
            )
            native_sql = ctx.transform_sql(semantic_sql)  # type: ignore[misc]
        except Exception as ex:  # pylint: disable=broad-except
            return _degraded(
                semantic_sql,
                dialect,
                f"wren-core could not plan the SQL: {ex}",
            )
        return PlannedSql(
            native_sql=native_sql,
            engine=self.name,
            rewritten=native_sql.strip() != semantic_sql.strip(),
            referenced_tables=extract_referenced_tables(native_sql, dialect=resolved),
            warnings=[],
        )


def _degraded(semantic_sql: str, dialect: str | None, reason: str) -> PlannedSql:
    """Fall back to returning the input SQL unchanged, with a warning."""

    return PlannedSql(
        native_sql=semantic_sql,
        engine="wren_core",
        rewritten=False,
        referenced_tables=extract_referenced_tables(semantic_sql, dialect=dialect),
        warnings=[reason],
    )
