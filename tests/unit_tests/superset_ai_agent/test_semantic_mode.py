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

"""Factor-matrix tests for the semantic-SQL mode evaluator (single source of truth)."""

from __future__ import annotations

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.semantic_layer.engine.mode import (
    evaluate_semantic_factors,
    guidance_enabled,
)
from superset_ai_agent.semantic_layer.engine.passthrough import PassthroughEngine


class _FakeEngine:
    """Minimal SemanticEngine stand-in with controllable name/availability."""

    def __init__(self, name: str = "wren_core", available: bool = True) -> None:
        self.name = name
        self._available = available

    def is_available(self) -> bool:
        return self._available


def _enabled_config() -> AgentConfig:
    return AgentConfig(
        wren_enabled=True,
        wren_engine="wren_core",
        wren_semantic_sql_enabled=True,
    )


def _factor(status, key):
    return next(f for f in status.factors if f.key == key)


def test_happy_path_is_semantic() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "semantic"
    assert status.blocking_factors == []
    assert status.user_fixable_blocker is False
    # The runtime factor is advisory and must not block the verdict pre-query.
    assert _factor(status, "context_loaded").state == "runtime"


def test_oracle_is_supported_via_dialect_finalization() -> None:
    """Oracle is now supported through the dialect-finalization (transpile) stage.

    Adding ``oracle`` to the wren-dialect map (which requires the transpile pass to
    ship in tandem) flips the ``dialect_supported`` factor to met, so the verdict is
    semantic — with the badge disclosing the transpile (see the endpoint/badge tests).
    """

    config = _enabled_config()
    engine = _FakeEngine(name="wren_core", available=True)
    status = evaluate_semantic_factors(
        config=config,
        engine=engine,
        backend="oracle",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "semantic"
    assert status.blocking_factors == []
    assert _factor(status, "dialect_supported").state == "met"
    assert guidance_enabled(config, engine) is True


def test_unsupported_dialect_still_blocks() -> None:
    """A dialect absent from the map (e.g. sqlite) still blocks — no false-green."""

    config = _enabled_config()
    status = evaluate_semantic_factors(
        config=config,
        engine=_FakeEngine(name="wren_core", available=True),
        backend="sqlite",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "native"
    assert status.blocking_factors == ["dialect_supported"]
    assert _factor(status, "dialect_supported").fixable_by == "database"


def test_flag_off_is_native() -> None:
    config = AgentConfig(
        wren_enabled=True,
        wren_engine="wren_core",
        wren_semantic_sql_enabled=False,
    )
    status = evaluate_semantic_factors(
        config=config,
        engine=_FakeEngine(),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "native"
    assert "semantic_sql_enabled" in status.blocking_factors
    assert _factor(status, "semantic_sql_enabled").fixable_by == "operator"


def test_no_scope_is_user_fixable_and_active_models_not_applicable() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(),
        backend="postgresql",
        schema_selected=False,
        project_selected=False,
        has_active_models=False,
    )
    assert status.mode == "native"
    assert "scope_selected" in status.blocking_factors
    assert status.user_fixable_blocker is True
    # Active models is moot until a scope is chosen — must not double-count as a
    # blocker.
    active = _factor(status, "active_models")
    assert active.state == "not_applicable"
    assert active.blocking is False


def test_scope_selected_but_no_active_models_blocks_user_fixable() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(),
        backend="postgresql",
        schema_selected=True,
        project_selected=False,
        has_active_models=False,
    )
    assert status.mode == "native"
    assert "active_models" in status.blocking_factors
    assert status.user_fixable_blocker is True


def test_passthrough_engine_blocks() -> None:
    config = AgentConfig(
        wren_enabled=True,
        wren_engine="passthrough",
        wren_semantic_sql_enabled=True,
    )
    status = evaluate_semantic_factors(
        config=config,
        engine=PassthroughEngine(),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "native"
    assert "engine_wren_core" in status.blocking_factors
    assert guidance_enabled(config, PassthroughEngine()) is False


def test_engine_not_installed_blocks() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(name="wren_core", available=False),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "native"
    assert "engine_installed" in status.blocking_factors


def test_runtime_context_false_blocks_after_query() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
        context_loaded=False,
    )
    # Deterministic factors all met, but a known failed runtime load downgrades.
    assert status.mode == "native"
    assert "context_loaded" in status.blocking_factors


def test_always_eight_factors_in_stable_order() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert [f.key for f in status.factors] == [
        "semantic_sql_enabled",
        "engine_wren_core",
        "engine_installed",
        "dialect_supported",
        "wren_enabled",
        "scope_selected",
        "active_models",
        "context_loaded",
    ]


def test_oracle_discloses_dialect_finalization() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(name="wren_core", available=True),
        backend="oracle",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "semantic"
    assert status.dialect_finalized_by == "oracle"
    assert "transpil" in _factor(status, "dialect_supported").detail.lower()


def test_native_backend_has_no_finalization_disclosure() -> None:
    status = evaluate_semantic_factors(
        config=_enabled_config(),
        engine=_FakeEngine(name="wren_core", available=True),
        backend="postgresql",
        schema_selected=True,
        project_selected=True,
        has_active_models=True,
    )
    assert status.mode == "semantic"
    assert status.dialect_finalized_by is None
