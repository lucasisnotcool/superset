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

from superset_ai_agent.conversations.schemas import ConversationArtifact
from superset_ai_agent.schemas import AgentQueryResponse, SqlValidation


def test_agent_query_response_accepts_legacy_payload() -> None:
    response = AgentQueryResponse.model_validate(
        {
            "status": "needs_review",
            "sql": "SELECT 1",
            "explanation": "A test query.",
            "validation": {
                "is_valid": True,
                "is_read_only": True,
                "normalized_sql": "SELECT 1",
                "errors": [],
            },
            "execution_result": None,
            "trace": [],
        }
    )

    assert response.insight_cards == []
    assert response.chart_spec is None
    assert response.recommended_followups == []


def test_conversation_artifact_defaults_new_artifact_fields() -> None:
    artifact = ConversationArtifact(
        sql="SELECT 1",
        validation=SqlValidation(is_valid=True, is_read_only=True),
    )

    assert artifact.answer_summary is None
    assert artifact.insight_cards == []
    assert artifact.chart_spec is None
    assert artifact.data_preview is None
    assert artifact.audit is None
    assert artifact.recommended_followups == []
    assert artifact.wren_context is None
