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

"""Skills — Markdown workflows that guide agents to operate the semantic layer.

Mirrors Wren's "skills" pattern: human-readable procedures (build MDL before
querying, fetch context before SQL, store confirmed examples after success) that
an orchestrator or external agent can surface. The seam is intentionally simple
(file-backed) so new agentic workflows are added as Markdown, not code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_SKILLS_DIR = Path(__file__).parent


def list_skills() -> list[str]:
    """Return the available skill names (Markdown stems)."""

    return sorted(path.stem for path in _SKILLS_DIR.glob("*.md"))


@lru_cache(maxsize=32)
def get_skill(name: str) -> str:
    """Load a skill workflow by name. Raises ``FileNotFoundError`` if missing."""

    safe = name.strip().replace("/", "").replace("\\", "")
    path = _SKILLS_DIR / f"{safe}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown skill: {name}")
    return path.read_text(encoding="utf-8")
