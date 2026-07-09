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

"""Semantic-SQL guidance must not present metrics as selectable columns.

wren_core has no ``metrics`` concept; a metric name reaches the DB verbatim and
raises (e.g. Oracle ORA-00904). The guidance in both agent graphs must tell the
model to inline the measure expression, never SELECT the metric by name.
"""

from __future__ import annotations

import pytest

from superset_ai_agent.conversation_graph import (
    _SEMANTIC_SQL_GUIDANCE as CONVERSATION_GUIDANCE,
)
from superset_ai_agent.graph import _SEMANTIC_SQL_GUIDANCE as ONESHOT_GUIDANCE


@pytest.mark.parametrize(
    "guidance",
    [ONESHOT_GUIDANCE, CONVERSATION_GUIDANCE],
    ids=["oneshot", "conversation"],
)
def test_guidance_tells_the_model_to_inline_metric_expressions(guidance: str) -> None:
    lowered = guidance.lower()
    # It must state a metric is not a selectable column and must be inlined.
    assert "not a" in lowered
    assert "selectable column" in lowered
    assert "inline" in lowered
    # It must no longer advertise metrics as directly referenceable identifiers.
    assert "defined relationships, and metrics" not in guidance


def test_both_graphs_share_the_corrected_guidance() -> None:
    assert ONESHOT_GUIDANCE == CONVERSATION_GUIDANCE
