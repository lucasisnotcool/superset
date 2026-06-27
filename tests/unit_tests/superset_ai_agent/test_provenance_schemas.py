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

from datetime import datetime, timedelta, timezone

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.semantic_layer.schemas import (
    actor_type_for,
    coalesce_user_runs,
    OnboardingRequest,
    ProvenanceEntry,
    provenance_from_event,
    SemanticLayerEvent,
)

_BASE = datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc)


def _entry(
    entry_id: str,
    actor_type: str,
    minutes: int,
    *,
    kind: str = "mdl_updated",
    paths: list[str] | None = None,
) -> ProvenanceEntry:
    detail: dict[str, object] = {}
    if paths is not None:
        detail["paths"] = paths
    return ProvenanceEntry(
        id=entry_id,
        kind=kind,  # type: ignore[arg-type]
        summary=f"event {entry_id}",
        created_at=_BASE + timedelta(minutes=minutes),
        actor_type=actor_type,  # type: ignore[arg-type]
        detail=detail,
    )


def _event(event_type: str, **kwargs: object) -> SemanticLayerEvent:
    return SemanticLayerEvent(
        project_id="proj-1",
        type=event_type,  # type: ignore[arg-type]
        scope=ConversationScope(database_id=1),
        message=kwargs.pop("message", "msg"),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_onboarding_request_defaults_to_whole_schema() -> None:
    request = OnboardingRequest()
    assert request.mode == "all"
    assert request.dataset_ids == []
    assert request.exclude_dataset_ids == []
    assert request.search is None


def test_onboarding_request_include_round_trips() -> None:
    request = OnboardingRequest.model_validate(
        {"mode": "include", "dataset_ids": [1, 2, 3]}
    )
    assert request.mode == "include"
    assert request.dataset_ids == [1, 2, 3]


def test_event_detail_round_trips() -> None:
    event = _event("mdl_created", detail={"path": "models/orders.json", "file_id": "f1"})
    restored = SemanticLayerEvent.model_validate(event.model_dump(mode="json"))
    assert restored.detail == {"path": "models/orders.json", "file_id": "f1"}


def test_provenance_mapping_covers_kinds_and_status() -> None:
    created = provenance_from_event(
        _event("mdl_created", detail={"path": "models/o.json", "actor": "user-1"})
    )
    assert created is not None
    assert created.kind == "mdl_created"
    assert created.status == "ok"
    assert created.actor == "user-1"
    assert created.detail["path"] == "models/o.json"

    activated = provenance_from_event(_event("mdl_activated"))
    assert activated is not None and activated.kind == "mdl_activated"

    failed = provenance_from_event(_event("onboarding_failed"))
    assert failed is not None
    assert failed.kind == "onboarding"
    assert failed.status == "error"

    warned = provenance_from_event(
        _event("onboarding_completed", detail={"warnings": ["x"]})
    )
    assert warned is not None and warned.status == "warning"

    enriched = provenance_from_event(_event("document_enriched"))
    assert enriched is not None and enriched.kind == "enrichment"


def test_non_provenance_events_map_to_none() -> None:
    assert provenance_from_event(_event("document_uploaded")) is None
    assert provenance_from_event(_event("document_extracted")) is None


def test_actor_type_classification() -> None:
    # Hand edits → user (the only kind that coalesces).
    assert actor_type_for("mdl_updated", "manual") == "user"
    assert actor_type_for("mdl_created", "uploaded_mdl") == "user"
    # Agent-authored files / agent kinds → agent.
    assert actor_type_for("mdl_updated", "copilot") == "agent"
    assert actor_type_for("copilot_edit", "copilot") == "agent"
    assert actor_type_for("enrichment", None) == "agent"
    assert actor_type_for("mdl_created", "enriched_markdown") == "agent"
    # Bulk / background origins → system.
    assert actor_type_for("onboarding", None) == "system"
    assert actor_type_for("coverage", None) == "system"
    assert actor_type_for("mdl_activated", "onboarding") == "system"
    # Manual-CRUD with missing source_type defaults to user.
    assert actor_type_for("mdl_updated", None) == "user"


def test_provenance_sets_actor_type_for_new_kinds() -> None:
    agent_edit = provenance_from_event(
        _event("mdl_agent_edit", detail={"source_type": "copilot", "actor": "u1"})
    )
    assert agent_edit is not None
    assert agent_edit.kind == "copilot_edit"
    assert agent_edit.actor_type == "agent"
    assert agent_edit.edit_count == 1
    assert agent_edit.first_at is None

    coverage = provenance_from_event(
        _event("coverage_completed", detail={"score": 0.8, "run_id": "r1"})
    )
    assert coverage is not None
    assert coverage.kind == "coverage"
    assert coverage.actor_type == "system"

    user_edit = provenance_from_event(
        _event("mdl_updated", detail={"source_type": "manual"})
    )
    assert user_edit is not None and user_edit.actor_type == "user"


def test_coalesce_collapses_consecutive_user_edits() -> None:
    # Two user edits with nothing between → one entry stamped at the latest time.
    entries = [
        _entry("late", "user", 60, paths=["models/a.json"]),
        _entry("early", "user", 0, paths=["models/b.json"]),
    ]
    coalesced = coalesce_user_runs(entries)
    assert len(coalesced) == 1
    row = coalesced[0]
    assert row.edit_count == 2
    assert row.created_at == _BASE + timedelta(minutes=60)
    assert row.first_at == _BASE
    assert row.summary == "Edited 2 times"
    assert row.detail["paths"] == ["models/a.json", "models/b.json"]


def test_coalesce_agent_entry_breaks_the_run() -> None:
    # user(5pm), agent, user(2pm) — the worked example: three distinct entries.
    entries = [
        _entry("user-late", "user", 120),
        _entry("agent", "agent", 60, kind="copilot_edit"),
        _entry("user-early", "user", 0),
    ]
    coalesced = coalesce_user_runs(entries)
    assert [e.id for e in coalesced] == ["user-late", "agent", "user-early"]
    assert all(e.edit_count == 1 for e in coalesced)


def test_coalesce_single_user_edit_unchanged() -> None:
    coalesced = coalesce_user_runs([_entry("solo", "user", 0)])
    assert len(coalesced) == 1
    assert coalesced[0].edit_count == 1
    assert coalesced[0].first_at is None


def test_coalesce_preserves_newest_first_order_with_system_entries() -> None:
    entries = [
        _entry("u3", "user", 50),
        _entry("u2", "user", 40),
        _entry("onb", "system", 30, kind="onboarding"),
        _entry("u1", "user", 10),
        _entry("cov", "system", 5, kind="coverage"),
    ]
    coalesced = coalesce_user_runs(entries)
    assert [e.id for e in coalesced] == ["u3", "onb", "u1", "cov"]
    assert coalesced[0].edit_count == 2  # u3 + u2 merged
    assert coalesced[2].edit_count == 1  # u1 alone
