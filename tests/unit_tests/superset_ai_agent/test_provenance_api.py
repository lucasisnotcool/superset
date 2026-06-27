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

"""MDL provenance timeline: emit on CRUD, read route, delete-on-reset (Feature B)."""

from __future__ import annotations

from tests.unit_tests.superset_ai_agent.test_semantic_layer_api import (
    _client,
    _resolve_project,
    _seed_base_model,
)


def _provenance(client, project_id: str) -> list[dict]:
    response = client.get(
        f"/agent/semantic-layer/projects/{project_id}/provenance"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_create_emits_mdl_created_entry(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)
    pid = project["id"]
    _seed_base_model(client, pid, model="orders", table="orders")

    entries = _provenance(client, pid)
    assert len(entries) == 1
    assert entries[0]["kind"] == "mdl_created"
    assert entries[0]["detail"]["path"] == "models/orders.json"
    assert entries[0]["detail"]["source_type"] == "manual"


def test_activation_and_delete_emit_entries_newest_first(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)
    pid = project["id"]
    # The StaticContextProvider exposes table "moves" with no columns; activation
    # enforces table + column existence, so match that shape.
    created = _seed_base_model(client, pid, model="moves", table="moves", columns=[])

    activate = client.patch(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/{created['id']}",
        json={"status": "active"},
    )
    assert activate.status_code == 200, activate.text
    delete = client.delete(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/{created['id']}"
    )
    assert delete.status_code == 200, delete.text

    entries = _provenance(client, pid)
    # Newest-first: delete, then activate, then create.
    assert [e["kind"] for e in entries] == [
        "mdl_deleted",
        "mdl_activated",
        "mdl_created",
    ]
    activated = entries[1]
    assert activated["detail"]["status_from"] == "draft"
    assert activated["detail"]["status_to"] == "active"


def test_edit_emits_mdl_updated_entry(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)
    pid = project["id"]
    created = _seed_base_model(client, pid, model="orders", table="orders")

    edit = client.patch(
        f"/agent/semantic-layer/projects/{pid}/mdl-files/{created['id']}",
        json={
            "content": (
                '{"models":[{"name":"orders","tableReference":{"table":"orders"},'
                '"columns":[{"name":"stage","type":"varchar"}]}]}'
            )
        },
    )
    assert edit.status_code == 200, edit.text

    entries = _provenance(client, pid)
    assert [e["kind"] for e in entries] == ["mdl_updated", "mdl_created"]


def test_consecutive_user_edits_coalesce_into_one_entry(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)
    pid = project["id"]
    created = _seed_base_model(client, pid, model="orders", table="orders")

    for col in ("stage", "amount", "region"):
        edit = client.patch(
            f"/agent/semantic-layer/projects/{pid}/mdl-files/{created['id']}",
            json={
                "content": (
                    '{"models":[{"name":"orders","tableReference":{"table":"orders"},'
                    f'"columns":[{{"name":"{col}","type":"varchar"}}]}}]}}'
                )
            },
        )
        assert edit.status_code == 200, edit.text

    entries = _provenance(client, pid)
    # Three saves collapse to a single "Edited 3 times" entry; create stands alone.
    assert [e["kind"] for e in entries] == ["mdl_updated", "mdl_created"]
    coalesced = entries[0]
    assert coalesced["edit_count"] == 3
    assert coalesced["summary"] == "Edited 3 times"
    assert coalesced["first_at"] is not None


def test_onboarding_entry_carries_selection_detail(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)
    pid = project["id"]

    onboard = client.post(
        f"/agent/semantic-layer/projects/{pid}/onboard",
        json={"mode": "include", "dataset_ids": [42]},
    )
    assert onboard.status_code == 202, onboard.text

    entries = _provenance(client, pid)
    onboarding = [e for e in entries if e["kind"] == "onboarding"]
    completed = onboarding[0]  # newest-first → completed precedes started
    assert completed["detail"]["mode"] == "selected"
    assert completed["detail"]["dataset_ids"] == [42]
    assert "model_count" in completed["detail"]


def test_reset_purges_provenance(tmp_path) -> None:
    client, _ = _client(tmp_path)
    project = _resolve_project(client)
    pid = project["id"]
    _seed_base_model(client, pid, model="orders", table="orders")
    assert len(_provenance(client, pid)) == 1

    reset = client.post(f"/agent/semantic-layer/projects/{pid}/reset")
    assert reset.status_code == 200, reset.text

    # Provenance is wiped on reset (delete-on-reset).
    assert _provenance(client, pid) == []
