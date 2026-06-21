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

from superset_ai_agent.auth import SupersetRequestAuth
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    LocalSupersetClient,
    SupersetClient,
)
from superset_ai_agent.integrations.superset.mcp import SupersetMcpClient
from superset_ai_agent.integrations.superset.rest import SupersetRestClient


def create_superset_client(
    config: AgentConfig,
    *,
    request_auth: SupersetRequestAuth | None = None,
) -> SupersetClient:
    """Create the configured Superset adapter without changing the graph."""

    adapter = config.superset_agent_adapter
    if adapter == "local":
        return LocalSupersetClient(config)
    if adapter == "rest":
        return SupersetRestClient(config, request_auth=request_auth)
    if adapter == "mcp":
        return SupersetMcpClient(config, request_auth=request_auth)
    raise ValueError(
        "Unsupported SUPERSET_AGENT_ADAPTER value "
        f"{adapter!r}. Expected one of: local, rest, mcp."
    )
