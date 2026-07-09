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
"""Offline tests for the prepare generators (pure cores; no model, no DB)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval test tooling

import pytest
from prepare import _agent_pass as ap, prepare_bi_docs, prepare_corpus, prepare_targets
from rig import scoring


# --- _agent_pass parsing ---------------------------------------------------- #
def test_extract_json_handles_fences_and_prose():
    assert ap.extract_json('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert ap.extract_json('Here you go: {"x": 2} thanks') == {"x": 2}


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError, match="no parseable JSON"):
        ap.extract_json("sorry, I cannot help")


# --- prepare_bi_docs -------------------------------------------------------- #
def test_bi_doc_strips_fences():
    doc = prepare_bi_docs.generate_bi_doc(
        "context", chat=lambda s, u: "```markdown\n# Glossary\nfoo\n```"
    )
    assert doc == "# Glossary\nfoo"


# --- prepare_targets -------------------------------------------------------- #
def test_targets_keeps_only_available_schemas():
    reply = json.dumps(
        {"schemas": ["SALES", "GHOST"], "rationale": {"SALES": "needed"}}
    )
    kept, rationale = prepare_targets.generate_targets(
        "ctx", ["sales", "supply"], chat=lambda s, u: reply
    )
    assert kept == ["sales"]  # GHOST dropped; matched case-insensitively
    assert rationale == {"SALES": "needed"}


def test_targets_bad_reply_raises():
    with pytest.raises(ValueError, match="schemas"):
        prepare_targets.schemas_from_reply('{"nope": 1}')


def test_as_manifest_dict_shape():
    m = prepare_targets.as_manifest_dict(
        "oracle_v1", ["SALES"], connection_uri_env="EVAL_ORACLE_URI"
    )
    assert m["id"] == "oracle_v1"
    assert m["schemas"] == ["SALES"]
    assert m["connection_uri_env"] == "EVAL_ORACLE_URI"


# --- prepare_corpus --------------------------------------------------------- #
def test_items_from_reply_and_to_records():
    reply = json.dumps(
        [
            {
                "question": "count?",
                "answer_type": "expected_values",
                "expected_values": 6,
                "capability_tags": ["slang"],
            },
            {"question": "rate?", "answer_type": "gold_sql", "gold_sql": "SELECT 1"},
            {
                "question": "trend?",
                "answer_type": "eval_note",
                "eval_note": "states trend",
            },
        ]
    )
    items = prepare_corpus.items_from_reply(reply)
    records, errors = prepare_corpus.to_records(items)
    assert not errors
    assert [r.answer_type for r in records] == [
        "expected_values",
        "gold_sql",
        "eval_note",
    ]
    assert records[0].answer_spec == {"nums": [6.0]}
    assert records[0].id == "Q1"  # id assigned


def test_to_records_skips_bad_items():
    items = [
        {"question": "", "answer_type": "gold_sql", "gold_sql": "x"},  # empty q
        {"question": "ok", "answer_type": "banana"},  # bad type
        {"question": "good", "answer_type": "eval_note", "eval_note": "r"},
    ]
    records, errors = prepare_corpus.to_records(items)
    assert len(records) == 1
    assert len(errors) == 2


def test_validate_drops_gold_sql_that_errors():
    recs, _ = prepare_corpus.to_records(
        [{"question": "q", "answer_type": "gold_sql", "gold_sql": "SELECT bad"}]
    )

    def boom(sql):
        raise RuntimeError("ORA-00942 table does not exist")

    report = prepare_corpus.validate(recs, boom, drop_invalid=True)
    assert not report.kept
    assert any("failed to execute" in d for d in report.dropped)


def test_validate_drops_gold_sql_with_no_rows():
    recs, _ = prepare_corpus.to_records(
        [{"question": "q", "answer_type": "gold_sql", "gold_sql": "SELECT 1 WHERE 1=0"}]
    )
    report = prepare_corpus.validate(
        recs, lambda sql: scoring.GoldResult(columns=["a"], rows=[]), drop_invalid=True
    )
    assert not report.kept
    assert any("no rows" in d for d in report.dropped)


def test_validate_keeps_valid_gold_sql():
    recs, _ = prepare_corpus.to_records(
        [{"question": "q", "answer_type": "gold_sql", "gold_sql": "SELECT 1"}]
    )
    report = prepare_corpus.validate(
        recs, lambda sql: scoring.GoldResult(columns=["a"], rows=[{"a": 1}])
    )
    assert len(report.kept) == 1
    assert not report.dropped


def test_validate_flags_unvalidated_when_no_db():
    recs, _ = prepare_corpus.to_records(
        [{"question": "q", "answer_type": "gold_sql", "gold_sql": "SELECT 1"}]
    )
    report = prepare_corpus.validate(recs, None)
    assert len(report.kept) == 1
    assert any("UNVALIDATED" in f for f in report.flagged)


def test_run_prepare_schema_hint_and_read_inputs(tmp_path):
    from prepare import run_prepare

    (tmp_path / "orders.csv").write_text("order_id,amount,status\n1,10,OK\n")
    (tmp_path / "context.md").write_text("Orders track sales.")
    (tmp_path / "schema_notes.md").write_text("full DDL here")
    context_text, schema_text = run_prepare.read_inputs(tmp_path)
    assert "Orders track sales." in context_text
    assert "context.md" not in schema_text  # context files excluded from schema
    assert "orders(order_id, amount, status)" in schema_text  # CSV header -> hint
    assert "full DDL here" in schema_text  # schema-named file included


def test_generate_corpus_end_to_end_pure():
    reply = json.dumps(
        [
            {
                "question": "count?",
                "answer_type": "expected_values",
                "expected_values": 6,
            },
            {"question": "rate?", "answer_type": "gold_sql", "gold_sql": "SELECT r"},
        ]
    )
    report = prepare_corpus.generate_corpus(
        "inputs",
        "schema",
        chat=lambda s, u: reply,
        execute_sql=lambda sql: scoring.GoldResult(columns=["r"], rows=[{"r": 1}]),
    )
    assert len(report.kept) == 2
    csv_text = prepare_corpus.to_csv(report)
    assert "count?" in csv_text
    assert "rate?" in csv_text
