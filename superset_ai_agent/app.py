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

import json  # noqa: TID251 - keep the standalone agent independent of Superset
from pathlib import Path
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
    ConversationTitleUpdateRequest,
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
from superset_ai_agent.integrations.wren.client import WrenClient
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
from superset_ai_agent.semantic_layer.access import (
    SemanticAccessService,
    SemanticPermission,
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
    S3DocumentStorage,
)
from superset_ai_agent.semantic_layer.indexer import rebuild_index
from superset_ai_agent.semantic_layer.jobs import (
    InMemoryJobStore,
    JobNotFoundError,
    JobRunner,
    JobStore,
    ThreadJobRunner,
)
from superset_ai_agent.semantic_layer.mdl_files import (
    InMemoryMdlFileStore,
    MdlFileNotFoundError,
    MdlFileStore,
    MdlFileValidationError,
    SqlAlchemyMdlFileStore,
)
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_mdl,
    validate_project_manifest,
)
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.onboarding import onboard_schema_project
from superset_ai_agent.semantic_layer.projects import (
    InMemorySemanticProjectStore,
    SemanticProjectNotFoundError,
    SemanticProjectStore,
    SqlAlchemySemanticProjectStore,
)
from superset_ai_agent.semantic_layer.review import apply_review
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    MdlFile,
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
    MdlValidationResult,
    SemanticDocument,
    SemanticDocumentTextRequest,
    SemanticJob,
    SemanticLayerEvent,
    SemanticLayerEventType,
    SemanticLayerIndexRequest,
    SemanticLayerReviewRequest,
    SemanticLayerState,
    SemanticLayerVersion,
    SemanticProject,
    SemanticProjectResolveRequest,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.sqlalchemy_store import (
    SqlAlchemySemanticLayerStore,
)
from superset_ai_agent.semantic_layer.store import (
    SemanticDocumentNotFoundError,
    SemanticLayerStore,
)
from superset_ai_agent.semantic_layer.wren_materializer import materialize_wren_project
from superset_ai_agent.tools.sql import validate_read_only_sql


def _conversation_sse(event: dict[str, Any]) -> str:
    """Serialize a conversation stream event as one SSE frame.

    ``complete`` events carry a pydantic response model, which is dumped to a
    JSON-safe mapping before serialization.
    """

    response = event.get("response")
    payload = dict(event)
    if response is not None and hasattr(response, "model_dump"):
        payload["response"] = response.model_dump(mode="json")
    event_type = str(payload.get("type", "message"))
    return f"event: {event_type}\ndata: {json.dumps(payload, default=str)}\n\n"


