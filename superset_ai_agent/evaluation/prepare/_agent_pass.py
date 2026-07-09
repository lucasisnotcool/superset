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
"""Shared helper for the prepare generators: chat + robust output parsing.

Keeps the LLM plumbing in one place so the three generators stay small. The
parsing helpers are pure and unit-tested (they handle the usual model quirks:
```json fences, prose around the payload, trailing commas are NOT handled — models
are instructed to emit strict JSON).
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling
import re
from typing import Any

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n|\n```$", re.MULTILINE)


def read_inputs(paths: list[Any]) -> str:
    """Concatenate the text of input files with clear separators."""

    chunks: list[str] = []
    for p in paths:
        chunks.append(
            f"----- {getattr(p, 'name', p)} -----\n{p.read_text(encoding='utf-8')}"
        )
    return "\n\n".join(chunks)


def strip_code_fences(text: str) -> str:
    """Remove a leading ```lang fence and a trailing ``` if present."""

    t = text.strip()
    if t.startswith("```"):
        t = _FENCE_RE.sub("", t).strip()
    return t


def extract_json(text: str) -> Any:
    """Extract the first JSON object/array from a model reply. Raises on failure."""

    t = strip_code_fences(text)
    # Try the whole thing first, then the widest {..}/[..] span.
    for candidate in (t, _widest_span(t, "[", "]"), _widest_span(t, "{", "}")):
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    raise ValueError(f"no parseable JSON in model reply: {text[:200]!r}")


def _widest_span(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


def chat(model_client: Any, system: str, user: str, *, model: str | None = None) -> str:
    """One chat turn (system+user folded into a single user message, like judge.py)."""

    from superset_ai_agent.llm.base import ChatMessage  # noqa: PLC0415

    content = f"{system}\n\n{user}" if system else user
    result = model_client.chat([ChatMessage(role="user", content=content)], model=model)
    return getattr(result, "content", None) or ""
