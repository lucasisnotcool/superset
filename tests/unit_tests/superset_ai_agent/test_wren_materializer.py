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

import json

from superset_ai_agent.semantic_layer.schemas import MdlFile, SemanticProject
from superset_ai_agent.semantic_layer.wren_materializer import (
    materialize_wren_project,
)


def test_materialize_wren_project_writes_active_yaml_and_json(tmp_path) -> None:
    project = SemanticProject(
        id="project-1",
        name="Sales.pipeline",
        owner_id="owner",
        database_uri_fingerprint="fingerprint",
        database_label="Sales",
        database_backend="postgresql",
        schema_name="pipeline",
        default_database_id=7,
    )
    active_file = MdlFile(
        project_id=project.id,
        path="models/gross_moves.yaml",
        filename="gross_moves.yaml",
        content="models:\n  - name: gross_moves\n",
        status="active",
        checksum="checksum",
    )
    draft_file = MdlFile(
        project_id=project.id,
        path="models/draft.yaml",
        filename="draft.yaml",
        content="models:\n  - name: draft\n",
        status="draft",
        checksum="checksum",
    )

    result = materialize_wren_project(
        project=project,
        mdl_files=[draft_file, active_file],
        base_path=tmp_path,
    )

    assert result.file_count == 1
    assert (tmp_path / project.id / "mdl" / "models" / "gross_moves.yaml").exists()
    assert not (tmp_path / project.id / "mdl" / "models" / "draft.yaml").exists()
    payload = json.loads((tmp_path / project.id / "mdl.json").read_text())
    assert payload["models"] == [{"name": "gross_moves"}]
    assert payload["dataSource"]["properties"]["semantic_project_id"] == project.id
