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

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from superset_ai_agent.auth import (
    SignedHeaderIdentityProvider,
    StaticIdentityProvider,
    SupersetRequestAuth,
    SupersetSessionIdentityProvider,
    sign_identity_payload,
)
from superset_ai_agent.config import AgentConfig


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode("ascii"), value.encode("utf-8"))
                for key, value in (headers or {}).items()
            ],
        }
    )


def test_static_identity_provider_returns_local_owner() -> None:
    identity = StaticIdentityProvider().get_identity(_request())

    assert identity.owner_id == "local"
    assert identity.source == "static"


def test_signed_header_identity_provider_accepts_valid_signature() -> None:
    provider = SignedHeaderIdentityProvider(
        header_name="x-agent-identity",
        secret="secret",
    )
    token = sign_identity_payload(
        {"owner_id": "user-1", "username": "ada", "email": "ada@example.com"},
        secret="secret",
    )

    identity = provider.get_identity(_request({"x-agent-identity": token}))

    assert identity.owner_id == "user-1"
    assert identity.username == "ada"
    assert identity.email == "ada@example.com"
    assert identity.source == "signed_header"


def test_signed_header_identity_provider_rejects_missing_header() -> None:
    provider = SignedHeaderIdentityProvider(
        header_name="x-agent-identity",
        secret="secret",
    )

    with pytest.raises(HTTPException) as excinfo:
        provider.get_identity(_request())

    assert excinfo.value.status_code == 401


def test_signed_header_identity_provider_rejects_tampered_signature() -> None:
    provider = SignedHeaderIdentityProvider(
        header_name="x-agent-identity",
        secret="secret",
    )
    token = sign_identity_payload({"owner_id": "user-1"}, secret="secret")

    with pytest.raises(HTTPException) as excinfo:
        provider.get_identity(_request({"x-agent-identity": f"{token}0"}))

    assert excinfo.value.status_code == 401


def test_superset_request_auth_extracts_cookie_and_authorization() -> None:
    request = _request(
        {
            "cookie": "session=abc; csrf_token=def",
            "authorization": "Bearer token",
            "x-csrftoken": "csrf",
        }
    )

    auth = SupersetRequestAuth.from_request(request)

    assert auth.has_credentials() is True
    assert auth.cookies() == {"session": "abc", "csrf_token": "def"}
    assert auth.headers(include_csrf=True) == {
        "Authorization": "Bearer token",
        "X-CSRFToken": "csrf",
    }
    assert SupersetRequestAuth.from_request(request) is auth


def test_superset_session_identity_provider_uses_superset_me() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/v1/me/"
        assert request.headers["cookie"] == "session=abc"
        return httpx.Response(
            200,
            json={
                "result": {
                    "id": 42,
                    "username": "ada",
                    "email": "ada@example.com",
                }
            },
        )

    provider = SupersetSessionIdentityProvider(
        config=AgentConfig(superset_base_url="http://superset.local"),
        transport=httpx.MockTransport(handler),
    )
    request = _request({"cookie": "session=abc"})

    identity = provider.get_identity(request)

    assert identity.owner_id == "superset:42"
    assert identity.username == "ada"
    assert identity.email == "ada@example.com"
    assert identity.source == "superset_session"
    assert provider.get_identity(request) is identity
    assert len(requests) == 1


def test_superset_session_identity_provider_rejects_missing_session() -> None:
    provider = SupersetSessionIdentityProvider(config=AgentConfig())

    with pytest.raises(HTTPException) as excinfo:
        provider.get_identity(_request())

    assert excinfo.value.status_code == 401


def test_superset_session_identity_provider_rejects_expired_session() -> None:
    provider = SupersetSessionIdentityProvider(
        config=AgentConfig(superset_base_url="http://superset.local"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"message": "expired"})
        ),
    )

    with pytest.raises(HTTPException) as excinfo:
        provider.get_identity(_request({"cookie": "session=expired"}))

    assert excinfo.value.status_code == 401
