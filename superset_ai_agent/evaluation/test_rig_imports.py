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
"""Guard the rig's dependency on agent-internal primitives (plan §7 risk).

If ``evals.*`` / ``llm.factory`` signatures drift, this fails loudly rather than
letting the rig break at run time. These are the only agent-internal seams the rig
relies on; keep this list in sync with rig.scoring / rig.model_client.
"""

from __future__ import annotations

import inspect


def test_scorer_primitives_importable_with_expected_signatures():
    from superset_ai_agent.evals.comparator import compare_result_sets
    from superset_ai_agent.evals.judge import judge_eval_note
    from superset_ai_agent.evals.typed_spec import score_expected_values

    # score_expected_values(spec, rows)
    assert list(inspect.signature(score_expected_values).parameters)[:2] == [
        "spec",
        "rows",
    ]
    # judge_eval_note(model_client, *, question, note, sql, rows, summary, votes, model)
    jparams = inspect.signature(judge_eval_note).parameters
    for name in ("question", "note", "sql", "rows", "summary", "votes", "model"):
        assert name in jparams, f"judge_eval_note lost kwarg {name}"
    # compare_result_sets(*, predicted_columns, predicted_rows, gold_columns, gold_rows)
    cparams = inspect.signature(compare_result_sets).parameters
    for name in (
        "predicted_columns",
        "predicted_rows",
        "gold_columns",
        "gold_rows",
    ):
        assert name in cparams, f"compare_result_sets lost kwarg {name}"


def test_model_client_factory_and_config_importable():
    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.llm.factory import create_model_client

    assert hasattr(AgentConfig, "from_env")
    assert callable(create_model_client)


def test_end_to_end_scoring_uses_real_primitives():
    # Exercises rig.scoring against the *real* typed_spec + comparator (no mocks).
    from rig import scoring

    ev = scoring.score_item(
        answer_type="expected_values",
        answer_spec={"nums": [6]},
        question="q",
        answer=scoring.AgentAnswer(status="ok", rows=[{"n": 6}]),
    )
    assert ev.verdict == scoring.CORRECT
