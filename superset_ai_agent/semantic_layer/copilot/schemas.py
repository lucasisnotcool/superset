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

"""MDL Copilot transient/display contracts.

A ``Changeset`` mirrors the ``MdlEnrichmentProposal`` precedent: it is a
*reviewable artifact, not stored MDL*. Nothing in a changeset is persisted until
the user accepts an item, at which point the existing per-file CRUD endpoints
apply it as a draft.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from superset_ai_agent.schemas import AgentStep
from superset_ai_agent.semantic_layer.schemas import MdlValidationResult

#: Default attachment ceiling (chars). The route enforces the configured value.
ATTACHMENT_MAX_CHARS = 200_000

ChangesetOp = Literal["create", "update", "delete"]

WorkspaceNodeKind = Literal[
    "folder",
    "mdl",
    "instructions",
    "queries",
    "document",
    "compiled",
    "memory",
    "config",
]


class ChangesetItem(BaseModel):
    """One proposed file operation in a copilot changeset (not yet persisted)."""

    op: ChangesetOp
    path: str
    #: Present for update/delete (the existing file being changed).
    file_id: str | None = None
    #: Diff base — the current on-disk content. ``None`` for a create.
    current_content: str | None = None
    #: The proposed content. ``None`` for a delete.
    proposed_content: str | None = None
    #: File-level validation of ``proposed_content`` (pre-validated server-side).
    validation: MdlValidationResult | None = None
    #: Short human label, e.g. "Add revenue metric to orders".
    summary: str = ""


class Changeset(BaseModel):
    """A reviewable set of proposed MDL edits produced by the copilot loop."""

    items: list[ChangesetItem] = Field(default_factory=list)
    #: Whole-project manifest validation after applying every item.
    manifest_validation: MdlValidationResult | None = None
    warnings: list[str] = Field(default_factory=list)
    #: The agentic loop's explain timeline (reuses the SQL agent's step shape).
    steps: list[AgentStep] = Field(default_factory=list)
    #: Free-text assistant summary of what was proposed (for the chat bubble).
    message: str = ""


class WorkspaceNode(BaseModel):
    """A node in the unified Wren-style workspace tree."""

    path: str
    name: str
    kind: WorkspaceNodeKind
    editable: bool = False
    #: draft|active for MDL files; None otherwise.
    status: str | None = None
    file_id: str | None = None
    #: Set on ``kind="document"`` nodes — the SemanticDocument id (selection key).
    document_id: str | None = None
    validation: MdlValidationResult | None = None
    children: list["WorkspaceNode"] = Field(default_factory=list)


class ToolDescriptor(BaseModel):
    """Read-only description of a copilot tool for the inspector."""

    name: str
    description: str = ""


class SkillDescriptor(BaseModel):
    """Read-only skill workflow text for the inspector."""

    name: str
    text: str


class InstructionView(BaseModel):
    """Project-scoped instruction surfaced in the inspector (editable elsewhere)."""

    id: str
    instruction: str
    is_global: bool = False


class CopilotInspector(BaseModel):
    """The effective agent context shown in the inspector drawer."""

    system_prompt: str
    skills: list[SkillDescriptor] = Field(default_factory=list)
    tools: list[ToolDescriptor] = Field(default_factory=list)
    instructions: list[InstructionView] = Field(default_factory=list)


class MessageAttachment(BaseModel):
    """An inline, ephemeral UTF-8 text attachment fed into the user message.

    MVP long-context model: no storage, no RAG. Distinct from persistent ``raw/``
    documents. ``text`` is truncated to the configured ceiling before the call.
    """

    filename: str = "attachment.txt"
    content_type: str = "text/plain"
    text: str = ""
    truncated: bool = False


class CopilotTurnRequest(BaseModel):
    """Request to run one agentic MDL-editing turn (returns a Changeset)."""

    message: str = Field(min_length=1)
    attachments: list[MessageAttachment] = Field(default_factory=list)
    conversation_id: str | None = None
    model: str | None = None
    max_steps: int = Field(default=8, ge=2, le=24)


class ChangesetApplyRequest(BaseModel):
    """Apply the user-accepted subset of a changeset's items as drafts."""

    items: list[ChangesetItem] = Field(default_factory=list)


# -- Coverage audit (markdown → MDL information-loss detection) --------------

CoverageClaimKind = Literal[
    "definition",
    "metric",
    "synonym",
    "relationship",
    "filter",
    "dimension",
    "rule",
    "other",
]

CoverageStatus = Literal["covered", "partial", "missing"]


class CoverageClaim(BaseModel):
    """One atomic, checkable assertion extracted from a source document."""

    kind: CoverageClaimKind = "other"
    subject: str = ""
    statement: str
    source_quote: str = ""


class CoverageFinding(BaseModel):
    """A claim aligned against the MDL: is its information captured?"""

    claim: CoverageClaim
    status: CoverageStatus = "missing"
    #: The MDL element/instruction that captures the claim, if any.
    matched: str = ""
    rationale: str = ""
    #: How to close the gap (feeds the copilot remediation loop).
    suggestion: str = ""


class OverreachFinding(BaseModel):
    """An MDL fact not supported by any claim in the document (over-reach)."""

    fact_ref: str
    fact_kind: str = ""
    supported: bool = True
    rationale: str = ""


class CoverageReport(BaseModel):
    """Coverage of a document's information by the project's MDL.

    ``score`` weights ``partial`` as 0.5. Advisory, not a gate: findings are
    LLM-judged and confidence varies (see wren_mdl_copilot.md coverage risks).
    ``overreach`` is the reverse direction — MDL claims unsupported by the document
    — populated only when the audit requests it.
    """

    document_id: str | None = None
    document_filename: str = ""
    findings: list[CoverageFinding] = Field(default_factory=list)
    total: int = 0
    covered: int = 0
    partial: int = 0
    missing: int = 0
    score: float = 0.0
    overreach: list[OverreachFinding] = Field(default_factory=list)
    unsupported: int = 0
    warnings: list[str] = Field(default_factory=list)


class CoverageRequest(BaseModel):
    """Run a coverage audit for one uploaded project document."""

    document_id: str
    model: str | None = None
    #: Also flag MDL facts unsupported by the document (reverse direction).
    include_overreach: bool = False


WorkspaceNode.model_rebuild()
