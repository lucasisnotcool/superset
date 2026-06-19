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

from typing import Any, Protocol

from pydantic import BaseModel, Field

from superset_ai_agent.schemas import ModelInfo


class ChatMessage(BaseModel):
    """Provider-neutral chat message."""

    role: str
    content: str


class ModelResult(BaseModel):
    """Provider-neutral model response."""

    content: str
    raw: dict[str, Any] = Field(default_factory=dict)


class ModelProviderError(RuntimeError):
    """Raised when a model provider cannot complete a request safely."""


class ModelClient(Protocol):
    """Minimal LLM client contract for agent backends."""

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        """Send chat messages to a model."""

    def is_reachable(self) -> bool:
        """Return whether the configured provider appears reachable."""

    def list_models(self) -> list[ModelInfo]:
        """List provider models when supported."""
