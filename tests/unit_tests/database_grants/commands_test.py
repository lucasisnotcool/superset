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

from typing import Any

import pytest
from pytest_mock import MockerFixture
from sqlalchemy.orm.session import Session

from superset.commands.database_grants.acknowledge import (
    AcknowledgeDatabaseGrantsCommand,
)
from superset.commands.database_grants.create import BulkCreateDatabaseGrantsCommand
from superset.commands.database_grants.exceptions import (
    DatabaseGrantDatabaseNotFoundError,
    DatabaseGrantNoUsernamesError,
    DatabaseGrantNotFoundError,
    DatabaseGrantTooManyUsernamesError,
)
from superset.commands.database_grants.revoke import RevokeDatabaseGrantCommand
from superset.models.database_grant import DatabaseUserGrant


def _setup(session: Session, mocker: MockerFixture) -> Any:
    """Create the schema and one database; pin the DAO to it."""
    from superset.models.core import Database

    Database.metadata.create_all(session.get_bind())
    database = Database(database_name="granted_db", sqlalchemy_uri="sqlite://")
    session.add(database)
    session.flush()

    # DatabaseDAO.find_by_id applies the request-scoped DatabaseFilter, which
    # needs a live app/user context; the command's DAO usage is pinned here.
    mocker.patch(
        "superset.commands.database_grants.create.DatabaseDAO.find_by_id",
        side_effect=lambda pk: database if pk == database.id else None,
    )
    return database


def _add_user(session: Session, username: str, email: str | None = None) -> Any:
    from flask_appbuilder.security.sqla.models import User

    user = User(
        first_name="F",
        last_name="L",
        username=username,
        email=email or username,
        active=True,
    )
    session.add(user)
    session.flush()
    return user


def test_bulk_create_normalizes_dedupes_and_skips_existing(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)

    result = BulkCreateDatabaseGrantsCommand(
        database.id,
        ["Alice@Example.com", " alice@example.com ", "bob@corp.io", "", "  "],
    ).run()

    assert result["created"] == ["alice@example.com", "bob@corp.io"]
    assert result["skipped"] == []
    assert session.query(DatabaseUserGrant).count() == 2

    # Re-pasting the same list is safe: everything is skipped, nothing dupes.
    rerun = BulkCreateDatabaseGrantsCommand(
        database.id, ["alice@example.com", "BOB@corp.io"]
    ).run()
    assert rerun["created"] == []
    assert sorted(rerun["skipped"]) == ["alice@example.com", "bob@corp.io"]
    assert session.query(DatabaseUserGrant).count() == 2


def test_bulk_create_claims_immediately_for_existing_account(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)
    user = _add_user(session, "alice@example.com")

    result = BulkCreateDatabaseGrantsCommand(
        database.id, ["alice@example.com", "not-registered@corp.io"]
    ).run()

    assert result["claimed_usernames"] == ["alice@example.com"]
    assert [role.name for role in user.roles] == [f"db_grant_{database.id}"]

    grants = {g.username: g for g in session.query(DatabaseUserGrant).all()}
    assert grants["alice@example.com"].status == "claimed"
    assert grants["alice@example.com"].user_id == user.id
    assert grants["not-registered@corp.io"].status == "pending"


def test_bulk_create_rejects_unknown_database(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)

    with pytest.raises(DatabaseGrantDatabaseNotFoundError):
        BulkCreateDatabaseGrantsCommand(database.id + 999, ["alice@x.io"]).run()


def test_bulk_create_rejects_empty_and_oversized_input(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)

    with pytest.raises(DatabaseGrantNoUsernamesError):
        BulkCreateDatabaseGrantsCommand(database.id, ["", "   "]).run()

    with pytest.raises(DatabaseGrantTooManyUsernamesError):
        BulkCreateDatabaseGrantsCommand(
            database.id, [f"user{i}@corp.io" for i in range(501)]
        ).run()


def test_revoke_pending_grant_deletes_row(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)
    BulkCreateDatabaseGrantsCommand(database.id, ["ghost@corp.io"]).run()
    grant_id = session.query(DatabaseUserGrant).one().id

    RevokeDatabaseGrantCommand(grant_id).run()
    assert session.query(DatabaseUserGrant).count() == 0


def test_revoke_claimed_grant_detaches_role_and_deletes_row(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)
    user = _add_user(session, "alice@example.com")
    BulkCreateDatabaseGrantsCommand(database.id, ["alice@example.com"]).run()
    assert len(user.roles) == 1
    grant_id = session.query(DatabaseUserGrant).one().id

    RevokeDatabaseGrantCommand(grant_id).run()

    assert user.roles == []
    assert session.query(DatabaseUserGrant).count() == 0
    # The role row itself survives — other users claimed via their own grants
    # may still hold it.
    from superset import security_manager

    assert (
        session.query(security_manager.role_model)
        .filter_by(name=f"db_grant_{database.id}")
        .count()
        == 1
    )


def test_revoke_missing_grant_raises(session: Session, mocker: MockerFixture) -> None:
    _setup(session, mocker)
    with pytest.raises(DatabaseGrantNotFoundError):
        RevokeDatabaseGrantCommand(12345).run()


def test_acknowledge_is_self_scoped_and_idempotent(
    session: Session, mocker: MockerFixture
) -> None:
    database = _setup(session, mocker)
    alice = _add_user(session, "alice@example.com")
    bob = _add_user(session, "bob@corp.io")
    BulkCreateDatabaseGrantsCommand(
        database.id, ["alice@example.com", "bob@corp.io"]
    ).run()

    grants = {g.username: g for g in session.query(DatabaseUserGrant).all()}
    alice_grant = grants["alice@example.com"]
    bob_grant = grants["bob@corp.io"]

    # Alice tries to acknowledge both her own and Bob's grant: only hers moves.
    count = AcknowledgeDatabaseGrantsCommand(
        alice, [alice_grant.id, bob_grant.id]
    ).run()
    assert count == 1
    assert alice_grant.status == "acknowledged"
    assert bob_grant.status == "claimed"

    # Re-acknowledging is a no-op, not an error.
    assert AcknowledgeDatabaseGrantsCommand(alice, [alice_grant.id]).run() == 0

    # Unknown ids are ignored.
    assert AcknowledgeDatabaseGrantsCommand(alice, [99999]).run() == 0
