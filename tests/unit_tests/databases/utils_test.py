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

from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm.session import Session

from superset.databases.utils import get_table_metadata, make_url_safe
from superset.sql.parse import Table


def test_make_url_safe_string(session: Session) -> None:
    """
    Test converting a string to a safe uri
    """
    uri_string = "postgresql+psycopg2://superset:***@127.0.0.1:5432/superset"
    uri_safe = make_url_safe(uri_string)
    assert str(uri_safe) == uri_string
    assert uri_safe == make_url(uri_string)


def test_make_url_safe_url(session: Session) -> None:
    """
    Test converting a url to a safe uri
    """
    uri = make_url("postgresql+psycopg2://superset:***@127.0.0.1:5432/superset")
    uri_safe = make_url_safe(uri)
    assert uri_safe == uri


def _mock_database() -> MagicMock:
    """A database whose introspection calls all succeed by default."""
    database = MagicMock()
    database.get_columns.return_value = [
        {"column_name": "id", "type": "INTEGER", "comment": None},
    ]
    database.get_pk_constraint.return_value = {
        "constrained_columns": ["id"],
        "name": "pk",
    }
    database.get_foreign_keys.return_value = []
    database.get_indexes.return_value = []
    database.get_table_comment.return_value = None
    database.select_star.return_value = "SELECT * FROM t"
    return database


def test_get_table_metadata_happy_path() -> None:
    """All introspection succeeds: full payload, no warnings."""
    database = _mock_database()
    metadata = get_table_metadata(database, Table("t", "main"))
    assert metadata["name"] == "t"
    assert [col["name"] for col in metadata["columns"]] == ["id"]
    # the primary key is wired onto the column
    assert metadata["columns"][0]["keys"][0]["type"] == "pk"
    assert metadata["selectStar"] == "SELECT * FROM t"
    assert metadata["warnings"] == []


def test_get_table_metadata_degrades_when_select_star_fails() -> None:
    """A failing SELECT * is reported as a warning, not a hard error."""
    database = _mock_database()
    database.select_star.side_effect = RuntimeError("boom")
    metadata = get_table_metadata(database, Table("t", "main"))
    # columns still present; select star degraded to empty + a warning
    assert [col["name"] for col in metadata["columns"]] == ["id"]
    assert metadata["selectStar"] == ""
    assert any("SELECT *" in warning for warning in metadata["warnings"])


def test_get_table_metadata_degrades_when_foreign_keys_fail() -> None:
    """A failing foreign-key reflection degrades to empty + a warning."""
    database = _mock_database()
    database.get_foreign_keys.side_effect = RuntimeError("boom")
    metadata = get_table_metadata(database, Table("t", "main"))
    assert metadata["foreignKeys"] == []
    assert any("foreign keys" in warning for warning in metadata["warnings"])


def test_get_table_metadata_degrades_when_primary_key_fails() -> None:
    """A failing primary-key reflection degrades without raising."""
    database = _mock_database()
    database.get_pk_constraint.side_effect = RuntimeError("boom")
    metadata = get_table_metadata(database, Table("t", "main"))
    assert any("primary key" in warning for warning in metadata["warnings"])
    # column still rendered, just without the pk badge
    assert metadata["columns"][0]["keys"] == []


def test_get_table_metadata_degrades_when_table_comment_fails() -> None:
    """A failing table-comment fetch degrades to a warning, not a hard error.

    This is the path that produced the observed 500: a transient
    ``RuntimeError`` from concurrent introspection inside ``get_table_comment``.
    """
    database = _mock_database()
    database.get_table_comment.side_effect = RuntimeError(
        "deque mutated during iteration"
    )
    metadata = get_table_metadata(database, Table("t", "main"))
    assert metadata["comment"] is None
    assert [col["name"] for col in metadata["columns"]] == ["id"]
    assert any("table comment" in warning for warning in metadata["warnings"])


def test_get_table_metadata_propagates_columns_failure() -> None:
    """Column reflection is essential — its failure propagates (becomes a 422
    via @handle_api_exception on the route) rather than returning empty."""
    database = _mock_database()
    database.get_columns.side_effect = RuntimeError("unknown database seagate")
    with pytest.raises(RuntimeError):
        get_table_metadata(database, Table("t", "seagate"))
