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

import asyncio
import functools
import hashlib
import json  # noqa: TID251 - keep the standalone agent independent of Superset
import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
from fastapi.concurrency import run_in_threadpool
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
    DEFAULT_OWNER_ID,
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
from superset_ai_agent.llm.metered import wrap_model_client
from superset_ai_agent.llm.usage_store import (
    InMemoryLlmUsageStore,
    LlmUsageStore,
    SqlAlchemyLlmUsageStore,
)
from superset_ai_agent.persistence.database import (
    create_engine_from_config,
    create_session_factory,
    run_migrations,
)
from superset_ai_agent.persistence.ttl_cache import TtlCache
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    HealthResponse,
    LlmUsageSummary,
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
    CoverageCancelledError,
    CoverageDocument,
    CoverageProgress as CoverageProgressEvent,
    InMemoryCoverageCache,
    run_coverage_audit,
    run_directory_coverage,
)
from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetApplyRequest,
    ChangesetItem,
    CopilotInspector,
    CopilotTurnRequest,
    CoverageProgress,
    CoverageReport,
    CoverageRequest,
    CoverageRun,
    InstructionView,
    MessageAttachment,
    ToolCallRecord,
    WorkspaceNode,
)
from superset_ai_agent.semantic_layer.copilot.service import (
    apply_changeset_items,
    apply_provenance_payload,
    build_deploy_preview,
    build_inspector,
    changeset_from_conversation,
    changeset_to_artifact,
    run_copilot,
)
from superset_ai_agent.semantic_layer.copilot.workspace import build_workspace_tree
from superset_ai_agent.semantic_layer.coverage_store import (
    CoverageRunNotFoundError,
    CoverageRunStore,
    InMemoryCoverageRunStore,
    SqlAlchemyCoverageRunStore,
)
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
from superset_ai_agent.semantic_layer.engine import (
    create_semantic_engine,
    evaluate_semantic_factors,
)
from superset_ai_agent.semantic_layer.events import to_sse
from superset_ai_agent.semantic_layer.extractors import (
    CompositeDocumentExtractor,
    DocumentExtractor,
)
from superset_ai_agent.semantic_layer.file_storage import (
    DocumentStorage,
    LocalDocumentStorage,
    PostgresDocumentStorage,
    S3DocumentStorage,
)
from superset_ai_agent.semantic_layer.golden_queries import (
    find_golden_queries_file,
    GOLDEN_QUERIES_PATH,
    GoldenQuery,
    GoldenQueryPromoteRequest,
    upsert_golden_query,
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
    coalesce_user_runs,
    InstructionCreateRequest,
    MdlBulkStatusRequest,
    MdlBulkStatusResult,
    MdlEnrichmentProposal,
    MdlFile,
    MdlFileCreateRequest,
    MdlFileStatus,
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
    SemanticModeStatus,
    SemanticProject,
    SemanticProjectDuplicateRequest,
    SemanticProjectReadiness,
    SemanticProjectRenameRequest,
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

# Durable project-events SSE tuning. The stream tails the event store on this
# interval, recycles the connection after the max lifetime (the client reconnects
# once, cheaply), and advertises the reconnect backoff so a dropped connection
# never becomes a hot loop.
SEMANTIC_EVENTS_POLL_INTERVAL_SECONDS = 2.0
SEMANTIC_EVENTS_MAX_STREAM_SECONDS = 300.0
SEMANTIC_EVENTS_RETRY_MS = 15000


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
    coverage_run_store: CoverageRunStore | None = None,
    schema_snapshot_store: SchemaSnapshotStore | None = None,
    retriever: Any | None = None,
    llm_call_store: LlmUsageStore | None = None,
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
    # LLM-call telemetry: wrap the model client at the one chokepoint every call
    # passes through so counting + timing needs no call-site changes. Durable when
    # an agent DB is configured; process-local otherwise. Recording is fail-open
    # inside the wrapper, so this never affects an agent response.
    active_llm_usage_store = llm_call_store or _create_llm_usage_store(
        session_factory=session_factory
    )
    active_model_client = wrap_model_client(
        active_model_client, store=active_llm_usage_store, config=app_config
    )
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
    active_document_storage = document_storage or _create_document_storage(
        app_config, session_factory=session_factory
    )
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
    # Durable background coverage runs (Feature B) — score/report history plus the
    # supersession lease so a new MDL change cancels a stale in-flight run.
    active_coverage_run_store = coverage_run_store or _create_coverage_run_store(
        app_config,
        session_factory=session_factory,
    )
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
    # Engine binding for the semantic-mode badge factor evaluation (cheap, stable
    # per process). Shares the same factory the graphs use so name/availability
    # match what a query would actually see.
    app_semantic_engine = create_semantic_engine(app_config)
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

    def require_admin(request: Request) -> None:
        # Gate admin-only agent surfaces (LLM usage). Defense-in-depth: the
        # Superset menu/route are also admin-gated, but the API enforces it too so
        # a direct call cannot bypass the UI.
        if not active_identity_provider.is_admin(request):
            raise HTTPException(status_code=403, detail="Admin access required.")

    admin_dependency = Depends(require_admin)
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

    # Authorization derives a project's read/write level from the caller's *live*
    # Superset DB-access, which means a dataset list + per-dataset N+1 introspection
    # for every schema in scope — on every authorized request. Every MDL Lab call
    # authorizes, and the editor's coverage badge/banner each hold an SSE channel
    # that re-polls status, so without memoization a single open project storms
    # Superset with identical introspection. A short TTL makes repeated checks
    # within the window reuse one build. Keyed by ``owner_id`` so one principal's
    # access view never satisfies another's check; access only changes on a role
    # change (a re-login), so a brief staleness window is safe. Only positive-TTL
    # builds are cached (the cache no-ops at ttl<=0).
    _auth_context_cache: TtlCache[tuple[Any, ...], Any] = TtlCache(
        ttl_seconds=app_config.wren_schema_index_cache_ttl_seconds,
    )

    def build_semantic_access_service(
        request: Request,
        *,
        owner_id: str | None = None,
    ) -> SemanticAccessService:
        def load_context(scope: ConversationScope) -> Any:
            # Cache only when the caller identifies the principal (the hot
            # authorize paths do); anonymous builds fall through uncached.
            cache_key = (
                owner_id,
                scope.database_id,
                scope.catalog_name,
                scope.schema_name,
                tuple(scope.dataset_ids or ()),
            )
            if owner_id is not None:
                cached = _auth_context_cache.get(cache_key)
                if cached is not None:
                    return cached
            request_context_provider, _ = build_superset_runtime(request)
            context = request_context_provider.get_context(
                AgentQueryRequest(
                    question="semantic layer scope authorization",
                    database_id=scope.database_id,
                    catalog_name=scope.catalog_name,
                    schema_name=scope.schema_name,
                    dataset_ids=scope.dataset_ids,
                )
            )
            if owner_id is not None:
                _auth_context_cache.set(cache_key, context)
            return context

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
            build_semantic_access_service(
                request, owner_id=identity.owner_id
            ).require_scope_permission(
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
                request, owner_id=owner_id
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

    @api.get("/agent/admin/llm-usage", response_model=LlmUsageSummary)
    def llm_usage(
        request: Request,
        days: int | None = None,
        _admin: None = admin_dependency,
    ) -> LlmUsageSummary:
        """Aggregated LLM-call telemetry (admin only).

        ``days`` optionally limits the window to the last N days; omitted = all
        retained history. Read-only and gated by :func:`require_admin`.
        """

        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
            if days is not None and days > 0
            else None
        )
        return active_llm_usage_store.summary(since=since)

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
            projects = build_semantic_access_service(fastapi_request).list_projects(
                identity=identity,
                scope=scope,
            )
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex
        # Enrich each row with its latest coverage score for the browser badge.
        # One batched lookup instead of one query per project (N+1). Best-effort:
        # a coverage-store hiccup must never break the project list.
        try:
            coverage_by_project = active_coverage_run_store.latest_complete_bulk(
                [project.id for project in projects]
            )
            for project in projects:
                run = coverage_by_project.get(project.id)
                if run is not None:
                    project.coverage_score = run.score
        except Exception:  # pylint: disable=broad-except
            logger.debug("coverage score bulk lookup failed", exc_info=True)
        return projects

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
        """Archive a semantic project (requires write access to its database)."""

        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        active_semantic_project_store.delete(project_id, owner_id=identity.owner_id)
        return {"deleted": True}

    @api.patch(
        "/agent/semantic-layer/projects/{project_id}",
        response_model=SemanticProject,
    )
    def rename_semantic_project(
        project_id: str,
        request: SemanticProjectRenameRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticProject:
        """Rename a semantic project (requires write access to its database)."""

        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="A project name is required.")
        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        return active_semantic_project_store.rename(
            project_id, name, owner_id=identity.owner_id
        )

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/duplicate",
        response_model=SemanticProject,
    )
    def duplicate_semantic_project(
        project_id: str,
        request: SemanticProjectDuplicateRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticProject:
        """Duplicate a project's MDL structure into a new project (fresh history).

        Copies the project identity, schema set, and MDL files; documents, coverage,
        and provenance are NOT carried (DP6/DP8). A single ``mdl_project_created``
        provenance entry records the clone's ``duplicated_from`` lineage.
        """

        # Read access on the source is sufficient — duplication is creative, not
        # destructive (it never mutates the source).
        source = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )
        clone = active_semantic_project_store.clone(
            project_id,
            new_name=(request.name or None),
            owner_id=identity.owner_id,
        )
        # The clone (project+memberships) and the file copy live in separate stores,
        # so they cannot share one transaction. If the file copy fails, compensate by
        # archiving the just-created clone — never leave an orphan empty project.
        try:
            active_mdl_file_store.duplicate_files(
                project_id, clone.id, owner_id=identity.owner_id
            )
        except Exception as ex:  # pylint: disable=broad-except
            try:
                active_semantic_project_store.delete(
                    clone.id, owner_id=identity.owner_id
                )
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to roll back an incomplete project duplicate %s.",
                    clone.id,
                    exc_info=True,
                )
            raise HTTPException(
                status_code=500,
                detail="Failed to duplicate the project's MDL files.",
            ) from ex
        if request.include_documents:
            # DP6 opt-in: copy the BI documents + chunks and re-embed them under the
            # clone's vector scope. Best-effort — the structural clone has already
            # succeeded, and vectors are an accelerator (recall degrades to keyword),
            # so a copy/embed failure must not fail the duplication. Large corpora
            # embed synchronously here; an async job is the follow-on for scale.
            try:
                copied_chunks = active_semantic_layer_store.duplicate_documents(
                    project_id, clone.id, owner_id=identity.owner_id
                )
                if copied_chunks:
                    active_document_index.index(
                        copied_chunks,
                        scope_key=document_scope_key(clone.id),
                    )
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to copy documents into project duplicate %s.",
                    clone.id,
                    exc_info=True,
                )
        _emit_project_created_provenance(
            clone=clone, source=source, owner_id=identity.owner_id
        )
        return clone

    @api.post(
        "/agent/semantic-layer/projects",
        response_model=SemanticProject,
    )
    def create_semantic_project(
        request: SemanticProjectResolveRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticProject:
        """Create a new named semantic project (the MDL Lab "New project" path)."""

        if not request.schema_name:
            raise HTTPException(status_code=400, detail="schema_name is required.")
        service = build_semantic_access_service(fastapi_request)
        # Prove DB access to the requested schema(s) before creating.
        service.require_schema_set_permission(
            identity=identity,
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_names=request.resolved_schema_names(),
            permission=SemanticPermission.WRITE,
        )
        try:
            return active_semantic_project_store.create(
                service.enrich_request(request), owner_id=identity.owner_id
            )
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

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
        actor_name: str | None = None,
    ) -> None:
        """Append an MDL-CRUD provenance event (best-effort; never blocks the write).

        Provenance is an audit aid, not part of the write contract — a failure to
        record must not fail the file operation (mirrors the Copilot step-sink).

        ``actor_name`` (the author's username/email, DP10) is captured at write time
        so a shared project's timeline can name *who* made a hand edit without a
        cross-user lookup the agent service cannot perform.
        """

        try:
            detail: dict[str, Any] = {
                "actor": owner_id,
                "path": file.path,
                "file_id": file.id,
                "source_type": file.source_type,
                # The active-set version this entry produced — the join key the
                # coverage-label overlay uses to annotate the entry (Feature B).
                "mdl_checksum": _active_mdl_checksum(project.id, owner_id),
            }
            if actor_name:
                detail["actor_name"] = actor_name
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

    def _emit_project_created_provenance(
        *,
        clone: SemanticProject,
        source: SemanticProject,
        owner_id: str,
    ) -> None:
        """Stamp a duplicated project's origin entry (best-effort; DP8 lineage)."""

        try:
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=owner_id,
                event_type="mdl_project_created",
                scope=_scope_from_project(clone),
                document_id=None,
                message=f'Duplicated from "{source.name}"',
                project_id=clone.id,
                detail={
                    "actor": owner_id,
                    "duplicated_from": source.id,
                    "source_name": source.name,
                },
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to record project-created provenance.", exc_info=True
            )

    def _emit_agent_apply_provenance(
        *,
        project: SemanticProject,
        owner_id: str,
        actor_name: str | None = None,
        items: list[ChangesetItem],
        conversation_id: str | None,
    ) -> None:
        """Record a Copilot apply as one provenance entry (best-effort).

        Reads the server-authoritative changeset back from the conversation for
        the agent's summary and the documents it consulted, classifies the apply
        as an enrichment pass (documents referenced) or a generic agent edit, and
        appends a single timeline entry. Never blocks the apply.
        """

        try:
            summary: str | None = None
            documents: list[dict[str, str | None]] = []
            tool_calls: list[ToolCallRecord] = []
            if conversation_id:
                conversation = active_conversation_store.get(
                    conversation_id, owner_id=owner_id
                )
                changeset = changeset_from_conversation(conversation)
                if changeset is not None:
                    summary = changeset.message
                    tool_calls = changeset.tool_calls
                    for document_id in changeset.referenced_document_ids:
                        filename: str | None = None
                        try:
                            document = active_semantic_layer_store.get_document(
                                document_id, owner_id=owner_id
                            )
                            filename = document.filename
                        except Exception:  # pylint: disable=broad-except
                            filename = None
                        documents.append({"id": document_id, "filename": filename})
                    # Inline attachments have no document id — record filename only.
                    for attachment in changeset.referenced_attachments:
                        documents.append({"id": None, "filename": attachment})
            event_type, message, detail = apply_provenance_payload(
                items=items,
                owner_id=owner_id,
                actor_name=actor_name,
                conversation_id=conversation_id,
                summary=summary,
                documents=documents,
                tool_calls=tool_calls,
            )
            # Stamp the resulting active-set version so the coverage-label overlay
            # can annotate this Copilot edit with its score (Feature B).
            detail["mdl_checksum"] = _active_mdl_checksum(project.id, owner_id)
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=owner_id,
                event_type=event_type,  # type: ignore[arg-type]
                scope=_scope_from_project(project),
                document_id=None,
                message=message,
                project_id=project.id,
                detail=detail,
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to record Copilot apply provenance event.", exc_info=True
            )

    # -- Background directory coverage (Feature B) ------------------------

    def _active_mdl_checksum(project_id: str, owner_id: str) -> str:
        """A deterministic version key for the active MDL directory.

        Hashes the sorted (path, per-file checksum) of active files — cheaper than
        materializing to disk and stable across reorderings. Drives both
        supersession (a change → a new key) and idempotency (same key → reuse).
        """

        active = sorted(
            (f.path, f.checksum)
            for f in active_mdl_file_store.list(project_id, owner_id=owner_id)
            if f.status == "active"
        )
        digest = hashlib.sha256(
            json.dumps(active, separators=(",", ":")).encode("utf-8")
        )
        return digest.hexdigest()

    def _coverage_documents(project_id: str, owner_id: str) -> list[CoverageDocument]:
        """Gather each project document's text (chunks, else extracted text)."""

        chunks_by_doc: dict[str, list[Any]] = {}
        for chunk in active_semantic_layer_store.list_project_chunks(
            project_id, owner_id=owner_id
        ):
            chunks_by_doc.setdefault(chunk.document_id, []).append(chunk)
        documents: list[CoverageDocument] = []
        for document in active_semantic_layer_store.list_project_documents(
            project_id, owner_id=owner_id
        ):
            doc_chunks = sorted(
                chunks_by_doc.get(document.id, []), key=lambda c: c.chunk_index
            )
            text = "\n\n".join(c.text for c in doc_chunks)
            if not text:
                text = document.extracted_text or ""
            if text.strip():
                documents.append(
                    CoverageDocument(
                        document_id=document.id,
                        filename=document.filename,
                        text=text,
                    )
                )
        return documents

    def _docs_checksum(documents: list[CoverageDocument]) -> str:
        payload = sorted(f"{d.document_id}:{len(d.text)}" for d in documents)
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _schedule_coverage(
        project: SemanticProject,
        owner_id: str,
        *,
        force: bool = False,
        recover_backfill: bool = True,
    ) -> bool:
        """Schedule a debounced directory coverage run on the active MDL set.

        Idempotent (skips an identical version already audited), superseding (a new
        change cancels any in-flight run), and a no-op when there is nothing to
        audit (no active MDL or no documents). Best-effort — never blocks the
        triggering write.

        ``force=True`` bypasses the version-idempotency short-circuit: it always
        supersedes any prior run and creates a fresh audit even when the active
        ``(mdl_checksum, docs_checksum)`` was already scored. This backs the
        explicit "Re-run analysis" action, which must recompute on demand rather
        than silently reuse the last result.

        ``recover_backfill=False`` suppresses the recovery back-fill on the
        already-audited path. The autonomous sweep uses this so its coverage pass
        only schedules coverage; recovery is the sole responsibility of the
        independent recovery pass (the two passes stay decoupled).

        Returns ``True`` when a fresh coverage run was created and submitted, else
        ``False`` (gated off, nothing to audit, or an idempotent skip). The sweep
        uses this to count work reliably even under a synchronous job runner.
        """

        if not (
            app_config.wren_copilot_enabled and app_config.wren_coverage_auto_enabled
        ):
            return False
        try:
            mdl_checksum = _active_mdl_checksum(project.id, owner_id)
            documents = _coverage_documents(project.id, owner_id)
            if not documents or not mdl_checksum:
                return False
            docs_checksum = _docs_checksum(documents)
            if not force:
                existing = active_coverage_run_store.find_complete(
                    project.id, mdl_checksum, docs_checksum
                )
                if existing is not None:
                    # The active version was already audited (idempotent — no
                    # re-run). But a run audited before the recovery feature
                    # existed (or whose recovery failed) carries no suggestions, so
                    # back-fill recovery for it: this lets already-active projects
                    # pick up recovery on the next trigger without a fresh audit.
                    # The recovery job re-checks the gate and is idempotent on its
                    # conversation id.
                    if (
                        recover_backfill
                        and app_config.wren_coverage_recovery_enabled
                        and existing.recovery_status in ("none", "failed")
                        and existing.report is not None
                        and (existing.report.missing + existing.report.partial) > 0
                    ):
                        active_coverage_run_store.set_recovery(
                            existing.id, status="pending"
                        )
                        active_job_runner.submit(
                            lambda: _run_recovery_job(existing.id, project, owner_id)
                        )
                    return False
            active_coverage_run_store.supersede(project.id)
            run = active_coverage_run_store.create(
                project_id=project.id,
                owner_id=owner_id,
                mdl_checksum=mdl_checksum,
                docs_checksum=docs_checksum,
            )
            active_job_runner.submit(
                lambda: _run_coverage_job(run.id, project, owner_id)
            )
            return True
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to schedule coverage run.", exc_info=True)
            return False

    def _run_coverage_job(  # noqa: C901 - debounce/claim/progress/persist seams
        run_id: str, project: SemanticProject, owner_id: str
    ) -> None:
        """Background body: debounce → claim → audit → persist + emit event."""

        if (debounce := app_config.wren_coverage_debounce_seconds) > 0:
            time.sleep(debounce)
        # Claim-on-start lease: a newer trigger that superseded this run wins, so
        # only the latest pending run proceeds (cross-worker safe).
        if not active_coverage_run_store.claim(run_id):
            return

        def _superseded() -> bool:
            try:
                return active_coverage_run_store.get(run_id).status == "superseded"
            except CoverageRunNotFoundError:
                return True

        # Live progress (Feature C): persist each stage tick on the run row and
        # emit a non-provenance liveness event only on stage *transitions* (≤4 per
        # run) so the badge re-polls promptly without flooding the event log.
        last_stage: dict[str, str] = {}

        def _on_progress(event: CoverageProgressEvent) -> None:
            try:
                active_coverage_run_store.report_progress(
                    run_id,
                    CoverageProgress(
                        stage=event.stage,
                        detail=event.detail,
                        current=event.current,
                        total=event.total,
                        phase_index=event.phase_index,
                        phase_total=event.phase_total,
                    ),
                )
            except CoverageRunNotFoundError:
                return
            if last_stage.get("stage") == event.stage:
                return
            last_stage["stage"] = event.stage
            try:
                _append_semantic_event(
                    store=active_semantic_layer_store,
                    owner_id=owner_id,
                    event_type="coverage_progress",
                    scope=_scope_from_project(project),
                    document_id=None,
                    message=f"Coverage: {event.detail or event.stage}",
                    project_id=project.id,
                    detail={"run_id": run_id, "stage": event.stage},
                )
            except Exception:  # pylint: disable=broad-except
                logger.warning("Failed to emit coverage progress.", exc_info=True)

        try:
            files = active_mdl_file_store.list(project.id, owner_id=owner_id)
            documents = _coverage_documents(project.id, owner_id)
            instructions = [
                view.instruction
                for view in _project_instruction_views(project, owner_id)
            ]
            report = run_directory_coverage(
                active_model_client,
                documents=documents,
                files=files,
                instructions=instructions,
                embedder=active_embedder,
                votes=app_config.wren_copilot_coverage_votes,
                include_overreach=app_config.wren_coverage_include_overreach,
                should_cancel=_superseded,
                progress_cb=_on_progress,
            )
        except CoverageCancelledError:
            return  # superseded mid-run; the newer run will report
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Coverage run failed.", exc_info=True)
            try:
                active_coverage_run_store.fail(run_id, str(ex))
            except CoverageRunNotFoundError:
                pass
            return

        active_coverage_run_store.complete(run_id, report, score=report.score)
        try:
            _append_semantic_event(
                store=active_semantic_layer_store,
                owner_id=owner_id,
                event_type="coverage_completed",
                scope=_scope_from_project(project),
                document_id=None,
                message=f"Coverage {round(report.score * 100)}%",
                project_id=project.id,
                detail={
                    "run_id": run_id,
                    "score": report.score,
                    "total": report.total,
                    "covered": report.covered,
                    "partial": report.partial,
                    "missing": report.missing,
                    "unsupported": report.unsupported,
                },
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to record coverage provenance.", exc_info=True)

        # Chain the recovery agent when the report has gaps and the feature is on.
        # Separate job (ack-fast-then-process): coverage labels land immediately;
        # the recovery Copilot turn streams its suggestions in afterwards.
        if (
            app_config.wren_copilot_enabled
            and app_config.wren_coverage_recovery_enabled
            and (report.missing + report.partial) > 0
        ):
            try:
                active_coverage_run_store.set_recovery(run_id, status="pending")
                active_job_runner.submit(
                    lambda: _run_recovery_job(run_id, project, owner_id)
                )
            except Exception:  # pylint: disable=broad-except
                logger.warning("Failed to schedule coverage recovery.", exc_info=True)

    def _run_recovery_job(  # noqa: C901 - gate/seed/run/persist/emit seams
        run_id: str, project: SemanticProject, owner_id: str
    ) -> None:
        """Chained Copilot turn that proposes edits to close coverage gaps.

        Reads the completed run's report, seeds a ``kind="recovery"`` conversation
        with the report as a synthetic user message, runs the full Copilot toolset,
        and persists the resulting changeset as the conversation's artifact (the
        reviewable suggestion set). Never auto-applies; emits a non-provenance
        ``recovery_suggestions_ready`` event when there is something to review.
        """

        if not (
            app_config.wren_copilot_enabled
            and app_config.wren_coverage_recovery_enabled
        ):
            return
        try:
            run = active_coverage_run_store.get(run_id)
        except CoverageRunNotFoundError:
            return
        # Only the latest completed run recovers; a superseded run is skipped, and
        # an already-recovered run is idempotent (no duplicate suggestions).
        if run.status != "complete" or run.report is None:
            return
        if run.recovery_conversation_id is not None:
            return
        if (run.report.missing + run.report.partial) == 0:
            active_coverage_run_store.set_recovery(run_id, status="empty")
            return

        active_coverage_run_store.set_recovery(run_id, status="running")
        try:
            files = active_mdl_file_store.list(project.id, owner_id=owner_id)
            instructions = [
                view.instruction
                for view in _project_instruction_views(project, owner_id)
            ]
            user_message = _build_recovery_message(run.report)
            conversation = active_conversation_store.create(
                _scope_from_project(project),
                owner_id=owner_id,
                kind="recovery",
                project_id=project.id,
            )
            turn_service = ConversationTurnService(active_conversation_store)
            turn_service.begin_turn(
                conversation.id,
                user_content=user_message,
                scope=_scope_from_project(project),
                owner_id=owner_id,
            )
            changeset = run_copilot(
                model_client=active_model_client,
                files=files,
                schema_index=_cached_schema_index(project),
                user_message=user_message,
                instructions=instructions,
                history=None,
                max_steps=app_config.wren_copilot_max_steps,
                tool_result_max_chars=app_config.wren_copilot_tool_result_max_chars,
                deep_validate=app_config.wren_modeling_deep_validation,
                autopilot=app_config.wren_copilot_autopilot_enabled,
                document_store=active_semantic_layer_store,
                document_index=active_document_index,
                project_id=project.id,
                owner_id=owner_id,
                retrieve_k=app_config.wren_document_retrieve_k,
                embedder=active_embedder,
            )
            turn_service.commit_turn(
                conversation.id,
                assistant_content=changeset.message or "",
                artifacts=[changeset_to_artifact(changeset)],
                owner_id=owner_id,
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning("Coverage recovery failed.", exc_info=True)
            try:
                active_coverage_run_store.set_recovery(run_id, status="failed")
            except CoverageRunNotFoundError:
                pass
            return

        count = len(changeset.items)
        active_coverage_run_store.set_recovery(
            run_id,
            status="ready" if count > 0 else "empty",
            conversation_id=conversation.id,
        )
        if count > 0:
            try:
                _append_semantic_event(
                    store=active_semantic_layer_store,
                    owner_id=owner_id,
                    event_type="recovery_suggestions_ready",
                    scope=_scope_from_project(project),
                    document_id=None,
                    message=f"{count} coverage suggestion(s) ready to review",
                    project_id=project.id,
                    detail={
                        "run_id": run_id,
                        "recovery_conversation_id": conversation.id,
                        "suggestion_count": count,
                        "mdl_checksum": run.mdl_checksum,
                    },
                )
            except Exception:  # pylint: disable=broad-except
                logger.warning("Failed to emit recovery event.", exc_info=True)

    def _run_coverage_sweep() -> dict[str, int]:  # noqa: C901 - two gated passes
        """One sweep tick: two INDEPENDENT passes over all projects.

        Pass 1 (coverage) schedules an audit for any project whose latest MDL
        version has no completed report — picking up legacy / pre-feature projects
        that never had a write to trigger the event-driven scheduler. It does not
        back-fill recovery.

        Pass 2 (recovery) independently schedules the recovery agent for every
        project whose latest completed report still has gaps and no suggestions.
        It reads existing completed reports directly, so it never depends on the
        coverage pass (or any fresh run) having produced them — that is the
        decoupling guarantee.

        Owner-agnostic: projects are db-access visible (``owner_id`` is audit), and
        each recoverable run carries its own ``owner_id``, so the sweep needs no
        request identity. Best-effort — a per-item failure never aborts the tick.
        Returns per-pass scheduled counts (for logging/tests).
        """

        counts = {"coverage_scheduled": 0, "recovery_scheduled": 0}
        if not app_config.wren_copilot_enabled:
            return counts

        # Pass 1 — coverage (gated by auto-coverage; recovery back-fill suppressed).
        if app_config.wren_coverage_auto_enabled:
            try:
                projects = active_semantic_project_store.list(
                    owner_id=DEFAULT_OWNER_ID
                )
            except Exception:  # pylint: disable=broad-except
                projects = []
                logger.warning(
                    "Coverage sweep: project enumeration failed.", exc_info=True
                )
            for project in projects:
                try:
                    if _schedule_coverage(
                        project, DEFAULT_OWNER_ID, recover_backfill=False
                    ):
                        counts["coverage_scheduled"] += 1
                except Exception:  # pylint: disable=broad-except
                    logger.debug(
                        "Coverage sweep: schedule failed for %s",
                        project.id,
                        exc_info=True,
                    )

        # Pass 2 — recovery (gated by recovery flag; independent of pass 1).
        if app_config.wren_coverage_recovery_enabled:
            try:
                recoverable = active_coverage_run_store.iter_recoverable()
            except Exception:  # pylint: disable=broad-except
                recoverable = []
                logger.warning(
                    "Coverage sweep: recoverable enumeration failed.", exc_info=True
                )
            for run in recoverable:
                try:
                    project = active_semantic_project_store.get(
                        run.project_id, owner_id=run.owner_id
                    )
                except Exception:  # pylint: disable=broad-except
                    logger.debug(
                        "Coverage sweep: project %s gone/not visible — skipped.",
                        run.project_id,
                        exc_info=True,
                    )
                    continue
                try:
                    active_coverage_run_store.set_recovery(run.id, status="pending")
                    # partial (not a closure) binds this iteration's run/project,
                    # avoiding the late-binding loop-variable trap.
                    active_job_runner.submit(
                        functools.partial(
                            _run_recovery_job, run.id, project, run.owner_id
                        )
                    )
                    counts["recovery_scheduled"] += 1
                except Exception:  # pylint: disable=broad-except
                    logger.debug(
                        "Coverage sweep: recovery schedule failed for run %s",
                        run.id,
                        exc_info=True,
                    )
        return counts

    # Exposed on the app for the periodic sweeper thread and for tests to invoke a
    # single tick deterministically (no wall-clock wait).
    api.state.run_coverage_sweep = _run_coverage_sweep

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
                actor_name=identity.username or identity.email,
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

    # Shared across requests (the app factory runs once); keyed per project so an
    # unauthorized caller never reaches a cached entry (access is checked upstream).
    _schema_index_cache: TtlCache[tuple[str, tuple[str, ...]], SchemaIndex] = TtlCache(
        ttl_seconds=app_config.wren_schema_index_cache_ttl_seconds,
    )

    def _cached_schema_index(project: SemanticProject) -> SchemaIndex | None:
        """Warm-cache-only physical schema index for background jobs.

        A background job (coverage recovery) has no request, so it cannot build a
        live schema index. Reuse the cache a recent request warmed; else ``None``
        so MDL validation degrades to structural-only (the apply route re-validates
        against a live schema before anything is persisted).
        """

        return _schema_index_cache.get(
            (project.id, tuple(sorted(project.schema_names)))
        )

    def _schema_index_for_project(  # noqa: C901
        project: SemanticProject,
        fastapi_request: Request,
    ) -> SchemaIndex | None:
        """Physical schema index for activation/generation checks.

        On a successful live fetch the schema is snapshotted for the project; on
        a Superset outage the last snapshot is used so physical validation keeps
        catching hallucinated columns instead of degrading to structural-only.
        Returns ``None`` only when neither a live fetch nor a snapshot exists.

        A short TTL cache (``wren_schema_index_cache_ttl_seconds``) fronts the live
        fetch: Copilot/MDL operations (validate-on-edit, deploy preview, copilot
        turns) call this repeatedly, and each live build is a Superset dataset list
        + a per-dataset N+1 per project schema. Caching by project id is safe — the
        caller has already authorized the project for this request, and the index
        is project-scoped. Keyed on the schema set so a multi-schema change is a
        miss; only successful live builds are cached (never the outage fallback).
        """

        if project.default_database_id is None:
            return None
        cache_key = (project.id, tuple(sorted(project.schema_names)))
        if (cached_index := _schema_index_cache.get(cache_key)) is not None:
            return cached_index
        try:
            request_context_provider, _ = build_superset_runtime(fastapi_request)
            # CR3: ground modeling/validation on the *complete* scope schema, not a
            # relevance-ranked top-k against a placeholder question (which can silently
            # drop the tables a document is about). Fall back to the ranked path only
            # for providers that do not implement full-schema introspection.
            fetch_full = getattr(request_context_provider, "get_full_schema", None)
            fetch = fetch_full or request_context_provider.get_context

            def _fetch(schema_name: str) -> Any:
                return fetch(
                    AgentQueryRequest(
                        question="semantic layer validation",
                        database_id=project.default_database_id,
                        catalog_name=project.catalog_name,
                        schema_name=schema_name,
                    )
                )

            # Union every member schema so the index knows the project's FULL scope
            # (mirrors ``_onboarding_context``). Without this, a multi-schema project
            # validates/generates against only its primary schema — so the R1
            # invariant wrongly rejects, and the Copilot is blind to, tables in the
            # project's secondary schemas even though their access is proven.
            context = _fetch(project.schema_name)
            seen = {dataset.id for dataset in context.datasets}
            for schema_name in project.schema_names:
                if schema_name == project.schema_name:
                    continue
                for dataset in _fetch(schema_name).datasets:
                    if dataset.id not in seen:
                        seen.add(dataset.id)
                        context.datasets.append(dataset)
        except Exception:  # pylint: disable=broad-except
            snapshot = active_schema_snapshot_store.get(project.id)
            if snapshot is None:
                return None
            return SchemaIndex.from_snapshot(
                snapshot.tables,
                tables_by_schema=snapshot.tables_by_schema or None,
            )
        index = SchemaIndex.from_agent_context(context)
        try:
            active_schema_snapshot_store.upsert(
                SchemaSnapshot(
                    project_id=project.id,
                    database_uri_fingerprint=project.database_uri_fingerprint,
                    catalog_name=project.catalog_name,
                    schema_name=project.schema_name,
                    tables=index.to_tables(),
                    # F3: persist the schema-qualified map too, so a multi-schema
                    # project's outage fallback stays schema-aware.
                    tables_by_schema=index.to_tables_by_schema(),
                )
            )
        except Exception:  # noqa: S110  # pylint: disable=broad-except
            # Snapshotting is best-effort; never block validation on it.
            pass
        _schema_index_cache.set(cache_key, index)
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

    def _enforce_activation_manifest(
        *,
        project: SemanticProject,
        fastapi_request: Request,
        contents: list[str],
    ) -> SchemaIndex | None:
        """Validate the *projected* active manifest and block if it is invalid.

        ``contents`` is the full set of file contents that will be active once the
        activation completes (already-active siblings plus the file(s) being
        activated) — validated as one manifest so cross-file references (a
        metric's ``baseObject``, a relationship's models) resolve. This is the
        atomic invariant: a single-file activation passes its active siblings +
        the new file; a bulk activation passes the whole projected active set,
        so dependency *order* among the activated files never matters.

        Returns the resolved physical ``SchemaIndex`` (or ``None`` when the
        project has no live/snapshot schema and live validation is not required)
        so callers can reuse it for the subsequent per-file validation instead of
        re-fetching the live schema a second time within the same request.
        """

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
            contents,
            schema_index=schema_index,
            deep_validate=app_config.wren_core_validation_enabled or require_engine,
            # W4: an enrichment that re-emits an existing model supersedes the
            # older copy instead of failing as a duplicate_model. The file(s)
            # being activated come last, so they win.
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
        return schema_index

    def _offending_activation_targets(
        *,
        project: SemanticProject,
        fastapi_request: Request,
        active_staying: list[str],
        targets: list[MdlFile],
    ) -> list[str]:
        """Name the target file(s) that make a bulk activation fail (leave-one-out).

        Runs only when the projected manifest is invalid. With one target, that
        target is the culprit. With several, validate the projected set with each
        target removed: if the remainder becomes valid, that target is the isolated
        culprit. Returns the offending file paths; empty when no single file is
        implicated (a genuine cross-file error) so the caller keeps the
        manifest-level message. Attribution only — nothing is dropped here (R3:
        surface the bad view so the reviewer can reject just that one).
        """

        if not targets:
            return []
        if len(targets) == 1:
            return [targets[0].path]
        schema_index = _schema_index_for_project(project, fastapi_request)
        deep = (
            app_config.wren_core_validation_enabled
            or app_config.wren_activation_requires_engine
        )
        contents_by_id = {target.id: target.content for target in targets}
        offending: list[str] = []
        for target in targets:
            remainder = active_staying + [
                content for tid, content in contents_by_id.items() if tid != target.id
            ]
            result = validate_project_manifest(
                remainder,
                schema_index=schema_index,
                deep_validate=deep,
                dedup_models=True,
            )
            if result.valid:
                offending.append(target.path)
        return offending

    def _enforce_activation(
        *,
        project: SemanticProject,
        fastapi_request: Request,
        owner_id: str,
        file_id: str,
        new_content: str,
    ) -> SchemaIndex | None:
        """Block activation when the resulting project manifest is invalid.

        Returns the resolved physical ``SchemaIndex`` so the caller can reuse it
        for the file's own validation (one live schema fetch per request).
        """

        siblings = [
            file.content
            for file in active_mdl_file_store.list(project.id, owner_id=owner_id)
            if file.id != file_id and file.status == "active"
        ]
        return _enforce_activation_manifest(
            project=project,
            fastapi_request=fastapi_request,
            contents=[*siblings, new_content],
        )

    def _resolve_bulk_targets(
        files: list[MdlFile],
        *,
        status: str,
        file_ids: list[str] | None,
    ) -> list[MdlFile]:
        """The files that must change to reach ``status`` (already-there skipped)."""

        if file_ids is None:
            return [file for file in files if file.status != status]
        by_id = {file.id: file for file in files}
        targets: list[MdlFile] = []
        for file_id in file_ids:
            file = by_id.get(file_id)
            if file is None:
                raise HTTPException(status_code=404, detail="MDL file not found.")
            if file.status != status:
                targets.append(file)
        return targets

    def _apply_bulk_status(
        *,
        project: SemanticProject,
        identity: AgentIdentity,
        targets: list[MdlFile],
        status: MdlFileStatus,
        schema_index: SchemaIndex | None,
    ) -> list[MdlFile]:
        """Flip each target to ``status`` and record provenance; returns the updates."""

        activating = status == "active"
        changed: list[MdlFile] = []
        for file in targets:
            file_validation = (
                validate_mdl(file.content, schema_index=schema_index)
                if activating
                else None
            )
            updated = active_mdl_file_store.update(
                file.id,
                MdlFileUpdateRequest(status=status),
                owner_id=identity.owner_id,
                validation=file_validation,
            )
            changed.append(updated)
            _emit_mdl_provenance(
                project=project,
                owner_id=identity.owner_id,
                event_type="mdl_activated" if activating else "mdl_updated",
                file=updated,
                message=(
                    f"Activated {updated.path}"
                    if activating
                    else f"Deactivated {updated.path}"
                ),
                status_from=file.status,
                actor_name=identity.username or identity.email,
            )
        return changed

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/mdl-files/bulk-status",
        response_model=MdlBulkStatusResult,
    )
    def set_mdl_files_status(
        project_id: str,
        request: MdlBulkStatusRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlBulkStatusResult:
        """Activate or deactivate many MDL files in one atomic operation.

        Activation validates the *whole projected active manifest* once, so files
        whose validity depends on each other — a metric and the model its
        ``baseObject`` references, a relationship and its endpoint models — can be
        activated together without being ordered by hand. All-or-nothing: an
        invalid projected manifest 422s and nothing changes. This replaces the
        per-file activation loop, which validated each file against only the
        already-active subset and so failed when a dependent was toggled first.
        """

        if request.status == "deleted":
            raise HTTPException(
                status_code=400,
                detail="Bulk status change supports 'active' or 'draft' only.",
            )
        project = authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="write",
        )
        files = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
        targets = _resolve_bulk_targets(
            files, status=request.status, file_ids=request.file_ids
        )
        if not targets:
            return MdlBulkStatusResult(files=files, changed_count=0)

        # Resolve the physical schema once per request. Activation enforces the
        # projected manifest and hands back the index it used, which the per-file
        # validation below reuses — one live schema fetch, not two. Deactivation
        # neither enforces nor re-validates files, so it needs no schema fetch at
        # all (the old code fetched one and threw it away).
        schema_index: SchemaIndex | None = None
        if request.status == "active":
            target_ids = {file.id for file in targets}
            # Projected active set = files staying active + the files being
            # activated. Validate it as one manifest so dependency order among the
            # targets never matters (the activated files come last → W4 last-wins).
            active_staying = [
                file.content
                for file in files
                if file.status == "active" and file.id not in target_ids
            ]
            projected = active_staying + [
                file.content for file in targets if file.id in target_ids
            ]
            try:
                schema_index = _enforce_activation_manifest(
                    project=project,
                    fastapi_request=fastapi_request,
                    contents=projected,
                )
            except HTTPException as exc:
                # R3: name the offending view file(s) so the reviewer can reject
                # just the bad one instead of losing the whole changeset. The
                # atomic invariant is preserved — nothing auto-activates.
                if exc.status_code == 422 and isinstance(exc.detail, dict):
                    offending = _offending_activation_targets(
                        project=project,
                        fastapi_request=fastapi_request,
                        active_staying=active_staying,
                        targets=targets,
                    )
                    if offending:
                        exc.detail["offending_files"] = offending
                raise

        try:
            changed = _apply_bulk_status(
                project=project,
                identity=identity,
                targets=targets,
                status=request.status,
                schema_index=schema_index,
            )
        except MdlFileNotFoundError as ex:
            raise HTTPException(status_code=404, detail="MDL file not found.") from ex
        except MdlFileValidationError as ex:
            raise HTTPException(status_code=422, detail=str(ex)) from ex
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

        # The active MDL directory changed → reindex once and re-audit coverage
        # once for the whole batch (the per-file loop did both N times).
        reindex_project_mdl(
            retriever=active_retriever,
            project_id=project_id,
            owner_id=identity.owner_id,
            mdl_file_store=active_mdl_file_store,
        )
        _schedule_coverage(project, identity.owner_id)

        refreshed = active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
        return MdlBulkStatusResult(files=refreshed, changed_count=len(changed))

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
            # Resolve the physical schema at most once per request: activation
            # enforcement returns the index it used, which the file's own
            # validation reuses instead of re-fetching the live schema.
            enforced_index: SchemaIndex | None = None
            enforced = False
            if request.status == "active":
                enforced_index = _enforce_activation(
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
                enforced = True
            if request.content is not None:
                schema_index = (
                    enforced_index
                    if enforced
                    else _schema_index_for_project(project, fastapi_request)
                )
                file_validation = validate_mdl(
                    request.content, schema_index=schema_index
                )
            else:
                file_validation = None
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
                    actor_name=identity.username or identity.email,
                )
            else:
                _emit_mdl_provenance(
                    project=project,
                    owner_id=identity.owner_id,
                    event_type="mdl_updated",
                    file=updated,
                    message=f"Edited {updated.path}",
                    actor_name=identity.username or identity.email,
                )
            # The active MDL directory changed (activation, or an edit to a live
            # file) → (re)run directory coverage on the latest version.
            if updated.status == "active":
                _schedule_coverage(project, identity.owner_id)
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
                actor_name=identity.username or identity.email,
            )
            # Deleting a live file changes the active directory → re-audit coverage.
            if existing.status == "active":
                _schedule_coverage(project, identity.owner_id)
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
        # F4: the Copilot is available even on an empty project, so it can *drive*
        # onboarding (propose models from a BI doc, human-in-the-loop). Readiness is
        # advisory, not a gate — the only remaining hard block is an in-flight
        # onboarding *job* (``indexing``): editing the MDL directory while the job is
        # still writing files would race it. ``empty``/``ready``/``failed`` all pass.
        readiness = _project_readiness(project, owner_id)
        if readiness.status == "indexing":
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
        """Audit a single document for information lost in markdown → MDL.

        DEPRECATED (Feature B): directory-level coverage now runs automatically in
        the background and is surfaced in the provenance dialog. This synchronous,
        per-document route is retained one release for an on-demand drill-down and
        will be removed once the badge + provenance surface fully replaces it.
        """

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

    # -- Background directory coverage: read + manual refresh (Feature B) ---

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/coverage/latest",
        response_model=CoverageRun | None,
    )
    def get_latest_coverage(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> CoverageRun | None:
        """The most recent completed directory coverage run (score + report)."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        return active_coverage_run_store.latest_complete(project_id)

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/coverage/runs/{run_id}",
        response_model=CoverageRun,
    )
    def get_coverage_run(
        project_id: str,
        run_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> CoverageRun:
        """Fetch one stored coverage run (the provenance dialog's drill-in)."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        try:
            run = active_coverage_run_store.get(run_id)
        except CoverageRunNotFoundError as ex:
            raise HTTPException(
                status_code=404, detail="Coverage run not found."
            ) from ex
        if run.project_id != project_id:
            raise HTTPException(status_code=404, detail="Coverage run not found.")
        return run

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/coverage/status",
    )
    def get_coverage_status(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, Any]:
        """Live coverage state for the editor badge (analysing / stale / ready)."""

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        latest = active_coverage_run_store.latest_complete(project_id)
        active = active_coverage_run_store.active_run(project_id)
        current_checksum = _active_mdl_checksum(project_id, identity.owner_id)
        running = active is not None
        stale = latest is not None and latest.mdl_checksum != current_checksum
        if running:
            status = "analysing"
        elif latest is None:
            status = "none"
        elif stale:
            status = "stale"
        else:
            status = "ready"
        return {
            "status": status,
            "running": running,
            "stale": stale,
            "score": latest.score if latest is not None else None,
            "run_id": latest.id if latest is not None else None,
            # Live, coarse stage progress while a run is in flight (Feature C).
            "progress": (
                active.progress.model_dump(mode="json")
                if active is not None and active.progress is not None
                else None
            ),
            # Recovery agent (latest run): drives the "suggestions ready"
            # notification. ``recovery_dismissed`` is the durable per-run dismissal.
            "recovery_status": (
                latest.recovery_status if latest is not None else "none"
            ),
            "recovery_run_id": latest.id if latest is not None else None,
            "recovery_dismissed": (
                latest.recovery_dismissed_at is not None
                if latest is not None
                else False
            ),
        }

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/coverage/refresh",
    )
    def refresh_coverage(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
        force: bool = True,
    ) -> dict[str, bool]:
        """Manually (re)schedule a directory coverage run on the current MDL.

        Defaults to ``force=True``: the only caller is the explicit "Re-run
        analysis" action, which must recompute even when the active MDL version
        was already scored. Pass ``?force=false`` for idempotent (re)scheduling
        that reuses an existing score for an unchanged version.
        """

        _require_copilot_enabled()
        project = authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        _schedule_coverage(project, identity.owner_id, force=force)
        return {"scheduled": True}

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/coverage/scores-by-version",
    )
    def get_coverage_scores_by_version(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, dict[str, Any]]:
        """Latest coverage score per MDL version, keyed by ``mdl_checksum``.

        The provenance dialog joins this against each entry's
        ``detail.mdl_checksum`` to render a coverage label (and before/after
        delta) per version — a read-only overlay, not a timeline entry (Feature B).
        """

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="read"
        )
        runs = active_coverage_run_store.scores_by_checksum(project_id)
        return {
            checksum: {
                "score": run.score,
                "run_id": run.id,
                "status": run.status,
                "computed_at": run.updated_at.isoformat(),
                "docs_checksum": run.docs_checksum,
            }
            for checksum, run in runs.items()
        }

    def _require_coverage_run(
        project_id: str, run_id: str, *, fastapi_request: Request, identity, permission
    ) -> CoverageRun:
        authorize_semantic_project(
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission=permission,
        )
        try:
            run = active_coverage_run_store.get(run_id)
        except CoverageRunNotFoundError as ex:
            raise HTTPException(404, "Coverage run not found.") from ex
        if run.project_id != project_id:
            raise HTTPException(404, "Coverage run not found.")
        return run

    @api.get(
        "/agent/semantic-layer/projects/{project_id}/coverage/runs/{run_id}/recovery",
    )
    def get_coverage_recovery(
        project_id: str,
        run_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, Any]:
        """Recovery suggestions (a reviewable changeset) for a coverage run.

        Reads the changeset back from the recovery conversation's artifact —
        server-authoritative, the same source the apply/provenance path trusts.
        ``stale`` flags that the active MDL moved on since this run was audited.
        """

        _require_copilot_enabled()
        run = _require_coverage_run(
            project_id,
            run_id,
            fastapi_request=fastapi_request,
            identity=identity,
            permission="read",
        )
        changeset = None
        if run.recovery_conversation_id:
            try:
                conversation = active_conversation_store.get(
                    run.recovery_conversation_id, owner_id=identity.owner_id
                )
                changeset = changeset_from_conversation(conversation)
            except Exception:  # pylint: disable=broad-except
                changeset = None
        current_checksum = _active_mdl_checksum(project_id, identity.owner_id)
        return {
            "run_id": run.id,
            "status": run.recovery_status,
            "conversation_id": run.recovery_conversation_id,
            "suggestion_count": len(changeset.items) if changeset else 0,
            "changeset": changeset.model_dump(mode="json") if changeset else None,
            "dismissed": run.recovery_dismissed_at is not None,
            "stale": run.mdl_checksum != current_checksum,
        }

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/coverage/runs/{run_id}"
        "/recovery/dismiss",
    )
    def dismiss_coverage_recovery(
        project_id: str,
        run_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> dict[str, bool]:
        """Durably dismiss the 'recovery suggestions ready' notification (per run)."""

        _require_copilot_enabled()
        _require_coverage_run(
            project_id,
            run_id,
            fastapi_request=fastapi_request,
            identity=identity,
            permission="write",
        )
        active_coverage_run_store.dismiss_recovery(run_id)
        return {"dismissed": True}

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
        project_id: str,
        conversation_id: str,
        owner_id: str,
        *,
        kinds: tuple[str, ...] = ("copilot",),
    ) -> Conversation:
        """Load a thread and assert it belongs to this project (else 404).

        ``kinds`` whitelists the acceptable conversation kinds. The interactive
        Copilot turn only accepts ``copilot`` threads; the apply route also accepts
        ``recovery`` threads so a user can apply the coverage recovery agent's
        suggestions (the recovery changeset is persisted on a ``recovery`` thread,
        not a ``copilot`` one).
        """

        try:
            conversation = active_conversation_store.get(
                conversation_id, owner_id=owner_id
            )
        except ConversationNotFoundError as ex:
            raise HTTPException(
                status_code=404, detail="Conversation not found."
            ) from ex
        if conversation.kind not in kinds or conversation.project_id != project_id:
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

        # Guard the whole preflight (authorization, readiness, and the
        # store/schema/thread reads) so any setup failure -- including one from the
        # write-permission check the SQL agent never exercises -- surfaces as a 502
        # with a diagnosable message instead of a bare 500. Expected typed failures
        # (403/404/409) pass through unchanged. ``conversation_id`` absent → the
        # thread turn is a stateless one-shot (backward compatible).
        try:
            _require_copilot_enabled()
            project = authorize_semantic_project(
                fastapi_request,
                project_id,
                owner_id=identity.owner_id,
                permission="write",
            )
            _require_project_ready(project, identity.owner_id)
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
                max_steps=request.max_steps or app_config.wren_copilot_max_steps,
                tool_result_max_chars=app_config.wren_copilot_tool_result_max_chars,
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
        changeset.referenced_attachments = [
            attachment.filename for attachment in request.attachments
        ]
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

        # Resolve the whole preflight (authorization, readiness, and the
        # request-scoped context the worker thread cannot fetch) before streaming
        # starts. A StreamingResponse commits a 200 status the moment its body
        # iterator is entered, so any failure here must surface as a normal HTTP
        # error *before* streaming begins. Without this guard an unhandled preflight
        # error -- including one from the write-permission check, which the SQL
        # agent never exercises -- collapses into a bare 500 ("Internal Server
        # Error", 21 bytes) with no logged traceback, unlike worker-loop errors
        # (streamed back as ``error`` events). Expected typed failures (403/404/409)
        # pass through unchanged; anything else becomes a diagnosable 502.
        try:
            _require_copilot_enabled()
            project = authorize_semantic_project(
                fastapi_request,
                project_id,
                owner_id=identity.owner_id,
                permission="write",
            )
            _require_project_ready(project, identity.owner_id)
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
                        max_steps=(
                            request.max_steps or app_config.wren_copilot_max_steps
                        ),
                        tool_result_max_chars=(
                            app_config.wren_copilot_tool_result_max_chars
                        ),
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
                changeset.referenced_attachments = [
                    attachment.filename for attachment in request.attachments
                ]
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
        project = authorize_semantic_project(
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

        # Record the agent's edit in the MDL provenance timeline (best-effort).
        _emit_agent_apply_provenance(
            project=project,
            owner_id=identity.owner_id,
            actor_name=identity.username or identity.email,
            items=request.items,
            conversation_id=request.conversation_id,
        )

        # Record the apply as an assistant turn so a resumed thread shows that the
        # proposal was applied (parity with the SQL agent's execute-sql turn). The
        # recovery agent persists its changeset on a ``recovery`` thread, so the
        # apply route accepts that kind too (not just interactive ``copilot``).
        if request.conversation_id:
            _require_copilot_conversation(
                project_id,
                request.conversation_id,
                identity.owner_id,
                kinds=("copilot", "recovery"),
            )
            count = len(applied)
            noun = "draft" if count == 1 else "drafts"
            _copilot_turn_service().commit_turn(
                request.conversation_id,
                assistant_content=f"Applied {count} {noun}.",
                owner_id=identity.owner_id,
            )

        # An apply that touched active files (update/delete of an active file, or a
        # create the user later activates) moves the active-set version, which
        # invalidates the current coverage score. Re-schedule coverage so the badge
        # re-analyses instead of silently going stale. Idempotent: a no-op when the
        # active checksum is unchanged (e.g. only drafts were created).
        _schedule_coverage(project, identity.owner_id)
        return applied

    @api.post(
        "/agent/semantic-layer/projects/{project_id}/golden-queries/promote",
        response_model=MdlFile,
    )
    def promote_golden_query(
        project_id: str,
        request: GoldenQueryPromoteRequest,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> MdlFile:
        """Promote an NL->SQL pair into the project's ``queries.json`` golden set.

        A copy, never a move — the source runtime-memory pair is left untouched
        (it stays in the shared, database-scoped pool). Idempotent on the question.
        Creating a new ``queries.json`` lands a draft; updating an existing file
        preserves its status (consistent with MDL file edits).
        """

        _require_copilot_enabled()
        authorize_semantic_project(
            fastapi_request, project_id, owner_id=identity.owner_id, permission="write"
        )
        semantic_sql = (request.semantic_sql or request.native_sql or "").strip()
        if not request.question.strip() or not semantic_sql:
            raise HTTPException(
                status_code=400,
                detail="Promote requires a question and semantic_sql (or native_sql).",
            )
        entry = GoldenQuery(
            name=request.name or request.question,
            question=request.question,
            semantic_sql=semantic_sql,
            verified_by=identity.username or identity.email or identity.owner_id,
            verified_at=int(time.time()),
            use_as_onboarding=request.use_as_onboarding,
            usage_guidance=request.usage_guidance,
        )
        existing = find_golden_queries_file(
            active_mdl_file_store.list(project_id, owner_id=identity.owner_id)
        )
        try:
            content = upsert_golden_query(
                existing.content if existing is not None else None, entry
            )
        except (ValueError, TypeError) as ex:
            raise HTTPException(
                status_code=400, detail=f"Existing queries.json is invalid: {ex}"
            ) from ex
        if existing is None:
            return active_mdl_file_store.create(
                project_id,
                MdlFileCreateRequest(path=GOLDEN_QUERIES_PATH, content=content),
                owner_id=identity.owner_id,
            )
        return active_mdl_file_store.update(
            existing.id,
            MdlFileUpdateRequest(content=content),
            owner_id=identity.owner_id,
        )

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

            def _fetch(schema_name: str | None) -> Any:
                return fetch(
                    AgentQueryRequest(
                        question="semantic layer onboarding",
                        database_id=project.default_database_id,
                        catalog_name=project.catalog_name,
                        schema_name=schema_name,
                        dataset_ids=dataset_ids or [],
                    )
                )

            if dataset_ids:
                # An explicit selection is keyed by GLOBALLY-UNIQUE dataset ids, so
                # fetch by ids ALONE (``schema_name=None``). Passing a single schema
                # here makes the provider intersect schema AND ids and silently drop
                # the datasets that live in the project's *other* schemas — so a
                # cross-schema selection would onboard only the primary schema. The
                # boundary guard then enforces the project's proven schema set.
                context = _fetch(None)
                _enforce_onboarding_schema_boundary(context, project, dataset_ids)
            else:
                # Whole-project introspection: union every member schema so onboarding
                # seeds base models across the project's full schema set. Each model's
                # tableReference still carries its own (schema, table).
                context = _fetch(project.schema_name)
                seen = {dataset.id for dataset in context.datasets}
                for schema_name in project.schema_names:
                    if schema_name == project.schema_name:
                        continue
                    for dataset in _fetch(schema_name).datasets:
                        if dataset.id not in seen:
                            seen.add(dataset.id)
                            context.datasets.append(dataset)
            return context, dataset_ids
        except SupersetAuthError as ex:
            raise HTTPException(status_code=ex.status_code, detail=str(ex)) from ex

    def _start_onboarding_job(
        project: SemanticProject,
        context: Any,
        owner_id: str,
        dataset_ids: list[int] | None = None,
        actor_name: str | None = None,
    ) -> SemanticJob:
        """Create + submit the onboarding job.

        Onboarding auto-activates valid base models; this also re-indexes retrieval
        so the freshly active layer is searchable immediately (E6 deploy→reindex).
        ``dataset_ids`` (the selected subset, or ``None`` for the whole schema) is
        recorded on the provenance entry (Feature B). ``actor_name`` (the
        onboarder's display name) is stamped so a shared project attributes the
        onboarding to a person, not a bare id (DP10).
        """

        mode = "selected" if dataset_ids is not None else "all"
        job = active_job_store.create(kind="onboarding", project_id=project.id)
        scope = _scope_from_project(project)
        _append_semantic_event(
            store=active_semantic_layer_store,
            owner_id=owner_id,
            actor_name=actor_name,
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
                    actor_name=actor_name,
                    event_type="onboarding_failed",
                    scope=scope,
                    document_id=None,
                    message=f"Onboarding failed: {ex}",
                    project_id=project.id,
                    detail={"actor": owner_id, "mode": mode},
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
                actor_name=actor_name,
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
                    # Active-set version produced by onboarding — the coverage
                    # overlay's join key (Feature B).
                    "mdl_checksum": _active_mdl_checksum(project.id, owner_id),
                },
            )
            # Onboarding activated the base layer → audit coverage on it.
            _schedule_coverage(project, owner_id)

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
            project,
            context,
            identity.owner_id,
            dataset_ids=dataset_ids,
            actor_name=identity.username or identity.email,
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
        # Cancel any in-flight coverage run so it cannot complete against MDL that
        # no longer exists (the active set is now empty).
        active_coverage_run_store.supersede(project_id)
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
            # F4: for a cross-schema project, also pass the schema-qualified view so
            # the enrichment grounds + validates per-schema (single-schema → None).
            schema_by_schema=(
                schema_index.schema_qualified_view()
                if schema_index is not None and schema_index.is_multi_schema()
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

    @api.get(
        "/agent/semantic-layer/mode-status",
        response_model=SemanticModeStatus,
    )
    def get_semantic_mode_status(
        fastapi_request: Request,
        database_id: int,
        catalog_name: str | None = None,
        schema_name: str | None = None,
        project_id: str | None = None,
        identity: AgentIdentity = identity_dependency,
    ) -> SemanticModeStatus:
        """Whether the AI SQL agent will apply semantic rewrite in this scope.

        Drives the semantic-mode badge. Computes the full precondition set from
        server truth (the same factory + dialect map a query would see) so the
        badge can never show a false-positive — notably on an unsupported dialect
        where the narrow authoring-guidance flag is still ``True``. Read-only.
        """

        scope = _scope_from_query(database_id, catalog_name, schema_name, None)
        authorize_semantic_scope(
            fastapi_request,
            scope,
            identity=identity,
            permission=SemanticPermission.READ,
        )

        # Factor 7 (active models): authoritative only for a pinned project. With
        # no project pinned the agent has no project-scoped models to ground on,
        # so this reads False — surfaced as "select/onboard a project".
        has_active_models = False
        if project_id:
            project = authorize_semantic_project(
                fastapi_request,
                project_id,
                owner_id=identity.owner_id,
                permission="read",
            )
            files = active_mdl_file_store.list(project.id, owner_id=identity.owner_id)
            has_active_models = any(f.status == "active" for f in files)

        # Factor 4 (dialect): resolve the database backend through the governed
        # per-request client. Best-effort — a lookup failure degrades to "unknown"
        # backend, which reads as an unsupported dialect rather than erroring.
        backend: str | None = None
        try:
            _, request_superset_client = build_superset_runtime(fastapi_request)
            backend = request_superset_client.get_database_dialect(database_id)
        except Exception:  # pylint: disable=broad-except
            logger.debug("mode-status backend lookup failed", exc_info=True)

        return evaluate_semantic_factors(
            config=app_config,
            engine=app_semantic_engine,
            backend=backend,
            schema_selected=bool(schema_name),
            project_selected=bool(project_id),
            has_active_models=has_active_models,
            context_loaded=None,
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
    async def get_project_semantic_layer_events(
        project_id: str,
        fastapi_request: Request,
        identity: AgentIdentity = identity_dependency,
    ) -> StreamingResponse:
        """Stream a project's semantic-layer events as a durable SSE channel.

        Authorizes once, replays the stored backlog, then tails new events on a
        short interval with heartbeats. A previous version emitted the backlog
        through a *finite* generator and returned immediately; the browser
        ``EventSource`` treats every close as a drop and reconnects, so the
        connection looped — and because each request re-authorizes via a live
        Superset schema introspection (a dataset list + per-dataset N+1 per
        schema), that loop stormed Superset. Holding the connection open removes
        the reconnect amplifier; the authorization cache bounds the per-request
        cost.

        Async by design: the wait between ticks yields to the event loop rather
        than parking a worker thread, so an always-open editor panel does not
        consume a thread for its lifetime (which would starve the sync endpoints
        that share the pool). The blocking store read is offloaded per tick, and
        the connection is recycled after a bounded lifetime (the client
        reconnects once, cheaply).
        """

        # Offload the synchronous (Superset-touching) authorization so a cold
        # cache does not block the event loop.
        await run_in_threadpool(
            authorize_semantic_project,
            fastapi_request,
            project_id,
            owner_id=identity.owner_id,
            permission="read",
        )

        async def event_stream() -> Any:
            # If the connection ever does drop, tell EventSource to back off so a
            # transient close never becomes a hot reconnect loop.
            yield f"retry: {SEMANTIC_EVENTS_RETRY_MS}\n\n"
            seen: set[str] = set()
            deadline = time.monotonic() + SEMANTIC_EVENTS_MAX_STREAM_SECONDS
            while True:
                try:
                    events = await run_in_threadpool(
                        active_semantic_layer_store.list_project_events,
                        project_id,
                        owner_id=identity.owner_id,
                    )
                except Exception:  # pylint: disable=broad-except
                    # Best-effort tail — a transient store error should not kill
                    # the stream; retry on the next tick.
                    events = []
                for event in events:
                    if event.id not in seen:
                        seen.add(event.id)
                        yield to_sse(event)
                if time.monotonic() >= deadline:
                    return
                # A comment frame keeps the connection (and any buffering proxy)
                # alive; awaiting the sleep lets the server notice a client
                # disconnect promptly, so closing the editor tears the stream down.
                yield ": keep-alive\n\n"
                await asyncio.sleep(SEMANTIC_EVENTS_POLL_INTERVAL_SECONDS)

        return StreamingResponse(
            event_stream(),
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
        # DP10: stamp self-vs-teammate attribution here, where the requesting
        # identity is known. The frontend renders "You" only when ``is_self``.
        entries = [
            entry.model_copy(update={"is_self": entry.actor == identity.owner_id})
            for entry in entries
        ]
        entries.sort(key=lambda entry: entry.created_at, reverse=True)
        # Collapse contiguous user-edit runs *before* capping so the cap bounds
        # displayed rows, not raw events. (Coalescing now only merges same-actor
        # runs, so a shared project keeps distinct users' edits separate.)
        entries = coalesce_user_runs(entries)
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

    # Autonomous coverage + recovery sweep. A daemon thread runs one tick shortly
    # after startup (so existing/legacy projects get picked up without a write)
    # and then every ``wren_coverage_sweep_interval_seconds``. 0 (default) leaves
    # the system purely event-driven. Off in tests (interval 0); tests invoke
    # ``api.state.run_coverage_sweep()`` directly for a deterministic single tick.
    sweep_interval = app_config.wren_coverage_sweep_interval_seconds
    if app_config.wren_copilot_enabled and sweep_interval > 0:

        def _sweep_loop() -> None:
            # Brief settle so startup/migrations finish before the first tick.
            time.sleep(min(15.0, sweep_interval))
            while True:
                try:
                    _run_coverage_sweep()
                except Exception:  # pylint: disable=broad-except
                    logger.warning("Coverage sweep tick failed.", exc_info=True)
                time.sleep(sweep_interval)

        threading.Thread(
            target=_sweep_loop, name="coverage-sweep", daemon=True
        ).start()

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


def _create_llm_usage_store(
    *,
    session_factory: Any | None = None,
) -> LlmUsageStore:
    # Durable when an agent DB is configured; process-local (non-durable) otherwise.
    # Keyed on the session factory rather than a store-mode knob so metering works
    # in every persistence mode without an extra config switch.
    if session_factory is None:
        return InMemoryLlmUsageStore()
    return SqlAlchemyLlmUsageStore(session_factory)


def _create_coverage_run_store(
    config: AgentConfig,
    *,
    session_factory: Any | None = None,
) -> CoverageRunStore:
    if config.semantic_layer_store == "memory":
        return InMemoryCoverageRunStore()
    if config.semantic_layer_store == "sqlalchemy":
        if session_factory is None:
            raise ValueError("SQLAlchemy coverage-run store requires a database.")
        return SqlAlchemyCoverageRunStore(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_SEMANTIC_LAYER_STORE value "
        f"{config.semantic_layer_store!r}. Expected one of: memory, sqlalchemy."
    )


def _create_document_storage(
    config: AgentConfig,
    session_factory: Any | None = None,
) -> DocumentStorage:
    if config.document_storage == "local":
        return LocalDocumentStorage(config.agent_storage_dir)
    if config.document_storage == "s3":
        return S3DocumentStorage(
            bucket=config.document_s3_bucket or "",
            prefix=config.document_s3_prefix,
            endpoint_url=config.document_s3_endpoint_url,
            region_name=config.document_s3_region_name,
        )
    if config.document_storage == "postgres":
        if session_factory is None:
            raise ValueError(
                "AI_AGENT_DOCUMENT_STORAGE=postgres requires the agent "
                "database (set AI_AGENT_DATABASE_URL and a sqlalchemy-backed "
                "store so a session factory exists)."
            )
        return PostgresDocumentStorage(session_factory)
    raise ValueError(
        "Unsupported AI_AGENT_DOCUMENT_STORAGE value "
        f"{config.document_storage!r}. Expected one of: local, s3, postgres."
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


def _build_recovery_message(report: CoverageReport) -> str:
    """Serialize a coverage report's gaps into the recovery agent's user message.

    Lists the missing/partial claims (with the judge's remediation hint and source
    document) and instructs the agent to propose minimal, source-grounded MDL edits
    that close them — removals allowed when justified. Covered findings are omitted.
    """

    gaps = [f for f in report.findings if f.status in ("missing", "partial")]
    lines = [
        "A coverage audit compared the project's source documents against the "
        "active MDL and found gaps. Propose the minimal set of MDL edits that "
        "capture the claims below that the model fails to represent. Only add or "
        "change semantics the documents support. You may remove or rewrite MDL "
        "that the documents contradict or that is redundant, but justify every "
        "removal. Do not invent data. Cite the claim each edit closes.",
        "",
        (
            f"Coverage: {round(report.score * 100)}% "
            f"({report.covered} covered, {report.partial} partial, "
            f"{report.missing} missing)."
        ),
        "",
        "Gaps to close:",
    ]
    for index, finding in enumerate(gaps, start=1):
        source = (
            f" [from {finding.document_filename}]" if finding.document_filename else ""
        )
        entry = (
            f"{index}. ({finding.status}) {finding.claim.subject}: "
            f"{finding.claim.statement}{source}"
        )
        if finding.suggestion:
            entry += f"\n   Hint: {finding.suggestion}"
        lines.append(entry)
    return "\n".join(lines)


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
    schema_by_schema: dict[str, dict[str, dict[str, object]]] | None = None,
    instructions: list[str] | None = None,
) -> MdlEnrichmentProposal:
    return wren_client.propose_mdl_from_document(
        project=project,
        document=document,
        schema=schema,
        schema_types=schema_types,
        schema_by_schema=schema_by_schema,
        instructions=instructions,
    )


def _wren_materialization_base(config: AgentConfig) -> Path:
    if config.wren_project_path:
        return Path(config.wren_project_path)
    return Path(config.agent_storage_dir) / "wren"


def _enforce_onboarding_schema_boundary(
    context: Any, project: SemanticProject, dataset_ids: list[int]
) -> None:
    """Keep only selected datasets within the project's schema set, and reconcile.

    An explicit ``include`` selection is fetched id-first (cross-schema). This is
    the project-boundary guard (F5/R1): never onboard a dataset whose schema is
    outside the project's proven set, even if a crafted request supplied its id —
    defense-in-depth, since R1 also rejects at activation. A shortfall between the
    requested ids and the resolved in-scope datasets is logged so a silent drop
    (an id that did not load or fell out of scope) surfaces rather than masking.
    """

    allowed = {schema.lower() for schema in project.schema_names}
    in_scope = [
        dataset
        for dataset in context.datasets
        if (dataset.schema_name or project.schema_name or "").lower() in allowed
    ]
    dropped = len(context.datasets) - len(in_scope)
    if dropped:
        logger.warning(
            "Onboarding dropped %s selected dataset(s) outside the project's "
            "schema set %s.",
            dropped,
            sorted(allowed),
        )
    context.datasets = in_scope
    requested = len(set(dataset_ids))
    if len(in_scope) < requested:
        logger.warning(
            "Onboarding resolved %s of %s requested dataset id(s) for project %s.",
            len(in_scope),
            requested,
            project.id,
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
    project_id: str | None = None,
    detail: dict[str, Any] | None = None,
    actor_name: str | None = None,
) -> None:
    # ``actor_name`` (the author's display name, DP10) is captured into the event
    # detail at write time so a shared project's timeline can name *who* acted
    # without a cross-user lookup the read path cannot perform.
    if actor_name:
        detail = {**(detail or {}), "actor_name": actor_name}
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
        or config.wren_memory_store in {"sqlalchemy", "lancedb", "postgres"}
        or config.document_storage == "postgres"
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
