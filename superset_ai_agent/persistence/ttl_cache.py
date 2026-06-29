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
"""A tiny in-process TTL cache.

Single-process, thread-safe, time-based eviction. Intended for caching derived
metadata (e.g. a project's physical schema index) so repeated reads within a
short window don't re-hit Superset. The clock is injectable for deterministic
tests; production uses ``time.monotonic`` (immune to wall-clock jumps).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, Hashable, Optional, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TtlCache(Generic[K, V]):
    """A minimal thread-safe time-to-live cache."""

    def __init__(
        self,
        ttl_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (stored_at, value)
        self._store: dict[K, tuple[float, V]] = {}

    @property
    def enabled(self) -> bool:
        """A non-positive TTL disables caching (every get misses)."""
        return self._ttl > 0

    def get(self, key: K) -> Optional[V]:
        """Return the cached value if present and still fresh, else ``None``.

        Stale entries are evicted on access.
        """
        if not self.enabled:
            return None
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if self._clock() - stored_at >= self._ttl:
                del self._store[key]
                return None
            return value

    def set(self, key: K, value: V) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._store[key] = (self._clock(), value)

    def invalidate(self, key: K) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
