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

from typing import Any

import httpx

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.integrations.wren.client import (
    deterministic_base_model_proposals,
    deterministic_mdl_proposal,
)
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.mdl_validation import validate_mdl
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    SemanticDocument,
    SemanticProject,
    SemanticUpdate,
)


class WrenHttpClient:
    """Read-only HTTP adapter for Wren context, planning, and onboarding."""

    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not config.wren_base_url:
            raise ValueError("WREN_BASE_URL is required when WREN_ADAPTER=http.")
        self.config = config
        self.base_url = config.wren_base_url.rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(config.wren_timeout_seconds)

    def is_available(self) -> bool:
        try:
            self._request("GET", "/health")
        except Exception:  # pylint: disable=broad-except
            return False
        return True

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        models = _payload_items(payload, "models")
        names: list[str] = []
        for model in models:
            name = model.get("name") or model.get("model") or model.get("table")
            if name:
                names.append(str(name))
        return names

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl_path: str | None = None,
    ) -> WrenContextArtifact:
        try:
            payload = self._request(
                "POST",
                "/context",
                json={
                    "question": question,
                    "superset_context": superset_context.model_dump(mode="json"),
                    "mdl_path": mdl_path,
                    "context_limit": self.config.wren_context_limit,
                    "example_limit": self.config.wren_example_limit,
                },
            )
        except Exception as ex:  # pylint: disable=broad-except
            return WrenContextArtifact(
                enabled=True,
                available=False,
                warnings=[str(ex)],
            )
        return _context_artifact(payload)

    def recall_examples(
        self,
        *,
        question: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "POST",
            "/examples",
            json={"question": question, "limit": limit},
        )
        return _payload_items(payload, "examples")

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
        mdl_path: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/dry-plan",
            json={
                "question": question,
                "sql": sql,
                "superset_context": context.model_dump(mode="json"),
                "mdl_path": mdl_path,
                "execution": "disabled",
            },
        )
        data = _result(payload)
        data.setdefault("planning_only", True)
        data.setdefault("execution", "disabled")
        return data

    def preview_document_updates(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> list[SemanticUpdate]:
        payload = self._request(
            "POST",
            "/documents/preview-updates",
            json={
                "project": project.model_dump(mode="json"),
                "document": document.model_dump(mode="json"),
            },
        )
        return [
            SemanticUpdate.model_validate(item)
            for item in _payload_items(payload, "updates")
        ]

    def propose_mdl_from_document(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
        schema: dict[str, list[str]] | None = None,
        schema_types: dict[str, dict[str, str]] | None = None,
        instructions: list[str] | None = None,
    ) -> MdlEnrichmentProposal:
        if not self.config.wren_onboarding_enabled:
            proposal = deterministic_mdl_proposal(project=project, document=document)
            return proposal.model_copy(
                update={
                    "warnings": [
                        *proposal.warnings,
                        "Wren onboarding is disabled; using deterministic draft MDL.",
                    ]
                }
            )
        try:
            payload = self._request(
                "POST",
                "/documents/propose-mdl",
                json={
                    "project": project.model_dump(mode="json"),
                    "document": document.model_dump(mode="json"),
                },
            )
        except Exception as ex:  # pylint: disable=broad-except
            proposal = deterministic_mdl_proposal(project=project, document=document)
            return proposal.model_copy(
                update={
                    "warnings": [
                        *proposal.warnings,
                        f"Wren onboarding failed; using deterministic draft MDL: {ex}",
                    ]
                }
            )
        data = _result(payload)
        proposed_content = str(
            data.get("proposed_content")
            or data.get("mdl")
            or data.get("content")
            or ""
        )
        validation = validate_mdl(proposed_content)
        warnings = data.get("warnings")
        return MdlEnrichmentProposal(
            source_document_id=document.id,
            proposed_path=str(
                data.get("proposed_path")
                or data.get("path")
                or f"{project.schema_name}/{document.filename}.json"
            ),
            proposed_content=proposed_content,
            validation=validation,
            warnings=(
                [str(warning) for warning in warnings]
                if isinstance(warnings, list)
                else []
            ),
        )

    def validate_mdl_project(self, *, mdl_path: str) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/mdl/validate",
            json={"mdl_path": mdl_path, "execution": "disabled"},
        )
        data = _result(payload)
        data.setdefault("planning_only", True)
        data.setdefault("execution", "disabled")
        return data

    def generate_base_model(
        self,
        *,
        project: SemanticProject,
        superset_context: AgentContext,
    ) -> list[MdlEnrichmentProposal]:
        if not self.config.wren_onboarding_enabled:
            return deterministic_base_model_proposals(
                project=project,
                superset_context=superset_context,
            )
        try:
            payload = self._request(
                "POST",
                "/models/generate",
                json={
                    "project": project.model_dump(mode="json"),
                    "superset_context": superset_context.model_dump(mode="json"),
                },
            )
        except Exception:  # pylint: disable=broad-except
            return deterministic_base_model_proposals(
                project=project,
                superset_context=superset_context,
            )
        proposals: list[MdlEnrichmentProposal] = []
        for item in _payload_items(payload, "proposals"):
            proposed_content = str(
                item.get("proposed_content")
                or item.get("mdl")
                or item.get("content")
                or ""
            )
            if not proposed_content.strip():
                continue
            proposals.append(
                MdlEnrichmentProposal(
                    source_document_id=str(
                        item.get("source_document_id") or f"onboarding:{project.id}"
                    ),
                    proposed_path=str(
                        item.get("proposed_path")
                        or item.get("path")
                        or "models/model.json"
                    ),
                    proposed_content=proposed_content,
                    validation=validate_mdl(proposed_content),
                    warnings=[str(warning) for warning in item.get("warnings", [])],
                )
            )
        if not proposals:
            return deterministic_base_model_proposals(
                project=project,
                superset_context=superset_context,
            )
        return proposals

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(
            timeout=self.timeout,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = client.request(method, self._url(path), json=json)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Wren HTTP {method} {path} returned a non-object.")
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.wren_api_key:
            headers["Authorization"] = f"Bearer {self.config.wren_api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


def _context_artifact(payload: dict[str, Any]) -> WrenContextArtifact:
    data = _result(payload)
    matched_models = data.get("matched_models") or data.get("models") or []
    example_ids = data.get("example_ids") or []
    document_ids = data.get("document_ids") or []
    context_items = data.get("context_items") or data.get("context") or []
    warnings = data.get("warnings") or []
    return WrenContextArtifact(
        enabled=True,
        available=bool(data.get("available", True)),
        matched_models=[
            str(item) for item in matched_models if isinstance(item, str)
        ],
        example_ids=[str(item) for item in example_ids if isinstance(item, str)],
        document_ids=[str(item) for item in document_ids if isinstance(item, str)],
        semantic_layer_version=data.get("semantic_layer_version"),
        indexing_status=data.get("indexing_status"),
        context_items=(
            [item for item in context_items if isinstance(item, dict)]
            if isinstance(context_items, list)
            else []
        ),
        warnings=[str(item) for item in warnings] if isinstance(warnings, list) else [],
    )


def _payload_items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    data = _result(payload)
    items = data.get(key) or data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("result", payload)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"items": data}
    return payload
