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
from superset_ai_agent.semantic_layer.engine.dialect_finalize import (
    finalize_native_sql,
)
from superset_ai_agent.semantic_layer.mdl_compile import CompiledManifest
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.metric_inline import (
    build_metric_expression_map,
    inline_metrics,
)


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
    #: When set, ``native_sql`` was transpiled from wren-core's canonical output
    #: into this target dialect (e.g. ``"oracle"``). ``None`` for wren-native
    #: backends. Provenance for auditing and the semantic-mode badge disclosure.
    finalized_dialect: str | None = None
    #: wren-core's canonical output BEFORE dialect finalization, kept for debugging
    #: (compare canonical vs the executed dialect SQL). Equals ``native_sql`` when
    #: no finalization ran.
    canonical_native_sql: str | None = None
    #: Metric names that were inlined into their measure expressions before the
    #: engine planned the SQL (wren_core 0.7.1 has no ``metrics`` concept, so a
    #: bare metric reference is unresolvable — see metric_inline). Provenance for
    #: auditing; empty when the draft referenced no metrics.
    inlined_metrics: list[str] = Field(default_factory=list)


def plan_semantic_sql_step(
    engine: SemanticEngine,
    *,
    sql: str,
    context: AgentContext,
    owner_id: str,
    project_id: str | None,
    mdl_file_store: MdlFileStore | None,
    finalize_enabled: bool = True,
) -> PlanStepResult:
    """Rewrite semantic SQL to native SQL and run the soft hallucination gate.

    The passthrough engine returns SQL unchanged. Never executes. For backends the
    engine does not fully render (e.g. Oracle), ``native_sql`` is finalized by a
    sqlglot transpile pass (:func:`finalize_native_sql`), gated per-backend and by
    ``finalize_enabled``; any transpile gap surfaces as a non-correctable warning.
    """

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

    # Metric inlining: wren_core 0.7.1 has no top-level ``metrics`` concept — it
    # drops the manifest ``metrics`` key, so a metric name is unresolvable and,
    # on the degraded passthrough path, would reach the physical DB verbatim
    # (Oracle ORA-00904). Substitute each metric with its measure expression
    # before the engine plans the SQL. Covers MDL metrics and Superset dataset
    # metrics; a real column of the same name always wins. See
    # plan_metric_semantic_translation_impl.md.
    known_columns = _known_column_names(manifest, context)
    metric_map = build_metric_expression_map(
        manifest_metrics=manifest.metrics,
        dataset_metrics=[
            (metric.name, metric.expression)
            for dataset in context.datasets
            for metric in dataset.metrics
        ],
        known_columns=known_columns,
    )
    inline = inline_metrics(sql, metrics=metric_map, known_columns=known_columns)
    engine_sql = inline.sql

    if engine.name == "passthrough":
        return PlanStepResult(
            semantic_sql=sql,
            native_sql=engine_sql,
            engine=engine.name,
            rewritten=engine_sql.strip() != sql.strip(),
            inlined_metrics=inline.inlined,
        )

    backend = getattr(context.database, "backend", None)
    dialect = resolve_dialect(backend)
    planned = engine.plan_sql(engine_sql, manifest, dialect=dialect)

    warnings = list(planned.warnings)
    # A wren-core rejection (invalid SQL against the manifest) is correctable —
    # feed it to the re-draft loop rather than executing the invalid SQL.
    correctable_warnings: list[str] = list(planned.correctable_warnings)

    # Dialect finalization: transpile wren-core's canonical output into the
    # backend's dialect where the engine does not render clause-level specifics
    # (e.g. Oracle LIMIT -> FETCH FIRST). No-op for wren-native backends. A
    # transpile gap is non-correctable — a semantic re-draft cannot fix it — so it
    # goes to ``warnings`` (engine_warnings -> repair/reflection) but never to
    # ``correctable_warnings`` (which drives the hallucination re-draft loop).
    finalized = finalize_native_sql(
        planned.native_sql, backend=backend, enabled=finalize_enabled
    )
    native_sql = finalized.sql
    warnings.extend(finalized.warnings)
    if manifest.model_names:
        known = {name.lower() for name in manifest.model_names}
        known.update(dataset.table_name.lower() for dataset in context.datasets)
        unknown = [
            table
            for table in extract_referenced_tables(engine_sql, dialect=dialect)
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
        native_sql=native_sql,
        engine=planned.engine,
        rewritten=planned.rewritten or bool(inline.inlined),
        referenced_tables=planned.referenced_tables,
        warnings=warnings,
        correctable_warnings=correctable_warnings,
        finalized_dialect=finalized.target_dialect if finalized.transpiled else None,
        canonical_native_sql=planned.native_sql,
        inlined_metrics=inline.inlined,
    )


def _known_column_names(
    manifest: CompiledManifest,
    context: AgentContext,
) -> frozenset[str]:
    """All physical column names known from the manifest models and datasets.

    A metric whose name collides with one of these is never inlined — the real
    column is authoritative (see metric_inline.build_metric_expression_map).
    """

    names: set[str] = set()
    for model in manifest.models:
        for column in model.get("columns") or []:
            if isinstance(column, dict):
                column_name = column.get("name")
                if isinstance(column_name, str) and column_name:
                    names.add(column_name)
    for dataset in context.datasets:
        for column in dataset.columns:
            if column.name:
                names.add(column.name)
    return frozenset(names)


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
