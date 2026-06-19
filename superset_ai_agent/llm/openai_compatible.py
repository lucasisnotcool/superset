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

import httpx

from superset_ai_agent.config import AgentConfig, StructuredOutputMode
from superset_ai_agent.llm.base import ChatMessage, ModelProviderError, ModelResult
from superset_ai_agent.llm.schema import to_strict_json_schema
from superset_ai_agent.schemas import ModelInfo

FALLBACK_ORDER: dict[StructuredOutputMode, tuple[StructuredOutputMode, ...]] = {
    "json_schema": ("json_schema", "json_object", "prompt_only"),
    "json_object": ("json_object", "prompt_only"),
    "prompt_only": ("prompt_only",),
}


class OpenAICompatibleModelClient:
    """Direct HTTP client for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
    ):
        self.config = config
        self.base_url = (config.openai_compatible_base_url or "").rstrip("/")
        self.model = config.openai_compatible_model or ""
        self.transport = transport
        self.timeout = httpx.Timeout(120.0)
        self._validate_config()

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        last_error: Exception | None = None
        modes = FALLBACK_ORDER[self.config.openai_compatible_structured_output]
        for mode in modes:
            try:
                return self._chat_with_mode(
                    messages,
                    model=model,
                    format_schema=format_schema,
                    mode=mode,
                )
            except httpx.HTTPStatusError as ex:
                last_error = ex
                if not self._can_retry_without_structured_output(ex):
                    break
            except httpx.HTTPError as ex:
                last_error = ex
                break

        raise self._provider_error(last_error)

    def is_reachable(self) -> bool:
        try:
            with httpx.Client(
                timeout=10.0,
                transport=self.transport,
                headers=self._headers(),
            ) as client:
                response = client.get(f"{self.base_url}/models")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[ModelInfo]:
        with httpx.Client(
            timeout=10.0,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = client.get(f"{self.base_url}/models")
            response.raise_for_status()
            data = response.json()

        return [
            ModelInfo(name=model.get("id", ""))
            for model in data.get("data", [])
            if model.get("id")
        ]

    def _chat_with_mode(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None,
        format_schema: dict[str, Any] | None,
        mode: StructuredOutputMode,
    ) -> ModelResult:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [
                message.model_dump()
                for message in self._messages_for_mode(messages, format_schema, mode)
            ],
            "stream": False,
        }
        response_format = self._response_format(format_schema, mode)
        if response_format:
            payload["response_format"] = response_format

        with httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as ex:
            raise ModelProviderError(
                "OpenAI-compatible response did not include choices[0].message.content."
            ) from ex
        return ModelResult(content=content, raw=data)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "superset-ai-agent/0.1",
        }
        if self.config.openai_compatible_api_key:
            headers["Authorization"] = (
                f"Bearer {self.config.openai_compatible_api_key}"
            )
        return headers

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
                    "name": "sql_draft",
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

    def _validate_config(self) -> None:
        if self.config.openai_compatible_structured_output not in FALLBACK_ORDER:
            raise ValueError(
                "OPENAI_COMPATIBLE_STRUCTURED_OUTPUT must be one of: "
                "json_schema, json_object, prompt_only."
            )
        if not self.base_url:
            raise ValueError(
                "OPENAI_COMPATIBLE_BASE_URL is required when "
                "AI_AGENT_MODEL_PROVIDER=openai_compatible."
            )
        if not self.model:
            raise ValueError(
                "OPENAI_COMPATIBLE_MODEL is required when "
                "AI_AGENT_MODEL_PROVIDER=openai_compatible."
            )
        if (
            self.config.openai_compatible_require_api_key
            and not self.config.openai_compatible_api_key
        ):
            raise ValueError(
                "OPENAI_COMPATIBLE_API_KEY is required unless "
                "OPENAI_COMPATIBLE_REQUIRE_API_KEY=false."
            )

    def _provider_error(self, ex: Exception | None) -> ModelProviderError:
        if isinstance(ex, httpx.HTTPStatusError):
            body = ex.response.text[:500]
            return ModelProviderError(
                "OpenAI-compatible request failed "
                f"for model {self.model!r} at {self.base_url!r}: "
                f"HTTP {ex.response.status_code}: {body}"
            )
        return ModelProviderError(
            f"OpenAI-compatible request failed for model {self.model!r} "
            f"at {self.base_url!r}: {ex}"
        )
