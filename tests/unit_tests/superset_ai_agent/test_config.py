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

from superset_ai_agent.config import AgentConfig


def test_agent_config_defaults_to_user_session_auth_without_admin_credentials() -> None:
    config = AgentConfig()

    assert config.identity_provider == "superset_session"
    assert config.superset_auth_mode == "user_session"
    assert config.superset_username is None
    assert config.superset_password is None


def test_agent_config_reads_superset_adapter_environment(monkeypatch) -> None:
    monkeypatch.setenv("AI_AGENT_MODEL_PROVIDER", "OPENAI_COMPATIBLE")
    monkeypatch.setenv("SUPERSET_AGENT_ADAPTER", "MCP")
    monkeypatch.setenv("SUPERSET_AUTH_MODE", "USER_SESSION")
    monkeypatch.setenv("SUPERSET_BASE_URL", "http://superset.local")
    monkeypatch.setenv("SUPERSET_MCP_URL", "http://superset.local/mcp")
    monkeypatch.setenv("SUPERSET_AUTH_TOKEN", "rest-token")
    monkeypatch.setenv("SUPERSET_USERNAME", "agent")
    monkeypatch.setenv("SUPERSET_PASSWORD", "secret")
    monkeypatch.setenv("SUPERSET_AUTH_PROVIDER", "ldap")
    monkeypatch.setenv("SUPERSET_CSRF_TOKEN", "csrf-token")
    monkeypatch.setenv("SUPERSET_SQL_POLL_ATTEMPTS", "3")
    monkeypatch.setenv("SUPERSET_SQL_POLL_INTERVAL_SECONDS", "0.25")
    monkeypatch.setenv("SUPERSET_MCP_AUTH_TOKEN", "mcp-token")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://llm.local/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "custom-sql-model")
    monkeypatch.setenv("OPENAI_COMPATIBLE_STRUCTURED_OUTPUT", "JSON_OBJECT")
    monkeypatch.setenv(
        "AI_AGENT_CORS_ALLOWED_ORIGINS",
        "http://localhost:9000,http://localhost:8088",
    )
    monkeypatch.setenv("WREN_ENABLED", "false")
    monkeypatch.setenv("WREN_PROJECT_PATH", "/tmp/wren-project")
    monkeypatch.setenv("WREN_MDL_PATH", "/tmp/wren-project/mdl.json")
    monkeypatch.setenv("WREN_MEMORY_PATH", "/tmp/wren-project/memory.json")
    monkeypatch.setenv("WREN_DRY_PLAN_ENABLED", "true")
    monkeypatch.setenv("WREN_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("WREN_CONTEXT_LIMIT", "4")
    monkeypatch.setenv("WREN_EXAMPLE_LIMIT", "2")
    monkeypatch.setenv("WREN_MAX_DOCUMENT_BYTES", "1234")
    monkeypatch.setenv("WREN_ALLOWED_DOCUMENT_TYPES", "text/plain,application/json")
    monkeypatch.setenv("AI_AGENT_CONVERSATION_STORE", "SQLALCHEMY")
    monkeypatch.setenv("AI_AGENT_SEMANTIC_LAYER_STORE", "SQLALCHEMY")
    monkeypatch.setenv("AI_AGENT_IDENTITY_PROVIDER", "SIGNED_HEADER")
    monkeypatch.setenv("AI_AGENT_SIGNED_IDENTITY_HEADER", "x-agent-user")
    monkeypatch.setenv("AI_AGENT_SIGNED_IDENTITY_SECRET", "identity-secret")
    monkeypatch.setenv("AI_AGENT_DATABASE_URL", "sqlite+pysqlite:///tmp/agent.db")
    monkeypatch.setenv("AI_AGENT_DATABASE_ECHO", "true")
    monkeypatch.setenv("AI_AGENT_RUN_MIGRATIONS", "false")
    monkeypatch.setenv("AI_AGENT_STORAGE_DIR", "/tmp/agent-storage")

    config = AgentConfig.from_env()

    assert config.superset_agent_adapter == "mcp"
    assert config.superset_auth_mode == "user_session"
    assert config.model_provider == "openai_compatible"
    assert config.openai_compatible_base_url == "http://llm.local/v1"
    assert config.openai_compatible_api_key == "test-key"
    assert config.default_model() == "custom-sql-model"
    assert config.openai_compatible_structured_output == "json_object"
    assert config.superset_base_url == "http://superset.local"
    assert config.superset_mcp_url == "http://superset.local/mcp"
    assert config.superset_auth_token == "rest-token"  # noqa: S105
    assert config.superset_username == "agent"
    assert config.superset_password == "secret"  # noqa: S105
    assert config.superset_auth_provider == "ldap"
    assert config.superset_csrf_token == "csrf-token"  # noqa: S105
    assert config.superset_sql_poll_attempts == 3
    assert config.superset_sql_poll_interval_seconds == 0.25
    assert config.superset_mcp_auth_token == "mcp-token"  # noqa: S105
    assert config.cors_allowed_origins == (
        "http://localhost:9000",
        "http://localhost:8088",
    )
    assert config.wren_enabled is False
    assert config.wren_project_path == "/tmp/wren-project"
    assert config.wren_mdl_path == "/tmp/wren-project/mdl.json"
    assert config.wren_memory_path == "/tmp/wren-project/memory.json"
    assert config.wren_dry_plan_enabled is True
    assert config.wren_execution_enabled is False
    assert config.wren_context_limit == 4
    assert config.wren_example_limit == 2
    assert config.wren_max_document_bytes == 1234
    assert config.wren_allowed_document_types == ("text/plain", "application/json")
    assert config.conversation_store == "sqlalchemy"
    assert config.semantic_layer_store == "sqlalchemy"
    assert config.identity_provider == "signed_header"
    assert config.signed_identity_header == "x-agent-user"
    assert config.signed_identity_secret == "identity-secret"
    assert config.agent_database_url == "sqlite+pysqlite:///tmp/agent.db"
    assert config.agent_database_echo is True
    assert config.agent_run_migrations is False
    assert config.agent_storage_dir == "/tmp/agent-storage"


def test_agent_config_reads_azure_openai_environment(monkeypatch) -> None:
    monkeypatch.setenv("AI_AGENT_MODEL_PROVIDER", "AZURE_OPENAI")
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT",
        "https://azure-openai.example.com/",
    )
    monkeypatch.setenv("AZURE_OPENAI_KEY", "azure-key")
    monkeypatch.setenv("AZURE_OPENAI_MODEL", "sql-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
    monkeypatch.setenv("AZURE_OPENAI_STRUCTURED_OUTPUT", "JSON_OBJECT")

    config = AgentConfig.from_env()

    assert config.model_provider == "azure_openai"
    assert config.azure_openai_endpoint == "https://azure-openai.example.com/"
    assert config.azure_openai_key == "azure-key"
    assert config.azure_openai_model == "sql-deployment"
    assert config.azure_openai_api_version == "2024-02-15-preview"
    assert config.azure_openai_structured_output == "json_object"
    assert config.default_model() == "sql-deployment"
    assert config.model_base_url() == "https://azure-openai.example.com"
