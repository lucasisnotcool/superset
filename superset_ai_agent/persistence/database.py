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

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.models import Base


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
    """Create agent-owned persistence tables.

    This is intentionally narrow and local to the standalone agent. The function
    name leaves room for swapping in Alembic revision execution later without
    changing app wiring.
    """

    engine = create_engine_from_config(config)
    Base.metadata.create_all(engine)


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return
    database = url.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
