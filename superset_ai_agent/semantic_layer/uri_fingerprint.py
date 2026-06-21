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

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_PORTS = {
    "mysql": 3306,
    "mysql+pymysql": 3306,
    "postgresql": 5432,
    "postgresql+psycopg2": 5432,
    "postgres": 5432,
    "redshift": 5439,
    "snowflake": 443,
}

SECRET_QUERY_KEYS = {
    "password",
    "pass",
    "pwd",
    "token",
    "access_token",
    "auth_token",
    "private_key",
    "secret",
}


def fingerprint_database_uri(uri: str, *, salt: str | None = None) -> str:
    """Return a stable, non-secret fingerprint for a database URI."""

    normalized = normalize_database_uri(uri)
    payload = f"{salt or ''}:{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def fingerprint_database_identity(
    *,
    database_id: int,
    salt: str | None = None,
) -> str:
    """Return a fallback fingerprint when the physical URI is unavailable."""

    payload = f"{salt or ''}:superset-database:{database_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_database_uri(uri: str) -> str:
    """Normalize a database URI without retaining credentials."""

    stripped = uri.strip()
    if not stripped:
        raise ValueError("Database URI is empty.")

    split = urlsplit(stripped)
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
    query = urlencode(query_pairs)
    return urlunsplit(
        (
            scheme,
            netloc,
            split.path.rstrip("/"),
            query,
            "",
        )
    )


def _normalized_port(scheme: str, port: int | None) -> int | None:
    if port is None:
        return None
    if DEFAULT_PORTS.get(scheme) == port:
        return None
    return port
