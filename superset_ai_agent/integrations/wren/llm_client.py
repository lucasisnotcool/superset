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

"""LLM-backed Wren modeling client.

This client implements the read/planning-only ``WrenClient`` protocol using the
agent's own ``ModelClient``. It powers three product touchpoints:

- onboarding: introspect a permission-filtered schema into draft MDL models;
- enrichment: improve a base model from a business document;
- query-time context: surface active MDL semantics to the SQL prompt.

It never executes SQL. Generated MDL is always returned as a reviewable draft.
"""

from __future__ import annotations

import json  # noqa: TID251 - keep the standalone agent independent of Superset
from pathlib import Path
from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.integrations.wren.client import deterministic_mdl_proposal
from superset_ai_agent.integrations.wren.mdl_exporter import model_from_dataset
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.mdl_authoring import (
    MdlProposalResponse,
    proposal_response_schema,
    serialize_manifest,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.mdl_validation import validate_mdl
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    SemanticDocument,
    SemanticProject,
    SemanticUpdate,
)

#: Surfaced to the UI when the model is invoked but returns no usable structured
#: MDL, so the agent falls back to the deterministic (structure-only) draft. This
#: makes the "rich enrichment vs. deterministic draft" degradation visible rather
#: than silent — it usually means the configured provider/model does not honor
#: structured JSON output well. (wren_full.md F5.)
_PROVIDER_FALLBACK_WARNING = (
    "The model did not return a valid structured MDL proposal, so a deterministic "
    "draft (structure only, descriptions not enriched) is shown. If you expected "
    "richer enrichment, the configured provider/model may not support structured "
    "JSON output reliably."
)


