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

import logging

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatasetMetadata,
    SupersetClient,
)
from superset_ai_agent.schemas import AgentQueryRequest
from superset_ai_agent.semantic_layer.retrieval import (
    retrieve_schema_context,
    RetrievedContext,
)

logger = logging.getLogger(__name__)


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

        # Only the database shell is needed here — ``datasets`` is replaced by the
        # candidate scan below, so fetching them in the base context too would pay
        # the per-dataset N+1 a second time for nothing.
        base_context = self.superset_client.get_agent_context(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            dataset_ids=[],
            include_datasets=False,
        )
        candidate_datasets = self._candidate_datasets(request)
        if not candidate_datasets:
            return base_context
        retrieved = retrieve_schema_context(
            request=request,
            context=base_context.model_copy(update={"datasets": candidate_datasets}),
            config=self.config,
        )
        self.last_retrieval = retrieved
        return retrieved.context

    def _candidate_datasets(self, request: AgentQueryRequest) -> list[DatasetMetadata]:
        """Datasets to rank for the question.

        Single-schema (the common case): scan the one schema, unchanged. For a
        **multi-schema project** (``effective_schema_names`` > 1), union every
        member schema's datasets so the agent can rank — and join — across the
        project's full scope, mirroring the modeling-time union
        (``_onboarding_context``/``_schema_index_for_project``). The union is
        bounded by ``wren_schema_total_candidate_limit`` (caps the N+1 scan);
        ranking then selects the most relevant from the union.
        """

        per_schema_limit = max(
            self.config.wren_schema_table_scan_limit,
            self.config.wren_schema_table_candidate_limit,
            self.config.max_context_datasets,
        )
        schemas = request.effective_schema_names
        if len(schemas) <= 1:
            return self.superset_client.list_datasets(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                limit=per_schema_limit,
            )
        total_cap = self.config.wren_schema_total_candidate_limit
        seen: set[int] = set()
        candidates: list[DatasetMetadata] = []
        truncated = False
        for schema in schemas:
            if total_cap > 0 and len(candidates) >= total_cap:
                truncated = True
                break
            for dataset in self.superset_client.list_datasets(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=schema,
                limit=per_schema_limit,
            ):
                if dataset.id in seen:
                    continue
                seen.add(dataset.id)
                candidates.append(dataset)
            if total_cap > 0 and len(candidates) >= total_cap:
                truncated = True
                candidates = candidates[:total_cap]
                break
        if truncated:
            logger.info(
                "Cross-schema candidate union truncated at %d "
                "(wren_schema_total_candidate_limit) over %d schemas %s",
                total_cap,
                len(schemas),
                schemas,
            )
        return candidates

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
        # Only the database shell is needed here — ``datasets`` is replaced by the
        # candidate scan below, so fetching them in the base context too would pay
        # the per-dataset N+1 a second time for nothing.
        base_context = self.superset_client.get_agent_context(
            database_id=request.database_id,
            catalog_name=request.catalog_name,
            schema_name=request.schema_name,
            dataset_ids=[],
            include_datasets=False,
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
