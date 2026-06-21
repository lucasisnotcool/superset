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
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from superset_ai_agent.conversations.schemas import (
    ConversationArtifact,
    ConversationMessage,
    ConversationScope,
)
from superset_ai_agent.conversations.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from superset_ai_agent.conversations.store import (
    ConversationArtifactNotFoundError,
    ConversationNotFoundError,
)
from superset_ai_agent.persistence.database import create_session_factory
from superset_ai_agent.persistence.models import Base
from superset_ai_agent.schemas import (
    ChartEncoding,
    ChartSpec,
    ExecutionResult,
    InsightCard,
    SqlValidation,
)


def _store() -> SqlAlchemyConversationStore:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        future=True,
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return SqlAlchemyConversationStore(create_session_factory(engine))


def _scope(database_id: int = 1) -> ConversationScope:
    return ConversationScope(
        database_id=database_id,
        catalog_name="prod",
        schema_name="pipeline",
        dataset_ids=[42],
    )


def test_sqlalchemy_conversation_store_round_trips_messages_and_artifacts() -> None:
    store = _store()
    conversation = store.create(_scope(), owner_id="user-1")
    artifact = ConversationArtifact(
        sql="select stage, gross_moves from moves",
        explanation="Grouped by stage.",
        validation=SqlValidation(is_valid=True, is_read_only=True),
        execution_result=ExecutionResult(
            columns=["stage", "gross_moves"],
            rows=[{"stage": "Closed", "gross_moves": 12}],
            row_count=1,
        ),
        answer_summary="Closed leads with 12 gross moves.",
        insight_cards=[
            InsightCard(
                title="Top stage",
                value=12,
                metric="gross_moves",
                category="Closed",
            )
        ],
        chart_spec=ChartSpec(
            type="bar",
            encoding=ChartEncoding(x="stage", y="gross_moves"),
        ),
        recommended_followups=["Show this by month"],
    )

    store.append(
        conversation.id,
        ConversationMessage(role="user", content="Show gross moves by stage"),
        owner_id="user-1",
    )
    saved = store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="Closed leads.",
            artifacts=[artifact],
        ),
        owner_id="user-1",
    )

    assert [message.role for message in saved.messages] == ["user", "assistant"]
    saved_artifact = saved.messages[-1].artifacts[0]
    assert saved_artifact.answer_summary == "Closed leads with 12 gross moves."
    assert saved_artifact.insight_cards[0].title == "Top stage"
    assert saved_artifact.chart_spec is not None
    assert saved_artifact.chart_spec.encoding.x == "stage"
    summary = store.list(owner_id="user-1")[0]
    assert summary.last_message == "Closed leads."
    assert summary.catalog_name == "prod"
    assert summary.schema_name == "pipeline"


def test_sqlalchemy_conversation_store_isolates_owners() -> None:
    store = _store()
    conversation = store.create(_scope(), owner_id="user-1")

    assert store.list(owner_id="user-2") == []
    with pytest.raises(ConversationNotFoundError):
        store.get(conversation.id, owner_id="user-2")


def test_sqlalchemy_conversation_store_replaces_artifact() -> None:
    store = _store()
    conversation = store.create(_scope(), owner_id="user-1")
    artifact = ConversationArtifact(sql="select 1")
    conversation = store.append(
        conversation.id,
        ConversationMessage(
            role="assistant",
            content="Draft SQL.",
            artifacts=[artifact],
        ),
        owner_id="user-1",
    )

    updated = artifact.model_copy(
        update={
            "answer_summary": "One row returned.",
            "recommended_followups": ["Break it down"],
        }
    )
    saved = store.replace_artifact(
        conversation.id,
        artifact.id,
        updated,
        owner_id="user-1",
    )

    saved_artifact = saved.messages[0].artifacts[0]
    assert saved_artifact.answer_summary == "One row returned."
    assert saved_artifact.recommended_followups == ["Break it down"]


def test_sqlalchemy_conversation_store_delete_is_soft_and_owner_scoped() -> None:
    store = _store()
    conversation = store.create(_scope(), owner_id="user-1")

    with pytest.raises(ConversationNotFoundError):
        store.delete(conversation.id, owner_id="user-2")

    store.delete(conversation.id, owner_id="user-1")
    assert store.list(owner_id="user-1") == []
    with pytest.raises(ConversationNotFoundError):
        store.get(conversation.id, owner_id="user-1")


def test_sqlalchemy_conversation_store_reports_missing_artifact() -> None:
    store = _store()
    conversation = store.create(_scope(), owner_id="user-1")

    with pytest.raises(ConversationArtifactNotFoundError):
        store.replace_artifact(
            conversation.id,
            "missing",
            ConversationArtifact(sql="select 1"),
            owner_id="user-1",
        )
