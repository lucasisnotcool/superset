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
"""Tests for the TTL cache backing the schema-index cache (F1)."""

from __future__ import annotations

from superset_ai_agent.persistence.ttl_cache import TtlCache


class _Clock:
    """Deterministic, manually-advanced clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_set_then_get_returns_value_while_fresh() -> None:
    clock = _Clock()
    cache: TtlCache[str, int] = TtlCache(ttl_seconds=60, clock=clock)

    cache.set("k", 7)
    assert cache.get("k") == 7

    clock.now = 59.9  # still within the window
    assert cache.get("k") == 7


def test_get_misses_for_unknown_key() -> None:
    cache: TtlCache[str, int] = TtlCache(ttl_seconds=60, clock=_Clock())
    assert cache.get("absent") is None


def test_entry_expires_and_is_evicted_after_ttl() -> None:
    clock = _Clock()
    cache: TtlCache[str, int] = TtlCache(ttl_seconds=60, clock=clock)
    cache.set("k", 7)

    clock.now = 60.0  # ttl reached (>= ttl is stale)
    assert cache.get("k") is None
    # The stale entry was evicted, so a later read is still a miss.
    assert cache.get("k") is None


def test_invalidate_and_clear() -> None:
    cache: TtlCache[str, int] = TtlCache(ttl_seconds=60, clock=_Clock())
    cache.set("a", 1)
    cache.set("b", 2)

    cache.invalidate("a")
    assert cache.get("a") is None
    assert cache.get("b") == 2

    cache.clear()
    assert cache.get("b") is None


def test_non_positive_ttl_disables_caching() -> None:
    cache: TtlCache[str, int] = TtlCache(ttl_seconds=0, clock=_Clock())
    assert cache.enabled is False
    cache.set("k", 7)
    assert cache.get("k") is None  # never stored


def test_tuple_keys_supported() -> None:
    """The schema-index cache keys on (project_id, sorted schema names)."""
    cache: TtlCache[tuple[str, tuple[str, ...]], str] = TtlCache(
        ttl_seconds=60, clock=_Clock()
    )
    key = ("proj-1", ("public", "sales"))
    cache.set(key, "index")
    assert cache.get(key) == "index"
    # A different schema set is a distinct key (cache miss).
    assert cache.get(("proj-1", ("public",))) is None
