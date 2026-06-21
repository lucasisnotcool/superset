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

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.models import Base

_HERE = Path(__file__).resolve().parent
_ALEMBIC_INI = _HERE / "alembic.ini"
_MIGRATIONS_DIR = _HERE / "migrations"


def create_engine_from_config(config: AgentConfig) -> Engine:
    """Create the SQLAlchemy engine for agent-owned persistence."""

    _ensure_sqlite_parent(config.agent_database_url)
    return create_engine(
        config.agent_database_url,
        echo=config.agent_database_echo,
        future=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a synchronous SQLAlchemy session factory."""

    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def run_migrations(config: AgentConfig) -> None:
    """Run agent-owned Alembic migrations to the latest revision."""

    engine = create_engine_from_config(config)
    alembic_config = _alembic_config(config)
    if _has_unversioned_agent_tables(engine):
        if config.agent_migration_bootstrap != "stamp_existing":
            raise RuntimeError(
                "Agent persistence tables exist without Alembic version state. "
                "Set AI_AGENT_MIGRATION_BOOTSTRAP=stamp_existing once to mark "
                "the existing development database as migrated, or migrate a "
                "fresh database."
            )
        command.stamp(alembic_config, "head")
        return
    command.upgrade(alembic_config, "head")


def create_all_for_tests(engine: Engine) -> None:
    """Create agent-owned tables for isolated store unit tests."""

    Base.metadata.create_all(engine)


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _alembic_config(config: AgentConfig) -> Config:
    alembic_config = Config(str(_ALEMBIC_INI))
    alembic_config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    alembic_config.set_main_option(
        "sqlalchemy.url",
        config.agent_database_url.replace("%", "%%"),
    )
    return alembic_config


def _has_unversioned_agent_tables(engine: Engine) -> bool:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    return "alembic_version" not in table_names and any(
        table_name.startswith("ai_agent_") for table_name in table_names
    )
