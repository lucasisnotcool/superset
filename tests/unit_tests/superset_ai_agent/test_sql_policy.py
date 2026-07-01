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

"""Determinism contract for the AI-agent SQL safety policy (R7)."""

from __future__ import annotations

import pytest

from superset_ai_agent.tools.sql_policy import (
    apply_limit,
    classify_sql,
    decide,
    ExecutionTier,
    SqlClassification,
    SqlClassificationKind,
)

# (engine, sql, expected_kind). Ground truth verified against
# superset.sql.parse.SQLScript — see the proposal's Phase 1 verification.
CLASSIFY_CASES = [
    # --- read-only happy paths -------------------------------------------
    ("postgresql", "SELECT a FROM t", "read_only"),
    ("postgresql", "select a from t", "read_only"),
    ("postgresql", "SELECT 1 UNION SELECT 2", "read_only"),
    ("postgresql", "WITH x AS (SELECT 1 AS a) SELECT a FROM x", "read_only"),
    ("postgresql", "SELECT a FROM t WHERE b IN (SELECT c FROM d)", "read_only"),
    ("sqlite", "SELECT name FROM birth_names", "read_only"),
    # False positives the old keyword regex wrongly rejected — now read-only.
    ("postgresql", "SELECT a FROM t WHERE note = 'please DROP by'", "read_only"),
    ("postgresql", "SELECT update_ts, create_date FROM events", "read_only"),
    # --- mutating (caught by core's is_mutating) -------------------------
    ("postgresql", "DELETE FROM t", "mutating"),
    ("postgresql", "UPDATE t SET a = 1", "mutating"),
    ("postgresql", "INSERT INTO t VALUES (1)", "mutating"),
    ("sqlite", "DROP TABLE birth_names", "mutating"),
    ("postgresql", "COPY t TO '/tmp/x'", "mutating"),
    ("postgresql", "GRANT SELECT ON t TO bob", "mutating"),
    ("postgresql", "COMMENT ON TABLE t IS 'x'", "mutating"),
    # Read-only-looking statements with write side effects.
    ("postgresql", "SELECT lo_export(1, '/tmp/x')", "mutating"),
    ("postgresql", "SELECT setval('s', 10)", "mutating"),
    ("postgresql", "SELECT nextval('s')", "mutating"),
    # Data-modifying CTE — root is Select but body mutates.
    (
        "postgresql",
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
        "mutating",
    ),
    # EXPLAIN ANALYZE wrapping a DML body.
    ("postgresql", "EXPLAIN ANALYZE DELETE FROM t", "mutating"),
    # --- opaque (fail closed) --------------------------------------------
    # Structured SET mutates session state but core does NOT flag it; the
    # fail-closed root allowlist blocks it anyway (defense in depth).
    ("postgresql", "SET search_path = public", "opaque"),
    ("postgresql", "SHOW server_version", "opaque"),
    # CALL is both opaque and mutating; mutation wins for a precise reason.
    ("postgresql", "CALL do_stuff()", "mutating"),
    # --- read-only introspection allowlist (EXPLAIN/DESCRIBE) -------------
    ("postgresql", "EXPLAIN SELECT a FROM t", "read_only"),
    ("sqlite", "EXPLAIN SELECT a FROM t", "read_only"),
    ("trino", "EXPLAIN SELECT a FROM t", "read_only"),
    ("postgresql", "EXPLAIN WITH x AS (SELECT 1) SELECT * FROM x", "read_only"),
    ("mysql", "EXPLAIN SELECT a FROM t", "read_only"),
    ("mysql", "DESCRIBE birth_names", "read_only"),
    ("mysql", "DESC birth_names", "read_only"),
    # EXPLAIN ANALYZE executes the statement — must NOT be auto-runnable.
    ("postgresql", "EXPLAIN ANALYZE SELECT a FROM t", "opaque"),
    ("postgresql", "EXPLAIN ANALYZE DELETE FROM t", "mutating"),
    # EXPLAIN of a write stays blocked (body re-classified, not the wrapper).
    ("postgresql", "EXPLAIN DELETE FROM t", "opaque"),
    ("postgresql", "EXPLAIN INSERT INTO t VALUES (1)", "opaque"),
    # EXPLAIN with an option list we cannot re-parse stays blocked (conservative).
    ("postgresql", "EXPLAIN (FORMAT JSON) SELECT a FROM t", "opaque"),
    # --- multi -----------------------------------------------------------
    ("postgresql", "SELECT 1; SELECT 2", "multi"),
    ("sqlite", "SELECT 1; DROP TABLE t", "multi"),
    # --- unparseable / empty ---------------------------------------------
    ("postgresql", "", "unparseable"),
    ("postgresql", "   ;  ", "unparseable"),
    ("mysql", "SELECT * FROM t INTO OUTFILE '/tmp/x'", "unparseable"),
    ("postgresql", "SELECT FROM WHERE", "unparseable"),
]


