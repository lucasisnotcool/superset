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

from types import SimpleNamespace
from typing import Any

from pytest_mock import MockerFixture
from sqlalchemy.orm.session import Session

from superset.commands.database_grants.exceptions import (
    DatabaseGrantDatabaseNotFoundError,
    DatabaseGrantNotFoundError,
)
from superset.models.database_grant import DatabaseUserGrant


def test_post_bulk_creates_grants(
    client: Any, full_api_access: None, mocker: MockerFixture
) -> None:
    command = mocker.patch(
        "superset.database_grants.api.BulkCreateDatabaseGrantsCommand"
    )
    command.return_value.run.return_value = {
        "created": ["alice@example.com"],
        "skipped": [],
        "claimed_usernames": [],
    }

    response = client.post(
        "/api/v1/database_grant/",
        json={"database_id": 1, "usernames": ["Alice@Example.com"]},
    )

    assert response.status_code == 201
    assert response.json["result"]["created"] == ["alice@example.com"]
    command.assert_called_once_with(1, ["Alice@Example.com"])


def test_post_missing_usernames_is_400(client: Any, full_api_access: None) -> None:
    response = client.post("/api/v1/database_grant/", json={"database_id": 1})
    assert response.status_code == 400


def test_post_unknown_database_is_422(
    client: Any, full_api_access: None, mocker: MockerFixture
) -> None:
    command = mocker.patch(
        "superset.database_grants.api.BulkCreateDatabaseGrantsCommand"
    )
    command.return_value.run.side_effect = DatabaseGrantDatabaseNotFoundError()

    response = client.post(
        "/api/v1/database_grant/",
        json={"database_id": 999, "usernames": ["a@b.c"]},
    )
    assert response.status_code == 422


def test_delete_revokes_grant(
    client: Any, full_api_access: None, mocker: MockerFixture
) -> None:
    command = mocker.patch("superset.database_grants.api.RevokeDatabaseGrantCommand")

    response = client.delete("/api/v1/database_grant/42")

    assert response.status_code == 200
    command.assert_called_once_with(42)


def test_delete_missing_grant_is_404(
    client: Any, full_api_access: None, mocker: MockerFixture
) -> None:
    command = mocker.patch("superset.database_grants.api.RevokeDatabaseGrantCommand")
    command.return_value.run.side_effect = DatabaseGrantNotFoundError()

    assert client.delete("/api/v1/database_grant/42").status_code == 404


def test_bulk_delete_revokes_each_grant(
    client: Any, full_api_access: None, mocker: MockerFixture
) -> None:
    command = mocker.patch("superset.database_grants.api.RevokeDatabaseGrantCommand")

    response = client.delete("/api/v1/database_grant/?q=!(1,2,3)")

    assert response.status_code == 200
    assert command.call_count == 3


def _setup_user_with_grant(session: Session) -> tuple[Any, Any]:
    """Schema + one database + one user with a PENDING grant for them."""
    from flask_appbuilder.security.sqla.models import User

    from superset.models.core import Database

    Database.metadata.create_all(session.get_bind())
    database = Database(database_name="warehouse_conn")
    database.set_sqlalchemy_uri("postgresql://bob:sekrit@dbhost:5432/warehouse")
    user = User(
        first_name="Alice",
        last_name="Doe",
        username="alice@example.com",
        email="alice@example.com",
        active=True,
    )
    session.add_all([database, user])
    session.flush()
    session.add(
        DatabaseUserGrant(database_id=database.id, username="alice@example.com")
    )
    session.flush()
    return database, user


def test_mine_claims_lazily_and_returns_signature_without_password(
    client: Any,
    full_api_access: None,
    session: Session,
    mocker: MockerFixture,
) -> None:
    database, user = _setup_user_with_grant(session)
    mocker.patch("superset.database_grants.api.g", SimpleNamespace(user=user))

    response = client.get("/api/v1/database_grant/mine")

    assert response.status_code == 200
    assert response.json["count"] == 1
    (payload,) = response.json["result"]
    assert payload["database_name"] == "warehouse_conn"
    assert payload["host"] == "dbhost"
    assert payload["port"] == 5432
    assert payload["database"] == "warehouse"
    assert payload["connection_username"] == "bob"
    assert payload["backend"] == "postgresql"
    # The pending grant was claimed by the request itself (T3).
    grant = session.query(DatabaseUserGrant).one()
    assert grant.status == "claimed"
    assert [role.name for role in user.roles] == [f"db_grant_{database.id}"]
    # The password never appears anywhere in the response.
    assert b"sekrit" not in response.data


def test_mine_excludes_acknowledged_grants(
    client: Any,
    full_api_access: None,
    session: Session,
    mocker: MockerFixture,
) -> None:
    _, user = _setup_user_with_grant(session)
    mocker.patch("superset.database_grants.api.g", SimpleNamespace(user=user))

    # First call claims; acknowledge, then the dialog feed must be empty.
    client.get("/api/v1/database_grant/mine")
    grant = session.query(DatabaseUserGrant).one()
    client.post("/api/v1/database_grant/acknowledge", json={"ids": [grant.id]})

    response = client.get("/api/v1/database_grant/mine")
    assert response.status_code == 200
    assert response.json["count"] == 0


def test_acknowledge_is_self_scoped(
    client: Any,
    full_api_access: None,
    session: Session,
    mocker: MockerFixture,
) -> None:
    from flask_appbuilder.security.sqla.models import User

    database, user = _setup_user_with_grant(session)
    other = User(
        first_name="Bob",
        last_name="Doe",
        username="bob@corp.io",
        email="bob@corp.io",
        active=True,
    )
    session.add(other)
    session.flush()
    other_grant = DatabaseUserGrant(
        database_id=database.id,
        username="bob@corp.io",
        user_id=other.id,
    )
    session.add(other_grant)
    session.flush()

    mocker.patch("superset.database_grants.api.g", SimpleNamespace(user=user))
    client.get("/api/v1/database_grant/mine")  # claims alice's grant

    response = client.post(
        "/api/v1/database_grant/acknowledge",
        json={"ids": [g.id for g in session.query(DatabaseUserGrant).all()]},
    )

    assert response.status_code == 200
    assert response.json["acknowledged"] == 1  # only alice's own grant
    assert other_grant.acknowledged_at is None


def test_acknowledge_requires_ids(client: Any, full_api_access: None) -> None:
    response = client.post("/api/v1/database_grant/acknowledge", json={})
    assert response.status_code == 400
