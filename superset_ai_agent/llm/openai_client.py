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

from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.base import ChatMessage, ModelProviderError, ModelResult
from superset_ai_agent.llm.schema import to_strict_json_schema
from superset_ai_agent.schemas import ModelInfo


def _response_format(format_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not format_schema:
        return None
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "sql_draft",
            "schema": to_strict_json_schema(format_schema),
            "strict": True,
        },
    }


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
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
        }
        response_format = _response_format(format_schema)
        if response_format:
            payload["response_format"] = response_format

        try:
            response = self.client.chat.completions.create(**payload)
        except Exception as ex:  # pylint: disable=broad-except
            raise ModelProviderError(
                f"OpenAI request failed for model {payload['model']!r}: {ex}"
            ) from ex

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
