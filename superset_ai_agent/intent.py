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

"""Lightweight intent classification (Wren parity: intent_classification).

Routes a question to ``text_to_sql`` (default), ``general`` (no SQL needed), or
``clarify`` (ambiguous). Fails closed to ``text_to_sql`` so a classifier outage
never blocks the existing SQL path.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Literal

from pydantic import BaseModel

from superset_ai_agent.llm.base import ChatMessage, ModelClient

IntentLabel = Literal["text_to_sql", "general", "clarify"]

_PROMPT = (
    "You classify a user's message to a data analytics agent. Return JSON with "
    "an 'intent' field: 'text_to_sql' if it asks for data that needs a SQL "
    "query; 'general' if it is small talk or a capability question needing no "
    "query; 'clarify' if it is too ambiguous to query without more detail. "
    "Include a short 'reason'."
)


class IntentResult(BaseModel):
    """Structured intent classification result."""

    intent: IntentLabel = "text_to_sql"
    reason: str = ""


def classify_intent(model_client: ModelClient, question: str) -> IntentResult:
    """Classify a question's intent; default to text_to_sql on any failure."""

    try:
        result = model_client.chat(
            [
                ChatMessage(role="system", content=_PROMPT),
                ChatMessage(role="user", content=question),
            ],
            format_schema=IntentResult.model_json_schema(),
        )
        return IntentResult.model_validate(json.loads(result.content))
    except Exception:  # pylint: disable=broad-except - fail closed to SQL path
        return IntentResult(intent="text_to_sql", reason="classifier-unavailable")
