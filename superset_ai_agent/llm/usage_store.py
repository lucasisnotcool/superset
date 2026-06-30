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

"""Storage for LLM-call telemetry (count + timing per ``ModelClient.chat``).

Append-one-row-per-call: durable, aggregates correctly across worker processes,
and a sub-millisecond insert is negligible against a multi-second LLM call. The
read path aggregates in Python from a windowed select so the in-memory and
SQLAlchemy stores share one summariser (identical results, one set of tests) and
the SQL stays portable. Reads are admin-only and infrequent; the ``created_at``
index plus retention (see ``scripts/purge_llm_calls.py``) keep them cheap.

Mirrors the ``coverage_store`` pattern: a Protocol with an in-memory impl (tests/
non-durable mode) and a SQLAlchemy impl (durable, cross-worker).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.persistence.models import AiAgentLlmCall
from superset_ai_agent.schemas import LlmUsageBucket, LlmUsageSummary


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class _CallRow:
    """Lightweight in-flight representation used by both stores' summariser."""

    created_at: datetime
    kind: str
    provider: str
    model: str | None
    duration_ms: int
    ok: bool
    prompt_tokens: int | None
    completion_tokens: int | None


def _bucket(key: str, rows: list[_CallRow]) -> LlmUsageBucket:
    calls = len(rows)
    total_ms = sum(r.duration_ms for r in rows)
    return LlmUsageBucket(
        key=key,
        calls=calls,
        failures=sum(1 for r in rows if not r.ok),
        total_duration_ms=total_ms,
        avg_duration_ms=round(total_ms / calls, 2) if calls else 0.0,
        prompt_tokens=sum(r.prompt_tokens or 0 for r in rows),
        completion_tokens=sum(r.completion_tokens or 0 for r in rows),
    )


def _group(rows: list[_CallRow], key_fn) -> list[LlmUsageBucket]:  # type: ignore[no-untyped-def]
    grouped: dict[str, list[_CallRow]] = {}
    for row in rows:
        grouped.setdefault(key_fn(row), []).append(row)
    # Day buckets read best chronologically; the rest most-used first.
    buckets = [_bucket(key, group) for key, group in grouped.items()]
    return buckets


def summarize(rows: list[_CallRow], *, since: datetime | None) -> LlmUsageSummary:
    """Aggregate call rows into totals + day/model/provider breakdowns."""

    total = _bucket("__total__", rows)
    by_day = sorted(
        _group(rows, lambda r: r.created_at.date().isoformat()),
        key=lambda b: b.key,
    )
    by_model = sorted(
        _group(rows, lambda r: r.model or "(unspecified)"),
        key=lambda b: b.calls,
        reverse=True,
    )
    by_provider = sorted(
        _group(rows, lambda r: r.provider),
        key=lambda b: b.calls,
        reverse=True,
    )
    return LlmUsageSummary(
        total_calls=total.calls,
        total_failures=total.failures,
        total_duration_ms=total.total_duration_ms,
        avg_duration_ms=total.avg_duration_ms,
        total_prompt_tokens=total.prompt_tokens,
        total_completion_tokens=total.completion_tokens,
        by_day=by_day,
        by_model=by_model,
        by_provider=by_provider,
        kinds=sorted({r.kind for r in rows}),
        since=since,
        generated_at=_now(),
    )


class LlmUsageStore(Protocol):
    """Storage contract for LLM-call telemetry."""

    def record(
        self,
        *,
        provider: str,
        model: str | None,
        duration_ms: int,
        ok: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        kind: str = "chat",
    ) -> None:
        """Append one call record. Implementations MUST be cheap and side-effect
        free on the caller (the meter wraps this in fail-open error handling)."""

    def summary(self, *, since: datetime | None = None) -> LlmUsageSummary:
        """Aggregate recorded calls, optionally limited to ``created_at >= since``."""

    def purge_before(self, cutoff: datetime) -> int:
        """Delete records older than ``cutoff``; return the number removed."""


class InMemoryLlmUsageStore:
    """Process-local store guarded by a lock (tests / non-durable mode)."""

    def __init__(self) -> None:
        self._rows: list[_CallRow] = []
        self._lock = threading.Lock()

    def record(
        self,
        *,
        provider: str,
        model: str | None,
        duration_ms: int,
        ok: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        kind: str = "chat",
    ) -> None:
        row = _CallRow(
            created_at=_now(),
            kind=kind,
            provider=provider,
            model=model,
            duration_ms=duration_ms,
            ok=ok,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        with self._lock:
            self._rows.append(row)

    def summary(self, *, since: datetime | None = None) -> LlmUsageSummary:
        with self._lock:
            rows = [
                row for row in self._rows if since is None or row.created_at >= since
            ]
        return summarize(rows, since=since)

    def purge_before(self, cutoff: datetime) -> int:
        with self._lock:
            keep = [row for row in self._rows if row.created_at >= cutoff]
            removed = len(self._rows) - len(keep)
            self._rows = keep
        return removed


class SqlAlchemyLlmUsageStore:
    """SQLAlchemy-backed store (durable, cross-worker)."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def record(
        self,
        *,
        provider: str,
        model: str | None,
        duration_ms: int,
        ok: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        kind: str = "chat",
    ) -> None:
        # Own short-lived session/transaction so metering never piggybacks on (or
        # contends with) a request's long transaction.
        with self.session_factory() as session:
            session.add(
                AiAgentLlmCall(
                    id=uuid4().hex,
                    created_at=_now(),
                    kind=kind,
                    provider=provider,
                    model=model,
                    duration_ms=duration_ms,
                    ok=ok,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            )
            session.commit()

    def summary(self, *, since: datetime | None = None) -> LlmUsageSummary:
        stmt = select(
            AiAgentLlmCall.created_at,
            AiAgentLlmCall.kind,
            AiAgentLlmCall.provider,
            AiAgentLlmCall.model,
            AiAgentLlmCall.duration_ms,
            AiAgentLlmCall.ok,
            AiAgentLlmCall.prompt_tokens,
            AiAgentLlmCall.completion_tokens,
        )
        if since is not None:
            stmt = stmt.where(AiAgentLlmCall.created_at >= since)
        with self.session_factory() as session:
            rows = [
                _CallRow(
                    created_at=row.created_at,
                    kind=row.kind,
                    provider=row.provider,
                    model=row.model,
                    duration_ms=row.duration_ms,
                    ok=row.ok,
                    prompt_tokens=row.prompt_tokens,
                    completion_tokens=row.completion_tokens,
                )
                for row in session.execute(stmt).all()
            ]
        return summarize(rows, since=since)

    def purge_before(self, cutoff: datetime) -> int:
        with self.session_factory() as session:
            result = session.execute(
                delete(AiAgentLlmCall).where(AiAgentLlmCall.created_at < cutoff)
            )
            session.commit()
            return int(result.rowcount or 0)
