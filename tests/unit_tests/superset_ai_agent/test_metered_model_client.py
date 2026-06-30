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

"""MeteredModelClient: records calls, fail-open, transparent delegation (NFR1)."""

from __future__ import annotations

import pytest

from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.llm.metered import MeteredModelClient
from superset_ai_agent.llm.usage_store import InMemoryLlmUsageStore
from superset_ai_agent.schemas import ModelInfo


class _FakeClient:
    """Minimal ModelClient stand-in with controllable behaviour."""

    def __init__(
        self, result: ModelResult | None = None, error: Exception | None = None
    ):
        self._result = result
        self._error = error
        self.calls: list[str | None] = []

    def chat(self, messages, *, model=None, format_schema=None, tools=None):
        self.calls.append(model)
        if self._error is not None:
            raise self._error
        return self._result

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="gpt-5.2")]


class _RaisingStore(InMemoryLlmUsageStore):
    def record(self, **kwargs):  # type: ignore[override]
        raise RuntimeError("db down")


_MESSAGES = [ChatMessage(role="user", content="hi")]


def _meter(inner, store, *, default_model="gpt-5.2"):
    return MeteredModelClient(
        inner, store=store, provider="openai", default_model=default_model
    )


def test_successful_chat_records_one_ok_row_with_tokens() -> None:
    result = ModelResult(
        content="ok",
        raw={"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    )
    store = InMemoryLlmUsageStore()
    meter = _meter(_FakeClient(result=result), store)

    returned = meter.chat(_MESSAGES, model="gpt-5.2")

    assert returned is result  # unchanged passthrough
    summary = store.summary()
    assert summary.total_calls == 1
    assert summary.total_failures == 0
    assert summary.total_prompt_tokens == 100
    assert summary.total_completion_tokens == 20
    assert summary.by_provider[0].key == "openai"
    assert summary.by_model[0].key == "gpt-5.2"


def test_failed_chat_reraises_and_records_failure() -> None:
    store = InMemoryLlmUsageStore()
    meter = _meter(_FakeClient(error=ValueError("boom")), store)

    with pytest.raises(ValueError, match="boom"):
        meter.chat(_MESSAGES, model="gpt-5.2")

    summary = store.summary()
    assert summary.total_calls == 1
    assert summary.total_failures == 1  # outcome still captured


def test_recording_failure_never_breaks_the_call() -> None:
    # NFR1: a store/DB failure must not affect the LLM call result.
    result = ModelResult(content="still works", raw={})
    meter = _meter(_FakeClient(result=result), _RaisingStore())

    returned = meter.chat(_MESSAGES, model="gpt-5.2")

    assert returned is result


def test_recording_failure_during_a_failed_call_preserves_original_error() -> None:
    # Both the inner call AND the store raise: the caller must see the inner error.
    meter = _meter(_FakeClient(error=KeyError("inner")), _RaisingStore())

    with pytest.raises(KeyError, match="inner"):
        meter.chat(_MESSAGES)


def test_none_model_falls_back_to_default_model() -> None:
    store = InMemoryLlmUsageStore()
    meter = _meter(_FakeClient(result=ModelResult(content="x", raw={})), store)

    meter.chat(_MESSAGES, model=None)

    assert store.summary().by_model[0].key == "gpt-5.2"


def test_missing_usage_records_null_tokens() -> None:
    store = InMemoryLlmUsageStore()
    meter = _meter(_FakeClient(result=ModelResult(content="x", raw={})), store)

    meter.chat(_MESSAGES, model="m")

    summary = store.summary()
    assert summary.total_calls == 1
    assert summary.total_prompt_tokens == 0  # None coerced to 0 in aggregate


def test_delegates_is_reachable_and_list_models() -> None:
    meter = _meter(
        _FakeClient(result=ModelResult(content="x")), InMemoryLlmUsageStore()
    )
    assert meter.is_reachable() is True
    assert [m.name for m in meter.list_models()] == ["gpt-5.2"]
