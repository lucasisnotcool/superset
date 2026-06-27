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

# A relationship emitted as a model (no tableReference/refSql, no columns) — the
# exact failure from the field report. Under strict_models the proposal-time
# validation errors, so the loop must self-correct (P1/P2).
RELATIONSHIP_AS_MODEL = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [{"name": "id", "type": "BIGINT"}],
            },
            {"name": "orders_to_customers"},  # relationship masquerading as a model
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


def test_loop_self_corrects_relationship_emitted_as_model() -> None:
    # The model first emits a join as a model (orders_to_customers, no mapping/
    # columns); strict_models makes proposal-time validation error, so the loop
    # feeds it back and the model fixes it — before the user ever sees the
    # changeset (so it can't be applied and fail at activation).
    model = ScriptedModel(
        [
            _write_call("models/orders.json", RELATIONSHIP_AS_MODEL),
            ModelResult(content="done"),  # validate -> error -> correct
            _write_call("models/orders.json", VALID),  # moved the join out
            ModelResult(content="Moved the join into relationships."),
        ]
    )
    toolset = MdlToolset([], schema_index=SCHEMA)

    changeset = run_copilot_loop(
        model_client=model,
        toolset=toolset,
        user_message="model orders and its relationship to customers",
        max_correction_retries=1,
    )

    assert changeset.manifest_validation.valid is True
    assert any(s.kind == "copilot_correct" for s in changeset.steps)
    # The correction prompt carried the actionable relationship guidance.
    correction_msgs = [
        m.content
        for call in model.calls
        for m in call["messages"]
        if "relationships[]" in (m.content or "")
    ]
    assert correction_msgs


def test_loop_degrades_when_model_emits_no_tool_calls() -> None:
    model = ScriptedModel([ModelResult(content="")])
    toolset = MdlToolset([], schema_index=SCHEMA)

    changeset = run_copilot_loop(
        model_client=model, toolset=toolset, user_message="do something"
    )

    assert changeset.items == []
    assert any("tool-calling is required" in w.lower() for w in changeset.warnings)


def test_loop_prepends_history_after_system_before_user() -> None:
    model = ScriptedModel([ModelResult(content="ok")])
    toolset = MdlToolset([], schema_index=SCHEMA)
    history = [
        ChatMessage(role="user", content="add an orders model"),
        ChatMessage(role="assistant", content="created models/orders.json"),
    ]

    run_copilot_loop(
        model_client=model,
        toolset=toolset,
        user_message="now also add a synonym",
        history=history,
    )

    sent = model.calls[0]["messages"]
    roles = [m.role for m in sent]
    # system, prior user, prior assistant, then the fresh user turn.
    assert roles[0] == "system"
    assert roles[1:4] == ["user", "assistant", "user"]
    assert sent[1].content == "add an orders model"
    assert sent[2].content == "created models/orders.json"
    assert sent[3].content.startswith("now also add a synonym")


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
