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

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from superset_ai_agent.semantic_layer.mdl_files import normalize_mdl_path
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    SemanticProject,
    WrenMaterializationResult,
)


def materialize_wren_project(
    *,
    project: SemanticProject,
    mdl_files: list[MdlFile],
    base_path: Path,
) -> WrenMaterializationResult:
    """Materialize active project MDL YAML files and a combined JSON sidecar."""

    active_files = sorted(
        [
            file
            for file in mdl_files
            if file.status == "active" and file.deleted_at is None
        ],
        key=lambda item: item.path,
    )
    project_path = base_path / project.id
    mdl_dir = project_path / "mdl"
    mdl_dir.mkdir(parents=True, exist_ok=True)

    merged: dict[str, Any] = {
        "catalog": project.catalog_name or "default",
        "dataSource": {
            "name": project.database_label or project.name,
            "type": project.database_backend,
            "properties": {
                "superset_database_id": project.default_database_id,
                "semantic_project_id": project.id,
                "schema_name": project.schema_name,
            },
        },
        "models": [],
        "semanticProject": {
            "id": project.id,
            "name": project.name,
            "schema": project.schema_name,
            "catalog": project.catalog_name,
        },
    }
    checksum = hashlib.sha256()
    for file in active_files:
        relative_path = normalize_mdl_path(file.path)
        target_path = mdl_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(file.content, encoding="utf-8")
        checksum.update(relative_path.encode("utf-8"))
        checksum.update(b"\0")
        checksum.update(file.content.encode("utf-8"))
        _merge_mdl_yaml(merged, file.content)

    sidecar_path = project_path / "mdl.json"
    sidecar_path.write_text(
        json.dumps(_drop_none(merged), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    checksum.update(sidecar_path.read_bytes())
    return WrenMaterializationResult(
        project_id=project.id,
        path=str(sidecar_path),
        file_count=len(active_files),
        checksum=checksum.hexdigest(),
    )


def _merge_mdl_yaml(target: dict[str, Any], content: str) -> None:
    payload = yaml.safe_load(content)
    if not isinstance(payload, dict):
        return
    for key in ("models", "semantic_models", "views"):
        value = payload.get(key)
        if isinstance(value, list):
            target.setdefault("models", []).extend(
                item for item in value if isinstance(item, dict)
            )
    for key in ("relationships", "metrics", "enums"):
        value = payload.get(key)
        if isinstance(value, list):
            target.setdefault(key, []).extend(
                item for item in value if isinstance(item, dict)
            )


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if isinstance(item, dict):
            cleaned[key] = _drop_none(item)
        else:
            cleaned[key] = item
    return cleaned
