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
"""Offline tests for rig.corpus (CSV <-> records, no stack)."""

from __future__ import annotations

from rig import corpus

_GOOD = (
    "id,question,answer_type,capability_tags,gold_sql,eval_note,expected_values,tolerance,notes\n"
    'Q1,"How many sites?",expected_values,slang,,,6,,\n'
    'Q2,"On-time rate?",gold_sql,metric;temporal,"SELECT 1",,,,"runs=1"\n'
    'Q3,"Is health improving?",eval_note,metric,,"Passes iff it states the trend.",,,\n'
    'Q4,"Which family leads?",expected_values,slang,,,Vantage,,\n'
    'Q5,"Undefined metric?",expected_values,trap,,,trap,,\n'
    'Q6,"No tundra line?",expected_values,negative,,,zero,,\n'
    'Q7,"Rate close?",expected_values,metric,,,0.469,0.05,\n'
)


def test_load_good_corpus_yields_typed_specs():
    load = corpus.load_corpus_csv(_GOOD)
    assert load.ok, load.errors
    by_id = {r.id: r for r in load.records}
    assert by_id["Q1"].answer_spec == {"nums": [6.0]}
    assert by_id["Q2"].answer_spec == {"sql": "SELECT 1"}
    assert by_id["Q2"].capability_tags == ("metric", "temporal")
    assert by_id["Q3"].answer_spec == {"note": "Passes iff it states the trend."}
    assert by_id["Q4"].answer_spec == {"names": ["Vantage"]}
    assert by_id["Q5"].answer_spec == {"trap": True}
    assert by_id["Q6"].answer_spec == {"zero": True}
    assert by_id["Q7"].answer_spec == {"nums": [0.469], "tolerance": 0.05}


def test_missing_required_header_is_fatal():
    load = corpus.load_corpus_csv("question,answer_type\na,b\n")
    assert not load.ok
    assert any("missing required column" in e for e in load.errors)


def test_per_row_errors_accumulate_and_do_not_abort():
    text = (
        "id,question,answer_type,gold_sql,eval_note,expected_values\n"
        'Q1,"good",expected_values,,,6\n'
        'Q2,"",expected_values,,,6\n'  # empty question
        'Q3,"bad type",banana,,,6\n'  # bad answer_type
        'Q4,"empty sql",gold_sql,,,\n'  # gold_sql empty
        'Q1,"dup id",expected_values,,,6\n'  # duplicate id
        'Q6,"good2",eval_note,,"rubric",\n'
    )
    load = corpus.load_corpus_csv(text)
    ids = {r.id for r in load.records}
    assert ids == {"Q1", "Q6"}  # only the two valid rows
    assert len(load.errors) == 4  # empty q, bad type, empty sql, dup id
    joined = " ".join(load.errors)
    assert "empty question" in joined
    assert "duplicate id" in joined


def test_unknown_tag_warns_but_passes():
    load = corpus.load_corpus_csv(
        "id,question,answer_type,capability_tags,expected_values\n"
        'Q1,"q",expected_values,made_up_tag,6\n'
    )
    assert load.ok
    assert any("unknown capability tag" in w for w in load.warnings)


def test_json_escape_hatch_and_absent_key():
    load = corpus.load_corpus_csv(
        "id,question,answer_type,expected_values\n"
        'Q5,"names+absent",expected_values,"{""names"":[""Reef""],""absent"":[""Tigerline""]}"\n'
    )
    assert load.ok, load.errors
    assert load.records[0].answer_spec == {"names": ["Reef"], "absent": ["Tigerline"]}


def test_csv_roundtrips():
    load = corpus.load_corpus_csv(_GOOD)
    reserialized = corpus.records_to_csv(load.records)
    reloaded = corpus.load_corpus_csv(reserialized)
    assert reloaded.ok, reloaded.errors
    assert [r.id for r in reloaded.records] == [r.id for r in load.records]
    assert {r.id: r.answer_spec for r in reloaded.records} == {
        r.id: r.answer_spec for r in load.records
    }


def test_empty_body_is_an_error():
    load = corpus.load_corpus_csv("id,question,answer_type,expected_values\n")
    assert not load.ok
    assert any("no data rows" in e for e in load.errors)
