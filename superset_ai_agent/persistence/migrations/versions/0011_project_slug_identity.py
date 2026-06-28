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

"""First-class project identity: add ``slug`` and key projects by it.

Moves project identity from ``(fingerprint, catalog, schema_name)`` to
``(fingerprint, catalog, slug)`` so a database can hold many named projects and a
project can be duplicated. ``schema_name`` stays as the primary-schema column but
leaves the identity key. Expand/contract: add ``slug`` nullable, backfill a unique
slug per (database, catalog) from each project's name, then swap the unique
constraint. The slugify here is inlined (a migration must not drift with app code).

Revision ID: 0011_project_slug_identity
Revises: 0010_semantic_project_schemas
Create Date: 2026-06-28 00:00:00.000000
"""

from __future__ import annotations

from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "0011_project_slug_identity"
down_revision = "0010_semantic_project_schemas"
branch_labels = None
depends_on = None

_TABLE = "ai_agent_semantic_projects"
_OLD_UQ = "uq_ai_agent_semantic_project_scope_deleted"
_NEW_UQ = "uq_ai_agent_semantic_project_slug_active"
_SLUG_MAX_LEN = 80


def _slugify(name: str) -> str:
    lowered = (name or "").strip().lower()
    out: list[str] = []
    prev_hyphen = False
    for char in lowered:
        if char.isalnum():
            out.append(char)
            prev_hyphen = False
        elif not prev_hyphen:
            out.append("-")
            prev_hyphen = True
    slug = "".join(out).strip("-")[:_SLUG_MAX_LEN].strip("-")
    return slug or "project"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("slug", sa.String(length=255), nullable=True))

    bind = op.get_bind()
    projects = sa.table(
        _TABLE,
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("slug", sa.String),
        sa.column("database_uri_fingerprint", sa.String),
        sa.column("catalog_name", sa.String),
    )
    rows = bind.execute(
        sa.select(
            projects.c.id,
            projects.c.name,
            projects.c.database_uri_fingerprint,
            projects.c.catalog_name,
        )
    ).fetchall()

    # Assign a slug unique within each (database, catalog) group, deterministic by id.
    taken: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in sorted(rows, key=lambda r: r.id):
        key = (row.database_uri_fingerprint, row.catalog_name or "")
        base = _slugify(row.name)
        slug = base
        index = 2
        while slug in taken[key]:
            slug = f"{base}-{index}"
            index += 1
        taken[key].add(slug)
        bind.execute(
            sa.update(projects)
            .where(projects.c.id == row.id)
            .values(slug=slug)
        )

    # Make slug non-nullable + drop the old (schema-based) unique constraint
    # (batch mode = SQLite-safe). Identity uniqueness then comes from a partial
    # unique index on active rows (created outside batch).
    with op.batch_alter_table(_TABLE, schema=None) as batch:
        batch.alter_column("slug", existing_type=sa.String(length=255), nullable=False)
        batch.drop_constraint(_OLD_UQ, type_="unique")
    op.create_index(
        _NEW_UQ,
        _TABLE,
        ["database_uri_fingerprint", "catalog_name", "slug"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_ai_agent_semantic_projects_slug", _TABLE, ["slug"])


def downgrade() -> None:
    op.drop_index("ix_ai_agent_semantic_projects_slug", table_name=_TABLE)
    op.drop_index(_NEW_UQ, table_name=_TABLE)
    with op.batch_alter_table(_TABLE, schema=None) as batch:
        batch.create_unique_constraint(
            _OLD_UQ,
            ["database_uri_fingerprint", "catalog_name", "schema_name", "deleted_at"],
        )
    op.drop_column(_TABLE, "slug")
