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
"""Admin pre-approval of usernames for database access.

A ``DatabaseUserGrant`` row records that an administrator pre-approved a
username for access to a specific database connection. The username is matched
against accounts at login/registration time (identity is trusted because
non-admin accounts are provisioned through SSO, where username == email), and
"claiming" the grant attaches the per-database grant role — carrying the
``database_access`` permission on the database's perm string — to the user, so
they gain the connection and every database-scoped object without entering
credentials.
"""

from __future__ import annotations

import uuid as uuid_module

from flask_appbuilder import Model
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy_utils import UUIDType

from superset.models.helpers import AuditMixinNullable

GRANT_ROLE_PREFIX = "db_grant_"


def grant_role_name(database_id: int) -> str:
    """Name of the role that carries a database's ``database_access`` PVM.

    Keyed by id, not name, so the role stays valid across database renames
    (the perm view-menu string is renamed in place by ``database_after_update``).
    """
    return f"{GRANT_ROLE_PREFIX}{database_id}"


def normalize_grant_username(username: str) -> str:
    """Grants match case-insensitively; store the canonical lowercased form."""
    return username.strip().lower()


class DatabaseUserGrant(AuditMixinNullable, Model):
    """A pre-approved (and possibly claimed) database access grant.

    Lifecycle: pending (``user_id`` null) -> claimed (role attached, stamped at
    login/registration or immediately when the account already exists) ->
    acknowledged (the user dismissed the notification dialog).
    """

    __tablename__ = "database_user_grants"
    __table_args__ = (
        UniqueConstraint(
            "database_id", "username", name="uq_database_user_grants_db_username"
        ),
    )

    id = Column(Integer, primary_key=True)
    uuid = Column(
        UUIDType(binary=True), nullable=False, unique=True, default=uuid_module.uuid4
    )
    database_id = Column(
        Integer, ForeignKey("dbs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stored lowercased/stripped (see normalize_grant_username); matched against
    # both username and email of the signing-in account.
    username = Column(String(255), nullable=False, index=True)
    user_id = Column(
        Integer, ForeignKey("ab_user.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)

    database = relationship("Database", foreign_keys=[database_id])

    @property
    def status(self) -> str:
        if self.acknowledged_at is not None:
            return "acknowledged"
        if self.user_id is not None:
            return "claimed"
        return "pending"

    def __repr__(self) -> str:
        return f"<DatabaseUserGrant {self.username} -> db:{self.database_id}>"
