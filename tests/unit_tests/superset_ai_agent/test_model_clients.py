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

import json  # noqa: TID251 - tests cover the standalone agent JSON contract
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.base import ChatMessage, ModelProviderError
from superset_ai_agent.llm.factory import create_model_client
from superset_ai_agent.llm.ollama import OllamaModelClient
from superset_ai_agent.llm.openai_client import OpenAIModelClient
from superset_ai_agent.llm.openai_compatible import OpenAICompatibleModelClient
from superset_ai_agent.llm.schema import to_strict_json_schema

SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["sql", "explanation"],
}

STRICT_SCHEMA = {
    **SCHEMA,
    "additionalProperties": False,
}


class FakeOpenAIChatCompletions:
    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    def create(self, **payload: Any) -> SimpleNamespace:
        self.payload = payload
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"sql":"select 1","explanation":"ok"}',
                    )
                )
            ],
            model_dump=lambda: {"id": "chatcmpl-test"},
        )


class FakeOpenAIModels:
    def list(self) -> SimpleNamespace:
        return SimpleNamespace(data=[SimpleNamespace(id="gpt-test")])


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=FakeOpenAIChatCompletions())
        self.models = FakeOpenAIModels()


def test_model_factory_selects_ollama() -> None:
    client = create_model_client(AgentConfig(model_provider="ollama"))

    assert isinstance(client, OllamaModelClient)


def test_model_factory_rejects_missing_openai_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_model_client(AgentConfig(model_provider="openai"))


def test_model_factory_selects_openai_compatible() -> None:
    client = create_model_client(
        AgentConfig(
            model_provider="openai_compatible",
            openai_compatible_base_url="http://llm.local/v1",
            openai_compatible_api_key="test-key",
            openai_compatible_model="custom-model",
        )
    )

    assert isinstance(client, OpenAICompatibleModelClient)


def test_openai_compatible_client_rejects_invalid_structured_output_mode() -> None:
    with pytest.raises(ValueError, match="OPENAI_COMPATIBLE_STRUCTURED_OUTPUT"):
        OpenAICompatibleModelClient(
            AgentConfig(
                model_provider="openai_compatible",
                openai_compatible_base_url="http://llm.local/v1",
                openai_compatible_api_key="test-key",
                openai_compatible_model="custom-model",
                openai_compatible_structured_output="invalid",  # type: ignore[arg-type]
            )
        )


def test_openai_client_sends_json_schema_response_format() -> None:
    fake_client = FakeOpenAIClient()
    client = OpenAIModelClient(
        AgentConfig(
            model_provider="openai",
            openai_api_key="test-key",
            openai_model="gpt-test",
        ),
        client=fake_client,
    )

    result = client.chat(
        [ChatMessage(role="user", content="return sql")],
        format_schema=SCHEMA,
    )

    payload = fake_client.chat.completions.payload
    assert result.content == '{"sql":"select 1","explanation":"ok"}'
    assert payload["model"] == "gpt-test"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"] == STRICT_SCHEMA
    assert "additionalProperties" not in SCHEMA


def test_openai_compatible_client_posts_chat_completion_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "custom-model"}]})
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-key"
        assert body["model"] == "custom-model"
        assert body["response_format"]["type"] == "json_schema"
        assert (
            body["response_format"]["json_schema"]["schema"]["additionalProperties"]
            is False
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"sql":"select 1","explanation":"ok"}',
                        }
                    }
                ]
            },
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

    result = client.chat(
        [ChatMessage(role="user", content="return sql")],
        format_schema=SCHEMA,
    )

    assert result.content == '{"sql":"select 1","explanation":"ok"}'
    assert requests[-1].url.path == "/v1/chat/completions"


def test_strict_json_schema_closes_nested_objects_without_mutating_input() -> None:
    schema = {
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "metadata": {
                "type": "object",
                "properties": {"dialect": {"type": "string"}},
                "required": ["dialect"],
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
        "required": ["sql", "metadata", "items"],
    }

    strict_schema = to_strict_json_schema(schema)

    assert strict_schema["additionalProperties"] is False
    assert strict_schema["properties"]["metadata"]["additionalProperties"] is False
    assert (
        strict_schema["properties"]["items"]["items"]["additionalProperties"] is False
    )
    assert "additionalProperties" not in schema
    assert "additionalProperties" not in schema["properties"]["metadata"]


def test_openai_compatible_client_falls_back_when_schema_is_rejected() -> None:
    seen_formats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_format = body.get("response_format", {})
        seen_formats.append(response_format.get("type", "prompt_only"))
        if response_format.get("type") == "json_schema":
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"sql":"select 1","explanation":"ok"}',
                        }
                    }
                ]
            },
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

    result = client.chat(
        [ChatMessage(role="user", content="return sql")],
        format_schema=SCHEMA,
    )

    assert result.content == '{"sql":"select 1","explanation":"ok"}'
    assert seen_formats == ["json_schema", "json_object"]


def test_openai_compatible_client_raises_sanitized_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    client = OpenAICompatibleModelClient(
        AgentConfig(
            model_provider="openai_compatible",
            openai_compatible_base_url="http://llm.local/v1",
            openai_compatible_api_key="secret-key",
            openai_compatible_model="custom-model",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelProviderError) as exc_info:
        client.chat([ChatMessage(role="user", content="return sql")])

    assert "secret-key" not in str(exc_info.value)
    assert "HTTP 401" in str(exc_info.value)


def test_openai_compatible_client_wraps_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = OpenAICompatibleModelClient(
        AgentConfig(
            model_provider="openai_compatible",
            openai_compatible_base_url="http://llm.local/v1",
            openai_compatible_api_key="secret-key",
            openai_compatible_model="custom-model",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelProviderError) as exc_info:
        client.chat([ChatMessage(role="user", content="return sql")])

    assert "connection refused" in str(exc_info.value)
    assert "secret-key" not in str(exc_info.value)
