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

"""Back-compat adapter over the deterministic SQL policy.

The bespoke keyword/AST denylists that used to live here have been removed in
favour of ``superset_ai_agent.tools.sql_policy`` (R1), which classifies SQL with
Superset core's ``SQLScript``. This module keeps the historical
``validate_read_only_sql`` entrypoint so existing graph/app/pipeline call sites
stay unchanged; it now builds a :class:`SqlValidation` from the policy verdict.
"""

from __future__ import annotations

from superset_ai_agent.schemas import SqlValidation
from superset_ai_agent.tools.sql_policy import apply_limit, classify_sql, PolicyMode


def validate_read_only_sql(
    sql: str,
    *,
    dialect: str | None = None,
    default_limit: int = 1000,
    policy_mode: PolicyMode = "strict",
) -> SqlValidation:
    """Classify ``sql`` and build a :class:`SqlValidation`.

    ``dialect`` is the Superset database backend string (e.g. ``"postgresql"``)
    as returned by ``SupersetClient.get_database_dialect``; it is forwarded to
    the policy as the parsing engine. A LIMIT is appended only for read-only
    SQL that lacks a top-level one. ``policy_mode`` is the operator-configured
    strictness (``AgentConfig.sql_policy_mode``).
    """

    classification = classify_sql(sql, engine=dialect, policy_mode=policy_mode)
    is_read_only = classification.is_read_only
    normalized_sql = (
        apply_limit(sql, engine=dialect, default_limit=default_limit)
        if is_read_only
        else None
    )
    return SqlValidation(
        is_valid=is_read_only,
        is_read_only=is_read_only,
        classification=classification.kind,
        reason=classification.reason,
        normalized_sql=normalized_sql,
        dialect=dialect,
        errors=[] if is_read_only else [classification.reason],
    )
