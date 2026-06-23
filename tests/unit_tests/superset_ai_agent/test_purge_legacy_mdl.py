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

from __future__ import annotations

import sqlalchemy as sa

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_engine_from_config,
)
from superset_ai_agent.scripts.purge_legacy_mdl import _counts, _purge, main


def _seed(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO ai_agent_semantic_projects "
            "(id,name,owner_id,database_uri_fingerprint,schema_name,visibility,"
            "status,created_at,updated_at,current_version_id) VALUES "
            "('p1','proj','o','fp','public','db_access','active',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'v1')"
        )
    )
    # One legacy YAML file, one native JSON file.
    connection.execute(
        sa.text(
            "INSERT INTO ai_agent_semantic_mdl_files "
            "(id,project_id,path,filename,content,content_type,source_type,"
            "status,checksum,created_at,updated_at) VALUES "
            "('m1','p1','models/a.yaml','a.yaml','models:\n  - name: a\n',"
            "'application/x-yaml','manual','active','c1',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO ai_agent_semantic_mdl_files "
            "(id,project_id,path,filename,content,content_type,source_type,"
            "status,checksum,created_at,updated_at) VALUES "
            "('m2','p1','models/b.json','b.json','{\"models\":[]}',"
            "'application/json','manual','draft','c2',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    )


def _engine(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'agent.db'}"
    config = AgentConfig(agent_database_url=url, semantic_layer_store="sqlalchemy")
    engine = create_engine_from_config(config)
    create_all_for_tests(engine)
    return engine, url


def test_purge_removes_legacy_keeps_json_and_resets_pointers(tmp_path) -> None:
    engine, _ = _engine(tmp_path)
    with engine.begin() as connection:
        _seed(connection)
        assert _counts(connection)["legacy_mdl_files"] == 1
        _purge(connection)
        after = _counts(connection)
        assert after["legacy_mdl_files"] == 0
        assert after["json_mdl_files"] == 1  # native file untouched
        remaining = [
            row[0]
            for row in connection.execute(
                sa.text("SELECT id FROM ai_agent_semantic_mdl_files")
            )
        ]
        assert remaining == ["m2"]
        current = connection.execute(
            sa.text("SELECT current_version_id FROM ai_agent_semantic_projects")
        ).scalar()
        assert current is None


def test_main_dry_run_does_not_write(tmp_path, monkeypatch, capsys) -> None:
    engine, url = _engine(tmp_path)
    with engine.begin() as connection:
        _seed(connection)
    monkeypatch.setenv("AI_AGENT_DATABASE_URL", url)
    monkeypatch.setenv("AI_AGENT_SEMANTIC_LAYER_STORE", "sqlalchemy")

    assert main([]) == 0  # dry run (no --apply)
    out = capsys.readouterr().out
    assert "Dry run" in out

    with engine.connect() as connection:
        assert _counts(connection)["legacy_mdl_files"] == 1  # nothing purged


def test_main_apply_purges(tmp_path, monkeypatch, capsys) -> None:
    engine, url = _engine(tmp_path)
    with engine.begin() as connection:
        _seed(connection)
    monkeypatch.setenv("AI_AGENT_DATABASE_URL", url)
    monkeypatch.setenv("AI_AGENT_SEMANTIC_LAYER_STORE", "sqlalchemy")

    assert main(["--apply"]) == 0
    assert "Purged 1 legacy MDL file" in capsys.readouterr().out

    with engine.connect() as connection:
        assert _counts(connection)["legacy_mdl_files"] == 0
