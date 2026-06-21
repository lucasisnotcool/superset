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
from superset_ai_agent.integrations.wren.client import (
    DisabledWrenClient,
    FileWrenClient,
    WrenClient,
)


def create_wren_client(config: AgentConfig) -> WrenClient:
    """Create the configured read-only Wren client."""

    if config.wren_execution_enabled:
        raise ValueError("Wren execution is not supported by the Superset AI agent.")
    if not config.wren_enabled:
        return DisabledWrenClient()
    return FileWrenClient(config)
