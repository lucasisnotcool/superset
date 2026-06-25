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

from __future__ import annotations

import json  # noqa: TID251 - standalone agent keeps its own JSON contract
import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

from superset_ai_agent.schemas import ModelInfo

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    """Provider-neutral chat message.

    ``tool_call_id`` and ``name`` carry the OpenAI ``role="tool"`` result wiring so
    a caller can feed a tool's output back into the next turn of an agentic loop.
    """

    role: str
    content: str
    #: Set on a ``role="tool"`` message to bind the result to a prior tool call.
    tool_call_id: str | None = None
    #: Optional tool name on a ``role="tool"`` message (provider-dependent).
    name: str | None = None
    #: Set on a replayed ``role="assistant"`` message that requested tool calls,
    #: so a multi-turn agentic loop can feed the model its own prior calls.
    tool_calls: "list[ToolCall] | None" = None


class ToolSpec(BaseModel):
    """A tool the model may call (provider-neutral, JSON-Schema parameters)."""

    name: str
    description: str = ""
    #: JSON Schema describing the tool's arguments object.
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """A single tool invocation requested by the model."""

    id: str = ""
    name: str
    #: Parsed argument object. Providers return JSON text; we parse leniently and
    #: keep the unparsed text in ``raw_arguments`` when parsing fails.
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: str = ""


ChatMessage.model_rebuild()


class ModelResult(BaseModel):
    """Provider-neutral model response.

    ``tool_calls`` is populated when the model chose to call tools instead of (or
    in addition to) returning text. ``content`` may be empty in that case.
    """

    content: str
    raw: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelProviderError(RuntimeError):
    """Raised when a model provider cannot complete a request safely."""


class ModelClient(Protocol):
    """Minimal LLM client contract for agent backends.

    ``tools`` is additive: existing structured-output callers (``format_schema``)
    are unaffected. When ``tools`` is supplied a provider that supports function
    calling returns ``ModelResult.tool_calls``; providers that do not support it
    degrade to text/structured output, and the agentic loop falls back to a
    structured edit-plan (see ``semantic_layer/copilot``).
    """

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> ModelResult:
        """Send chat messages to a model."""

    def is_reachable(self) -> bool:
        """Return whether the configured provider appears reachable."""

    def list_models(self) -> list[ModelInfo]:
        """List provider models when supported."""


def message_to_openai(message: ChatMessage) -> dict[str, Any]:
    """Render a chat message into the OpenAI/compatible wire shape.

    Ordinary messages stay ``{role, content}``; ``role="tool"`` messages carry
    ``tool_call_id``/``name``; a replayed assistant message carries ``tool_calls``
    in the provider's ``{id, type, function:{name, arguments}}`` shape.
    """

    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.role == "tool":
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.name:
            payload["name"] = message.name
    if message.tool_calls:
        # OpenAI accepts (and often requires) null content alongside tool_calls.
        payload["content"] = message.content or None
        payload["tool_calls"] = [
            {
                "id": call.id or f"call_{index}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments or json.dumps(call.arguments),
                },
            }
            for index, call in enumerate(message.tool_calls)
        ]
    return payload


def tools_to_openai(tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
    """Render tool specs into the OpenAI ``tools`` payload shape.

    Shared by the OpenAI, Azure, and OpenAI-compatible HTTP clients.
    """

    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def tools_to_ollama(tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
    """Render tool specs into the Ollama ``tools`` payload shape (OpenAI-like)."""

    return tools_to_openai(tools)


def _coerce_arguments(arguments: Any) -> tuple[dict[str, Any], str]:
    """Parse a tool call's arguments leniently.

    Providers return either a JSON string (OpenAI/Azure/compatible) or an already
    decoded object (Ollama). Returns ``(parsed_dict, raw_text)``.
    """

    if isinstance(arguments, dict):
        return arguments, json.dumps(arguments)
    if isinstance(arguments, str):
        raw = arguments
        try:
            parsed = json.loads(arguments) if arguments.strip() else {}
        except (ValueError, TypeError):
            logger.warning("Tool call arguments were not valid JSON: %r", raw[:200])
            return {}, raw
        return (parsed if isinstance(parsed, dict) else {}), raw
    return {}, ""


def parse_openai_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """Extract tool calls from an OpenAI-style ``choices[0].message``."""

    raw_calls = message.get("tool_calls") or []
    calls: list[ToolCall] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function") or {}
        name = function.get("name") or ""
        if not name:
            continue
        parsed, raw_text = _coerce_arguments(function.get("arguments"))
        calls.append(
            ToolCall(
                id=str(raw.get("id") or ""),
                name=name,
                arguments=parsed,
                raw_arguments=raw_text,
            )
        )
    return calls
