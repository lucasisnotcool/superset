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

"""Phase 0.0 — durable semantic persistence baseline (wren_full.md)."""

from __future__ import annotations

import pytest

from superset_ai_agent.app import (
    _parity_features_enabled,
    _requires_agent_database,
    _validate_semantic_persistence_config,
)
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.semantic_layer.mdl_files import SqlAlchemyMdlFileStore
from superset_ai_agent.semantic_layer.schemas import (
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
)

_MDL_YAML = (
    "models:\n"
    "  - name: gross_moves\n"
    "    table_reference:\n"
    "      schema: public\n"
    "      table: gross_moves\n"
    "    columns:\n"
    "      - name: stage\n"
    "        type: VARCHAR\n"
)


def _config(**overrides) -> AgentConfig:
    return AgentConfig(**overrides)


def test_parity_features_disabled_by_default() -> None:
    assert _parity_features_enabled(_config()) is False
    # Default config keeps the in-memory store legal.
    _validate_semantic_persistence_config(_config())


@pytest.mark.parametrize(
    "overrides",
    [
        {"wren_engine": "wren_core"},
        {"wren_retriever": "embedding"},
        {"wren_memory_store": "sqlalchemy"},
    ],
)
def test_parity_feature_requires_sqlalchemy_persistence(overrides) -> None:
    config = _config(semantic_layer_store="memory", **overrides)
    assert _parity_features_enabled(config) is True
    with pytest.raises(ValueError, match="durable semantic persistence"):
        _validate_semantic_persistence_config(config)


def test_parity_feature_allowed_with_sqlalchemy_persistence() -> None:
    config = _config(semantic_layer_store="sqlalchemy", wren_engine="wren_core")
    # Does not raise.
    _validate_semantic_persistence_config(config)


def test_memory_store_sqlalchemy_requires_agent_database() -> None:
    assert _requires_agent_database(_config(wren_memory_store="sqlalchemy")) is True
    assert _requires_agent_database(_config()) is False


def test_mdl_survives_restart_with_sqlalchemy_store(tmp_path) -> None:
    """Create + activate an MDL file, then re-open the DB and assert it persists."""

    database_url = f"sqlite+pysqlite:///{tmp_path / 'agent.db'}"
    config = _config(
        agent_database_url=database_url,
        semantic_layer_store="sqlalchemy",
    )
    run_migrations(config)
    engine = create_engine_from_config(config)

    # First "process": write and activate a draft MDL file.
    store = SqlAlchemyMdlFileStore(create_session_factory(engine))
    created = store.create(
        "project-1",
        MdlFileCreateRequest(path="models/gross_moves.yaml", content=_MDL_YAML),
    )
    store.update(created.id, MdlFileUpdateRequest(status="active"))

    # Second "process": brand-new engine + store on the same database file.
    engine2 = create_engine_from_config(config)
    store2 = SqlAlchemyMdlFileStore(create_session_factory(engine2))
    reloaded = store2.list("project-1")

    assert [file.path for file in reloaded] == ["models/gross_moves.yaml"]
    assert reloaded[0].status == "active"
    assert reloaded[0].content == _MDL_YAML
