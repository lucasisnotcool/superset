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

from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.semantic_layer.schemas import (
    OnboardingRequest,
    provenance_from_event,
    SemanticLayerEvent,
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
