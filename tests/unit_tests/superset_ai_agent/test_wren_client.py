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

import pytest

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.integrations.wren.client import FileWrenClient
from superset_ai_agent.integrations.wren.factory import create_wren_client


def test_file_wren_client_loads_context_and_examples(tmp_path) -> None:
    mdl_path = tmp_path / "mdl.json"
    memory_path = tmp_path / "memory.json"
    mdl_path.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "name": "deals",
                        "description": "Sales deal stages and gross moves",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    memory_path.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "id": "gross-moves-by-stage",
                        "question": "Show gross moves by stage",
                        "sql": "SELECT stage, SUM(gross_moves) FROM deals",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client = FileWrenClient(
        AgentConfig(
            wren_mdl_path=str(mdl_path),
            wren_memory_path=str(memory_path),
        )
    )

    context = client.fetch_context(
        question="Show gross moves by stage",
        superset_context=_agent_context(),
    )

    assert context.enabled is True
    assert context.available is True
    assert context.matched_models == ["deals"]
    assert context.example_ids == ["gross-moves-by-stage"]


def test_file_wren_client_reports_unavailable_without_mdl() -> None:
    client = FileWrenClient(AgentConfig(wren_mdl_path="/does/not/exist.json"))

    context = client.fetch_context(
        question="Show gross moves by stage",
        superset_context=_agent_context(),
    )

    assert context.enabled is True
    assert context.available is False
    assert context.warnings


def test_wren_factory_rejects_execution_enabled() -> None:
    with pytest.raises(ValueError, match="Wren execution is not supported"):
        create_wren_client(AgentConfig(wren_execution_enabled=True))


def _agent_context() -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="examples", backend="sqlite"),
        datasets=[
            DatasetMetadata(
                id=1,
                table_name="deals",
                database_id=1,
                columns=[
                    ColumnSummary(name="stage", type="VARCHAR"),
                    ColumnSummary(name="gross_moves", type="BIGINT"),
                ],
                metrics=[],
            )
        ],
    )
