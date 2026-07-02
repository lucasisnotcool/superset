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
"""Claiming of pre-approved database access grants.

Claiming attaches the per-database grant role (carrying the
``database_access`` PVM on the database's perm string) to a user whose
username or email matches a pending grant. It runs on every successful login
and at registration, and must therefore be:

- **idempotent** — OAuth's ``AUTH_ROLES_SYNC_AT_LOGIN`` overwrites
  ``user.roles`` from the IdP mapping on each login, so grant roles must be
  re-attached every time;
- **fail-soft** — a defect here must never break authentication, so
  ``claim_database_grants`` swallows and logs all errors.

Role/permission rows are manipulated through the plain SQLAlchemy session
(``db.session``, which Flask-AppBuilder shares in production) rather than the
security manager's mutator methods, so the whole claim commits atomically.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from superset.models.database_grant import (
    DatabaseUserGrant,
    grant_role_name,
    normalize_grant_username,
)

logger = logging.getLogger(__name__)

DATABASE_ACCESS_PERMISSION = "database_access"


def grant_candidates(user: Any) -> set[str]:
    """Normalized identity strings a grant username may match for this user.

    Both username and email are candidates: on the SSO deployments this
    feature targets they coincide, and matching both makes admin paste input
    (which may mix the two) forgiving.
    """
    candidates = set()
    for value in (getattr(user, "username", None), getattr(user, "email", None)):
        if value:
            normalized = normalize_grant_username(value)
            if normalized:
                candidates.add(normalized)
    return candidates


def ensure_grant_role(database: Any, session: Session) -> Any:
    """Find-or-create the ``db_grant_<id>`` role holding the database's
    ``database_access`` PVM. Returns the role without committing."""
    # pylint: disable=import-outside-toplevel
    from superset import security_manager

    role_model = security_manager.role_model
    permission_model = security_manager.permission_model
    view_menu_model = security_manager.viewmenu_model
    pvm_model = security_manager.permissionview_model

    role_name = grant_role_name(database.id)
    role = session.query(role_model).filter_by(name=role_name).one_or_none()
    if role is None:
        role = role_model(name=role_name)
        session.add(role)

    permission = (
        session.query(permission_model)
        .filter_by(name=DATABASE_ACCESS_PERMISSION)
        .one_or_none()
    )
    if permission is None:
        permission = permission_model(name=DATABASE_ACCESS_PERMISSION)
        session.add(permission)

    view_menu = (
        session.query(view_menu_model).filter_by(name=database.perm).one_or_none()
    )
    if view_menu is None:
        view_menu = view_menu_model(name=database.perm)
        session.add(view_menu)
    session.flush()

    pvm = (
        session.query(pvm_model)
        .filter_by(permission_id=permission.id, view_menu_id=view_menu.id)
        .one_or_none()
    )
    if pvm is None:
        pvm = pvm_model(permission=permission, view_menu=view_menu)
        session.add(pvm)
        session.flush()

    if pvm not in role.permissions:
        role.permissions.append(pvm)
    return role


def _claim_grant(grant: DatabaseUserGrant, user: Any, session: Session) -> bool:
    """Attach the grant role to the user and stamp the row. Idempotent."""
    database = grant.database
    if database is None:
        # FK cascade should prevent this; skip defensively.
        return False
    role = ensure_grant_role(database, session)
    if role not in user.roles:
        user.roles.append(role)
    if grant.user_id != user.id:
        grant.user_id = user.id
        grant.claimed_at = datetime.now()
    elif grant.claimed_at is None:
        grant.claimed_at = datetime.now()
    return True


def claim_database_grants(
    user: Any,
    session: Optional[Session] = None,
    commit: bool = True,
) -> list[DatabaseUserGrant]:
    """Attach grant roles for every grant matching this user. Never raises.

    Called from the login path (``on_user_login``), from registration
    (``add_user``), and lazily from the ``mine`` API endpoint. Re-running is a
    no-op apart from re-attaching roles stripped by an IdP roles sync.
    """
    # pylint: disable=import-outside-toplevel
    from superset import db

    session = session if session is not None else db.session
    try:
        if getattr(user, "id", None) is None:
            return []
        candidates = grant_candidates(user)
        if not candidates:
            return []

        grants = (
            session.query(DatabaseUserGrant)
            .filter(DatabaseUserGrant.username.in_(candidates))
            .all()
        )
        claimed = [grant for grant in grants if _claim_grant(grant, user, session)]
        if claimed and commit:
            session.commit()
        return claimed
    except Exception:  # pylint: disable=broad-except
        # A claiming defect must never break login/registration.
        logger.exception(
            "Failed to claim database grants for user %r",
            getattr(user, "username", None),
        )
        try:
            session.rollback()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Rollback after failed grant claim also failed")
        return []
