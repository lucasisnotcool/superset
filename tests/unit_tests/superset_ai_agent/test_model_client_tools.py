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

"""Tool-calling round-trip coverage for the LLM provider clients (Phase 0)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from types import SimpleNamespace
from typing import Any

import httpx

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.azure_openai import AzureOpenAIModelClient
from superset_ai_agent.llm.base import (
    ChatMessage,
    message_to_openai,
    parse_openai_tool_calls,
    tools_to_openai,
    ToolSpec,
)
from superset_ai_agent.llm.ollama import OllamaModelClient
from superset_ai_agent.llm.openai_client import OpenAIModelClient
from superset_ai_agent.llm.openai_compatible import OpenAICompatibleModelClient

TOOLS = [
    ToolSpec(
        name="update_mdl_file",
        description="Replace an MDL file's content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    )
]

_OPENAI_TOOL_MESSAGE = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "update_mdl_file",
                "arguments": '{"path": "models/orders.json", "content": "{}"}',
            },
        }
    ],
}


def test_tools_to_openai_shape() -> None:
    rendered = tools_to_openai(TOOLS)

    assert rendered == [
        {
            "type": "function",
            "function": {
                "name": "update_mdl_file",
                "description": "Replace an MDL file's content.",
                "parameters": TOOLS[0].parameters,
            },
        }
    ]
    assert tools_to_openai(None) is None
    assert tools_to_openai([]) is None


def test_parse_openai_tool_calls_parses_json_arguments() -> None:
    calls = parse_openai_tool_calls(_OPENAI_TOOL_MESSAGE)

    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "update_mdl_file"
    assert calls[0].arguments == {"path": "models/orders.json", "content": "{}"}


def test_parse_openai_tool_calls_tolerates_bad_json() -> None:
    message = {
        "tool_calls": [{"id": "x", "function": {"name": "t", "arguments": "{not json"}}]
    }

    calls = parse_openai_tool_calls(message)

    assert calls[0].arguments == {}
    assert calls[0].raw_arguments == "{not json"


def test_message_to_openai_drops_none_but_keeps_tool_wiring() -> None:
    user = message_to_openai(ChatMessage(role="user", content="hi"))
    tool = message_to_openai(
        ChatMessage(
            role="tool", content="ok", tool_call_id="call_1", name="update_mdl_file"
        )
    )

    assert user == {"role": "user", "content": "hi"}
    assert tool == {
        "role": "tool",
        "content": "ok",
        "tool_call_id": "call_1",
        "name": "update_mdl_file",
    }


def test_openai_client_returns_tool_calls() -> None:
    class _ToolCompletions:
        def __init__(self) -> None:
            self.payload: dict[str, Any] | None = None

        def create(self, **payload: Any) -> SimpleNamespace:
            self.payload = payload
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=[])
                    )
                ],
                model_dump=lambda: {"choices": [{"message": _OPENAI_TOOL_MESSAGE}]},
            )

    completions = _ToolCompletions()
    client = OpenAIModelClient(
        AgentConfig(
            model_provider="openai",
            openai_api_key="test-key",
            openai_model="gpt-test",
        ),
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )

    result = client.chat(
        [ChatMessage(role="user", content="add a metric")], tools=TOOLS
    )

    assert result.content == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "update_mdl_file"
    assert completions.payload is not None
    assert completions.payload["tools"][0]["function"]["name"] == "update_mdl_file"


def test_openai_compatible_client_returns_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "custom-model"}]})
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "update_mdl_file"
        return httpx.Response(
            200, json={"choices": [{"message": _OPENAI_TOOL_MESSAGE}]}
        )

    client = OpenAICompatibleModelClient(
        AgentConfig(
            model_provider="openai_compatible",
            openai_compatible_base_url="http://llm.local/v1",
            openai_compatible_api_key="test-key",
            openai_compatible_model="custom-model",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = client.chat([ChatMessage(role="user", content="x")], tools=TOOLS)

    assert result.tool_calls[0].arguments["path"] == "models/orders.json"


def test_azure_client_returns_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "update_mdl_file"
        return httpx.Response(
            200, json={"choices": [{"message": _OPENAI_TOOL_MESSAGE}]}
        )

    client = AzureOpenAIModelClient(
        AgentConfig(
            model_provider="azure_openai",
            azure_openai_endpoint="https://azure-openai.example.com",
            azure_openai_key="test-key",
            azure_openai_model="sql-deployment",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = client.chat([ChatMessage(role="user", content="x")], tools=TOOLS)

    assert result.tool_calls[0].name == "update_mdl_file"


def test_ollama_client_returns_tool_calls_with_object_arguments() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "update_mdl_file",
                                "arguments": {"path": "models/orders.json"},
                            }
                        }
                    ],
                }
            },
        )

    # OllamaModelClient builds its own httpx.Client without a transport seam, so
    # patch the module's httpx.Client with a MockTransport-backed real client.
    client = OllamaModelClient(AgentConfig(model_provider="ollama"))

    import superset_ai_agent.llm.ollama as ollama_module

    original = ollama_module.httpx.Client

    def _client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return original(transport=httpx.MockTransport(handler))

    ollama_module.httpx.Client = _client_factory  # type: ignore[assignment]
    try:
        result = client.chat([ChatMessage(role="user", content="x")], tools=TOOLS)
    finally:
        ollama_module.httpx.Client = original  # type: ignore[assignment]

    assert result.tool_calls[0].arguments == {"path": "models/orders.json"}
    assert captured["body"]["tools"][0]["function"]["name"] == "update_mdl_file"
