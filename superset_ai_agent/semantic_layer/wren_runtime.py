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
from superset_ai_agent.schemas import normalize_schema_names
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.projects import (
    SemanticProjectNotFoundError,
    SemanticProjectStore,
)
from superset_ai_agent.semantic_layer.schemas import (
    SemanticProject,
    WrenMaterializationResult,
)
from superset_ai_agent.semantic_layer.wren_materializer import (
    materialize_wren_project,
)


def resolve_effective_schema(
    *,
    semantic_project_store: SemanticProjectStore | None,
    owner_id: str = DEFAULT_OWNER_ID,
    database_id: int,
    schema_name: str | None,
    project_id: str | None,
) -> tuple[str | None, list[str]]:
    """Resolve the schema scope for a query, **project-wins** over the tab schema.

    The frontend sets ``schema_name`` from the SQL Lab tab, not the project — so a
    user who pins a project in the AI panel but has no tab schema would otherwise
    send ``schema_name=None``. A :class:`SemanticProject` already declares its
    schema set, so a pinned project is the source of truth: it overrides the tab
    schema and, for a multi-schema project, yields the **full** set.

    Returns ``(primary, full_set)``: ``primary`` (back-compat scalar, ``None`` when
    nothing resolved) and the ordered, de-duplicated schema set. Degrades closed to
    the passed schema when there is no store/pin, the pin is unresolvable, or the
    project belongs to a different database (never infer onto the wrong DB).
    Selects *context*, not *access* — the per-schema context-load stays
    Superset-gated, so this never widens what a user can reach.
    """

    passthrough = (schema_name, normalize_schema_names(schema_name, None))
    if semantic_project_store is None or project_id is None:
        return passthrough
    try:
        project = semantic_project_store.get(project_id, owner_id=owner_id)
    except SemanticProjectNotFoundError:
        return passthrough
    except Exception:  # pylint: disable=broad-except - degrade to the tab schema
        return passthrough
    if project is None:
        return passthrough
    # DB guard: a project pinned for a different database must not infer its schema
    # onto this request (avoid grounding on the wrong DB).
    if (
        project.default_database_id is not None
        and project.default_database_id != database_id
    ):
        return passthrough
    return project.schema_name, normalize_schema_names(
        project.schema_name, project.schema_names
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

    if semantic_project_store is None or mdl_file_store is None:
        return None
    if schema_name is None:
        # No tab schema: infer it from the pinned project (project-wins) instead of
        # failing the whole semantic layer. ``store.list`` below is schema-filtered,
        # so we need a concrete schema to find the project's candidate set.
        if project_id is None:
            return None
        schema_name, _ = resolve_effective_schema(
            semantic_project_store=semantic_project_store,
            owner_id=owner_id,
            database_id=database_id,
            schema_name=None,
            project_id=project_id,
        )
        if schema_name is None:
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
