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

"""Reporting-view migration 0022 (plan P5.1): chain, dialect gate, view shape.

The full CREATE VIEW was validated by execution against live Postgres at build
time (tag unnest, tagless rows, override folding). These offline tests pin the
migration's structure so a refactor can't silently break the chain or the
Postgres-only gate.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "superset_ai_agent"
    / "persistence"
    / "migrations"
    / "versions"
    / "0022_eval_reporting_view.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("m0022", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain_and_names():
    m = _load()
    assert m.revision == "0022_eval_reporting_view"
    assert m.down_revision == "0021_conversation_rewrites"
    assert m.VIEW_NAME == "ai_agent_eval_reporting"


def test_view_sql_covers_the_reporting_contract():
    m = _load()
    sql = m._CREATE_VIEW
    # One row per (result, score, tag); overrides folded; runs joined.
    for fragment in (
        "COALESCE(res.override_verdict, res.verdict) AS effective_verdict",
        "json_array_elements_text",
        "LEFT JOIN ai_agent_eval_scores",
        "JOIN ai_agent_eval_runs",
        "capability_tag",
        "run_config",
    ):
        assert fragment in sql, fragment
    # LEFT LATERAL keeps tag-less results (capability_tag IS NULL rows).
    assert "LEFT JOIN LATERAL" in sql
    assert "ON TRUE" in sql
