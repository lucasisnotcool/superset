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
from typing import Optional

from superset.commands.base import BaseCommand
from superset.commands.database_grants.exceptions import DatabaseGrantNotFoundError
from superset.extensions import db
from superset.models.database_grant import DatabaseUserGrant, grant_role_name
from superset.utils.decorators import transaction

logger = logging.getLogger(__name__)


class RevokeDatabaseGrantCommand(BaseCommand):
    """Revoke a grant: detach the grant role from the claimed user (if any)
    and delete the row.

    Revocation must go through this command (i.e. the grants panel/API):
    manually stripping the ``db_grant_*`` role in the roles UI is silently
    re-healed by the idempotent claim on the user's next login.
    """

    def __init__(self, grant_id: int):
        self._grant_id = grant_id
        self._grant: Optional[DatabaseUserGrant] = None

    @transaction()
    def run(self) -> None:
        self.validate()
        assert self._grant is not None  # for mypy; validate() raises otherwise
        grant = self._grant

        if grant.user_id is not None:
            self._detach_role(grant)
        db.session.delete(grant)

    def validate(self) -> None:
        self._grant = db.session.get(DatabaseUserGrant, self._grant_id)
        if self._grant is None:
            raise DatabaseGrantNotFoundError()

    @staticmethod
    def _detach_role(grant: DatabaseUserGrant) -> None:
        # pylint: disable=import-outside-toplevel
        from superset import security_manager

        user = db.session.get(security_manager.user_model, grant.user_id)
        if user is None:
            return
        role_name = grant_role_name(grant.database_id)
        for role in list(user.roles):
            if role.name == role_name:
                user.roles.remove(role)
