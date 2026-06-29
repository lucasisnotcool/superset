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

from superset_ai_agent.conversations.schemas import (
    Conversation,
    ConversationArtifact,
)
from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.llm.embeddings import Embedder
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
    ToolCallRecord,
    ToolDescriptor,
)
from superset_ai_agent.semantic_layer.copilot.tools import DocumentReader, MdlToolset
from superset_ai_agent.semantic_layer.document_retriever import DocumentChunkIndex
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
#: follows these procedures). Surfaced read-only in the inspector. ``onboarding``
#: gives the agent the schema → base-MDL procedure so it can assist with (or drive)
#: onboarding, matching Wren v2's onboarding/generate-mdl/enrich-context triad.
COPILOT_SKILLS = ("onboarding", "generate-mdl", "enrich-context")


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
    history: list[ChatMessage] | None = None,
    model: str | None = None,
    max_steps: int = 8,
    max_correction_retries: int = 1,
    tool_result_max_chars: int = 4000,
    deep_validate: bool = False,
    autopilot: bool = False,
    on_step: StepSink | None = None,
    document_store: DocumentReader | None = None,
    document_index: DocumentChunkIndex | None = None,
    project_id: str | None = None,
    owner_id: str | None = None,
    retrieve_k: int = 8,
    embedder: Embedder | None = None,
) -> Changeset:
    """Run one agentic MDL-editing turn against the project's files.

    ``history`` carries prior conversation turns for multi-turn memory; it is
    passed through to the loop unchanged (assembled by the caller from the
    persisted thread via ``ConversationTurnService``).
    """

    toolset = MdlToolset(
        [f for f in files if f.status != "deleted"],
        schema_index=schema_index,
        deep_validate=deep_validate,
        document_store=document_store,
        document_index=document_index,
        project_id=project_id,
        owner_id=owner_id,
        retrieve_k=retrieve_k,
        # Coverage self-review (read-only ``run_coverage`` tool) reuses the turn's
        # model client + retrieval embedder; instructions ground the audit.
        model_client=model_client,
        embedder=embedder,
        instructions=instructions,
    )
    return run_copilot_loop(
        model_client=model_client,
        toolset=toolset,
        user_message=user_message,
        attachments_text=attachments_text,
        instructions=instructions,
        skills=[text for _name, text in _skill_texts()],
        history=history,
        model=model,
        max_steps=max_steps,
        max_correction_retries=max_correction_retries,
        tool_result_max_chars=tool_result_max_chars,
        autopilot=autopilot,
        on_step=on_step,
    )


#: Conversation-artifact discriminator for a persisted Copilot changeset. A resumed
#: thread re-renders past proposals from the artifact's generic ``payload``.
CHANGESET_ARTIFACT_TYPE = "changeset"