@pytest.mark.parametrize("engine,sql,expected", CLASSIFY_CASES)
def test_classify_sql(engine: str, sql: str, expected: str) -> None:
    result = classify_sql(sql, engine=engine)
    assert result.kind == expected, f"{sql!r} -> {result.kind} ({result.reason})"
    assert result.is_read_only == (expected == "read_only")
    assert result.reason  # every verdict is explainable (R3)


def test_read_only_introspection_is_runnable_with_distinct_reason() -> None:
    # EXPLAIN of a read-only body classifies read-only and therefore auto-runs,
    # with a reason that distinguishes it from a normal query.
    result = classify_sql("EXPLAIN SELECT a FROM t", engine="postgresql")
    assert result.kind == "read_only"
    assert "introspection" in result.reason.lower()
    assert decide(result, tier="read_only").allow is True


def test_strict_mode_blocks_multi_statement_read_only_scripts() -> None:
    # Default (strict) mode: any multi-statement script is blocked, even when
    # every statement is read-only.
    assert classify_sql("SELECT 1; SELECT 2", engine="postgresql").kind == "multi"
    explicit = classify_sql(
        "SELECT 1; SELECT 2", engine="postgresql", policy_mode="strict"
    )
    assert explicit.kind == "multi"


def test_permissive_mode_allows_all_read_only_multi_statement_script() -> None:
    result = classify_sql(
        "SELECT 1; SELECT 2 FROM t", engine="postgresql", policy_mode="permissive"
    )
    assert result.kind == "read_only"
    assert "permissive" in result.reason.lower()
    assert decide(result, tier="read_only").allow is True


def test_permissive_mode_still_blocks_multi_script_with_a_write() -> None:
    # A single mutating/opaque statement anywhere keeps the whole script blocked,
    # so permissive can never let a write auto-run (preserves R5).
    assert (
        classify_sql(
            "SELECT 1; DROP TABLE t", engine="postgresql", policy_mode="permissive"
        ).kind
        == "multi"
    )
    assert (
        classify_sql(
            "SELECT 1; UPDATE t SET a = 1",
            engine="postgresql",
            policy_mode="permissive",
        ).kind
        == "multi"
    )
    assert (
        classify_sql(
            "SELECT 1; CALL proc()", engine="postgresql", policy_mode="permissive"
        ).kind
        == "multi"
    )


def test_permissive_mode_does_not_change_single_statement_behaviour() -> None:
    # The knob only ever relaxes the multi case.
    drop = classify_sql("DROP TABLE t", engine="postgresql", policy_mode="permissive")
    assert drop.kind == "mutating"
    select = classify_sql(
        "SELECT a FROM t", engine="postgresql", policy_mode="permissive"
    )
    assert select.kind == "read_only"


def test_classify_unknown_engine_falls_back_and_still_classifies() -> None:
    # Unknown engine must not crash; falls back to base dialect (R2).
    assert classify_sql("SELECT 1", engine="not_a_real_engine").kind == "read_only"
    assert classify_sql("DROP TABLE t", engine=None).kind == "mutating"


# --- decide() policy matrix: every class x every tier --------------------
TIERS: tuple[ExecutionTier, ...] = ("manual", "read_only", "auto")
NON_READ_ONLY: tuple[SqlClassificationKind, ...] = (
    "mutating",
    "opaque",
    "multi",
    "unparseable",
)


@pytest.mark.parametrize("tier", TIERS)
def test_decide_read_only(tier: ExecutionTier) -> None:
    cls = SqlClassification("read_only", "Read-only query.")
    decision = decide(cls, tier=tier)
    # Only read_only/auto auto-run; manual waits for approval.
    assert decision.allow is (tier in ("read_only", "auto"))


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("kind", NON_READ_ONLY)
def test_decide_blocks_non_read_only_in_every_tier(
    kind: SqlClassificationKind, tier: ExecutionTier
) -> None:
    cls = SqlClassification(kind, "blocked reason")
    assert decide(cls, tier=tier).allow is False
    # Approval never promotes a non-read-only statement (R4).
    assert decide(cls, tier=tier, approved=True).allow is False


