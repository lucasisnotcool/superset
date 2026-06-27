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
    # The MDL Copilot loads exactly these three skills (COPILOT_SKILLS); the query
    # agent is a structured-output pipeline and loads none.
    assert {"onboarding", "generate-mdl", "enrich-context"} == set(names)
    body = get_skill("onboarding")
    assert "MDL" in body


def test_unknown_skill_raises() -> None:
    with pytest.raises(FileNotFoundError):
        get_skill("does-not-exist")


def test_enrich_skill_lets_autopilot_propose_relationships_and_metrics() -> None:
    # P2: relationships/metrics are no longer grilled/suppressed in auto-pilot —
    # they are proposed into the (human-reviewed) changeset. Guard the contract so a
    # future edit can't silently restore the old "high-blast-radius → grill" gating.
    body = get_skill("enrich-context")

    assert "propose them into the" in body or "proposed directly into the" in body
    assert "review gate" in body or "review-gated" in body
    # The old gating that suppressed relationships/metrics must be gone.
    assert "high-blast-radius" not in body
    assert "three escalations" not in body


def test_skill_text_excludes_license_header() -> None:
    # The ASF license header must stay in the source file but never reach the
    # injected system prompt (it wastes tokens and distracts the agent).
    for name in ("onboarding", "generate-mdl", "enrich-context"):
        body = get_skill(name)
        assert "Licensed to the Apache Software Foundation" not in body
        assert not body.lstrip().startswith("<!--")
        assert body.strip()  # real content survives


def test_prompt_text_excludes_license_header() -> None:
    from superset_ai_agent.prompts.registry import get_prompt

    body = get_prompt("mdl_copilot")
    assert "Licensed to the Apache Software Foundation" not in body
    assert "MDL Copilot" in body


def test_strip_leading_metadata_handles_comment_and_frontmatter() -> None:
    from superset_ai_agent.prompts.registry import strip_leading_metadata

    text = "<!--\nlicense\n-->\n---\nname: x\n---\n# Title\n\nBody."
    assert strip_leading_metadata(text) == "# Title\n\nBody."
    # No header → unchanged content.
    assert strip_leading_metadata("# Title\n\nBody.") == "# Title\n\nBody."
