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

"""MDL Copilot service layer.

Glues the toolset + agentic loop to the real stores, applies an accepted
changeset via the existing per-file CRUD, and assembles the inspector view.
Kept free of FastAPI so it is unit-testable with in-memory stores.
"""

from __future__ import annotations

import logging
from typing import Protocol

from superset_ai_agent.llm.base import ModelClient
from superset_ai_agent.semantic_layer.copilot.loop import (
    build_system_prompt,
    run_copilot_loop,
    StepSink,
)
from superset_ai_agent.semantic_layer.copilot.schemas import (
    Changeset,
    ChangesetItem,
    CopilotInspector,
    InstructionView,
    SkillDescriptor,
    ToolDescriptor,
)
from superset_ai_agent.semantic_layer.copilot.tools import MdlToolset
from superset_ai_agent.semantic_layer.mdl_validator import (
    SchemaIndex,
    validate_project_manifest,
)
from superset_ai_agent.semantic_layer.schemas import (
    MdlFile,
    MdlFileCreateRequest,
    MdlFileUpdateRequest,
)
from superset_ai_agent.skills import get_skill, list_skills

logger = logging.getLogger(__name__)

#: Skills the copilot system prompt is grounded on (Wren parity: the agent
#: follows these procedures). Surfaced read-only in the inspector.
COPILOT_SKILLS = ("generate-mdl", "enrich-context")


class _MdlFileStoreLike(Protocol):
    def create(
        self,
        project_id: str,
        request: MdlFileCreateRequest,
        *,
        owner_id: str = ...,
        validation: object | None = ...,
    ) -> MdlFile: ...

    def update(
        self,
        file_id: str,
        request: MdlFileUpdateRequest,
        *,
        owner_id: str = ...,
        validation: object | None = ...,
    ) -> MdlFile: ...

    def delete(self, file_id: str, *, owner_id: str = ...) -> None: ...


def _skill_texts(names: tuple[str, ...] = COPILOT_SKILLS) -> list[tuple[str, str]]:
    available = set(list_skills())
    texts: list[tuple[str, str]] = []
    for name in names:
        if name in available:
            try:
                texts.append((name, get_skill(name)))
            except OSError:  # pragma: no cover - skill file unreadable
                logger.warning("Skill %r could not be read.", name)
    return texts


def run_copilot(
    *,
    model_client: ModelClient,
    files: list[MdlFile],
    schema_index: SchemaIndex | None,
    user_message: str,
    attachments_text: str = "",
    instructions: list[str] | None = None,
    model: str | None = None,
    max_steps: int = 8,
    max_correction_retries: int = 1,
    deep_validate: bool = False,
    on_step: StepSink | None = None,
) -> Changeset:
    """Run one agentic MDL-editing turn against the project's files."""

    toolset = MdlToolset(
        [f for f in files if f.status != "deleted"],
        schema_index=schema_index,
        deep_validate=deep_validate,
    )
    return run_copilot_loop(
        model_client=model_client,
        toolset=toolset,
        user_message=user_message,
        attachments_text=attachments_text,
        instructions=instructions,
        skills=[text for _name, text in _skill_texts()],
        model=model,
        max_steps=max_steps,
        max_correction_retries=max_correction_retries,
        on_step=on_step,
    )


def apply_changeset_items(
    store: _MdlFileStoreLike,
    *,
    project_id: str,
    items: list[ChangesetItem],
    owner_id: str,
) -> list[MdlFile]:
    """Persist accepted changeset items as drafts via the existing CRUD.

    Activation/Deploy stays a separate, human action — created/updated files land
    as drafts (the store's default status).
    """

    applied: list[MdlFile] = []
    for item in items:
        if item.op == "create":
            if not item.proposed_content:
                continue
            applied.append(
                store.create(
                    project_id,
                    MdlFileCreateRequest(
                        path=item.path,
                        content=item.proposed_content,
                        source_type="copilot",
                    ),
                    owner_id=owner_id,
                    validation=item.validation,
                )
            )
        elif item.op == "update":
            if not item.file_id or not item.proposed_content:
                continue
            applied.append(
                store.update(
                    item.file_id,
                    MdlFileUpdateRequest(content=item.proposed_content),
                    owner_id=owner_id,
                    validation=item.validation,
                )
            )
        elif item.op == "delete":
            if not item.file_id:
                continue
            store.delete(item.file_id, owner_id=owner_id)
    return applied


def build_deploy_preview(
    files: list[MdlFile],
    *,
    schema_index: SchemaIndex | None = None,
    deep_validate: bool = False,
) -> Changeset:
    """Preview what activating all drafts would change (Wren "Deploy" diff).

    Returns a changeset of draft→active transitions (each draft diffed against the
    active file at the same path, if any) plus the manifest validation of the
    resulting active set. Read-only: nothing is mutated.
    """

    live = [f for f in files if f.status != "deleted"]
    active_by_path = {f.path: f for f in live if f.status == "active"}
    drafts = [f for f in live if f.status == "draft"]

    items: list[ChangesetItem] = []
    for draft in sorted(drafts, key=lambda file: file.path):
        current = active_by_path.get(draft.path)
        items.append(
            ChangesetItem(
                op="update" if current else "create",
                path=draft.path,
                file_id=draft.id,
                current_content=current.content if current else None,
                proposed_content=draft.content,
                validation=draft.validation,
                summary=(
                    f"Activate {draft.path}"
                    if current is None
                    else f"Update active {draft.path}"
                ),
            )
        )

    # The would-be-active manifest: active files, with drafts superseding their
    # same-path active counterpart.
    resulting = {f.path: f.content for f in live if f.status == "active"}
    for draft in drafts:
        resulting[draft.path] = draft.content
    manifest_validation = validate_project_manifest(
        list(resulting.values()),
        schema_index=schema_index,
        deep_validate=deep_validate,
        dedup_models=True,
    )
    return Changeset(
        items=items,
        manifest_validation=manifest_validation,
        message=(
            f"{len(items)} draft(s) would be activated."
            if items
            else "No drafts to deploy."
        ),
    )


def build_inspector(
    *,
    instructions: list[InstructionView] | None = None,
) -> CopilotInspector:
    """Assemble the inspector view: prompt, skills, tools, instructions."""

    skills = [SkillDescriptor(name=name, text=text) for name, text in _skill_texts()]
    system_prompt = build_system_prompt(
        instructions=[i.instruction for i in (instructions or [])],
        skills=[s.text for s in skills],
    )
    tools = [
        ToolDescriptor(name=spec.name, description=spec.description)
        for spec in MdlToolset([]).specs()
    ]
    return CopilotInspector(
        system_prompt=system_prompt,
        skills=skills,
        tools=tools,
        instructions=instructions or [],
    )
