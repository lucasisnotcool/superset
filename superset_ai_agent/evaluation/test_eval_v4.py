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
"""Offline tests for the v4 matrix + scoreboard pure functions."""

from __future__ import annotations

import run_eval_v4 as r4


def test_expand_configs_is_eight():
    configs = r4.expand_configs()
    names = [c["name"] for c in configs]
    assert len(configs) == 8
    assert names[:2] == ["basic", "context_dump"]
    # 3 wren modes x 2 onboard
    expected_wren = {
        "wren_base·manual", "wren_base·auto",
        "wren_bi·manual", "wren_bi·auto",
        "wren_bi_context·manual", "wren_bi_context·auto",
    }
    assert expected_wren.issubset(set(names))
    assert sum(1 for c in configs if c["onboard"] is None) == 2
    assert sum(1 for c in configs if c["onboard"] == "auto") == 3


def test_total_correct_counts_trap_ok():
    v = {"Q1": "correct", "Q12": "trap_ok", "Q2": "wrong", "Q21": "correct"}
    assert r4.total_correct(v) == 3


def test_capability_scores_tallies_by_tag():
    cap = {
        "Q1": ("slang",),
        "Q19": ("xschema3", "bridge"),
        "Q20": ("xschema3", "bridge"),
    }
    verdicts = {"Q1": "correct", "Q19": "correct", "Q20": "wrong"}
    cs = r4.capability_scores(verdicts, cap)
    assert cs["slang"] == [1, 1]
    assert cs["xschema3"] == [1, 2]
    assert cs["bridge"] == [1, 2]


def test_build_scoreboard_aggregates_and_deltas():
    cap = {"Q1": ("slang",), "Q19": ("xschema3",)}
    # two trials, two configs of interest present
    trials = [
        {
            "context_dump": {"Q1": "correct", "Q19": "wrong"},
            "wren_bi·auto": {"Q1": "correct", "Q19": "correct"},
            "wren_base·auto": {"Q1": "wrong", "Q19": "wrong"},
        },
        {
            "context_dump": {"Q1": "correct", "Q19": "wrong"},
            "wren_bi·auto": {"Q1": "correct", "Q19": "correct"},
            "wren_base·auto": {"Q1": "correct", "Q19": "wrong"},
        },
    ]
    sb = r4.build_scoreboard(trials, capability=cap, meta={"x": 1})
    assert sb["by_config"]["wren_bi·auto"]["total"]["mean"] == 2.0
    assert sb["by_config"]["context_dump"]["total"]["mean"] == 1.0
    # enrichment delta: wren_bi·auto(2.0) − wren_base·auto(mean of 0,1 = 0.5) = 1.5
    assert sb["deltas"]["enrichment (wren_bi·auto − wren_base·auto)"] == 1.5
    # layer vs raw context: 2.0 − 1.0 = 1.0
    assert sb["deltas"]["layer vs raw context (wren_bi·auto − context_dump)"] == 1.0
    # by_capability table carries per-config mean correct
    assert sb["by_capability"]["xschema3"]["wren_bi·auto"] == 1.0


def test_missing_config_in_trial_is_tolerated():
    sb = r4.build_scoreboard(
        [{"basic": {"Q1": "correct"}}], capability={"Q1": ("slang",)}, meta={}
    )
    assert sb["by_config"]["basic"]["total"]["mean"] == 1.0
    assert sb["by_config"]["wren_bi·auto"]["trials"] == 0
