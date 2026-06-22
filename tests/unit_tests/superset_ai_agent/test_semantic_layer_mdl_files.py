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

import pytest

from superset_ai_agent.semantic_layer.mdl_files import (
    InMemoryMdlFileStore,
    MdlFileValidationError,
    normalize_mdl_path,
)
from superset_ai_agent.semantic_layer.mdl_validation import validate_mdl_yaml
from superset_ai_agent.semantic_layer.schemas import (
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
)


def test_mdl_validation_accepts_object_yaml() -> None:
    result = validate_mdl_yaml(
        "models:\n"
        "  - name: gross_moves\n"
        "    table_reference:\n"
        "      table: gross_moves\n"
        "    columns:\n"
        "      - name: stage\n"
    )

    assert result.valid is True
    assert result.messages == []


def test_mdl_validation_accepts_minimal_model_with_warnings() -> None:
    result = validate_mdl_yaml("models:\n  - name: gross_moves\n")

    # Structurally valid, but warns that the model has no mapping or columns.
    assert result.valid is True
    assert {message.code for message in result.messages} == {
        "model_without_mapping",
        "model_without_columns",
    }


def test_mdl_validation_reports_parse_errors() -> None:
    result = validate_mdl_yaml("models:\n - [")

    assert result.valid is False
    assert result.messages[0].code == "yaml_parse_error"
    assert result.messages[0].line == 2


def test_cannot_activate_structurally_invalid_mdl_file() -> None:
    store = InMemoryMdlFileStore()
    file = store.create(
        "project-1",
        MdlFileCreateRequest(
            path="models/bad.yaml",
            content=(
                "models:\n"
                "  - name: deals\n"
                "    table_reference:\n"
                "      table: deals\n"
                "    columns:\n"
                "      - name: stage\n"
                "relationships:\n"
                "  - name: r\n"
                "    models: [deals, sites]\n"
                "    join_type: SIDEWAYS\n"
            ),
        ),
        owner_id="owner",
    )
    assert file.validation is not None
    assert file.validation.valid is False

    with pytest.raises(MdlFileValidationError):
        store.update(
            file.id,
            MdlFileUpdateRequest(status="active"),
            owner_id="owner",
        )


def test_normalize_mdl_path_rejects_unsafe_paths() -> None:
    assert normalize_mdl_path("models/gross_moves.yaml") == "models/gross_moves.yaml"
    with pytest.raises(ValueError):
        normalize_mdl_path("../gross_moves.yaml")
    with pytest.raises(ValueError):
        normalize_mdl_path("gross_moves.md")


def test_mdl_file_store_round_trips_and_soft_deletes() -> None:
    store = InMemoryMdlFileStore()
    file = store.create(
        "project-1",
        MdlFileCreateRequest(
            path="models/gross_moves.yaml",
            content="models:\n  - name: gross_moves\n",
        ),
        owner_id="owner",
    )

    assert file.validation is not None
    assert file.validation.valid is True
    assert store.list("project-1", owner_id="analyst")[0].id == file.id

    updated = store.update(
        file.id,
        MdlFileUpdateRequest(
            content="models:\n  - name: gross_moves_by_stage\n",
            status="active",
        ),
        owner_id="analyst",
    )

    assert updated.status == "active"
    assert updated.updated_by == "analyst"
    assert store.validate(file.id, owner_id="owner").valid is True

    store.delete(file.id, owner_id="owner")
    assert store.list("project-1", owner_id="owner") == []
