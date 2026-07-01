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

"""Single source of truth for semantic-SQL mode and its preconditions.

The agent's ``semantic_sql_mode`` boolean (used to inject authoring guidance) is
only two of the conditions that actually decide whether a query gets rewritten —
notably it is ``True`` on an unsupported dialect (e.g. Oracle), where the engine
silently degrades to passthrough at call time. The badge would mislead if it
mirrored that flag, so :func:`evaluate_semantic_factors` computes the full
precondition set and derives an honest ``semantic``/``native`` verdict. Both the
graphs (for the guidance flag) and the mode-status endpoint (for the badge) call
into here so the two can never drift.
"""

from __future__ import annotations

from typing import Literal

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.semantic_layer.engine.base import (
    resolve_dialect,
    SemanticEngine,
)
from superset_ai_agent.semantic_layer.schemas import (
    SemanticFactorState,
    SemanticModeFactor,
    SemanticModeStatus,
)


def guidance_enabled(config: AgentConfig, engine: SemanticEngine) -> bool:
    """The authoring-guidance flag: inject "write semantic SQL" instructions.

    This is the literal historically inlined at the graph call-sites — factors 1
    and 2 only. Centralized here so the graphs share one definition. It is
    deliberately NARROWER than :func:`evaluate_semantic_factors`' ``semantic``
    verdict; see the module docstring for why the badge must not use this.
    """

    return config.wren_semantic_sql_enabled and engine.name != "passthrough"


def evaluate_semantic_factors(
    *,
    config: AgentConfig,
    engine: SemanticEngine,
    backend: str | None,
    schema_selected: bool,
    project_selected: bool,
    has_active_models: bool,
    context_loaded: bool | None = None,
) -> SemanticModeStatus:
    """Compute every precondition for semantic-SQL mode plus the overall verdict.

    ``mode`` is ``semantic`` only when factors 1-7 are all ``met``; the runtime
    factor (``context_loaded``) is advisory — ``None`` renders as ``runtime`` and
    never blocks the verdict ahead of a query.
    """

    factors: list[SemanticModeFactor] = []

    # Factor 1 — operator enabled semantic SQL authoring.
    factors.append(
        SemanticModeFactor(
            key="semantic_sql_enabled",
            label="Semantic SQL enabled",
            state="met" if config.wren_semantic_sql_enabled else "blocked",
            blocking=not config.wren_semantic_sql_enabled,
            detail=(
                "Semantic SQL authoring is enabled."
                if config.wren_semantic_sql_enabled
                else "Semantic SQL authoring is turned off for this deployment."
            ),
            fixable_by="operator",
        )
    )

    # Factor 2 — the configured engine is the rewriting engine, not passthrough.
    engine_is_wren = config.wren_engine == "wren_core"
    factors.append(
        SemanticModeFactor(
            key="engine_wren_core",
            label="Rewrite engine selected",
            state="met" if engine_is_wren else "blocked",
            blocking=not engine_is_wren,
            detail=(
                "The semantic rewrite engine is selected."
                if engine_is_wren
                else "The deployment is configured for pass-through (no rewrite)."
            ),
            fixable_by="operator",
        )
    )

    # Factor 3 — the rewrite engine's backing dependency is importable/usable.
    engine_available = engine.is_available()
    factors.append(
        SemanticModeFactor(
            key="engine_installed",
            label="Rewrite engine available",
            state="met" if engine_available else "blocked",
            blocking=not engine_available,
            detail=(
                "The semantic rewrite engine is installed and ready."
                if engine_available
                else "The semantic rewrite engine is not available in this deployment."
            ),
            fixable_by="operator",
        )
    )

    # Factor 4 — this database's backend maps to a supported wren-core dialect.
    dialect_supported = resolve_dialect(backend) is not None
    factors.append(
        SemanticModeFactor(
            key="dialect_supported",
            label="Database dialect supported",
            state="met" if dialect_supported else "blocked",
            blocking=not dialect_supported,
            detail=(
                "This database's dialect is supported by the semantic engine."
                if dialect_supported
                else (
                    "This database's dialect is not supported by the semantic "
                    "engine; queries run as native SQL."
                )
            ),
            fixable_by="database",
        )
    )

    # Factor 5 — Wren integration enabled at all.
    factors.append(
        SemanticModeFactor(
            key="wren_enabled",
            label="Semantic layer enabled",
            state="met" if config.wren_enabled else "blocked",
            blocking=not config.wren_enabled,
            detail=(
                "The semantic layer integration is enabled."
                if config.wren_enabled
                else "The semantic layer integration is turned off for this deployment."
            ),
            fixable_by="operator",
        )
    )

    # Factor 6 — a project or schema is selected to ground on (user-actionable).
    scope_selected = schema_selected or project_selected
    factors.append(
        SemanticModeFactor(
            key="scope_selected",
            label="Project or schema selected",
            state="met" if scope_selected else "blocked",
            blocking=not scope_selected,
            detail=(
                "A semantic project or schema is selected."
                if scope_selected
                else "Select a semantic project or a database schema to ground on."
            ),
            fixable_by="user",
        )
    )

    # Factor 7 — the grounded project has at least one active model
    # (user-actionable, but moot until a project/scope is selected).
    active_state: SemanticFactorState
    if not scope_selected:
        active_state = "not_applicable"
        active_blocking = False
        active_detail = "Select a project or schema first."
    elif has_active_models:
        active_state = "met"
        active_blocking = False
        active_detail = "The semantic layer has active models."
    else:
        active_state = "blocked"
        active_blocking = True
        active_detail = "No active semantic models yet; onboard or activate models."
    factors.append(
        SemanticModeFactor(
            key="active_models",
            label="Active semantic models",
            state=active_state,
            blocking=active_blocking,
            detail=active_detail,
            fixable_by="user",
        )
    )

    # Factor 8 — semantic context actually loaded (runtime; advisory only).
    ctx_state: SemanticFactorState
    if context_loaded is None:
        ctx_state = "runtime"
        ctx_blocking = False
        ctx_detail = "Confirmed when a query runs."
    elif context_loaded:
        ctx_state = "met"
        ctx_blocking = False
        ctx_detail = "Semantic context loaded for the last query."
    else:
        ctx_state = "blocked"
        ctx_blocking = True
        ctx_detail = "Semantic context could not be loaded for the last query."
    factors.append(
        SemanticModeFactor(
            key="context_loaded",
            label="Semantic context loaded",
            state=ctx_state,
            blocking=ctx_blocking,
            detail=ctx_detail,
            fixable_by="runtime",
        )
    )

    # Verdict: semantic only when every deterministic factor (1-7) is met AND no
    # factor is actively blocking. The runtime factor is advisory when unknown
    # (``runtime`` state ahead of a query) but a KNOWN failed load
    # (``context_loaded is False``) downgrades to native.
    deterministic = [f for f in factors if f.key != "context_loaded"]
    deterministic_met = all(f.state == "met" for f in deterministic)
    runtime_failed = context_loaded is False
    mode: Literal["semantic", "native"] = (
        "semantic" if deterministic_met and not runtime_failed else "native"
    )
    blocking_factors = [f.key for f in factors if f.blocking]
    user_fixable_blocker = any(f.blocking and f.fixable_by == "user" for f in factors)

    return SemanticModeStatus(
        mode=mode,
        factors=factors,
        blocking_factors=blocking_factors,
        user_fixable_blocker=user_fixable_blocker,
    )
