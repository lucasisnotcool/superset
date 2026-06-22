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

"""Wiring the built-but-inert seams into the graphs (wren_full.md RV2/RV4/RO1)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from dataclasses import dataclass
from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversation_graph import ConversationGraph
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    ConversationScope,
    ConversationTurnRequest,
)
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.llm.base import ChatMessage, ModelResult
from superset_ai_agent.semantic_layer.memory_store import InMemoryMemory
from superset_ai_agent.semantic_layer.schema_retriever import (
    KeywordRetriever,
    retrieve_mdl_context,
)
from superset_ai_agent.semantic_layer.store import scope_hash
from tests.unit_tests.superset_ai_agent.test_conversation_graph import (
    FakeContextProvider,
    FakeSupersetClient,
)
from tests.unit_tests.superset_ai_agent.test_graph_semantic_engine import (
    _FakeGatingEngine,
)

_MODEL_YAML = """
models:
  - name: orders
    table_reference:
      schema: sales
      table: orders
    columns:
      - name: id
        type: BIGINT
      - name: revenue
        type: DOUBLE
  - name: customers
    table_reference:
      schema: sales
      table: customers
    columns:
      - name: id
        type: BIGINT
      - name: region
        type: VARCHAR
"""


@dataclass
class _FakeMdlFile:
    content: str
    status: str = "active"
    deleted_at: Any = None


class _FakeMdlFileStore:
    def __init__(self, files: list[_FakeMdlFile]) -> None:
        self._files = files

    def list(self, project_id: str, *, owner_id: str) -> list[_FakeMdlFile]:
        return self._files


# --- RV2: Retriever seam feeds the prompt context -----------------------------


def test_retrieve_mdl_context_ranks_active_mdl_into_context_items() -> None:
    items = retrieve_mdl_context(
        config=AgentConfig(),
        retriever=KeywordRetriever(),
        question="customer region",
        project_id="proj-1",
        owner_id="owner-1",
        mdl_file_store=_FakeMdlFileStore([_FakeMdlFile(_MODEL_YAML)]),
    )
    assert items, "expected retrieved schema context items"
    assert all(item["source"] == "retriever" for item in items)
    assert all(item["retriever"] == "keyword" for item in items)
    # The question's tokens (customer, region) should surface those chunks.
    texts = " ".join(item["text"] for item in items)
    assert "region" in texts
    assert "customers" in texts


def test_retrieve_mdl_context_stamps_effective_mode_on_fallback() -> None:
    # G8a: an EmbeddingRetriever with no usable embedder silently uses keyword;
    # the stamped retriever must reflect that, not the configured "embedding".
    from superset_ai_agent.llm.embeddings import NullEmbedder
    from superset_ai_agent.semantic_layer.schema_retriever import EmbeddingRetriever

    items = retrieve_mdl_context(
        config=AgentConfig(),
        retriever=EmbeddingRetriever(NullEmbedder()),
        question="customer region",
        project_id="proj-1",
        owner_id="owner-1",
        mdl_file_store=_FakeMdlFileStore([_FakeMdlFile(_MODEL_YAML)]),
    )
    assert items
    assert all(item["retriever"] == "keyword" for item in items)


def test_retrieve_mdl_context_degrades_closed() -> None:
    cfg = AgentConfig()
    retriever = KeywordRetriever()
    # No project, no store, and no active files each yield an empty list.
    assert (
        retrieve_mdl_context(
            config=cfg,
            retriever=retriever,
            question="q",
            project_id=None,
            owner_id="o",
            mdl_file_store=_FakeMdlFileStore([_FakeMdlFile(_MODEL_YAML)]),
        )
        == []
    )
    assert (
        retrieve_mdl_context(
            config=cfg,
            retriever=retriever,
            question="q",
            project_id="p",
            owner_id="o",
            mdl_file_store=None,
        )
        == []
    )
    assert (
        retrieve_mdl_context(
            config=cfg,
            retriever=retriever,
            question="q",
            project_id="p",
            owner_id="o",
            mdl_file_store=_FakeMdlFileStore(
                [_FakeMdlFile(_MODEL_YAML, status="draft")]
            ),
        )
        == []
    )


def test_conversation_graph_builds_a_default_retriever() -> None:
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=_IntentAwareModelClient([]),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=InMemoryConversationStore(),
    )
    assert graph.retriever.name == "keyword"


# --- Shared conversation harness ----------------------------------------------


class _IntentAwareModelClient:
    """Returns queued JSON responses in order, recording prompt payloads."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.index = 0
        self.payloads: list[str] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
    ) -> ModelResult:
        self.payloads.append(messages[-1].content)
        if self.index < len(self.responses):
            response = self.responses[self.index]
        else:
            response = self.responses[-1] if self.responses else {}
        self.index += 1
        return ModelResult(content=json.dumps(response))

    def is_reachable(self) -> bool:
        return True


_SQL_DRAFT = {
    "response_type": "sql",
    "message": "Top names.",
    "sql": "SELECT name, SUM(num) AS total_births FROM birth_names GROUP BY name",
    "explanation": "Sum births by name.",
}


def _run_sql_turn(graph: ConversationGraph, store, scope, message: str):
    conversation = store.create(scope)
    return conversation, graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message=message,
            scope=scope,
            execution_mode="read_only",
        ),
    )


# --- RV4: conversation-graph memory write-back + recall -----------------------


