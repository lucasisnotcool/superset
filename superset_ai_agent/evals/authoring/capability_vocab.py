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

"""Fixture-agnostic capability vocabulary (plan P1.1, R3/R9).

The declared tag set every authored benchmark item draws from. Definitions are
deliberately one line each — they double as the authoring agent's tagging
rubric (fed into its prompt) and the human reviewer's legend, so the two can
never drift apart.

Superset (pun intended) of the research rig's ``evaluation/rig/corpus.KNOWN_TAGS``
generic tags; deliberately EXCLUDES the Seagate-fixture-specific variants
(``join1``/``xschema2``/``xschema3``/``golden``/``viewable``) — new benchmarks
use the generic ``join``/``cross_schema`` forms. Unknown tags warn, never fail
(same policy as the rig), so legacy fixtures still load.
"""

from __future__ import annotations

#: tag -> one-line definition (the tagging rubric AND the UI legend).
CAPABILITY_VOCAB: dict[str, str] = {
    "slang": "Question uses business jargon that must be mapped to real columns.",
    "join": "Requires joining two or more tables within one schema.",
    "cross_schema": "Requires a join that crosses a schema boundary.",
    "bridge": "Requires a many-to-many bridge/associative table on the join path.",
    "metric": "Requires a business-defined metric formula, not a bare aggregate.",
    "aggregation": "Requires a non-trivial aggregate (grouped, windowed, or ratio).",
    "filter_value": "Requires filtering on specific literal values or enum codes.",
    "temporal": "Requires date/time logic: ranges, custom calendars, or grains.",
    "multihop": "Requires chaining several intermediate steps or subqueries.",
    "trap": "A correct answer refuses: the metric/entity is undefined for the ask.",
    "negative": "The correct answer is empty or zero; fabricating rows is a fail.",
    "distractor": "Plausible-but-wrong tables/columns exist and must be avoided.",
}

#: Stable ordering for prompts and UI legends.
CAPABILITY_TAGS: tuple[str, ...] = tuple(CAPABILITY_VOCAB)


def unknown_tags(tags: list[str] | tuple[str, ...]) -> list[str]:
    """Tags not in the declared vocabulary (warn-level, never a hard failure)."""

    return [t for t in tags if t not in CAPABILITY_VOCAB]


def vocab_prompt_block() -> str:
    """The vocabulary rendered for an authoring/tagging prompt."""

    return "\n".join(f"- {tag}: {desc}" for tag, desc in CAPABILITY_VOCAB.items())
