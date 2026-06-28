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

import pytest

from superset_ai_agent.integrations.superset.client import ColumnSummary
from superset_ai_agent.semantic_layer.column_identity import (
    physical_column_reference,
    resolve_column_type,
    safe_identifier,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("created_at", "created_at"),  # already valid → unchanged
        ("2003", "_2003"),  # leading digit → prefixed
        ("% growth", "growth"),  # punctuation collapses, edges stripped
        ("col-name", "col_name"),  # hyphen → underscore
        ("net sales 2024", "net_sales_2024"),
        ("***", "unnamed"),  # pure punctuation → placeholder, never empty
    ],
)
def test_safe_identifier(raw: str, expected: str) -> None:
    assert safe_identifier(raw) == expected


def test_physical_column_reference_quotes_and_escapes() -> None:
    assert physical_column_reference("2003") == '"2003"'
    assert physical_column_reference("% growth") == '"% growth"'
    # Embedded double quotes are doubled (SQL-style) so the expression stays valid.
    assert physical_column_reference('weird"name') == '"weird""name"'


def test_resolve_type_prefers_real_catalog_type() -> None:
    col = ColumnSummary(name="x", type="BIGINT", type_generic="NUMERIC")
    assert resolve_column_type(col) == ("BIGINT", False)


def test_resolve_type_falls_back_to_generic_family() -> None:
    # Typeless column, but the catalog knows its generic family → concrete type,
    # flagged inferred so the seed builder can tag it.
    assert resolve_column_type(
        ColumnSummary(name="n", type=None, type_generic="NUMERIC")
    ) == ("DOUBLE", True)
    assert resolve_column_type(
        ColumnSummary(name="s", type=None, type_generic="STRING")
    ) == ("VARCHAR", True)
    assert resolve_column_type(
        ColumnSummary(name="t", type=None, type_generic="TEMPORAL")
    ) == ("TIMESTAMP", True)
    assert resolve_column_type(
        ColumnSummary(name="b", type=None, type_generic="BOOLEAN")
    ) == ("BOOLEAN", True)


def test_resolve_type_falls_back_to_dttm_flag() -> None:
    col = ColumnSummary(name="d", type=None, type_generic=None, is_dttm=True)
    assert resolve_column_type(col) == ("TIMESTAMP", True)


def test_resolve_type_unresolved_stays_none_never_guessed() -> None:
    # No type, no generic family, not a datetime → fail-closed (D-B/D-C): the
    # caller leaves the column untyped rather than guessing.
    col = ColumnSummary(name="u", type=None, type_generic=None, is_dttm=False)
    assert resolve_column_type(col) == (None, False)