class LlmWrenClient:
    """Read/planning-only Wren client backed by the agent's ``ModelClient``."""

    def __init__(
        self,
        config: AgentConfig,
        model_client: ModelClient,
        *,
        mdl_file_store: MdlFileStore | None = None,
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.mdl_file_store = mdl_file_store

    def is_available(self) -> bool:
        return self.model_client is not None

    def list_models(self) -> list[str]:
        return []

    def fetch_context(
        self,
        *,
        question: str,
        superset_context: AgentContext,
        mdl_path: str | None = None,
    ) -> WrenContextArtifact:
        mdl = _load_mdl_json(mdl_path)
        if not mdl:
            return WrenContextArtifact(
                enabled=True,
                available=False,
                warnings=["No materialized MDL is available for this scope."],
            )
        models = [model for model in mdl.get("models", []) if isinstance(model, dict)]
        if not models:
            return WrenContextArtifact(
                enabled=True,
                available=False,
                warnings=["Materialized MDL has no models."],
            )
        dataset_names = {
            dataset.table_name.lower() for dataset in superset_context.datasets
        }
        ranked = _rank_models(question, models, dataset_names)
        budget = self.config.wren_schema_context_token_budget
        context_items = _trim_to_budget(ranked, budget)
        relationships = [
            rel for rel in mdl.get("relationships", []) if isinstance(rel, dict)
        ]
        if relationships:
            context_items.append({"type": "relationships", "items": relationships})
        return WrenContextArtifact(
            enabled=True,
            available=True,
            matched_models=[
                str(model.get("name"))
                for model in ranked
                if model.get("name")
            ][: self.config.wren_context_limit],
            context_items=context_items,
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
            "enabled": True,
            "available": _load_mdl_json(mdl_path) is not None,
            "planning_only": True,
            "execution": "disabled",
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
        base_mdl = self._active_mdl_json(project)
        document_text = (
            document.extracted_text or document.extracted_text_preview or ""
        ).strip()
        if not document_text:
            return deterministic_mdl_proposal(project=project, document=document)
        payload = {
            "project": {
                "name": project.name,
                "schema_name": project.schema_name,
                "catalog_name": project.catalog_name,
                "database_label": project.database_label,
            },
            "current_mdl": base_mdl,
            "document_filename": document.filename,
            "document_text": document_text[:20_000],
        }
        response = self._call_model("wren_enrichment", payload)
        if response is None or not response.files:
            # The model was invoked (there *is* document text) but returned no
            # usable structured proposal — surface the degradation, don't hide it.
            fallback = deterministic_mdl_proposal(project=project, document=document)
            return fallback.model_copy(
                update={"warnings": [*fallback.warnings, _PROVIDER_FALLBACK_WARNING]}
            )
        first = response.files[0]
        # F6: patch the enrichment into the file that owns its models — even when
        # several active files exist — merging into that file's full content so its
        # untouched entities survive. This generalizes the W4 single-file in-place
        # targeting and kills the duplicate_model cascade for multi-file projects.
        # When the overlay spans multiple files (no single owner), fall back to the
        # model's path / schema default and let the activation dedup net guarantee
        # activatability.
        overlay = first.manifest.model_dump(by_alias=True, exclude_none=True)
        patch = self._patch_target(project, overlay)
        if patch is not None:
            proposed_path, proposed_content = patch
        else:
            proposed_content = serialize_manifest(first.manifest)
            proposed_path = _safe_relative_path(
                first.path, default=f"models/{_safe_name(project.schema_name)}.json"
            )
        return MdlEnrichmentProposal(
            source_document_id=document.id,
            proposed_path=proposed_path,
            proposed_content=proposed_content,
            validation=validate_mdl(proposed_content),
            warnings=[
                *response.warnings,
                "LLM-generated MDL draft. Review before activation.",
            ],
        )

    def validate_mdl_project(self, *, mdl_path: str) -> dict[str, Any]:
        path = Path(mdl_path)
        return {
            "enabled": True,
            "available": path.exists(),
            "planning_only": True,
            "path": str(path),
        }

    def generate_base_model(
        self,
        *,
        project: SemanticProject,
        superset_context: AgentContext,
    ) -> list[MdlEnrichmentProposal]:
        if not superset_context.datasets:
            return []
        payload = {
            "project": {
                "name": project.name,
                "schema_name": project.schema_name,
                "catalog_name": project.catalog_name,
                "database_label": project.database_label,
                "database_backend": project.database_backend,
            },
            "datasets": [
                dataset.model_dump(mode="json")
                for dataset in superset_context.datasets
            ],
        }
        # W3: structure is seeded deterministically from the permission-filtered
        # datasets (authoritative name/tableReference/columns+types). The model
        # only supplies *semantics* (descriptions, synonyms), which are overlaid
        # onto the seed — so a useless or absent model can never drop a column or
        # its required type. This is how Wren avoids the structural-hallucination
        # class: structure from the catalog, semantics from the model.
        seeds = {
            str(seed.get("name")): seed
            for seed in (
                model_from_dataset(dataset) for dataset in superset_context.datasets
            )
            if seed.get("name")
        }
        response = self._call_model("wren_onboarding", payload)
        llm_models, warnings = _llm_models_by_name(response)
        if response is None:
            # Structure is still seeded deterministically (so the proposals are
            # valid), but the semantic enrichment was skipped — tell the user.
            warnings = [*warnings, _PROVIDER_FALLBACK_WARNING]

        proposals: list[MdlEnrichmentProposal] = []
        for name, seed in seeds.items():
            _overlay_model_semantics(seed, llm_models.get(name))
            proposed_content = json.dumps({"models": [seed]}, indent=2)
            proposals.append(
                MdlEnrichmentProposal(
                    source_document_id=f"onboarding:{project.id}",
                    proposed_path=f"models/{_safe_name(name)}.json",
                    proposed_content=proposed_content,
                    validation=validate_mdl(proposed_content),
                    warnings=[
                        *warnings,
                        "Base model seeded from schema introspection; descriptions "
                        "enriched by the model. Review before activation.",
                    ],
                )
            )
        return proposals

    def _active_files_content(
        self, project: SemanticProject
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(path, parsed_content)`` for each active MDL file."""

        if self.mdl_file_store is None:
            return []
        try:
            files = self.mdl_file_store.list(project.id)
        except Exception:  # pylint: disable=broad-except
            return []
        out: list[tuple[str, dict[str, Any]]] = []
        for file in files:
            if file.status != "active":
                continue
            try:
                payload = json.loads(file.content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                out.append((file.path, payload))
        return out

    def _patch_target(
        self, project: SemanticProject, overlay: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Route an enrichment overlay to the active file that owns its models (F6).

        Returns ``(path, merged_json)`` when the overlay's models all belong to a
        single existing file (or there is exactly one active file), having merged
        the overlay into that file's full content so its untouched entities are
        preserved. Returns ``None`` when the overlay spans multiple files or there
        is no single clear target — leaving the caller's default-path plus the
        activation-time dedup net in charge, which still guarantees activatability.
        """

        active = self._active_files_content(project)
        if not active:
            return None

        overlay_model_names = {
            model["name"]
            for model in overlay.get("models", [])
            if isinstance(model, dict) and isinstance(model.get("name"), str)
        }
        owner_by_model: dict[str, str] = {}
        for path, content in active:
            for model in content.get("models", []) or []:
                name = model.get("name") if isinstance(model, dict) else None
                if isinstance(name, str):
                    owner_by_model.setdefault(name, path)

        owner_paths = {
            owner_by_model[name]
            for name in overlay_model_names
            if name in owner_by_model
        }
        if len(owner_paths) > 1:
            # The overlay touches models in several files; a single proposal
            # cannot patch them all without losing data — fall back.
            return None
        if owner_paths:
            target_path = next(iter(owner_paths))
        elif len(active) == 1:
            # All-new models with one active file: keep them with the existing
            # file rather than spawning a colliding sibling.
            target_path = active[0][0]
        else:
            return None

        base = next(content for path, content in active if path == target_path)
        merged = _merge_manifest_sections(base, overlay)
        return target_path, json.dumps(merged, indent=2)

    def _active_mdl_json(self, project: SemanticProject) -> list[dict[str, Any]]:
        """Return the active models as native dicts — read-only enrichment context."""

        if self.mdl_file_store is None:
            return []
        try:
            files = self.mdl_file_store.list(project.id)
        except Exception:  # pylint: disable=broad-except
            return []
        models: list[dict[str, Any]] = []
        for file in files:
            if file.status != "active":
                continue
            try:
                payload = json.loads(file.content)
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                models.extend(
                    model
                    for model in payload.get("models", [])
                    if isinstance(model, dict)
                )
        return models

    def _call_model(
        self,
        prompt_name: str,
        payload: dict[str, Any],
    ) -> MdlProposalResponse | None:
        try:
            prompt = get_prompt(prompt_name)
        except OSError:
            return None
        try:
            result = self.model_client.chat(
                [
                    ChatMessage(role="system", content=prompt),
                    ChatMessage(
                        role="user",
                        content=(
                            "Produce MDL using this context. Return only JSON "
                            "matching the requested schema.\n"
                            f"{json.dumps(payload, default=str)}"
                        ),
                    ),
                ],
                format_schema=proposal_response_schema(),
            )
        except Exception:  # pylint: disable=broad-except
            return None
        try:
            data = json.loads(result.content)
            return MdlProposalResponse.model_validate(data)
        except Exception:  # pylint: disable=broad-except
            return None


def _load_mdl_json(mdl_path: str | None) -> dict[str, Any]:
    if not mdl_path:
        return {}
    path = Path(mdl_path)
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tokens(text: str) -> set[str]:
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in text
    )
    return {token for token in normalized.split() if token}


def _rank_models(
    question: str,
    models: list[dict[str, Any]],
    dataset_names: set[str],
) -> list[dict[str, Any]]:
    question_tokens = _tokens(question)

    def score(model: dict[str, Any]) -> int:
        name = str(model.get("name") or "")
        text = json.dumps(model, default=str)
        value = len(question_tokens & _tokens(text))
        if name.lower() in dataset_names:
            value += 3
        return value

    scored = sorted(models, key=score, reverse=True)
    return scored


def _trim_to_budget(
    models: list[dict[str, Any]],
    token_budget: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    used = 0
    for model in models:
        rendered = json.dumps(model, default=str)
        cost = max(1, len(rendered) // 4)
        if items and used + cost > token_budget:
            break
        items.append({"type": "model", "model": model})
        used += cost
    return items


def _llm_models_by_name(
    response: MdlProposalResponse | None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Index the model's proposed manifests by model name (native dicts)."""

    if response is None:
        return {}, []
    models: dict[str, dict[str, Any]] = {}
    for file in response.files:
        payload = file.manifest.model_dump(by_alias=True, exclude_none=True)
        for model in payload.get("models", []):
            name = model.get("name")
            if isinstance(name, str) and name:
                models[name] = model
    return models, list(response.warnings)


#: Manifest sections merged entity-by-entity when patching an enrichment overlay
#: into an existing file (F6).
_MERGE_SECTIONS: tuple[str, ...] = (
    "models",
    "relationships",
    "views",
    "metrics",
    "cubes",
)


def _merge_named(
    base: list[Any], overlay: list[Any]
) -> list[dict[str, Any]]:
    """Merge two lists of ``{name: ...}`` mappings.

    An overlay entry replaces the base entry of the same ``name`` in place;
    entries with a new name are appended. Order of existing entries is preserved
    so a patch never reshuffles the file.
    """

    result: list[dict[str, Any]] = [
        dict(item) for item in base if isinstance(item, dict)
    ]
    index = {
        item["name"]: pos
        for pos, item in enumerate(result)
        if isinstance(item.get("name"), str)
    }
    for item in overlay:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name in index:
            result[index[name]] = item
        else:
            result.append(item)
            if isinstance(name, str):
                index[name] = len(result) - 1
    return result


def _merge_manifest_sections(
    base: dict[str, Any], overlay: dict[str, Any]
) -> dict[str, Any]:
    """Overlay a proposed manifest onto a target file's content, section by section.

    The target file's other entities (and envelope keys like ``catalog``/
    ``schema``) are preserved; only the overlay's named entities are added or
    replaced. This is what makes an enrichment a *patch* of the owning file rather
    than a wholesale overwrite that would drop its untouched models.
    """

    merged = dict(base)
    for section in _MERGE_SECTIONS:
        base_list = base.get(section)
        overlay_list = overlay.get(section)
        base_list = base_list if isinstance(base_list, list) else []
        overlay_list = overlay_list if isinstance(overlay_list, list) else []
        if base_list or overlay_list:
            merged[section] = _merge_named(base_list, overlay_list)
    return merged


def _overlay_model_semantics(
    seed: dict[str, Any],
    llm_model: dict[str, Any] | None,
) -> None:
    """Overlay model/column descriptions + synonyms from the model onto the seed.

    Structure (name, tableReference, column names + types) stays authoritative;
    only semantic fields are taken from the LLM, and only when present.
    """

    if not llm_model:
        return
    if llm_model.get("description"):
        seed["description"] = llm_model["description"]
    llm_columns = {
        col.get("name"): col
        for col in llm_model.get("columns", [])
        if isinstance(col, dict) and col.get("name")
    }
    for column in seed.get("columns", []):
        match = llm_columns.get(column.get("name"))
        if not match:
            continue
        if match.get("description"):
            column["description"] = match["description"]
        if isinstance(match.get("properties"), dict):
            merged = {**column.get("properties", {}), **match["properties"]}
            if merged:
                column["properties"] = merged


def _safe_name(value: str) -> str:
    chars = [char if char.isalnum() else "_" for char in value.lower()]
    name = "_".join("".join(chars).split("_"))
    return name or "semantic_model"


def _safe_relative_path(path: str, *, default: str) -> str:
    candidate = (path or "").strip().replace("\\", "/").lstrip("/")
    if not candidate or ".." in candidate.split("/"):
        return default
    if candidate.endswith((".yaml", ".yml")):
        candidate = candidate.rsplit(".", 1)[0]
    if not candidate.endswith(".json"):
        candidate = f"{candidate}.json"
    return candidate
