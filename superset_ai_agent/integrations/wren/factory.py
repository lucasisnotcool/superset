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

from typing import TYPE_CHECKING

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.wren.client import (
    DisabledWrenClient,
    FileWrenClient,
    WrenClient,
)
from superset_ai_agent.integrations.wren.http_client import WrenHttpClient

if TYPE_CHECKING:
    from superset_ai_agent.llm.base import ModelClient
    from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore


def create_wren_client(
    config: AgentConfig,
    *,
    model_client: "ModelClient | None" = None,
    mdl_file_store: "MdlFileStore | None" = None,
) -> WrenClient:
    """Create the configured read-only Wren client.

    The ``llm`` adapter requires a ``model_client``; without one it falls back to
    the deterministic file-backed client so the service still starts.
    """

    if config.wren_execution_enabled:
        raise ValueError("Wren execution is not supported by the Superset AI agent.")
    if not config.wren_enabled:
        return DisabledWrenClient()
    if config.wren_adapter == "http":
        return WrenHttpClient(config)
    if config.wren_adapter == "llm" and model_client is not None:
        # Imported lazily to avoid importing LLM/semantic-layer modules when the
        # llm adapter is not selected.
        from superset_ai_agent.integrations.wren.llm_client import LlmWrenClient

        return LlmWrenClient(
            config,
            model_client,
            mdl_file_store=mdl_file_store,
        )
    return FileWrenClient(config)