def test_conversation_memory_writeback_and_recall_round_trip() -> None:
    memory = InMemoryMemory()
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])

    write_graph = ConversationGraph(
        config=AgentConfig(),
        model_client=_IntentAwareModelClient([_SQL_DRAFT]),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
        memory=memory,
    )
    _run_sql_turn(write_graph, store, scope, "show the most popular names")

    # The confirmed pair was stored for this owner+scope.
    recalled = memory.recall_examples(
        "popular names",
        scope_hash=scope_hash(scope),
        owner_id=DEFAULT_OWNER_ID,
        k=3,
    )
    assert any(pair.question == "show the most popular names" for pair in recalled)

    # A second turn recalls the stored pair into the draft prompt payload.
    capturing = _IntentAwareModelClient([_SQL_DRAFT])
    recall_graph = ConversationGraph(
        config=AgentConfig(),
        model_client=capturing,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
        memory=memory,
    )
    _run_sql_turn(recall_graph, store, scope, "what are popular names")
    payloads = capturing.payloads
    assert any("recalled_examples" in payload for payload in payloads)
    assert any("show the most popular names" in payload for payload in payloads)


def test_conversation_memory_is_noop_without_a_memory_store() -> None:
    # Default ctor → NullMemory: write-back and recall are inert, turn still runs.
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=_IntentAwareModelClient([_SQL_DRAFT]),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )
    _, response = _run_sql_turn(graph, store, scope, "show names")
    assert response.status in {"ok", "needs_review"}


# --- RO1: gated intent classification pre-node --------------------------------


def test_intent_classification_runs_when_enabled() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    model = _IntentAwareModelClient(
        [{"intent": "general", "reason": "greeting"}, _SQL_DRAFT]
    )
    graph = ConversationGraph(
        config=AgentConfig(wren_intent_classification_enabled=True),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )
    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(message="hi there", scope=scope),
    )
    intent_events = [e for e in response.trace if e.step == "classify_intent"]
    assert len(intent_events) == 1
    assert intent_events[0].details["intent"] == "general"
    # The intent label reaches the draft prompt payload.
    assert any('"intent"' in payload for payload in model.payloads)


def test_intent_classification_off_by_default() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    graph = ConversationGraph(
        config=AgentConfig(),
        model_client=_IntentAwareModelClient([_SQL_DRAFT]),
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )
    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(message="hi there", scope=scope),
    )
    assert not any(e.step == "classify_intent" for e in response.trace)


# --- RO1a: gated intent routing short-circuit ---------------------------------


def test_intent_routing_short_circuits_general_intent() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    # First call → intent; second call → the direct answer.
    model = _IntentAwareModelClient(
        [
            {"intent": "general", "reason": "capability question"},
            {"response_type": "answer", "message": "I can query your data.", "sql": ""},
        ]
    )
    superset = FakeSupersetClient()
    graph = ConversationGraph(
        config=AgentConfig(
            wren_intent_classification_enabled=True,
            wren_intent_routing_enabled=True,
        ),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=superset,
        conversation_store=store,
    )
    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(message="what can you do?", scope=scope),
    )
    steps = {e.step for e in response.trace}
    # Direct-answer path taken; context-load + SQL nodes skipped; no execution.
    assert "answer_directly" in steps
    assert "load_context" not in steps
    assert "draft_response" not in steps
    assert superset.executed_sql == []
    assert response.message.content == "I can query your data."


def test_intent_routing_off_runs_full_flow_for_general_intent() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    model = _IntentAwareModelClient(
        [
            {"intent": "general", "reason": "x"},
            {"response_type": "answer", "message": "hello", "sql": ""},
        ]
    )
    graph = ConversationGraph(
        config=AgentConfig(wren_intent_classification_enabled=True),  # routing off
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
    )
    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(message="what can you do?", scope=scope),
    )
    steps = {e.step for e in response.trace}
    assert "answer_directly" not in steps
    assert "load_context" in steps


# --- B: engine-feedback correction loop in the conversation graph -------------


def test_conversation_engine_correction_redrafts_hallucinated_table() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    # First SQL draft references an unknown table (gate fires); second is clean;
    # third response feeds the post-execution reflection (falls back to answer).
    model = _IntentAwareModelClient(
        [
            {
                "response_type": "sql",
                "message": "draft",
                "sql": "SELECT * FROM ghost_table",
                "explanation": "x",
            },
            {
                "response_type": "sql",
                "message": "fixed",
                "sql": "SELECT num FROM birth_names",
                "explanation": "x",
            },
            {"outcome": "answer", "message": "done"},
        ]
    )
    superset = FakeSupersetClient()
    graph = ConversationGraph(
        config=AgentConfig(wren_engine_max_correction_retries=1),
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=superset,
        conversation_store=store,
        semantic_engine=_FakeGatingEngine(),
    )
    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="popular names", scope=scope, execution_mode="read_only"
        ),
    )
    assert any(e.step == "correct_semantic_sql" for e in response.trace)
    assert superset.executed_sql
    assert "public.birth_names" in superset.executed_sql[-1]
    assert all("ghost_table" not in sql for sql in superset.executed_sql)


def test_conversation_engine_correction_off_by_default() -> None:
    store = InMemoryConversationStore()
    scope = ConversationScope(database_id=1, dataset_ids=[16])
    conversation = store.create(scope)
    model = _IntentAwareModelClient(
        [
            {
                "response_type": "sql",
                "message": "draft",
                "sql": "SELECT * FROM ghost_table",
                "explanation": "x",
            },
            {"outcome": "answer", "message": "done"},
        ]
    )
    graph = ConversationGraph(
        config=AgentConfig(),  # wren_engine_max_correction_retries=0
        model_client=model,
        context_provider=FakeContextProvider(),
        superset_client=FakeSupersetClient(),
        conversation_store=store,
        semantic_engine=_FakeGatingEngine(),
    )
    response = graph.run(
        conversation_id=conversation.id,
        request=ConversationTurnRequest(
            message="popular names", scope=scope, execution_mode="read_only"
        ),
    )
    assert not any(e.step == "correct_semantic_sql" for e in response.trace)
