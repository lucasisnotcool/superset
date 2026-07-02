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
"""Create database_user_grants table

Admin pre-approval of usernames for database access: each row pre-approves a
(lowercased) username for one database connection. Rows are "claimed" at
login/registration by attaching the per-database grant role to the matching
user, and "acknowledged" when the user dismisses the notification dialog.

Revision ID: 5c1a0e6b9d42
Revises: 78a40c08b4be
Create Date: 2026-07-02 10:00:00.000000

"""

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy_utils import UUIDType

from superset.migrations.shared.utils import (
    create_fks_for_table,
    create_index,
    create_table,
    drop_table,
)

# revision identifiers, used by Alembic.
revision = "5c1a0e6b9d42"
down_revision = "78a40c08b4be"

TABLE = "database_user_grants"


def upgrade():
    create_table(
        TABLE,
        Column("id", Integer, primary_key=True),
        Column("uuid", UUIDType(binary=True), nullable=False, unique=True),
        Column("database_id", Integer, nullable=False),
        Column("username", String(255), nullable=False),
        Column("user_id", Integer, nullable=True),
        Column("claimed_at", DateTime, nullable=True),
        Column("acknowledged_at", DateTime, nullable=True),
        # AuditMixinNullable columns
        Column("created_on", DateTime, nullable=True),
        Column("changed_on", DateTime, nullable=True),
        Column("created_by_fk", Integer, nullable=True),
        Column("changed_by_fk", Integer, nullable=True),
        UniqueConstraint(
            "database_id", "username", name="uq_database_user_grants_db_username"
        ),
    )

    create_index(TABLE, "idx_database_user_grants_database_id", ["database_id"])
    create_index(TABLE, "idx_database_user_grants_username", ["username"])
    create_index(TABLE, "idx_database_user_grants_uuid", ["uuid"], unique=True)

    create_fks_for_table(
        foreign_key_name="fk_database_user_grants_database_id_dbs",
        table_name=TABLE,
        referenced_table="dbs",
        local_cols=["database_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )
    create_fks_for_table(
        foreign_key_name="fk_database_user_grants_user_id_ab_user",
        table_name=TABLE,
        referenced_table="ab_user",
        local_cols=["user_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    create_fks_for_table(
        foreign_key_name="fk_database_user_grants_created_by_fk_ab_user",
        table_name=TABLE,
        referenced_table="ab_user",
        local_cols=["created_by_fk"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    create_fks_for_table(
        foreign_key_name="fk_database_user_grants_changed_by_fk_ab_user",
        table_name=TABLE,
        referenced_table="ab_user",
        local_cols=["changed_by_fk"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade():
    drop_table(TABLE)
