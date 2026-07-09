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
"""Offline tests for rig.fixture (manifest load/validate, no stack)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval test tooling

import pytest
from rig import fixture as fx


def _write(tmp_path, manifest: dict, *, corpus="questions.csv", ctx=None):
    (tmp_path / corpus).write_text("id,question,answer_type,expected_values\n")
    for name, body in (ctx or {}).items():
        (tmp_path / name).write_text(body)
    p = tmp_path / "fixture.json"
    p.write_text(json.dumps(manifest))
    return p


def test_valid_manifest_resolves_paths(tmp_path):
    p = _write(
        tmp_path,
        {
            "id": "demo",
            "database_name": "examples",
            "schemas": ["S1", "S2"],
            "onboard_mode": "auto",
            "corpus": "questions.csv",
            "context_docs": ["ctx.md"],
        },
        ctx={"ctx.md": "hello"},
    )
    f = fx.load_fixture(p)
    assert f.id == "demo"
    assert f.schemas == ("S1", "S2")
    assert f.onboard_mode == "auto"
    assert f.corpus_path.is_file()
    assert len(f.context_docs) == 1
    assert f.context_docs[0].name == "ctx.md"
    assert f.grounding_modes == fx.DEFAULT_GROUNDING_MODES  # defaulted


def test_missing_id_rejected(tmp_path):
    p = _write(
        tmp_path, {"database_name": "x", "schemas": ["S"], "corpus": "questions.csv"}
    )
    with pytest.raises(fx.FixtureError, match="id"):
        fx.load_fixture(p)


def test_empty_schemas_rejected(tmp_path):
    p = _write(
        tmp_path,
        {"id": "d", "database_name": "x", "schemas": [], "corpus": "questions.csv"},
    )
    with pytest.raises(fx.FixtureError, match="schemas"):
        fx.load_fixture(p)


def test_no_connection_reference_rejected(tmp_path):
    p = _write(tmp_path, {"id": "d", "schemas": ["S"], "corpus": "questions.csv"})
    with pytest.raises(fx.FixtureError, match="database_name / database_id"):
        fx.load_fixture(p)


def test_connection_uri_env_satisfies_connection_requirement(tmp_path):
    p = _write(
        tmp_path,
        {
            "id": "d",
            "connection_uri_env": "EVAL_ORACLE_URI",
            "schemas": ["S"],
            "corpus": "questions.csv",
        },
    )
    f = fx.load_fixture(p)
    assert f.connection_uri_env == "EVAL_ORACLE_URI"
    assert f.database_name is None
    assert f.database_id is None


def test_bad_onboard_mode_rejected(tmp_path):
    p = _write(
        tmp_path,
        {
            "id": "d",
            "database_name": "x",
            "schemas": ["S"],
            "corpus": "questions.csv",
            "onboard_mode": "turbo",
        },
    )
    with pytest.raises(fx.FixtureError, match="onboard_mode"):
        fx.load_fixture(p)


def test_missing_corpus_file_rejected(tmp_path):
    (tmp_path / "fixture.json").write_text(
        json.dumps(
            {"id": "d", "database_name": "x", "schemas": ["S"], "corpus": "nope.csv"}
        )
    )
    with pytest.raises(fx.FixtureError, match="corpus not found"):
        fx.load_fixture(tmp_path / "fixture.json")


def test_missing_manifest_rejected(tmp_path):
    with pytest.raises(fx.FixtureError, match="not found"):
        fx.load_fixture(tmp_path / "does_not_exist.json")