def changeset_to_artifact(changeset: Changeset) -> ConversationArtifact:
    """Wrap a reviewable changeset as a generic conversation artifact.

    Keeps the conversation layer agent-agnostic: the changeset rides in the
    artifact's opaque ``payload`` (no typed import into ``conversations/``).
    """

    return ConversationArtifact(
        type=CHANGESET_ARTIFACT_TYPE,
        payload=changeset.model_dump(mode="json"),
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
        # R-B6: a file the agent derived from a single source document is recorded
        # as ``enriched_markdown`` + linked to that document; everything else the
        # Copilot writes stays a generic ``copilot`` edit.
        source_type = item.source_type or (
            "enriched_markdown" if item.source_document_id else "copilot"
        )
        if item.op == "create":
            if not item.proposed_content:
                continue
            applied.append(
                store.create(
                    project_id,
                    MdlFileCreateRequest(
                        path=item.path,
                        content=item.proposed_content,
                        source_type=source_type,
                        source_document_id=item.source_document_id,
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
                    MdlFileUpdateRequest(
                        content=item.proposed_content,
                        # Only re-stamp origin when this edit is doc-grounded;
                        # a plain agent edit must not relabel a manual file.
                        source_type=(source_type if item.source_document_id else None),
                        source_document_id=item.source_document_id,
                    ),
                    owner_id=owner_id,
                    validation=item.validation,
                )
            )
        elif item.op == "delete":
            if not item.file_id:
                continue
            store.delete(item.file_id, owner_id=owner_id)
    return applied


def changeset_from_conversation(conversation: Conversation) -> Changeset | None:
    """Return the most recent persisted changeset artifact, if any.

    The apply request carries only the accepted items (not the agent's summary or
    the documents it consulted), so provenance reads the server-authoritative
    changeset back from the conversation transcript (it cannot be spoofed by the
    client). Returns ``None`` when the thread has no changeset artifact.
    """

    for message in reversed(conversation.messages):
        for artifact in reversed(message.artifacts):
            if artifact.type == CHANGESET_ARTIFACT_TYPE:
                try:
                    return Changeset.model_validate(artifact.payload)
                except (ValueError, TypeError):
                    return None
    return None


#: Ceiling on tool-call records folded into one apply event's detail. Bounds the
#: stored ``payload`` JSON for a pathological turn (R3); the per-verb
#: ``action_summary`` is always computed over the *full* set, so a truncated
#: ledger still rolls up correctly — only the expandable member list is capped.
TOOL_CALL_DETAIL_CAP = 100


def apply_provenance_payload(
    *,
    items: list[ChangesetItem],
    owner_id: str,
    actor_name: str | None = None,
    conversation_id: str | None,
    summary: str | None,
    documents: list[dict[str, str | None]],
    tool_calls: list[ToolCallRecord] | None = None,
) -> tuple[str, str, dict[str, object]]:
    """Build the (event_type, message, detail) for an agent-apply provenance event.

    Classifies the apply as an ``enrichment`` pass when the agent was grounded on
    documents — retrieved passages or inline attachments (``documents`` non-empty)
    — else a generic ``copilot_edit``. ``ops``/``paths`` derive from the accepted
    items; ``documents`` are ``{id, filename}`` pairs (id is ``None`` for inline
    attachments, which have no persisted document). ``tool_calls`` (when present)
    are folded into ``detail.tool_calls`` with a derived ``action_summary``
    per-verb rollup so the timeline can present "onboarded 3 · wrote 4" without
    re-deriving it client-side.
    """

    ops = {"create": 0, "update": 0, "delete": 0}
    paths: list[str] = []
    for item in items:
        ops[item.op] = ops.get(item.op, 0) + 1
        paths.append(item.path)
    is_enrichment = bool(documents)
    event_type = "document_enriched" if is_enrichment else "mdl_agent_edit"
    label = (summary or "").strip()
    if not label:
        total = len(items)
        noun = "change" if total == 1 else "changes"
        label = f"{'Enriched' if is_enrichment else 'Applied'} {total} {noun}"
    detail: dict[str, object] = {
        "actor": owner_id,
        "source_type": "copilot",
        "conversation_id": conversation_id,
        "summary": label,
        "ops": ops,
        "paths": paths,
        "documents": documents,
    }
    if actor_name:
        detail["actor_name"] = actor_name
    records = tool_calls or []
    if records:
        # Roll up over the FULL ledger (before any cap) so counts stay honest.
        action_summary: dict[str, int] = {}
        for record in records:
            action_summary[record.action] = action_summary.get(record.action, 0) + 1
        detail["action_summary"] = action_summary
        stored = [record.model_dump(mode="json") for record in records]
        if len(stored) > TOOL_CALL_DETAIL_CAP:
            detail["tool_calls_truncated"] = len(stored) - TOOL_CALL_DETAIL_CAP
            stored = stored[:TOOL_CALL_DETAIL_CAP]
        detail["tool_calls"] = stored
    return event_type, label, detail


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
    autopilot: bool = False,
) -> CopilotInspector:
    """Assemble the inspector view: prompt, skills, tools, instructions.

    ``autopilot`` mirrors the live runtime flag so the previewed system prompt
    shows the same ``## Active mode`` banner the loop will actually send.
    """

    skills = [SkillDescriptor(name=name, text=text) for name, text in _skill_texts()]
    system_prompt = build_system_prompt(
        instructions=[i.instruction for i in (instructions or [])],
        skills=[s.text for s in skills],
        mode="autopilot" if autopilot else "grill",
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
