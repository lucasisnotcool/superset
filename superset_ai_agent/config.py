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

import os
from dataclasses import dataclass
from typing import cast, Literal

SupersetAdapterMode = Literal["local", "rest", "mcp"]
WrenAdapterMode = Literal["file", "http", "llm"]
WrenEngineMode = Literal["passthrough", "wren_core"]
WrenRetrieverMode = Literal["keyword", "embedding"]
WrenMemoryStoreMode = Literal["none", "sqlalchemy", "lancedb"]
ConversationStoreMode = Literal["memory", "sqlalchemy"]
SemanticLayerStoreMode = Literal["memory", "sqlalchemy"]
DocumentStorageMode = Literal["local", "s3"]
IdentityProviderMode = Literal["static", "signed_header", "superset_session"]
SupersetAuthMode = Literal["service_account", "user_session"]
MigrationBootstrapMode = Literal["error", "stamp_existing"]
ModelProviderMode = Literal[
    "ollama",
    "openai",
    "openai_compatible",
    "azure_openai",
]
StructuredOutputMode = Literal["json_schema", "json_object", "prompt_only"]
SemanticAccessMode = Literal["superset_only", "db_uri_match", "superset_or_uri"]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration for the standalone AI agent POC."""

    app_name: str = "Superset AI Agent POC"
    model_provider: ModelProviderMode = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    openai_compatible_api_key: str | None = None
    openai_compatible_base_url: str | None = None
    openai_compatible_model: str | None = None
    openai_compatible_require_api_key: bool = True
    openai_compatible_structured_output: StructuredOutputMode = "json_schema"
    azure_openai_endpoint: str | None = None
    azure_openai_key: str | None = None
    azure_openai_model: str | None = None
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_structured_output: StructuredOutputMode = "json_schema"
    default_sql_limit: int = 1000
    max_repair_attempts: int = 1
    max_context_datasets: int = 8
    max_sample_rows: int = 5
    conversation_store: ConversationStoreMode = "memory"
    semantic_layer_store: SemanticLayerStoreMode = "memory"
    identity_provider: IdentityProviderMode = "superset_session"
    allow_static_identity_with_persistence: bool = False
    signed_identity_header: str = "X-Superset-Ai-Agent-Identity"
    signed_identity_secret: str | None = None
    agent_database_url: str = "sqlite:///./.data/ai_agent.db"
    agent_database_echo: bool = False
    agent_run_migrations: bool = True
    agent_migration_bootstrap: MigrationBootstrapMode = "error"
    agent_storage_dir: str = "./.data"
    document_storage: DocumentStorageMode = "local"
    document_s3_bucket: str | None = None
    document_s3_prefix: str = "superset-ai-agent/documents"
    document_s3_endpoint_url: str | None = None
    document_s3_region_name: str | None = None
    max_history_messages: int = 12
    max_prompt_result_rows: int = 5
    max_agent_sql_iterations: int = 3
    wren_enabled: bool = True
    wren_adapter: WrenAdapterMode = "llm"
    wren_base_url: str | None = None
    wren_api_key: str | None = None
    wren_timeout_seconds: float = 30.0
    wren_onboarding_enabled: bool = False
    wren_project_path: str | None = None
    wren_mdl_path: str | None = None
    wren_memory_path: str | None = None
    wren_dry_plan_enabled: bool = False
    wren_execution_enabled: bool = False
    wren_context_limit: int = 8
    wren_example_limit: int = 5
    wren_schema_table_scan_limit: int = 100
    wren_schema_table_candidate_limit: int = 12
    wren_schema_metric_candidate_limit: int = 20
    wren_schema_example_candidate_limit: int = 5
    wren_schema_document_candidate_limit: int = 5
    wren_schema_context_token_budget: int = 6000
    wren_require_schema_scope: bool = True
    wren_max_document_bytes: int = 2_000_000
    wren_allowed_document_types: tuple[str, ...] = (
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
    )
    semantic_access_mode: SemanticAccessMode = "superset_or_uri"
    semantic_full_access_grants_write: bool = False
    semantic_activation_requires_live_schema: bool = False
    wren_core_validation_enabled: bool = False
    # Wren full-parity seams (see wren_full.md). All default to the
    # zero-dependency binding so the service starts unchanged; turning any of
    # these on requires durable semantic persistence (semantic_layer_store=
    # sqlalchemy), enforced at startup.
    wren_engine: WrenEngineMode = "passthrough"
    wren_semantic_sql_enabled: bool = False
    wren_retriever: WrenRetrieverMode = "keyword"
    wren_memory_store: WrenMemoryStoreMode = "none"
    wren_memory_learning_enabled: bool = True
    wren_memory_recall_k: int = 3
    wren_lancedb_path: str | None = None
    embedder_provider: str | None = None
    embedder_model: str = "text-embedding-3-small"
    embedder_dimensions: int = 1536
    embedder_api_key: str | None = None
    embedder_base_url: str | None = None
    embedder_batch_size: int = 128
    superset_agent_adapter: SupersetAdapterMode = "rest"
    superset_auth_mode: SupersetAuthMode = "user_session"
    superset_base_url: str = "http://localhost:8091"
    superset_mcp_url: str = "http://localhost:8098/mcp"
    superset_auth_token: str | None = None
    superset_username: str | None = None
    superset_password: str | None = None
    superset_auth_provider: str = "db"
    superset_csrf_token: str | None = None
    superset_sql_poll_attempts: int = 10
    superset_sql_poll_interval_seconds: float = 0.5
    superset_mcp_auth_token: str | None = None
    cors_allowed_origins: tuple[str, ...] = (
        "http://localhost",
        "http://localhost:8090",
        "http://127.0.0.1:8090",
        "http://localhost:8092",
        "http://127.0.0.1:8092",
    )
    log_level: str = "INFO"
    suppress_superset_logs: bool = True
    local_superset_secret_key: str = (
        "ai-agent-local-dev-secret-key-not-for-production"  # noqa: S105
    )

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """Build config from environment variables."""

        return cls(
            model_provider=cast(
                ModelProviderMode,
                os.getenv("AI_AGENT_MODEL_PROVIDER", cls.model_provider)
                .strip()
                .lower(),
            ),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", cls.ollama_base_url),
            ollama_model=os.getenv("AI_AGENT_MODEL", cls.ollama_model),
            openai_api_key=os.getenv("OPENAI_API_KEY") or cls.openai_api_key,
            openai_base_url=os.getenv("OPENAI_BASE_URL", cls.openai_base_url),
            openai_model=os.getenv("OPENAI_MODEL", cls.openai_model),
            openai_compatible_api_key=(
                os.getenv("OPENAI_COMPATIBLE_API_KEY") or cls.openai_compatible_api_key
            ),
            openai_compatible_base_url=(
                os.getenv("OPENAI_COMPATIBLE_BASE_URL")
                or cls.openai_compatible_base_url
            ),
            openai_compatible_model=(
                os.getenv("OPENAI_COMPATIBLE_MODEL") or cls.openai_compatible_model
            ),
            openai_compatible_require_api_key=_env_bool(
                "OPENAI_COMPATIBLE_REQUIRE_API_KEY",
                cls.openai_compatible_require_api_key,
            ),
            openai_compatible_structured_output=cast(
                StructuredOutputMode,
                os.getenv(
                    "OPENAI_COMPATIBLE_STRUCTURED_OUTPUT",
                    cls.openai_compatible_structured_output,
                )
                .strip()
                .lower(),
            ),
            azure_openai_endpoint=(
                os.getenv("AZURE_OPENAI_ENDPOINT") or cls.azure_openai_endpoint
            ),
            azure_openai_key=os.getenv("AZURE_OPENAI_KEY") or cls.azure_openai_key,
            azure_openai_model=(
                os.getenv("AZURE_OPENAI_MODEL") or cls.azure_openai_model
            ),
            azure_openai_api_version=os.getenv(
                "AZURE_OPENAI_API_VERSION",
                cls.azure_openai_api_version,
            ),
            azure_openai_structured_output=cast(
                StructuredOutputMode,
                os.getenv(
                    "AZURE_OPENAI_STRUCTURED_OUTPUT",
                    cls.azure_openai_structured_output,
                )
                .strip()
                .lower(),
            ),
            default_sql_limit=int(
                os.getenv("AI_AGENT_DEFAULT_SQL_LIMIT", str(cls.default_sql_limit))
            ),
            max_repair_attempts=int(
                os.getenv("AI_AGENT_MAX_REPAIR_ATTEMPTS", str(cls.max_repair_attempts))
            ),
            max_context_datasets=int(
                os.getenv(
                    "AI_AGENT_MAX_CONTEXT_DATASETS",
                    str(cls.max_context_datasets),
                )
            ),
            max_sample_rows=int(
                os.getenv("AI_AGENT_MAX_SAMPLE_ROWS", str(cls.max_sample_rows))
            ),
            conversation_store=cast(
                ConversationStoreMode,
                os.getenv("AI_AGENT_CONVERSATION_STORE", cls.conversation_store)
                .strip()
                .lower(),
            ),
            semantic_layer_store=cast(
                SemanticLayerStoreMode,
                os.getenv(
                    "AI_AGENT_SEMANTIC_LAYER_STORE",
                    cls.semantic_layer_store,
                )
                .strip()
                .lower(),
            ),
            identity_provider=cast(
                IdentityProviderMode,
                os.getenv("AI_AGENT_IDENTITY_PROVIDER", cls.identity_provider)
                .strip()
                .lower(),
            ),
            allow_static_identity_with_persistence=_env_bool(
                "AI_AGENT_ALLOW_STATIC_IDENTITY_WITH_PERSISTENCE",
                cls.allow_static_identity_with_persistence,
            ),
            signed_identity_header=os.getenv(
                "AI_AGENT_SIGNED_IDENTITY_HEADER",
                cls.signed_identity_header,
            ),
            signed_identity_secret=(
                os.getenv("AI_AGENT_SIGNED_IDENTITY_SECRET")
                or cls.signed_identity_secret
            ),
            agent_database_url=os.getenv(
                "AI_AGENT_DATABASE_URL",
                cls.agent_database_url,
            ),
            agent_database_echo=_env_bool(
                "AI_AGENT_DATABASE_ECHO",
                cls.agent_database_echo,
            ),
            agent_run_migrations=_env_bool(
                "AI_AGENT_RUN_MIGRATIONS",
                cls.agent_run_migrations,
            ),
            agent_migration_bootstrap=cast(
                MigrationBootstrapMode,
                os.getenv(
                    "AI_AGENT_MIGRATION_BOOTSTRAP",
                    cls.agent_migration_bootstrap,
                )
                .strip()
                .lower(),
            ),
            agent_storage_dir=os.getenv("AI_AGENT_STORAGE_DIR", cls.agent_storage_dir),
            document_storage=cast(
                DocumentStorageMode,
                os.getenv("AI_AGENT_DOCUMENT_STORAGE", cls.document_storage)
                .strip()
                .lower(),
            ),
            document_s3_bucket=(
                os.getenv("AI_AGENT_DOCUMENT_S3_BUCKET") or cls.document_s3_bucket
            ),
            document_s3_prefix=os.getenv(
                "AI_AGENT_DOCUMENT_S3_PREFIX",
                cls.document_s3_prefix,
            ),
            document_s3_endpoint_url=(
                os.getenv("AI_AGENT_DOCUMENT_S3_ENDPOINT_URL")
                or cls.document_s3_endpoint_url
            ),
            document_s3_region_name=(
                os.getenv("AI_AGENT_DOCUMENT_S3_REGION_NAME")
                or cls.document_s3_region_name
            ),
            max_history_messages=int(
                os.getenv(
                    "AI_AGENT_MAX_HISTORY_MESSAGES",
                    str(cls.max_history_messages),
                )
            ),
            max_prompt_result_rows=int(
                os.getenv(
                    "AI_AGENT_MAX_PROMPT_RESULT_ROWS",
                    str(cls.max_prompt_result_rows),
                )
            ),
            max_agent_sql_iterations=int(
                os.getenv(
                    "AI_AGENT_MAX_SQL_ITERATIONS",
                    str(cls.max_agent_sql_iterations),
                )
            ),
            wren_enabled=_env_bool("WREN_ENABLED", cls.wren_enabled),
            wren_adapter=cast(
                WrenAdapterMode,
                os.getenv("WREN_ADAPTER", cls.wren_adapter).strip().lower(),
            ),
            wren_base_url=os.getenv("WREN_BASE_URL") or cls.wren_base_url,
            wren_api_key=os.getenv("WREN_API_KEY") or cls.wren_api_key,
            wren_timeout_seconds=float(
                os.getenv("WREN_TIMEOUT_SECONDS", str(cls.wren_timeout_seconds))
            ),
            wren_onboarding_enabled=_env_bool(
                "WREN_ONBOARDING_ENABLED",
                cls.wren_onboarding_enabled,
            ),
            wren_project_path=os.getenv("WREN_PROJECT_PATH") or cls.wren_project_path,
            wren_mdl_path=os.getenv("WREN_MDL_PATH") or cls.wren_mdl_path,
            wren_memory_path=os.getenv("WREN_MEMORY_PATH") or cls.wren_memory_path,
            wren_dry_plan_enabled=_env_bool(
                "WREN_DRY_PLAN_ENABLED",
                cls.wren_dry_plan_enabled,
            ),
            wren_execution_enabled=_env_bool(
                "WREN_EXECUTION_ENABLED",
                cls.wren_execution_enabled,
            ),
            wren_context_limit=int(
                os.getenv("WREN_CONTEXT_LIMIT", str(cls.wren_context_limit))
            ),
            wren_example_limit=int(
                os.getenv("WREN_EXAMPLE_LIMIT", str(cls.wren_example_limit))
            ),
            wren_schema_table_scan_limit=int(
                os.getenv(
                    "WREN_SCHEMA_TABLE_SCAN_LIMIT",
                    str(cls.wren_schema_table_scan_limit),
                )
            ),
            wren_schema_table_candidate_limit=int(
                os.getenv(
                    "WREN_SCHEMA_TABLE_CANDIDATE_LIMIT",
                    str(cls.wren_schema_table_candidate_limit),
                )
            ),
            wren_schema_metric_candidate_limit=int(
                os.getenv(
                    "WREN_SCHEMA_METRIC_CANDIDATE_LIMIT",
                    str(cls.wren_schema_metric_candidate_limit),
                )
            ),
            wren_schema_example_candidate_limit=int(
                os.getenv(
                    "WREN_SCHEMA_EXAMPLE_CANDIDATE_LIMIT",
                    str(cls.wren_schema_example_candidate_limit),
                )
            ),
            wren_schema_document_candidate_limit=int(
                os.getenv(
                    "WREN_SCHEMA_DOCUMENT_CANDIDATE_LIMIT",
                    str(cls.wren_schema_document_candidate_limit),
                )
            ),
            wren_schema_context_token_budget=int(
                os.getenv(
                    "WREN_SCHEMA_CONTEXT_TOKEN_BUDGET",
                    str(cls.wren_schema_context_token_budget),
                )
            ),
            wren_require_schema_scope=_env_bool(
                "WREN_REQUIRE_SCHEMA_SCOPE",
                cls.wren_require_schema_scope,
            ),
            wren_max_document_bytes=int(
                os.getenv(
                    "WREN_MAX_DOCUMENT_BYTES",
                    str(cls.wren_max_document_bytes),
                )
            ),
            wren_allowed_document_types=_env_list(
                "WREN_ALLOWED_DOCUMENT_TYPES",
                cls.wren_allowed_document_types,
            ),
            semantic_access_mode=cast(
                SemanticAccessMode,
                os.getenv(
                    "AI_AGENT_SEMANTIC_ACCESS_MODE",
                    cls.semantic_access_mode,
                )
                .strip()
                .lower(),
            ),
            semantic_full_access_grants_write=_env_bool(
                "AI_AGENT_SEMANTIC_FULL_ACCESS_GRANTS_WRITE",
                cls.semantic_full_access_grants_write,
            ),
            semantic_activation_requires_live_schema=_env_bool(
                "AI_AGENT_SEMANTIC_ACTIVATION_REQUIRES_LIVE_SCHEMA",
                cls.semantic_activation_requires_live_schema,
            ),
            wren_core_validation_enabled=_env_bool(
                "WREN_CORE_VALIDATION_ENABLED",
                cls.wren_core_validation_enabled,
            ),
            wren_engine=cast(
                WrenEngineMode,
                os.getenv("WREN_ENGINE", cls.wren_engine).strip().lower(),
            ),
            wren_semantic_sql_enabled=_env_bool(
                "WREN_SEMANTIC_SQL_ENABLED",
                cls.wren_semantic_sql_enabled,
            ),
            wren_retriever=cast(
                WrenRetrieverMode,
                os.getenv("WREN_RETRIEVER", cls.wren_retriever).strip().lower(),
            ),
            wren_memory_store=cast(
                WrenMemoryStoreMode,
                os.getenv("WREN_MEMORY_STORE", cls.wren_memory_store).strip().lower(),
            ),
            wren_memory_learning_enabled=_env_bool(
                "WREN_MEMORY_LEARNING_ENABLED",
                cls.wren_memory_learning_enabled,
            ),
            wren_memory_recall_k=int(
                os.getenv("WREN_MEMORY_RECALL_K", str(cls.wren_memory_recall_k))
            ),
            wren_lancedb_path=os.getenv("WREN_LANCEDB_PATH") or cls.wren_lancedb_path,
            embedder_provider=(
                os.getenv("AI_AGENT_EMBEDDER_PROVIDER") or cls.embedder_provider
            ),
            embedder_model=os.getenv("AI_AGENT_EMBEDDER_MODEL", cls.embedder_model),
            embedder_dimensions=int(
                os.getenv("AI_AGENT_EMBEDDER_DIMENSIONS", str(cls.embedder_dimensions))
            ),
            embedder_api_key=(
                os.getenv("AI_AGENT_EMBEDDER_API_KEY") or cls.embedder_api_key
            ),
            embedder_base_url=(
                os.getenv("AI_AGENT_EMBEDDER_BASE_URL") or cls.embedder_base_url
            ),
            embedder_batch_size=int(
                os.getenv("AI_AGENT_EMBEDDER_BATCH_SIZE", str(cls.embedder_batch_size))
            ),
            superset_agent_adapter=cast(
                SupersetAdapterMode,
                os.getenv("SUPERSET_AGENT_ADAPTER", cls.superset_agent_adapter)
                .strip()
                .lower(),
            ),
            superset_auth_mode=cast(
                SupersetAuthMode,
                os.getenv("SUPERSET_AUTH_MODE", cls.superset_auth_mode)
                .strip()
                .lower(),
            ),
            superset_base_url=os.getenv("SUPERSET_BASE_URL", cls.superset_base_url),
            superset_mcp_url=os.getenv("SUPERSET_MCP_URL", cls.superset_mcp_url),
            superset_auth_token=(
                os.getenv("SUPERSET_AUTH_TOKEN") or cls.superset_auth_token
            ),
            superset_username=os.getenv("SUPERSET_USERNAME") or cls.superset_username,
            superset_password=os.getenv("SUPERSET_PASSWORD") or cls.superset_password,
            superset_auth_provider=os.getenv(
                "SUPERSET_AUTH_PROVIDER",
                cls.superset_auth_provider,
            ),
            superset_csrf_token=(
                os.getenv("SUPERSET_CSRF_TOKEN") or cls.superset_csrf_token
            ),
            superset_sql_poll_attempts=int(
                os.getenv(
                    "SUPERSET_SQL_POLL_ATTEMPTS",
                    str(cls.superset_sql_poll_attempts),
                )
            ),
            superset_sql_poll_interval_seconds=float(
                os.getenv(
                    "SUPERSET_SQL_POLL_INTERVAL_SECONDS",
                    str(cls.superset_sql_poll_interval_seconds),
                )
            ),
            superset_mcp_auth_token=(
                os.getenv("SUPERSET_MCP_AUTH_TOKEN")
                or os.getenv("SUPERSET_AUTH_TOKEN")
                or cls.superset_mcp_auth_token
            ),
            cors_allowed_origins=_env_list(
                "AI_AGENT_CORS_ALLOWED_ORIGINS",
                cls.cors_allowed_origins,
            ),
            log_level=os.getenv("AI_AGENT_LOG_LEVEL", cls.log_level),
            suppress_superset_logs=_env_bool(
                "AI_AGENT_SUPPRESS_SUPERSET_LOGS",
                cls.suppress_superset_logs,
            ),
            local_superset_secret_key=os.getenv(
                "AI_AGENT_LOCAL_SUPERSET_SECRET_KEY",
                cls.local_superset_secret_key,
            ),
        )

    def default_model(self) -> str:
        """Return the configured default model for the active provider."""

        if self.model_provider == "openai":
            return self.openai_model
        if self.model_provider == "openai_compatible":
            return self.openai_compatible_model or ""
        if self.model_provider == "azure_openai":
            return self.azure_openai_model or ""
        return self.ollama_model

    def model_base_url(self) -> str:
        """Return the configured base URL for the active provider."""

        if self.model_provider == "openai":
            return self.openai_base_url.rstrip("/")
        if self.model_provider == "openai_compatible":
            return (self.openai_compatible_base_url or "").rstrip("/")
        if self.model_provider == "azure_openai":
            return (self.azure_openai_endpoint or "").rstrip("/")
        return self.ollama_base_url.rstrip("/")
