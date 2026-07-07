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

"""Trust-ladder contract of the text_to_sql seed prompt (A3).

The prompt file is runtime behavior, not documentation — these tests guard the
grounding-precedence structure (plan_sql_agent_doc_grounding_spec.md A3) so a
future edit cannot silently drop a rung, the conflict rule, or the abstention
guidance that claws back eval v4's distractor-trap regression (Q12).
"""

from __future__ import annotations

from superset_ai_agent.prompts.registry import get_prompt


def _prompt() -> str:
    return get_prompt("text_to_sql")


def test_prompt_declares_trust_ladder_in_order() -> None:
    prompt = _prompt()
    assert "Grounding precedence" in prompt
    # The ladder's rungs appear in trust order.
    ladder_start = prompt.index("Grounding precedence")
    ladder = prompt[ladder_start:]
    positions = [
        ladder.index("`recalled_examples`"),
        ladder.index("`wren_context`"),
        ladder.index("`document_context`"),
        ladder.index("`datasets`"),
    ]
    assert positions == sorted(positions)


def test_prompt_keeps_document_context_advisory() -> None:
    prompt = _prompt()
    # Conflict rule: the semantic layer wins over document passages.
    assert "the semantic layer wins" in prompt
    # Documents never authorize new tables/columns.
    assert "never authorize a table or column" in prompt


def test_prompt_carries_abstention_guidance() -> None:
    prompt = _prompt()
    assert "Abstention" in prompt
    assert "return an empty `sql` string" in prompt
    # The distractor-trap clawback: retrieved != required.
    assert "do not force a join" in prompt.lower()


def test_prompt_keeps_temporal_layer_preference() -> None:
    # Eval v4: temporal was the weakest capability; the layer's structured
    # date/calendar columns beat prose-derived periods (Q22: 3/3 vs 0/3).
    prompt = _prompt()
    assert "structured date/calendar columns" in prompt


def test_prompt_retains_metric_definition_preference() -> None:
    prompt = _prompt()
    assert "metric expressions defined in the semantic layer" in prompt
