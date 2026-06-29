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

"""Storage for background MDL coverage runs (Feature B).

A coverage run is both an audit result (score + report) and the supersession
state row: a new active-set change supersedes any in-flight run and starts a
fresh one. ``claim`` is a compare-and-set lease so two workers cannot run the
same run concurrently. Mirrors ``jobs.py`` (in-memory for tests, SQLAlchemy for
cross-worker durability).
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.persistence.models import AiAgentCoverageRun
from superset_ai_agent.semantic_layer.copilot.schemas import (
    CoverageProgress,
    CoverageReport,
    CoverageRun,
)

#: Run states that are still in flight (not terminal).
_ACTIVE_STATES = ("pending", "running")


class CoverageRunNotFoundError(KeyError):
    """Raised when a coverage run id is unknown."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CoverageRunStore(Protocol):
    """Storage contract for background coverage runs."""

    def create(
        self, *, project_id: str, owner_id: str, mdl_checksum: str, docs_checksum: str
    ) -> CoverageRun: ...

    def get(self, run_id: str) -> CoverageRun: ...

    def claim(self, run_id: str) -> bool:
        """Atomically transition ``pending`` → ``running``; True if claimed."""

    def report_progress(self, run_id: str, progress: CoverageProgress) -> CoverageRun:
        """Record live progress on a running run (advisory; Feature C)."""

    def complete(
        self, run_id: str, report: CoverageReport, *, score: float
    ) -> CoverageRun: ...

    def fail(self, run_id: str, error: str) -> CoverageRun: ...

    def set_recovery(
        self, run_id: str, *, status: str, conversation_id: str | None = None
    ) -> CoverageRun:
        """Set the recovery agent status (and optionally its conversation id)."""

    def dismiss_recovery(self, run_id: str) -> CoverageRun:
        """Durably dismiss the 'recovery suggestions ready' notification."""

    def supersede(self, project_id: str, *, except_run_id: str | None = None) -> int:
        """Mark in-flight runs for the project ``superseded`` (except one)."""

    def latest_complete(self, project_id: str) -> CoverageRun | None: ...

    def latest_complete_bulk(self, project_ids: list[str]) -> dict[str, CoverageRun]:
        """Batch ``latest_complete`` — newest complete run per project id.

        Returns only the project ids that have a complete run, so the project
        list can enrich coverage scores with one query instead of one per row.
        """
        ...

    def scores_by_checksum(self, project_id: str) -> dict[str, CoverageRun]:
        """Latest complete run per ``mdl_checksum`` for the project.

        The coverage-label overlay (Feature B) joins this against each provenance
        entry's resulting ``mdl_checksum`` to annotate a version with its score —
        keyed on the MDL version (not docs), latest run wins per version.
        """
        ...

    def active_run(self, project_id: str) -> CoverageRun | None:
        """The newest pending/running run for the project, if any."""

    def find_complete(
        self, project_id: str, mdl_checksum: str, docs_checksum: str
    ) -> CoverageRun | None:
        """A completed run for an identical directory+docs version (idempotency)."""


