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

"""Schema onboarding: introspect a schema into base MDL models.

Onboarding seeds one base model per catalog table (structure from the catalog,
semantics optionally from the model) and — unless told otherwise — **auto-activates**
every model that passes structural + physical validation, so a freshly onboarded (or
reset) project lands on a populated, queryable semantic layer rather than a pile of
drafts. Models that fail validation stay draft with a warning so a human can fix them.
"""

from __future__ import annotations

from typing import Protocol

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.semantic_layer.mdl_files import (
    MdlFileStore,
    MdlFileValidationError,
)
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_mdl,
    validate_project_manifest,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    MdlFile,
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
    MdlValidationResult,
    OnboardingResult,
    SemanticProject,
)


class SupportsBaseModelGeneration(Protocol):
    """Subset of the Wren client used for onboarding."""

    def generate_base_model(
        self,
        *,
        project: SemanticProject,
        superset_context: AgentContext,
    ) -> list[MdlEnrichmentProposal]:
        """Return reviewable base MDL proposals from schema introspection."""


def onboard_schema_project(
    *,
    project: SemanticProject,
    superset_context: AgentContext,
    wren_client: SupportsBaseModelGeneration,
    mdl_file_store: MdlFileStore,
    owner_id: str = DEFAULT_OWNER_ID,
    auto_activate: bool = True,
    deep_validate: bool = False,
) -> OnboardingResult:
    """Generate base MDL files for a schema project.

    When ``auto_activate`` is set (the default), every generated model that passes
    structural + physical validation is activated so the layer is immediately
    queryable; a model that fails validation stays draft with a warning. Set
    ``auto_activate=False`` to keep the legacy draft-only behavior (review-first).

    Activation runs the SAME project-manifest gate as the manual activation
    route (``validate_project_manifest`` over the full to-be-activated set,
    with the deep wren-core compile pass when ``deep_validate`` is set — the
    caller passes the manual route's condition). Previously onboarding
    auto-activated on per-file validation only, so a file could activate here
    yet be un-reactivatable later on the manual route. When the whole set
    fails, each file is gated alone (base models are per-table independent);
    offenders stay draft with a warning instead of poisoning the active set.
    """

    schema_index = SchemaIndex.from_agent_context(superset_context)
    warnings: list[str] = []
    # Names-first live introspection (synthetic negative ids) lists tables
    # WITHOUT columns; bulk onboarding every one of them would either emit
    # column-less junk models or re-create the eager whole-schema reflection
    # this path must never pay. Skip them here — selective, tool-driven
    # onboarding via the Copilot is the intended route for a live catalog
    # (agentic choice grounded in the BI). Registered datasets are untouched.
    names_only = [d for d in superset_context.datasets if d.id < 0 and not d.columns]
    if names_only:
        skipped_ids = {d.id for d in names_only}
        superset_context = superset_context.model_copy(
            update={
                "datasets": [
                    d for d in superset_context.datasets if d.id not in skipped_ids
                ]
            }
        )
        warnings.append(
            f"{len(names_only)} live table(s) were listed without column "
            "metadata and skipped by bulk onboarding; onboard the specific "
            "tables you need via the MDL Copilot instead."
        )
    proposals = wren_client.generate_base_model(
        project=project,
        superset_context=superset_context,
    )
    files: list[MdlFile] = []
    #: (index into ``files``, per-file validation) for each activation candidate.
    staged: list[tuple[int, MdlValidationResult]] = []
    if not superset_context.datasets:
        warnings.append("No permission-filtered datasets were found for this schema.")
    for proposal in proposals:
        # Physical, schema-aware validation (R3): a hallucinated table/column
        # makes the draft non-activatable but is still written so a human can
        # correct it rather than silently losing the proposal.
        validation = validate_mdl(proposal.proposed_content, schema_index=schema_index)
        try:
            created = mdl_file_store.create(
                project.id,
                MdlFileCreateRequest(
                    path=proposal.proposed_path,
                    content=proposal.proposed_content,
                    source_type="onboarding",
                ),
                owner_id=owner_id,
                validation=validation,
            )
        except ValueError as ex:
            warnings.append(f"Skipped {proposal.proposed_path}: {ex}")
            continue
        if not validation.valid:
            warnings.append(
                f"{proposal.proposed_path} has validation errors and cannot be "
                "activated until fixed: "
                + "; ".join(
                    message.message
                    for message in validation.messages
                    if message.severity == "error"
                )
            )
        elif auto_activate:
            staged.append((len(files), validation))
        files.append(created)
        warnings.extend(proposal.warnings)

    if staged:
        _activate_staged(
            files=files,
            staged=staged,
            schema_index=schema_index,
            deep_validate=deep_validate,
            mdl_file_store=mdl_file_store,
            owner_id=owner_id,
            warnings=warnings,
        )

    activated = sum(1 for file in files if file.status == "active")
    return OnboardingResult(
        project_id=project.id,
        files=files,
        model_count=len(files),
        activated_count=activated,
        warnings=list(dict.fromkeys(warnings)),
    )


def _activate_staged(
    *,
    files: list[MdlFile],
    staged: list[tuple[int, MdlValidationResult]],
    schema_index: SchemaIndex,
    deep_validate: bool,
    mdl_file_store: MdlFileStore,
    owner_id: str,
    warnings: list[str],
) -> None:
    """Activate staged onboarding files through the project-manifest gate.

    Parity with the manual activation route: the WHOLE projected active set is
    validated as one manifest (cross-file references, keep-last dedup, optional
    deep wren-core compile) before anything activates. When the set fails, each
    file is gated alone — base models are per-table independent, so a solo pass
    isolates the offender(s); they stay draft with a warning instead of
    poisoning the active set. Mutates ``files`` (activated rows are replaced)
    and ``warnings`` in place.
    """

    manifest_validation = validate_project_manifest(
        [files[index].content for index, _ in staged],
        schema_index=schema_index,
        deep_validate=deep_validate,
        dedup_models=True,
    )
    if not manifest_validation.valid:
        approved: list[tuple[int, MdlValidationResult]] = []
        for index, validation in staged:
            solo = validate_project_manifest(
                [files[index].content],
                schema_index=schema_index,
                deep_validate=deep_validate,
                dedup_models=True,
            )
            if solo.valid:
                approved.append((index, validation))
            else:
                warnings.append(
                    f"{files[index].path} failed the project-manifest "
                    "activation gate and stays draft: "
                    + "; ".join(
                        message.message
                        for message in solo.messages
                        if message.severity == "error"
                    )
                )
        staged = approved
    for index, validation in staged:
        # Activate the freshly seeded model so the layer is queryable at once.
        # The store's activation gate is a final structural safety net, so a
        # surprise failure degrades to draft.
        try:
            files[index] = mdl_file_store.update(
                files[index].id,
                MdlFileUpdateRequest(status="active"),
                owner_id=owner_id,
                validation=validation,
            )
        except MdlFileValidationError as ex:
            warnings.append(f"{files[index].path} could not be auto-activated: {ex}")
