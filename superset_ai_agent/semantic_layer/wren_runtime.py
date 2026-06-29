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

from pathlib import Path

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.projects import SemanticProjectStore
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.wren_materializer import (
    materialize_wren_project,
)


def materialize_request_semantic_project(
    *,
    config: AgentConfig,
    semantic_project_store: SemanticProjectStore | None,
    mdl_file_store: MdlFileStore | None,
    owner_id: str = DEFAULT_OWNER_ID,
    database_id: int,
    catalog_name: str | None,
    schema_name: str | None,
    project_id: str | None = None,
) -> tuple[SemanticProject, WrenMaterializationResult, list[str]] | None:
    """Materialize the semantic project that grounds an agent request.

    ``project_id`` is an optional explicit pin (from the request scope or a
    conversation). It is honored only when it appears in the access- and
    schema-filtered candidate set returned by ``store.list`` — so a client can
    never name a project it lacks access to (visibility/owner filter) or one
    that does not cover the requested schema (schema-membership filter). When
    the pin is unavailable (unauthorized, wrong schema, archived/deleted) the
    resolver falls back to the most-recently-updated match and returns a warning
    for the caller to surface. Returns ``(project, materialization, warnings)``.
    """

    if semantic_project_store is None or mdl_file_store is None or schema_name is None:
        return None
    projects = semantic_project_store.list(
        owner_id=owner_id,
        database_id=database_id,
        catalog_name=catalog_name,
        schema_name=schema_name,
    )
    if not projects:
        return None
    warnings: list[str] = []
    project = projects[0]
    if project_id is not None and project_id != project.id:
        match = next((item for item in projects if item.id == project_id), None)
        if match is not None:
            project = match
        else:
            warnings.append(
                f"Requested semantic project is unavailable for this schema; "
                f"grounding on '{project.name}' instead."
            )
    mdl_files = mdl_file_store.list(project.id, owner_id=owner_id)
    materialization = materialize_wren_project(
        project=project,
        mdl_files=mdl_files,
        base_path=_wren_materialization_base(config),
    )
    return project, materialization, warnings


def _wren_materialization_base(config: AgentConfig) -> Path:
    if config.wren_project_path:
        return Path(config.wren_project_path)
    return Path(config.agent_storage_dir) / "wren"
