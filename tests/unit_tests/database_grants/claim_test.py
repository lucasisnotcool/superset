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

from pytest_mock import MockerFixture
from sqlalchemy.orm.session import Session

from superset.commands.database_grants.claim import (
    claim_database_grants,
    ensure_grant_role,
    grant_candidates,
)
from superset.models.database_grant import DatabaseUserGrant


def _setup(session: Session) -> tuple[Any, Any]:
    """Create the schema plus one database and one user."""
    from flask_appbuilder.security.sqla.models import User

    from superset.models.core import Database

    Database.metadata.create_all(session.get_bind())

    database = Database(database_name="granted_db", sqlalchemy_uri="sqlite://")
    user = User(
        first_name="Alice",
        last_name="Doe",
        username="alice@example.com",
        email="alice@example.com",
        active=True,
    )
    session.add_all([database, user])
    session.flush()
    return database, user


def _grant(session: Session, database: Any, username: str) -> DatabaseUserGrant:
    grant = DatabaseUserGrant(database_id=database.id, username=username)
    session.add(grant)
    session.flush()
    return grant


def test_grant_candidates_uses_username_and_email() -> None:
    class FakeUser:
        username = "Alice@Example.com "
        email = "alias@corp.io"

    assert grant_candidates(FakeUser()) == {"alice@example.com", "alias@corp.io"}


def test_claim_attaches_role_with_single_database_access_pvm(
    session: Session,
) -> None:
    database, user = _setup(session)
    grant = _grant(session, database, "alice@example.com")

    claimed = claim_database_grants(user, session=session)

    assert [g.id for g in claimed] == [grant.id]
    assert grant.user_id == user.id
    assert grant.claimed_at is not None
    assert grant.status == "claimed"

    assert len(user.roles) == 1
    role = user.roles[0]
    assert role.name == f"db_grant_{database.id}"
    assert len(role.permissions) == 1
    pvm = role.permissions[0]
    assert pvm.permission.name == "database_access"
    assert pvm.view_menu.name == f"[granted_db].(id:{database.id})"


def test_claim_is_idempotent(session: Session) -> None:
    database, user = _setup(session)
    _grant(session, database, "alice@example.com")

    claim_database_grants(user, session=session)
    first_claimed_at = (
        session.query(DatabaseUserGrant).one().claimed_at  # noqa: F841
    )
    claim_database_grants(user, session=session)

    grant = session.query(DatabaseUserGrant).one()
    assert grant.claimed_at == first_claimed_at
    assert len(user.roles) == 1
    assert len(user.roles[0].permissions) == 1

    from superset import security_manager

    roles = session.query(security_manager.role_model).all()
    assert len(roles) == 1


def test_claim_reattaches_role_after_idp_roles_sync_wipe(session: Session) -> None:
    """AUTH_ROLES_SYNC_AT_LOGIN overwrites user.roles; the next claim heals it."""
    database, user = _setup(session)
    _grant(session, database, "alice@example.com")

    claim_database_grants(user, session=session)
    original_claimed_at = session.query(DatabaseUserGrant).one().claimed_at

    user.roles = []  # what an IdP roles sync does
    session.flush()

    claim_database_grants(user, session=session)
    assert [role.name for role in user.roles] == [f"db_grant_{database.id}"]
    # Already claimed by this user — the stamp is not rewritten.
    assert session.query(DatabaseUserGrant).one().claimed_at == original_claimed_at


def test_claim_matches_email_when_grant_targets_email(session: Session) -> None:
    database, user = _setup(session)
    user.username = "corp\\alice"  # SSO shortname; grant was pasted as email
    session.flush()
    _grant(session, database, "alice@example.com")

    claimed = claim_database_grants(user, session=session)
    assert len(claimed) == 1
    assert user.roles[0].name == f"db_grant_{database.id}"


def test_claim_matches_case_insensitively(session: Session) -> None:
    database, user = _setup(session)
    user.username = "Alice@Example.COM"
    user.email = "Alice@Example.COM"
    session.flush()
    _grant(session, database, "alice@example.com")

    assert len(claim_database_grants(user, session=session)) == 1


def test_claim_ignores_non_matching_grants(session: Session) -> None:
    database, user = _setup(session)
    _grant(session, database, "someone-else@example.com")

    assert claim_database_grants(user, session=session) == []
    assert user.roles == []


def test_claim_without_identity_is_noop(session: Session) -> None:
    class AnonymousUser:
        id = None
        username = ""
        email = None
        roles: list[Any] = []

    assert claim_database_grants(AnonymousUser(), session=session) == []


def test_claim_swallows_errors_and_rolls_back(
    session: Session, mocker: MockerFixture
) -> None:
    database, user = _setup(session)
    _grant(session, database, "alice@example.com")

    mocker.patch(
        "superset.commands.database_grants.claim.ensure_grant_role",
        side_effect=RuntimeError("boom"),
    )
    rollback = mocker.spy(session, "rollback")

    assert claim_database_grants(user, session=session) == []
    assert rollback.called


def test_ensure_grant_role_reuses_existing_pvm(session: Session) -> None:
    """The database_access PVM is created by the database_after_insert
    listener when the connection is saved; the grant role must link to that
    existing PVM rather than mint a duplicate."""
    from superset import security_manager

    database, _ = _setup(session)

    pvm_model = security_manager.permissionview_model
    pre_existing = session.query(pvm_model).count()
    assert pre_existing >= 1  # created by the after_insert listener

    role = ensure_grant_role(database, session)
    assert len(role.permissions) == 1
    assert role.permissions[0].view_menu.name == database.perm
    assert session.query(pvm_model).count() == pre_existing
