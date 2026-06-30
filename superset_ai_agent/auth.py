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

import base64
import hashlib
import hmac
import json  # noqa: TID251 - standalone agent auth payload signing
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Literal, Protocol

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID


class AgentIdentity(BaseModel):
    """Identity used to scope persisted agent state."""

    owner_id: str = Field(min_length=1)
    username: str | None = None
    email: str | None = None
    source: Literal["static", "signed_header", "superset_session"] = "static"
    #: Superset role names, when the provider can supply them (signed_header
    #: payloads carry them; the session provider fetches them lazily for admin
    #: checks only — see ``IdentityProvider.is_admin``). Empty when unknown.
    roles: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SupersetRequestAuth:
    """Request-scoped Superset auth material copied from the inbound request."""

    cookie_header: str | None = field(default=None, repr=False)
    authorization: str | None = field(default=None, repr=False)
    csrf_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_request(cls, request: Request) -> "SupersetRequestAuth":
        existing = getattr(request.state, "superset_request_auth", None)
        if isinstance(existing, cls):
            return existing
        auth = cls(
            cookie_header=request.headers.get("cookie"),
            authorization=request.headers.get("authorization"),
            csrf_token=(
                request.headers.get("x-csrftoken")
                or request.headers.get("x-csrf-token")
            ),
        )
        request.state.superset_request_auth = auth
        return auth

    def has_credentials(self) -> bool:
        return bool(self.cookie_header or self.authorization)

    def cookies(self) -> dict[str, str]:
        if not self.cookie_header:
            return {}
        cookie = SimpleCookie()
        cookie.load(self.cookie_header)
        return {key: morsel.value for key, morsel in cookie.items()}

    def headers(self, *, include_csrf: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if include_csrf and self.csrf_token:
            headers["X-CSRFToken"] = self.csrf_token
        return headers


class IdentityProvider(Protocol):
    """Resolve the owner for persisted agent state."""

    def get_identity(self, request: Request) -> AgentIdentity:
        """Return the identity for this request."""

    def is_admin(self, request: Request) -> bool:
        """Whether the caller may access admin-only agent surfaces.

        Kept off the hot path: implementations may make an extra call (e.g. fetch
        Superset roles) since only admin-gated endpoints invoke this, never the
        per-request identity resolution.
        """


class StaticIdentityProvider:
    """Development-only identity provider using DEFAULT_OWNER_ID."""

    def get_identity(self, request: Request) -> AgentIdentity:
        return AgentIdentity(owner_id=DEFAULT_OWNER_ID, source="static")

    def is_admin(self, request: Request) -> bool:
        # Static identity is a local-dev convenience (no real auth); treat it as
        # admin so the usage view is reachable in development.
        return True


class SignedHeaderIdentityProvider:
    """Trust signed internal identity headers from an authenticated proxy."""

    def __init__(
        self,
        *,
        header_name: str,
        secret: str,
        admin_roles: tuple[str, ...] = ("Admin",),
    ):
        self.header_name = header_name
        self.secret = secret
        self.admin_roles = set(admin_roles)

    def is_admin(self, request: Request) -> bool:
        # The signed payload is trusted; admin follows from the roles it carries.
        return bool(set(self.get_identity(request).roles) & self.admin_roles)

    def get_identity(self, request: Request) -> AgentIdentity:
        header_value = request.headers.get(self.header_name)
        if not header_value:
            raise HTTPException(status_code=401, detail="Missing agent identity.")
        try:
            payload_token, signature = header_value.rsplit(".", 1)
        except ValueError as ex:
            raise HTTPException(
                status_code=401, detail="Invalid agent identity."
            ) from ex

        expected = _signature(payload_token, self.secret)
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid agent identity.")

        try:
            payload = json.loads(_urlsafe_decode(payload_token).decode("utf-8"))
            identity = AgentIdentity.model_validate(
                {**payload, "source": "signed_header"}
            )
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(
                status_code=401, detail="Invalid agent identity."
            ) from ex
        return identity


class SupersetSessionIdentityProvider:
    """Resolve identity by validating the incoming Superset browser session."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = config.superset_base_url.rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(30.0)
        self.admin_roles = set(config.admin_roles)

    def is_admin(self, request: Request) -> bool:
        cached = getattr(request.state, "agent_is_admin", None)
        if isinstance(cached, bool):
            return cached
        # Roles live at a separate endpoint from /api/v1/me/; fetch them only here
        # (admin-gated routes), never on the per-request identity hot path.
        auth = SupersetRequestAuth.from_request(request)
        if not auth.has_credentials():
            raise HTTPException(status_code=401, detail="Missing Superset session.")
        try:
            with httpx.Client(
                cookies=auth.cookies(),
                headers=auth.headers(),
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = client.get(self._url("/api/v1/me/roles/"))
        except httpx.HTTPError as ex:
            raise HTTPException(
                status_code=502,
                detail=f"Could not resolve Superset roles: {ex}",
            ) from ex
        if response.status_code in {401, 403}:
            raise HTTPException(
                status_code=response.status_code,
                detail="Superset session is missing, expired, or unauthorized.",
            )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Could not resolve Superset roles: HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as ex:
            raise HTTPException(
                status_code=502,
                detail="Superset /api/v1/me/roles/ returned invalid JSON.",
            ) from ex
        is_admin = bool(_role_names_from_me_roles(payload) & self.admin_roles)
        request.state.agent_is_admin = is_admin
        return is_admin

    def get_identity(self, request: Request) -> AgentIdentity:
        existing = getattr(request.state, "agent_identity", None)
        if isinstance(existing, AgentIdentity):
            return existing

        auth = SupersetRequestAuth.from_request(request)
        if not auth.has_credentials():
            raise HTTPException(
                status_code=401,
                detail="Missing Superset session.",
            )

        try:
            with httpx.Client(
                cookies=auth.cookies(),
                headers=auth.headers(),
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                response = client.get(self._url("/api/v1/me/"))
        except httpx.HTTPError as ex:
            raise HTTPException(
                status_code=502,
                detail=f"Could not validate Superset session: {ex}",
            ) from ex

        if response.status_code in {401, 403}:
            raise HTTPException(
                status_code=response.status_code,
                detail="Superset session is missing, expired, or unauthorized.",
            )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Could not validate Superset session: HTTP {response.status_code}"
                ),
            )

        try:
            payload = response.json()
        except ValueError as ex:
            raise HTTPException(
                status_code=502,
                detail="Superset /api/v1/me/ returned invalid JSON.",
            ) from ex
        identity = _identity_from_superset_me_payload(payload)
        request.state.agent_identity = identity
        return identity

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


def create_identity_provider(config: AgentConfig) -> IdentityProvider:
    """Create the configured identity provider."""

    if config.identity_provider == "static":
        return StaticIdentityProvider()
    if config.identity_provider == "signed_header":
        if not config.signed_identity_secret:
            raise ValueError(
                "AI_AGENT_SIGNED_IDENTITY_SECRET is required for signed_header "
                "identity."
            )
        return SignedHeaderIdentityProvider(
            header_name=config.signed_identity_header,
            secret=config.signed_identity_secret,
            admin_roles=config.admin_roles,
        )
    if config.identity_provider == "superset_session":
        return SupersetSessionIdentityProvider(config=config)
    raise ValueError(
        "Unsupported AI_AGENT_IDENTITY_PROVIDER value "
        f"{config.identity_provider!r}. Expected one of: static, signed_header, "
        "superset_session."
    )


def sign_identity_payload(payload: dict[str, str], *, secret: str) -> str:
    """Create a signed identity header value for tests and trusted proxies."""

    payload_token = _urlsafe_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{payload_token}.{_signature(payload_token, secret)}"


def _signature(payload_token: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        payload_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _identity_from_superset_me_payload(payload: object) -> AgentIdentity:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="Superset /api/v1/me/ returned an unexpected payload.",
        )
    data = payload.get("result", payload)
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail="Superset /api/v1/me/ returned an unexpected result.",
        )
    user_id = data.get("id") or data.get("user_id") or data.get("pk")
    username = data.get("username") or data.get("name")
    email = data.get("email")
    owner_key = user_id or username or email
    if owner_key is None:
        raise HTTPException(
            status_code=502,
            detail="Superset /api/v1/me/ did not include a user identifier.",
        )
    return AgentIdentity(
        owner_id=f"superset:{owner_key}",
        username=str(username) if username is not None else None,
        email=str(email) if email is not None else None,
        source="superset_session",
    )


def _role_names_from_me_roles(payload: object) -> set[str]:
    """Extract role names from a Superset ``/api/v1/me/roles/`` response.

    The endpoint returns ``{"result": {"roles": {"<RoleName>": [...perms...]}}}``
    (bootstrap_user_data, include_perms). Degrades to an empty set on any
    unexpected shape so an admin check fails closed (denies access) rather than
    raising.
    """

    if not isinstance(payload, dict):
        return set()
    data = payload.get("result", payload)
    if not isinstance(data, dict):
        return set()
    roles = data.get("roles")
    if isinstance(roles, dict):
        return {str(name) for name in roles}
    if isinstance(roles, list):
        # Defensive: some shapes expose a flat list of role names.
        return {str(name) for name in roles if isinstance(name, str)}
    return set()
