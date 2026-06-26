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

import pytest
from sqlalchemy import inspect

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.conversations.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from superset_ai_agent.persistence.database import (
    create_all_for_tests,
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)


def _config(database_url: str, **overrides) -> AgentConfig:
    return AgentConfig(
        agent_database_url=database_url,
        identity_provider="static",
        superset_auth_mode="service_account",
        **overrides,
    )


def test_run_migrations_upgrades_empty_database_and_is_idempotent(tmp_path) -> None:
    config = _config(f"sqlite+pysqlite:///{tmp_path / 'agent.db'}")

    run_migrations(config)
    run_migrations(config)

    engine = create_engine_from_config(config)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    assert "alembic_version" in table_names
    assert {
        "ai_agent_conversations",
        "ai_agent_messages",
        "ai_agent_artifacts",
        "ai_agent_semantic_documents",
        "ai_agent_events",
        "ai_agent_semantic_projects",
        "ai_agent_semantic_project_grants",
        "ai_agent_semantic_access_proofs",
        "ai_agent_semantic_mdl_files",
    }.issubset(table_names)

    store = SqlAlchemyConversationStore(create_session_factory(engine))
    conversation = store.create(
        ConversationScope(
            database_id=1,
            catalog_name="prod",
            schema_name="pipeline",
            dataset_ids=[],
        ),
        owner_id="user-1",
    )
    assert conversation.scope.schema_name == "pipeline"


def test_conversation_kind_project_columns_present_after_migration(tmp_path) -> None:
    config = _config(f"sqlite+pysqlite:///{tmp_path / 'agent.db'}")

    run_migrations(config)

    engine = create_engine_from_config(config)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("ai_agent_conversations")
    }
    assert {"kind", "project_id"}.issubset(columns)

    # A copilot thread persists its kind + project binding.
    store = SqlAlchemyConversationStore(create_session_factory(engine))
    conversation = store.create(
        ConversationScope(database_id=7),
        owner_id="user-1",
        kind="copilot",
        project_id="proj-1",
    )
    reloaded = store.get(conversation.id, owner_id="user-1")
    assert reloaded.kind == "copilot"
    assert reloaded.project_id == "proj-1"


def test_migration_0008_downgrade_drops_columns(tmp_path) -> None:
    from alembic import command

    from superset_ai_agent.persistence.database import _alembic_config

    config = _config(f"sqlite+pysqlite:///{tmp_path / 'agent.db'}")
    run_migrations(config)

    alembic_cfg = _alembic_config(config)
    command.downgrade(alembic_cfg, "0007_document_chunks")

    engine = create_engine_from_config(config)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("ai_agent_conversations")
    }
    assert "kind" not in columns
    assert "project_id" not in columns

    # Upgrade back to head leaves the columns in place (round-trip is clean).
    command.upgrade(alembic_cfg, "head")
    columns = {
        column["name"]
        for column in inspect(create_engine_from_config(config)).get_columns(
            "ai_agent_conversations"
        )
    }
    assert {"kind", "project_id"}.issubset(columns)


def test_run_migrations_rejects_unversioned_existing_agent_tables(tmp_path) -> None:
    config = _config(f"sqlite+pysqlite:///{tmp_path / 'agent.db'}")
    engine = create_engine_from_config(config)
    create_all_for_tests(engine)

    with pytest.raises(RuntimeError, match="without Alembic version state"):
        run_migrations(config)


def test_run_migrations_can_stamp_existing_development_tables(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'agent.db'}"
    config = _config(database_url)
    engine = create_engine_from_config(config)
    create_all_for_tests(engine)

    run_migrations(
        _config(database_url, agent_migration_bootstrap="stamp_existing")
    )

    inspector = inspect(create_engine_from_config(config))
    assert "alembic_version" in inspector.get_table_names()
