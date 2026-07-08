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
import threading

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.context.base import ContextProvider
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatasetMetadata,
    SupersetClient,
)
from superset_ai_agent.persistence.ttl_cache import TtlCache
from superset_ai_agent.schemas import AgentQueryRequest
from superset_ai_agent.semantic_layer.retrieval import (
    retrieve_schema_context,
    RetrievedContext,
)

logger = logging.getLogger(__name__)

#: Process-level cache of live NAMES listings, keyed per (database, catalog,
#: schema). Providers are per-request, so the cache lives at module level and
#: is created lazily with the first config's TTL (one config per process).
#: Trust model matches the shared schema-index cache: entries are table NAMES
#: for scopes the caller has already been authorized against (DB-level access);
#: results are read-only DatasetMetadata and never mutated by consumers.
_names_cache: (
    TtlCache[tuple[int, str | None, str | None], list[DatasetMetadata]] | None
) = None
_names_cache_guard = threading.Lock()


def _names_listing_cache(
    config: AgentConfig,
) -> TtlCache[tuple[int, str | None, str | None], list[DatasetMetadata]]:
    global _names_cache  # noqa: PLW0603
    with _names_cache_guard:
        if _names_cache is None:
            _names_cache = TtlCache(
                ttl_seconds=config.wren_introspection_names_cache_ttl_seconds
            )
        return _names_cache


def reset_names_listing_cache() -> None:
    """Drop the process-level names cache (test isolation)."""

    global _names_cache  # noqa: PLW0603
    with _names_cache_guard:
        _names_cache = None


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
        # UNION, not fallback: the physical catalog is the live schema
        # (owner-scoped names introspection) merged with any registered Superset
        # datasets — datasets ENRICH a table with synced columns but never GATE
        # the catalog. A fallback-only design let a single registered dataset
        # shadow every live-only table (and schema) in scope, which blocked
        # activation of MDL grounded on real, accessible tables.
        introspected = self._introspect_schema(request)
        if introspected:
            registered = {
                ((dataset.schema_name or "").lower(), dataset.table_name.lower())
                for dataset in candidate_datasets
            }
            candidate_datasets = candidate_datasets + [
                dataset
                for dataset in introspected
                if (
                    (dataset.schema_name or "").lower(),
                    dataset.table_name.lower(),
                )
                not in registered
            ]
        if not candidate_datasets:
            return base_context
        return base_context.model_copy(update={"datasets": candidate_datasets})

    def _introspect_schema(self, request: AgentQueryRequest) -> list[DatasetMetadata]:
        """Live-introspect the request's schema (names-only, one call).

        Gated by ``wren_live_schema_introspection`` and the adapter actually
        implementing ``introspect_schema`` (the MCP adapter returns ``[]``).
        Fail-soft: any adapter/engine error degrades to an empty catalog — the
        pre-introspection behavior, never worse.

        Names-first: this lists table NAMES only (one call per schema, bounded
        by ``wren_introspection_names_limit`` so nothing is silently truncated
        on a real warehouse). Columns are reflected lazily per table — by the
        schema index's column loader — only for the tables the agent actually
        selects, never for the whole schema.
        """

        if not self.config.wren_live_schema_introspection:
            return []
        introspect = getattr(self.superset_client, "introspect_schema", None)
        if introspect is None:
            return []
        cache = _names_listing_cache(self.config)
        cache_key = (
            request.database_id,
            request.catalog_name,
            request.schema_name,
        )
        if (cached := cache.get(cache_key)) is not None:
            return list(cached)
        try:
            result = introspect(
                database_id=request.database_id,
                catalog_name=request.catalog_name,
                schema_name=request.schema_name,
                limit=max(self.config.wren_introspection_names_limit, 1),
                names_only=True,
            )
            # Defensive: only a real list feeds the context (guards a misbehaving
            # adapter — and MagicMock clients in tests, which auto-return mocks).
            if not isinstance(result, list):
                return []
            # Cache only a non-empty success: a failed/empty listing retries on
            # the next build instead of pinning emptiness for the TTL.
            if result:
                cache.set(cache_key, list(result))
            return result
        except Exception:  # noqa: BLE001  # pylint: disable=broad-except
            logger.warning(
                "Live schema introspection failed for database %s schema %s",
                request.database_id,
                request.schema_name,
                exc_info=True,
            )
            return []
