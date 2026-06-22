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

"""Shared semantic-SQL planning step used by both agent graphs.

Keeps the rewrite + soft physical-resolution gate in one tested place so the
one-shot and conversation graphs cannot drift.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.schemas import AuditInfo
from superset_ai_agent.semantic_layer.engine.base import (
    extract_referenced_tables,
    resolve_dialect,
    SemanticEngine,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore


class PlanStepResult(BaseModel):
    """Outcome of the semantic-SQL planning step (graph-state agnostic)."""

    semantic_sql: str
    native_sql: str
    engine: str
    rewritten: bool = False
    referenced_tables: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    #: Subset of ``warnings`` a model re-draft could plausibly fix (the
    #: hallucination gate). Degrade reasons (unsupported dialect, passthrough)
    #: are excluded — re-drafting cannot fix those, so they never drive the
    #: engine-correction loop (1.4).
    correctable_warnings: list[str] = Field(default_factory=list)


def plan_semantic_sql_step(
    engine: SemanticEngine,
    *,
    sql: str,
    context: AgentContext,
    owner_id: str,
    project_id: str | None,
    mdl_file_store: MdlFileStore | None,
) -> PlanStepResult:
    """Rewrite semantic SQL to native SQL and run the soft hallucination gate.

    The passthrough engine returns SQL unchanged. Never executes.
    """

    if engine.name == "passthrough":
        return PlanStepResult(
            semantic_sql=sql,
            native_sql=sql,
            engine=engine.name,
            rewritten=False,
        )

    active_files = []
    if project_id and mdl_file_store is not None:
        try:
            active_files = [
                file
                for file in mdl_file_store.list(project_id, owner_id=owner_id)
                if file.status == "active" and file.deleted_at is None
            ]
        except Exception:  # pylint: disable=broad-except
            active_files = []
    manifest = engine.compile(active_files)
    dialect = resolve_dialect(getattr(context.database, "backend", None))
    planned = engine.plan_sql(sql, manifest, dialect=dialect)

    warnings = list(planned.warnings)
    correctable_warnings: list[str] = []
    if manifest.model_names:
        known = {name.lower() for name in manifest.model_names}
        known.update(dataset.table_name.lower() for dataset in context.datasets)
        unknown = [
            table
            for table in extract_referenced_tables(sql, dialect=dialect)
            if table.lower() not in known
        ]
        if unknown:
            hallucination = (
                "Semantic SQL references unknown models/tables: "
                + ", ".join(sorted(unknown))
            )
            warnings.append(hallucination)
            correctable_warnings.append(hallucination)

    return PlanStepResult(
        semantic_sql=sql,
        native_sql=planned.native_sql,
        engine=planned.engine,
        rewritten=planned.rewritten,
        referenced_tables=planned.referenced_tables,
        warnings=warnings,
        correctable_warnings=correctable_warnings,
    )


def with_engine_provenance(
    audit: AuditInfo | None,
    *,
    engine: str | None,
    semantic_sql: str | None,
    native_sql: str | None,
) -> AuditInfo | None:
    """Stamp semantic-engine provenance onto an execution audit record."""

    if engine is None:
        return audit
    base = audit or AuditInfo()
    return base.model_copy(
        update={
            "engine": engine,
            "semantic_sql": semantic_sql,
            "native_sql": native_sql,
        }
    )
