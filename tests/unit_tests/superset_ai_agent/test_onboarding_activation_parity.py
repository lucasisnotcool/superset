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

"""Onboarding activation parity with the manual activation gate.

Onboarding previously auto-activated on per-file ``validate_mdl`` only, so a
file could activate here yet fail the manual route's project-manifest gate on
its next (re)activation — the asymmetry behind "worked at onboarding, cannot
activate now". These tests pin that onboarding runs the SAME whole-manifest
gate (``validate_project_manifest`` + optional deep wren-core pass), strands
offenders as drafts with a warning, and still activates independent siblings.
See plan_mdl_activation_stability_impl.md item C.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract

from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    ColumnSummary,
    DatabaseSummary,
    DatasetMetadata,
)
from superset_ai_agent.semantic_layer.mdl_files import InMemoryMdlFileStore
from superset_ai_agent.semantic_layer.onboarding import onboard_schema_project
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    MdlValidationResult,
    SemanticProject,
)


def _project() -> SemanticProject:
    return SemanticProject(
        name="proj",
        owner_id="owner",
        database_uri_fingerprint="fp",
        schema_name="public",
        schema_names=["public"],
        default_database_id=1,
    )


def _context() -> AgentContext:
    return AgentContext(
        database=DatabaseSummary(id=1, name="examples", backend="postgresql"),
        datasets=[
            DatasetMetadata(
                id=7,
                table_name="orders",
                schema_name="public",
                database_id=1,
                columns=[ColumnSummary(name="amount", type="NUMBER")],
                metrics=[],
            )
        ],
    )


def _proposal(path: str, content: dict) -> MdlEnrichmentProposal:
    return MdlEnrichmentProposal(
        source_document_id="doc",
        proposed_path=path,
        proposed_content=json.dumps(content),
        validation=MdlValidationResult(valid=True, messages=[]),
    )


_ORDERS_MODEL = {
    "models": [
        {
            "name": "orders",
            "tableReference": {"schema": "public", "table": "orders"},
            "columns": [{"name": "amount", "type": "integer"}],
        }
    ]
}

#: Valid as a per-file FRAGMENT (unresolved endpoints are warnings), but the
#: merged project manifest enforces strict relationships — `ghost` never
#: resolves, so the manual activation gate would 422 this file.
_GHOST_RELATIONSHIP = {
    "relationships": [
        {
            "name": "orders_to_ghost",
            "models": ["orders", "ghost"],
            "joinType": "MANY_TO_ONE",
            "condition": "orders.ghost_id = ghost.id",
        }
    ]
}


class _StubWrenClient:
    def __init__(self, proposals: list[MdlEnrichmentProposal]) -> None:
        self.proposals = proposals

    def generate_base_model(self, *, project, superset_context):
        return self.proposals


def _onboard(proposals: list[MdlEnrichmentProposal], **kwargs):
    store = InMemoryMdlFileStore()
    result = onboard_schema_project(
        project=_project(),
        superset_context=_context(),
        wren_client=_StubWrenClient(proposals),
        mdl_file_store=store,
        owner_id="owner",
        **kwargs,
    )
    return result


def test_onboarding_strands_manifest_gate_offender_and_activates_sibling() -> None:
    result = _onboard(
        [
            _proposal("models/orders.json", _ORDERS_MODEL),
            _proposal("models/relationships.json", _GHOST_RELATIONSHIP),
        ]
    )

    by_path = {file.path: file for file in result.files}
    # The independent base model still activates…
    assert by_path["models/orders.json"].status == "active"
    # …but the file the MANUAL gate would 422 stays draft (previously it
    # auto-activated on the lenient per-file pass, poisoning the active set).
    assert by_path["models/relationships.json"].status == "draft"
    assert result.activated_count == 1
    joined = " ".join(result.warnings)
    assert "models/relationships.json" in joined
    assert "activation gate" in joined


def test_onboarding_activates_everything_when_the_projected_set_is_valid() -> None:
    result = _onboard([_proposal("models/orders.json", _ORDERS_MODEL)])

    assert result.activated_count == 1
    assert result.files[0].status == "active"
    assert not [w for w in result.warnings if "activation gate" in w]


def test_onboarding_deep_validate_reaches_the_engine_gate(monkeypatch) -> None:
    from superset_ai_agent.semantic_layer import wren_core_validator

    calls: list[object] = []

    def _fake_deep(models, relationships=None, views=None):
        calls.append(models)
        return MdlValidationResult(valid=True, messages=[])

    monkeypatch.setattr(wren_core_validator, "validate_with_wren_core", _fake_deep)

    _onboard([_proposal("models/orders.json", _ORDERS_MODEL)], deep_validate=True)
    assert calls, "deep_validate=True must invoke the wren-core gate"

    calls.clear()
    _onboard([_proposal("models/orders.json", _ORDERS_MODEL)], deep_validate=False)
    assert not calls, "deep_validate=False must skip the wren-core gate"
