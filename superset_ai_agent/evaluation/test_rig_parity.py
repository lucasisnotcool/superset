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
"""Phase 6 parity gate — the new grader must agree with legacy ``seagate_scoring``.

This is the *deterministic* half of the R6 no-regression check (the live half is a
smoke run once the stack is up). For every Seagate ``EXPECTED`` spec we build a
canonically-correct result and a no-signal result and assert the new
``rig.scoring`` and the old ``seagate_scoring.score_result`` agree on correctness —
insulated from LLM run-to-run variance because both grade the *same* rows.

It also pins the one known twin divergence (the ``absent`` guard) so a future change
that silently widens it fails here.
"""

from __future__ import annotations

import seagate_scoring as sc
from rig import corpus, scoring


def _rig_correct(spec: dict, rows: list[dict]) -> bool:
    out = scoring.score_item(
        answer_type="expected_values",
        answer_spec=spec,
        question="q",
        answer=scoring.AgentAnswer(status="ok", rows=rows),
    )
    return out.verdict == scoring.CORRECT


def _legacy_correct(qid: str, rows: list[dict]) -> bool:
    return sc.score_result(qid, rows, None) in sc.CORRECT_VERDICTS


def _correct_rows(spec: dict) -> list[dict]:
    """A result the spec should accept."""

    if spec.get("trap") or spec.get("zero"):
        return []  # no confident number
    if "names" in spec:
        return [{"c": n} for n in spec["names"]]  # each required name, absent omitted
    return [{f"c{i}": n for i, n in enumerate(spec["nums"])}]


def test_corpus_from_shim_covers_all_expected():
    load = corpus.from_markdown_and_expected(
        __import__("eval_v2").fixture_dir("seagate_multi") / "test_queries.md",
        sc.EXPECTED,
        sc.CAPABILITY,
    )
    assert load.ok, load.errors
    assert {r.id for r in load.records} == set(sc.EXPECTED)


def test_grader_parity_correct_rows_all_agree():
    mismatches = []
    for qid, spec in sc.EXPECTED.items():
        rows = _correct_rows(spec)
        if not (_rig_correct(spec, rows) and _legacy_correct(qid, rows)):
            mismatches.append((qid, "correct-row", rows))
    assert not mismatches, f"graders disagree on correct rows: {mismatches}"


def test_grader_parity_no_signal_rows_all_agree():
    # An empty result: trap/zero should PASS (no confident answer), everything
    # numeric/name should FAIL. Both graders must agree either way.
    for qid, spec in sc.EXPECTED.items():
        rig = _rig_correct(spec, [])
        legacy = _legacy_correct(qid, [])
        assert rig == legacy, f"{qid}: rig={rig} legacy={legacy} on empty rows"


def test_grader_parity_wrong_number_rows_agree():
    for qid, spec in sc.EXPECTED.items():
        if "nums" not in spec:
            continue
        rows = [{"c": 999999.0}]  # a value no spec expects
        assert _rig_correct(spec, rows) == _legacy_correct(qid, rows), qid


def test_absent_guard_parity():
    # Q5 forbids "Tigerline Point". Both graders model `absent` (verified): a result
    # containing the forbidden name is rejected by BOTH — full parity, no divergence.
    spec = sc.EXPECTED["Q5"]
    rows_with_forbidden = [{"c": n} for n in spec["names"]] + [{"c": "Tigerline Point"}]
    assert _legacy_correct("Q5", rows_with_forbidden) is False
    assert _rig_correct(spec, rows_with_forbidden) is False
