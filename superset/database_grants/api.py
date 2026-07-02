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
"""REST API for admin pre-approval of usernames for database access.

Grant management (list/create/revoke) is Admin-only: the
``DatabaseAccessGrant`` permission view lives in ``ADMIN_ONLY_VIEW_MENUS``.
The two self-scoped endpoints — ``mine`` (list-and-claim your own grants,
which drives the notification dialog) and ``acknowledge`` (dismiss it) — carry
their own permission names so non-admin roles can be granted just those (see
``BuilderSecurityManager.GRANT_SELF_PERMS``).
"""

import logging
from typing import Any

from flask import g, request, Response
from flask_appbuilder import permission_name
from flask_appbuilder.api import expose, protect, rison as parse_rison, safe
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_babel import ngettext
from marshmallow import ValidationError

from superset.commands.database_grants.acknowledge import (
    AcknowledgeDatabaseGrantsCommand,
)
from superset.commands.database_grants.claim import claim_database_grants
from superset.commands.database_grants.create import BulkCreateDatabaseGrantsCommand
from superset.commands.database_grants.exceptions import (
    DatabaseGrantDatabaseNotFoundError,
    DatabaseGrantNotFoundError,
)
from superset.commands.database_grants.revoke import RevokeDatabaseGrantCommand
from superset.commands.exceptions import CommandInvalidError
from superset.constants import MODEL_API_RW_METHOD_PERMISSION_MAP, RouteMethod
from superset.database_grants.schemas import (
    DatabaseGrantAcknowledgeSchema,
    DatabaseGrantBulkPostSchema,
    get_delete_ids_schema,
)
from superset.database_grants.utils import database_signature
from superset.extensions import db, event_logger
from superset.models.database_grant import DatabaseUserGrant
from superset.views.base_api import (
    BaseSupersetModelRestApi,
    requires_json,
    statsd_metrics,
)

logger = logging.getLogger(__name__)


