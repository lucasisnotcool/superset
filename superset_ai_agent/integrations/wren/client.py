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

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

import yaml

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.mdl_validation import validate_mdl_yaml
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    SemanticDocument,
    SemanticProject,
    SemanticUpdate,
)


class WrenClient(Protocol):
    """Read-only Wren integration used for context and planning."""

    def is_available(self) -> bool:
        """Return whether Wren assets are usable."""

    def list_models(self) -> list[str]:
        """Return semantic model names known to Wren."""

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl_path: str | None = None,
    ) -> WrenContextArtifact:
        """Fetch semantic context for an already permission-filtered scope."""

    def recall_examples(
        self,
        *,
        question: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return example questions, SQL patterns, or semantic memories."""

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
        mdl_path: str | None = None,
    ) -> dict[str, Any]:
        """Return planning metadata without executing SQL."""

    def preview_document_updates(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> list[SemanticUpdate]:
        """Return reviewable semantic updates without activating MDL."""

    def propose_mdl_from_document(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> MdlEnrichmentProposal:
        """Return reviewable MDL YAML without activating it."""

    def validate_mdl_project(self, *, mdl_path: str) -> dict[str, Any]:
        """Validate a materialized MDL project without executing queries."""


class DisabledWrenClient:
    """Fail-closed Wren client used when Wren is disabled."""

    def is_available(self) -> bool:
        return False

    def list_models(self) -> list[str]:
        return []

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl_path: str | None = None,
    ) -> WrenContextArtifact:
        return WrenContextArtifact(
            enabled=False,
            available=False,
            warnings=["Wren integration is disabled."],
        )

    def recall_examples(
        self,
        *,
        question: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        return []

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
        mdl_path: str | None = None,
    ) -> dict[str, Any]:
        return {
            "enabled": False,
            "planning_only": True,
            "warnings": ["Wren integration is disabled."],
        }

    def preview_document_updates(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> list[SemanticUpdate]:
        return []

    def propose_mdl_from_document(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> MdlEnrichmentProposal:
        return deterministic_mdl_proposal(project=project, document=document)

    def validate_mdl_project(self, *, mdl_path: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "available": False,
            "planning_only": True,
            "warnings": ["Wren integration is disabled."],
        }


class FileWrenClient:
    """Read-only Wren client backed by local MDL and memory files."""

    def __init__(self, config: AgentConfig):
        self.config = config

    def is_available(self) -> bool:
        return self._mdl_path() is not None

    def list_models(self) -> list[str]:
        mdl = self._load_mdl()
        return [
            name
            for name in (_model_name(model) for model in _models_from_mdl(mdl))
            if name
        ]

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl_path: str | None = None,
    ) -> WrenContextArtifact:
        if self._mdl_path(mdl_path) is None:
            return WrenContextArtifact(
                enabled=True,
                available=False,
                warnings=["Wren MDL path is not configured or does not exist."],
            )

        mdl = self._load_mdl(mdl_path)
        examples = self.recall_examples(
            question=question,
            limit=self.config.wren_example_limit,
        )
        matched_models = self._matched_models(
            question=question,
            superset_context=superset_context,
            mdl=mdl,
        )
        return WrenContextArtifact(
            enabled=True,
            available=True,
            matched_models=matched_models[: self.config.wren_context_limit],
            example_ids=[
                str(example.get("id") or _example_id(example)) for example in examples
            ],
        )

    def recall_examples(
        self,
        *,
        question: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        memory_path = self._memory_path()
        if memory_path is None:
            return []
        try:
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        raw_examples = payload.get("examples") if isinstance(payload, dict) else payload
        if not isinstance(raw_examples, list):
            return []
        question_tokens = _tokens(question)

        def score(example: Any) -> tuple[int, str]:
            if not isinstance(example, dict):
                return (0, "")
            text = " ".join(
                str(example.get(key) or "")
                for key in ("id", "question", "description", "sql")
            )
            example_tokens = set(_tokens(text))
            return (len(question_tokens & example_tokens), str(example.get("id") or ""))

        examples = [example for example in raw_examples if isinstance(example, dict)]
        return sorted(examples, key=score, reverse=True)[:limit]

    def dry_plan(
        self,
        *,
        question: str,
        sql: str | None,
        context: AgentContext,
        mdl_path: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.wren_dry_plan_enabled:
            return {
                "enabled": True,
                "planning_only": True,
                "dry_plan_enabled": False,
            }
        if self._mdl_path(mdl_path) is None:
            return {
                "enabled": True,
                "available": False,
                "planning_only": True,
                "warnings": ["Wren MDL path is not configured or does not exist."],
            }
        mdl = self._load_mdl(mdl_path)
        return {
            "enabled": True,
            "available": True,
            "planning_only": True,
            "execution": "disabled",
            "matched_models": self._matched_models(
                question=question,
                superset_context=context,
                mdl=mdl,
            )[: self.config.wren_context_limit],
            "sql_hash": _hash_text(sql or ""),
        }

    def preview_document_updates(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> list[SemanticUpdate]:
        return []

    def propose_mdl_from_document(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
    ) -> MdlEnrichmentProposal:
        return deterministic_mdl_proposal(project=project, document=document)

    def validate_mdl_project(self, *, mdl_path: str) -> dict[str, Any]:
        path = Path(mdl_path)
        return {
            "enabled": True,
            "available": path.exists(),
            "planning_only": True,
            "path": str(path),
        }

    def _matched_models(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl: dict[str, Any],
    ) -> list[str]:
        question_tokens = _tokens(question)
        dataset_names = {
            dataset.table_name.lower() for dataset in superset_context.datasets
        }
        matches: list[tuple[int, str]] = []
        for model in _models_from_mdl(mdl):
            name = _model_name(model)
            if not name:
                continue
            text = json.dumps(model, default=str)
            tokens = set(_tokens(text))
            score = len(question_tokens & tokens)
            if name.lower() in dataset_names:
                score += 3
            if score > 0:
                matches.append((score, name))
        if not matches:
            return [
                name
                for name in (_model_name(model) for model in _models_from_mdl(mdl))
                if name
            ]
        return [name for _, name in sorted(matches, reverse=True)]

    def _load_mdl(self, mdl_path: str | None = None) -> dict[str, Any]:
        path = self._mdl_path(mdl_path)
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _mdl_path(self, mdl_path: str | None = None) -> Path | None:
        if mdl_path:
            path = Path(mdl_path)
            return path if path.exists() and path.is_file() else None
        candidates = []
        if self.config.wren_mdl_path:
            candidates.append(Path(self.config.wren_mdl_path))
        if self.config.wren_project_path:
            project = Path(self.config.wren_project_path)
            candidates.extend([project / "mdl.json", project / "wren" / "mdl.json"])
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _memory_path(self) -> Path | None:
        if not self.config.wren_memory_path:
            return None
        path = Path(self.config.wren_memory_path)
        return path if path.exists() and path.is_file() else None


def _models_from_mdl(mdl: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("models", "semantic_models", "views"):
        value = mdl.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _model_name(model: dict[str, Any]) -> str:
    value = model.get("name") or model.get("model") or model.get("table")
    return str(value) if value else ""


def _tokens(text: str) -> set[str]:
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in text
    )
    return {token for token in normalized.split() if token}


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _example_id(example: dict[str, Any]) -> str:
    return _hash_text(json.dumps(example, sort_keys=True, default=str))[:12]


def deterministic_mdl_proposal(
    *,
    project: SemanticProject,
    document: SemanticDocument,
) -> MdlEnrichmentProposal:
    """Create a local review draft when Wren onboarding is unavailable."""

    text = document.extracted_text or document.extracted_text_preview or ""
    description = document.summary or text.strip()[:500] or document.filename
    model_name = _safe_mdl_name(project.schema_name)
    payload = {
        "models": [
            {
                "name": model_name,
                "description": description,
                "properties": {
                    "database_label": project.database_label,
                    "catalog_name": project.catalog_name,
                    "schema_name": project.schema_name,
                    "source_document_id": document.id,
                    "source_document": document.filename,
                },
            }
        ]
    }
    proposed_yaml = yaml.safe_dump(
        payload,
        sort_keys=False,
        allow_unicode=False,
    )
    return MdlEnrichmentProposal(
        source_document_id=document.id,
        proposed_path=f"{model_name}/{_safe_mdl_name(document.filename)}.yaml",
        proposed_yaml=proposed_yaml,
        validation=validate_mdl_yaml(proposed_yaml),
        warnings=[
            "Generated MDL is a review draft. Confirm model names, columns, "
            "metrics, and relationships before activation."
        ],
    )


def _safe_mdl_name(value: str) -> str:
    lowered = value.lower()
    chars = [char if char.isalnum() else "_" for char in lowered]
    name = "_".join("".join(chars).split("_"))
    return name or "semantic_model"
