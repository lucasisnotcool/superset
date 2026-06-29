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

from contextlib import contextmanager
from typing import Any, Iterator

from flask import Flask

from superset.utils import server_timing


@contextmanager
def _server_timing_enabled(app: Flask, enabled: bool) -> Iterator[None]:
    original = app.config.get("SERVER_TIMING_ENABLED")
    app.config["SERVER_TIMING_ENABLED"] = enabled
    try:
        yield
    finally:
        app.config["SERVER_TIMING_ENABLED"] = original


def test_record_metric_accumulates_repeated_names(app: Flask) -> None:
    with app.test_request_context():
        server_timing.record_metric("db", 10.0)
        server_timing.record_metric("db", 5.0)
        server_timing.record_metric("cache", 2.0)

        header = server_timing.build_header_value()

    assert "db;dur=15.0" in header
    assert "cache;dur=2.0" in header


def test_build_header_value_includes_total_and_description(app: Flask) -> None:
    with app.test_request_context():
        server_timing.record_metric("db", 53.0, "Query execution")
        header = server_timing.build_header_value(total_ms=80.0)

    # Total is rendered first, with its description quoted.
    assert header.startswith('total;dur=80.0;desc="Total server time"')
    assert 'db;dur=53.0;desc="Query execution"' in header


def test_server_timing_context_manager_records_phase(app: Flask) -> None:
    with app.test_request_context():
        with server_timing.server_timing("db", "Query execution"):
            pass
        header = server_timing.build_header_value()

    assert "db;dur=" in header


def test_metric_name_is_sanitized_to_a_token(app: Flask) -> None:
    with app.test_request_context():
        server_timing.record_metric("weird name,with;chars", 1.0)
        header = server_timing.build_header_value()

    assert "weird_name_with_chars;dur=1.0" in header
    # No raw separators leak into the metric name.
    assert "weird name" not in header


def test_no_op_outside_request_context(app: Flask) -> None:
    # Only an app context is active here (autouse fixture), not a request.
    server_timing.record_metric("db", 10.0)
    assert server_timing.build_header_value() == ""
    assert server_timing.elapsed_ms() is None


def test_disabled_flag_skips_recording(app: Flask) -> None:
    with app.test_request_context(), _server_timing_enabled(app, False):
        assert server_timing.is_enabled() is False
        server_timing.record_metric("db", 10.0)
        with server_timing.server_timing("cache"):
            pass
        assert server_timing.build_header_value(total_ms=80.0) == ""


def test_mark_request_start_enables_elapsed(app: Flask) -> None:
    with app.test_request_context():
        server_timing.mark_request_start()
        elapsed = server_timing.elapsed_ms()

    assert elapsed is not None
    assert elapsed >= 0


def test_header_emitted_on_real_response(client: Any) -> None:
    """The before/after request hooks attach Server-Timing to live responses."""
    response = client.get("/health")
    header = response.headers.get("Server-Timing")
    assert header is not None
    assert "total;dur=" in header
