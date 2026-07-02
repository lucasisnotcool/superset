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

import re
from functools import lru_cache
from pathlib import Path

_LEADING_HTML_COMMENT = re.compile(r"^\s*<!--.*?-->\s*", re.DOTALL)
_LEADING_FRONTMATTER = re.compile(r"^\s*---\s*\n.*?\n---\s*\n", re.DOTALL)


def strip_leading_metadata(text: str) -> str:
    """Strip a leading license/HTML-comment block and/or YAML frontmatter.

    Prompt and skill files carry an ASF license header (an HTML comment), and may
    carry YAML frontmatter. That boilerplate is required in the *source file* but
    must never reach the model's system prompt — there it only wastes tokens and
    distracts the agent. Headers are removed at load time so the files stay
    license-compliant while the injected text starts at the real content. Repeats
    until stable so either ordering (comment-then-frontmatter or the reverse) is
    handled.
    """

    previous = None
    stripped = text
    while previous != stripped:
        previous = stripped
        stripped = _LEADING_HTML_COMMENT.sub("", stripped, count=1)
        stripped = _LEADING_FRONTMATTER.sub("", stripped, count=1)
    return stripped.lstrip()


#: Optional DB-backed override resolver (testing platform P2.1). Installed by
#: ``create_app`` when a prompt store exists; returns the ``production``-labeled
#: content for a name or ``None`` (→ file fallback). The file always remains
#: the fail-safe default, so prompt resolution can never take the agent down.
_prompt_resolver = None


def set_prompt_resolver(resolver) -> None:
    """Install (or clear, with ``None``) the DB-backed prompt override hook."""

    global _prompt_resolver  # noqa: PLW0603 - single process-wide seam
    _prompt_resolver = resolver


@lru_cache(maxsize=32)
def get_file_prompt(name: str) -> str:
    """Load the repo-file default for a prompt name (the git-reviewed seed)."""

    prompt_path = Path(__file__).parent / f"{name}.md"
    return strip_leading_metadata(prompt_path.read_text(encoding="utf-8"))


def list_file_prompt_names() -> list[str]:
    """Names of all repo-file prompt defaults (``prompts/*.md``)."""

    return sorted(path.stem for path in Path(__file__).parent.glob("*.md"))


def get_prompt(name: str) -> str:
    """Resolve a prompt: DB ``production`` override first, file default second.

    The override path is best-effort — any resolver error degrades to the file
    default rather than failing the agent turn.
    """

    if _prompt_resolver is not None:
        try:
            override = _prompt_resolver(name)
        except Exception:  # pylint: disable=broad-except - fail-safe fallback
            override = None
        if override is not None:
            return strip_leading_metadata(override)
    return get_file_prompt(name)
