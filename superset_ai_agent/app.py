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
from superset_ai_agent.graph import TextToSqlGraph
from superset_ai_agent.integrations.superset.factory import create_superset_client
from superset_ai_agent.llm.factory import create_model_client
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    AgentQueryResponse,
    HealthResponse,
    ModelInfo,
    ValidateSqlRequest,
)
from superset_ai_agent.tools.sql import validate_read_only_sql


def create_app(
    *,
    config: AgentConfig | None = None,
    model_client: Any | None = None,
    ollama_client: Any | None = None,
    text_to_sql_graph: Any | None = None,
) -> FastAPI:
    """Create the standalone AI agent API.

    Dependency injection keeps the service lightweight and easy to test.
    """

    app_config = config or AgentConfig.from_env()
    active_model_client = (
        model_client or ollama_client or create_model_client(app_config)
    )

    if text_to_sql_graph is None:
        superset_client = create_superset_client(app_config)
        context_provider = SupersetMetadataContextProvider(superset_client)
        graph = TextToSqlGraph(
            config=app_config,
            model_client=active_model_client,
            context_provider=context_provider,
            superset_client=superset_client,
        )
    else:
        graph = text_to_sql_graph

    api = FastAPI(title=app_config.app_name, version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_config.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
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

    @api.post("/agent/validate-sql")
    def validate_sql(request: ValidateSqlRequest):
        """Validate SQL without invoking the model."""

        return validate_read_only_sql(
            request.sql,
            dialect=request.dialect,
            default_limit=request.default_limit or app_config.default_sql_limit,
        )

    return api


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
