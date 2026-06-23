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

"""Purge legacy YAML/snake-case MDL data (native-JSON rebuild, wren_full.md D2).

The MDL authoring/storage format moved from a snake_case YAML dialect to
wren-core's native camelCase JSON. Pre-existing MDL file rows hold YAML content
that no longer parses, so this one-time data migration removes them and the
derived semantic-layer state built from them. Scope rows (projects) and uploaded
business documents (markdown/text) are preserved; the semantic layer simply
re-materializes from re-authored JSON on next onboarding/activation.

Only legacy rows are affected: a fresh install (all MDL files already
``application/json``) deletes nothing. This migration runs automatically when
``AI_AGENT_RUN_MIGRATIONS=true``; for a manual one-time run see
``superset_ai_agent/scripts/purge_legacy_mdl.py``.

Revision ID: 0004_purge_legacy_yaml_mdl
Revises: 0003_nl_sql_examples
Create Date: 2026-06-23 00:00:00.000000
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0004_purge_legacy_yaml_mdl"
down_revision = "0003_nl_sql_examples"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: The only valid MDL content type after the native-JSON rebuild.
_JSON_CONTENT_TYPE = "application/json"


def upgrade() -> None:
    bind = op.get_bind()

    legacy_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM ai_agent_semantic_mdl_files "
            "WHERE content_type <> :ct"
        ),
        {"ct": _JSON_CONTENT_TYPE},
    ).scalar()

    if not legacy_count:
        logger.info("No legacy YAML MDL files found; nothing to purge.")
        return

    logger.warning(
        "Purging %s legacy YAML MDL file(s) and derived semantic-layer state "
        "(native-JSON rebuild). Projects and documents are preserved.",
        legacy_count,
    )

    # 1. The YAML-bearing MDL files themselves (the rows that no longer parse).
    bind.execute(
        sa.text(
            "DELETE FROM ai_agent_semantic_mdl_files WHERE content_type <> :ct"
        ),
        {"ct": _JSON_CONTENT_TYPE},
    )
    # 2. Derived state built from the old MDL: materialized versions and the
    #    query-time context cache. Both regenerate on the next materialize.
    bind.execute(sa.text("DELETE FROM ai_agent_semantic_layer_versions"))
    bind.execute(sa.text("DELETE FROM ai_agent_wren_context_cache"))
    # 3. Detach projects from their now-deleted current version so the UI shows
    #    "not materialized" and prompts re-onboarding.
    bind.execute(
        sa.text("UPDATE ai_agent_semantic_projects SET current_version_id = NULL")
    )


def downgrade() -> None:
    # Irreversible data migration: purged YAML MDL content cannot be restored
    # (and would not be loadable by the native-JSON engine if it were).
    pass
