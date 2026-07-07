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

"""Storage for Project Benchmarks (benchmarks/items/runs/results/scores).

Mirrors ``semantic_layer/coverage_store.py``: a Protocol with an in-memory
implementation (tests/dev) and a SQLAlchemy implementation (durable,
cross-worker). Runs use the same compare-and-set ``claim`` lease and
``supersede`` semantics as coverage runs so two workers can't execute the same
run and a new submission cancels an in-flight one (I-3).
"""

from __future__ import annotations

import hashlib
import json  # noqa: TID251 - standalone agent, independent of Superset
import threading
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.evals.schemas import (
    Benchmark,
    BenchmarkItem,
    EvalResult,
    EvalRun,
    EvalScore,
    ResultVerdict,
    RunProgress,
    RunTotals,
)
from superset_ai_agent.persistence.models import (
    AiAgentEvalBenchmark,
    AiAgentEvalItem,
    AiAgentEvalResult,
    AiAgentEvalRun,
    AiAgentEvalScore,
)

_ACTIVE_STATES = ("pending", "running")


class BenchmarkNotFoundError(KeyError):
    """Raised when a benchmark id is unknown (or soft-deleted)."""


class BenchmarkItemNotFoundError(KeyError):
    """Raised when a benchmark item id is unknown (or soft-deleted)."""


class EvalRunNotFoundError(KeyError):
    """Raised when a run id is unknown."""


