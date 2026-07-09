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

"""Authoring corpus CSV contract + capability vocab (plan P0.2/P1.1/P1.2)."""

from __future__ import annotations

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.evals.authoring.capability_vocab import (
    CAPABILITY_TAGS,
    CAPABILITY_VOCAB,
    unknown_tags,
    vocab_prompt_block,
)
from superset_ai_agent.evals.authoring.corpus_csv import parse_corpus_csv

# --- P0.2 config flags -------------------------------------------------------


def test_authoring_flags_default_off_and_env_readable(monkeypatch):
    assert AgentConfig().wren_benchmark_authoring_enabled is False
    monkeypatch.setenv("WREN_BENCHMARK_AUTHORING_ENABLED", "true")
    monkeypatch.setenv("WREN_BENCHMARK_AUTHOR_MODEL", "gpt-test")
    monkeypatch.setenv("WREN_BENCHMARK_AUTHOR_MAX_STEPS", "5")
    cfg = AgentConfig.from_env()
    assert cfg.wren_benchmark_authoring_enabled is True
    assert cfg.wren_benchmark_author_model == "gpt-test"
    assert cfg.wren_benchmark_author_max_steps == 5


# --- P1.1 capability vocab ---------------------------------------------------


def test_vocab_is_nonempty_with_stable_slug_shape():
    assert CAPABILITY_VOCAB
    for tag, desc in CAPABILITY_VOCAB.items():
        assert tag == tag.lower()
        assert " " not in tag
        assert desc.strip()
    assert CAPABILITY_TAGS == tuple(CAPABILITY_VOCAB)


def test_vocab_excludes_fixture_specific_tags():
    for legacy in ("join1", "xschema2", "xschema3", "golden", "viewable"):
        assert legacy not in CAPABILITY_VOCAB


def test_unknown_tags_and_prompt_block():
    assert unknown_tags(["metric", "made_up"]) == ["made_up"]
    block = vocab_prompt_block()
    assert "- metric:" in block
    assert "- trap:" in block


# --- P1.2 CSV parser ---------------------------------------------------------

HEADER = (
    "type,question,gold_sql,expected_values,eval_note,"
    "answer_type,capability_tags,target_schema,context,notes\n"
)


def test_happy_path_all_three_answer_types_and_context():
    csv_text = HEADER + "\n".join(
        [
            "question,How many widgets?,SELECT COUNT(*) FROM w,,,,"
            '"metric;join",sales,,',
            "question,Total revenue?,,42,,,,,,",
            "question,Is churn healthy?,,,Answer must cite the churn "
            'definition,,temporal,,,"check rubric"',
            'context,,,,,,,,"Widgets are physical goods; revenue is net of tax",',
        ]
    )
    draft = parse_corpus_csv(csv_text)
    assert draft.ok, draft.errors
    assert [i.answer_type for i in draft.items] == [
        "gold_sql",
        "expected_values",
        "eval_note",
    ]
    assert draft.items[0].answer_spec == {"sql": "SELECT COUNT(*) FROM w"}
    assert draft.items[0].capability_tags == ("metric", "join")
    assert draft.items[0].target_schema == "sales"
    assert draft.items[1].answer_spec == {"nums": [42.0]}
    assert draft.items[2].answer_spec == {
        "note": "Answer must cite the churn definition"
    }
    assert not draft.items[0].needs_authoring
    assert len(draft.contexts) == 1
    assert "net of tax" in draft.contexts[0].text


def test_question_only_row_needs_authoring():
    draft = parse_corpus_csv(HEADER + "question,Who buys the most?,,,,,,,,\n")
    assert draft.ok
    item = draft.items[0]
    assert item.needs_authoring is True
    assert item.answer_type is None
    assert item.answer_spec is None


def test_row_type_inferred_without_type_column():
    csv_text = "question,gold_sql,context\nHow many?,SELECT 1,\n,,Some context text\n"
    draft = parse_corpus_csv(csv_text)
    assert draft.ok
    assert len(draft.items) == 1
    assert len(draft.contexts) == 1


def test_multiple_answer_cells_is_row_error():
    draft = parse_corpus_csv(HEADER + "question,Q?,SELECT 1,42,,,,,,\n")
    assert not draft.ok
    assert "multiple answer cells" in draft.errors[0]
    assert draft.items == []


def test_answer_type_contradiction_and_bad_values():
    contradiction = parse_corpus_csv(HEADER + "question,Q?,SELECT 1,,,eval_note,,,,\n")
    assert "contradicts" in contradiction.errors[0]
    bad_type = parse_corpus_csv(HEADER + "question,Q?,,,,nonsense,,,,\n")
    assert "not in" in bad_type.errors[0]
    bad_json = parse_corpus_csv(HEADER + 'question,Q?,,"{""bogus"": 1}",,,,,,\n')
    assert "unknown expected_values keys" in bad_json.errors[0]


def test_expected_values_shorthand_forms():
    rows = [
        ("trap", {"trap": True}),
        ("zero", {"zero": True}),
        ('"nums: 1, 2.5"', {"nums": [1.0, 2.5]}),
        ('"names: Acme; Globex"', {"names": ["Acme", "Globex"]}),
        ('"1; 2"', {"nums": [1.0, 2.0]}),
        ('"Acme, Globex"', {"names": ["Acme", "Globex"]}),
        ('"{""nums"": [7], ""tolerance"": 0.1}"', {"nums": [7], "tolerance": 0.1}),
    ]
    for cell, expected in rows:
        draft = parse_corpus_csv(HEADER + f"question,Q?,,{cell},,,,,,\n")
        assert draft.ok, (cell, draft.errors)
        assert draft.items[0].answer_spec == expected, cell


def test_unknown_tag_warns_but_keeps_item():
    draft = parse_corpus_csv(HEADER + "question,Q?,SELECT 1,,,,wibble,,,\n")
    assert draft.ok
    assert draft.items[0].capability_tags == ("wibble",)
    assert any("unknown capability tag" in w for w in draft.warnings)


def test_missing_question_and_missing_header_are_errors():
    no_question = parse_corpus_csv(HEADER + "question,,SELECT 1,,,,,,,\n")
    assert "no question text" in no_question.errors[0]
    no_header = parse_corpus_csv("a,b\n1,2\n")
    assert not no_header.ok


def test_empty_rows_are_skipped_silently():
    draft = parse_corpus_csv(HEADER + ",,,,,,,,,\n" * 3)
    assert draft.ok
    assert draft.items == []
    assert draft.contexts == []
