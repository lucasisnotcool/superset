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

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.session import Session

from superset.models.database_grant import (
    DatabaseUserGrant,
    grant_role_name,
    normalize_grant_username,
)


def test_grant_role_name_is_id_keyed() -> None:
    # Id-based so the role survives database renames.
    assert grant_role_name(42) == "db_grant_42"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Alice@Example.COM", "alice@example.com"),
        ("  bob@corp.io \n", "bob@corp.io"),
        ("plainuser", "plainuser"),
    ],
)
def test_normalize_grant_username(raw: str, expected: str) -> None:
    assert normalize_grant_username(raw) == expected


def _make_database(session: Session) -> "Database":  # noqa: F821
    from superset.models.core import Database

    database = Database(database_name="granted_db", sqlalchemy_uri="sqlite://")
    session.add(database)
    session.flush()
    return database


def test_status_lifecycle(session: Session) -> None:
    from superset.models.core import Database

    Database.metadata.create_all(session.get_bind())
    database = _make_database(session)

    grant = DatabaseUserGrant(database_id=database.id, username="alice@example.com")
    session.add(grant)
    session.flush()

    assert grant.status == "pending"
    assert grant.uuid is not None

    grant.user_id = 1
    grant.claimed_at = datetime(2026, 7, 2, 12, 0, 0)
    assert grant.status == "claimed"

    grant.acknowledged_at = datetime(2026, 7, 2, 12, 5, 0)
    assert grant.status == "acknowledged"


def test_duplicate_grant_for_same_db_and_username_rejected(session: Session) -> None:
    from superset.models.core import Database

    Database.metadata.create_all(session.get_bind())
    database = _make_database(session)

    session.add(
        DatabaseUserGrant(database_id=database.id, username="alice@example.com")
    )
    session.flush()

    session.add(
        DatabaseUserGrant(database_id=database.id, username="alice@example.com")
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_same_username_across_databases_allowed(session: Session) -> None:
    from superset.models.core import Database

    Database.metadata.create_all(session.get_bind())
    db_one = _make_database(session)
    db_two = Database(database_name="other_db", sqlalchemy_uri="sqlite://")
    session.add(db_two)
    session.flush()

    session.add_all(
        [
            DatabaseUserGrant(database_id=db_one.id, username="alice@example.com"),
            DatabaseUserGrant(database_id=db_two.id, username="alice@example.com"),
        ]
    )
    session.flush()

    grants = session.query(DatabaseUserGrant).all()
    assert {grant.database_id for grant in grants} == {db_one.id, db_two.id}
