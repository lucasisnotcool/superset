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

"""Metering decorator around a ``ModelClient``.

A single transparent wrapper at the one chokepoint every LLM call passes through:
it times each ``chat`` invocation and appends a usage record, then returns the
result (or re-raises the original error) **unchanged**. Recording is fail-open —
a telemetry or DB failure logs and is swallowed so it can never break, slow, or
alter an agent response (the reliability requirement).

The embedding path uses a separate ``Embedder`` interface and is intentionally
NOT captured here; the deferred embedding meter would reuse ``LlmUsageStore`` with
``kind="embedding"`` (see usage_store + plan_llm_call_logging_impl.md §Seams).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.llm.base import ChatMessage, ModelClient, ModelResult, ToolSpec
from superset_ai_agent.llm.usage_store import LlmUsageStore
from superset_ai_agent.schemas import ModelInfo

logger = logging.getLogger(__name__)


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_tokens(result: ModelResult | None) -> tuple[int | None, int | None]:
    """Best-effort (prompt, completion) token counts from a provider response.

    Only providers that report ``usage`` (e.g. OpenAI) contribute; anything else
    degrades to ``(None, None)``.
    """

    if result is None or not isinstance(result.raw, dict):
        return None, None
    usage = result.raw.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return _as_int(usage.get("prompt_tokens")), _as_int(usage.get("completion_tokens"))


class MeteredModelClient:
    """Wrap a ``ModelClient`` to record per-call count + timing telemetry."""

    def __init__(
        self,
        inner: ModelClient,
        *,
        store: LlmUsageStore,
        provider: str,
        default_model: str | None,
    ) -> None:
        self._inner = inner
        self._store = store
        self._provider = provider
        self._default_model = default_model

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> ModelResult:
        start = time.monotonic()
        ok = True
        result: ModelResult | None = None
        try:
            result = self._inner.chat(
                messages,
                model=model,
                format_schema=format_schema,
                tools=tools,
            )
            return result
        except Exception:
            ok = False
            raise
        finally:
            # Runs on both success and failure; the record itself is fail-open so
            # the original return/raise is never disturbed.
            duration_ms = int((time.monotonic() - start) * 1000)
            self._record(model=model, duration_ms=duration_ms, ok=ok, result=result)

    def _record(
        self,
        *,
        model: str | None,
        duration_ms: int,
        ok: bool,
        result: ModelResult | None,
    ) -> None:
        try:
            prompt_tokens, completion_tokens = _usage_tokens(result)
            self._store.record(
                provider=self._provider,
                model=model or self._default_model,
                duration_ms=duration_ms,
                ok=ok,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                kind="chat",
            )
        except Exception:  # pylint: disable=broad-except - telemetry must not break calls
            logger.warning("LLM usage recording failed; metric dropped.", exc_info=True)

    # -- Transparent delegation for the rest of the ModelClient contract --------

    def is_reachable(self) -> bool:
        return self._inner.is_reachable()

    def list_models(self) -> list[ModelInfo]:
        return self._inner.list_models()


def wrap_model_client(
    inner: ModelClient,
    *,
    store: LlmUsageStore,
    config: AgentConfig,
) -> MeteredModelClient:
    """Wrap ``inner`` with metering, deriving provider + default model from config."""

    return MeteredModelClient(
        inner,
        store=store,
        provider=config.model_provider,
        default_model=config.default_model(),
    )