class DatabaseGrantRestApi(BaseSupersetModelRestApi):
    datamodel = SQLAInterface(DatabaseUserGrant)
    include_route_methods = {
        RouteMethod.GET,
        RouteMethod.GET_LIST,
        RouteMethod.POST,
        RouteMethod.DELETE,
        RouteMethod.INFO,
    } | {
        "bulk_delete",
        "mine",
        "acknowledge",
    }
    resource_name = "database_grant"
    class_permission_name = "DatabaseAccessGrant"
    openapi_spec_tag = "Database Access Grants"
    method_permission_name = MODEL_API_RW_METHOD_PERMISSION_MAP
    allow_browser_login = True

    list_columns = [
        "id",
        "uuid",
        "username",
        "database_id",
        "database.id",
        "database.database_name",
        "user_id",
        "claimed_at",
        "acknowledged_at",
        "created_on",
        "changed_on",
        "changed_on_delta_humanized",
        "created_by.id",
        "created_by.first_name",
        "created_by.last_name",
    ]
    show_columns = list_columns
    order_columns = [
        "username",
        "database_id",
        "created_on",
        "claimed_at",
        "changed_on_delta_humanized",
    ]
    search_columns = ("username", "database", "created_by")

    add_model_schema = DatabaseGrantBulkPostSchema()
    acknowledge_model_schema = DatabaseGrantAcknowledgeSchema()

    apispec_parameter_schemas = {
        "get_delete_ids_schema": get_delete_ids_schema,
    }

    @expose("/", methods=("POST",))
    @protect()
    @safe
    @statsd_metrics
    @requires_json
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.post",
        log_to_statsd=False,
    )
    def post(self) -> Response:
        """Pre-approve a list of usernames for a database.
        ---
        post:
          summary: Pre-approve usernames for database access
          description: >-
            Creates pending grants for each username. Usernames whose account
            already exists are claimed immediately (access is live without a
            new login). Already-granted usernames are skipped and reported.
          requestBody:
            description: Database id and usernames
            required: true
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/{{self.__class__.__name__}}.post'
          responses:
            201:
              description: Grants created
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      result:
                        type: object
                        properties:
                          created:
                            type: array
                            items:
                              type: string
                          skipped:
                            type: array
                            items:
                              type: string
                          claimed_usernames:
                            type: array
                            items:
                              type: string
            400:
              $ref: '#/components/responses/400'
            401:
              $ref: '#/components/responses/401'
            403:
              $ref: '#/components/responses/403'
            422:
              $ref: '#/components/responses/422'
            500:
              $ref: '#/components/responses/500'
        """
        try:
            item = self.add_model_schema.load(request.json)
        except ValidationError as error:
            return self.response_400(message=error.messages)
        try:
            result = BulkCreateDatabaseGrantsCommand(
                item["database_id"], item["usernames"]
            ).run()
            return self.response(201, result=result)
        except DatabaseGrantDatabaseNotFoundError as ex:
            return self.response_422(message=str(ex))
        except CommandInvalidError as ex:
            return self.response_422(message=str(ex))

    @expose("/<int:pk>", methods=("DELETE",))
    @protect()
    @safe
    @statsd_metrics
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.delete",
        log_to_statsd=False,
    )
    def delete(self, pk: int) -> Response:
        """Revoke a grant.
        ---
        delete:
          summary: Revoke a database access grant
          description: >-
            Detaches the grant role from the claimed user (if any) and deletes
            the grant. Access via other grants or the user's own connections
            is unaffected.
          parameters:
          - in: path
            schema:
              type: integer
            name: pk
            description: The grant pk
          responses:
            200:
              description: Grant revoked
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      message:
                        type: string
            401:
              $ref: '#/components/responses/401'
            403:
              $ref: '#/components/responses/403'
            404:
              $ref: '#/components/responses/404'
            500:
              $ref: '#/components/responses/500'
        """
        try:
            RevokeDatabaseGrantCommand(pk).run()
            return self.response(200, message="OK")
        except DatabaseGrantNotFoundError:
            return self.response_404()

    @expose("/", methods=("DELETE",))
    @protect()
    @safe
    @statsd_metrics
    @parse_rison(get_delete_ids_schema)
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.bulk_delete",
        log_to_statsd=False,
    )
    def bulk_delete(self, **kwargs: Any) -> Response:
        """Bulk revoke grants.
        ---
        delete:
          summary: Bulk revoke database access grants
          parameters:
          - in: query
            name: q
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/get_delete_ids_schema'
          responses:
            200:
              description: Grants revoked
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      message:
                        type: string
            401:
              $ref: '#/components/responses/401'
            403:
              $ref: '#/components/responses/403'
            404:
              $ref: '#/components/responses/404'
            500:
              $ref: '#/components/responses/500'
        """
        item_ids = kwargs["rison"]
        try:
            for item_id in item_ids:
                RevokeDatabaseGrantCommand(item_id).run()
            return self.response(
                200,
                message=ngettext(
                    "Revoked %(num)d grant",
                    "Revoked %(num)d grants",
                    num=len(item_ids),
                ),
            )
        except DatabaseGrantNotFoundError:
            return self.response_404()

    @expose("/mine", methods=("GET",))
    @protect()
    @safe
    @statsd_metrics
    @permission_name("mine")
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.mine",
        log_to_statsd=False,
    )
    def mine(self) -> Response:
        """List (and lazily claim) the caller's unacknowledged grants.
        ---
        get:
          summary: List the caller's unacknowledged database access grants
          description: >-
            First claims any pending grants matching the caller's username or
            email (so a grant issued mid-session becomes live on the next app
            load), then returns claimed-but-unacknowledged grants with the
            connection signature (host, port, database, connection username —
            never the password). Drives the post-login notification dialog.
          responses:
            200:
              description: Unacknowledged grants
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      count:
                        type: integer
                      result:
                        type: array
                        items:
                          type: object
                          properties:
                            id:
                              type: integer
                            database_id:
                              type: integer
                            database_name:
                              type: string
                            backend:
                              type: string
                              nullable: true
                            driver:
                              type: string
                              nullable: true
                            host:
                              type: string
                              nullable: true
                            port:
                              type: integer
                              nullable: true
                            database:
                              type: string
                              nullable: true
                            connection_username:
                              type: string
                              nullable: true
                            granted_on:
                              type: string
                              nullable: true
            401:
              $ref: '#/components/responses/401'
            403:
              $ref: '#/components/responses/403'
            500:
              $ref: '#/components/responses/500'
        """
        user = g.user
        # T3 lazy claim: covers grants issued mid-session and any auth-path
        # ordering surprises. Idempotent and fail-soft.
        claim_database_grants(user)

        grants = (
            db.session.query(DatabaseUserGrant)
            .filter(
                DatabaseUserGrant.user_id == user.id,
                DatabaseUserGrant.acknowledged_at.is_(None),
            )
            .all()
        )
        result = []
        for grant in grants:
            database = grant.database
            if database is None:
                continue
            payload = {
                "id": grant.id,
                "database_id": grant.database_id,
                "granted_on": (
                    grant.created_on.isoformat() if grant.created_on else None
                ),
            }
            payload.update(database_signature(database))
            result.append(payload)
        return self.response(200, count=len(result), result=result)

    @expose("/acknowledge", methods=("POST",))
    @protect()
    @safe
    @statsd_metrics
    @requires_json
    @permission_name("acknowledge")
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: f"{self.__class__.__name__}.acknowledge",
        log_to_statsd=False,
    )
    def acknowledge(self) -> Response:
        """Acknowledge the caller's own grants (dismisses the dialog).
        ---
        post:
          summary: Acknowledge database access grants
          description: >-
            Marks the caller's own grants as acknowledged. Ids belonging to
            other users (or unknown ids) are ignored, not errors.
          requestBody:
            description: Grant ids
            required: true
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    ids:
                      type: array
                      items:
                        type: integer
          responses:
            200:
              description: Acknowledged
              content:
                application/json:
                  schema:
                    type: object
                    properties:
                      acknowledged:
                        type: integer
            400:
              $ref: '#/components/responses/400'
            401:
              $ref: '#/components/responses/401'
            403:
              $ref: '#/components/responses/403'
            500:
              $ref: '#/components/responses/500'
        """
        try:
            item = self.acknowledge_model_schema.load(request.json)
        except ValidationError as error:
            return self.response_400(message=error.messages)
        count = AcknowledgeDatabaseGrantsCommand(g.user, item["ids"]).run()
        return self.response(200, acknowledged=count)
