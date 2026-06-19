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
from superset_ai_agent.llm.azure_openai import AzureOpenAIModelClient
from superset_ai_agent.llm.base import ModelClient
from superset_ai_agent.llm.ollama import OllamaModelClient
from superset_ai_agent.llm.openai_client import OpenAIModelClient
from superset_ai_agent.llm.openai_compatible import OpenAICompatibleModelClient


def create_model_client(config: AgentConfig) -> ModelClient:
    """Create the configured model provider client."""

    provider = config.model_provider
    if provider == "ollama":
        return OllamaModelClient(config)
    if provider == "openai":
        return OpenAIModelClient(config)
    if provider == "openai_compatible":
        return OpenAICompatibleModelClient(config)
    if provider == "azure_openai":
        return AzureOpenAIModelClient(config)
    raise ValueError(
        "Unsupported AI_AGENT_MODEL_PROVIDER value "
        f"{provider!r}. Expected one of: ollama, openai, openai_compatible, "
        "azure_openai."
    )
