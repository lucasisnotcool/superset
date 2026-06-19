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

import httpx

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.schemas import ModelInfo


class OllamaModelClient:
    """Small direct Ollama API client.

    This avoids coupling the POC to a specific LangChain wrapper while still
    leaving a clean ModelClient seam for later LiteLLM/OpenAI/Anthropic adapters.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.base_url = config.ollama_base_url.rstrip("/")

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        payload: dict[str, Any] = {
            "model": model or self.config.ollama_model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
        }
        if format_schema:
            payload["format"] = format_schema

        with httpx.Client(timeout=120.0) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

        content = data.get("message", {}).get("content", "")
        return ModelResult(content=content, raw=data)

    def is_reachable(self) -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    def list_models(self) -> list[ModelInfo]:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()

        return [
            ModelInfo(
                name=model.get("name", ""),
                modified_at=model.get("modified_at"),
                size=model.get("size"),
            )
            for model in data.get("models", [])
            if model.get("name")
        ]
