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

import pytest
from pytest_mock import MockerFixture

from superset.commands.database.test_connection import TestConnectionDatabaseCommand
from superset.errors import ErrorLevel, SupersetError, SupersetErrorType
from superset.exceptions import OAuth2RedirectError


def test_command(mocker: MockerFixture) -> None:
    """
    Test the happy path of the command.
    """
    user = mocker.MagicMock()
    user.email = "alice@example.org"
    mocker.patch("superset.db_engine_specs.gsheets.g", user=user)
    mocker.patch("superset.db_engine_specs.gsheets.create_engine")

    database = mocker.MagicMock()
    database.db_engine_spec.__name__ = "GSheetsEngineSpec"
    with database.get_sqla_engine() as engine:
        engine.dialect.do_ping.return_value = True

    DatabaseDAO = mocker.patch("superset.commands.database.test_connection.DatabaseDAO")  # noqa: N806
    DatabaseDAO.build_db_for_connection_test.return_value = database

    properties = {
        "sqlalchemy_uri": "gsheets://",
        "engine": "gsheets",
        "driver": "gsheets",
        "catalog": {"test": "https://example.org/"},
    }
    command = TestConnectionDatabaseCommand(properties)
    command.run()


def test_command_with_oauth2(mocker: MockerFixture) -> None:
    """
    Test the command when OAuth2 is needed.
    """
    user = mocker.MagicMock()
    user.email = "alice@example.org"
    mocker.patch("superset.db_engine_specs.gsheets.g", user=user)
    mocker.patch("superset.db_engine_specs.gsheets.create_engine")

    database = mocker.MagicMock()
    database.is_oauth2_enabled.return_value = True
    database.db_engine_spec.needs_oauth2.return_value = True
    database.start_oauth2_dance.side_effect = OAuth2RedirectError(
        "url",
        "tab_id",
        "redirect_uri",
    )
    database.db_engine_spec.__name__ = "GSheetsEngineSpec"
    with database.get_sqla_engine() as engine:
        engine.dialect.do_ping.side_effect = Exception("OAuth2 needed")

    DatabaseDAO = mocker.patch("superset.commands.database.test_connection.DatabaseDAO")  # noqa: N806
    DatabaseDAO.build_db_for_connection_test.return_value = database

    properties = {
        "sqlalchemy_uri": "gsheets://",
        "engine": "gsheets",
        "driver": "gsheets",
        "catalog": {"test": "https://example.org/"},
    }
    command = TestConnectionDatabaseCommand(properties)
    with pytest.raises(OAuth2RedirectError) as excinfo:
        command.run()
    assert excinfo.value.error == SupersetError(
        message="You don't have permission to access the data.",
        error_type=SupersetErrorType.OAUTH2_REDIRECT,
        level=ErrorLevel.WARNING,
        extra={"url": "url", "tab_id": "tab_id", "redirect_uri": "redirect_uri"},
    )


def test_command_does_not_unmask_inaccessible_database(
    mocker: MockerFixture,
) -> None:
    """
    Test that stored secrets are only reused for databases the caller can see.

    The stored model is resolved by *name*; a caller who cannot reach that
    database through the owner-scoped base filter (``find_by_id`` returns
    None) must not have its masked URI substituted with the decrypted one
    (object-level authorization, OWASP API1).
    """
    DatabaseDAO = mocker.patch(  # noqa: N806
        "superset.commands.database.test_connection.DatabaseDAO"
    )
    stored = mocker.MagicMock()
    stored.id = 42
    stored.safe_sqlalchemy_uri.return_value = "postgresql://u:XXXXXXXXXX@host/db"
    stored.sqlalchemy_uri_decrypted = "postgresql://u:real-secret@host/db"
    DatabaseDAO.get_database_by_name.return_value = stored
    # Owner-scoped resolution fails: caller cannot see this database.
    DatabaseDAO.find_by_id.return_value = None

    command = TestConnectionDatabaseCommand(
        {
            "database_name": "victims_db",
            "sqlalchemy_uri": "postgresql://u:XXXXXXXXXX@host/db",
        }
    )

    assert command._model is None
    assert "real-secret" not in command._uri
    assert command._context["password"] == "XXXXXXXXXX"


def test_command_unmasks_accessible_database(mocker: MockerFixture) -> None:
    """
    Test that stored secrets ARE reused when the caller can see the database.
    """
    DatabaseDAO = mocker.patch(  # noqa: N806
        "superset.commands.database.test_connection.DatabaseDAO"
    )
    stored = mocker.MagicMock()
    stored.id = 42
    stored.safe_sqlalchemy_uri.return_value = "postgresql://u:XXXXXXXXXX@host/db"
    stored.sqlalchemy_uri_decrypted = "postgresql://u:real-secret@host/db"
    DatabaseDAO.get_database_by_name.return_value = stored
    # Owner-scoped resolution succeeds: this is the caller's own database.
    DatabaseDAO.find_by_id.return_value = stored

    command = TestConnectionDatabaseCommand(
        {
            "database_name": "my_db",
            "sqlalchemy_uri": "postgresql://u:XXXXXXXXXX@host/db",
        }
    )

    assert command._model is stored
    assert command._context["password"] == "real-secret"
