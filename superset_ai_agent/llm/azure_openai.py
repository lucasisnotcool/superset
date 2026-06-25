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

import json  # noqa: TID251 - keep the standalone agent independent of Superset
from typing import Any
from urllib.parse import quote

import httpx

from superset_ai_agent.config import AgentConfig, StructuredOutputMode
from superset_ai_agent.llm.base import (
    ChatMessage,
    message_to_openai,
    ModelProviderError,
    ModelResult,
    parse_openai_tool_calls,
    tools_to_openai,
    ToolSpec,
)
from superset_ai_agent.llm.openai_compatible import FALLBACK_ORDER
from superset_ai_agent.llm.schema import to_strict_json_schema
from superset_ai_agent.schemas import ModelInfo


class AzureOpenAIModelClient:
    """Direct HTTP client for Azure OpenAI chat completions deployments."""

    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
    ):
        self.config = config
        self.endpoint = self._normalize_endpoint(config.azure_openai_endpoint)
        self.deployment = config.azure_openai_model or ""
        self.api_version = config.azure_openai_api_version
        self.transport = transport
        self.timeout = httpx.Timeout(120.0)
        self._validate_config()

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> ModelResult:
        last_error: Exception | None = None
        modes = FALLBACK_ORDER[self.config.azure_openai_structured_output]
        deployment = model or self.deployment
        for mode in modes:
            try:
                return self._chat_with_mode(
                    messages,
                    deployment=deployment,
                    format_schema=format_schema,
                    mode=mode,
                    tools=tools,
                )
            except httpx.HTTPStatusError as ex:
                last_error = ex
                if not self._can_retry_without_structured_output(ex):
                    break
            except httpx.HTTPError as ex:
                last_error = ex
                break

        raise self._provider_error(last_error, deployment=deployment)

    def is_reachable(self) -> bool:
        try:
            with httpx.Client(
                timeout=10.0,
                transport=self.transport,
                headers=self._headers(),
            ) as client:
                response = client.get(
                    f"{self.endpoint}/openai/deployments",
                    params={"api-version": self.api_version},
                )
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[ModelInfo]:
        try:
            with httpx.Client(
                timeout=10.0,
                transport=self.transport,
                headers=self._headers(),
            ) as client:
                response = client.get(
                    f"{self.endpoint}/openai/deployments",
                    params={"api-version": self.api_version},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError:
            return [ModelInfo(name=self.deployment)]

        deployments = data.get("data", []) if isinstance(data, dict) else []
        models = [
            ModelInfo(name=deployment.get("id", ""))
            for deployment in deployments
            if isinstance(deployment, dict) and deployment.get("id")
        ]
        return models or [ModelInfo(name=self.deployment)]

    def _chat_with_mode(
        self,
        messages: list[ChatMessage],
        *,
        deployment: str,
        format_schema: dict[str, Any] | None,
        mode: StructuredOutputMode,
        tools: list[ToolSpec] | None = None,
    ) -> ModelResult:
        payload: dict[str, Any] = {
            "messages": [
                message_to_openai(message)
                for message in self._messages_for_mode(messages, format_schema, mode)
            ],
            "stream": False,
        }
        response_format = self._response_format(format_schema, mode)
        if response_format:
            payload["response_format"] = response_format
        tool_payload = tools_to_openai(tools)
        if tool_payload:
            payload["tools"] = tool_payload

        with httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = client.post(
                self._chat_completions_url(deployment),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as ex:
            raise ModelProviderError(
                "Azure OpenAI response did not include choices[0].message."
            ) from ex
        tool_calls = parse_openai_tool_calls(message)
        content = message.get("content") or ""
        if not content and not tool_calls:
            raise ModelProviderError(
                "Azure OpenAI response had neither content nor tool_calls."
            )
        return ModelResult(content=content, raw=data, tool_calls=tool_calls)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "User-Agent": "superset-ai-agent/0.1",
            "api-key": self.config.azure_openai_key or "",
        }

    def _chat_completions_url(self, deployment: str) -> str:
        encoded_deployment = quote(deployment, safe="")
        return (
            f"{self.endpoint}/openai/deployments/{encoded_deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )

    @staticmethod
    def _response_format(
        format_schema: dict[str, Any] | None,
        mode: StructuredOutputMode,
    ) -> dict[str, Any] | None:
        if not format_schema:
            return None
        if mode == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": to_strict_json_schema(format_schema),
                    "strict": True,
                },
            }
        if mode == "json_object":
            return {"type": "json_object"}
        return None

    @staticmethod
    def _messages_for_mode(
        messages: list[ChatMessage],
        format_schema: dict[str, Any] | None,
        mode: StructuredOutputMode,
    ) -> list[ChatMessage]:
        if not format_schema or mode == "json_schema":
            return messages
        instruction = (
            "Return only a JSON object that validates against this JSON Schema. "
            "Do not include markdown or prose outside the JSON object.\n"
            f"{json.dumps(to_strict_json_schema(format_schema))}"
        )
        return [*messages, ChatMessage(role="system", content=instruction)]

    @staticmethod
    def _can_retry_without_structured_output(ex: httpx.HTTPStatusError) -> bool:
        return ex.response.status_code in {400, 422}

    @staticmethod
    def _normalize_endpoint(endpoint: str | None) -> str:
        value = (endpoint or "").rstrip("/")
        if value.endswith("/openai"):
            return value[: -len("/openai")]
        return value

    def _validate_config(self) -> None:
        if self.config.azure_openai_structured_output not in FALLBACK_ORDER:
            raise ValueError(
                "AZURE_OPENAI_STRUCTURED_OUTPUT must be one of: "
                "json_schema, json_object, prompt_only."
            )
        if not self.endpoint:
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT is required when "
                "AI_AGENT_MODEL_PROVIDER=azure_openai."
            )
        if not self.config.azure_openai_key:
            raise ValueError(
                "AZURE_OPENAI_KEY is required when "
                "AI_AGENT_MODEL_PROVIDER=azure_openai."
            )
        if not self.deployment:
            raise ValueError(
                "AZURE_OPENAI_MODEL is required when "
                "AI_AGENT_MODEL_PROVIDER=azure_openai."
            )
        if not self.api_version:
            raise ValueError(
                "AZURE_OPENAI_API_VERSION is required when "
                "AI_AGENT_MODEL_PROVIDER=azure_openai."
            )

    def _provider_error(
        self,
        ex: Exception | None,
        *,
        deployment: str,
    ) -> ModelProviderError:
        if isinstance(ex, httpx.HTTPStatusError):
            body = ex.response.text[:500]
            return ModelProviderError(
                "Azure OpenAI request failed "
                f"for deployment {deployment!r} at {self.endpoint!r}: "
                f"HTTP {ex.response.status_code}: {body}"
            )
        return ModelProviderError(
            "Azure OpenAI request failed "
            f"for deployment {deployment!r} at {self.endpoint!r}: {ex}"
        )
