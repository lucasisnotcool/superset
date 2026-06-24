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
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.integrations.superset.client import AgentContext, SupersetClient
from superset_ai_agent.schemas import AgentQueryRequest
from superset_ai_agent.semantic_layer.retrieval import (
    retrieve_schema_context,
    RetrievedContext,
)


class SupersetMetadataContextProvider(ContextProvider):
    """Phase 1 context provider backed by Superset dataset metadata."""

    def __init__(
        self,
        superset_client: SupersetClient,
        *,
        config: AgentConfig | None = None,
    ):
        self.superset_client = superset_client
        self.config = config or AgentConfig()
        self.last_retrieval: RetrievedContext | None = None

    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        self.last_retrieval = None
        if request.dataset_ids or not request.schema_name:
            return self.superset_client.get_agent_context(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                dataset_ids=request.dataset_ids,
            )

        base_context = self.superset_client.get_agent_context(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            dataset_ids=[],
        )
        candidate_datasets = self.superset_client.list_datasets(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            limit=max(
                self.config.wren_schema_table_scan_limit,
                self.config.wren_schema_table_candidate_limit,
                self.config.max_context_datasets,
            ),
        )
        if not candidate_datasets:
            return base_context
        retrieved = retrieve_schema_context(
            request=request,
            context=base_context.model_copy(update={"datasets": candidate_datasets}),
            config=self.config,
        )
        self.last_retrieval = retrieved
        return retrieved.context

    def get_full_schema(self, request: AgentQueryRequest) -> AgentContext:
        """Return the **complete** in-scope schema, with no question ranking (CR3).

        Modeling-time consumers (enrichment, onboarding, MDL validation) must see
        every dataset in the scope — not a relevance-ranked top-k against a
        placeholder question, which can silently drop the very tables a document is
        about. Bounded only by ``wren_schema_table_scan_limit``; the request question
        is ignored.
        """

        if request.dataset_ids or not request.schema_name:
            return self.superset_client.get_agent_context(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                dataset_ids=request.dataset_ids,
            )
        base_context = self.superset_client.get_agent_context(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            dataset_ids=[],
        )
        candidate_datasets = self.superset_client.list_datasets(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            limit=max(self.config.wren_schema_table_scan_limit, 1),
        )
        if not candidate_datasets:
            return base_context
        return base_context.model_copy(update={"datasets": candidate_datasets})
