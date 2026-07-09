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
"""Shared model client for the rig's judge (grading) and prepare (authoring) passes.

Built once from the agent's own ``.env`` via ``create_model_client(
AgentConfig.from_env())`` — the same provider/model/key the running agent uses, so
the judge and the generators never diverge from it and there is no second secret to
manage (plan §4, DP-2/DP-5). Importing this module requires the agent package on the
path (the rig runs in-repo — plan §4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_CACHED: Any | None = None


@dataclass(frozen=True)
class JudgeSettings:
    """Judge knobs, read from the agent config (``wren_benchmark_judge_*``)."""

    enabled: bool = True
    votes: int = 1
    model: str | None = None


def _agent_config() -> Any:
    from superset_ai_agent.config import AgentConfig  # noqa: PLC0415

    return AgentConfig.from_env()


def get_model_client(*, force_new: bool = False) -> Any:
    """Return a cached ``ModelClient`` built from the agent's env config.

    Cached because construction may open provider sessions; ``force_new`` rebuilds
    (e.g. after an env change in a long session).
    """

    global _CACHED
    if _CACHED is not None and not force_new:
        return _CACHED
    from superset_ai_agent.llm.factory import create_model_client  # noqa: PLC0415

    _CACHED = create_model_client(_agent_config())
    return _CACHED


def judge_settings() -> JudgeSettings:
    """Read judge settings from the agent config (falls back to safe defaults)."""

    cfg = _agent_config()
    return JudgeSettings(
        enabled=bool(getattr(cfg, "wren_benchmark_judge_enabled", True)),
        votes=int(getattr(cfg, "wren_benchmark_judge_votes", 1) or 1),
        model=getattr(cfg, "wren_benchmark_judge_model", None),
    )
