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

"""Coverage-detector eval harness.

Scores a coverage detector's findings against gold labels (per-claim status),
so the extract+judge pipeline can be evaluated against a real model offline. The
scoring itself is deterministic and unit-tested; the gold fixtures + a live-model
runner are the deployment-side complement (run with a real ``ModelClient``, then
``score_coverage(report.findings, gold)``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from superset_ai_agent.semantic_layer.copilot.schemas import (
    CoverageFinding,
    CoverageStatus,
)

_STATUSES: tuple[CoverageStatus, ...] = ("covered", "partial", "missing")


class GoldLabel(BaseModel):
    """The expected coverage status for one claim, keyed by its statement."""

    statement: str
    status: CoverageStatus


class StatusMetric(BaseModel):
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    support: int = 0


class CoverageEvalMetrics(BaseModel):
    """Accuracy + per-status precision/recall/F1 for a detector run."""

    total: int = 0
    matched: int = 0
    correct: int = 0
    accuracy: float = 0.0
    per_status: dict[str, StatusMetric] = Field(default_factory=dict)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def score_coverage(
    predicted: list[CoverageFinding],
    gold: list[GoldLabel],
) -> CoverageEvalMetrics:
    """Score predictions against gold, matching by normalized claim statement.

    Unmatched gold claims count as errors (the detector failed to surface them).
    Per-status precision/recall/F1 are computed over the matched set plus the
    unmatched-gold penalty on recall.
    """

    pred_by_stmt = {_normalize(f.claim.statement): f.status for f in predicted}
    tp: dict[str, int] = dict.fromkeys(_STATUSES, 0)
    fp: dict[str, int] = dict.fromkeys(_STATUSES, 0)
    fn: dict[str, int] = dict.fromkeys(_STATUSES, 0)
    support: dict[str, int] = dict.fromkeys(_STATUSES, 0)

    matched = 0
    correct = 0
    for label in gold:
        support[label.status] += 1
        predicted_status = pred_by_stmt.get(_normalize(label.statement))
        if predicted_status is None:
            fn[label.status] += 1  # detector missed this claim entirely
            continue
        matched += 1
        if predicted_status == label.status:
            correct += 1
            tp[label.status] += 1
        else:
            fn[label.status] += 1
            fp[predicted_status] += 1

    per_status: dict[str, StatusMetric] = {}
    for status in _STATUSES:
        precision = (
            tp[status] / (tp[status] + fp[status]) if tp[status] + fp[status] else 0.0
        )
        recall = (
            tp[status] / (tp[status] + fn[status]) if tp[status] + fn[status] else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        per_status[status] = StatusMetric(
            precision=round(precision, 3),
            recall=round(recall, 3),
            f1=round(f1, 3),
            support=support[status],
        )

    total = len(gold)
    return CoverageEvalMetrics(
        total=total,
        matched=matched,
        correct=correct,
        accuracy=round(correct / total, 3) if total else 0.0,
        per_status=per_status,
    )