class InMemoryCoverageRunStore:
    """Process-local coverage-run store guarded by a lock (tests/dev)."""

    def __init__(self) -> None:
        self._runs: dict[str, CoverageRun] = {}
        self._lock = threading.Lock()

    def create(
        self, *, project_id: str, owner_id: str, mdl_checksum: str, docs_checksum: str
    ) -> CoverageRun:
        run = CoverageRun(
            project_id=project_id,
            owner_id=owner_id,
            mdl_checksum=mdl_checksum,
            docs_checksum=docs_checksum,
            status="pending",
        )
        with self._lock:
            self._runs[run.id] = run
        return run.model_copy(deep=True)

    def get(self, run_id: str) -> CoverageRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise CoverageRunNotFoundError(run_id)
            return run.model_copy(deep=True)

    def claim(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise CoverageRunNotFoundError(run_id)
            if run.status != "pending":
                return False
            self._runs[run_id] = run.model_copy(
                update={"status": "running", "updated_at": _now()}
            )
            return True

    def report_progress(self, run_id: str, progress: CoverageProgress) -> CoverageRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise CoverageRunNotFoundError(run_id)
            updated = run.model_copy(
                update={"progress": progress, "updated_at": _now()}
            )
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def complete(
        self, run_id: str, report: CoverageReport, *, score: float
    ) -> CoverageRun:
        return self._update(run_id, status="complete", report=report, score=score)

    def fail(self, run_id: str, error: str) -> CoverageRun:
        return self._update(run_id, status="failed", error=error)

    def set_recovery(
        self, run_id: str, *, status: str, conversation_id: str | None = None
    ) -> CoverageRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise CoverageRunNotFoundError(run_id)
            update: dict[str, object] = {
                "recovery_status": status,
                "updated_at": _now(),
            }
            if conversation_id is not None:
                update["recovery_conversation_id"] = conversation_id
            updated = run.model_copy(update=update)
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def dismiss_recovery(self, run_id: str) -> CoverageRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise CoverageRunNotFoundError(run_id)
            now = _now()
            updated = run.model_copy(
                update={"recovery_dismissed_at": now, "updated_at": now}
            )
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def supersede(self, project_id: str, *, except_run_id: str | None = None) -> int:
        count = 0
        with self._lock:
            for run_id, run in list(self._runs.items()):
                if (
                    run.project_id == project_id
                    and run.status in _ACTIVE_STATES
                    and run_id != except_run_id
                ):
                    self._runs[run_id] = run.model_copy(
                        update={"status": "superseded", "updated_at": _now()}
                    )
                    count += 1
        return count

    def latest_complete(self, project_id: str) -> CoverageRun | None:
        with self._lock:
            runs = [
                run.model_copy(deep=True)
                for run in self._runs.values()
                if run.project_id == project_id and run.status == "complete"
            ]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        return runs[0] if runs else None

    def latest_complete_bulk(self, project_ids: list[str]) -> dict[str, CoverageRun]:
        wanted = set(project_ids)
        with self._lock:
            runs = [
                run.model_copy(deep=True)
                for run in self._runs.values()
                if run.project_id in wanted and run.status == "complete"
            ]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        latest: dict[str, CoverageRun] = {}
        for run in runs:  # newest-first: keep the first seen per project
            latest.setdefault(run.project_id, run)
        return latest

    def scores_by_checksum(self, project_id: str) -> dict[str, CoverageRun]:
        with self._lock:
            runs = [
                run.model_copy(deep=True)
                for run in self._runs.values()
                if run.project_id == project_id and run.status == "complete"
            ]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        latest: dict[str, CoverageRun] = {}
        for run in runs:  # newest-first: keep the first seen per checksum
            latest.setdefault(run.mdl_checksum, run)
        return latest

    def active_run(self, project_id: str) -> CoverageRun | None:
        with self._lock:
            runs = [
                run.model_copy(deep=True)
                for run in self._runs.values()
                if run.project_id == project_id and run.status in _ACTIVE_STATES
            ]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        return runs[0] if runs else None

    def find_complete(
        self, project_id: str, mdl_checksum: str, docs_checksum: str
    ) -> CoverageRun | None:
        with self._lock:
            runs = [
                run.model_copy(deep=True)
                for run in self._runs.values()
                if run.project_id == project_id
                and run.status == "complete"
                and run.mdl_checksum == mdl_checksum
                and run.docs_checksum == docs_checksum
            ]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        return runs[0] if runs else None

    def _update(
        self,
        run_id: str,
        *,
        status: str,
        report: CoverageReport | None = None,
        score: float | None = None,
        error: str | None = None,
    ) -> CoverageRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise CoverageRunNotFoundError(run_id)
            updated = run.model_copy(
                update={
                    "status": status,
                    "report": report if report is not None else run.report,
                    "score": score if score is not None else run.score,
                    # Terminal transition: live progress no longer applies.
                    "progress": None,
                    "error": error,
                    "updated_at": _now(),
                }
            )
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)


