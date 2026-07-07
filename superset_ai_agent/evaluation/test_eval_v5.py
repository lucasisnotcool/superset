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

"""Offline tests for the v5 matrix (doc-RAG configs) pure functions."""

from __future__ import annotations

import run_eval_v4 as r4
import run_eval_v5 as r5


def test_expand_configs_is_ten_and_extends_v4():
    configs = r5.expand_configs()
    names = [c["name"] for c in configs]
    assert len(configs) == 10
    # v4's matrix is preserved verbatim as a prefix.
    assert names[:8] == [c["name"] for c in r4.expand_configs()]
    assert {"wren_bi_rag·manual", "wren_bi_rag·auto"}.issubset(set(names))


def _resp(passages: int | None):
    """A minimal agent response; None means the channel step is absent."""
    if passages is None:
        return {"trace": [{"step": "load_context", "details": {}}]}
    return {
        "trace": [
            {"step": "load_context", "details": {}},
            {
                "step": "load_document_context",
                "details": {
                    "passage_count": passages,
                    "document_count": 1 if passages else 0,
                    "retriever": "keyword",
                    "truncated": False,
                },
            },
        ]
    }


def test_doc_rag_signal_extracts_channel_activity():
    assert r5.doc_rag_signal(_resp(None)) is None
    signal = r5.doc_rag_signal(_resp(3))
    assert signal == {
        "passage_count": 3,
        "document_count": 1,
        "retriever": "keyword",
        "truncated": False,
    }
    assert r5.doc_rag_active(_resp(3)) is True
    # A zero-passage step means the channel ran but nothing grounded.
    assert r5.doc_rag_active(_resp(0)) is False
    assert r5.doc_rag_active(_resp(None)) is False


def test_mismatch_flags_served_mode_contradictions():
    # wren_bi_rag sweep served WITHOUT passages -> mismatch.
    assert r5.mismatch(True, _resp(None)) is True
    assert r5.mismatch(True, _resp(0)) is True
    assert r5.mismatch(True, _resp(2)) is False
    # wren_bi sweep served WITH passages -> mismatch.
    assert r5.mismatch(False, _resp(2)) is True
    assert r5.mismatch(False, _resp(None)) is False


def test_split_trials_and_total_mismatches():
    raw = [
        {
            "basic": {"Q1": "correct"},
            "wren_bi_rag·auto": {"Q1": "correct"},
            "_channel_audit": {
                "basic": {"expected_doc_rag": False, "mismatches": 0},
                "wren_bi_rag·auto": {"expected_doc_rag": True, "mismatches": 2},
            },
        },
        {
            "basic": {"Q1": "wrong"},
            "_channel_audit": {
                "basic": {"expected_doc_rag": False, "mismatches": 1},
            },
        },
    ]
    verdicts, audits = r5.split_trials(raw)
    assert all("_channel_audit" not in t for t in verdicts)
    assert verdicts[0]["wren_bi_rag·auto"] == {"Q1": "correct"}
    assert r5.total_mismatches(audits) == 3


def test_scoreboard_carries_ship_gate_delta():
    cap = {"Q1": ("slang",)}
    trials = [
        {
            "wren_bi_rag·auto": {"Q1": "correct"},
            "wren_bi_context·auto": {"Q1": "correct"},
            "wren_bi·auto": {"Q1": "wrong"},
            "context_dump": {"Q1": "wrong"},
        }
    ]
    sb = r4.build_scoreboard(
        trials,
        capability=cap,
        meta={"fixture_version": "v5"},
        config_names=r5.config_names(),
        extra_deltas=r5.V5_DELTAS,
    )
    gate = "rag vs dump — SHIP GATE (wren_bi_rag·auto − wren_bi_context·auto)"
    assert gate in sb["deltas"]
    assert sb["deltas"][gate] == 0.0
    assert (
        sb["deltas"]["rag lift over bare layer (wren_bi_rag·auto − wren_bi·auto)"]
        == 1.0
    )
    # v4's own headline deltas remain intact for cross-version comparability.
    assert "layer vs raw context (wren_bi·auto − context_dump)" in sb["deltas"]
