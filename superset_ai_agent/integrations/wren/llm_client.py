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
import logging
from pathlib import Path
from typing import Any

from superset_ai_agent.config import AgentConfig
from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.integrations.wren.client import deterministic_mdl_proposal
from superset_ai_agent.integrations.wren.mdl_exporter import model_from_dataset
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.schemas import WrenContextArtifact
from superset_ai_agent.semantic_layer.document_chunks import select_relevant_sections
from superset_ai_agent.semantic_layer.mdl_authoring import (
    MdlProposalResponse,
    proposal_response_schema,
)
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.mdl_merge import (
    merge_manifest_sections as _merge_manifest_sections,
    merge_model_preserving_structure as _merge_model_preserving_structure,
)
from superset_ai_agent.semantic_layer.mdl_validation import SchemaIndex, validate_mdl
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    MdlValidationResult,
    SemanticDocument,
    SemanticProject,
)
from superset_ai_agent.semantic_layer.wren_core_validator import (
    validate_with_wren_core,
    wren_core_available,
)

logger = logging.getLogger(__name__)

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
        # Surface views the same way: a view is a vetted, named query the agent can
        # select from instead of re-deriving the joins. Native (``dialect``) views
        # are excluded — they are not in the engine manifest, so the agent must not
        # be told to query one.
        views = [
            view
            for view in mdl.get("views", [])
            if isinstance(view, dict) and view.get("name") and not view.get("dialect")
        ]
        ranked_views = _rank_views(question, views)[: self.config.wren_context_limit]
        if ranked_views:
            context_items.append({"type": "views", "items": ranked_views})
        return WrenContextArtifact(
            enabled=True,
            available=True,
            matched_models=[
                str(model.get("name")) for model in ranked if model.get("name")
            ][: self.config.wren_context_limit],
            matched_views=[str(view.get("name")) for view in ranked_views],
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

    def propose_mdl_from_document(
        self,
        *,
        project: SemanticProject,
        document: SemanticDocument,
        schema: dict[str, list[str]] | None = None,
        schema_types: dict[str, dict[str, str]] | None = None,
        schema_by_schema: dict[str, dict[str, dict[str, object]]] | None = None,
        instructions: list[str] | None = None,
    ) -> MdlEnrichmentProposal:
        base_mdl = self._effective_mdl_json(project)
        document_text = (
            document.extracted_text or document.extracted_text_preview or ""
        ).strip()
        if not document_text:
            return deterministic_mdl_proposal(project=project, document=document)
        base_payload: dict[str, Any] = {
            "project": {
                "name": project.name,
                "schema_name": project.schema_name,
                "catalog_name": project.catalog_name,
                "database_label": project.database_label,
            },
            # E2: the model sees a *reference* of existing models (names, table
            # refs, column names+types) — not full re-emittable bodies. The real
            # file content is preserved by the merge, so trimming here only shapes
            # context; it also resolves the wren_full.md W4 input-shape mismatch.
            "current_mdl": _mdl_reference(base_mdl),
            "document_filename": document.filename,
            # C4: budget the document for the prompt by selecting the sections most
            # relevant to the project's tables/columns/models, so a large document's
            # late content about real tables survives instead of a blind head-cut.
            "document_text": select_relevant_sections(
                document_text,
                terms=_schema_terms(schema, base_mdl),
                budget=self.config.wren_document_prompt_char_budget,
            ),
        }
        # E2: ground the model on the authoritative physical schema (real tables +
        # columns) so it never references a column/table that does not exist. C3:
        # when catalog types are available (live path), also carry them so the model
        # types a brand-new column correctly, and the loop's SchemaIndex can reject a
        # cross-family type mismatch.
        # F4: a multi-schema project also carries a SCHEMA-QUALIFIED view (each table
        # under its schema), so the model authors a correct ``tableReference.schema``
        # and the correction loop's index validates per-schema (no same-name collision
        # across schemas). Single-schema enrichment is unaffected.
        tables_by_schema, types_by_schema = _split_schema_view(schema_by_schema)
        schema_index = (
            SchemaIndex.from_snapshot(
                schema,
                schema_types,
                tables_by_schema=tables_by_schema,
                types_by_schema=types_by_schema,
            )
            if schema
            else None
        )
        if schema:
            base_payload["physical_schema"] = schema
            if schema_types:
                base_payload["physical_schema_types"] = schema_types
            if schema_by_schema:
                base_payload["physical_schema_by_schema"] = schema_by_schema
        # Operator guidance (Wren `instructions`) steers the enrichment.
        if instructions:
            base_payload["instructions"] = instructions
        draft = self._draft_with_correction(
            project, base_mdl, base_payload, schema_index=schema_index
        )
        if draft is None:
            # The model was invoked (there *is* document text) but returned no usable
            # structured proposal. CR2: never fabricate the schema-name blob when a real
            # base/schema exists — that masks the failure as a fake one-model
            # "success". Return a no-op proposal (the effective base unchanged, or
            # empty) plus a loud warning so the UI shows the degradation.
            if base_mdl or schema:
                return self._no_change_proposal(project, document)
            # No base and no physical schema: nothing to anchor to. The deterministic
            # draft (structure-only) is the honest degrade for a bare project.
            fallback = deterministic_mdl_proposal(project=project, document=document)
            return fallback.model_copy(
                update={"warnings": [*fallback.warnings, _PROVIDER_FALLBACK_WARNING]}
            )
        proposed_path, proposed_content, validation, response_warnings = draft
        # E5: defense-in-depth — if any base column is missing from the proposal,
        # surface it rather than letting a structural regression pass silently.
        dropped = _dropped_columns(base_mdl, proposed_content)
        drop_warning = (
            [
                "Enrichment dropped existing column(s): "
                + ", ".join(dropped)
                + ". Review before activation."
            ]
            if dropped
            else []
        )
        return MdlEnrichmentProposal(
            source_document_id=document.id,
            proposed_path=proposed_path,
            proposed_content=proposed_content,
            validation=validation,
            warnings=[
                *response_warnings,
                *drop_warning,
                "LLM-generated MDL draft. Review before activation.",
            ],
        )

    def _no_change_proposal(
        self, project: SemanticProject, document: SemanticDocument
    ) -> MdlEnrichmentProposal:
        """A non-fabricating proposal for a provider that returned nothing (CR2).

        Echoes the effective base MDL (the first effective file) unchanged, or an
        empty manifest when there is none, so the review surface shows "no changes
        applied" rather than a misleading new schema-named model. The
        provider-fallback warning is attached so the degradation is visible.
        """

        if effective := self._effective_files_content(project):
            path, content = effective[0]
            proposed_content = json.dumps(content, indent=2)
            proposed_path = path
        else:
            proposed_content = json.dumps({"models": []}, indent=2)
            proposed_path = f"models/{_safe_name(project.schema_name)}.json"
        return MdlEnrichmentProposal(
            source_document_id=document.id,
            proposed_path=proposed_path,
            proposed_content=proposed_content,
            validation=validate_mdl(proposed_content),
            warnings=[
                _PROVIDER_FALLBACK_WARNING,
                "No enrichment changes were applied. The base models are unchanged; "
                "review the configured model/provider before retrying.",
            ],
        )

    def _draft_with_correction(
        self,
        project: SemanticProject,
        base_mdl: list[dict[str, Any]],
        base_payload: dict[str, Any],
        *,
        schema_index: SchemaIndex | None = None,
    ) -> tuple[str, str, MdlValidationResult, list[str]] | None:
        """Draft an enrichment, re-prompting on validation errors (E3).

        Returns ``(path, content, validation, warnings)`` for the best draft, or
        ``None`` when the provider never returned a usable structured proposal.
        Each retry feeds the prior attempt's errors back to the model, bounded by
        ``wren_modeling_max_correction_retries``. When ``schema_index`` is supplied
        the validation is *physical* (E2/E3): invented columns/tables become errors
        the loop corrects, not just structural issues. The structure-preserving
        merge (E4) is applied on every attempt.
        """

        max_retries = max(0, self.config.wren_modeling_max_correction_retries)
        best: tuple[str, str, MdlValidationResult, list[str]] | None = None
        errors_feedback: list[str] | None = None
        for _ in range(max_retries + 1):
            payload = dict(base_payload)
            if errors_feedback:
                payload["previous_validation_errors"] = errors_feedback
            response = self._call_model("wren_enrichment", payload)
            if response is None or not response.files:
                # CR8: separate "provider returned nothing usable" (response is None,
                # already logged in _call_model) from "model returned an empty
                # proposal" (a no-op the prompt should avoid when structure exists).
                if response is not None:
                    logger.info(
                        "Enrichment model returned an empty files array (no changes); "
                        "warnings=%s",
                        list(response.warnings),
                    )
                break
            first = response.files[0]
            # F6: patch the enrichment into the file that owns its models, merging
            # into that file's full content so untouched entities survive; when the
            # overlay spans files (no single owner), structure-preserve each touched
            # model against the active base so a column it omits is not dropped (E4).
            overlay = first.manifest.model_dump(by_alias=True, exclude_none=True)
            patch = self._patch_target(project, overlay)
            if patch is not None:
                proposed_path, proposed_content = patch
            else:
                reconciled = _reconcile_overlay_with_base(base_mdl, overlay)
                proposed_content = json.dumps(reconciled, indent=2)
                proposed_path = _safe_relative_path(
                    first.path,
                    default=_default_overlay_path(project, overlay),
                )
            validation = validate_mdl(proposed_content, schema_index=schema_index)
            # C2.1: when structural+physical validation passes, optionally deep-
            # validate with wren-core — but merge the overlay against the *full*
            # active MDL first, because the engine compiles a whole manifest
            # (relationships/calculated fields resolve across files). Folds engine
            # expression errors into the same correction loop.
            if validation.valid and self._deep_validation_enabled():
                deep = self._deep_validate(base_mdl, proposed_content)
                if not deep.valid:
                    validation = MdlValidationResult(
                        valid=False,
                        messages=[*validation.messages, *deep.messages],
                    )
            best = (
                proposed_path,
                proposed_content,
                validation,
                list(response.warnings),
            )
            if validation.valid:
                break
            errors_feedback = [
                message.message
                for message in validation.messages
                if message.severity == "error"
            ]
        return best

    def _deep_validation_enabled(self) -> bool:
        """Whether to run wren-core deep validation in the correction loop (C2.1)."""

        return self.config.wren_modeling_deep_validation and wren_core_available()

    def _deep_validate(
        self, base_mdl: list[dict[str, Any]], proposed_content: str
    ) -> MdlValidationResult:
        """Deep-validate the proposed overlay against the full active manifest (C2.1).

        wren-core compiles a *whole* manifest, so a partial overlay would fail on
        cross-file references. Reconstruct the complete proposed manifest — the
        proposed models win by name, every other active model is carried over — then
        deep-validate. Degrades to ``valid=True`` when the content cannot be parsed
        (the structural validator already ran) so this never falsely blocks a draft.
        """

        try:
            proposed = json.loads(proposed_content)
        except (ValueError, TypeError):
            return MdlValidationResult(valid=True)
        if not isinstance(proposed, dict):
            return MdlValidationResult(valid=True)
        models, relationships = _full_proposed_manifest(base_mdl, proposed)
        return validate_with_wren_core(models, relationships)

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
                dataset.model_dump(mode="json") for dataset in superset_context.datasets
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

    def _effective_files_content(
        self, project: SemanticProject
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(path, parsed_content)`` for the **effective** MDL per path (CR1).

        Wren enriches the *modeled* MDL; in this codebase the onboarded structure
        exists as **drafts** before activation (``onboarding.py`` writes drafts only).
        So the enrichment base is the effective file per path: the active file if one
        exists, otherwise the latest draft. Deleted files are excluded. This is what
        makes ``onboard → enrich`` work without a manual activation in between.
        """

        if self.mdl_file_store is None:
            return []
        try:
            files = self.mdl_file_store.list(project.id)
        except Exception:  # pylint: disable=broad-except
            return []
        effective: dict[str, Any] = {}
        for file in files:
            if file.status == "deleted":
                continue
            current = effective.get(file.path)
            if current is None or _supersedes(file, current):
                effective[file.path] = file
        out: list[tuple[str, dict[str, Any]]] = []
        for file in effective.values():
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
        """Route an enrichment overlay to the effective file that owns its models (F6).

        Returns ``(path, merged_json)`` when the overlay's models all belong to a
        single existing file (or there is exactly one effective file), having merged
        the overlay into that file's full content so its untouched entities are
        preserved. Returns ``None`` when the overlay spans multiple files or there
        is no single clear target — leaving the caller's default-path plus the
        activation-time dedup net in charge, which still guarantees activatability.
        Operates on the **effective** MDL (active, else latest draft) so an
        onboarded-but-unactivated project still has a patch target (CR1).
        """

        active = self._effective_files_content(project)
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

    def _effective_mdl_json(self, project: SemanticProject) -> list[dict[str, Any]]:
        """Return the effective models as native dicts — read-only enrichment context.

        Uses the effective MDL per path (active, else latest draft) so enrichment
        grounds on the onboarded structure before activation (CR1).
        """

        models: list[dict[str, Any]] = []
        for _path, payload in self._effective_files_content(project):
            models.extend(
                model for model in payload.get("models", []) if isinstance(model, dict)
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
            logger.warning("MDL prompt %r could not be loaded.", prompt_name)
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
        except Exception as ex:  # pylint: disable=broad-except
            # CR8: distinguish a provider/transport failure from a parse failure so
            # the fallback's cause is diagnosable rather than uniformly silent.
            logger.warning("MDL model call (%s) raised: %s", prompt_name, ex)
            return None
        try:
            data = json.loads(result.content)
            return MdlProposalResponse.model_validate(data)
        except Exception as ex:  # pylint: disable=broad-except
            # CR8: the provider responded but the body was not schema-valid JSON —
            # the classic "provider does not honor structured output" case.
            logger.warning(
                "MDL model call (%s) returned unparseable/invalid structured output "
                "(%s); first 200 chars: %r",
                prompt_name,
                ex,
                (getattr(result, "content", "") or "")[:200],
            )
            return None


def _split_schema_view(
    view: dict[str, dict[str, dict[str, object]]] | None,
) -> tuple[
    dict[str, dict[str, list[str]]] | None,
    dict[str, dict[str, dict[str, str]]] | None,
]:
    """Split a schema-qualified view into ``(tables_by_schema, types_by_schema)``.

    The copilot/onboarding surface a ``{schema: {table: {columns, types?}}}`` view;
    ``SchemaIndex.from_snapshot`` wants the two maps separately. Returns
    ``(None, None)`` for an empty view (single-schema enrichment).
    """

    if not view:
        return None, None
    tables: dict[str, dict[str, list[str]]] = {}
    types: dict[str, dict[str, dict[str, str]]] = {}
    for schema_name, tbls in view.items():
        tables[schema_name] = {}
        for table, entry in tbls.items():
            cols = entry.get("columns") if isinstance(entry, dict) else None
            tables[schema_name][table] = (
                [str(col) for col in cols] if isinstance(cols, list) else []
            )
            typ = entry.get("types") if isinstance(entry, dict) else None
            if isinstance(typ, dict) and typ:
                types.setdefault(schema_name, {})[table] = {
                    str(key): str(value) for key, value in typ.items()
                }
    return tables, (types or None)


def _supersedes(candidate: Any, current: Any) -> bool:
    """Whether ``candidate`` is the more authoritative file for a path (CR1).

    Active beats draft; within the same status the later ``updated_at`` wins. Used to
    pick the effective MDL per path so enrichment grounds on the onboarded structure
    (drafts) before activation while still preferring an activated file when present.
    """

    cand_active = candidate.status == "active"
    cur_active = current.status == "active"
    if cand_active != cur_active:
        return cand_active
    return candidate.updated_at >= current.updated_at


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


def _rank_views(
    question: str,
    views: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank views by token overlap with the question (mirrors ``_rank_models``).

    Only views that share at least one term with the question are returned, so an
    unrelated view never crowds the context — a view earns its slot by matching.
    """

    question_tokens = _tokens(question)

    def score(view: dict[str, Any]) -> int:
        return len(question_tokens & _tokens(json.dumps(view, default=str)))

    matched = [view for view in views if score(view) > 0]
    return sorted(matched, key=score, reverse=True)


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


def _mdl_reference(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trim active models to a read-only reference for the prompt (E2 / W4).

    Emits names, ``tableReference``, model/column descriptions, and column
    names+types — enough for the model to know what exists and refine semantics,
    but not the full re-emittable bodies (which the merge preserves anyway).
    """

    reference: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            continue
        ref: dict[str, Any] = {"name": model["name"]}
        if model.get("tableReference"):
            ref["tableReference"] = model["tableReference"]
        if model.get("description"):
            ref["description"] = model["description"]
        columns: list[dict[str, Any]] = []
        for column in model.get("columns", []) or []:
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                continue
            col_ref: dict[str, Any] = {"name": column["name"]}
            if column.get("type"):
                col_ref["type"] = column["type"]
            if column.get("description"):
                col_ref["description"] = column["description"]
            columns.append(col_ref)
        if columns:
            ref["columns"] = columns
        reference.append(ref)
    return reference


def _schema_terms(
    schema: dict[str, list[str]] | None, base_mdl: list[dict[str, Any]]
) -> set[str]:
    """Relevance vocabulary for C4 section selection: table/column/model names.

    Sections of a large document that mention real tables, columns or existing model
    names are the ones enrichment should keep within the prompt budget. Drawn from the
    physical schema (when grounded) and the active MDL.
    """

    terms: set[str] = set()
    for table, columns in (schema or {}).items():
        terms.add(str(table))
        terms.update(str(column) for column in columns)
    for model in base_mdl:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        if isinstance(name, str):
            terms.add(name)
        for column in model.get("columns", []) or []:
            if isinstance(column, dict) and isinstance(column.get("name"), str):
                terms.add(column["name"])
    return {term for term in terms if term}


def _full_proposed_manifest(
    base_mdl: list[dict[str, Any]], proposed: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconstruct the complete proposed manifest for deep validation (C2.1).

    wren-core compiles a *whole* manifest, but the proposal only carries the
    touched file's models. Union the proposed models with every other active model
    (proposed wins by name), so a proposed relationship/calculated field can resolve
    against models that live in untouched files — without that union, deep
    validation would raise false errors on legitimate cross-file references.
    Relationships come from the proposal (the base snapshot carries models only);
    including only proposed relationships cannot create false positives because all
    models are present.
    """

    proposed_models = [
        model for model in proposed.get("models", []) or [] if isinstance(model, dict)
    ]
    proposed_names = {
        model.get("name")
        for model in proposed_models
        if isinstance(model.get("name"), str)
    }
    merged_models = [*proposed_models]
    for model in base_mdl:
        name = model.get("name") if isinstance(model, dict) else None
        if isinstance(name, str) and name not in proposed_names:
            merged_models.append(model)
    relationships = [
        rel for rel in proposed.get("relationships", []) or [] if isinstance(rel, dict)
    ]
    return merged_models, relationships


def _reconcile_overlay_with_base(
    base_models: list[dict[str, Any]], overlay: dict[str, Any]
) -> dict[str, Any]:
    """Structure-preserve an overlay manifest against all active base models (E4).

    Used on the multi-file fallback path, where there is no single owning file to
    patch: each overlay model that matches an existing active model by name is
    merged column-level so a touched model never loses columns; brand-new models
    pass through unchanged.
    """

    base_by_name = {
        model["name"]: model
        for model in base_models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
    }
    merged_models: list[dict[str, Any]] = []
    for model in overlay.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        base_model = base_by_name.get(model.get("name"))
        if isinstance(base_model, dict):
            merged_models.append(_merge_model_preserving_structure(base_model, model))
        else:
            merged_models.append(dict(model))
    out = dict(overlay)
    out["models"] = merged_models
    return out


def _dropped_columns(
    base_models: list[dict[str, Any]], proposed_content: str
) -> list[str]:
    """Return ``model.column`` for base columns missing from the proposal (E5).

    Defense-in-depth: with the structure-preserving merge a touched model can no
    longer drop a column, so a non-empty result signals a regression (or a
    genuinely ambiguous case) and is surfaced as a proposal warning. Models that
    are absent from the proposal entirely (they live in other files) are skipped,
    not reported.
    """

    try:
        proposed = json.loads(proposed_content)
    except (ValueError, TypeError):
        return []
    if not isinstance(proposed, dict):
        return []
    proposed_columns: dict[str, set[str]] = {}
    for model in proposed.get("models", []) or []:
        if not isinstance(model, dict) or not isinstance(model.get("name"), str):
            continue
        proposed_columns[model["name"]] = {
            col["name"]
            for col in model.get("columns", []) or []
            if isinstance(col, dict) and isinstance(col.get("name"), str)
        }
    dropped: list[str] = []
    for model in base_models:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        if not isinstance(name, str) or name not in proposed_columns:
            continue
        base_columns = {
            col["name"]
            for col in model.get("columns", []) or []
            if isinstance(col, dict) and isinstance(col.get("name"), str)
        }
        for column in sorted(base_columns - proposed_columns[name]):
            dropped.append(f"{name}.{column}")
    return dropped


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
            # The model keys semantics by the catalog name it was shown; when the
            # seed sanitized that name (``2003`` → ``_2003``) fall back to the
            # physical name so a renamed column still receives its description.
            physical = (column.get("properties") or {}).get("superset_column_name")
            if physical:
                match = llm_columns.get(physical)
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


def _default_overlay_path(project: SemanticProject, overlay: dict[str, Any]) -> str:
    """Fallback file path for an overlay whose own path is unusable.

    A views-only overlay (no models) lands under ``views/<name>.json`` to match
    Wren's project layout; everything else keeps the model-file default. The store
    is path-agnostic, so this is a tidiness convention, not a constraint.
    """

    views = overlay.get("views")
    if views and not overlay.get("models"):
        first = views[0]
        name = first.get("name") if isinstance(first, dict) else None
        return f"views/{_safe_name(str(name))}.json" if name else "views/view.json"
    return f"models/{_safe_name(project.schema_name)}.json"


def _safe_relative_path(path: str, *, default: str) -> str:
    candidate = (path or "").strip().replace("\\", "/").lstrip("/")
    if not candidate or ".." in candidate.split("/"):
        return default
    if candidate.endswith((".yaml", ".yml")):
        candidate = candidate.rsplit(".", 1)[0]
    if not candidate.endswith(".json"):
        candidate = f"{candidate}.json"
    return candidate
