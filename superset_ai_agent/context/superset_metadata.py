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

from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.schemas import AgentQueryRequest


class SupersetMetadataContextProvider(ContextProvider):
    """Phase 1 context provider backed by Superset dataset metadata."""

    def __init__(self, superset_client: SupersetClient):
        self.superset_client = superset_client

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return self.superset_client.get_agent_context(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            dataset_ids=request.dataset_ids,
        )
