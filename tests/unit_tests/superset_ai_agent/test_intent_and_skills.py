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

"""Phase 4 — intent classification + skills."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

import pytest

from superset_ai_agent.intent import classify_intent
from superset_ai_agent.llm.base import ModelResult
from superset_ai_agent.skills import get_skill, list_skills


class _FakeModel:
    def __init__(self, content: str | Exception) -> None:
        self.content = content

    def chat(self, messages, *, model=None, format_schema=None) -> ModelResult:
        if isinstance(self.content, Exception):
            raise self.content
        return ModelResult(content=self.content)


def test_classify_intent_text_to_sql() -> None:
    model = _FakeModel(json.dumps({"intent": "text_to_sql", "reason": "needs data"}))
    assert classify_intent(model, "top customers by revenue").intent == "text_to_sql"


def test_classify_intent_general() -> None:
    model = _FakeModel(json.dumps({"intent": "general", "reason": "greeting"}))
    assert classify_intent(model, "hi there").intent == "general"


def test_classify_intent_fails_closed_to_sql() -> None:
    model = _FakeModel(RuntimeError("model down"))
    result = classify_intent(model, "anything")
    assert result.intent == "text_to_sql"
    assert result.reason == "classifier-unavailable"


def test_classify_intent_bad_json_fails_closed() -> None:
    assert classify_intent(_FakeModel("not json"), "q").intent == "text_to_sql"


def test_skills_are_listed_and_loadable() -> None:
    names = list_skills()
    assert {"onboarding", "generate-mdl", "enrich-context", "usage"} <= set(names)
    body = get_skill("usage")
    assert "semantic layer" in body.lower()


def test_unknown_skill_raises() -> None:
    with pytest.raises(FileNotFoundError):
        get_skill("does-not-exist")
