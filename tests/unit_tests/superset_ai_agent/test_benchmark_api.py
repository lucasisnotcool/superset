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

"""Project Benchmarks HTTP routes: CRUD, dry-run, flywheel (F11 / P1.1-P1.2)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

from fastapi.testclient import TestClient

from superset_ai_agent.app import create_app
from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.memory import InMemoryConversationStore
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.schemas import (
    AgentQueryRequest,
    ExecutionResult,
    ModelInfo,
)
from superset_ai_agent.semantic_layer.file_storage import LocalDocumentStorage
from superset_ai_agent.semantic_layer.jobs import InlineJobRunner
from superset_ai_agent.semantic_layer.memory import InMemorySemanticLayerStore

GOLD_SQL = "SELECT region, revenue FROM sales"


class _ContextProvider:
    def get_context(self, request: AgentQueryRequest) -> AgentContext:
        return AgentContext(
            database=DatabaseSummary(id=request.database_id, name="examples"),
            datasets=[
                DatasetMetadata(
                    id=42,
                    table_name="sales",
                    database_id=request.database_id,
                    columns=[ColumnSummary(name="region", type="TEXT")],
                    metrics=[],
                )
            ],
        )


class _Model:
    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name="test-model")]

    def chat(self, messages: Any, **kwargs: Any) -> Any:
        raise AssertionError("Benchmark CRUD must not call the model.")


class FakeSupersetClient:
    """Executes gold SQL from a canned map; raises for unknown SQL."""

    def __init__(self, results: dict[str, ExecutionResult] | None = None) -> None:
        self.results = results or {}
        self.executed: list[str] = []

    def execute_sql(
        self, *, database_id: int, sql: str, **kwargs: Any
    ) -> ExecutionResult:
        self.executed.append(sql)
        if sql in self.results:
            return self.results[sql]
        raise RuntimeError(f"no canned result for: {sql}")

    def get_database_dialect(self, database_id: int) -> str | None:
        return "postgresql"


def make_client(
    tmp_path,
    *,
    enabled: bool = True,
    superset_client: Any | None = None,
    text_to_sql_graph: Any | None = None,
    eval_store: Any | None = None,
    model_client: Any | None = None,
    **config_overrides: Any,
) -> TestClient:
    app = create_app(
        config=AgentConfig(
            identity_provider="static",
            superset_auth_mode="service_account",
            conversation_store="memory",
            semantic_layer_store="memory",
            wren_engine="passthrough",
            wren_core_validation_enabled=False,
            wren_copilot_enabled=True,
            wren_benchmarks_enabled=enabled,
            agent_storage_dir=str(tmp_path),
            **config_overrides,
        ),
        model_client=model_client or _Model(),
        text_to_sql_graph=text_to_sql_graph or object(),
        conversation_graph=object(),
        conversation_store=InMemoryConversationStore(),
        semantic_layer_store=InMemorySemanticLayerStore(),
        document_storage=LocalDocumentStorage(str(tmp_path)),
        context_provider=_ContextProvider(),
        superset_client=superset_client,
        job_runner=InlineJobRunner(),
        eval_store=eval_store,
    )
    return TestClient(app)


def resolve_project(client: TestClient) -> str:
    response = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={"database_id": 1, "database_label": "Sales", "schema_name": "public"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def create_benchmark(client: TestClient, pid: str, name: str = "Core") -> str:
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks",
        json={"name": name},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def add_gold_item(
    client: TestClient, pid: str, bid: str, question: str = "Revenue by region?"
) -> str:
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": question,
            "answer_type": "gold_sql",
            "answer_spec": {"sql": GOLD_SQL},
            "capability_tags": ["metric"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_benchmark_crud_round_trip(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)

    bid = create_benchmark(client, pid, name="Core questions")
    listing = client.get(f"/agent/semantic-layer/projects/{pid}/benchmarks")
    assert [b["name"] for b in listing.json()] == ["Core questions"]

    renamed = client.patch(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}",
        json={"name": "Renamed"},
    )
    assert renamed.json()["name"] == "Renamed"

    deleted = client.delete(f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}")
    assert deleted.json() == {"deleted": True}
    assert client.get(f"/agent/semantic-layer/projects/{pid}/benchmarks").json() == []


def test_flag_off_hides_the_surface(tmp_path) -> None:
    client = make_client(tmp_path, enabled=False)
    pid = resolve_project(client)
    response = client.get(f"/agent/semantic-layer/projects/{pid}/benchmarks")
    assert response.status_code == 403


def test_benchmark_of_other_project_is_404(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    other = client.post(
        "/agent/semantic-layer/projects/resolve",
        json={"database_id": 1, "database_label": "Sales", "schema_name": "other"},
    ).json()["id"]
    response = client.get(
        f"/agent/semantic-layer/projects/{other}/benchmarks/{bid}/items"
    )
    assert response.status_code == 404


def test_item_crud_and_validation(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)

    item_id = add_gold_item(client, pid, bid)
    items = client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items"
    ).json()
    assert len(items) == 1
    assert items[0]["answer_type"] == "gold_sql"

    updated = client.patch(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/{item_id}",
        json={"question": "Total revenue per region?", "verified": True},
    )
    assert updated.status_code == 200
    assert updated.json()["question"] == "Total revenue per region?"
    assert updated.json()["verified_by"]

    deleted = client.delete(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/{item_id}"
    )
    assert deleted.json() == {"deleted": True}


def test_mutating_gold_sql_is_rejected(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "Drop it?",
            "answer_type": "gold_sql",
            "answer_spec": {"sql": "DROP TABLE sales"},
        },
    )
    assert response.status_code == 422
    assert "read-only" in response.json()["detail"]


def test_invalid_expected_values_spec_is_rejected(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "How many?",
            "answer_type": "expected_values",
            "answer_spec": {"nums": ["not-a-number"]},
        },
    )
    assert response.status_code == 422


def test_item_cap_is_enforced(tmp_path, monkeypatch) -> None:
    import superset_ai_agent.app as app_module

    monkeypatch.setattr(app_module, "MAX_ITEMS_PER_BENCHMARK", 1)
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    add_gold_item(client, pid, bid)
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "Second?",
            "answer_type": "gold_sql",
            "answer_spec": {"sql": GOLD_SQL},
        },
    )
    assert response.status_code == 409


def test_dry_run_gold_sql_previews_rows(tmp_path) -> None:
    superset = FakeSupersetClient(
        results={
            GOLD_SQL: ExecutionResult(
                columns=["region", "revenue"],
                rows=[{"region": "emea", "revenue": 10}],
                row_count=1,
            )
        }
    )
    client = make_client(tmp_path, superset_client=superset)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    item_id = add_gold_item(client, pid, bid)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/{item_id}/dry-run"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["region", "revenue"]
    assert body["rows"] == [{"region": "emea", "revenue": 10}]


def test_dry_run_surfaces_gold_sql_errors_as_400(tmp_path) -> None:
    client = make_client(tmp_path, superset_client=FakeSupersetClient())
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    item_id = add_gold_item(client, pid, bid)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/{item_id}/dry-run"
    )
    assert response.status_code == 400
    assert "failed to execute" in response.json()["detail"]


def test_dry_run_expected_values_echoes_spec(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    created = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "How many drives?",
            "answer_type": "expected_values",
            "answer_spec": {"nums": [42]},
        },
    )
    item_id = created.json()["id"]
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items/{item_id}/dry-run"
    )
    assert response.status_code == 200
    assert response.json()["spec"] == {"nums": [42]}
    assert response.json()["problems"] == []


def test_promote_item_writes_golden_file_and_flags_item(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    item_id = add_gold_item(client, pid, bid)

    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/items/{item_id}/promote-example"
    )
    assert response.status_code == 200, response.text
    assert response.json()["use_as_example"] is True

    files = client.get(f"/agent/semantic-layer/projects/{pid}/mdl-files").json()
    golden = [f for f in files if f["path"].endswith("queries.json")]
    assert len(golden) == 1
    content = json.loads(golden[0]["content"])
    assert content["queries"][0]["question"] == "Revenue by region?"


def test_promote_rejects_non_gold_items(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    created = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}/items",
        json={
            "question": "Vibes ok?",
            "answer_type": "eval_note",
            "answer_spec": {"note": "sensible tone"},
        },
    )
    response = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/items/{created.json()['id']}/promote-example"
    )
    assert response.status_code == 400


def test_import_golden_creates_items_and_dedupes(tmp_path) -> None:
    client = make_client(tmp_path)
    pid = resolve_project(client)
    bid = create_benchmark(client, pid)
    item_id = add_gold_item(client, pid, bid)  # promoted → golden file exists
    client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{bid}"
        f"/items/{item_id}/promote-example"
    )

    # A second, empty benchmark imports the golden set.
    other_bid = create_benchmark(client, pid, name="Imported")
    imported = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{other_bid}/import-golden"
    )
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"created": 1, "skipped_duplicates": 0}
    items = client.get(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{other_bid}/items"
    ).json()
    assert items[0]["use_as_example"] is True
    assert items[0]["capability_tags"] == ["golden"]

    # Re-import is idempotent.
    again = client.post(
        f"/agent/semantic-layer/projects/{pid}/benchmarks/{other_bid}/import-golden"
    )
    assert again.json() == {"created": 0, "skipped_duplicates": 1}
