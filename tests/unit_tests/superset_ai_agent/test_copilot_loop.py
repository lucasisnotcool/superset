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

"""MDL Copilot agentic loop — tool-call, correction, and degrade paths (Phase 3)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.llm.base import ChatMessage, ModelResult, ToolCall, ToolSpec
from superset_ai_agent.semantic_layer.copilot.loop import run_copilot_loop
from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex

SCHEMA = SchemaIndex.from_snapshot({"orders": ["id", "amount"]})

VALID = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [{"name": "id", "type": "BIGINT"}],
            }
        ]
    }
)

INVALID = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [{"name": "id"}],  # missing required "type"
            }
        ]
    }
)


class ScriptedModel:
    """Returns a pre-scripted sequence of ModelResults, ignoring inputs."""

    def __init__(self, results: list[ModelResult]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> ModelResult:
        self.calls.append({"messages": messages, "tools": tools})
        return self._results.pop(0)

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[Any]:
        return []


def _write_call(path: str, content: str) -> ModelResult:
    return ModelResult(
        content="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="write_mdl_file",
                arguments={"path": path, "content": content},
            )
        ],
    )


def test_loop_creates_file_via_tool_call() -> None:
    model = ScriptedModel(
        [
            _write_call("models/orders.json", VALID),
            ModelResult(content="Added the orders model."),
        ]
    )
    toolset = MdlToolset([], schema_index=SCHEMA)

    changeset = run_copilot_loop(
        model_client=model,
        toolset=toolset,
        user_message="model the orders table",
    )

    assert changeset.message == "Added the orders model."
    assert len(changeset.items) == 1
    assert changeset.items[0].op == "create"
    assert changeset.manifest_validation.valid is True
    # tools were offered to the model
    assert model.calls[0]["tools"]
    kinds = [s.kind for s in changeset.steps]
    assert "copilot_plan" in kinds
    assert "copilot_tool" in kinds
    assert "copilot_validate" in kinds


def test_loop_runs_correction_when_validation_fails() -> None:
    model = ScriptedModel(
        [
            _write_call("models/orders.json", INVALID),  # bad: column without type
            ModelResult(content="done"),  # triggers validate -> fails -> correct
            _write_call("models/orders.json", VALID),  # fix
            ModelResult(content="Fixed the column type."),
        ]
    )
    toolset = MdlToolset([], schema_index=SCHEMA)

    changeset = run_copilot_loop(
        model_client=model,
        toolset=toolset,
        user_message="model orders",
        max_correction_retries=1,
    )

    assert changeset.manifest_validation.valid is True
    assert changeset.items[0].op == "create"
    assert any(s.kind == "copilot_correct" for s in changeset.steps)


def test_loop_degrades_when_model_emits_no_tool_calls() -> None:
    model = ScriptedModel([ModelResult(content="")])
    toolset = MdlToolset([], schema_index=SCHEMA)

    changeset = run_copilot_loop(
        model_client=model, toolset=toolset, user_message="do something"
    )

    assert changeset.items == []
    assert any("tool-calling is required" in w.lower() for w in changeset.warnings)


def test_loop_emits_progress_via_on_step() -> None:
    model = ScriptedModel(
        [_write_call("models/orders.json", VALID), ModelResult(content="ok")]
    )
    toolset = MdlToolset([], schema_index=SCHEMA)
    seen: list[str] = []

    run_copilot_loop(
        model_client=model,
        toolset=toolset,
        user_message="model orders",
        on_step=lambda step: seen.append(step.kind),
    )

    assert "copilot_tool" in seen