def create_app(  # noqa: C901
    *,
    config: AgentConfig | None = None,
    model_client: Any | None = None,
    ollama_client: Any | None = None,
    text_to_sql_graph: Any | None = None,
    conversation_graph: Any | None = None,
    conversation_store: ConversationStore | None = None,
    semantic_layer_store: SemanticLayerStore | None = None,
    semantic_project_store: SemanticProjectStore | None = None,
    mdl_file_store: MdlFileStore | None = None,
    document_storage: DocumentStorage | None = None,
    document_extractor: DocumentExtractor | None = None,
    superset_client: Any | None = None,
    context_provider: Any | None = None,
    wren_client: Any | None = None,
    identity_provider: Any | None = None,
    job_store: JobStore | None = None,
    job_runner: JobRunner | None = None,
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
    active_semantic_project_store = semantic_project_store or (
        _create_semantic_project_store(
            app_config,
            session_factory=session_factory,
        )
    )
    active_mdl_file_store = mdl_file_store or _create_mdl_file_store(
        app_config,
        session_factory=session_factory,
    )
    active_document_storage = document_storage or _create_document_storage(app_config)
    active_document_extractor = document_extractor or CompositeDocumentExtractor()
    active_job_store = job_store or InMemoryJobStore()
    active_job_runner = job_runner or ThreadJobRunner()

    app_superset_client = (
        superset_client
        or (
            create_superset_client(app_config)
            if app_config.superset_auth_mode != "user_session"
            else None
        )
    )
    active_wren_client = wren_client or create_wren_client(
        app_config,
        model_client=active_model_client,
        mdl_file_store=active_mdl_file_store,
    )
    app_context_provider = context_provider or (
        SupersetMetadataContextProvider(app_superset_client, config=app_config)
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
            semantic_layer_store=active_semantic_layer_store,
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
        )
        service_conversation_graph = conversation_graph or ConversationGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=app_context_provider,
            superset_client=app_superset_client,
            conversation_store=active_conversation_store,
            wren_client=active_wren_client,
            semantic_layer_store=active_semantic_layer_store,
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
        )

    api = FastAPI(title=app_config.app_name, version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_config.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "PATCH", "POST", "OPTIONS"],
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
            request_superset_client,
            config=app_config,
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
            semantic_layer_store=active_semantic_layer_store,
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
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
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
        )

    def build_semantic_access_service(
        request: Request,
    ) -> SemanticAccessService:
        def load_context(scope: ConversationScope) -> Any:
            request_context_provider, _ = build_superset_runtime(request)
            return request_context_provider.get_context(
                AgentQueryRequest(
                    question="semantic layer scope authorization",
                    database_id=scope.database_id,
                    catalog_name=scope.catalog_name,
                    schema_name=scope.schema_name,
                    dataset_ids=scope.dataset_ids,
                )
            )

        def get_database_identity(
            database_id: int,
            catalog_name: str | None,
        ) -> Any:
            _, request_superset_client = build_superset_runtime(request)
            return request_superset_client.get_database_identity(
                database_id=database_id,
                catalog_name=catalog_name,
            )

        return SemanticAccessService(
            project_store=active_semantic_project_store,
            load_context=load_context,
            get_database_identity=get_database_identity,
            semantic_access_mode=app_config.semantic_access_mode,
            semantic_full_access_grants_write=(
                app_config.semantic_full_access_grants_write
            ),
        )

    def authorize_semantic_scope(
        request: Request,
        scope: ConversationScope,
        *,
        identity: AgentIdentity,
        permission: SemanticPermission = SemanticPermission.READ,
    ) -> None:
        try:
            build_semantic_access_service(request).require_scope_permission(
                identity=identity,
                scope=scope,
                permission=permission,
            )
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

    def authorize_semantic_project(
        request: Request,
        project_id: str,
        *,
        owner_id: str,
        permission: str = "read",
    ) -> SemanticProject:
        try:
            return build_semantic_access_service(
                request
            ).require_project_permission(
                identity=AgentIdentity(owner_id=owner_id),
                project_id=project_id,
                permission=SemanticPermission(permission),
            )
        except SemanticProjectNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Semantic project not found.",
            ) from ex
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex
        except PermissionError as ex:
            raise HTTPException(
                status_code=403,
                detail="Insufficient semantic project permission.",
            ) from ex

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
            return build_text_to_sql_graph(fastapi_request).run(
                payload,
                owner_id=identity.owner_id,
            )
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

    @api.patch(
        "/agent/conversations/{conversation_id}",
        response_model=Conversation,
    )
    def rename_conversation(
        conversation_id: str,
        request: ConversationTitleUpdateRequest,
        identity: AgentIdentity = identity_dependency,
    ) -> Conversation:
        """Rename a conversation."""

        try:
            return active_conversation_store.update_title(
                conversation_id,
                request.title,
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

    @api.post("/agent/conversations/{conversation_id}/messages/stream")
    def stream_conversation_message(
        conversation_id: str,
        request: ConversationTurnRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream a conversational agent turn as server-sent events."""

        # Validate existence up front so a missing conversation returns 404
        # rather than a 200 stream carrying an error event.
        try:
            active_conversation_store.get(
                conversation_id,
                owner_id=identity.owner_id,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex

        graph = build_conversation_graph(fastapi_request)

        def event_stream() -> Any:
            try:
                for event in graph.run_stream(
                    conversation_id=conversation_id,
                    request=request,
                    owner_id=identity.owner_id,
                ):
                    yield _conversation_sse(event)
            except Exception as ex:  # pylint: disable=broad-except
                # The HTTP status is already committed, so surface late failures
                # as a terminal error event instead of raising.
                yield _conversation_sse({"type": "error", "detail": str(ex)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
        )

    @api.post("/agent/conversations/{conversation_id}/execute-sql/stream")
    def stream_execute_conversation_sql(
        conversation_id: str,
        request: ConversationSqlExecutionRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream an approved-SQL execution turn as server-sent events."""

        try:
            active_conversation_store.get(
                conversation_id,
                owner_id=identity.owner_id,
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Conversation not found.",
            ) from ex

        graph = build_conversation_graph(fastapi_request)

        def event_stream() -> Any:
            try:
                for event in graph.execute_approved_sql_stream(
                    conversation_id=conversation_id,
                    request=request,
                    owner_id=identity.owner_id,
                ):
                    yield _conversation_sse(event)
            except Exception as ex:  # pylint: disable=broad-except
                yield _conversation_sse({"type": "error", "detail": str(ex)})

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
        )

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
            authorize_semantic_scope(
                fastapi_request,
                parsed_scope,
                identity=identity,
                permission=SemanticPermission.WRITE,
            )
            content = await file.read()
            document = create_document(
                filename=file.filename or "document",
                content_type=file.content_type or "application/octet-stream",
                content=content,
                scope=parsed_scope,
                owner_id=identity.owner_id,
                config=app_config,
                store=active_semantic_layer_store,
                storage=active_document_storage,
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

    @api.post(
        "/agent/semantic-layer/projects/resolve",
        response_model=SemanticProject,
    )
    def resolve_semantic_project(
        request: SemanticProjectResolveRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticProject:
        """Resolve or create a schema-scoped semantic project."""

        if not request.schema_name:
            raise HTTPException(status_code=400, detail="schema_name is required.")
        try:
            return build_semantic_access_service(fastapi_request).resolve_project(
                identity=identity,
                request=request,
                permission=SemanticPermission.READ,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

    @api.get(
        "/agent/semantic-layer/projects",
        response_model=list[SemanticProject],
    )
    def list_semantic_projects(
        fastapi_request: Request,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> list[SemanticProject]:
        """List semantic projects visible in a governed database scope."""

        scope = ConversationScope(
            database_id=database_id,
            catalog_name=catalog_name,
            schema_name=schema_name,
        )
        try:
            return build_semantic_access_service(fastapi_request).list_projects(
                identity=identity,
                scope=scope,
            )
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

    @api.get(
        "/agent/semantic-layer/projects/{project_id}",
        response_model=SemanticProject,
    )
    def get_semantic_project(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticProject:
        """Return a governed semantic project."""

        return authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )

    @api.delete("/agent/semantic-layer/projects/{project_id}")
    def delete_semantic_project(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, bool]:
        """Archive a semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="admin",
        )
        active_semantic_project_store.delete(project_id, owner_id=identity.owner_id)
        return {"deleted": True}

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/mdl-files",
        response_model=list[MdlFile],
    )
    def list_mdl_files(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[MdlFile]:
        """List MDL YAML files in a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        return active_mdl_file_store.list(project_id, owner_id=identity.owner_id)

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/mdl-files",
        response_model=MdlFile,
    )
    def create_mdl_file(
        project_id: str,
        request: MdlFileCreateRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlFile:
        """Create an MDL YAML file in a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        try:
            return active_mdl_file_store.create(
                project_id,
                request,
                owner_id=identity.owner_id,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}",
        response_model=MdlFile,
    )
    def get_mdl_file(
        project_id: str,
        file_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlFile:
        """Return one MDL YAML file from a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        try:
            file = active_mdl_file_store.get(file_id, owner_id=identity.owner_id)
        except MdlFileNotFoundError as ex:
            raise HTTPException(status_code=404, detail="MDL file not found.") from ex
        if file.project_id != project_id:
            raise HTTPException(status_code=404, detail="MDL file not found.")
        return file

    def _schema_index_for_project(
        project: SemanticProject,
        fastapi_request: Request,
    ) -> SchemaIndex | None:
        """Best-effort physical schema index for activation/generation checks.

        A Superset outage degrades to structural-only validation rather than
        blocking activation entirely.
        """

        if project.default_database_id is None:
            return None
        try:
            request_context_provider, _ = build_superset_runtime(fastapi_request)
            context = request_context_provider.get_context(
                AgentQueryRequest(
                    question="semantic layer validation",
                    database_id=project.default_database_id,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                )
            )
        except Exception:  # pylint: disable=broad-except
            return None
        return SchemaIndex.from_agent_context(context)

    def _enforce_activation(
        *,
        project: SemanticProject,
        fastapi_request: Request,
        owner_id: str,
        file_id: str,
        new_content: str,
    ) -> None:
        """Block activation when the resulting project manifest is invalid."""

        siblings = [
            file.content
            for file in active_mdl_file_store.list(project.id, owner_id=owner_id)
            if file.id != file_id and file.status == "active"
        ]
        validation = validate_project_manifest(
            [*siblings, new_content],
            schema_index=_schema_index_for_project(project, fastapi_request),
        )
        if not validation.valid:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "MDL failed validation and cannot be activated.",
                    "validation": validation.model_dump(mode="json"),
                },
            )

    @api.patch(
        "/agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}",
        response_model=MdlFile,
    )
    def update_mdl_file(
        project_id: str,
        file_id: str,
        request: MdlFileUpdateRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlFile:
        """Update one MDL YAML file in a governed semantic project."""

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        try:
            existing = active_mdl_file_store.get(
                file_id,
                owner_id=identity.owner_id,
            )
            if existing.project_id != project_id:
                raise MdlFileNotFoundError(file_id)
            if request.status == "active":
                _enforce_activation(
                    project=project,
                    fastapi_request=fastapi_request,
                    owner_id=identity.owner_id,
                    file_id=file_id,
                    new_content=(
                        request.content
                        if request.content is not None
                        else existing.content
                    ),
                )
            return active_mdl_file_store.update(
                file_id,
                request,
                owner_id=identity.owner_id,
            )
        except MdlFileNotFoundError as ex:
            raise HTTPException(status_code=404, detail="MDL file not found.") from ex
        except MdlFileValidationError as ex:
            raise HTTPException(status_code=422, detail=str(ex)) from ex
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @api.delete(
        "/agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}",
    )
    def delete_mdl_file(
        project_id: str,
        file_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, bool]:
        """Delete one MDL YAML file from a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        try:
            existing = active_mdl_file_store.get(
                file_id,
                owner_id=identity.owner_id,
            )
            if existing.project_id != project_id:
                raise MdlFileNotFoundError(file_id)
            active_mdl_file_store.delete(file_id, owner_id=identity.owner_id)
        except MdlFileNotFoundError as ex:
            raise HTTPException(status_code=404, detail="MDL file not found.") from ex
        return {"deleted": True}

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}/validate",
        response_model=MdlValidationResult,
    )
    def validate_mdl_file(
        project_id: str,
        file_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlValidationResult:
        """Validate one MDL YAML file from a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        try:
            existing = active_mdl_file_store.get(
                file_id,
                owner_id=identity.owner_id,
            )
            if existing.project_id != project_id:
                raise MdlFileNotFoundError(file_id)
            return active_mdl_file_store.validate(
                file_id,
                owner_id=identity.owner_id,
            )
        except MdlFileNotFoundError as ex:
            raise HTTPException(status_code=404, detail="MDL file not found.") from ex

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/materialize",
        response_model=WrenMaterializationResult,
    )
    def materialize_semantic_project(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> WrenMaterializationResult:
        """Materialize active MDL YAML files for read-only Wren context use."""

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        mdl_files = active_mdl_file_store.list(
            project_id,
            owner_id=identity.owner_id,
        )
        return materialize_wren_project(
            project=project,
            mdl_files=mdl_files,
            base_path=_wren_materialization_base(app_config),
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/onboard",
        response_model=SemanticJob,
        status_code=202,
    )
    def onboard_semantic_project(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticJob:
        """Start async schema onboarding; poll the returned job for the result.

        The schema context is fetched synchronously (request-scoped auth); only
        the slower LLM generation and MDL writes run in the background.
        """

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        if project.default_database_id is None:
            raise HTTPException(
                status_code=400,
                detail="Project has no associated database for onboarding.",
            )
        request_context_provider, _ = build_superset_runtime(fastapi_request)
        try:
            context = request_context_provider.get_context(
                AgentQueryRequest(
                    question="semantic layer onboarding",
                    database_id=project.default_database_id,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                )
            )
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

        job = active_job_store.create(kind="onboarding", project_id=project.id)
        owner_id = identity.owner_id
        scope = _scope_from_project(project)
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=owner_id,
            event_type="onboarding_started",
            scope=scope,
            document_id=None,
            message="Onboarding started.",
            project_id=project.id,
        )

        def _run_onboarding() -> None:
            try:
                result = onboard_schema_project(
                    project=project,
                    superset_context=context,
                    wren_client=active_wren_client,
                    mdl_file_store=active_mdl_file_store,
                    owner_id=owner_id,
                )
            except Exception as ex:  # pylint: disable=broad-except
                active_job_store.fail(job.id, str(ex))
                _append_semantic_event(
                    store=active_semantic_layer_store,
                    owner_id=owner_id,
                    event_type="onboarding_failed",
                    scope=scope,
                    document_id=None,
                    message=f"Onboarding failed: {ex}",
                    project_id=project.id,
                )
                return
            active_job_store.complete(job.id, result)
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=owner_id,
                event_type="onboarding_completed",
                scope=scope,
                document_id=None,
                message=f"Onboarded {result.model_count} draft model(s).",
                project_id=project.id,
            )

        active_job_runner.submit(_run_onboarding)
        # Re-fetch so an inline runner reflects completion immediately while a
        # threaded runner returns the still-running job for the client to poll.
        return active_job_store.get(job.id)

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/jobs/{job_id}",
        response_model=SemanticJob,
    )
    def get_semantic_job(
        project_id: str,
        job_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticJob:
        """Return the status/result of an async semantic-layer job."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        try:
            job = active_job_store.get(job_id)
        except JobNotFoundError as ex:
            raise HTTPException(status_code=404, detail="Job not found.") from ex
        if job.project_id != project_id:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/mdl-files/upload",
        response_model=MdlFile,
    )
    async def upload_mdl_file(
        project_id: str,
        fastapi_request: Request,
        path: str | None = Form(None),
        file: UploadFile = upload_file,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlFile:
        """Upload a reviewed MDL YAML file to a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        try:
            content = (await file.read()).decode("utf-8")
            target_path = path or file.filename or "model.yaml"
            return active_mdl_file_store.create(
                project_id,
                MdlFileCreateRequest(
                    path=target_path,
                    content=content,
                    source_type="uploaded_mdl",
                ),
                owner_id=identity.owner_id,
            )
        except UnicodeDecodeError as ex:
            raise HTTPException(
                status_code=400,
                detail="MDL YAML upload must be UTF-8 text.",
            ) from ex
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/documents",
        response_model=SemanticDocument,
    )
    async def upload_project_source_document(
        project_id: str,
        fastapi_request: Request,
        file: UploadFile = upload_file,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Upload a source document for Wren-style semantic enrichment."""

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        scope = _scope_from_project(project)
        try:
            content = await file.read()
            document = create_document(
                filename=file.filename or "document",
                content_type=file.content_type or "application/octet-stream",
                content=content,
                scope=scope,
                project_id=project_id,
                owner_id=identity.owner_id,
                config=app_config,
                store=active_semantic_layer_store,
                storage=active_document_storage,
                extractor=active_document_extractor,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=identity.owner_id,
            event_type="document_uploaded",
            scope=scope,
            project_id=project_id,
            document_id=document.id,
            message=f"Uploaded {document.filename}.",
        )
        return active_semantic_layer_store.get_document(
            document.id,
            owner_id=identity.owner_id,
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/documents/text",
        response_model=SemanticDocument,
    )
    def create_project_source_document_from_text(
        project_id: str,
        payload: SemanticDocumentTextRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Create a source document from pasted BI markdown text."""

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        scope = _scope_from_project(project)
        try:
            document = create_document(
                filename=payload.filename,
                content_type=payload.content_type,
                content=payload.text.encode("utf-8"),
                scope=scope,
                project_id=project_id,
                owner_id=identity.owner_id,
                config=app_config,
                store=active_semantic_layer_store,
                storage=active_document_storage,
                extractor=active_document_extractor,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=identity.owner_id,
            event_type="document_uploaded",
            scope=scope,
            project_id=project_id,
            document_id=document.id,
            message=f"Added {document.filename} from text.",
        )
        return active_semantic_layer_store.get_document(
            document.id,
            owner_id=identity.owner_id,
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/documents/{document_id}/enrich",
        response_model=MdlEnrichmentProposal,
    )
    def enrich_project_document(
        project_id: str,
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlEnrichmentProposal:
        """Create a reviewable MDL proposal from a source document."""

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
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
        if document.project_id != project_id:
            raise HTTPException(status_code=404, detail="Semantic document not found.")
        proposal = _enrichment_proposal(
            project=project,
            document=document,
            wren_client=active_wren_client,
        )
        # Re-validate the proposal against the live schema (R3) so hallucinated
        # columns/tables are visible before the user tries to activate.
        schema_index = _schema_index_for_project(project, fastapi_request)
        if schema_index is not None:
            validation = validate_mdl(
                proposal.proposed_yaml,
                schema_index=schema_index,
            )
            extra_warnings = (
                []
                if validation.valid
                else [
                    "Proposal references tables/columns absent from the schema "
                    "and cannot be activated until corrected."
                ]
            )
            proposal = proposal.model_copy(
                update={
                    "validation": validation,
                    "warnings": [*proposal.warnings, *extra_warnings],
                }
            )
        return proposal

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/state",
        response_model=SemanticLayerState,
    )
    def get_project_semantic_layer_state(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticLayerState:
        """Return document/indexing state for a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        return active_semantic_layer_store.get_project_state(
            project_id,
            owner_id=identity.owner_id,
        )

    @api.get(
        "/agent/semantic-layer/documents",
        response_model=list[SemanticDocument],
    )
    def list_semantic_documents(
        fastapi_request: Request,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> list[SemanticDocument]:
        """List semantic-layer documents for a governed Superset scope."""

        scope = _scope_from_query(database_id, catalog_name, schema_name, dataset_ids)
        authorize_semantic_scope(
            fastapi_request,
            scope,
            identity=identity,
            permission=SemanticPermission.READ,
        )
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
        authorize_semantic_scope(
            fastapi_request,
            document.scope,
            identity=identity,
            permission=SemanticPermission.READ,
        )
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
            authorize_semantic_scope(
                fastapi_request,
                document.scope,
                identity=identity,
                permission=SemanticPermission.WRITE,
            )
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

        authorize_semantic_scope(
            fastapi_request,
            request.scope,
            identity=identity,
            permission=SemanticPermission.WRITE,
        )
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
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticLayerState:
        """Return semantic-layer state for a governed Superset scope."""

        scope = _scope_from_query(database_id, catalog_name, schema_name, dataset_ids)
        authorize_semantic_scope(
            fastapi_request,
            scope,
            identity=identity,
            permission=SemanticPermission.READ,
        )
        return active_semantic_layer_store.get_state(
            scope,
            owner_id=identity.owner_id,
        )

    @api.get("/agent/semantic-layer/events")
    def get_semantic_layer_events(
        fastapi_request: Request,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream stored semantic-layer events as server-sent events."""

        scope = _scope_from_query(database_id, catalog_name, schema_name, dataset_ids)
        authorize_semantic_scope(
            fastapi_request,
            scope,
            identity=identity,
            permission=SemanticPermission.READ,
        )
        events = active_semantic_layer_store.list_events(
            scope,
            owner_id=identity.owner_id,
        )
        return StreamingResponse(
            (to_sse(event) for event in events),
            media_type="text/event-stream",
        )

    @api.get("/agent/semantic-layer/projects/{project_id}/events")
    def get_project_semantic_layer_events(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream stored semantic-layer events for a governed project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        events = active_semantic_layer_store.list_project_events(
            project_id,
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


def _create_semantic_project_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> SemanticProjectStore:
    if config.semantic_layer_store == "memory":
        return InMemorySemanticProjectStore()
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy semantic project store requires a database.")
        return SqlAlchemySemanticProjectStore(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_SEMANTIC_LAYER_STORE value "
        f"{config.semantic_layer_store!r}. Expected one of: memory, sqlalchemy."
    )


def _create_mdl_file_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> MdlFileStore:
    if config.semantic_layer_store == "memory":
        return InMemoryMdlFileStore()
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy MDL file store requires a database.")
        return SqlAlchemyMdlFileStore(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_SEMANTIC_LAYER_STORE value "
        f"{config.semantic_layer_store!r}. Expected one of: memory, sqlalchemy."
    )


def _create_document_storage(config: AgentConfig) -> DocumentStorage:
    if config.document_storage == "local":
        return LocalDocumentStorage(config.agent_storage_dir)
    if config.document_storage == "s3":
        return S3DocumentStorage(
            bucket=config.document_s3_bucket or "",
            prefix=config.document_s3_prefix,
            endpoint_url=config.document_s3_endpoint_url,
            region_name=config.document_s3_region_name,
        )
    raise ValueError(
        "Unsupported AI_AGENT_DOCUMENT_STORAGE value "
        f"{config.document_storage!r}. Expected one of: local, s3."
    )


def _scope_from_query(
    database_id: int,
    catalog_name: str | None,
    schema_name: str | None,
    dataset_ids: str | None,
) -> ConversationScope:
    return ConversationScope(
        database_id=database_id,
        catalog_name=catalog_name,
        schema_name=schema_name,
        dataset_ids=_parse_dataset_ids(dataset_ids),
    )


def _scope_from_project(project: SemanticProject) -> ConversationScope:
    if project.default_database_id is None:
        raise HTTPException(
            status_code=400,
            detail="Semantic project is not linked to a Superset database.",
        )
    return ConversationScope(
        database_id=project.default_database_id,
        catalog_name=project.catalog_name,
        schema_name=project.schema_name,
    )


def _enrichment_proposal(
    *,
    project: SemanticProject,
    document: SemanticDocument,
    wren_client: WrenClient,
) -> MdlEnrichmentProposal:
    return wren_client.propose_mdl_from_document(
        project=project,
        document=document,
    )


def _wren_materialization_base(config: AgentConfig) -> Path:
    if config.wren_project_path:
        return Path(config.wren_project_path)
    return Path(config.agent_storage_dir) / "wren"


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
    project_id: str | None = None,
) -> None:
    state = store.get_state(scope, owner_id=owner_id)
    store.append_event(
        SemanticLayerEvent(
            project_id=project_id,
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
