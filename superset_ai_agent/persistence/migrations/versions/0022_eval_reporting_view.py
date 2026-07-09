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

"""Benchmark reporting view for native Superset charting (plan P5.1, DP-B6).

``ai_agent_eval_reporting`` flattens ``results x scores x items x runs`` with
``capability_tags`` unnested to one row per (result, score, tag), so a Superset
dataset on this view charts pass-rate by capability tag / by run / over time
with zero client-side reshaping. A result with no tags still yields one row
(``capability_tag IS NULL``); ``effective_verdict`` folds in human overrides.

**Postgres-only** (the postgres-only deployment topology): SQLite dev DBs have
no ``json_array_elements_text``, so this migration is a documented no-op there
— the in-app BenchmarksPanel remains the results surface for SQLite dev.
"""

from __future__ import annotations

from alembic import op

revision = "0022_eval_reporting_view"
down_revision = "0021_conversation_rewrites"
branch_labels = None
depends_on = None

VIEW_NAME = "ai_agent_eval_reporting"

# One row per (result, score, capability_tag). LEFT JOINs keep score-less and
# tag-less results visible. json_array_elements_text handles the sa.JSON tag
# array; jsonb casts would break on the plain-JSON column type 0019 created.
# View body is built only from constant identifiers — no user input (S608 FP).
_CREATE_VIEW = f"""
CREATE VIEW {VIEW_NAME} AS
SELECT
    res.id                AS result_id,
    res.run_id            AS run_id,
    run.benchmark_id      AS benchmark_id,
    run.project_id        AS project_id,
    run.created_at        AS run_created_at,
    run.status            AS run_status,
    run.trials            AS run_trials,
    run.score             AS run_score,
    run.config            AS run_config,
    res.item_id           AS item_id,
    res.question          AS question,
    res.answer_type       AS answer_type,
    res.trial_index       AS trial_index,
    res.verdict           AS verdict,
    COALESCE(res.override_verdict, res.verdict) AS effective_verdict,
    res.verdict_source    AS verdict_source,
    res.duration_ms       AS duration_ms,
    res.created_at        AS result_created_at,
    tag.capability_tag    AS capability_tag,
    score.name            AS score_name,
    score.value           AS score_value,
    score.label           AS score_label,
    score.source          AS score_source
FROM ai_agent_eval_results AS res
JOIN ai_agent_eval_runs AS run ON run.id = res.run_id
LEFT JOIN ai_agent_eval_items AS item ON item.id = res.item_id
LEFT JOIN LATERAL
    json_array_elements_text(COALESCE(item.capability_tags, '[]'::json))
        AS tag(capability_tag) ON TRUE
LEFT JOIN ai_agent_eval_scores AS score ON score.result_id = res.id
"""  # noqa: S608


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return  # SQLite dev: documented no-op (docstring above).
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
    op.execute(_CREATE_VIEW)


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
