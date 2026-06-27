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
import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
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
from superset_ai_agent.conversations.turns import ConversationTurnService
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.superset.client import SupersetAuthError
from superset_ai_agent.integrations.superset.factory import create_superset_client
from superset_ai_agent.integrations.wren.client import WrenClient
from superset_ai_agent.integrations.wren.factory import create_wren_client
from superset_ai_agent.llm.base import ChatMessage
from superset_ai_agent.llm.embeddings import create_embedder
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
from superset_ai_agent.semantic_layer.copilot.coverage import (
    InMemoryCoverageCache,
    run_coverage_audit,
)
from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetApplyRequest,
    CopilotInspector,
    CopilotTurnRequest,
    CoverageReport,
    CoverageRequest,
    InstructionView,
    MessageAttachment,
    WorkspaceNode,
)
from superset_ai_agent.semantic_layer.copilot.service import (
    apply_changeset_items,
    build_deploy_preview,
    build_inspector,
    changeset_to_artifact,
    run_copilot,
)
from superset_ai_agent.semantic_layer.copilot.workspace import build_workspace_tree
from superset_ai_agent.semantic_layer.document_chunks import (
    DocumentChunk,
    DocumentChunkMatch,
)
from superset_ai_agent.semantic_layer.document_retriever import (
    create_document_index,
    document_scope_key,
    find_exact_duplicate_matches,
)
from superset_ai_agent.semantic_layer.documents import (
    create_document,
    delete_document_cascade,
    extract_document,
    register_document,
    reindex_document,
)
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
from superset_ai_agent.semantic_layer.instructions import (
    create_instruction_store,
    Instruction,
)
from superset_ai_agent.semantic_layer.jobs import (
    InMemoryJobStore,
    JobNotFoundError,
    JobRunner,
    JobStore,
    SqlAlchemyJobStore,
    ThreadJobRunner,
)
from superset_ai_agent.semantic_layer.mdl_files import (
    InMemoryMdlFileStore,
    MdlFileExistsError,
    MdlFileNotFoundError,
    MdlFileStore,
    MdlFileValidationError,
    SqlAlchemyMdlFileStore,
)
from superset_ai_agent.semantic_layer.mdl_schema import MdlManifest
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_mdl,
    validate_project_manifest,
)
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore
from superset_ai_agent.semantic_layer.memory_store import create_memory
from superset_ai_agent.semantic_layer.onboarding import onboard_schema_project
from superset_ai_agent.semantic_layer.projects import (
    InMemorySemanticProjectStore,
    SemanticProjectNotFoundError,
    SemanticProjectStore,
    SqlAlchemySemanticProjectStore,
)
from superset_ai_agent.semantic_layer.schema_retriever import (
    create_retriever,
    effective_vector_index,
    reindex_project_mdl,
)
from superset_ai_agent.semantic_layer.schema_snapshot import (
    InMemorySchemaSnapshotStore,
    SchemaSnapshot,
    SchemaSnapshotStore,
    SqlAlchemySchemaSnapshotStore,
)
from superset_ai_agent.semantic_layer.schemas import (
    InstructionCreateRequest,
    MdlEnrichmentProposal,
    MdlFile,
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
    MdlValidationResult,
    OnboardingRequest,
    PROVENANCE_EVENT_TYPES,
    provenance_from_event,
    PROVENANCE_HISTORY_CAP,
    ProvenanceEntry,
    SemanticDocument,
    SemanticDocumentTextRequest,
    SemanticJob,
    SemanticLayerEvent,
    SemanticLayerEventType,
    SemanticLayerState,
    SemanticProject,
    SemanticProjectReadiness,
    SemanticProjectResolveRequest,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.sqlalchemy_store import (
    SqlAlchemySemanticLayerStore,
)
from superset_ai_agent.semantic_layer.store import (
    instruction_scope_hash,
    SemanticDocumentNotFoundError,
    SemanticLayerStore,
)
from superset_ai_agent.semantic_layer.wren_core_validator import wren_core_available
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
    schema_snapshot_store: SchemaSnapshotStore | None = None,
    retriever: Any | None = None,
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
    _validate_semantic_persistence_config(app_config)
    logger.info(
        "AI agent persistence: semantic_layer_store=%s conversation_store=%s "
        "db=%s parity_features=%s",
        app_config.semantic_layer_store,
        app_config.conversation_store,
        app_config.agent_database_url if _requires_agent_database(app_config) else "-",
        _parity_features_enabled(app_config),
    )

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
    active_job_store = job_store or _create_job_store(
        app_config,
        session_factory=session_factory,
    )
    active_job_runner = job_runner or ThreadJobRunner()
    active_schema_snapshot_store = schema_snapshot_store or (
        _create_schema_snapshot_store(
            app_config,
            session_factory=session_factory,
        )
    )
    # Build the embedder + retriever once per app so the in-process vector index
    # (and any LanceDB connection) is shared across requests/graphs in a worker
    # — instead of a cold index per request (wren_full.md C4).
    active_embedder = create_embedder(app_config)
    # Share the embedder with memory so example recall is semantic (R3/R6), not
    # keyword — degrading closed to token overlap when no embedder is configured.
    active_memory = create_memory(
        app_config,
        session_factory=session_factory,
        embedder=active_embedder,
    )
    # User-authored instructions (Wren `instructions`) — semantic recall shares the
    # app embedder, durable when the agent DB is configured.
    active_instruction_store = create_instruction_store(
        app_config,
        session_factory=session_factory,
        embedder=active_embedder,
    )
    active_retriever = retriever or create_retriever(app_config, active_embedder)
    # Document-chunk RAG index — shares the app embedder + vector-index mode, built
    # once so the LanceDB connection is reused. Degrades closed to keyword recall.
    active_document_index = create_document_index(app_config, active_embedder)
    # Per-worker coverage-report cache (determinism on repeat audits).
    active_coverage_cache = InMemoryCoverageCache()
    active_vector_index = effective_vector_index(app_config, active_retriever)
    if active_vector_index == "memory_fallback":
        logger.warning(
            "WREN_VECTOR_INDEX=lancedb was requested but LanceDB did not "
            "connect; embedding retrieval is running in-process and will NOT "
            "survive a restart. Install `lancedb` or set WREN_VECTOR_INDEX=memory."
        )

    app_superset_client = superset_client or (
        create_superset_client(app_config)
        if app_config.superset_auth_mode != "user_session"
        else None
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
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
            memory=active_memory,
            retriever=active_retriever,
            instruction_store=active_instruction_store,
        )
        service_conversation_graph = conversation_graph or ConversationGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=app_context_provider,
            superset_client=app_superset_client,
            conversation_store=active_conversation_store,
            wren_client=active_wren_client,
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
            memory=active_memory,
            retriever=active_retriever,
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
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
            memory=active_memory,
            retriever=active_retriever,
            instruction_store=active_instruction_store,
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
            semantic_project_store=active_semantic_project_store,
            mdl_file_store=active_mdl_file_store,
            memory=active_memory,
            retriever=active_retriever,
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
            return build_semantic_access_service(request).require_project_permission(
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
            semantic_layer_persistent=app_config.semantic_layer_store != "memory",
            vector_index=active_vector_index,
            max_document_bytes=app_config.wren_max_document_bytes,
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

        # ``kind="sql"`` keeps the AI SQL history clean of Copilot threads, which
        # live under the project-scoped ``/copilot/conversations`` surface.
        return active_conversation_store.list(owner_id=identity.owner_id, kind="sql")

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
                document_index=active_document_index,
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
        if document.status == "error":
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
        except SemanticProjectNotFoundError as ex:
            raise HTTPException(
                status_code=404,
                detail="Semantic project not found.",
            ) from ex
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex
        except PermissionError as ex:
            raise HTTPException(
                status_code=403,
                detail="Insufficient semantic project permission.",
            ) from ex

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
        """List MDL JSON files in a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        return active_mdl_file_store.list(project_id, owner_id=identity.owner_id)

    def _emit_mdl_provenance(
        *,
        project: SemanticProject,
        owner_id: str,
        event_type: SemanticLayerEventType,
        file: MdlFile,
        message: str,
        status_from: str | None = None,
    ) -> None:
        """Append an MDL-CRUD provenance event (best-effort; never blocks the write).

        Provenance is an audit aid, not part of the write contract — a failure to
        record must not fail the file operation (mirrors the Copilot step-sink).
        """

        try:
            detail: dict[str, Any] = {
                "actor": owner_id,
                "path": file.path,
                "file_id": file.id,
                "source_type": file.source_type,
            }
            if file.source_document_id:
                detail["document_id"] = file.source_document_id
            if status_from is not None:
                detail["status_from"] = status_from
                detail["status_to"] = file.status
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=owner_id,
                event_type=event_type,
                scope=_scope_from_project(project),
                document_id=file.source_document_id,
                message=message,
                project_id=project.id,
                detail=detail,
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to record MDL provenance event.", exc_info=True)

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
        """Create an MDL JSON file in a governed semantic project."""

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        try:
            created = active_mdl_file_store.create(
                project_id,
                request,
                owner_id=identity.owner_id,
                validation=validate_mdl(
                    request.content,
                    schema_index=_schema_index_for_project(project, fastapi_request),
                ),
            )
            _emit_mdl_provenance(
                project=project,
                owner_id=identity.owner_id,
                event_type="mdl_created",
                file=created,
                message=f"Created {created.path}",
            )
            return created
        except MdlFileExistsError as ex:
            # A path conflict is distinct from a malformed request — 409 lets the
            # client recover (rename/auto-suffix) rather than treating it as a 400.
            raise HTTPException(status_code=409, detail=str(ex)) from ex
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
        """Return one MDL JSON file from a governed semantic project."""

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
        """Physical schema index for activation/generation checks.

        On a successful live fetch the schema is snapshotted for the project; on
        a Superset outage the last snapshot is used so physical validation keeps
        catching hallucinated columns instead of degrading to structural-only.
        Returns ``None`` only when neither a live fetch nor a snapshot exists.
        """

        if project.default_database_id is None:
            return None
        try:
            request_context_provider, _ = build_superset_runtime(fastapi_request)
            # CR3: ground modeling/validation on the *complete* scope schema, not a
            # relevance-ranked top-k against a placeholder question (which can silently
            # drop the tables a document is about). Fall back to the ranked path only
            # for providers that do not implement full-schema introspection.
            fetch_full = getattr(request_context_provider, "get_full_schema", None)
            fetch = fetch_full or request_context_provider.get_context
            context = fetch(
                AgentQueryRequest(
                    question="semantic layer validation",
                    database_id=project.default_database_id,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                )
            )
        except Exception:  # pylint: disable=broad-except
            snapshot = active_schema_snapshot_store.get(project.id)
            if snapshot is None:
                return None
            return SchemaIndex.from_snapshot(snapshot.tables)
        index = SchemaIndex.from_agent_context(context)
        try:
            active_schema_snapshot_store.upsert(
                SchemaSnapshot(
                    project_id=project.id,
                    database_uri_fingerprint=project.database_uri_fingerprint,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                    tables=index.to_tables(),
                )
            )
        except Exception:  # noqa: S110  # pylint: disable=broad-except
            # Snapshotting is best-effort; never block validation on it.
            pass
        return index

    def _project_has_models(project_id: str, *, owner_id: str) -> bool:
        """Whether the project has at least one non-deleted MDL model (CR2).

        Enrichment requires onboarded structure to overlay onto (drafts count, since
        onboarding writes drafts); a project with no models cannot be enriched.
        """

        try:
            files = active_mdl_file_store.list(project_id, owner_id=owner_id)
        except Exception:  # pylint: disable=broad-except
            return False
        for file in files:
            if file.status == "deleted":
                continue
            try:
                payload = json.loads(file.content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("models"):
                return True
        return False

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
        schema_index = _schema_index_for_project(project, fastapi_request)
        if schema_index is None and app_config.semantic_activation_requires_live_schema:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Schema metadata is unavailable; activation requires live "
                    "schema validation."
                ),
            )
        # F0.1: when configured, the wren-core engine must be present and authoritative
        # for activation — degrade *closed* instead of silently structural-only.
        require_engine = app_config.wren_activation_requires_engine
        if require_engine and not wren_core_available():
            raise HTTPException(
                status_code=409,
                detail=(
                    "The wren-core engine is required for activation but is not "
                    "installed; install wren-core-py or unset "
                    "WREN_ACTIVATION_REQUIRES_ENGINE."
                ),
            )
        validation = validate_project_manifest(
            [*siblings, new_content],
            schema_index=schema_index,
            deep_validate=app_config.wren_core_validation_enabled or require_engine,
            # W4: an enrichment that re-emits an existing model supersedes the
            # older copy instead of failing as a duplicate_model. new_content is
            # last, so the file being activated wins.
            dedup_models=True,
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
        """Update one MDL JSON file in a governed semantic project."""

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
            file_validation = (
                validate_mdl(
                    request.content,
                    schema_index=_schema_index_for_project(project, fastapi_request),
                )
                if request.content is not None
                else None
            )
            updated = active_mdl_file_store.update(
                file_id,
                request,
                owner_id=identity.owner_id,
                validation=file_validation,
            )
            # E6: eager deploy→reindex. When a file is activated, refresh the
            # retriever index now (off the next query's critical path; primes the
            # persistent index) so retrieval reflects the new MDL immediately.
            if request.status == "active":
                reindex_project_mdl(
                    retriever=active_retriever,
                    project_id=project_id,
                    owner_id=identity.owner_id,
                    mdl_file_store=active_mdl_file_store,
                )
            if request.status == "active" and existing.status != "active":
                _emit_mdl_provenance(
                    project=project,
                    owner_id=identity.owner_id,
                    event_type="mdl_activated",
                    file=updated,
                    message=f"Activated {updated.path}",
                    status_from=existing.status,
                )
            else:
                _emit_mdl_provenance(
                    project=project,
                    owner_id=identity.owner_id,
                    event_type="mdl_updated",
                    file=updated,
                    message=f"Edited {updated.path}",
                )
            return updated
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
        """Delete one MDL JSON file from a governed semantic project."""

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
            active_mdl_file_store.delete(file_id, owner_id=identity.owner_id)
            _emit_mdl_provenance(
                project=project,
                owner_id=identity.owner_id,
                event_type="mdl_deleted",
                file=existing,
                message=f"Deleted {existing.path}",
            )
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
        """Validate one MDL JSON file from a governed semantic project."""

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

    # -- MDL Copilot (wren_mdl_copilot.md) --------------------------------

    def _require_copilot_enabled() -> None:
        if not app_config.wren_copilot_enabled:
            raise HTTPException(
                status_code=404,
                detail="MDL Copilot is disabled (set WREN_COPILOT_ENABLED=true).",
            )

    def _project_readiness(
        project: SemanticProject, owner_id: str
    ) -> SemanticProjectReadiness:
        """Whether the MDL base layer is onboarded and stable (Copilot gate).

        The Copilot must not edit while onboarding is still writing files. Derived
        from existing signals — active MDL files + in-flight onboarding jobs — so no
        new project state/migration is needed (see ``wren_mdl_copilot.md`` §AB).
        """

        files = active_mdl_file_store.list(project.id, owner_id=owner_id)
        active = [file for file in files if file.status == "active"]
        jobs = active_job_store.list_for_project(project.id)
        running = next(
            (
                job
                for job in reversed(jobs)
                if job.kind == "onboarding" and job.status == "running"
            ),
            None,
        )
        if running is not None:
            return SemanticProjectReadiness(
                status="indexing",
                ready=False,
                has_active_models=bool(active),
                active_model_count=len(active),
                running_job_id=running.id,
                detail="Onboarding in progress; the semantic layer is initializing.",
            )
        if active:
            return SemanticProjectReadiness(
                status="ready",
                ready=True,
                has_active_models=True,
                active_model_count=len(active),
                detail="Semantic layer is ready.",
            )
        last_onboarding = next(
            (job for job in reversed(jobs) if job.kind == "onboarding"), None
        )
        if last_onboarding is not None and last_onboarding.status == "failed":
            return SemanticProjectReadiness(
                status="failed",
                ready=False,
                has_active_models=False,
                detail=last_onboarding.error or "Onboarding failed; retry onboarding.",
            )
        return SemanticProjectReadiness(
            status="empty",
            ready=False,
            has_active_models=False,
            detail="Schema has not been onboarded yet.",
        )

    def _require_project_ready(project: SemanticProject, owner_id: str) -> None:
        readiness = _project_readiness(project, owner_id)
        if not readiness.ready:
            # 409 Conflict: the request is valid but the project is not in a state
            # that can accept Copilot edits yet. The structured detail lets the UI
            # show a spinner (indexing) vs an onboarding prompt (empty/failed).
            raise HTTPException(
                status_code=409,
                detail={
                    "status": readiness.status,
                    "message": readiness.detail,
                    "running_job_id": readiness.running_job_id,
                },
            )

    def _project_instruction_views(
        project: SemanticProject, owner_id: str
    ) -> list[InstructionView]:
        if project.default_database_id is None:
            return []
        scope = _scope_from_project(project)
        return [
            InstructionView(
                id=item.id, instruction=item.instruction, is_global=item.is_global
            )
            for item in active_instruction_store.list_instructions(
                scope_hash=instruction_scope_hash(scope), owner_id=owner_id
            )
        ]

    def _recalled_instructions(
        project: SemanticProject, owner_id: str, query: str
    ) -> list[str]:
        if project.default_database_id is None:
            return []
        scope = _scope_from_project(project)
        return [
            item.instruction
            for item in active_instruction_store.recall(
                query,
                scope_hash=instruction_scope_hash(scope),
                owner_id=owner_id,
                k=app_config.wren_instruction_recall_k,
            )
        ]

    def _attachments_text(attachments: list[MessageAttachment]) -> str:
        limit = app_config.wren_copilot_attachment_max_chars
        blocks: list[str] = []
        for attachment in attachments:
            text = attachment.text or ""
            if len(text) > limit:
                text = text[:limit] + "\n…(truncated)…"
            blocks.append(f"### {attachment.filename}\n{text}")
        return "\n\n".join(blocks)

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/workspace",
        response_model=WorkspaceNode,
    )
    def get_project_workspace(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> WorkspaceNode:
        """Unified Wren-style workspace tree for the MDL Copilot editor."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        files = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
        instructions = _project_instruction_views(project, identity.owner_id)
        documents = active_semantic_layer_store.list_project_documents(
            project_id, owner_id=identity.owner_id
        )
        has_active = any(
            file.status == "active" for file in files if file.status != "deleted"
        )
        return build_workspace_tree(
            files,
            instruction_count=len(instructions),
            documents=documents,
            has_compiled=has_active,
        )

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/readiness",
        response_model=SemanticProjectReadiness,
    )
    def get_project_readiness(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticProjectReadiness:
        """Whether the MDL base layer is onboarded and stable enough for the Copilot.

        Read-only and not behind the Copilot flag — the editor polls this to show a
        loading spinner (``indexing``) or an onboarding prompt (``empty``/``failed``)
        before mounting the Copilot.
        """

        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        return _project_readiness(project, identity.owner_id)

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/copilot/inspector",
        response_model=CopilotInspector,
    )
    def get_copilot_inspector(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> CopilotInspector:
        """Effective agent context: prompt, skills, tools, project instructions."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        return build_inspector(
            instructions=_project_instruction_views(project, identity.owner_id),
            autopilot=app_config.wren_copilot_autopilot_enabled,
        )

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/copilot/deploy-preview",
        response_model=Changeset,
    )
    def get_copilot_deploy_preview(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> Changeset:
        """Aggregate diff of all drafts vs active (Wren-style Deploy review)."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        files = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
        return build_deploy_preview(
            files,
            schema_index=_schema_index_for_project(project, fastapi_request),
            deep_validate=app_config.wren_core_validation_enabled,
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/copilot/coverage",
        response_model=CoverageReport,
    )
    def run_project_coverage(
        project_id: str,
        request: CoverageRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> CoverageReport:
        """Audit a document for information lost in markdown → MDL conversion."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        # Assemble the document text from its persisted chunks (the extracted text).
        chunks = active_semantic_layer_store.list_project_chunks(
            project_id, owner_id=identity.owner_id
        )
        doc_chunks = sorted(
            (c for c in chunks if c.document_id == request.document_id),
            key=lambda c: c.chunk_index,
        )
        document_text = "\n\n".join(chunk.text for chunk in doc_chunks)
        filename = ""
        # Fall back to the document's extracted text when chunks are absent (e.g.
        # document indexing disabled), so coverage does not require the RAG index.
        try:
            document = active_semantic_layer_store.get_document(
                request.document_id, owner_id=identity.owner_id
            )
            filename = document.filename
            if not document_text:
                document_text = document.extracted_text or ""
        except SemanticDocumentNotFoundError as ex:
            raise HTTPException(status_code=404, detail="Document not found.") from ex
        files = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
        instructions = [
            view.instruction
            for view in _project_instruction_views(project, identity.owner_id)
        ]
        try:
            return run_coverage_audit(
                active_model_client,
                document_text=document_text,
                files=files,
                instructions=instructions,
                document_id=request.document_id,
                document_filename=filename,
                model=request.model,
                embedder=active_embedder,
                votes=app_config.wren_copilot_coverage_votes,
                cache=active_coverage_cache,
                include_overreach=request.include_overreach,
            )
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(status_code=502, detail=str(ex)) from ex

    # -- Copilot conversations (persistent, multi-turn threads) -----------
    # Parallel to the AI SQL ``/agent/conversations`` surface but project-scoped
    # and tagged ``kind="copilot"`` in the shared store. See
    # plan_copilot_parity_impl.md §6.

    def _copilot_turn_service() -> ConversationTurnService:
        return ConversationTurnService(active_conversation_store)

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/copilot/conversations",
        response_model=Conversation,
    )
    def create_copilot_conversation(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> Conversation:
        """Start a Copilot thread bound to the project (scope from the project)."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        return active_conversation_store.create(
            _scope_from_project(project),
            owner_id=identity.owner_id,
            kind="copilot",
            project_id=project_id,
        )

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/copilot/conversations",
        response_model=list[ConversationSummary],
    )
    def list_copilot_conversations(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[ConversationSummary]:
        """List the project's Copilot threads for the current identity."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        return active_conversation_store.list(
            owner_id=identity.owner_id, kind="copilot", project_id=project_id
        )

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/copilot/conversations/"
        "{conversation_id}",
        response_model=Conversation,
    )
    def get_copilot_conversation(
        project_id: str,
        conversation_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> Conversation:
        """Return one Copilot thread transcript (incl. persisted changesets)."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        try:
            conversation = active_conversation_store.get(
                conversation_id, owner_id=identity.owner_id
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404, detail="Conversation not found."
            ) from ex
        if conversation.kind != "copilot" or conversation.project_id != project_id:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return conversation

    @api.patch(
        "/agent/semantic-layer/projects/{project_id}/copilot/conversations/"
        "{conversation_id}",
        response_model=Conversation,
    )
    def rename_copilot_conversation(
        project_id: str,
        conversation_id: str,
        request: ConversationTitleUpdateRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> Conversation:
        """Rename a Copilot thread."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        _require_copilot_conversation(project_id, conversation_id, identity.owner_id)
        return active_conversation_store.update_title(
            conversation_id, request.title, owner_id=identity.owner_id
        )

    @api.delete(
        "/agent/semantic-layer/projects/{project_id}/copilot/conversations/"
        "{conversation_id}",
    )
    def delete_copilot_conversation(
        project_id: str,
        conversation_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, bool]:
        """Delete a Copilot thread."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        _require_copilot_conversation(project_id, conversation_id, identity.owner_id)
        active_conversation_store.delete(conversation_id, owner_id=identity.owner_id)
        return {"deleted": True}

    def _require_copilot_conversation(
        project_id: str, conversation_id: str, owner_id: str
    ) -> Conversation:
        """Load a thread and assert it is this project's Copilot thread (else 404)."""

        try:
            conversation = active_conversation_store.get(
                conversation_id, owner_id=owner_id
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404, detail="Conversation not found."
            ) from ex
        if conversation.kind != "copilot" or conversation.project_id != project_id:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return conversation

    def _copilot_thread_turn(
        conversation_id: str | None,
        project: SemanticProject,
        message: str,
        owner_id: str,
    ) -> tuple[list[ChatMessage] | None, Callable[..., None]]:
        """Open a persistent turn: append the user message, return (history, commit).

        Returns ``(None, no-op)`` when ``conversation_id`` is absent so the turn
        routes stay backward-compatible as stateless one-shots. The returned
        ``commit(content, changeset=None)`` always appends a paired assistant turn
        — the changeset summary + artifact on success, or a plain error/cancel note
        — so the stored transcript never ends on a dangling user message (mirrors
        the AI SQL agent's stream contract).
        """

        if not conversation_id:
            return None, lambda *_args, **_kwargs: None

        _require_copilot_conversation(project.id, conversation_id, owner_id)
        turn_service = _copilot_turn_service()
        conversation = turn_service.begin_turn(
            conversation_id,
            user_content=message,
            scope=_scope_from_project(project),
            owner_id=owner_id,
        )
        history = turn_service.history_messages(
            conversation,
            max_messages=app_config.wren_copilot_max_history_messages,
        )

        def commit(content: str, changeset: Changeset | None = None) -> None:
            turn_service.commit_turn(
                conversation_id,
                assistant_content=content,
                artifacts=[changeset_to_artifact(changeset)] if changeset else [],
                owner_id=owner_id,
            )

        return history, commit

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/copilot",
        response_model=Changeset,
    )
    def run_project_copilot(
        project_id: str,
        request: CopilotTurnRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> Changeset:
        """Run one agentic MDL-editing turn; returns a reviewable changeset."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        _require_project_ready(project, identity.owner_id)
        # Persistent thread: append the user turn, feed prior turns as history,
        # then persist the assistant turn + changeset artifact. ``conversation_id``
        # absent → stateless one-shot (backward compatible). Guard the preflight
        # (store/schema/thread reads) so a setup failure surfaces as a 502 with a
        # diagnosable message instead of a bare 500 (mirrors the stream route).
        try:
            files = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
            schema_index = _schema_index_for_project(project, fastapi_request)
            history, commit = _copilot_thread_turn(
                request.conversation_id, project, request.message, identity.owner_id
            )
        except HTTPException:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            logger.exception("Copilot preflight failed for project %s", project_id)
            raise HTTPException(
                status_code=502,
                detail=f"Copilot preflight failed: {type(ex).__name__}: {ex}",
            ) from ex
        try:
            changeset = run_copilot(
                model_client=active_model_client,
                files=files,
                schema_index=schema_index,
                user_message=request.message,
                attachments_text=_attachments_text(request.attachments),
                instructions=_recalled_instructions(
                    project, identity.owner_id, request.message
                ),
                history=history,
                model=request.model,
                max_steps=request.max_steps,
                deep_validate=app_config.wren_modeling_deep_validation,
                autopilot=app_config.wren_copilot_autopilot_enabled,
                document_store=active_semantic_layer_store,
                document_index=active_document_index,
                project_id=project_id,
                owner_id=identity.owner_id,
                retrieve_k=app_config.wren_document_retrieve_k,
            )
        except Exception as ex:  # pylint: disable=broad-except
            # Record the failure as the assistant turn so the thread stays paired,
            # then surface the 502 to the client.
            commit(f"The Copilot turn failed: {ex}")
            raise HTTPException(status_code=502, detail=str(ex)) from ex
        commit(changeset.message or "", changeset)
        return changeset

    @api.post("/agent/semantic-layer/projects/{project_id}/copilot/stream")
    def stream_project_copilot(  # noqa: C901
        project_id: str,
        request: CopilotTurnRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream the agentic edit loop: ``progress`` steps then ``complete``."""

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        _require_project_ready(project, identity.owner_id)
        # Resolve request-scoped context before streaming starts (no Request access
        # from the worker thread). A StreamingResponse commits a 200 status the
        # moment its body iterator is entered, so any failure here must surface as a
        # normal HTTP error *before* streaming begins. Without this guard an
        # unhandled preflight error collapses into a bare 500 ("Internal Server
        # Error", 21 bytes) with no logged traceback -- unlike worker-loop errors,
        # which are streamed back as ``error`` events. Mirror the non-stream
        # sibling's 502 contract and log the cause so it is diagnosable.
        try:
            files = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
            schema_index = _schema_index_for_project(project, fastapi_request)
            instructions = _recalled_instructions(
                project, identity.owner_id, request.message
            )
            attachments_text = _attachments_text(request.attachments)
            # Append the user turn + assemble history in request scope (the worker
            # thread has no Request access); ``commit`` persists the result after.
            history, commit = _copilot_thread_turn(
                request.conversation_id, project, request.message, identity.owner_id
            )
        except HTTPException:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            logger.exception(
                "Copilot stream preflight failed for project %s", project_id
            )
            raise HTTPException(
                status_code=502,
                detail=f"Copilot preflight failed: {type(ex).__name__}: {ex}",
            ) from ex

        def event_stream() -> Any:
            events: queue.Queue[tuple[str, Any]] = queue.Queue()
            holder: dict[str, Any] = {}

            def on_step(step: Any) -> None:
                events.put(("progress", step))

            def run() -> None:
                try:
                    holder["changeset"] = run_copilot(
                        model_client=active_model_client,
                        files=files,
                        schema_index=schema_index,
                        user_message=request.message,
                        attachments_text=attachments_text,
                        instructions=instructions,
                        history=history,
                        model=request.model,
                        max_steps=request.max_steps,
                        deep_validate=app_config.wren_modeling_deep_validation,
                        autopilot=app_config.wren_copilot_autopilot_enabled,
                        on_step=on_step,
                        document_store=active_semantic_layer_store,
                        document_index=active_document_index,
                        project_id=project_id,
                        owner_id=identity.owner_id,
                        retrieve_k=app_config.wren_document_retrieve_k,
                    )
                except Exception as ex:  # pylint: disable=broad-except
                    holder["error"] = str(ex)
                finally:
                    events.put(("done", None))

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                while True:
                    kind, payload = events.get()
                    if kind == "done":
                        break
                    if kind == "progress":
                        yield _conversation_sse(
                            {
                                "type": "progress",
                                "agent_step": payload.model_dump(mode="json"),
                            }
                        )
            except GeneratorExit:
                # Client disconnected (e.g. pressed Stop) before completion. Record
                # a cancellation so the stored transcript stays paired, then let the
                # worker finish in the background and propagate the close.
                commit("Generation cancelled.")
                raise
            worker.join()
            if "error" in holder:
                # Persist the failure as the assistant turn (paired transcript),
                # then surface the terminal error event.
                commit(f"The Copilot turn failed: {holder['error']}")
                yield _conversation_sse({"type": "error", "detail": holder["error"]})
            else:
                changeset = holder["changeset"]
                commit(changeset.message or "", changeset)
                yield _conversation_sse(
                    {
                        "type": "complete",
                        "changeset": changeset.model_dump(mode="json"),
                    }
                )

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/copilot/apply",
        response_model=list[MdlFile],
    )
    def apply_project_copilot(
        project_id: str,
        request: ChangesetApplyRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[MdlFile]:
        """Persist the user-accepted changeset items as drafts."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        try:
            applied = apply_changeset_items(
                active_mdl_file_store,
                project_id=project_id,
                items=request.items,
                owner_id=identity.owner_id,
            )
        except MdlFileNotFoundError as ex:
            raise HTTPException(status_code=404, detail="MDL file not found.") from ex
        except MdlFileExistsError as ex:
            raise HTTPException(status_code=409, detail=str(ex)) from ex
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

        # Record the apply as an assistant turn so a resumed thread shows that the
        # proposal was applied (parity with the SQL agent's execute-sql turn).
        if request.conversation_id:
            _require_copilot_conversation(
                project_id, request.conversation_id, identity.owner_id
            )
            count = len(applied)
            noun = "draft" if count == 1 else "drafts"
            _copilot_turn_service().commit_turn(
                request.conversation_id,
                assistant_content=f"Applied {count} {noun}.",
                owner_id=identity.owner_id,
            )
        return applied

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/materialize",
        response_model=WrenMaterializationResult,
    )
    def materialize_semantic_project(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> WrenMaterializationResult:
        """Materialize active MDL JSON files for read-only Wren context use."""

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

    def _resolve_onboarding_dataset_ids(
        superset_client_for_request: Any,
        project: SemanticProject,
        request: OnboardingRequest,
    ) -> list[int] | None:
        """Turn an ``OnboardingRequest`` into concrete dataset ids (or ``None``).

        ``None`` means "the whole schema" — the legacy full-introspection path.
        ``include`` selects exactly the chosen datasets; ``all`` means the schema,
        optionally minus excludes (resolved server-side, bounded by the table-scan
        limit — the same bound onboarding already applies).
        """

        if request.mode == "include":
            if not request.dataset_ids:
                raise HTTPException(
                    status_code=400,
                    detail="Select at least one table to onboard.",
                )
            return list(dict.fromkeys(request.dataset_ids))

        # mode == "all"
        if not request.exclude_dataset_ids:
            return None  # whole schema — unchanged behavior

        excluded = set(request.exclude_dataset_ids)
        candidates = superset_client_for_request.list_datasets(
            database_id=project.default_database_id,
            catalog_name=project.catalog_name,
            schema_name=project.schema_name,
            limit=app_config.wren_schema_table_scan_limit,
        )
        resolved = [dataset.id for dataset in candidates if dataset.id not in excluded]
        if not resolved:
            raise HTTPException(
                status_code=400,
                detail="No tables remain after exclusions.",
            )
        return resolved

    def _onboarding_context(
        project: SemanticProject,
        fastapi_request: Request,
        request: OnboardingRequest | None = None,
    ) -> tuple[Any, list[int] | None]:
        """Fetch the onboarding schema context for the requested table selection.

        Returns ``(context, resolved_dataset_ids)``. ``resolved_dataset_ids`` is
        recorded on the onboarding provenance entry (Feature B). Maps auth errors.
        """

        if project.default_database_id is None:
            raise HTTPException(
                status_code=400,
                detail="Project has no associated database for onboarding.",
            )
        request = request or OnboardingRequest()
        request_context_provider, request_superset_client = build_superset_runtime(
            fastapi_request
        )
        try:
            dataset_ids = _resolve_onboarding_dataset_ids(
                request_superset_client, project, request
            )
            # CR3: onboard the whole scope (full introspection) unless a subset was
            # selected. ``get_full_schema`` forwards ``dataset_ids`` to the id-filtered
            # fetch (context/superset_metadata.py), so a selection seeds only those.
            fetch_full = getattr(request_context_provider, "get_full_schema", None)
            fetch = fetch_full or request_context_provider.get_context
            context = fetch(
                AgentQueryRequest(
                    question="semantic layer onboarding",
                    database_id=project.default_database_id,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                    dataset_ids=dataset_ids or [],
                )
            )
            return context, dataset_ids
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

    def _start_onboarding_job(
        project: SemanticProject,
        context: Any,
        owner_id: str,
        dataset_ids: list[int] | None = None,
    ) -> SemanticJob:
        """Create + submit the onboarding job.

        Onboarding auto-activates valid base models; this also re-indexes retrieval
        so the freshly active layer is searchable immediately (E6 deploy→reindex).
        ``dataset_ids`` (the selected subset, or ``None`` for the whole schema) is
        recorded on the provenance entry (Feature B).
        """

        mode = "selected" if dataset_ids is not None else "all"
        job = active_job_store.create(kind="onboarding", project_id=project.id)
        scope = _scope_from_project(project)
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=owner_id,
            event_type="onboarding_started",
            scope=scope,
            document_id=None,
            message="Onboarding started.",
            project_id=project.id,
            detail={
                "actor": owner_id,
                "mode": mode,
                "dataset_ids": dataset_ids or [],
            },
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
            # Auto-activation populated the layer; refresh the retrieval index so the
            # active models are searchable now (best-effort, degrade-closed).
            reindex_project_mdl(
                retriever=active_retriever,
                project_id=project.id,
                owner_id=owner_id,
                mdl_file_store=active_mdl_file_store,
            )
            active_job_store.complete(job.id, result)
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=owner_id,
                event_type="onboarding_completed",
                scope=scope,
                document_id=None,
                message=(
                    f"Onboarded {result.model_count} model(s); "
                    f"{result.activated_count} activated."
                ),
                project_id=project.id,
                detail={
                    "actor": owner_id,
                    "mode": mode,
                    "dataset_ids": dataset_ids or [],
                    "model_count": result.model_count,
                    "activated_count": result.activated_count,
                    "paths": [f.path for f in result.files],
                    "warnings": result.warnings,
                },
            )

        active_job_runner.submit(_run_onboarding)
        # Re-fetch so an inline runner reflects completion immediately while a
        # threaded runner returns the still-running job for the client to poll.
        return active_job_store.get(job.id)

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/onboard",
        response_model=SemanticJob,
        status_code=202,
    )
    def onboard_semantic_project(
        project_id: str,
        fastapi_request: Request,
        request: OnboardingRequest | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticJob:
        """Start async schema onboarding; poll the returned job for the result.

        The schema context is fetched synchronously (request-scoped auth); only
        the slower LLM generation and MDL writes run in the background. The body
        selects which tables to onboard (Feature A); an absent/empty body onboards
        the whole schema (backward compatible).
        """

        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        context, dataset_ids = _onboarding_context(
            project, fastapi_request, request or OnboardingRequest()
        )
        return _start_onboarding_job(
            project, context, identity.owner_id, dataset_ids=dataset_ids
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/reset",
    )
    def reset_semantic_project(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, int]:
        """Delete all MDL for a project, returning it to the un-onboarded state.

        A destructive "start over": every MDL file (base models, enrichment overlays,
        and hand-edits) is soft-deleted, so the project's readiness falls back to
        ``empty`` and the editor re-gates the Copilot behind an explicit onboard.
        Reset does **not** auto re-onboard — onboarding is always a deliberate user
        action (it is the required first step on an empty layer). Uploaded
        **documents are kept**, so the operator can re-enrich after re-onboarding.
        Returns the number of MDL files deleted.
        """

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        deleted = 0
        for file in active_mdl_file_store.list(project_id, owner_id=identity.owner_id):
            active_mdl_file_store.delete(file.id, owner_id=identity.owner_id)
            deleted += 1
        # Provenance is the editing history of the MDL directory, so it resets with
        # it (delete-on-reset). Document events are NOT in PROVENANCE_EVENT_TYPES —
        # uploaded documents survive a reset, so their history must too.
        active_semantic_layer_store.delete_project_events(
            project_id,
            owner_id=identity.owner_id,
            types=PROVENANCE_EVENT_TYPES,
        )
        return {"deleted": deleted}

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
        """Upload a reviewed MDL JSON file to a governed semantic project."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        try:
            content = (await file.read()).decode("utf-8")
            target_path = path or file.filename or "model.json"
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
                detail="MDL JSON upload must be UTF-8 text.",
            ) from ex
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/documents",
        response_model=list[SemanticDocument],
    )
    def list_project_source_documents(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[SemanticDocument]:
        """List the uploaded source documents for a governed semantic project."""

        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        return active_semantic_layer_store.list_project_documents(
            project_id, owner_id=identity.owner_id
        )

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
        content = await file.read()
        try:
            document = register_document(
                filename=file.filename or "document",
                content_type=file.content_type or "application/octet-stream",
                content=content,
                scope=scope,
                project_id=project_id,
                owner_id=identity.owner_id,
                config=app_config,
                store=active_semantic_layer_store,
                storage=active_document_storage,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

        if document.deduplicated:
            # Byte-identical to an existing document in this project: it is already
            # extracted + indexed, so skip re-extraction and return it as-is (the
            # ``deduplicated`` flag lets the client surface a "reusing" notice).
            return document

        def _extract() -> None:
            extract_document(
                document.id,
                owner_id=identity.owner_id,
                config=app_config,
                store=active_semantic_layer_store,
                storage=active_document_storage,
                extractor=active_document_extractor,
                document_index=active_document_index,
            )

        if document.size_bytes <= app_config.wren_document_async_threshold_bytes:
            # Small file: extract inline so the response carries the final status.
            _extract()
        else:
            # Large file: extract on a background thread; the document row tracks
            # progress (uploaded -> extracting -> extracted/needs_ocr/error) and is
            # pollable via GET .../documents/{id}. (InlineJobRunner in tests runs
            # this synchronously, so the response already reflects completion.)
            active_semantic_layer_store.update_document(
                document.model_copy(update={"status": "extracting"}),
                owner_id=identity.owner_id,
            )
            active_job_runner.submit(_extract)
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
                document_index=active_document_index,
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
        # CR2: enrichment overlays semantics onto *introspected* structure (Wren's
        # authority model) — the LLM never authors models. A project with no base models
        # (onboarding never run, or its drafts never reviewed) has nothing to enrich, so
        # fail closed with actionable copy instead of fabricating a schema-name blob.
        if not _project_has_models(project.id, owner_id=identity.owner_id):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This semantic project has no base models to enrich. Run "
                    "onboarding for the schema, then review the generated base "
                    "models, before enriching a document."
                ),
            )
        # E2: ground the proposal on the authoritative physical schema up front so
        # the model avoids inventing columns/tables, and the modeling repair loop
        # can correct physical errors (E3) — then reuse the same index to re-validate.
        schema_index = _schema_index_for_project(project, fastapi_request)
        # Inject operator instructions for the project's scope (global + those most
        # relevant to the document) so guidance steers the enrichment too.
        instructions: list[str] = []
        if project.default_database_id is not None:
            scope = _scope_from_project(project)
            instructions = [
                item.instruction
                for item in active_instruction_store.recall(
                    f"{document.filename} {document.summary or ''}".strip(),
                    scope_hash=instruction_scope_hash(scope),
                    owner_id=identity.owner_id,
                    k=app_config.wren_instruction_recall_k,
                )
            ]
        proposal = _enrichment_proposal(
            project=project,
            document=document,
            wren_client=active_wren_client,
            schema=schema_index.to_tables() if schema_index is not None else None,
            # C3: pass catalog types only when the live fetch supplied them; the
            # names-only snapshot path leaves this None (degrades to E2 grounding).
            schema_types=(
                schema_index.typed_tables()
                if schema_index is not None and schema_index.has_types()
                else None
            ),
            instructions=instructions,
        )
        # Re-validate the proposal against the live schema (R3) so hallucinated
        # columns/tables are visible before the user tries to activate.
        if schema_index is not None:
            validation = validate_mdl(
                proposal.proposed_content,
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

    # -- Document RAG + CRUD (uploaded_documents_rag_and_crud.md) ----------

    def _require_document_indexing() -> None:
        if not app_config.wren_document_indexing_enabled:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Document indexing is disabled "
                    "(set WREN_DOCUMENT_INDEXING_ENABLED=true)."
                ),
            )

    def _load_authorized_document(
        document_id: str,
        request: Request,
        identity: AgentIdentity,
        permission: SemanticPermission,
    ) -> SemanticDocument:
        """Load a document and enforce object-level scope auth (404 then access)."""

        try:
            document = active_semantic_layer_store.get_document(
                document_id, owner_id=identity.owner_id
            )
        except SemanticDocumentNotFoundError as ex:
            raise HTTPException(
                status_code=404, detail="Semantic document not found."
            ) from ex
        authorize_semantic_scope(
            request, document.scope, identity=identity, permission=permission
        )
        return document

    @api.get(
        "/agent/semantic-layer/documents/{document_id}/chunks",
        response_model=list[DocumentChunk],
    )
    def list_document_chunks(
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[DocumentChunk]:
        """List a document's persisted chunks in document order."""

        _require_document_indexing()
        _load_authorized_document(
            document_id, fastapi_request, identity, SemanticPermission.READ
        )
        return active_semantic_layer_store.list_chunks(
            document_id, owner_id=identity.owner_id
        )

    @api.get("/agent/semantic-layer/documents/{document_id}/content")
    def download_document(
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream the raw uploaded bytes of a document (original file download)."""

        document = _load_authorized_document(
            document_id, fastapi_request, identity, SemanticPermission.READ
        )
        try:
            content = active_document_storage.read(document.storage_uri)
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(
                status_code=404, detail="Document content is unavailable."
            ) from ex
        disposition = f'attachment; filename="{document.filename}"'
        return StreamingResponse(
            iter([content]),
            media_type=document.content_type or "application/octet-stream",
            headers={"Content-Disposition": disposition},
        )

    @api.delete(
        "/agent/semantic-layer/documents/{document_id}",
        response_model=SemanticDocument,
    )
    def delete_semantic_document(
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Delete a document everywhere (vectors, chunk rows, blob, row)."""

        _load_authorized_document(
            document_id, fastapi_request, identity, SemanticPermission.WRITE
        )
        return delete_document_cascade(
            document_id,
            owner_id=identity.owner_id,
            store=active_semantic_layer_store,
            storage=active_document_storage,
            document_index=active_document_index,
        )

    @api.post(
        "/agent/semantic-layer/documents/{document_id}/reindex",
        response_model=list[DocumentChunk],
    )
    def reindex_semantic_document(
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[DocumentChunk]:
        """Re-chunk + re-embed a document (idempotent)."""

        _require_document_indexing()
        _load_authorized_document(
            document_id, fastapi_request, identity, SemanticPermission.WRITE
        )
        return reindex_document(
            document_id,
            owner_id=identity.owner_id,
            store=active_semantic_layer_store,
            document_index=active_document_index,
        )

    @api.get(
        "/agent/semantic-layer/documents/{document_id}/retrieve",
        response_model=list[DocumentChunk],
    )
    def retrieve_document_chunks(
        document_id: str,
        q: str,
        fastapi_request: Request,
        k: int | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> list[DocumentChunk]:
        """Return the document's chunks most relevant to ``q`` (RAG; degrade-closed)."""

        _require_document_indexing()
        _load_authorized_document(
            document_id, fastapi_request, identity, SemanticPermission.READ
        )
        chunks = active_semantic_layer_store.list_chunks(
            document_id, owner_id=identity.owner_id
        )
        document = active_semantic_layer_store.get_document(
            document_id, owner_id=identity.owner_id
        )
        scope_key = document_scope_key(document.project_id, document.scope)
        return active_document_index.retrieve(
            q,
            chunks,
            scope_key=scope_key,
            k=k or app_config.wren_document_retrieve_k,
        )

    @api.post(
        "/agent/semantic-layer/documents/{document_id}/summarize",
        response_model=SemanticDocument,
    )
    def summarize_semantic_document(
        document_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticDocument:
        """Regenerate a document's summary with the model (degrade-closed)."""

        document = _load_authorized_document(
            document_id, fastapi_request, identity, SemanticPermission.WRITE
        )
        text = (document.extracted_text or "").strip()
        if not text:
            return document
        budget = app_config.wren_document_prompt_char_budget or 20_000
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "Summarize the document for a data analyst in 3-5 sentences. "
                    "Focus on the business entities, metrics, and rules it describes."
                ),
            ),
            ChatMessage(role="user", content=text[:budget]),
        ]
        try:
            summary = active_model_client.chat(messages).content.strip()
        except Exception as ex:  # pylint: disable=broad-except
            raise HTTPException(
                status_code=502, detail=f"Summarization failed: {ex}"
            ) from ex
        if not summary:
            return document
        updated = document.model_copy(update={"summary": summary})
        return active_semantic_layer_store.update_document(
            updated, owner_id=identity.owner_id
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/documents/duplicates",
        response_model=list[DocumentChunkMatch],
    )
    def find_project_duplicate_chunks(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[DocumentChunkMatch]:
        """Find exact-duplicate chunk pairs across a project's documents."""

        _require_document_indexing()
        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        chunks = active_semantic_layer_store.list_project_chunks(
            project_id, owner_id=identity.owner_id
        )
        return find_exact_duplicate_matches(chunks)

    @api.post(
        "/agent/semantic-layer/instructions",
        response_model=Instruction,
    )
    def create_instruction(
        fastapi_request: Request,
        request: InstructionCreateRequest,
        identity: AgentIdentity = identity_dependency,
    ) -> Instruction:
        """Add a user-authored instruction injected into generation for a scope."""

        authorize_semantic_scope(
            fastapi_request,
            request.scope,
            identity=identity,
            permission=SemanticPermission.WRITE,
        )
        text = request.instruction.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Instruction is empty.")
        return active_instruction_store.add(
            instruction=text,
            scope_hash=instruction_scope_hash(request.scope),
            owner_id=identity.owner_id,
            is_global=request.is_global,
        )

    @api.get(
        "/agent/semantic-layer/instructions",
        response_model=list[Instruction],
    )
    def list_instructions(
        fastapi_request: Request,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        dataset_ids: str | None = Query(default=None),
        identity: AgentIdentity = identity_dependency,
    ) -> list[Instruction]:
        """List instructions for a scope."""

        scope = ConversationScope(
            database_id=database_id,
            catalog_name=catalog_name,
            schema_name=schema_name,
            dataset_ids=_parse_dataset_ids(dataset_ids),
        )
        authorize_semantic_scope(
            fastapi_request,
            scope,
            identity=identity,
            permission=SemanticPermission.READ,
        )
        return active_instruction_store.list_instructions(
            scope_hash=instruction_scope_hash(scope),
            owner_id=identity.owner_id,
        )

    @api.delete("/agent/semantic-layer/instructions/{instruction_id}")
    def delete_instruction(
        instruction_id: str,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, bool]:
        """Delete one of the caller's instructions."""

        deleted = active_instruction_store.delete(
            instruction_id, owner_id=identity.owner_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Instruction not found.")
        return {"deleted": True}

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

    @api.get("/agent/semantic-layer/mdl-schema")
    def get_mdl_schema(
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, Any]:
        """Return the native MDL manifest JSON Schema.

        This is the same camelCase shape the engine enforces (derived from
        ``MdlManifest``), exposed so the editor can validate as-you-type against
        one source of truth (wren_full.md F2/DF2). It carries no scope-bound data
        — only the structural contract — so it requires a valid agent identity but
        no per-scope authorization.
        """

        return MdlManifest.model_json_schema(by_alias=True)

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

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/provenance",
        response_model=list[ProvenanceEntry],
    )
    def get_project_provenance(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> list[ProvenanceEntry]:
        """Return the MDL directory's provenance timeline (newest-first, capped).

        Onboarding / enrichment / MDL-CRUD entries only — document events are
        excluded (they outlive a reset). Reset clears this log (delete-on-reset).
        """

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
        entries = [
            entry
            for entry in (provenance_from_event(event) for event in events)
            if entry is not None
        ]
        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        return entries[:PROVENANCE_HISTORY_CAP]

    @api.post("/agent/validate-sql")
    def validate_sql(request: ValidateSqlRequest) -> SqlValidation:
        """Validate SQL without invoking the model."""

        return validate_read_only_sql(
            request.sql,
            dialect=request.dialect,
            default_limit=request.default_limit or app_config.default_sql_limit,
            policy_mode=app_config.sql_policy_mode,
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


def _create_schema_snapshot_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> SchemaSnapshotStore:
    if config.semantic_layer_store == "memory":
        return InMemorySchemaSnapshotStore()
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy schema snapshot store requires a database.")
        return SqlAlchemySchemaSnapshotStore(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_SEMANTIC_LAYER_STORE value "
        f"{config.semantic_layer_store!r}. Expected one of: memory, sqlalchemy."
    )


def _create_job_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> JobStore:
    if config.semantic_layer_store == "memory":
        return InMemoryJobStore()
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy job store requires a database.")
        return SqlAlchemyJobStore(session_factory)
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
    schema: dict[str, list[str]] | None = None,
    schema_types: dict[str, dict[str, str]] | None = None,
    instructions: list[str] | None = None,
) -> MdlEnrichmentProposal:
    return wren_client.propose_mdl_from_document(
        project=project,
        document=document,
        schema=schema,
        schema_types=schema_types,
        instructions=instructions,
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
    detail: dict[str, Any] | None = None,
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
            detail=detail,
        ),
        owner_id=owner_id,
    )


logger = logging.getLogger(__name__)


def _requires_agent_database(config: AgentConfig) -> bool:
    return (
        config.conversation_store == "sqlalchemy"
        or config.semantic_layer_store == "sqlalchemy"
        or config.wren_memory_store == "sqlalchemy"
    )


def _parity_features_enabled(config: AgentConfig) -> bool:
    """Return whether any Wren full-parity seam needs a durable manifest.

    The engine's compiled-context cache, the embedding retrieval index, and the
    memory learning loop all key off a durable, materialized MDL manifest; an
    in-memory semantic store would silently lose that state on restart.
    """

    return (
        config.wren_engine != "passthrough"
        or config.wren_retriever != "keyword"
        or config.wren_memory_store != "none"
    )


def _validate_semantic_persistence_config(config: AgentConfig) -> None:
    """Fail closed if a parity feature is enabled without durable persistence."""

    if not _parity_features_enabled(config):
        return
    if config.semantic_layer_store == "sqlalchemy":
        return
    raise ValueError(
        "Wren parity features require durable semantic persistence. One of "
        f"wren_engine={config.wren_engine!r}, wren_retriever="
        f"{config.wren_retriever!r}, or wren_memory_store="
        f"{config.wren_memory_store!r} is enabled, but "
        f"semantic_layer_store={config.semantic_layer_store!r}. Set "
        "AI_AGENT_SEMANTIC_LAYER_STORE=sqlalchemy so models, the materialized "
        "manifest, and learned examples survive restarts."
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
