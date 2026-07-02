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
import logging
from typing import Any, Optional

from sqlalchemy import func, or_

from superset.commands.base import BaseCommand
from superset.commands.database_grants.claim import claim_database_grants
from superset.commands.database_grants.exceptions import (
    DatabaseGrantDatabaseNotFoundError,
    DatabaseGrantNoUsernamesError,
    DatabaseGrantTooManyUsernamesError,
)
from superset.daos.database import DatabaseDAO
from superset.extensions import db
from superset.models.database_grant import DatabaseUserGrant, normalize_grant_username
from superset.utils.decorators import transaction

logger = logging.getLogger(__name__)

MAX_USERNAMES_PER_REQUEST = 500


class BulkCreateDatabaseGrantsCommand(BaseCommand):
    """Pre-approve a pasted list of usernames for one database.

    Duplicate usernames (already granted for the same database) are skipped
    and reported, not errors, so admins can re-paste an updated list. Grants
    whose username already matches an existing account are claimed
    immediately, so those users gain access without waiting for a login.
    """

    def __init__(self, database_id: int, usernames: list[str]):
        self._database_id = database_id
        self._raw_usernames = usernames
        self._usernames: list[str] = []
        self._database: Optional[Any] = None

    @transaction()
    def run(self) -> dict[str, Any]:
        self.validate()

        existing = {
            grant.username
            for grant in db.session.query(DatabaseUserGrant.username)
            .filter(DatabaseUserGrant.database_id == self._database_id)
            .all()
        }

        created: list[str] = []
        skipped: list[str] = []
        for username in self._usernames:
            if username in existing:
                skipped.append(username)
                continue
            db.session.add(
                DatabaseUserGrant(database_id=self._database_id, username=username)
            )
            existing.add(username)
            created.append(username)
        db.session.flush()

        claimed = self._claim_for_existing_users(created)
        return {
            "created": created,
            "skipped": skipped,
            "claimed_usernames": claimed,
        }

    def validate(self) -> None:
        normalized = []
        seen = set()
        for raw in self._raw_usernames:
            username = normalize_grant_username(raw or "")
            if username and username not in seen:
                seen.add(username)
                normalized.append(username)
        if not normalized:
            raise DatabaseGrantNoUsernamesError()
        if len(normalized) > MAX_USERNAMES_PER_REQUEST:
            raise DatabaseGrantTooManyUsernamesError()
        self._usernames = normalized

        # DatabaseDAO applies the owner-scoped DatabaseFilter, so even if the
        # route-level Admin gate were misconfigured, a caller could only grant
        # access to connections they can already see.
        self._database = DatabaseDAO.find_by_id(self._database_id)
        if self._database is None:
            raise DatabaseGrantDatabaseNotFoundError()

    def _claim_for_existing_users(self, created: list[str]) -> list[str]:
        """Immediately claim fresh grants whose user already has an account."""
        if not created:
            return []
        # pylint: disable=import-outside-toplevel
        from superset import security_manager

        user_model = security_manager.user_model
        users = (
            db.session.query(user_model)
            .filter(
                or_(
                    func.lower(user_model.username).in_(created),
                    func.lower(user_model.email).in_(created),
                )
            )
            .all()
        )
        claimed: list[str] = []
        for user in users:
            grants = claim_database_grants(user, session=db.session, commit=False)
            if any(grant.database_id == self._database_id for grant in grants):
                claimed.append(user.username)
        return claimed
