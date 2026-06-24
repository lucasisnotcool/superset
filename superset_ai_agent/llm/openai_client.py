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

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from superset_ai_agent.config import AgentConfig, StructuredOutputMode
from superset_ai_agent.llm.base import ChatMessage, ModelProviderError, ModelResult
from superset_ai_agent.llm.schema import to_strict_json_schema
from superset_ai_agent.schemas import ModelInfo

#: Degrade order per starting mode. Strict ``json_schema`` is rejected by OpenAI for
#: schemas with open-ended objects (our MDL ``properties`` is a free-form dict), so the
#: client must fall back to ``json_object`` (then prompt-only) rather than failing the
#: whole call — the documented json_schema→json_object→prompt contract.
FALLBACK_ORDER: dict[StructuredOutputMode, tuple[StructuredOutputMode, ...]] = {
    "json_schema": ("json_schema", "json_object", "prompt_only"),
    "json_object": ("json_object", "prompt_only"),
    "prompt_only": ("prompt_only",),
}


def _response_format(
    format_schema: dict[str, Any] | None,
    mode: StructuredOutputMode,
) -> dict[str, Any] | None:
    if not format_schema or mode == "prompt_only":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_response",
            "schema": to_strict_json_schema(format_schema),
            "strict": True,
        },
    }


def _messages_for_mode(
    messages: list[ChatMessage],
    format_schema: dict[str, Any] | None,
    mode: StructuredOutputMode,
) -> list[ChatMessage]:
    """In the non-schema modes, inject the schema into the prompt so the model still
    has the target shape (json_object/prompt_only do not enforce it on the API side)."""

    if not format_schema or mode == "json_schema":
        return messages
    instruction = (
        "Return only a JSON object that validates against this JSON Schema. "
        "Do not include markdown or prose outside the JSON object.\n"
        f"{json.dumps(to_strict_json_schema(format_schema))}"
    )
    return [*messages, ChatMessage(role="system", content=instruction)]


def _status_code(ex: Exception) -> int | None:
    code = getattr(ex, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(ex, "response", None)
    response_code = getattr(response, "status_code", None)
    return response_code if isinstance(response_code, int) else None


def _can_retry_without_structured_output(ex: Exception) -> bool:
    # A 400/422 from the schema/response_format is recoverable by degrading the mode;
    # auth/rate-limit/server errors are not and should surface.
    return _status_code(ex) in {400, 422}


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


class OpenAIModelClient:
    """OpenAI SDK-backed model client."""

    def __init__(self, config: AgentConfig, client: Any | None = None):
        self.config = config
        self.base_url = config.openai_base_url.rstrip("/")
        self.model = config.openai_model
        self.client = client or self._build_client()

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        last_error: Exception | None = None
        modes = FALLBACK_ORDER[self.config.openai_structured_output]
        for mode in modes:
            try:
                return self._chat_with_mode(
                    messages, model=model, format_schema=format_schema, mode=mode
                )
            except Exception as ex:  # pylint: disable=broad-except
                last_error = ex
                # Only a schema/response_format rejection is recoverable by degrading
                # the mode; anything else (auth, rate-limit, 5xx) should surface.
                if not _can_retry_without_structured_output(ex):
                    break
        raise ModelProviderError(
            f"OpenAI request failed for model {model or self.model!r}: {last_error}"
        ) from last_error

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
                for message in _messages_for_mode(messages, format_schema, mode)
            ],
            "stream": False,
        }
        response_format = _response_format(format_schema, mode)
        if response_format:
            payload["response_format"] = response_format

        response = self.client.chat.completions.create(**payload)
        try:
            content = response.choices[0].message.content or ""
        except (AttributeError, IndexError) as ex:
            raise ModelProviderError(
                "OpenAI response did not include text content."
            ) from ex

        return ModelResult(content=content, raw=_model_dump(response))

    def is_reachable(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:  # pylint: disable=broad-except
            return False

    def list_models(self) -> list[ModelInfo]:
        response = self.client.models.list()
        data = getattr(response, "data", [])
        return [
            ModelInfo(name=getattr(model, "id", ""))
            for model in data
            if getattr(model, "id", "")
        ]

    def _build_client(self) -> Any:
        if not self.config.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when AI_AGENT_MODEL_PROVIDER=openai."
            )

        try:
            from openai import OpenAI
        except ImportError as ex:
            raise RuntimeError(
                "The openai package is required for AI_AGENT_MODEL_PROVIDER=openai."
            ) from ex

        return OpenAI(
            api_key=self.config.openai_api_key,
            base_url=self.base_url,
        )
