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

"""Schema onboarding: introspect a schema into draft base MDL models."""

from __future__ import annotations

from typing import Protocol

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.integrations.superset.client import AgentContext
from superset_ai_agent.semantic_layer.mdl_files import MdlFileStore
from superset_ai_agent.semantic_layer.mdl_validator import SchemaIndex, validate_mdl
from superset_ai_agent.semantic_layer.schemas import (
    MdlEnrichmentProposal,
    MdlFileCreateRequest,
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
) -> OnboardingResult:
    """Generate draft base MDL files for a schema project.

    Files are always written as drafts; activation remains a human decision.
    """

    schema_index = SchemaIndex.from_agent_context(superset_context)
    proposals = wren_client.generate_base_model(
        project=project,
        superset_context=superset_context,
    )
    files = []
    warnings: list[str] = []
    if not superset_context.datasets:
        warnings.append(
            "No permission-filtered datasets were found for this schema."
        )
    for proposal in proposals:
        # Physical, schema-aware validation (R3): a hallucinated table/column
        # makes the draft non-activatable but is still written so a human can
        # correct it rather than silently losing the proposal.
        validation = validate_mdl(proposal.proposed_yaml, schema_index=schema_index)
        try:
            created = mdl_file_store.create(
                project.id,
                MdlFileCreateRequest(
                    path=proposal.proposed_path,
                    content=proposal.proposed_yaml,
                    source_type="onboarding",
                ),
                owner_id=owner_id,
                validation=validation,
            )
        except ValueError as ex:
            warnings.append(f"Skipped {proposal.proposed_path}: {ex}")
            continue
        files.append(created)
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
        warnings.extend(proposal.warnings)

    return OnboardingResult(
        project_id=project.id,
        files=files,
        model_count=len(files),
        warnings=list(dict.fromkeys(warnings)),
    )
