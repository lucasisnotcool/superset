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

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.superset_metadata import SupersetMetadataContextProvider
from superset_ai_agent.conversation_graph import ConversationGraph
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationCreateRequest,
    ConversationSqlExecutionRequest,
    ConversationSummary,
    ConversationTurnRequest,
    ConversationTurnResponse,
)
from superset_ai_agent.conversations.store import (
    ConversationNotFoundError,
    ConversationStore,
    DEFAULT_OWNER_ID,
)
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.superset.factory import create_superset_client
from superset_ai_agent.llm.factory import create_model_client
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    HealthResponse,
    ModelInfo,
    SqlValidation,
    ValidateSqlRequest,
)
from superset_ai_agent.tools.sql import validate_read_only_sql


def create_app(  # noqa: C901
    *,
    config: AgentConfig | None = None,
    model_client: Any | None = None,
    ollama_client: Any | None = None,
    text_to_sql_graph: Any | None = None,
    conversation_graph: Any | None = None,
    conversation_store: ConversationStore | None = None,
) -> FastAPI:
    """Create the standalone AI agent API.

    Dependency injection keeps the service lightweight and easy to test.
    """

    app_config = config or AgentConfig.from_env()
    active_model_client = (
        model_client or ollama_client or create_model_client(app_config)
    )

    active_conversation_store = conversation_store or _create_conversation_store(
        app_config
    )

    if text_to_sql_graph is None or conversation_graph is None:
        superset_client = create_superset_client(app_config)
        context_provider = SupersetMetadataContextProvider(superset_client)
        graph = text_to_sql_graph or TextToSqlGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=context_provider,
            superset_client=superset_client,
        )
        active_conversation_graph = conversation_graph or ConversationGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=context_provider,
            superset_client=superset_client,
            conversation_store=active_conversation_store,
        )
    else:
        graph = text_to_sql_graph
        active_conversation_graph = conversation_graph

    api = FastAPI(title=app_config.app_name, version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_config.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @api.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Check API and model provider reachability."""

        reachable = active_model_client.is_reachable()
        return HealthResponse(
            status="ok" if reachable else "degraded",
            model_provider=app_config.model_provider,
            base_url=app_config.model_base_url(),
            default_model=app_config.default_model(),
            reachable=reachable,
            ollama_base_url=app_config.ollama_base_url,
            ollama_reachable=(
                reachable if app_config.model_provider == "ollama" else None
            ),
        )

    @api.get("/models", response_model=list[ModelInfo])
    def models() -> list[ModelInfo]:
        """List available models for the active provider when supported."""

        try:
            return active_model_client.list_models()
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=str(ex)) from ex

    @api.post("/agent/query", response_model=AgentQueryResponse)
    def query_agent(request: AgentQueryRequest) -> AgentQueryResponse:
        """Generate validated SQL from natural language."""

        try:
            return graph.run(request)
        except Exception as ex:  # pylint: disable=broad-except
            return _agent_error_response(str(ex))

    @api.post("/agent/conversations", response_model=Conversation)
    def create_conversation(request: ConversationCreateRequest) -> Conversation:
        """Create a conversation scoped to the active Superset context."""

        return active_conversation_store.create(
            request.scope,
            owner_id=DEFAULT_OWNER_ID,
        )

    @api.get("/agent/conversations", response_model=list[ConversationSummary])
    def list_conversations() -> list[ConversationSummary]:
        """List conversation summaries for the current integration identity."""

        return active_conversation_store.list(owner_id=DEFAULT_OWNER_ID)

    @api.get("/agent/conversations/{conversation_id}", response_model=Conversation)
    def get_conversation(conversation_id: str) -> Conversation:
        """Return a conversation transcript."""

        try:
            return active_conversation_store.get(
                conversation_id,
                owner_id=DEFAULT_OWNER_ID,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex

    @api.post(
        "/agent/conversations/{conversation_id}/messages",
        response_model=ConversationTurnResponse,
    )
    def send_conversation_message(
        conversation_id: str,
        request: ConversationTurnRequest,
    ) -> ConversationTurnResponse:
        """Append a user message and run a conversational agent turn."""

        try:
            return active_conversation_graph.run(
                conversation_id=conversation_id,
                request=request,
                owner_id=DEFAULT_OWNER_ID,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=str(ex)) from ex

    @api.post(
        "/agent/conversations/{conversation_id}/execute-sql",
        response_model=ConversationTurnResponse,
    )
    def execute_conversation_sql(
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
    ) -> ConversationTurnResponse:
        """Execute an approved SQL artifact and continue the conversation."""

        try:
            return active_conversation_graph.execute_approved_sql(
                conversation_id=conversation_id,
                request=request,
                owner_id=DEFAULT_OWNER_ID,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=str(ex)) from ex

    @api.delete("/agent/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str) -> dict[str, bool]:
        """Delete a conversation transcript."""

        try:
            active_conversation_store.delete(
                conversation_id,
                owner_id=DEFAULT_OWNER_ID,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex
        return {"deleted": True}

    @api.post("/agent/validate-sql")
    def validate_sql(request: ValidateSqlRequest) -> SqlValidation:
        """Validate SQL without invoking the model."""

        return validate_read_only_sql(
            request.sql,
            dialect=request.dialect,
            default_limit=request.default_limit or app_config.default_sql_limit,
        )

    return api


def _create_conversation_store(config: AgentConfig) -> ConversationStore:
    if config.conversation_store == "memory":
        return InMemoryConversationStore()
    raise ValueError(
        "Unsupported AI_AGENT_CONVERSATION_STORE value "
        f"{config.conversation_store!r}. Expected: memory."
    )


def _agent_error_response(message: str) -> AgentQueryResponse:
    return AgentQueryResponse(
        status="error",
        sql=None,
        explanation="The agent could not complete the request.",
        validation=validate_read_only_sql(""),
        trace=[
            {
                "step": "agent_error",
                "status": "error",
                "summary": message,
                "details": {},
            }
        ],
    )


app = create_app()
