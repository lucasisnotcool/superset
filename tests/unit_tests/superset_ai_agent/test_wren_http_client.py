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

import json  # noqa: TID251 - tests cover standalone adapter JSON payloads

import httpx

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.conversations.schemas import ConversationScope
from superset_ai_agent.integrations.superset.client import (
    AgentContext,
    DatabaseSummary,
)
from superset_ai_agent.integrations.wren.factory import create_wren_client
from superset_ai_agent.integrations.wren.http_client import WrenHttpClient
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticProject,
)


def test_wren_http_client_fetches_context_examples_and_dry_plan() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer wren-key"
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/models":
            return httpx.Response(200, json={"models": [{"name": "moves"}]})
        if request.url.path == "/context":
            body = json.loads(request.content)
            assert body["mdl_path"] == "/tmp/project/mdl.json"
            assert body["superset_context"]["database"]["id"] == 1
            return httpx.Response(
                200,
                json={
                    "result": {
                        "available": True,
                        "matched_models": ["moves"],
                        "example_ids": ["example-1"],
                        "document_ids": ["document-1"],
                        "context_items": [{"type": "model", "name": "moves"}],
                    }
                },
            )
        if request.url.path == "/examples":
            return httpx.Response(
                200,
                json={"examples": [{"id": "example-1", "question": "Show moves"}]},
            )
        if request.url.path == "/dry-plan":
            body = json.loads(request.content)
            assert body["execution"] == "disabled"
            return httpx.Response(
                200,
                json={"result": {"available": True, "steps": ["plan"]}},
            )
        if request.url.path == "/mdl/validate":
            body = json.loads(request.content)
            assert body["execution"] == "disabled"
            return httpx.Response(200, json={"result": {"valid": True}})
        return httpx.Response(404, text=request.url.path)

    client = WrenHttpClient(
        AgentConfig(
            wren_adapter="http",
            wren_base_url="http://wren.local",
            wren_api_key="wren-key",
        ),
        transport=httpx.MockTransport(handler),
    )

    assert client.is_available() is True
    assert client.list_models() == ["moves"]
    context = client.fetch_context(
        question="Show gross moves by stage",
        superset_context=_agent_context(),
        mdl_path="/tmp/project/mdl.json",
    )
    assert context.available is True
    assert context.matched_models == ["moves"]
    assert context.document_ids == ["document-1"]
    assert client.recall_examples(question="Show moves", limit=1)[0]["id"] == (
        "example-1"
    )
    assert client.dry_plan(
        question="Show moves",
        sql="select 1",
        context=_agent_context(),
        mdl_path="/tmp/project/mdl.json",
    )["execution"] == "disabled"
    assert client.validate_mdl_project(mdl_path="/tmp/project/mdl.json")[
        "execution"
    ] == "disabled"
    assert [request.url.path for request in requests] == [
        "/health",
        "/models",
        "/context",
        "/examples",
        "/dry-plan",
        "/mdl/validate",
    ]


def test_wren_http_client_proposes_mdl_from_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/documents/propose-mdl"
        body = json.loads(request.content)
        assert body["project"]["id"] == "project-1"
        assert body["document"]["id"] == "document-1"
        return httpx.Response(
            200,
            json={
                "result": {
                    "proposed_path": "models/moves.yaml",
                    "proposed_yaml": "models:\n  - name: moves\n",
                    "warnings": ["review before activation"],
                }
            },
        )

    client = WrenHttpClient(
        AgentConfig(
            wren_adapter="http",
            wren_base_url="http://wren.local",
            wren_onboarding_enabled=True,
        ),
        transport=httpx.MockTransport(handler),
    )

    proposal = client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
    )

    assert proposal.proposed_path == "models/moves.yaml"
    assert proposal.proposed_yaml == "models:\n  - name: moves\n"
    assert proposal.validation.valid is True
    assert proposal.warnings == ["review before activation"]


def test_wren_http_client_falls_back_when_onboarding_disabled() -> None:
    client = WrenHttpClient(
        AgentConfig(
            wren_adapter="http",
            wren_base_url="http://wren.local",
            wren_onboarding_enabled=False,
        ),
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    proposal = client.propose_mdl_from_document(
        project=_project(),
        document=_document(),
    )

    assert proposal.source_document_id == "document-1"
    assert "Wren onboarding is disabled" in " ".join(proposal.warnings)


def test_wren_factory_creates_http_adapter() -> None:
    client = create_wren_client(
        AgentConfig(
            wren_adapter="http",
            wren_base_url="http://wren.local",
        )
    )

    assert isinstance(client, WrenHttpClient)


def _agent_context() -> AgentContext:
    return AgentContext(database=DatabaseSummary(id=1, name="warehouse"), datasets=[])


def _project() -> SemanticProject:
    return SemanticProject(
        id="project-1",
        name="Warehouse.sales",
        owner_id="owner",
        database_uri_fingerprint="fingerprint",
        schema_name="sales",
        default_database_id=1,
    )


def _document() -> SemanticDocument:
    return SemanticDocument(
        id="document-1",
        project_id="project-1",
        filename="moves.md",
        content_type="text/markdown",
        size_bytes=12,
        status="extracted",
        scope=ConversationScope(database_id=1, schema_name="sales"),
        checksum="checksum",
        storage_uri="file:///tmp/moves.md",
        summary="Gross moves by stage.",
        extracted_text="Gross moves by stage.",
    )
