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

"""Passthrough engine: the zero-dependency default SemanticEngine binding.

It compiles MDL and returns SQL unchanged from ``plan_sql`` (the LLM writes
native SQL directly). Structural/physical validation of authoring JSON stays on
the existing ``validate_project_manifest`` path (activation gate, materializer);
the engine's ``validate`` covers only engine-specific deep checks, which the
passthrough binding does not perform.
"""

from __future__ import annotations

from superset_ai_agent.semantic_layer.engine.base import (
    extract_referenced_tables,
    PlannedSql,
)
from superset_ai_agent.semantic_layer.mdl_compile import (
    compile_manifest,
    CompiledManifest,
)
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlValidationMessage,
    MdlValidationResult,
)


class PassthroughEngine:
    """Default engine: no semantic rewrite, no deep validation."""

    name = "passthrough"

    def is_available(self) -> bool:
        return True

    def compile(self, mdl_files: list[MdlFile]) -> CompiledManifest:
        return compile_manifest(mdl_files)

    def validate(
        self,
        manifest: CompiledManifest,
        *,
        deep: bool = False,
        schema_index: SchemaIndex | None = None,
    ) -> MdlValidationResult:
        return MdlValidationResult(
            valid=True,
            messages=[
                MdlValidationMessage(
                    severity="info",
                    message="Deep validation skipped (engine=passthrough).",
                    code="engine_passthrough",
                )
            ],
        )

    def plan_sql(
        self,
        semantic_sql: str,
        manifest: CompiledManifest,
        *,
        dialect: str | None = None,
    ) -> PlannedSql:
        return PlannedSql(
            native_sql=semantic_sql,
            engine=self.name,
            rewritten=False,
            referenced_tables=extract_referenced_tables(semantic_sql, dialect=dialect),
            warnings=["semantic rewrite skipped (engine=passthrough)"],
        )
