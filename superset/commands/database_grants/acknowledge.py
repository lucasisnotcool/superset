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
from datetime import datetime
from typing import Any

from superset.commands.base import BaseCommand
from superset.extensions import db
from superset.models.database_grant import DatabaseUserGrant
from superset.utils.decorators import transaction

logger = logging.getLogger(__name__)


class AcknowledgeDatabaseGrantsCommand(BaseCommand):
    """Mark the caller's own grants as acknowledged (dismisses the dialog).

    Self-scoped by construction: only rows claimed by this user are touched;
    foreign or unknown ids are ignored rather than treated as errors, so the
    dialog's dismiss action is safely idempotent.
    """

    def __init__(self, user: Any, grant_ids: list[int]):
        self._user = user
        self._grant_ids = grant_ids

    @transaction()
    def run(self) -> int:
        self.validate()
        if not self._grant_ids:
            return 0
        grants = (
            db.session.query(DatabaseUserGrant)
            .filter(
                DatabaseUserGrant.id.in_(self._grant_ids),
                DatabaseUserGrant.user_id == self._user.id,
                DatabaseUserGrant.acknowledged_at.is_(None),
            )
            .all()
        )
        now = datetime.now()
        for grant in grants:
            grant.acknowledged_at = now
        return len(grants)

    def validate(self) -> None:
        pass