class EvalResultNotFoundError(KeyError):
    """Raised when a result id is unknown."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def compute_benchmark_checksum(items: list[BenchmarkItem]) -> str:
    """Stable content hash over the ordered item set (run pinning, I-1)."""

    payload = [
        {
            "id": item.id,
            "question": item.question,
            "answer_type": item.answer_type,
            "answer_spec": item.answer_spec,
        }
        for item in sorted(items, key=lambda i: (i.position, i.id))
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class EvalStore(Protocol):
    """Storage contract for benchmarks, items, runs, results, and scores."""

    # Benchmarks
    def create_benchmark(
        self, *, project_id: str, owner_id: str, name: str, description: str | None
    ) -> Benchmark: ...

    def get_benchmark(self, benchmark_id: str) -> Benchmark: ...

    def list_benchmarks(self, project_id: str) -> list[Benchmark]: ...

    def update_benchmark(
        self,
        benchmark_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Benchmark: ...

    def delete_benchmark(self, benchmark_id: str) -> None:
        """Soft delete (runs/results remain readable via their ids)."""

    # Items
    def add_item(self, item: BenchmarkItem) -> BenchmarkItem: ...

    def get_item(self, item_id: str) -> BenchmarkItem: ...

    def list_items(self, benchmark_id: str) -> list[BenchmarkItem]: ...

    def update_item(self, item: BenchmarkItem) -> BenchmarkItem: ...

    def delete_item(self, item_id: str) -> None:
        """Soft delete (past results keep their frozen copies)."""

    # Runs
    def create_run(self, run: EvalRun) -> EvalRun: ...

    def get_run(self, run_id: str) -> EvalRun: ...

    def list_runs(self, benchmark_id: str) -> list[EvalRun]: ...

    def claim_run(self, run_id: str) -> bool:
        """Atomically transition ``pending`` → ``running``; True if claimed."""

    def report_run_progress(self, run_id: str, progress: RunProgress) -> EvalRun: ...

    def complete_run(
        self, run_id: str, *, totals: RunTotals, score: float
    ) -> EvalRun: ...

    def fail_run(self, run_id: str, error: str) -> EvalRun: ...

    def supersede_runs(
        self,
        benchmark_id: str,
        *,
        except_run_id: str | None = None,
        except_run_ids: list[str] | None = None,
    ) -> int:
        """Mark in-flight runs superseded, sparing the given run id(s).

        ``except_run_ids`` exists for matrix submissions (P2.3): sibling runs
        of one batch must not cancel each other.
        """

    # Results + scores
    def add_result(self, result: EvalResult) -> EvalResult:
        """Persist one result together with its score rows."""

    def get_result(self, result_id: str) -> EvalResult: ...

    def list_results(self, run_id: str) -> list[EvalResult]: ...

    def override_result(
        self,
        result_id: str,
        *,
        verdict: ResultVerdict,
        by: str,
        comment: str | None,
    ) -> EvalResult: ...


class InMemoryEvalStore:
    """Process-local store guarded by a lock (tests/dev)."""

    def __init__(self) -> None:
        self._benchmarks: dict[str, Benchmark] = {}
        self._deleted_benchmarks: set[str] = set()
        self._items: dict[str, BenchmarkItem] = {}
        self._deleted_items: set[str] = set()
        self._runs: dict[str, EvalRun] = {}
        self._results: dict[str, EvalResult] = {}
        self._lock = threading.Lock()

    # -- benchmarks ----------------------------------------------------------

    def create_benchmark(
        self, *, project_id: str, owner_id: str, name: str, description: str | None
    ) -> Benchmark:
        benchmark = Benchmark(
            project_id=project_id,
            owner_id=owner_id,
            name=name,
            description=description,
        )
        with self._lock:
            self._benchmarks[benchmark.id] = benchmark
        return benchmark.model_copy(deep=True)

    def _get_benchmark_locked(self, benchmark_id: str) -> Benchmark:
        benchmark = self._benchmarks.get(benchmark_id)
        if benchmark is None or benchmark_id in self._deleted_benchmarks:
            raise BenchmarkNotFoundError(benchmark_id)
        return benchmark

    def get_benchmark(self, benchmark_id: str) -> Benchmark:
        with self._lock:
            benchmark = self._get_benchmark_locked(benchmark_id)
            return benchmark.model_copy(
                update={"item_count": self._item_count_locked(benchmark_id)}
            )

    def list_benchmarks(self, project_id: str) -> list[Benchmark]:
        with self._lock:
            benchmarks = [
                b.model_copy(update={"item_count": self._item_count_locked(b.id)})
                for b in self._benchmarks.values()
                if b.project_id == project_id and b.id not in self._deleted_benchmarks
            ]
        return sorted(benchmarks, key=lambda b: b.created_at)

    def update_benchmark(
        self,
        benchmark_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Benchmark:
        with self._lock:
            benchmark = self._get_benchmark_locked(benchmark_id)
            updates: dict[str, object] = {"updated_at": _now()}
            if name is not None:
                updates["name"] = name
            if description is not None:
                updates["description"] = description
            updated = benchmark.model_copy(update=updates)
            self._benchmarks[benchmark_id] = updated
            return updated.model_copy(
                update={"item_count": self._item_count_locked(benchmark_id)}
            )

    def delete_benchmark(self, benchmark_id: str) -> None:
        with self._lock:
            self._get_benchmark_locked(benchmark_id)
            self._deleted_benchmarks.add(benchmark_id)

    def _item_count_locked(self, benchmark_id: str) -> int:
        return sum(
            1
            for item in self._items.values()
            if item.benchmark_id == benchmark_id and item.id not in self._deleted_items
        )

    # -- items ----------------------------------------------------------------

    def add_item(self, item: BenchmarkItem) -> BenchmarkItem:
        with self._lock:
            self._items[item.id] = item
        return item.model_copy(deep=True)

    def get_item(self, item_id: str) -> BenchmarkItem:
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item_id in self._deleted_items:
                raise BenchmarkItemNotFoundError(item_id)
            return item.model_copy(deep=True)

    def list_items(self, benchmark_id: str) -> list[BenchmarkItem]:
        with self._lock:
            items = [
                item.model_copy(deep=True)
                for item in self._items.values()
                if item.benchmark_id == benchmark_id
                and item.id not in self._deleted_items
            ]
        return sorted(items, key=lambda i: (i.position, i.created_at))

    def update_item(self, item: BenchmarkItem) -> BenchmarkItem:
        with self._lock:
            if item.id not in self._items or item.id in self._deleted_items:
                raise BenchmarkItemNotFoundError(item.id)
            updated = item.model_copy(update={"updated_at": _now()})
            self._items[item.id] = updated
            return updated.model_copy(deep=True)

    def delete_item(self, item_id: str) -> None:
        with self._lock:
            if item_id not in self._items or item_id in self._deleted_items:
                raise BenchmarkItemNotFoundError(item_id)
            self._deleted_items.add(item_id)

    # -- runs ------------------------------------------------------------------

    def create_run(self, run: EvalRun) -> EvalRun:
        with self._lock:
            self._runs[run.id] = run
        return run.model_copy(deep=True)

    def get_run(self, run_id: str) -> EvalRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise EvalRunNotFoundError(run_id)
            return run.model_copy(deep=True)

    def list_runs(self, benchmark_id: str) -> list[EvalRun]:
        with self._lock:
            runs = [
                run.model_copy(deep=True)
                for run in self._runs.values()
                if run.benchmark_id == benchmark_id
            ]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    def claim_run(self, run_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise EvalRunNotFoundError(run_id)
            if run.status != "pending":
                return False
            self._runs[run_id] = run.model_copy(
                update={"status": "running", "updated_at": _now()}
            )
            return True

    def report_run_progress(self, run_id: str, progress: RunProgress) -> EvalRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise EvalRunNotFoundError(run_id)
            updated = run.model_copy(
                update={"progress": progress, "updated_at": _now()}
            )
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def complete_run(self, run_id: str, *, totals: RunTotals, score: float) -> EvalRun:
        return self._finish(run_id, status="complete", totals=totals, score=score)

    def fail_run(self, run_id: str, error: str) -> EvalRun:
        return self._finish(run_id, status="failed", error=error)

    def _finish(
        self,
        run_id: str,
        *,
        status: str,
        totals: RunTotals | None = None,
        score: float | None = None,
        error: str | None = None,
    ) -> EvalRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise EvalRunNotFoundError(run_id)
            updated = run.model_copy(
                update={
                    "status": status,
                    "totals": totals if totals is not None else run.totals,
                    "score": score if score is not None else run.score,
                    "progress": None,
                    "error": error,
                    "updated_at": _now(),
                }
            )
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    def supersede_runs(
        self,
        benchmark_id: str,
        *,
        except_run_id: str | None = None,
        except_run_ids: list[str] | None = None,
    ) -> int:
        spared = set(except_run_ids or [])
        if except_run_id is not None:
            spared.add(except_run_id)
        count = 0
        with self._lock:
            for run_id, run in list(self._runs.items()):
                if (
                    run.benchmark_id == benchmark_id
                    and run.status in _ACTIVE_STATES
                    and run_id not in spared
                ):
                    self._runs[run_id] = run.model_copy(
                        update={"status": "superseded", "updated_at": _now()}
                    )
                    count += 1
        return count

    # -- results ----------------------------------------------------------------

    def add_result(self, result: EvalResult) -> EvalResult:
        stored = result.model_copy(deep=True)
        for score in stored.scores:
            score.result_id = stored.id
        with self._lock:
            self._results[stored.id] = stored
        return stored.model_copy(deep=True)

    def get_result(self, result_id: str) -> EvalResult:
        with self._lock:
            result = self._results.get(result_id)
            if result is None:
                raise EvalResultNotFoundError(result_id)
            return result.model_copy(deep=True)

    def list_results(self, run_id: str) -> list[EvalResult]:
        with self._lock:
            results = [
                result.model_copy(deep=True)
                for result in self._results.values()
                if result.run_id == run_id
            ]
        return sorted(results, key=lambda r: (r.trial_index, r.created_at))

    def override_result(
        self,
        result_id: str,
        *,
        verdict: ResultVerdict,
        by: str,
        comment: str | None,
    ) -> EvalResult:
        with self._lock:
            result = self._results.get(result_id)
            if result is None:
                raise EvalResultNotFoundError(result_id)
            now = _now()
            updated = result.model_copy(
                update={
                    "override_verdict": verdict,
                    "override_by": by,
                    "override_at": now,
                    "override_comment": comment,
                }
            )
            updated.scores = list(updated.scores) + [
                EvalScore(
                    result_id=result_id,
                    name="human_override",
                    label=verdict,
                    explanation=comment,
                    source="human",
                )
            ]
            self._results[result_id] = updated
            return updated.model_copy(deep=True)


class SqlAlchemyEvalStore:
    """SQLAlchemy-backed store (durable, cross-worker CAS claim)."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    # -- benchmarks -------------------------------------------------------------

    def create_benchmark(
        self, *, project_id: str, owner_id: str, name: str, description: str | None
    ) -> Benchmark:
        benchmark = Benchmark(
            project_id=project_id,
            owner_id=owner_id,
            name=name,
            description=description,
        )
        with self.session_factory() as session:
            session.add(_benchmark_to_model(benchmark))
            session.commit()
        return benchmark.model_copy(deep=True)

    def get_benchmark(self, benchmark_id: str) -> Benchmark:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalBenchmark, benchmark_id)
            if model is None or model.deleted_at is not None:
                raise BenchmarkNotFoundError(benchmark_id)
            return _benchmark_from_model(
                model, item_count=self._item_count(session, benchmark_id)
            )

    def list_benchmarks(self, project_id: str) -> list[Benchmark]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentEvalBenchmark)
                    .where(
                        AiAgentEvalBenchmark.project_id == project_id,
                        AiAgentEvalBenchmark.deleted_at.is_(None),
                    )
                    .order_by(AiAgentEvalBenchmark.created_at)
                )
                .scalars()
                .all()
            )
            return [
                _benchmark_from_model(m, item_count=self._item_count(session, m.id))
                for m in models
            ]

    def update_benchmark(
        self,
        benchmark_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Benchmark:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalBenchmark, benchmark_id)
            if model is None or model.deleted_at is not None:
                raise BenchmarkNotFoundError(benchmark_id)
            if name is not None:
                model.name = name
            if description is not None:
                model.description = description
            model.updated_at = _now()
            session.commit()
            return _benchmark_from_model(
                model, item_count=self._item_count(session, benchmark_id)
            )

    def delete_benchmark(self, benchmark_id: str) -> None:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalBenchmark, benchmark_id)
            if model is None or model.deleted_at is not None:
                raise BenchmarkNotFoundError(benchmark_id)
            model.deleted_at = _now()
            session.commit()

    @staticmethod
    def _item_count(session: Session, benchmark_id: str) -> int:
        return (
            session.query(AiAgentEvalItem)
            .filter(
                AiAgentEvalItem.benchmark_id == benchmark_id,
                AiAgentEvalItem.deleted_at.is_(None),
            )
            .count()
        )

    # -- items --------------------------------------------------------------------

    def add_item(self, item: BenchmarkItem) -> BenchmarkItem:
        with self.session_factory() as session:
            session.add(_item_to_model(item))
            session.commit()
        return item.model_copy(deep=True)

    def get_item(self, item_id: str) -> BenchmarkItem:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalItem, item_id)
            if model is None or model.deleted_at is not None:
                raise BenchmarkItemNotFoundError(item_id)
            return _item_from_model(model)

    def list_items(self, benchmark_id: str) -> list[BenchmarkItem]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentEvalItem)
                    .where(
                        AiAgentEvalItem.benchmark_id == benchmark_id,
                        AiAgentEvalItem.deleted_at.is_(None),
                    )
                    .order_by(AiAgentEvalItem.position, AiAgentEvalItem.created_at)
                )
                .scalars()
                .all()
            )
            return [_item_from_model(m) for m in models]

    def update_item(self, item: BenchmarkItem) -> BenchmarkItem:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalItem, item.id)
            if model is None or model.deleted_at is not None:
                raise BenchmarkItemNotFoundError(item.id)
            model.position = item.position
            model.question = item.question
            model.answer_type = item.answer_type
            model.answer_spec = item.answer_spec
            model.capability_tags = item.capability_tags
            model.use_as_example = item.use_as_example
            model.verified_by = item.verified_by
            model.verified_at = item.verified_at
            model.updated_at = _now()
            session.commit()
            return _item_from_model(model)

    def delete_item(self, item_id: str) -> None:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalItem, item_id)
            if model is None or model.deleted_at is not None:
                raise BenchmarkItemNotFoundError(item_id)
            model.deleted_at = _now()
            session.commit()

    # -- runs -----------------------------------------------------------------------

    def create_run(self, run: EvalRun) -> EvalRun:
        with self.session_factory() as session:
            session.add(_run_to_model(run))
            session.commit()
        return run.model_copy(deep=True)

    def get_run(self, run_id: str) -> EvalRun:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalRun, run_id)
            if model is None:
                raise EvalRunNotFoundError(run_id)
            return _run_from_model(model)

    def list_runs(self, benchmark_id: str) -> list[EvalRun]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentEvalRun)
                    .where(AiAgentEvalRun.benchmark_id == benchmark_id)
                    .order_by(AiAgentEvalRun.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [_run_from_model(m) for m in models]

    def claim_run(self, run_id: str) -> bool:
        with self.session_factory() as session:
            result = session.execute(
                update(AiAgentEvalRun)
                .where(
                    AiAgentEvalRun.id == run_id,
                    AiAgentEvalRun.status == "pending",
                )
                .values(status="running", updated_at=_now())
            )
            session.commit()
            return bool(result.rowcount)

    def report_run_progress(self, run_id: str, progress: RunProgress) -> EvalRun:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalRun, run_id)
            if model is None:
                raise EvalRunNotFoundError(run_id)
            model.progress = progress.model_dump(mode="json")
            model.updated_at = _now()
            session.commit()
            return _run_from_model(model)

    def complete_run(self, run_id: str, *, totals: RunTotals, score: float) -> EvalRun:
        return self._finish(run_id, status="complete", totals=totals, score=score)

    def fail_run(self, run_id: str, error: str) -> EvalRun:
        return self._finish(run_id, status="failed", error=error)

    def _finish(
        self,
        run_id: str,
        *,
        status: str,
        totals: RunTotals | None = None,
        score: float | None = None,
        error: str | None = None,
    ) -> EvalRun:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalRun, run_id)
            if model is None:
                raise EvalRunNotFoundError(run_id)
            model.status = status
            if totals is not None:
                model.totals = totals.model_dump(mode="json")
            if score is not None:
                model.score = score
            model.progress = None
            model.error = error
            model.updated_at = _now()
            session.commit()
            return _run_from_model(model)

    def supersede_runs(
        self,
        benchmark_id: str,
        *,
        except_run_id: str | None = None,
        except_run_ids: list[str] | None = None,
    ) -> int:
        spared = set(except_run_ids or [])
        if except_run_id is not None:
            spared.add(except_run_id)
        with self.session_factory() as session:
            stmt = (
                update(AiAgentEvalRun)
                .where(
                    AiAgentEvalRun.benchmark_id == benchmark_id,
                    AiAgentEvalRun.status.in_(_ACTIVE_STATES),
                )
                .values(status="superseded", updated_at=_now())
            )
            if spared:
                stmt = stmt.where(AiAgentEvalRun.id.notin_(spared))
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0)

    # -- results ----------------------------------------------------------------------

    def add_result(self, result: EvalResult) -> EvalResult:
        stored = result.model_copy(deep=True)
        with self.session_factory() as session:
            session.add(_result_to_model(stored))
            for score in stored.scores:
                score.result_id = stored.id
                session.add(_score_to_model(score))
            session.commit()
        return stored

    def get_result(self, result_id: str) -> EvalResult:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalResult, result_id)
            if model is None:
                raise EvalResultNotFoundError(result_id)
            return _result_from_model(model, self._scores(session, [result_id]))

    def list_results(self, run_id: str) -> list[EvalResult]:
        with self.session_factory() as session:
            models = (
                session.execute(
                    select(AiAgentEvalResult)
                    .where(AiAgentEvalResult.run_id == run_id)
                    .order_by(
                        AiAgentEvalResult.trial_index, AiAgentEvalResult.created_at
                    )
                )
                .scalars()
                .all()
            )
            score_map = self._scores(session, [m.id for m in models])
            return [_result_from_model(m, score_map) for m in models]

    def override_result(
        self,
        result_id: str,
        *,
        verdict: ResultVerdict,
        by: str,
        comment: str | None,
    ) -> EvalResult:
        with self.session_factory() as session:
            model = session.get(AiAgentEvalResult, result_id)
            if model is None:
                raise EvalResultNotFoundError(result_id)
            now = _now()
            model.override_verdict = verdict
            model.override_by = by
            model.override_at = now
            model.override_comment = comment
            session.add(
                _score_to_model(
                    EvalScore(
                        result_id=result_id,
                        name="human_override",
                        label=verdict,
                        explanation=comment,
                        source="human",
                    )
                )
            )
            session.commit()
            return _result_from_model(model, self._scores(session, [result_id]))

    @staticmethod
    def _scores(session: Session, result_ids: list[str]) -> dict[str, list[EvalScore]]:
        if not result_ids:
            return {}
        models = (
            session.execute(
                select(AiAgentEvalScore)
                .where(AiAgentEvalScore.result_id.in_(result_ids))
                .order_by(AiAgentEvalScore.created_at)
            )
            .scalars()
            .all()
        )
        out: dict[str, list[EvalScore]] = {}
        for model in models:
            out.setdefault(model.result_id, []).append(_score_from_model(model))
        return out


