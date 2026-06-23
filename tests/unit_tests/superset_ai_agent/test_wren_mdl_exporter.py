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

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
    MetricSummary,
)
from superset_ai_agent.integrations.wren.mdl_exporter import (
    export_agent_context_to_mdl,
    write_mdl,
)


def _context() -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="examples", backend="sqlite"),
        datasets=[
            DatasetMetadata(
                id=42,
                table_name="gross moves",
                schema_name="main",
                database_id=1,
                description="Opportunity movement by stage.",
                columns=[
                    ColumnSummary(
                        name="stage",
                        type="VARCHAR",
                        description="Sales stage.",
                    ),
                    ColumnSummary(name="created_at", type="DATETIME", is_dttm=True),
                ],
                metrics=[
                    MetricSummary(
                        name="gross moves",
                        expression="COUNT(*)",
                        description="Gross movement count.",
                    )
                ],
            )
        ],
    )


def test_export_agent_context_to_mdl_maps_superset_metadata() -> None:
    mdl = export_agent_context_to_mdl(
        _context(),
        semantic_overlay={"semantic_updates": [{"id": "update-1"}]},
    )

    assert mdl["dataSource"]["properties"]["superset_database_id"] == 1
    assert mdl["models"][0]["name"] == "gross_moves"
    assert mdl["models"][0]["tableReference"] == {
        "schema": "main",
        "table": "gross moves",
    }
    assert mdl["models"][0]["columns"][0]["name"] == "stage"
    assert mdl["models"][0]["columns"][0]["isCalculated"] is False
    assert mdl["models"][0]["columns"][1]["properties"]["is_time"] is True
    assert mdl["models"][0]["metrics"][0]["expression"] == "COUNT(*)"
    assert mdl["semanticOverlay"]["semantic_updates"][0]["id"] == "update-1"


def test_write_mdl_writes_json(tmp_path) -> None:
    output_path = tmp_path / "wren" / "mdl.json"

    write_mdl(_context(), output_path)

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["models"][0]["properties"]["superset_dataset_id"] == 42
