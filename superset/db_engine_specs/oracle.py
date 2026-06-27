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
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, TypedDict

from flask_babel import gettext as __
from marshmallow import fields, Schema
from marshmallow.validate import Range
from sqlalchemy import types
from sqlalchemy.engine.url import URL

from superset.constants import TimeGrain
from superset.databases.utils import make_url_safe
from superset.db_engine_specs.base import (
    BaseEngineSpec,
    BasicParametersMixin,
    BasicPropertiesType,
    DatabaseCategory,
)
from superset.errors import ErrorLevel, SupersetError, SupersetErrorType
from superset.utils.network import is_hostname_valid, is_port_open


class OracleParametersSchema(Schema):
    """
    Parameters for the form-based Oracle connection UI.

    Oracle identifies a database by a *service name* (recommended for 12c+ and
    pluggable databases) or a legacy *SID*, not by a plain database name. Exactly
    one of the two must be provided.
    """

    username = fields.String(
        required=True, allow_none=True, metadata={"description": __("Username")}
    )
    password = fields.String(allow_none=True, metadata={"description": __("Password")})
    host = fields.String(
        required=True, metadata={"description": __("Hostname or IP address")}
    )
    port = fields.Integer(
        required=True,
        metadata={"description": __("Database port")},
        validate=Range(min=0, max=2**16, max_inclusive=False),
    )
    service_name = fields.String(
        allow_none=True,
        metadata={
            "description": __(
                "Oracle service name (recommended for Oracle 12c+ and pluggable "
                "databases). Provide either a service name or a SID."
            )
        },
    )
    sid = fields.String(
        allow_none=True,
        metadata={
            "description": __(
                "Oracle System ID (SID) for older databases. Provide either a "
                "service name or a SID."
            )
        },
    )
    query = fields.Dict(
        keys=fields.Str(),
        values=fields.Raw(),
        metadata={"description": __("Additional parameters")},
    )


class OracleParametersType(TypedDict, total=False):
    username: Optional[str]
    password: Optional[str]
    host: str
    port: int
    service_name: Optional[str]
    sid: Optional[str]
    query: dict[str, Any]


