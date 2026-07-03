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

"""The one-time SQLite -> Postgres copy is complete, idempotent, and URI-safe.

Exercised SQLite -> SQLite: the script is dialect-agnostic (plain metadata
inserts + the LargeBinary blob table), so the copy semantics are identical.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from superset_ai_agent.scripts.migrate_to_postgres import migrate
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage


def _seed_source(source_url: str, tmp_path) -> str:
    """Create a legacy-shaped source DB with a conversation and a local doc."""

    from superset_ai_agent.config import AgentConfig
    from superset_ai_agent.persistence.database import run_migrations

    run_migrations(AgentConfig(agent_database_url=source_url))
    engine = sa.create_engine(source_url, future=True)
    now = datetime.now(timezone.utc)
    storage = LocalDocumentStorage(str(tmp_path / "data"))
    storage_uri = storage.write(
        document_id="doc-1", filename="notes.md", content=b"the source bytes"
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO ai_agent_conversations "
                "(id, owner_id, title, kind, database_id, scope, created_at, "
                "updated_at) VALUES (:id, :owner, :title, 'sql', 1, :scope, "
                ":now, :now)"
            ),
            {
                "id": "conv-1",
                "owner": "user-1",
                "title": "t",
                "scope": '{"database_id": 1}',
                "now": now,
            },
        )
        conn.execute(
            sa.text(
                "INSERT INTO ai_agent_messages "
                "(id, conversation_id, owner_id, role, content, sequence, "
                "created_at) VALUES ('msg-1', 'conv-1', 'user-1', 'user', "
                "'hello', 1, :now)"
            ),
            {"now": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO ai_agent_semantic_documents "
                "(id, owner_id, database_id, dataset_ids, filename, "
                "content_type, size_bytes, checksum, storage_uri, status, "
                "warnings, created_at, updated_at) VALUES "
                "('doc-1', 'user-1', 1, '[]', 'notes.md', 'text/markdown', "
                "16, 'c', :uri, 'extracted', '[]', :now, :now)"
            ),
            {"uri": storage_uri, "now": now},
        )
    return storage_uri


def test_migrate_copies_rows_and_document_bytes(tmp_path, capsys) -> None:
    source_url = f"sqlite:///{tmp_path}/source.db"
    target_url = f"sqlite:///{tmp_path}/target.db"
    _seed_source(source_url, tmp_path)

    # Dry run writes nothing.
    assert (
        migrate(
            source_url=source_url,
            target_url=target_url,
            include_documents=True,
            apply=False,
        )
        == 0
    )
    target = sa.create_engine(target_url, future=True)
    with target.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT COUNT(*) FROM ai_agent_conversations")
            ).scalar()
            == 0
        )

    # Apply copies rows, moves bytes, rewrites the URI.
    assert (
        migrate(
            source_url=source_url,
            target_url=target_url,
            include_documents=True,
            apply=True,
        )
        == 0
    )
    with target.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT COUNT(*) FROM ai_agent_conversations")
            ).scalar()
            == 1
        )
        assert (
            conn.execute(sa.text("SELECT COUNT(*) FROM ai_agent_messages")).scalar()
            == 1
        )
        uri = conn.execute(
            sa.text("SELECT storage_uri FROM ai_agent_semantic_documents")
        ).scalar()
        assert uri == "agent-db://documents/doc-1/notes.md"
        data = conn.execute(
            sa.text("SELECT data FROM ai_agent_document_blobs")
        ).scalar()
        assert bytes(data) == b"the source bytes"

    # Idempotent: a second apply copies nothing new.
    assert (
        migrate(
            source_url=source_url,
            target_url=target_url,
            include_documents=True,
            apply=True,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "target already has every source row" in out
    with target.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT COUNT(*) FROM ai_agent_document_blobs")
            ).scalar()
            == 1
        )


def test_migrate_refuses_same_database(tmp_path) -> None:
    url = f"sqlite:///{tmp_path}/one.db"
    assert (
        migrate(source_url=url, target_url=url, include_documents=False, apply=True)
        == 1
    )
