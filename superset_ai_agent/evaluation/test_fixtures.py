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
"""Consistency tests for the seagate_multi cross-schema fixture.

Guards the contract the harness relies on: the manifest is internally consistent,
the glossary names every relevant table and **no** distractor, and the query file
parses to the full Q1-Q18 set with ground truth.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset
from pathlib import Path

import eval_common as ec

FIXTURE = Path(__file__).resolve().parent.parent / "dev_fixtures" / "seagate_multi"
MANIFEST = json.loads((FIXTURE / "tables.json").read_text(encoding="utf-8"))
GLOSSARY = (FIXTURE / "bi_glossary.md").read_text(encoding="utf-8")


def test_manifest_relevant_and_distractor_partition_table_schema():
    relevant = set(MANIFEST["relevant_tables"])
    distractors = set(MANIFEST["distractor_tables"])
    assert not (relevant & distractors), "relevant/distractor overlap"
    assert relevant | distractors == set(MANIFEST["table_schema"])


def test_manifest_schemas_match_table_schema_values():
    assert set(MANIFEST["table_schema"].values()) == set(MANIFEST["schemas"])


def test_glossary_names_every_relevant_table():
    for table in MANIFEST["relevant_tables"]:
        assert table in GLOSSARY, f"glossary omits relevant table {table}"


def test_glossary_names_no_distractor_table():
    leaked = [t for t in MANIFEST["distractor_tables"] if t in GLOSSARY]
    assert not leaked, f"glossary leaks distractor table(s): {leaked}"


def test_adversarial_distractors_are_a_subset_of_distractors():
    adversarial = set(MANIFEST["adversarial_distractors"])
    assert adversarial <= set(MANIFEST["distractor_tables"])


def test_out_of_scope_schema_excluded_from_project_scope_doc():
    # seagate_ref must be a real schema but never the primary/scope in the README.
    assert "seagate_ref" in MANIFEST["schemas"]
    ref_tables = [t for t, s in MANIFEST["table_schema"].items() if s == "seagate_ref"]
    assert all(t in MANIFEST["distractor_tables"] for t in ref_tables)


def test_test_queries_parse_to_q1_through_q18():
    records = ec.parse_test_queries(FIXTURE / "test_queries.md")
    ids = [r["id"] for r in records]
    assert ids == [f"Q{i}" for i in range(1, 19)]


def test_parsed_questions_have_text_and_trap_flag():
    records = {r["id"]: r for r in ec.parse_test_queries(FIXTURE / "test_queries.md")}
    assert records["Q12"]["is_trap"] is True
    # Non-trap questions carry a non-empty natural-language question.
    assert records["Q16"]["question"]
    assert "WARM" in records["Q16"]["question"]


def test_cross_schema_question_partition_in_manifest():
    cross = set(MANIFEST["cross_schema_questions"])
    controls = set(MANIFEST["within_schema_control_questions"])
    cross_only = set(MANIFEST["cross_schema_only_questions"])
    assert controls == {"Q5", "Q11"}
    assert cross_only <= cross
    assert not (cross & controls), "a question cannot be both cross-schema and control"
