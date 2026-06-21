# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information regarding
# copyright ownership.
#
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from flask import current_app as app, request
from flask_appbuilder.api import expose, protect, safe
from sqlalchemy.exc import OperationalError

from superset.extensions import db, event_logger, security_manager
from superset.models.core import Database
from superset.superset_typing import FlaskResponse
from superset.views.base_api import BaseSupersetApi, statsd_metrics

DEFAULT_PORTS = {
    "mysql": 3306,
    "mysql+pymysql": 3306,
    "postgres": 5432,
    "postgresql": 5432,
    "postgresql+psycopg2": 5432,
    "redshift": 5439,
    "snowflake": 443,
}

SECRET_QUERY_KEYS = {
    "access_token",
    "auth_token",
    "pass",
    "password",
    "private_key",
    "pwd",
    "secret",
    "token",
}


class AiAgentRestApi(BaseSupersetApi):
    """Governed helper endpoints for the standalone Superset AI agent."""

    resource_name = "ai-agent"
    class_permission_name = "AiAgent"
    allow_browser_login = True

    @expose("/database/<int:database_id>/identity", methods=("GET",))
    @protect()
    @safe
    @statsd_metrics
    @event_logger.log_this_with_context(
        action=lambda self, *args, **kwargs: (
            f"{self.__class__.__name__}.database_identity"
        ),
        log_to_statsd=False,
    )
    def database_identity(self, database_id: int) -> FlaskResponse:
        """Return non-secret database identity for AI semantic-layer matching."""

        database = db.session.get(Database, database_id)
        if database is None:
            return self.response_404()

        security_manager.raise_for_access(database=database)
        catalog = request.args.get("catalog")
        schemas = _accessible_schemas(database, catalog=catalog)
        return self.response(
            200,
            result={
                "database_id": database.id,
                "database_name": database.database_name,
                "backend": database.backend,
                "driver": database.driver,
                "uri_fingerprint": _database_uri_fingerprint(
                    database.sqlalchemy_uri_decrypted
                ),
                "catalog": catalog,
                "schemas": schemas,
            },
        )


def _accessible_schemas(database: Database, *, catalog: str | None) -> list[str]:
    try:
        schemas = database.get_all_schema_names(
            catalog=catalog,
            cache=database.schema_cache_enabled,
            cache_timeout=database.schema_cache_timeout or None,
            force=False,
        )
    except OperationalError:
        return []
    return sorted(
        security_manager.get_schemas_accessible_by_user(
            database,
            catalog,
            schemas,
        )
    )


def _database_uri_fingerprint(uri: str) -> str:
    normalized = _normalize_database_uri(uri)
    salt = app.config.get("AI_AGENT_SEMANTIC_URI_FINGERPRINT_SALT") or ""
    return hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()


def _normalize_database_uri(uri: str) -> str:
    split = urlsplit(uri.strip())
    scheme = split.scheme.lower()
    hostname = (split.hostname or "").lower()
    port = _normalized_port(scheme, split.port)
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    query_pairs = [
        (key, value)
        for key, value in sorted(parse_qsl(split.query, keep_blank_values=True))
        if key.lower() not in SECRET_QUERY_KEYS
    ]
    return urlunsplit(
        (
            scheme,
            netloc,
            _normalized_path(split.path),
            urlencode(query_pairs),
            "",
        )
    )


def _normalized_path(path: str) -> str:
    return quote(unquote(path.rstrip("/")), safe="/")


def _normalized_port(scheme: str, port: int | None) -> int | None:
    if port is None or DEFAULT_PORTS.get(scheme) == port:
        return None
    return port
