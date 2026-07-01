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

"""Tests for the dialect-finalization stage (sqlglot transpile of wren output)."""

from __future__ import annotations

from superset_ai_agent.semantic_layer.engine.dialect_finalize import (
    finalize_native_sql,
    needs_finalization,
)

# Shape mirrors what wren-core emits: quoted identifiers + a top-level LIMIT.
_WREN_OUT = 'SELECT o."ID" FROM "ORDERS" AS o ORDER BY o."ID" LIMIT 100'


def test_oracle_limit_becomes_fetch_first() -> None:
    result = finalize_native_sql(_WREN_OUT, backend="oracle")
    assert result.transpiled is True
    assert result.target_dialect == "oracle"
    assert "FETCH FIRST 100 ROWS ONLY" in result.sql
    assert "LIMIT" not in result.sql.upper()
    assert result.warnings == []


def test_mssql_limit_is_rewritten_too() -> None:
    # The latent bug: mssql was "supported" but wren-core emits LIMIT, which T-SQL
    # rejects. Finalization rewrites it (TOP/OFFSET-FETCH).
    result = finalize_native_sql(_WREN_OUT, backend="mssql")
    assert result.transpiled is True
    assert result.target_dialect == "tsql"
    assert "LIMIT" not in result.sql.upper()


def test_native_backend_is_a_noop() -> None:
    # Postgres is wren-native → not in the map → returned unchanged.
    result = finalize_native_sql(_WREN_OUT, backend="postgresql")
    assert result.transpiled is False
    assert result.target_dialect is None
    assert result.sql == _WREN_OUT
    assert result.warnings == []


def test_unknown_backend_is_a_noop() -> None:
    result = finalize_native_sql(_WREN_OUT, backend="sqlite")
    assert result.transpiled is False
    assert result.sql == _WREN_OUT


def test_disabled_flag_is_a_noop_even_for_oracle() -> None:
    result = finalize_native_sql(_WREN_OUT, backend="oracle", enabled=False)
    assert result.transpiled is False
    assert result.target_dialect is None
    assert result.sql == _WREN_OUT


def test_empty_sql_is_a_noop() -> None:
    for sql in ("", "   "):
        result = finalize_native_sql(sql, backend="oracle")
        assert result.transpiled is False
        assert result.sql == sql


def test_none_backend_is_a_noop() -> None:
    result = finalize_native_sql(_WREN_OUT, backend=None)
    assert result.transpiled is False


def test_malformed_sql_degrades_closed_with_warning() -> None:
    # A transpiler failure must NOT drop the query: return the original + a
    # non-correctable warning naming the target dialect.
    junk = "SELECT FROM WHERE ORDER BY )("
    result = finalize_native_sql(junk, backend="oracle")
    assert result.transpiled is False
    assert result.sql == junk
    assert result.warnings
    assert "oracle" in result.warnings[0].lower()


def test_needs_finalization_lookup() -> None:
    assert needs_finalization("oracle") == "oracle"
    assert needs_finalization("ORACLE") == "oracle"
    assert needs_finalization("mssql") == "tsql"
    assert needs_finalization("postgresql") is None
    assert needs_finalization(None) is None


def test_idempotent_on_already_oracle_sql() -> None:
    # Finalizing already-Oracle SQL must not corrupt it (no LIMIT reintroduced).
    once = finalize_native_sql(_WREN_OUT, backend="oracle").sql
    twice = finalize_native_sql(once, backend="oracle").sql
    assert "FETCH FIRST 100 ROWS ONLY" in twice
    assert "LIMIT" not in twice.upper()


def test_finalization_guidance_for_oracle() -> None:
    from superset_ai_agent.semantic_layer.engine.dialect_finalize import (
        finalization_guidance,
    )

    g = finalization_guidance("oracle")
    assert g is not None
    assert "oracle" in g.lower()
    # Portable-SQL steer for the LLM.
    assert "ANSI-standard" in g or "portable" in g.lower() or "avoid" in g.lower()


def test_finalization_guidance_none_for_native_or_disabled() -> None:
    from superset_ai_agent.semantic_layer.engine.dialect_finalize import (
        finalization_guidance,
    )

    assert finalization_guidance("postgresql") is None
    assert finalization_guidance(None) is None
    assert finalization_guidance("oracle", enabled=False) is None


def test_oracle_uppercases_lowercase_quoted_identifiers() -> None:
    # The ORA-00904 root cause: SQLAlchemy reflects Oracle's stored ID as lowercase,
    # so wren-core emits "id". Finalization must uppercase it to match storage.
    lowered = 'SELECT o."id", o."amount" FROM "orders" AS o LIMIT 10'
    result = finalize_native_sql(lowered, backend="oracle")
    assert '"ID"' in result.sql
    assert '"AMOUNT"' in result.sql
    assert '"ORDERS"' in result.sql
    assert '"id"' not in result.sql
    assert "FETCH FIRST 10 ROWS ONLY" in result.sql


def test_oracle_preserves_reserved_words_quoted_and_mixed_case() -> None:
    # Reserved word stays quoted (uppercased, still safe); genuine mixed-case (which
    # SQLAlchemy never lowercases) is left exactly as authored.
    sql = 'SELECT t."number", t."MixedCase" FROM "t" AS t'
    result = finalize_native_sql(sql, backend="oracle")
    assert '"NUMBER"' in result.sql  # reserved word, still quoted
    assert '"MixedCase"' in result.sql  # untouched


def test_mssql_does_not_uppercase_identifiers() -> None:
    # T-SQL is case-insensitive and SQLAlchemy preserves its case — only the row
    # limit is rewritten (TOP), identifiers keep their lowercase (bracket-quoted).
    lowered = 'SELECT o."id" FROM "orders" AS o LIMIT 10'
    result = finalize_native_sql(lowered, backend="mssql")
    assert "[id]" in result.sql  # lowercase preserved, NOT uppercased
    assert "[ID]" not in result.sql
    assert "LIMIT" not in result.sql.upper()
