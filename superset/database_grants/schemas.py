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
from marshmallow import fields, Schema

get_delete_ids_schema = {"type": "array", "items": {"type": "integer"}}


class DatabaseGrantBulkPostSchema(Schema):
    """Bulk pre-approval: one database, a pasted list of usernames."""

    database_id = fields.Integer(
        required=True,
        metadata={"description": "The database connection to grant access to"},
    )
    usernames = fields.List(
        fields.String(),
        required=True,
        metadata={
            "description": (
                "Usernames (== emails on SSO deployments) to pre-approve. "
                "Normalized to lowercase; duplicates and already-granted "
                "entries are skipped, not errors."
            )
        },
    )


class DatabaseGrantAcknowledgeSchema(Schema):
    """Self-scoped acknowledgment of the grant notification dialog."""

    ids = fields.List(
        fields.Integer(),
        required=True,
        metadata={"description": "Grant ids to acknowledge (own grants only)"},
    )
