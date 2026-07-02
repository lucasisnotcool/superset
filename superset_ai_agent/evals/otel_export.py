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

"""OTel GenAI export for benchmark scores (F8/P3.3, interop escape hatch).

Serializes stored eval scores as ``gen_ai.evaluation.result`` events per the
OpenTelemetry GenAI semantic conventions (semconv >= 1.39): attributes
``gen_ai.evaluation.name`` / ``.score.value`` / ``.score.label`` /
``.explanation``. Our score rows were shaped onto these fields at the schema
level (P0.1), so this is a serializer, not a remodel. Consumers (Langfuse,
Phoenix, any OTLP collector) can ingest the JSON event list with a thin
adapter; a full OTLP/HTTP push stays an operator-side concern.
"""

from __future__ import annotations

from typing import Any

from superset_ai_agent.evals.schemas import EvalResult, EvalRun


def run_to_evaluation_events(
    run: EvalRun, results: list[EvalResult]
) -> list[dict[str, Any]]:
    """Flatten a run's scores into ``gen_ai.evaluation.result`` event dicts."""

    events: list[dict[str, Any]] = []
    for result in results:
        for score in result.scores:
            attributes: dict[str, Any] = {
                "gen_ai.evaluation.name": score.name,
                "gen_ai.evaluation.score.label": score.label,
                "gen_ai.evaluation.explanation": score.explanation,
                # Non-semconv correlation attributes (namespaced to the agent).
                "superset_ai_agent.eval.run_id": run.id,
                "superset_ai_agent.eval.result_id": result.id,
                "superset_ai_agent.eval.item_id": result.item_id,
                "superset_ai_agent.eval.trial_index": result.trial_index,
                "superset_ai_agent.eval.benchmark_id": run.benchmark_id,
                "superset_ai_agent.eval.project_id": run.project_id,
                "superset_ai_agent.eval.score_source": score.source,
            }
            if score.value is not None:
                attributes["gen_ai.evaluation.score.value"] = score.value
            events.append(
                {
                    "name": "gen_ai.evaluation.result",
                    "timestamp": score.created_at.isoformat(),
                    "attributes": {
                        key: value
                        for key, value in attributes.items()
                        if value is not None
                    },
                }
            )
    return events
