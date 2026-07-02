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
from typing import Any

logger = logging.getLogger(__name__)


def database_signature(database: Any) -> dict[str, Any]:
    """Everything identifying a connection EXCEPT the password.

    Shown to grantees so they can recognize which database they were given.
    Parsed from the *stored* SQLAlchemy URI, in which the password is already
    masked (``set_sqlalchemy_uri`` hides it at write time), so the secret is
    never even read here. Parse failures (exotic engines/URIs) degrade to the
    display name and backend only.
    """
    signature: dict[str, Any] = {
        "database_name": database.database_name,
        "backend": None,
        "driver": None,
        "host": None,
        "port": None,
        "database": None,
        "connection_username": None,
    }
    try:
        # pylint: disable=import-outside-toplevel
        from superset.databases.utils import make_url_safe

        url = make_url_safe(database.sqlalchemy_uri)
        signature.update(
            {
                "backend": url.get_backend_name(),
                "driver": url.get_driver_name(),
                "host": url.host,
                "port": url.port,
                "database": url.database,
                "connection_username": url.username,
            }
        )
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            "Could not parse connection signature for database %s",
            database.id,
            exc_info=True,
        )
    return signature
