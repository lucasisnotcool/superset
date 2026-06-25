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

"""The MDL Copilot agentic edit loop.

A bounded tool-calling loop that lets the model CRUD MDL via the toolset, runs
engine validation, and feeds structured errors back to self-correct — the
generalization of ``llm_client._draft_with_correction`` from one-document/one-file
to a multi-file changeset (see ``wren_mdl_copilot.md`` §3.1). The loop never
persists; it returns a reviewable ``Changeset``.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
import logging
from collections.abc import Callable
from typing import Any, Literal

from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import AgentStep
from superset_ai_agent.semantic_layer.copilot.schemas import Changeset
from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset

logger = logging.getLogger(__name__)

#: Progress callback signature: receives each emitted AgentStep as it happens.
StepSink = Callable[[AgentStep], None]


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)…"


def build_system_prompt(
    *,
    instructions: list[str] | None = None,
    skills: list[str] | None = None,
) -> str:
    """Assemble the effective system prompt: base + skills + project instructions."""

    base = get_prompt("mdl_copilot")
    blocks = [base]
    if skills:
        blocks.append("## Skills\n" + "\n\n".join(skills))
    if instructions:
        rendered = "\n".join(f"- {item}" for item in instructions if item.strip())
        if rendered:
            blocks.append(
                "## Operator instructions for this schema\n"
                "Follow these unless they conflict with the hard rules.\n" + rendered
            )
    return "\n\n".join(blocks)


def run_copilot_loop(  # noqa: C901 - a tool-call+correction loop is irreducibly branchy
    *,
    model_client: ModelClient,
    toolset: MdlToolset,
    user_message: str,
    attachments_text: str = "",
    instructions: list[str] | None = None,
    skills: list[str] | None = None,
    model: str | None = None,
    max_steps: int = 8,
    max_correction_retries: int = 1,
    on_step: StepSink | None = None,
) -> Changeset:
    """Run the bounded agentic edit loop and return a reviewable changeset."""

    steps: list[AgentStep] = []

    def emit(step: AgentStep) -> None:
        steps.append(step)
        if on_step is not None:
            try:
                on_step(step)
            except Exception:  # pylint: disable=broad-except
                logger.debug("Copilot step sink raised; continuing.", exc_info=True)

    specs = toolset.specs()
    system_prompt = build_system_prompt(instructions=instructions, skills=skills)

    user_content = user_message
    if attachments_text:
        user_content += "\n\n## Attached files\n" + attachments_text

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_content),
    ]

    emit(AgentStep(kind="copilot_plan", summary="Planning MDL edits", status="ok"))

    final_text = ""
    corrections = 0
    steps_taken = 0
    tools_unsupported = False

    while steps_taken < max_steps:
        steps_taken += 1
        try:
            result = model_client.chat(messages, tools=specs, model=model)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Copilot model call failed: %s", ex)
            emit(
                AgentStep(
                    kind="copilot_error",
                    summary=f"Model call failed: {ex}",
                    status="error",
                )
            )
            break

        if result.tool_calls:
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=result.content or "",
                    tool_calls=result.tool_calls,
                )
            )
            for call in result.tool_calls:
                output = toolset.dispatch(call.name, call.arguments)
                status: Literal["ok", "warning", "error"] = (
                    "error" if "error" in output else "ok"
                )
                emit(
                    AgentStep(
                        kind="copilot_tool",
                        summary=f"{call.name}({_arg_label(call.arguments)})",
                        status=status,
                    )
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=_truncate(json.dumps(output, default=str)),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
            continue

        # No tool calls: the model produced a final answer. Validate and either
        # accept or feed errors back (bounded correction).
        final_text = result.content or ""
        if steps_taken == 1 and not final_text.strip():
            # A provider with no tool-calling returned nothing actionable.
            tools_unsupported = True
            break

        validation = toolset.validate_working()
        errors = [m for m in validation.messages if m.severity == "error"]
        if validation.valid or not errors or corrections >= max_correction_retries:
            emit(
                AgentStep(
                    kind="copilot_validate",
                    summary=(
                        "Project valid"
                        if validation.valid
                        else f"Finalized with {len(errors)} issue(s)"
                    ),
                    status="ok" if validation.valid else "warning",
                )
            )
            break

        corrections += 1
        emit(
            AgentStep(
                kind="copilot_correct",
                summary=f"Validation failed; correcting (pass {corrections})",
                status="warning",
                attempt_index=corrections,
            )
        )
        messages.append(ChatMessage(role="assistant", content=final_text))
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    "validate_project reported errors. Fix exactly these and "
                    "finalize:\n" + "\n".join(f"- {m.message}" for m in errors)
                ),
            )
        )

    changeset = toolset.build_changeset(message=final_text)
    changeset.steps = steps
    if tools_unsupported:
        changeset.warnings.append(
            "The configured model did not return tool calls; no edits were "
            "proposed. Tool-calling is required for agentic MDL editing."
        )
    return changeset


def _arg_label(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    return str(path) if path else ""
