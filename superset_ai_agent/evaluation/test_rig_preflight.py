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
"""Offline tests for rig.preflight (pure checks + fake-client resolution)."""

from __future__ import annotations

from rig import corpus, preflight
from rig.fixture import Fixture


def _corpus(text):
    return corpus.load_corpus_csv(text)


_EVAL_NOTE_CSV = (
    'id,question,answer_type,eval_note\nQ1,"is it up?",eval_note,"states the trend"\n'
)
_EV_CSV = "id,question,answer_type,expected_values\nQ1,q,expected_values,6\n"


def test_check_corpus_surfaces_errors():
    load = _corpus("question\nx\n")  # missing required headers
    assert preflight.check_corpus(load)


def test_check_schemas_case_insensitive_subset():
    assert preflight.check_schemas(["SALES", "SUPPLY"], ("sales",)) == []
    problems = preflight.check_schemas(["SALES"], ("supply",))
    assert problems
    assert "supply" in problems[0]


def test_needs_judge_detects_eval_note():
    assert preflight.needs_judge(_corpus(_EVAL_NOTE_CSV))
    assert not preflight.needs_judge(_corpus(_EV_CSV))


class _FakeConfig:
    database_id = None
    database_name = None


class _FakeClient:
    def __init__(self, *, db_id=7, schemas=("SALES",)):
        self.config = _FakeConfig()
        self._db_id = db_id
        self._schemas = list(schemas)

    def resolve_database_id(self):
        if self._db_id is None:
            raise RuntimeError("not found")
        return self._db_id

    def _superset(self, method, path, **kwargs):
        return {"result": self._schemas}

    def _ok(self, resp, what):
        return resp


def _fixture(tmp_path, **kw):
    (tmp_path / "c.csv").write_text("x")
    base = {
        "id": "f",
        "schemas": ("SALES",),
        "corpus_path": tmp_path / "c.csv",
        "manifest_dir": tmp_path,
    }
    base.update(kw)
    return Fixture(**base)


def test_resolve_connection_by_name(tmp_path):
    client = _FakeClient(db_id=7)
    fixture = _fixture(tmp_path, database_name="examples")
    db_id, problems = preflight.resolve_connection(client, fixture)
    assert db_id == 7
    assert not problems


def test_resolve_connection_uri_env_unset_is_problem(tmp_path, monkeypatch):
    monkeypatch.delenv("EVAL_ORACLE_URI", raising=False)
    client = _FakeClient()
    fixture = _fixture(
        tmp_path, database_name=None, connection_uri_env="EVAL_ORACLE_URI"
    )
    db_id, problems = preflight.resolve_connection(client, fixture)
    assert db_id is None
    assert any("unset" in p for p in problems)


def test_run_preflight_ok_path(tmp_path):
    client = _FakeClient(db_id=7, schemas=("SALES", "SUPPLY"))
    fixture = _fixture(tmp_path, database_name="examples", schemas=("SALES",))
    report = preflight.run_preflight(client, fixture, _corpus(_EV_CSV))
    assert report.ok, report.problems
    assert report.database_id == 7


def test_run_preflight_missing_schema_fails(tmp_path):
    client = _FakeClient(db_id=7, schemas=("SALES",))
    fixture = _fixture(tmp_path, database_name="examples", schemas=("SUPPLY",))
    report = preflight.run_preflight(client, fixture, _corpus(_EV_CSV))
    assert not report.ok
    assert any("schema" in p for p in report.problems)


def test_run_preflight_bad_corpus_stops_early(tmp_path):
    client = _FakeClient()
    fixture = _fixture(tmp_path, database_name="examples")
    report = preflight.run_preflight(client, fixture, _corpus("question\nx\n"))
    assert not report.ok
    assert report.database_id is None  # never got to connection resolution


def test_run_preflight_eval_note_without_client_warns(tmp_path):
    client = _FakeClient(db_id=7, schemas=("SALES",))
    fixture = _fixture(tmp_path, database_name="examples", schemas=("SALES",))
    report = preflight.run_preflight(
        client, fixture, _corpus(_EVAL_NOTE_CSV), model_client=None
    )
    assert report.ok  # warning, not a problem
    assert any("eval_note" in w for w in report.warnings)