class OracleEngineSpec(BasicParametersMixin, BaseEngineSpec):
    engine = "oracle"
    engine_name = "Oracle"

    # The form generates ``oracle+cx_oracle://`` URIs. On SQLAlchemy 1.4 (which
    # Superset pins) only the cx_oracle dialect is registered natively, so it is
    # the driver discovered by ``get_available_engine_specs``; using it here is
    # what makes the parameters form appear. The cx_oracle dialect still drives
    # the modern python-oracledb client when ``cx_Oracle`` is aliased to
    # ``oracledb``. Switch to ``oracledb`` once Superset moves to SQLAlchemy 2.0,
    # which ships a native oracledb dialect.
    parameters_schema = OracleParametersSchema()
    default_driver = "cx_oracle"
    sqlalchemy_uri_placeholder = (
        "oracle+oracledb://user:password@host:port/?service_name=service"
    )

    metadata = {
        "description": "Oracle Database is a multi-model database management system.",
        "logo": "oraclelogo.png",
        "homepage_url": "https://www.oracle.com/database/",
        "categories": [
            DatabaseCategory.TRADITIONAL_RDBMS,
            DatabaseCategory.PROPRIETARY,
        ],
        "pypi_packages": ["oracledb"],
        "connection_string": (
            "oracle+oracledb://{username}:{password}@{hostname}:{port}"
            "/?service_name={service_name}"
        ),
        "default_port": 1521,
        "notes": "Previously used cx_Oracle, now uses oracledb.",
        "docs_url": "https://python-oracledb.readthedocs.io/en/latest/user_guide/installation.html",
        "parameters": {
            "username": "Database username",
            "password": "Database password",
            "host": "Hostname or IP address of the Oracle listener",
            "port": "Default 1521",
            "service_name": "Oracle service name (recommended)",
            "sid": "Oracle SID (legacy alternative to service name)",
        },
    }
    force_column_alias_quotes = True
    max_column_name_length = 128
    supports_multivalues_insert = True

    _time_grain_expressions = {
        None: "{col}",
        TimeGrain.SECOND: "CAST({col} as DATE)",
        TimeGrain.MINUTE: "TRUNC(CAST({col} as DATE), 'MI')",
        TimeGrain.HOUR: "TRUNC(CAST({col} as DATE), 'HH')",
        TimeGrain.DAY: "TRUNC(CAST({col} as DATE), 'DDD')",
        TimeGrain.WEEK: "TRUNC(CAST({col} as DATE), 'WW')",
        TimeGrain.MONTH: "TRUNC(CAST({col} as DATE), 'MONTH')",
        TimeGrain.QUARTER: "TRUNC(CAST({col} as DATE), 'Q')",
        TimeGrain.YEAR: "TRUNC(CAST({col} as DATE), 'YEAR')",
    }

    @classmethod
    def build_sqlalchemy_uri(  # type: ignore[override]
        cls,
        parameters: OracleParametersType,
        encrypted_extra: Optional[dict[str, str]] = None,
    ) -> str:
        query = dict(parameters.get("query", {}) or {})
        service_name = parameters.get("service_name")
        sid = parameters.get("sid")

        # cx_oracle maps the URL path to a SID and the ``service_name`` query
        # parameter to a service name, and rejects a URL that sets both. Prefer
        # the service name when supplied.
        database: Optional[str] = None
        if service_name:
            query["service_name"] = service_name
        elif sid:
            database = sid

        return str(
            URL.create(
                f"{cls.engine}+{cls.default_driver}",
                username=parameters.get("username"),
                password=parameters.get("password"),
                host=parameters["host"],
                port=parameters["port"],
                database=database,
                query=query,
            )
        )

    @classmethod
    def get_parameters_from_uri(  # type: ignore[override]
        cls, uri: str, encrypted_extra: Optional[dict[str, Any]] = None
    ) -> OracleParametersType:
        url = make_url_safe(uri)
        query = dict(url.query.items())
        service_name = query.pop("service_name", None)
        return {
            "username": url.username,
            "password": url.password,
            "host": url.host,
            "port": url.port,
            "service_name": service_name,
            "sid": url.database or None,
            "query": query,
        }

    @classmethod
    def validate_parameters(
        cls, properties: BasicPropertiesType
    ) -> list[SupersetError]:
        """
        Progressive validation for the Oracle parameters form.

        Validates required fields, enforces that exactly one connection
        identifier (service name or SID) is provided, and — once a host and port
        are present — checks that the host resolves and the port is reachable.
        """
        errors: list[SupersetError] = []

        parameters = properties.get("parameters", {})
        present = {key for key in parameters if parameters.get(key, ())}

        if missing := sorted({"host", "port", "username"} - present):
            errors.append(
                SupersetError(
                    message=f"One or more parameters are missing: {', '.join(missing)}",
                    error_type=SupersetErrorType.CONNECTION_MISSING_PARAMETERS_ERROR,
                    level=ErrorLevel.WARNING,
                    extra={"missing": missing},
                ),
            )

        has_service_name = bool(parameters.get("service_name"))
        has_sid = bool(parameters.get("sid"))
        if not has_service_name and not has_sid:
            errors.append(
                SupersetError(
                    message=__(
                        "Either a service name or a SID must be provided."
                    ),
                    error_type=SupersetErrorType.CONNECTION_MISSING_PARAMETERS_ERROR,
                    level=ErrorLevel.WARNING,
                    extra={"missing": ["service_name", "sid"]},
                ),
            )
        elif has_service_name and has_sid:
            errors.append(
                SupersetError(
                    message=__(
                        "Provide either a service name or a SID, not both."
                    ),
                    error_type=SupersetErrorType.CONNECTION_MISSING_PARAMETERS_ERROR,
                    level=ErrorLevel.ERROR,
                    extra={"invalid": ["service_name", "sid"]},
                ),
            )

        host = parameters.get("host", None)
        if not host:
            return errors
        if not is_hostname_valid(host):
            errors.append(
                SupersetError(
                    message="The hostname provided can't be resolved.",
                    error_type=SupersetErrorType.CONNECTION_INVALID_HOSTNAME_ERROR,
                    level=ErrorLevel.ERROR,
                    extra={"invalid": ["host"]},
                ),
            )
            return errors

        port = parameters.get("port", None)
        if not port:
            return errors
        try:
            port = int(port)
        except (ValueError, TypeError):
            errors.append(
                SupersetError(
                    message="Port must be a valid integer.",
                    error_type=SupersetErrorType.CONNECTION_INVALID_PORT_ERROR,
                    level=ErrorLevel.ERROR,
                    extra={"invalid": ["port"]},
                ),
            )
        if not (isinstance(port, int) and 0 <= port < 2**16):
            errors.append(
                SupersetError(
                    message=(
                        "The port must be an integer between 0 and 65535 (inclusive)."
                    ),
                    error_type=SupersetErrorType.CONNECTION_INVALID_PORT_ERROR,
                    level=ErrorLevel.ERROR,
                    extra={"invalid": ["port"]},
                ),
            )
        elif not is_port_open(host, port):
            errors.append(
                SupersetError(
                    message="The port is closed.",
                    error_type=SupersetErrorType.CONNECTION_PORT_CLOSED_ERROR,
                    level=ErrorLevel.ERROR,
                    extra={"invalid": ["port"]},
                ),
            )

        return errors

    @classmethod
    def convert_dttm(
        cls, target_type: str, dttm: datetime, db_extra: Optional[dict[str, Any]] = None
    ) -> Optional[str]:
        sqla_type = cls.get_sqla_column_type(target_type)

        if isinstance(sqla_type, types.Date):
            return f"TO_DATE('{dttm.date().isoformat()}', 'YYYY-MM-DD')"
        if isinstance(sqla_type, types.TIMESTAMP):
            return f"""TO_TIMESTAMP('{
                dttm.isoformat(timespec="microseconds")
            }', 'YYYY-MM-DD"T"HH24:MI:SS.ff6')"""
        if isinstance(sqla_type, types.DateTime):
            datetime_formatted = dttm.isoformat(timespec="seconds")
            return f"""TO_DATE('{datetime_formatted}', 'YYYY-MM-DD"T"HH24:MI:SS')"""
        return None

    @classmethod
    def epoch_to_dttm(cls) -> str:
        return "TO_DATE('1970-01-01','YYYY-MM-DD')+(1/24/60/60)*{col}"

    @classmethod
    def epoch_ms_to_dttm(cls) -> str:
        return "TO_DATE('1970-01-01','YYYY-MM-DD')+(1/24/60/60/1000)*{col}"

    @classmethod
    def fetch_data(
        cls, cursor: Any, limit: Optional[int] = None
    ) -> list[tuple[Any, ...]]:
        """
        :param cursor: Cursor instance
        :param limit: Maximum number of rows to be returned by the cursor
        :return: Result of query
        """
        if not cursor.description:
            return []
        return super().fetch_data(cursor, limit)