# --- model mapping -----------------------------------------------------------


def _benchmark_to_model(benchmark: Benchmark) -> AiAgentEvalBenchmark:
    return AiAgentEvalBenchmark(
        id=benchmark.id,
        project_id=benchmark.project_id,
        owner_id=benchmark.owner_id,
        name=benchmark.name,
        description=benchmark.description,
        created_at=benchmark.created_at,
        updated_at=benchmark.updated_at,
    )


def _benchmark_from_model(
    model: AiAgentEvalBenchmark, *, item_count: int = 0
) -> Benchmark:
    return Benchmark(
        id=model.id,
        project_id=model.project_id,
        owner_id=model.owner_id,
        name=model.name,
        description=model.description,
        created_at=model.created_at,
        updated_at=model.updated_at,
        item_count=item_count,
    )


def _item_to_model(item: BenchmarkItem) -> AiAgentEvalItem:
    return AiAgentEvalItem(
        id=item.id,
        benchmark_id=item.benchmark_id,
        position=item.position,
        question=item.question,
        answer_type=item.answer_type,
        answer_spec=item.answer_spec,
        capability_tags=item.capability_tags,
        use_as_example=item.use_as_example,
        verified_by=item.verified_by,
        verified_at=item.verified_at,
        created_by=item.created_by,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _item_from_model(model: AiAgentEvalItem) -> BenchmarkItem:
    return BenchmarkItem(
        id=model.id,
        benchmark_id=model.benchmark_id,
        position=model.position,
        question=model.question,
        answer_type=model.answer_type,  # type: ignore[arg-type]
        answer_spec=model.answer_spec,
        capability_tags=model.capability_tags or [],
        use_as_example=model.use_as_example,
        verified_by=model.verified_by,
        verified_at=model.verified_at,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _run_to_model(run: EvalRun) -> AiAgentEvalRun:
    return AiAgentEvalRun(
        id=run.id,
        benchmark_id=run.benchmark_id,
        project_id=run.project_id,
        owner_id=run.owner_id,
        status=run.status,
        trials=run.trials,
        config=run.config,
        mdl_checksum=run.mdl_checksum,
        benchmark_checksum=run.benchmark_checksum,
        database_id=run.database_id,
        score=run.score,
        totals=run.totals.model_dump(mode="json") if run.totals else None,
        progress=run.progress.model_dump(mode="json") if run.progress else None,
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def _run_from_model(model: AiAgentEvalRun) -> EvalRun:
    return EvalRun(
        id=model.id,
        benchmark_id=model.benchmark_id,
        project_id=model.project_id,
        owner_id=model.owner_id,
        status=model.status,  # type: ignore[arg-type]
        trials=model.trials,
        config=model.config or {},
        mdl_checksum=model.mdl_checksum,
        benchmark_checksum=model.benchmark_checksum or "",
        database_id=model.database_id,
        score=model.score,
        totals=RunTotals.model_validate(model.totals) if model.totals else None,
        progress=(
            RunProgress.model_validate(model.progress) if model.progress else None
        ),
        error=model.error,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _result_to_model(result: EvalResult) -> AiAgentEvalResult:
    return AiAgentEvalResult(
        id=result.id,
        run_id=result.run_id,
        item_id=result.item_id,
        trial_index=result.trial_index,
        question=result.question,
        answer_type=result.answer_type,
        answer_spec=result.answer_spec,
        agent_sql=result.agent_sql,
        agent_status=result.agent_status,
        agent_rows_preview=result.agent_rows_preview,
        gold_rows_preview=result.gold_rows_preview,
        verdict=result.verdict,
        verdict_source=result.verdict_source,
        reasons=result.reasons,
        matched_models=result.matched_models,
        duration_ms=result.duration_ms,
        override_verdict=result.override_verdict,
        override_by=result.override_by,
        override_at=result.override_at,
        override_comment=result.override_comment,
        created_at=result.created_at,
    )


def _result_from_model(
    model: AiAgentEvalResult, score_map: dict[str, list[EvalScore]]
) -> EvalResult:
    return EvalResult(
        id=model.id,
        run_id=model.run_id,
        item_id=model.item_id,
        trial_index=model.trial_index,
        question=model.question,
        answer_type=model.answer_type,  # type: ignore[arg-type]
        answer_spec=model.answer_spec,
        agent_sql=model.agent_sql,
        agent_status=model.agent_status,
        agent_rows_preview=model.agent_rows_preview,
        gold_rows_preview=model.gold_rows_preview,
        verdict=model.verdict,  # type: ignore[arg-type]
        verdict_source=model.verdict_source,  # type: ignore[arg-type]
        reasons=model.reasons or [],
        matched_models=model.matched_models,
        duration_ms=model.duration_ms,
        override_verdict=model.override_verdict,  # type: ignore[arg-type]
        override_by=model.override_by,
        override_at=model.override_at,
        override_comment=model.override_comment,
        created_at=model.created_at,
        scores=score_map.get(model.id, []),
    )


def _score_to_model(score: EvalScore) -> AiAgentEvalScore:
    return AiAgentEvalScore(
        id=score.id,
        result_id=score.result_id,
        name=score.name,
        value=score.value,
        label=score.label,
        explanation=score.explanation,
        source=score.source,
        created_at=score.created_at,
    )


def _score_from_model(model: AiAgentEvalScore) -> EvalScore:
    return EvalScore(
        id=model.id,
        result_id=model.result_id,
        name=model.name,
        value=model.value,
        label=model.label,
        explanation=model.explanation,
        source=model.source,  # type: ignore[arg-type]
        created_at=model.created_at,
    )
