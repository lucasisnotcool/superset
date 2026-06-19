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

from __future__ import annotations

from superset_ai_agent.tools.sql import validate_read_only_sql


def test_validate_read_only_sql_accepts_select_and_adds_limit() -> None:
    validation = validate_read_only_sql(
        "select name from birth_names",
        dialect="sqlite",
        default_limit=20,
    )

    assert validation.is_valid is True
    assert validation.is_read_only is True
    assert validation.normalized_sql == "select name from birth_names\nLIMIT 20"


def test_validate_read_only_sql_keeps_existing_limit() -> None:
    validation = validate_read_only_sql(
        "select name from birth_names limit 5",
        dialect="sqlite",
        default_limit=20,
    )

    assert validation.is_valid is True
    assert validation.normalized_sql == "select name from birth_names limit 5"


def test_validate_read_only_sql_blocks_destructive_statement() -> None:
    validation = validate_read_only_sql(
        "drop table birth_names",
        dialect="sqlite",
    )

    assert validation.is_valid is False
    assert validation.is_read_only is False
    assert any("DROP" in error for error in validation.errors)


def test_validate_read_only_sql_blocks_multiple_statements() -> None:
    validation = validate_read_only_sql(
        "select 1; select 2",
        dialect="sqlite",
    )

    assert validation.is_valid is False
    assert "Exactly one SQL statement is allowed." in validation.errors


def test_validate_read_only_sql_maps_postgresql_dialect() -> None:
    validation = validate_read_only_sql(
        "select name from birth_names",
        dialect="postgresql",
        default_limit=20,
    )

    assert validation.is_valid is True
    assert validation.dialect == "postgres"
    assert validation.normalized_sql == "select name from birth_names\nLIMIT 20"


def test_validate_read_only_sql_falls_back_for_unknown_dialect() -> None:
    validation = validate_read_only_sql(
        "select name from birth_names",
        dialect="not_a_sqlglot_dialect",
        default_limit=20,
    )

    assert validation.is_valid is True
    assert validation.dialect == "not_a_sqlglot_dialect"
    assert validation.normalized_sql == "select name from birth_names\nLIMIT 20"
