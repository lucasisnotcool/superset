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

"""LLM listwise reranking seam (B3, plan_sql_agent_doc_grounding_spec.md).

A second-stage reranker over a first-stage (hybrid) candidate pool, using the
already-configured :class:`ModelClient` — no cross-encoder dependency. Opt-in
(``wren_rerank_enabled``): it adds one model call per query, so the default
path never pays it. Degrades closed: any failure or malformed output defers to
the first-stage order.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
import logging
from typing import Any

from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.prompts.registry import get_prompt

logger = logging.getLogger(__name__)

_RERANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"order": {"type": "array", "items": {"type": "integer"}}},
    "required": ["order"],
}

#: Per-candidate char cap in the rerank prompt, so a long chunk cannot blow the
#: rerank call's own token budget.
_CANDIDATE_CHAR_LIMIT = 600


def llm_rerank(
    model_client: ModelClient,
    question: str,
    candidates: list[str],
    k: int,
) -> list[int] | None:
    """Rank ``candidates`` for ``question``; return indices best-first, or ``None``.

    The returned indices are validated (hallucinated/duplicate indices dropped)
    and capped to ``k``. ``None`` — on a missing prompt, provider error, or
    unusable output — tells the caller to keep the first-stage order.
    """

    if not candidates or k <= 0:
        return None
    try:
        prompt = get_prompt("rerank")
    except OSError:
        return None
    payload = {
        "question": question,
        "candidates": [
            {"index": index, "text": text[:_CANDIDATE_CHAR_LIMIT]}
            for index, text in enumerate(candidates)
        ],
        "top_k": k,
    }
    try:
        result = model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(
                    role="user",
                    content=(
                        "Rank the candidates. Return only JSON matching the "
                        f"schema.\n{json.dumps(payload, default=str)}"
                    ),
                ),
            ],
            format_schema=_RERANK_SCHEMA,
        )
        data = json.loads(result.content)
    except Exception:  # pylint: disable=broad-except - defer to first-stage order
        logger.warning("LLM rerank failed; keeping first-stage order.")
        return None
    order = data.get("order") if isinstance(data, dict) else None
    if not isinstance(order, list):
        return None
    seen: set[int] = set()
    valid: list[int] = []
    for value in order:
        in_range = isinstance(value, int) and 0 <= value < len(candidates)
        if in_range and value not in seen:
            seen.add(value)
            valid.append(value)
    if not valid:
        return None
    return valid[:k]
