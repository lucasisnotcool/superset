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

"""Async job store and runner for long-running semantic-layer work.

Onboarding can generate many models via the LLM and would otherwise block the
request. These primitives let a route submit the work, return immediately, and
expose progress through a pollable job record.

The in-memory store is process-local (single worker). A production deployment
should back this with the agent database or a task queue (Celery); see the
``wren_model.md`` risk register.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable, Protocol

from superset_ai_agent.semantic_layer.schemas import OnboardingResult, SemanticJob


class JobNotFoundError(KeyError):
    """Raised when a semantic job cannot be found."""


class JobStore(Protocol):
    """Storage contract for async semantic-layer jobs."""

    def create(self, *, kind: str, project_id: str | None) -> SemanticJob: ...

    def get(self, job_id: str) -> SemanticJob: ...

    def complete(self, job_id: str, result: OnboardingResult) -> SemanticJob: ...

    def fail(self, job_id: str, error: str) -> SemanticJob: ...


class InMemoryJobStore:
    """Process-local job store guarded by a lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, SemanticJob] = {}
        self._lock = threading.Lock()

    def create(self, *, kind: str, project_id: str | None) -> SemanticJob:
        job = SemanticJob(kind=kind, project_id=project_id, status="running")
        with self._lock:
            self._jobs[job.id] = job
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> SemanticJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            return job.model_copy(deep=True)

    def complete(self, job_id: str, result: OnboardingResult) -> SemanticJob:
        return self._update(job_id, status="completed", result=result)

    def fail(self, job_id: str, error: str) -> SemanticJob:
        return self._update(job_id, status="failed", error=error)

    def _update(
        self,
        job_id: str,
        *,
        status: str,
        result: OnboardingResult | None = None,
        error: str | None = None,
    ) -> SemanticJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(job_id)
            updated = job.model_copy(
                update={
                    "status": status,
                    "result": result,
                    "error": error,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._jobs[job_id] = updated
            return updated.model_copy(deep=True)


class JobRunner(Protocol):
    """Executes a job callable, in-thread or in-process."""

    def submit(self, fn: Callable[[], None]) -> None: ...


class InlineJobRunner:
    """Runs the job synchronously. Used in tests and single-shot contexts."""

    def submit(self, fn: Callable[[], None]) -> None:
        fn()


class ThreadJobRunner:
    """Runs the job on a daemon thread so the request returns immediately."""

    def submit(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()
