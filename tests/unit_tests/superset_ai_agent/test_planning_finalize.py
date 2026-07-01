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

"""Dialect finalization wired into the shared semantic-planning step."""

from __future__ import annotations

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
)
from superset_ai_agent.semantic_layer.engine.base import PlannedSql
from superset_ai_agent.semantic_layer.engine.planning import plan_semantic_sql_step
from superset_ai_agent.semantic_layer.mdl_compile import compile_manifest

# wren-core-style canonical output: quoted idents + a top-level LIMIT.
_CANONICAL = 'SELECT o."ID" FROM "ORDERS" AS o LIMIT 100'


class _FakeEngine:
    """SemanticEngine stand-in returning fixed canonical SQL."""

    name = "fake"

    def is_available(self) -> bool:
        return True

    def compile(self, mdl_files):
        return compile_manifest(mdl_files)

    def validate(self, manifest, *, deep=False, schema_index=None):
        raise NotImplementedError

    def plan_sql(self, semantic_sql, manifest, *, dialect=None) -> PlannedSql:
        return PlannedSql(native_sql=_CANONICAL, engine=self.name, rewritten=True)


def _context(backend: str) -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="db", backend=backend),
        datasets=[],
    )


def _plan(backend: str, *, finalize_enabled: bool = True):
    return plan_semantic_sql_step(
        _FakeEngine(),
        sql="SELECT id FROM orders",
        context=_context(backend),
        owner_id="o",
        project_id=None,
        mdl_file_store=None,
        finalize_enabled=finalize_enabled,
    )


def test_oracle_backend_finalizes_limit_to_fetch_first() -> None:
    result = _plan("oracle")
    assert "FETCH FIRST 100 ROWS ONLY" in result.native_sql
    assert "LIMIT" not in result.native_sql.upper()
    assert result.finalized_dialect == "oracle"
    # Canonical (pre-finalize) SQL is preserved for audit/debugging.
    assert result.canonical_native_sql == _CANONICAL
    # Finalization is transparent — no correctable warning is raised.
    assert result.correctable_warnings == []


def test_postgres_backend_is_a_noop() -> None:
    result = _plan("postgresql")
    assert result.native_sql == _CANONICAL
    assert result.finalized_dialect is None
    assert result.canonical_native_sql == _CANONICAL


def test_kill_switch_disables_finalization() -> None:
    result = _plan("oracle", finalize_enabled=False)
    assert result.native_sql == _CANONICAL
    assert result.finalized_dialect is None


class _MalformedEngine(_FakeEngine):
    def plan_sql(self, semantic_sql, manifest, *, dialect=None) -> PlannedSql:
        return PlannedSql(
            native_sql="SELECT FROM WHERE )(", engine=self.name, rewritten=True
        )


def test_transpile_gap_surfaces_as_non_correctable_warning() -> None:
    # A transpile failure must reach engine_warnings (-> repair/reflection) but
    # NOT correctable_warnings (a re-draft can't fix a transpiler gap), and the
    # un-transpiled SQL still flows through so it fails loudly at execution.
    result = plan_semantic_sql_step(
        _MalformedEngine(),
        sql="SELECT id FROM orders",
        context=_context("oracle"),
        owner_id="o",
        project_id=None,
        mdl_file_store=None,
    )
    assert result.warnings, "expected a finalize degrade warning"
    assert any("oracle" in w.lower() for w in result.warnings)
    assert result.correctable_warnings == []
    assert result.finalized_dialect is None  # transpile did not apply
