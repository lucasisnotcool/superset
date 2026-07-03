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

"""One-time copy of agent state from the legacy SQLite database into Postgres.

Finalizes the postgres-only persistence migration for a deployment that already
has data: every ``ai_agent_*`` relational row moves from the SQLite file to the
target database, and (with ``--include-documents``) locally-stored raw document
bytes move into the ``ai_agent_document_blobs`` table with their ``storage_uri``
rewritten to the ``agent-db://`` scheme. Vector indexes are deliberately NOT
migrated — they are checksum/signature-keyed caches that re-embed on first use.

Idempotent: rows whose primary key already exists in the target are skipped, so
a partial run can simply be re-run. Both databases are migrated to the Alembic
head first, so source and target schemas always match.

Usage (inside the agent container/venv)::

    # Report what would move (default, no writes):
    python -m superset_ai_agent.scripts.migrate_to_postgres \
        --source sqlite:////app/.data/ai_agent.db \
        --target postgresql+psycopg://user:pass@host:5432/db

    # Perform the copy, including raw document bytes from local storage:
    python -m superset_ai_agent.scripts.migrate_to_postgres \
        --source sqlite:////app/.data/ai_agent.db \
        --target postgresql+psycopg://user:pass@host:5432/db \
        --include-documents --apply

``--target`` defaults to the configured ``AI_AGENT_DATABASE_URL``, so after the
env flip the only required argument is ``--source``.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlparse

import sqlalchemy as sa

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.persistence.database import run_migrations
from superset_ai_agent.persistence.models import Base


def _copy_plan(
    source: sa.engine.Engine, target: sa.engine.Engine
) -> list[tuple[sa.Table, list[dict]]]:
    """Rows missing from the target, per table, in FK-safe insert order."""

    plan: list[tuple[sa.Table, list[dict]]] = []
    with source.connect() as src, target.connect() as dst:
        for table in Base.metadata.sorted_tables:
            pk_columns = [column.name for column in table.primary_key.columns]
            rows = [dict(row) for row in src.execute(sa.select(table)).mappings()]
            if not rows:
                plan.append((table, []))
                continue
            existing = {
                tuple(row)
                for row in dst.execute(
                    sa.select(*[table.c[name] for name in pk_columns])
                )
            }
            missing = [
                row
                for row in rows
                if tuple(row[name] for name in pk_columns) not in existing
            ]
            plan.append((table, missing))
    return plan


def _migrate_documents(
    target: sa.engine.Engine, *, apply: bool
) -> tuple[int, int, list[str]]:
    """Move ``file://`` document bytes into the blob table; rewrite URIs.

    Operates on the TARGET rows (run after the relational copy). Returns
    ``(moved, skipped_non_local, warnings)``. S3-stored documents are left
    untouched — their URIs keep working as long as S3 access remains.
    """

    from superset_ai_agent.persistence.database import create_session_factory
    from superset_ai_agent.semantic_layer.file_storage import (
        _path_from_file_uri,
        PostgresDocumentStorage,
    )

    session_factory = create_session_factory(target)
    storage = PostgresDocumentStorage(session_factory)
    moved = 0
    skipped = 0
    warnings: list[str] = []
    with target.connect() as conn:
        documents = conn.execute(
            sa.text("SELECT id, filename, storage_uri FROM ai_agent_semantic_documents")
        ).fetchall()
    for document_id, filename, storage_uri in documents:
        scheme = urlparse(storage_uri).scheme
        if scheme == "agent-db":
            continue  # already migrated
        if scheme != "file":
            skipped += 1
            continue
        try:
            content = _path_from_file_uri(storage_uri).read_bytes()
        except OSError as ex:
            warnings.append(f"{document_id} ({filename}): unreadable source: {ex}")
            continue
        if apply:
            new_uri = storage.write(
                document_id=document_id, filename=filename, content=content
            )
            with target.begin() as conn:
                conn.execute(
                    sa.text(
                        "UPDATE ai_agent_semantic_documents "
                        "SET storage_uri = :uri, updated_at = :now WHERE id = :id"
                    ),
                    {
                        "uri": new_uri,
                        "now": datetime.now(timezone.utc),
                        "id": document_id,
                    },
                )
        moved += 1
    return moved, skipped, warnings


def migrate(
    *,
    source_url: str,
    target_url: str,
    include_documents: bool,
    apply: bool,
    bootstrap: str = "error",
) -> int:
    if source_url == target_url:
        print("Source and target are the same database; nothing to do.")
        return 1
    base = AgentConfig.from_env()
    # Bring BOTH schemas to head so column sets match row-for-row.
    run_migrations(
        replace(
            base, agent_database_url=source_url, agent_migration_bootstrap=bootstrap
        )
    )
    run_migrations(
        replace(
            base, agent_database_url=target_url, agent_migration_bootstrap=bootstrap
        )
    )
    source = sa.create_engine(source_url, future=True)
    target = sa.create_engine(target_url, future=True)

    plan = _copy_plan(source, target)
    total = 0
    for table, missing in plan:
        if not missing:
            continue
        total += len(missing)
        verb = "copying" if apply else "would copy"
        print(f"{verb:>10}  {len(missing):>6}  {table.name}")
        if apply:
            with target.begin() as conn:
                conn.execute(table.insert(), missing)
    if total == 0:
        print("Relational state: target already has every source row.")
    if include_documents:
        # Apply reads the freshly-copied target rows; a dry run previews from
        # the source (the target has no rows yet).
        moved, skipped, warnings = _migrate_documents(
            target if apply else source, apply=apply
        )
        verb = "moved" if apply else "would move"
        print(f"Documents: {verb} {moved} local file(s) into ai_agent_document_blobs")
        if skipped:
            print(f"Documents: left {skipped} non-local (e.g. s3://) URI(s) untouched")
        for warning in warnings:
            print(f"WARNING: {warning}")
    if not apply:
        print("Dry run only — re-run with --apply to write.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        required=True,
        help="SQLAlchemy URL of the legacy database (e.g. sqlite:////app/.data/ai_agent.db)",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Target database URL; defaults to the configured AI_AGENT_DATABASE_URL",
    )
    parser.add_argument(
        "--include-documents",
        action="store_true",
        help="Also move file:// document bytes into ai_agent_document_blobs",
    )
    parser.add_argument(
        "--migration-bootstrap",
        default="error",
        choices=["error", "stamp_existing"],
        help="Forwarded to the pre-copy schema migration of both databases",
    )
    parser.add_argument("--apply", action="store_true", help="Write (default: dry run)")
    args = parser.parse_args()
    target = args.target or AgentConfig.from_env().agent_database_url
    return migrate(
        source_url=args.source,
        target_url=target,
        include_documents=args.include_documents,
        apply=args.apply,
        bootstrap=args.migration_bootstrap,
    )


if __name__ == "__main__":
    sys.exit(main())
