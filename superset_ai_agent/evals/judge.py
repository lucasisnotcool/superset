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

"""LLM judge for free-text (`eval_note`) benchmark items (P2.2, spec F4).

Follows the judge best practices the research pinned down: **binary pass/fail
with a written critique** (never a Likert scale), the author's evaluation note
as the explicit rubric, and an optional **panel vote** (PoLL) — N independent
calls with majority pooling, ties and malformed outputs degrading to
``needs_review`` rather than a fabricated verdict. The judge is a *secondary*
signal by design: SQL-comparable items never reach it (§16 doctrine).
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent, independent of Superset
import logging
from dataclasses import dataclass
from typing import Any

from superset_ai_agent.llm.base import ChatMessage

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """You are grading one answer from a SQL analytics agent.

Question the user asked:
{question}

What a correct answer must satisfy (the author's rubric):
{note}

The agent's answer:
- SQL: {sql}
- Result rows (may be truncated): {rows}
- Summary: {summary}

Decide strictly against the rubric. If the evidence is insufficient to decide,
say so instead of guessing.

Respond with ONLY a JSON object:
{{"verdict": "pass" | "fail" | "needs_review",
  "critique": "<one to three sentences citing the rubric>"}}
"""

_VALID_VERDICTS = {"pass", "fail", "needs_review"}

#: Rows shown to the judge (cost control; enough to check a rubric).
_JUDGE_ROW_CAP = 20


@dataclass
class JudgeOutcome:
    """Aggregated judge decision for one item."""

    verdict: str
    critique: str
    votes: list[str]


def _parse_judgment(content: str) -> tuple[str, str] | None:
    """Extract (verdict, critique) from a judge reply; None when malformed."""

    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    verdict = str(parsed.get("verdict", "")).strip().lower()
    if verdict not in _VALID_VERDICTS:
        return None
    return verdict, str(parsed.get("critique", "")).strip()


def judge_eval_note(
    model_client: Any,
    *,
    question: str,
    note: str,
    sql: str | None,
    rows: list[dict[str, Any]] | None,
    summary: str | None,
    votes: int = 1,
    model: str | None = None,
) -> JudgeOutcome:
    """Grade an agent answer against a free-text rubric.

    ``votes`` > 1 runs a small panel and takes the majority; a tie or a
    malformed majority degrades to ``needs_review``. Any transport error on a
    vote counts as ``needs_review`` for that vote — the judge must never turn
    an eval run into an exception.
    """

    prompt = _JUDGE_PROMPT.format(
        question=question,
        note=note,
        sql=sql or "(none)",
        rows=json.dumps((rows or [])[:_JUDGE_ROW_CAP], default=str),
        summary=summary or "(none)",
    )
    ballots: list[str] = []
    critiques: list[str] = []
    for _ in range(max(1, votes)):
        try:
            result = model_client.chat(
                [ChatMessage(role="user", content=prompt)],
                model=model,
            )
            content = getattr(result, "content", None) or ""
        except Exception:  # pylint: disable=broad-except - judge is best-effort
            logger.warning("Judge call failed; counting a needs_review vote.")
            ballots.append("needs_review")
            continue
        parsed = _parse_judgment(content)
        if parsed is None:
            ballots.append("needs_review")
            continue
        verdict, critique = parsed
        ballots.append(verdict)
        if critique:
            critiques.append(critique)

    counts = {v: ballots.count(v) for v in _VALID_VERDICTS}
    best = max(counts.values())
    winners = [v for v, c in counts.items() if c == best]
    verdict = winners[0] if len(winners) == 1 else "needs_review"
    critique = " | ".join(dict.fromkeys(critiques)) or (
        "Judge could not produce a parseable decision."
        if verdict == "needs_review" and not critiques
        else ""
    )
    return JudgeOutcome(verdict=verdict, critique=critique, votes=ballots)
