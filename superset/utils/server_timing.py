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
"""Request-scoped ``Server-Timing`` instrumentation.

This module accumulates named timing phases (in milliseconds) for the current
request and renders them into a ``Server-Timing`` response header. Combined with
the frontend's existing ``load_chart`` (end-to-end) and ``render_chart`` (pure
render) timing events, it makes frontend-vs-backend latency attribution
unambiguous: the opaque "time to first byte" inside a chart request becomes a
labelled breakdown the browser can read via
``PerformanceResourceTiming.serverTiming``.

All helpers are no-ops outside of a request context (e.g. Celery workers) and
never raise, so instrumentation can never break a request.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Iterator, Optional

from flask import current_app, Flask, g, has_request_context, Response

from superset.utils.dates import now_as_float

logger = logging.getLogger(__name__)

# Request-scoped attribute names on ``flask.g``.
_STORE_KEY = "_server_timing"
_START_KEY = "_server_timing_start"

# Server-Timing metric names must be RFC 7230 tokens. Anything outside the token
# grammar (notably spaces and ``,``/``;``/``=``) is replaced with ``_``.
_TOKEN_DISALLOWED = re.compile(r"[^A-Za-z0-9!#$%&'*+._^`|~-]")


def is_enabled() -> bool:
    """Whether Server-Timing emission is turned on (defaults to enabled)."""
    try:
        return bool(current_app.config.get("SERVER_TIMING_ENABLED", True))
    except RuntimeError:
        # No application context.
        return False


def mark_request_start() -> None:
    """Stamp the start of the request so total server time can be computed."""
    if has_request_context() and is_enabled():
        setattr(g, _START_KEY, now_as_float())


def elapsed_ms() -> Optional[float]:
    """Milliseconds since :func:`mark_request_start`, or ``None`` if unstamped."""
    start = getattr(g, _START_KEY, None)
    if start is None:
        return None
    return now_as_float() - start


def record_metric(
    name: str, duration_ms: float, description: Optional[str] = None
) -> None:
    """Accumulate a named phase (milliseconds) into the request-scoped store.

    Repeated names sum, so e.g. ``db`` becomes the total database time across
    every query executed while serving the request.
    """
    if not has_request_context() or not is_enabled():
        return
    try:
        store = getattr(g, _STORE_KEY, None)
        if store is None:
            store = {}
            setattr(g, _STORE_KEY, store)
        entry = store.get(name)
        if entry is None:
            store[name] = {"dur": float(duration_ms), "desc": description}
        else:
            entry["dur"] += float(duration_ms)
            if description and not entry.get("desc"):
                entry["desc"] = description
    except Exception:  # pylint: disable=broad-except
        # Instrumentation must never break the request it is measuring.
        logger.debug("Failed to record server timing %s", name, exc_info=True)


@contextmanager
def server_timing(name: str, description: Optional[str] = None) -> Iterator[None]:
    """Time a block and record it as a phase. No-op outside a request context."""
    if not has_request_context() or not is_enabled():
        yield
        return
    start = now_as_float()
    try:
        yield
    finally:
        record_metric(name, now_as_float() - start, description)


def _format_metric(name: str, duration_ms: float, description: Optional[str]) -> str:
    token = _TOKEN_DISALLOWED.sub("_", name)
    metric = f"{token};dur={duration_ms:.1f}"
    if description:
        escaped = description.replace("\\", "\\\\").replace('"', '\\"')
        metric += f';desc="{escaped}"'
    return metric


def build_header_value(total_ms: Optional[float] = None) -> str:
    """Render accumulated phases (and an optional ``total``) as a header value."""
    if not is_enabled():
        return ""
    store = getattr(g, _STORE_KEY, None) or {}
    parts: list[str] = []
    if total_ms is not None:
        parts.append(_format_metric("total", total_ms, "Total server time"))
    for name, entry in store.items():
        if name == "total":
            continue
        parts.append(_format_metric(name, entry["dur"], entry.get("desc")))
    return ", ".join(parts)


def register_request_handlers(app: Flask) -> None:
    """Register the before/after request hooks that emit the Server-Timing header."""

    @app.before_request
    def _start_server_timing() -> None:
        mark_request_start()

    @app.after_request
    def _add_server_timing_header(response: Response) -> Response:
        if not is_enabled():
            return response
        header_value = build_header_value(elapsed_ms())
        if header_value and "Server-Timing" not in response.headers:
            response.headers["Server-Timing"] = header_value
            allow_origin = app.config["SERVER_TIMING_ALLOW_ORIGIN"]
            if allow_origin and "Timing-Allow-Origin" not in response.headers:
                response.headers["Timing-Allow-Origin"] = allow_origin
        return response
