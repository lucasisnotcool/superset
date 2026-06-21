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

import json  # noqa: TID251 - standalone agent metadata serialization
import re
from dataclasses import dataclass

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatasetMetadata,
)
from superset_ai_agent.schemas import AgentQueryRequest, WrenRetrievalArtifact


@dataclass(frozen=True)
class RetrievedContext:
    """Ranked context and metadata for one schema-scoped agent run."""

    context: AgentContext
    retrieval: WrenRetrievalArtifact


def retrieve_schema_context(
    *,
    request: AgentQueryRequest,
    context: AgentContext,
    config: AgentConfig,
    project_id: str | None = None,
) -> RetrievedContext:
    """Return a bounded top-k context for the selected schema."""

    if request.dataset_ids or not request.schema_name:
        retrieval = _retrieval_artifact(
            request=request,
            context=context,
            project_id=project_id,
            selected_datasets=context.datasets,
            context_truncated=False,
        )
        return RetrievedContext(context=context, retrieval=retrieval)

    ranked = _rank_datasets(
        question=request.question,
        datasets=context.datasets,
    )
    selected = [
        dataset
        for _, dataset in ranked[: max(config.wren_schema_table_candidate_limit, 1)]
    ]
    selected, context_truncated = _fit_token_budget(
        selected,
        token_budget=max(config.wren_schema_context_token_budget, 1),
    )
    retrieval = _retrieval_artifact(
        request=request,
        context=context,
        project_id=project_id,
        selected_datasets=selected,
        context_truncated=context_truncated,
    )
    return RetrievedContext(
        context=context.model_copy(update={"datasets": selected}),
        retrieval=retrieval,
    )


def _rank_datasets(
    *,
    question: str,
    datasets: list[DatasetMetadata],
) -> list[tuple[int, DatasetMetadata]]:
    question_tokens = set(_tokens(question))
    ranked: list[tuple[int, int, DatasetMetadata]] = []
    for index, dataset in enumerate(datasets):
        score = _dataset_score(question_tokens, dataset)
        ranked.append((score, index, dataset))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2].table_name))
    return [(score, dataset) for score, _, dataset in ranked]


def _dataset_score(
    question_tokens: set[str],
    dataset: DatasetMetadata,
) -> int:
    if not question_tokens:
        return 0
    score = 0
    table_tokens = set(_tokens(dataset.table_name))
    score += 5 * len(question_tokens & table_tokens)
    if dataset.schema_name:
        score += len(question_tokens & set(_tokens(dataset.schema_name)))
    if dataset.description:
        score += len(question_tokens & set(_tokens(dataset.description)))
    for column in dataset.columns:
        score += 3 * len(question_tokens & set(_tokens(column.name)))
        if column.description:
            score += len(question_tokens & set(_tokens(column.description)))
    for metric in dataset.metrics:
        score += 4 * len(question_tokens & set(_tokens(metric.name)))
        if metric.expression:
            score += len(question_tokens & set(_tokens(metric.expression)))
        if metric.description:
            score += len(question_tokens & set(_tokens(metric.description)))
    return score


def _fit_token_budget(
    datasets: list[DatasetMetadata],
    *,
    token_budget: int,
) -> tuple[list[DatasetMetadata], bool]:
    selected: list[DatasetMetadata] = []
    used = 0
    truncated = False
    for dataset in datasets:
        token_count = _estimate_tokens(dataset)
        if selected and used + token_count > token_budget:
            truncated = True
            break
        selected.append(dataset)
        used += token_count
    if not selected and datasets:
        return [datasets[0]], True
    return selected, truncated or len(selected) < len(datasets)


def _retrieval_artifact(
    *,
    request: AgentQueryRequest,
    context: AgentContext,
    project_id: str | None,
    selected_datasets: list[DatasetMetadata],
    context_truncated: bool,
) -> WrenRetrievalArtifact:
    metric_names: list[str] = []
    for dataset in selected_datasets:
        metric_names.extend(metric.name for metric in dataset.metrics)
    selected_names = [dataset.table_name for dataset in selected_datasets]
    return WrenRetrievalArtifact(
        project_id=project_id,
        database_id=request.database_id,
        catalog_name=request.catalog_name,
        schema_name=request.schema_name,
        candidate_table_names=selected_names,
        candidate_metric_names=metric_names,
        scanned_table_count=len(context.datasets),
        omitted_table_count=max(len(context.datasets) - len(selected_datasets), 0),
        context_truncated=context_truncated,
    )


def _estimate_tokens(dataset: DatasetMetadata) -> int:
    payload = dataset.model_dump(mode="json")
    text = json.dumps(payload, default=str)
    return max(len(text) // 4, 1)


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^A-Za-z0-9]+", value.lower())
        if len(token) >= 2
    ]