class SqlAlchemyCoverageRunStore:
    """SQLAlchemy-backed coverage-run store (cross-worker supersession lease)."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def create(
        self, *, project_id: str, owner_id: str, mdl_checksum: str, docs_checksum: str
    ) -> CoverageRun:
        run = CoverageRun(
            project_id=project_id,
            owner_id=owner_id,
            mdl_checksum=mdl_checksum,
            docs_checksum=docs_checksum,
            status="pending",
        )
        with self.session_factory() as session:
            session.add(_to_model(run))
            session.commit()
        return run.model_copy(deep=True)

    def get(self, run_id: str) -> CoverageRun:
        with self.session_factory() as session:
            model = session.get(AiAgentCoverageRun, run_id)
            if model is None:
                raise CoverageRunNotFoundError(run_id)
            return _from_model(model)

    def claim(self, run_id: str) -> bool:
        # Atomic compare-and-set: only the worker whose UPDATE flips pending→running
        # (rowcount == 1) owns the run; concurrent claimers see rowcount 0.
        with self.session_factory() as session:
            result = session.execute(
                update(AiAgentCoverageRun)
                .where(
                    AiAgentCoverageRun.id == run_id,
                    AiAgentCoverageRun.status == "pending",
                )
                .values(status="running", updated_at=_now())
            )
            session.commit()
            return bool(result.rowcount)

    def report_progress(self, run_id: str, progress: CoverageProgress) -> CoverageRun:
        with self.session_factory() as session:
            model = session.get(AiAgentCoverageRun, run_id)
            if model is None:
                raise CoverageRunNotFoundError(run_id)
            model.progress = progress.model_dump(mode="json")
            model.updated_at = _now()
            session.commit()
            return _from_model(model)

    def complete(
        self, run_id: str, report: CoverageReport, *, score: float
    ) -> CoverageRun:
        return self._update(run_id, status="complete", report=report, score=score)

    def fail(self, run_id: str, error: str) -> CoverageRun:
        return self._update(run_id, status="failed", error=error)

    def set_recovery(
        self, run_id: str, *, status: str, conversation_id: str | None = None
    ) -> CoverageRun:
        with self.session_factory() as session:
            model = session.get(AiAgentCoverageRun, run_id)
            if model is None:
                raise CoverageRunNotFoundError(run_id)
            model.recovery_status = status
            if conversation_id is not None:
                model.recovery_conversation_id = conversation_id
            model.updated_at = _now()
            session.commit()
            return _from_model(model)

    def dismiss_recovery(self, run_id: str) -> CoverageRun:
        with self.session_factory() as session:
            model = session.get(AiAgentCoverageRun, run_id)
            if model is None:
                raise CoverageRunNotFoundError(run_id)
            now = _now()
            model.recovery_dismissed_at = now
            model.updated_at = now
            session.commit()
            return _from_model(model)

    def supersede(self, project_id: str, *, except_run_id: str | None = None) -> int:
        with self.session_factory() as session:
            stmt = (
                update(AiAgentCoverageRun)
                .where(
                    AiAgentCoverageRun.project_id == project_id,
                    AiAgentCoverageRun.status.in_(_ACTIVE_STATES),
                )
                .values(status="superseded", updated_at=_now())
            )
            if except_run_id is not None:
                stmt = stmt.where(AiAgentCoverageRun.id != except_run_id)
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0)

    def latest_complete(self, project_id: str) -> CoverageRun | None:
        return self._newest(project_id, statuses=("complete",))

    def latest_complete_bulk(self, project_ids: list[str]) -> dict[str, CoverageRun]:
        if not project_ids:
            return {}
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentCoverageRun)
                    .where(
                        AiAgentCoverageRun.project_id.in_(project_ids),
                        AiAgentCoverageRun.status == "complete",
                    )
                    .order_by(AiAgentCoverageRun.created_at.desc())
                )
                .scalars()
                .all()
            )
        latest: dict[str, CoverageRun] = {}
        for model in models:  # newest-first: keep the first seen per project
            if model.project_id not in latest:
                latest[model.project_id] = _from_model(model)
        return latest

    def scores_by_checksum(self, project_id: str) -> dict[str, CoverageRun]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentCoverageRun)
                    .where(
                        AiAgentCoverageRun.project_id == project_id,
                        AiAgentCoverageRun.status == "complete",
                    )
                    .order_by(AiAgentCoverageRun.created_at.desc())
                )
                .scalars()
                .all()
            )
        latest: dict[str, CoverageRun] = {}
        for model in models:  # newest-first: keep the first seen per checksum
            if model.mdl_checksum not in latest:
                latest[model.mdl_checksum] = _from_model(model)
        return latest

    def active_run(self, project_id: str) -> CoverageRun | None:
        return self._newest(project_id, statuses=_ACTIVE_STATES)

    def find_complete(
        self, project_id: str, mdl_checksum: str, docs_checksum: str
    ) -> CoverageRun | None:
        with self.session_factory() as session:
            model = (
                session.execute(
                    select(AiAgentCoverageRun)
                    .where(
                        AiAgentCoverageRun.project_id == project_id,
                        AiAgentCoverageRun.status == "complete",
                        AiAgentCoverageRun.mdl_checksum == mdl_checksum,
                        AiAgentCoverageRun.docs_checksum == docs_checksum,
                    )
                    .order_by(AiAgentCoverageRun.created_at.desc())
                )
                .scalars()
                .first()
            )
            return _from_model(model) if model is not None else None

    def _newest(
        self, project_id: str, *, statuses: tuple[str, ...]
    ) -> CoverageRun | None:
        with self.session_factory() as session:
            model = (
                session.execute(
                    select(AiAgentCoverageRun)
                    .where(
                        AiAgentCoverageRun.project_id == project_id,
                        AiAgentCoverageRun.status.in_(statuses),
                    )
                    .order_by(AiAgentCoverageRun.created_at.desc())
                )
                .scalars()
                .first()
            )
            return _from_model(model) if model is not None else None

    def _update(
        self,
        run_id: str,
        *,
        status: str,
        report: CoverageReport | None = None,
        score: float | None = None,
        error: str | None = None,
    ) -> CoverageRun:
        with self.session_factory() as session:
            model = session.get(AiAgentCoverageRun, run_id)
            if model is None:
                raise CoverageRunNotFoundError(run_id)
            model.status = status
            if report is not None:
                model.report = report.model_dump(mode="json")
            if score is not None:
                model.score = score
            # Terminal transition: live progress no longer applies.
            model.progress = None
            model.error = error
            model.updated_at = _now()
            session.commit()
            return _from_model(model)


def _to_model(run: CoverageRun) -> AiAgentCoverageRun:
    return AiAgentCoverageRun(
        id=run.id,
        project_id=run.project_id,
        owner_id=run.owner_id,
        mdl_checksum=run.mdl_checksum,
        docs_checksum=run.docs_checksum,
        status=run.status,
        score=run.score,
        report=run.report.model_dump(mode="json") if run.report is not None else None,
        progress=(
            run.progress.model_dump(mode="json") if run.progress is not None else None
        ),
        recovery_status=run.recovery_status,
        recovery_conversation_id=run.recovery_conversation_id,
        recovery_dismissed_at=run.recovery_dismissed_at,
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _from_model(model: AiAgentCoverageRun) -> CoverageRun:
    return CoverageRun(
        id=model.id,
        project_id=model.project_id,
        owner_id=model.owner_id,
        mdl_checksum=model.mdl_checksum,
        docs_checksum=model.docs_checksum,
        status=model.status,
        score=model.score,
        report=(
            CoverageReport.model_validate(model.report)
            if model.report is not None
            else None
        ),
        progress=(
            CoverageProgress.model_validate(model.progress)
            if model.progress is not None
            else None
        ),
        recovery_status=model.recovery_status or "none",
        recovery_conversation_id=model.recovery_conversation_id,
        recovery_dismissed_at=model.recovery_dismissed_at,
        error=model.error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )
