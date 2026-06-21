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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from superset_ai_agent.auth import (
    AgentIdentity,
    create_identity_provider,
    SupersetRequestAuth,
)
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.superset_metadata import SupersetMetadataContextProvider
from superset_ai_agent.conversation_graph import ConversationGraph
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationCreateRequest,
    ConversationScope,
    ConversationSqlExecutionRequest,
    ConversationSummary,
    ConversationTurnRequest,
    ConversationTurnResponse,
)
from superset_ai_agent.conversations.sqlalchemy_store import SqlAlchemyConversationStore
from superset_ai_agent.conversations.store import (
    ConversationNotFoundError,
    ConversationStore,
)
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.superset.client import SupersetAuthError
from superset_ai_agent.integrations.superset.factory import create_superset_client
from superset_ai_agent.integrations.wren.factory import create_wren_client
from superset_ai_agent.llm.factory import create_model_client
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    HealthResponse,
    ModelInfo,
    SqlValidation,
    TraceEvent,
    ValidateSqlRequest,
)
from superset_ai_agent.semantic_layer.documents import create_document
from superset_ai_agent.semantic_layer.events import to_sse
from superset_ai_agent.semantic_layer.extractors import (
    CompositeDocumentExtractor,
    DocumentExtractor,
)
from superset_ai_agent.semantic_layer.file_storage import (
    DocumentStorage,
    LocalDocumentStorage,
)
from superset_ai_agent.semantic_layer.indexer import rebuild_index
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.review import apply_review
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerEvent,
    SemanticLayerEventType,
    SemanticLayerIndexRequest,
    SemanticLayerReviewRequest,
    SemanticLayerState,
    SemanticLayerVersion,
)
from superset_ai_agent.semantic_layer.sqlalchemy_store import (
    SqlAlchemySemanticLayerStore,
)
from superset_ai_agent.semantic_layer.store import (
    SemanticDocumentNotFoundError,
    SemanticLayerStore,
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
    semantic_layer_store: SemanticLayerStore | None = None,
    document_storage: DocumentStorage | None = None,
    document_extractor: DocumentExtractor | None = None,
    superset_client: Any | None = None,
    context_provider: Any | None = None,
    wren_client: Any | None = None,
    identity_provider: Any | None = None,
) -> FastAPI:
    """Create the standalone AI agent API.

    Dependency injection keeps the service lightweight and easy to test.
    """

    app_config = config or AgentConfig.from_env()
    active_model_client = (
        model_client or ollama_client or create_model_client(app_config)
    )
    active_identity_provider = identity_provider or create_identity_provider(app_config)
    _validate_identity_persistence_config(app_config)

    session_factory = None
    if _requires_agent_database(app_config):
        engine = create_engine_from_config(app_config)
        if app_config.agent_run_migrations:
            run_migrations(app_config)
        session_factory = create_session_factory(engine)
    active_conversation_store = conversation_store or _create_conversation_store(
        app_config,
        session_factory=session_factory,
    )
    active_semantic_layer_store = semantic_layer_store or _create_semantic_layer_store(
        app_config, session_factory=session_factory
    )
    active_document_storage = document_storage
    active_document_extractor = document_extractor or CompositeDocumentExtractor()

    app_superset_client = (
        superset_client
        or (
            create_superset_client(app_config)
            if app_config.superset_auth_mode != "user_session"
            else None
        )
    )
    active_wren_client = wren_client or create_wren_client(app_config)
    app_context_provider = context_provider or (
        SupersetMetadataContextProvider(app_superset_client)
        if app_superset_client is not None
        else None
    )

    service_text_to_sql_graph = None
    service_conversation_graph = None
    if app_context_provider is not None and app_superset_client is not None:
        service_text_to_sql_graph = text_to_sql_graph or TextToSqlGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=app_context_provider,
            superset_client=app_superset_client,
            wren_client=active_wren_client,
        )
        service_conversation_graph = conversation_graph or ConversationGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=app_context_provider,
            superset_client=app_superset_client,
            conversation_store=active_conversation_store,
            wren_client=active_wren_client,
            semantic_layer_store=active_semantic_layer_store,
        )

    api = FastAPI(title=app_config.app_name, version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_config.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def get_identity(request: Request) -> AgentIdentity:
        return active_identity_provider.get_identity(request)

    identity_dependency = Depends(get_identity)
    scope_form = Form(...)
    upload_file = File(...)

    def build_superset_runtime(
        request: Request,
    ) -> tuple[Any, Any]:
        if app_context_provider is not None and app_superset_client is not None:
            return app_context_provider, app_superset_client
        request_auth = (
            SupersetRequestAuth.from_request(request)
            if app_config.superset_auth_mode == "user_session"
            else None
        )
        request_superset_client = superset_client or create_superset_client(
            app_config,
            request_auth=request_auth,
        )
        request_context_provider = context_provider or SupersetMetadataContextProvider(
            request_superset_client
        )
        return request_context_provider, request_superset_client

    def build_text_to_sql_graph(request: Request) -> Any:
        if text_to_sql_graph is not None:
            return text_to_sql_graph
        if service_text_to_sql_graph is not None:
            return service_text_to_sql_graph
        request_context_provider, request_superset_client = build_superset_runtime(
            request
        )
        return TextToSqlGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=request_context_provider,
            superset_client=request_superset_client,
            wren_client=active_wren_client,
        )

    def build_conversation_graph(request: Request) -> Any:
        if conversation_graph is not None:
            return conversation_graph
        if service_conversation_graph is not None:
            return service_conversation_graph
        request_context_provider, request_superset_client = build_superset_runtime(
            request
        )
        return ConversationGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=request_context_provider,
            superset_client=request_superset_client,
            conversation_store=active_conversation_store,
            wren_client=active_wren_client,
            semantic_layer_store=active_semantic_layer_store,
        )

    def authorize_semantic_scope(
        request: Request,
        scope: ConversationScope,
    ) -> None:
        try:
            request_context_provider, _ = build_superset_runtime(request)
            request_context_provider.get_context(
                AgentQueryRequest(
                    question="semantic layer scope authorization",
                    database_id=scope.database_id,
                    schema_name=scope.schema_name,
                    dataset_ids=scope.dataset_ids,
                )
            )
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

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
    def query_agent(
        payload: AgentQueryRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> AgentQueryResponse:
        """Generate validated SQL from natural language."""

        try:
            return build_text_to_sql_graph(fastapi_request).run(payload)
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex
        except Exception as ex:  # pylint: disable=broad-except
            return _agent_error_response(str(ex))

    @api.post("/agent/conversations", response_model=Conversation)
    def create_conversation(
        request: ConversationCreateRequest,
        identity: AgentIdentity = identity_dependency,
    ) -> Conversation:
        """Create a conversation scoped to the active Superset context."""

        return active_conversation_store.create(
            request.scope,
            owner_id=identity.owner_id,
        )

    @api.get("/agent/conversations", response_model=list[ConversationSummary])
    def list_conversations(
        identity: AgentIdentity = identity_dependency,
    ) -> list[ConversationSummary]:
        """List conversation summaries for the current integration identity."""

        return active_conversation_store.list(owner_id=identity.owner_id)

    @api.get("/agent/conversations/{conversation_id}", response_model=Conversation)
    def get_conversation(
        conversation_id: str,
        identity: AgentIdentity = identity_dependency,
    ) -> Conversation:
        """Return a conversation transcript."""

        try:
            return active_conversation_store.get(
                conversation_id,
                owner_id=identity.owner_id,
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
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> ConversationTurnResponse:
        """Append a user message and run a conversational agent turn."""

        try:
            return build_conversation_graph(fastapi_request).run(
                conversation_id=conversation_id,
                request=request,
                owner_id=identity.owner_id,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=str(ex)) from ex

    @api.post(
        "/agent/conversations/{conversation_id}/execute-sql",
        response_model=ConversationTurnResponse,
    )
    def execute_conversation_sql(
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> ConversationTurnResponse:
        """Execute an approved SQL artifact and continue the conversation."""

        try:
            return build_conversation_graph(fastapi_request).execute_approved_sql(
                conversation_id=conversation_id,
                request=request,
                owner_id=identity.owner_id,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=str(ex)) from ex

    @api.delete("/agent/conversations/{conversation_id}")
    def delete_conversation(
        conversation_id: str,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, bool]:
        """Delete a conversation transcript."""

        try:
            active_conversation_store.delete(
                conversation_id,
                owner_id=identity.owner_id,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex
        return {"deleted": True}

    @api.post(
        "/agent/semantic-layer/documents",
        response_model=SemanticDocument,
    )
    async def upload_semantic_document(
        fastapi_request: Request,
        scope: str = scope_form,
        file: UploadFile = upload_file,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Upload a document that can propose reviewed semantic context."""

        try:
            parsed_scope = ConversationScope.model_validate_json(scope)
        except ValidationError as ex:
            raise HTTPException(status_code=422, detail=str(ex)) from ex
        try:
            authorize_semantic_scope(fastapi_request, parsed_scope)
            content = await file.read()
            document = create_document(
                filename=file.filename or "document",
                content_type=file.content_type or "application/octet-stream",
                content=content,
                scope=parsed_scope,
                owner_id=identity.owner_id,
                config=app_config,
                store=active_semantic_layer_store,
                storage=active_document_storage
                or LocalDocumentStorage(app_config.agent_storage_dir),
                extractor=active_document_extractor,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=identity.owner_id,
            event_type="document_uploaded",
            scope=parsed_scope,
            document_id=document.id,
            message=f"Uploaded {document.filename}.",
        )
        if document.status == "needs_review":
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=identity.owner_id,
                event_type="review_required",
                scope=parsed_scope,
                document_id=document.id,
                message=f"{document.filename} needs semantic review.",
            )
        elif document.status == "error":
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=identity.owner_id,
                event_type="index_failed",
                scope=parsed_scope,
                document_id=document.id,
                message=document.error or "Document extraction failed.",
            )
        else:
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=identity.owner_id,
                event_type="document_extracted",
                scope=parsed_scope,
                document_id=document.id,
                message=f"Extracted {document.filename}.",
            )
        return active_semantic_layer_store.get_document(
            document.id,
            owner_id=identity.owner_id,
        )

    @api.get(
        "/agent/semantic-layer/documents",
        response_model=list[SemanticDocument],
    )
    def list_semantic_documents(
        fastapi_request: Request,
        database_id: int,
        schema_name: str | None = None,
        dataset_ids: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> list[SemanticDocument]:
        """List semantic-layer documents for a governed Superset scope."""

        scope = _scope_from_query(database_id, schema_name, dataset_ids)
        authorize_semantic_scope(fastapi_request, scope)
        return active_semantic_layer_store.list_documents(
            scope,
            owner_id=identity.owner_id,
        )

    @api.get(
        "/agent/semantic-layer/documents/{document_id}",
        response_model=SemanticDocument,
    )
    def get_semantic_document(
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Return one semantic-layer document."""

        try:
            document = active_semantic_layer_store.get_document(
                document_id,
                owner_id=identity.owner_id,
            )
        except SemanticDocumentNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Semantic document not found.",
            ) from ex
        authorize_semantic_scope(fastapi_request, document.scope)
        return document

    @api.patch(
        "/agent/semantic-layer/documents/{document_id}/review",
        response_model=SemanticDocument,
    )
    def review_semantic_document(
        document_id: str,
        fastapi_request: Request,
        request: SemanticLayerReviewRequest,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Review proposed semantic-layer updates from a document."""

        try:
            document = active_semantic_layer_store.get_document(
                document_id,
                owner_id=identity.owner_id,
            )
            authorize_semantic_scope(fastapi_request, document.scope)
            reviewed_document = apply_review(
                active_semantic_layer_store,
                document_id=document_id,
                request=request,
                owner_id=identity.owner_id,
                reviewer_id=identity.username or identity.owner_id,
            )
        except SemanticDocumentNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Semantic document not found.",
            ) from ex
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=identity.owner_id,
            event_type="review_saved",
            scope=reviewed_document.scope,
            document_id=reviewed_document.id,
            message=f"Saved review for {reviewed_document.filename}.",
        )
        return reviewed_document

    @api.post(
        "/agent/semantic-layer/index/rebuild",
        response_model=SemanticLayerVersion,
    )
    def rebuild_semantic_layer_index(
        fastapi_request: Request,
        request: SemanticLayerIndexRequest,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticLayerVersion:
        """Rebuild the reviewed semantic overlay for a governed scope."""

        authorize_semantic_scope(fastapi_request, request.scope)
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=identity.owner_id,
            event_type="index_started",
            scope=request.scope,
            document_id=None,
            message="Semantic-layer index rebuild started.",
        )
        try:
            version = rebuild_index(
                active_semantic_layer_store,
                scope=request.scope,
                owner_id=identity.owner_id,
            )
        except Exception as ex:  # pylint: disable=broad-except
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=identity.owner_id,
                event_type="index_failed",
                scope=request.scope,
                document_id=None,
                message=str(ex),
            )
            raise HTTPException(status_code=502, detail=str(ex)) from ex
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=identity.owner_id,
            event_type="index_completed",
            scope=request.scope,
            document_id=None,
            message=f"Semantic-layer index {version.version} rebuilt.",
        )
        return version

    @api.get(
        "/agent/semantic-layer/state",
        response_model=SemanticLayerState,
    )
    def get_semantic_layer_state(
        fastapi_request: Request,
        database_id: int,
        schema_name: str | None = None,
        dataset_ids: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticLayerState:
        """Return semantic-layer state for a governed Superset scope."""

        scope = _scope_from_query(database_id, schema_name, dataset_ids)
        authorize_semantic_scope(fastapi_request, scope)
        return active_semantic_layer_store.get_state(
            scope,
            owner_id=identity.owner_id,
        )

    @api.get("/agent/semantic-layer/events")
    def get_semantic_layer_events(
        fastapi_request: Request,
        database_id: int,
        schema_name: str | None = None,
        dataset_ids: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream stored semantic-layer events as server-sent events."""

        scope = _scope_from_query(database_id, schema_name, dataset_ids)
        authorize_semantic_scope(fastapi_request, scope)
        events = active_semantic_layer_store.list_events(
            scope,
            owner_id=identity.owner_id,
        )
        return StreamingResponse(
            (to_sse(event) for event in events),
            media_type="text/event-stream",
        )

    @api.post("/agent/validate-sql")
    def validate_sql(request: ValidateSqlRequest) -> SqlValidation:
        """Validate SQL without invoking the model."""

        return validate_read_only_sql(
            request.sql,
            dialect=request.dialect,
            default_limit=request.default_limit or app_config.default_sql_limit,
        )

    return api


def _create_conversation_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> ConversationStore:
    if config.conversation_store == "memory":
        return InMemoryConversationStore()
    if config.conversation_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy conversation store requires a database.")
        return SqlAlchemyConversationStore(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_CONVERSATION_STORE value "
        f"{config.conversation_store!r}. Expected one of: memory, sqlalchemy."
    )


def _create_semantic_layer_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> SemanticLayerStore:
    if config.semantic_layer_store == "memory":
        return InMemorySemanticLayerStore()
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy semantic layer store requires a database.")
        return SqlAlchemySemanticLayerStore(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_SEMANTIC_LAYER_STORE value "
        f"{config.semantic_layer_store!r}. Expected one of: memory, sqlalchemy."
    )


def _scope_from_query(
    database_id: int,
    schema_name: str | None,
    dataset_ids: str | None,
) -> ConversationScope:
    return ConversationScope(
        database_id=database_id,
        schema_name=schema_name,
        dataset_ids=_parse_dataset_ids(dataset_ids),
    )


def _parse_dataset_ids(dataset_ids: str | None) -> list[int]:
    if not dataset_ids:
        return []
    return [int(item.strip()) for item in dataset_ids.split(",") if item.strip()]


def _append_semantic_event(
    *,
    store: SemanticLayerStore,
    owner_id: str,
    event_type: SemanticLayerEventType,
    scope: ConversationScope,
    document_id: str | None,
    message: str,
) -> None:
    state = store.get_state(scope, owner_id=owner_id)
    store.append_event(
        SemanticLayerEvent(
            type=event_type,
            scope=scope,
            document_id=document_id,
            state=state,
            message=message,
        ),
        owner_id=owner_id,
    )


def _requires_agent_database(config: AgentConfig) -> bool:
    return (
        config.conversation_store == "sqlalchemy"
        or config.semantic_layer_store == "sqlalchemy"
    )


def _validate_identity_persistence_config(config: AgentConfig) -> None:
    if not _requires_agent_database(config):
        return
    if config.identity_provider != "static":
        return
    if config.allow_static_identity_with_persistence:
        return
    raise ValueError(
        "Persistent AI agent stores require non-static identity. Set "
        "AI_AGENT_IDENTITY_PROVIDER=superset_session or signed_header, or "
        "explicitly enable AI_AGENT_ALLOW_STATIC_IDENTITY_WITH_PERSISTENCE "
        "for local development."
    )


def _agent_error_response(message: str) -> AgentQueryResponse:
    return AgentQueryResponse(
        status="error",
        sql=None,
        explanation="The agent could not complete the request.",
        validation=validate_read_only_sql(""),
        trace=[
            TraceEvent(
                step="agent_error",
                status="error",
                summary=message,
            )
        ],
    )


app = create_app()
