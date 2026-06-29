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


def _truncate(text: str, limit: int | None = 4000) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + "\n…(truncated)…"


#: Tool results are truncated before re-entering the model to bound context cost.
#: But reads and validation must NOT be silently cut: the agent reasons over their
#: full content and, for write/patch, must reproduce it — a truncated read is the
#: correctness hazard this exempts (a model file >~4 KB used to be cut mid-JSON
#: while the agent was told to re-emit it whole). Large physical-schema dumps keep
#: a higher cap (``find_tables`` is the targeted alternative); the rest use the
#: configured default.
_UNTRUNCATED_RESULT_TOOLS = frozenset(
    {"read_mdl_file", "read_document", "validate_project"}
)
_HIGH_LIMIT_RESULT_TOOLS = frozenset({"get_physical_schema", "find_tables"})
_HIGH_LIMIT_MULTIPLIER = 6


def _result_limit(tool_name: str, default: int) -> int | None:
    """Per-tool truncation cap for a tool result (``None`` = do not truncate)."""

    if tool_name in _UNTRUNCATED_RESULT_TOOLS:
        return None
    if tool_name in _HIGH_LIMIT_RESULT_TOOLS:
        return default * _HIGH_LIMIT_MULTIPLIER
    return default


#: Active-mode banner injected into the system prompt so the agent knows which
#: posture the enrich-context skill's Step 0 selects. Without this the resolved
#: ``wren_copilot_autopilot_enabled`` flag never reaches the model and it defaults
#: to the cautious (grill) reading — see plan_copilot_enrichment_assertiveness.md RC1.
_MODE_BLOCK = {
    "grill": (
        "## Active mode\n"
        "MODE = grill. Propose each change and wait for the human to accept, edit, "
        "or skip — one decision at a time. Do not batch-apply inferences."
    ),
    "autopilot": (
        "## Active mode\n"
        "MODE = autopilot. Make your best-effort inferences and **propose them "
        "directly in the changeset** — including new relationships and metrics. The "
        "human accept/reject step on the changeset is the review gate, so you do not "
        "ask first for those. Still escalate only genuine conflicts (a document "
        "disagreeing with current MDL) and routing ambiguity; tag every inference "
        "with confidence and source."
    ),
}


def build_system_prompt(
    *,
    instructions: list[str] | None = None,
    skills: list[str] | None = None,
    mode: str = "grill",
) -> str:
    """Assemble the effective system prompt: base + mode + skills + instructions.

    ``mode`` is ``"grill"`` or ``"autopilot"`` (resolved from
    ``wren_copilot_autopilot_enabled``); it renders an ``## Active mode`` banner so
    the enrich-context skill's mode branch is actually selectable by the model.
    """

    base = get_prompt("mdl_copilot")
    blocks = [base, _MODE_BLOCK.get(mode, _MODE_BLOCK["grill"])]
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
    history: list[ChatMessage] | None = None,
    model: str | None = None,
    max_steps: int = 8,
    max_correction_retries: int = 1,
    tool_result_max_chars: int = 4000,
    autopilot: bool = False,
    on_step: StepSink | None = None,
) -> Changeset:
    """Run the bounded agentic edit loop and return a reviewable changeset.

    ``history`` carries prior conversation turns (multi-turn memory). It is
    prepended after the system prompt and before the new user turn, so the model
    sees the running thread without changing the base prompt contract.
    """

    steps: list[AgentStep] = []

    def emit(step: AgentStep) -> None:
        steps.append(step)
        if on_step is not None:
            try:
                on_step(step)
            except Exception:  # pylint: disable=broad-except
                logger.debug("Copilot step sink raised; continuing.", exc_info=True)

    specs = toolset.specs()
    system_prompt = build_system_prompt(
        instructions=instructions,
        skills=skills,
        mode="autopilot" if autopilot else "grill",
    )

    user_content = user_message
    if attachments_text:
        user_content += "\n\n## Attached files\n" + attachments_text

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt),
        *(history or []),
        ChatMessage(role="user", content=user_content),
    ]

    emit(AgentStep(kind="copilot_plan", summary="Planning MDL edits", status="ok"))

    final_text = ""
    corrections = 0
    steps_taken = 0
    tools_unsupported = False
    finalized = False
    model_failed = False

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
            model_failed = True
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
                        content=_truncate(
                            json.dumps(output, default=str),
                            _result_limit(call.name, tool_result_max_chars),
                        ),
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
            finalized = True
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

    if not finalized and not tools_unsupported and not model_failed:
        # Step budget exhausted while the model was still mid-edit. Force one
        # tool-free turn so it writes a closing summary instead of leaving an
        # empty message, then validate the partial working set and flag it so the
        # (possibly incomplete) changeset is clearly reviewable and re-runnable.
        if not final_text.strip():
            try:
                closing = model_client.chat(messages, tools=None, model=model)
                final_text = closing.content or final_text
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Copilot finalize call failed: %s", ex)
        emit(
            AgentStep(
                kind="copilot_validate",
                summary=f"Stopped at the {max_steps}-step limit",
                status="warning",
            )
        )

    changeset = toolset.build_changeset(message=final_text)
    changeset.steps = steps
    if tools_unsupported:
        changeset.warnings.append(
            "The configured model did not return tool calls; no edits were "
            "proposed. Tool-calling is required for agentic MDL editing."
        )
    elif not finalized and not model_failed:
        changeset.warnings.append(
            f"Reached the {max_steps}-step tool budget before finishing. The "
            "proposals may be incomplete — accept what is correct, then re-run "
            "to continue (or raise WREN_COPILOT_MAX_STEPS)."
        )
    return changeset


def _arg_label(arguments: dict[str, Any]) -> str:
    path = arguments.get("path")
    return str(path) if path else ""