def test_decide_approval_runs_read_only_in_manual_mode() -> None:
    cls = SqlClassification("read_only", "Read-only query.")
    assert decide(cls, tier="manual", approved=True).allow is True


@pytest.mark.parametrize("kind", NON_READ_ONLY)
def test_auto_tier_never_executes_non_read_only(kind: SqlClassificationKind) -> None:
    # R5: pin that `auto` can never execute a mutating/opaque statement, so a
    # future autopilot change cannot silently drop the read-only invariant.
    cls = SqlClassification(kind, "blocked")
    assert decide(cls, tier="auto").allow is False
    assert decide(cls, tier="auto", approved=True).allow is False


# --- apply_limit ----------------------------------------------------------
def test_apply_limit_appends_when_missing() -> None:
    # Dialect-correct append, rendered from the AST (no stray newline).
    out = apply_limit("SELECT a FROM t", engine="postgresql", default_limit=20)
    assert out == "SELECT a FROM t LIMIT 20"
    assert "\n" not in out


def test_apply_limit_keeps_existing_top_level_limit() -> None:
    assert (
        apply_limit("SELECT a FROM t LIMIT 5", engine="postgresql", default_limit=20)
        == "SELECT a FROM t LIMIT 5"
    )


def test_apply_limit_ignores_subquery_limit_and_caps_outer() -> None:
    # A LIMIT buried in a subquery must NOT suppress the outer cap (§2.3).
    sql = "SELECT a FROM (SELECT a FROM t LIMIT 5) s"
    out = apply_limit(sql, engine="postgresql", default_limit=20)
    assert out == "SELECT a FROM (SELECT a FROM t LIMIT 5) AS s LIMIT 20"


def test_apply_limit_strips_trailing_semicolon() -> None:
    out = apply_limit("SELECT a FROM t;", engine="postgresql", default_limit=20)
    assert out == "SELECT a FROM t LIMIT 20"
    assert ";" not in out


def test_apply_limit_caps_union() -> None:
    out = apply_limit("SELECT 1 UNION SELECT 2", engine="postgresql", default_limit=20)
    assert out == "SELECT 1 UNION SELECT 2 LIMIT 20"


def test_apply_limit_is_dialect_correct_for_oracle() -> None:
    # The ORA-00911 fix: Oracle gets FETCH FIRST, never a bare LIMIT, and no
    # stray newline reaches the driver.
    out = apply_limit("SELECT a FROM t", engine="oracle", default_limit=20)
    assert out == "SELECT a FROM t FETCH FIRST 20 ROWS ONLY"
    assert "LIMIT" not in out.upper()
    assert "\n" not in out


def test_apply_limit_is_dialect_correct_for_tsql() -> None:
    out = apply_limit("SELECT a FROM t", engine="mssql", default_limit=20)
    assert out == "SELECT TOP 20 a FROM t"
    assert "LIMIT" not in out.upper()


def test_apply_limit_strips_trailing_newline_and_semicolon_for_oracle() -> None:
    # Regression for the reported ORA-00911: a trailing newline/semicolon must
    # never reach Oracle.
    out = apply_limit("SELECT a FROM t ;\n", engine="oracle", default_limit=20)
    assert out == "SELECT a FROM t FETCH FIRST 20 ROWS ONLY"
    assert "\n" not in out and ";" not in out


def test_apply_limit_degrades_to_sanitized_sql_when_unparseable() -> None:
    # Unparseable input: no cap guessed, but still sanitized (no trailing junk).
    out = apply_limit("NOT SQL AT ALL ;\n", engine="oracle", default_limit=20)
    assert out == "NOT SQL AT ALL"


def test_sanitize_sql_strips_control_chars_and_trailing_terminators() -> None:
    from superset_ai_agent.tools.sql_policy import sanitize_sql

    assert sanitize_sql("SELECT a\x00 FROM t\x07 ;\n") == "SELECT a FROM t"
    # Internal semicolons (stacked-query separators) are preserved for the
    # multi-statement classifier — only the TRAILING terminator run is removed.
    assert sanitize_sql("SELECT 1; SELECT 2;") == "SELECT 1; SELECT 2"
