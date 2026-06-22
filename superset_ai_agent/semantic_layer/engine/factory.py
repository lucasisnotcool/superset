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

"""Factory for the configured SemanticEngine binding."""

from __future__ import annotations

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.semantic_layer.engine.base import SemanticEngine
from superset_ai_agent.semantic_layer.engine.passthrough import PassthroughEngine
from superset_ai_agent.semantic_layer.engine.wren_core_engine import WrenCoreEngine


def create_semantic_engine(config: AgentConfig) -> SemanticEngine:
    """Return the engine selected by ``config.wren_engine``.

    Defaults to the zero-dependency passthrough binding so the service starts
    unchanged. The wren-core binding itself degrades to passthrough behavior at
    call time when the optional engine is not installed.
    """

    if config.wren_engine == "wren_core":
        return WrenCoreEngine()
    return PassthroughEngine()
