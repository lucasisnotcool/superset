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

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from superset_ai_agent.conversations.schemas import (
    ConversationScope,
    normalize_schema_names,
)

# Active values are "uploaded"/"extracted"/"error". "needs_review"/"approved"/
# "indexed" are legacy (the removed document-review/overlay flow) and are retained
# ONLY so pre-existing persisted rows still validate on read; nothing produces them.
SemanticDocumentStatus = Literal[
    "uploaded",
    "extracting",
    "extracted",
    "needs_ocr",
    "needs_review",
    "approved",
    "indexed",
    "error",
]
# "review_*"/"index_*" are legacy event types from the removed review/overlay flow,
# retained only for read-compat with persisted rows; current code emits the
# "document_*"/"onboarding_*" types (plus "index_failed" reused for extraction errors).
SemanticLayerEventType = Literal[
    "document_uploaded",
    "document_extracted",
    "review_required",
    "review_saved",
    "index_started",
    "index_completed",
    "index_failed",
    "onboarding_started",
    "onboarding_completed",
    "onboarding_failed",
    # MDL provenance (plan_onboarding_selection_and_provenance_impl.md, Feature B).
    # These record the editing history of the MDL directory and are deleted on reset.
    "mdl_created",
    "mdl_updated",
    "mdl_activated",
    "mdl_deleted",
    "document_enriched",
    # Copilot/agent edits applied via the changeset path, and background coverage
    # runs (plan_provenance_and_coverage_impl.md, Features A & B).
    "mdl_agent_edit",
    "coverage_completed",
    # Live, non-provenance coverage progress (Feature C): emitted at stage
    # boundaries to nudge the badge to re-poll; never a provenance timeline entry.
    "coverage_progress",
    # Non-provenance: the chained recovery agent finished and has gap-closing
    # suggestions to review. Wakes the notification banner; not a timeline entry.
    "recovery_suggestions_ready",
    # Project lifecycle: the origin entry stamped on a duplicated project
    # (plan_mdl_lab_spec.md DP8) recording its ``duplicated_from`` lineage.
    "mdl_project_created",
]
SemanticJobStatus = Literal["running", "completed", "failed"]

#: Event types that make up the MDL provenance timeline (Feature B). Document
#: upload/extract events are intentionally excluded — documents survive an MDL reset,
#: so their events must not be purged with the provenance log (delete-on-reset).
PROVENANCE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "onboarding_started",
        "onboarding_completed",
        "onboarding_failed",
        "mdl_created",
        "mdl_updated",
        "mdl_activated",
        "mdl_deleted",
        "document_enriched",
        "mdl_agent_edit",
        # ``coverage_completed`` is intentionally NOT a provenance entry: coverage
        # is a read-only annotation over an MDL version, surfaced as a label on the
        # version-producing entries (Feature B), not a timeline event of its own.
        "mdl_project_created",
    }
)
#: Max provenance entries returned by the read route (newest-first). History is
#: bounded per onboarding cycle by delete-on-reset; this caps pathological growth.
PROVENANCE_HISTORY_CAP = 500


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


class SemanticDocument(BaseModel):
    """Uploaded semantic-layer source document."""

    id: str = Field(default_factory=_new_id)
    project_id: str | None = None
    filename: str
    content_type: str
    size_bytes: int
    status: SemanticDocumentStatus = "uploaded"
    scope: ConversationScope
    checksum: str
    storage_uri: str
    summary: str | None = None
    extracted_text: str | None = None
    extracted_text_preview: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    # Transient, response-only flag: set True when an upload was short-circuited to
    # an existing byte-identical document (content-hash dedup). It is never
    # persisted — the storage mapper does not read it, so a reloaded document is
    # always ``deduplicated=False``.
    deduplicated: bool = False


class SemanticLayerState(BaseModel):
    """Semantic-layer document state for one Superset scope."""

    project_id: str | None = None
    database_id: int
    catalog_name: str | None = None
    schema_name: str | None = None
    #: Full schema set when the project spans multiple schemas (primary first).
    schema_names: list[str] | None = None
    dataset_ids: list[int] = Field(default_factory=list)
    document_count: int
    last_error: str | None = None


class InstructionCreateRequest(BaseModel):
    """Request to add a user-authored instruction for a scope (Wren guidance)."""

    scope: ConversationScope
    instruction: str
    is_global: bool = False


class SemanticLayerEvent(BaseModel):
    """Semantic-layer event for polling or server-sent events."""

    id: str = Field(default_factory=_new_id)
    project_id: str | None = None
    type: SemanticLayerEventType
    scope: ConversationScope
    document_id: str | None = None
    state: SemanticLayerState | None = None
    message: str
    #: Structured, event-specific provenance payload (path, file_id, source_type,
    #: dataset_ids, status transition, etc.). Round-trips via the event row's JSON
    #: ``payload`` column — no DB migration required.
    detail: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utc_now)


SemanticProjectVisibility = Literal["private", "db_access", "custom"]
SemanticProjectStatus = Literal["active", "archived"]
SemanticPermission = Literal["read", "write", "admin"]
MdlFileStatus = Literal["draft", "active", "deleted"]
MdlFileSourceType = Literal[
    "uploaded_mdl",
    "manual",
    "enriched_markdown",
    "onboarding",
    "copilot",
]
MdlContentType = Literal["application/json"]

#: Max characters kept from a slugified name (leaves room for a ``-NN`` suffix).
_SLUG_MAX_LEN = 80


def slugify_project_name(name: str) -> str:
    """Return a lowercase, hyphenated, identity-safe slug for a project name.

    Non-alphanumeric runs collapse to a single hyphen; leading/trailing hyphens are
    stripped; empty input falls back to ``project``. Uniqueness within a database is
    the store's responsibility (it appends ``-2``, ``-3`` on collision).
    """

    lowered = (name or "").strip().lower()
    out: list[str] = []
    prev_hyphen = False
    for char in lowered:
        if char.isalnum():
            out.append(char)
            prev_hyphen = False
        elif not prev_hyphen:
            out.append("-")
            prev_hyphen = True
    slug = "".join(out).strip("-")[:_SLUG_MAX_LEN].strip("-")
    return slug or "project"


class SemanticProject(BaseModel):
    """Wren semantic project spanning one or more schemas of a database.

    ``schema_name`` is the **primary** schema (the wren-core logical namespace and
    the back-compat scalar). ``schema_names`` is the full, ordered set the project
    covers, with the primary first; a model may reference a physical table in any
    member schema via its ``tableReference.schema``.
    """

    id: str = Field(default_factory=_new_id)
    name: str
    #: URL/identity-safe handle, unique within (database, catalog). Derived from
    #: ``name`` when not supplied; the store guarantees uniqueness (collision suffix).
    slug: str = ""
    description: str | None = None
    owner_id: str
    database_uri_fingerprint: str
    database_backend: str | None = None
    database_label: str | None = None
    catalog_name: str | None = None
    schema_name: str
    schema_names: list[str] = Field(default_factory=list)
    schema_display_name: str | None = None
    default_database_id: int | None = None
    visibility: SemanticProjectVisibility = "db_access"
    current_version_id: str | None = None
    status: SemanticProjectStatus = "active"
    permission: SemanticPermission = "admin"
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    deleted_at: datetime | None = None
    #: Transient, response-only: the latest complete coverage score (0–1) for the
    #: project's active MDL, populated only by the list/get routes so the MDL Lab
    #: browser can show a per-project coverage badge without an N+1 status fetch.
    #: ``None`` when coverage has never completed (or is disabled).
    coverage_score: float | None = None

    @model_validator(mode="after")
    def _sync_identity(self) -> "SemanticProject":
        """Keep the schema set consistent and ensure a derived ``slug``.

        The primary schema is always element 0 of the de-duplicated set. ``slug``
        defaults to a slugified ``name`` (the store layer makes it unique within the
        database/catalog); a single-schema, name-only project is unchanged otherwise.
        """

        names = normalize_schema_names(self.schema_name, self.schema_names)
        if names:
            if self.schema_names != names:
                self.schema_names = names
            if self.schema_name != names[0]:
                self.schema_name = names[0]
        if not self.slug:
            self.slug = slugify_project_name(self.name)
        return self


class SemanticProjectResolveRequest(BaseModel):
    """Resolve or create the semantic project for a database/catalog/schema(s)."""

    database_id: int
    database_label: str | None = None
    database_backend: str | None = None
    catalog_name: str | None = None
    schema_name: str
    #: Optional additional schemas to scope the project to (primary stays
    #: ``schema_name``). Back-compat: callers may send only ``schema_name``.
    schema_names: list[str] | None = None
    #: Optional explicit project name (the MDL Lab "create named project" path).
    #: When absent the default schema-derived name is used.
    name: str | None = None
    supplied_uri: str | None = None
    database_uri_fingerprint: str | None = None
    create_if_missing: bool = True

    def resolved_schema_names(self) -> list[str]:
        """Ordered, de-duplicated requested schema set with the primary first."""

        return normalize_schema_names(self.schema_name, self.schema_names)


class SemanticProjectRenameRequest(BaseModel):
    """Rename a semantic project (the MDL Lab rename action)."""

    name: str


class SemanticProjectDuplicateRequest(BaseModel):
    """Duplicate a semantic project; optional new name (default ``<source> (copy)``)."""

    name: str | None = None
    #: DP6 opt-in: also copy the project's BI documents + chunks into the clone and
    #: re-embed them under the clone's vector scope. Default off — the structural
    #: clone (MDL + schema set) carries no documents/coverage/history.
    include_documents: bool = False


class MdlValidationMessage(BaseModel):
    """MDL validation message for editor annotations."""

    line: int | None = None
    column: int | None = None
    severity: Literal["error", "warning", "info"] = "error"
    message: str
    code: str | None = None


class MdlValidationResult(BaseModel):
    """Validation result for one MDL JSON file."""

    valid: bool
    messages: list[MdlValidationMessage] = Field(default_factory=list)


class MdlFile(BaseModel):
    """One JSON file in a schema-scoped Wren MDL project (native manifest shape)."""

    id: str = Field(default_factory=_new_id)
    project_id: str
    path: str
    filename: str
    content: str
    content_type: MdlContentType = "application/json"
    source_type: MdlFileSourceType = "manual"
    status: MdlFileStatus = "draft"
    validation: MdlValidationResult | None = None
    checksum: str
    source_document_id: str | None = None
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    deleted_at: datetime | None = None


class MdlFileCreateRequest(BaseModel):
    """Create a draft MDL JSON file."""

    path: str
    content: str
    source_type: MdlFileSourceType = "manual"
    source_document_id: str | None = None


class MdlFileUpdateRequest(BaseModel):
    """Update an MDL JSON file."""

    path: str | None = None
    content: str | None = None
    status: MdlFileStatus | None = None
    #: Re-stamp the file's origin (e.g. agent enrichment grounded on a document
    #: changes ``copilot`` → ``enriched_markdown``). ``None`` leaves it unchanged.
    source_type: MdlFileSourceType | None = None
    #: Link the file to the source document it was (re-)derived from (R-B6).
    #: ``None`` leaves the existing link unchanged.
    source_document_id: str | None = None


class MdlBulkStatusRequest(BaseModel):
    """Set the status of many MDL files in one atomic operation.

    ``file_ids=None`` targets every file in the project not already at ``status``
    (the "Activate all" / "Deactivate all" rail action). Activation validates the
    whole projected active manifest *once*, so files can be activated together
    regardless of cross-file dependency order (a metric and the model it
    references need not be toggled in sequence).
    """

    status: MdlFileStatus
    file_ids: list[str] | None = None


class MdlBulkStatusResult(BaseModel):
    """Outcome of a bulk status change: the project's files and how many changed."""

    files: list[MdlFile]
    changed_count: int


class MdlEnrichmentProposal(BaseModel):
    """Proposed MDL (native JSON) generated from a source document."""

    source_document_id: str
    proposed_path: str
    proposed_content: str
    validation: MdlValidationResult
    warnings: list[str] = Field(default_factory=list)


class WrenMaterializationResult(BaseModel):
    """Result of writing active MDL files into a Wren project directory."""

    project_id: str
    path: str
    file_count: int
    checksum: str
    warnings: list[str] = Field(default_factory=list)


OnboardingMode = Literal["all", "include"]


class OnboardingRequest(BaseModel):
    """Which tables to onboard into the semantic layer (Feature A).

    The unit is a Superset *dataset* (a registered table). An empty request means
    ``mode="all"`` with no excludes — exactly the legacy whole-schema onboard, so
    existing callers are unchanged.

    - ``mode="include"``: onboard exactly ``dataset_ids``.
    - ``mode="all"``: onboard every dataset in the project's schema, minus
      ``exclude_dataset_ids``, optionally narrowed by a ``search`` (table-name
      substring). With no search/excludes this is the full-schema path.
    """

    mode: OnboardingMode = "all"
    dataset_ids: list[int] = Field(default_factory=list)
    exclude_dataset_ids: list[int] = Field(default_factory=list)
    search: str | None = None


class OnboardingResult(BaseModel):
    """Result of generating base MDL from schema introspection."""

    project_id: str
    files: list[MdlFile] = Field(default_factory=list)
    model_count: int = 0
    activated_count: int = 0
    warnings: list[str] = Field(default_factory=list)


ProvenanceKind = Literal[
    "onboarding",
    "enrichment",
    "copilot_edit",
    "coverage",
    "mdl_created",
    "mdl_updated",
    "mdl_activated",
    "mdl_deleted",
    "project_created",
]

#: Who is responsible for a timeline entry. Drives the dialog's icon/label and,
#: crucially, coalescing: only contiguous ``user`` runs collapse into one entry.
#: ``actor`` is always the owner id (the human owns the project even when driving
#: the Copilot), so origin must be derived from the file ``source_type``/``kind``.
ActorType = Literal["user", "agent", "system"]


class ProvenanceEntry(BaseModel):
    """One entry in the MDL directory's provenance timeline (Feature B).

    A flattened, UI-ready projection of a provenance ``SemanticLayerEvent`` — the
    dialog renders these directly (reusing the AI Explain timeline shell).
    """

    id: str
    kind: ProvenanceKind
    status: Literal["ok", "warning", "error"] = "ok"
    summary: str
    created_at: datetime
    actor: str | None = None
    #: Human-readable author name (username/email) captured at write time (DP10).
    #: ``None`` for historical events / system actors; the UI falls back to ``actor``
    #: (the owner id) then to a generic "Teammate" label.
    actor_name: str | None = None
    actor_type: ActorType = "system"
    #: True when the viewer is the actor (DP10). Computed at read time by the
    #: endpoint, which knows the requesting identity; drives "You" vs the actor's id
    #: in a shared (multi-user) project. Defaults True so single-user history reads
    #: unchanged.
    is_self: bool = True
    #: Number of raw events merged into this entry (>1 only for coalesced user runs).
    edit_count: int = 1
    #: Earliest timestamp in a coalesced user run (``None`` when ``edit_count == 1``).
    first_at: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


#: Maps provenance ``SemanticLayerEventType`` values to the dialog's ``ProvenanceKind``.
#: Onboarding start/fail collapse onto the ``onboarding`` kind (status conveys outcome);
#: ``document_enriched`` maps to ``enrichment``; ``mdl_agent_edit`` to ``copilot_edit``.
_PROVENANCE_KIND_BY_EVENT: dict[str, ProvenanceKind] = {
    "onboarding_started": "onboarding",
    "onboarding_completed": "onboarding",
    "onboarding_failed": "onboarding",
    "document_enriched": "enrichment",
    "mdl_agent_edit": "copilot_edit",
    # ``coverage_completed`` is deliberately omitted — coverage is a version
    # label overlay, not a provenance entry (Feature B). It returns None here.
    "mdl_created": "mdl_created",
    "mdl_updated": "mdl_updated",
    "mdl_activated": "mdl_activated",
    "mdl_deleted": "mdl_deleted",
    "mdl_project_created": "project_created",
}


def actor_type_for(kind: ProvenanceKind, source_type: str | None) -> ActorType:
    """Classify a provenance entry's origin for display and coalescing.

    ``user`` is reserved for hand edits (manual / uploaded MDL) so that only
    those collapse into a single timeline entry; agent (Copilot/enrichment) and
    system (onboarding/coverage) entries always stand alone.
    """

    if kind in ("onboarding", "coverage", "project_created"):
        return "system"
    if kind in ("enrichment", "copilot_edit"):
        return "agent"
    # ``mdl_*`` CRUD events: classify by the file's recorded ``source_type``.
    if source_type in ("manual", "uploaded_mdl"):
        return "user"
    if source_type in ("copilot", "enriched_markdown"):
        return "agent"
    if source_type == "onboarding":
        return "system"
    # Manual-CRUD REST routes always stamp ``source_type``; default to user.
    return "user"


def provenance_from_event(event: SemanticLayerEvent) -> ProvenanceEntry | None:
    """Project a provenance event into a ``ProvenanceEntry`` (else ``None``).

    Non-provenance events (e.g. ``document_uploaded``) return ``None`` so the
    timeline shows only MDL-directory operations.
    """

    kind = _PROVENANCE_KIND_BY_EVENT.get(event.type)
    if kind is None:
        return None
    detail = event.detail or {}
    status: Literal["ok", "warning", "error"] = "ok"
    if event.type == "onboarding_failed":
        status = "error"
    elif detail.get("warnings"):
        status = "warning"
    return ProvenanceEntry(
        id=event.id,
        kind=kind,
        status=status,
        summary=event.message,
        created_at=event.created_at,
        actor=detail.get("actor"),
        actor_name=detail.get("actor_name"),
        actor_type=actor_type_for(kind, detail.get("source_type")),
        detail=detail,
    )


def _coalesced_paths(run: list[ProvenanceEntry]) -> list[str]:
    """Union the file paths touched across a coalesced user run (order-preserving)."""

    paths: list[str] = []
    for entry in run:
        candidates = list(entry.detail.get("paths") or [])
        single = entry.detail.get("path")
        if isinstance(single, str):
            candidates.append(single)
        for path in candidates:
            if isinstance(path, str) and path not in paths:
                paths.append(path)
    return paths


def _is_user_edit(entry: ProvenanceEntry) -> bool:
    """A hand ``mdl_updated`` save — the only entry that coalesces.

    Lifecycle actions (create / activate / delete) are distinct events a user
    expects to see individually; only repeated content saves are noise worth
    collapsing, matching the "edit MDL at 2pm then 5pm = one change" intent.
    """

    return entry.actor_type == "user" and entry.kind == "mdl_updated"


def coalesce_user_runs(entries: list[ProvenanceEntry]) -> list[ProvenanceEntry]:
    """Collapse contiguous runs of user edits into a single timeline entry.

    ``entries`` must be sorted newest-first. A maximal run of adjacent user
    ``mdl_updated`` entries becomes one entry stamped at the run's latest
    timestamp (``first_at`` = earliest, ``edit_count`` = run size, ``summary`` =
    "Edited N times"). Anything else (agent/enrichment/coverage/onboarding, or a
    user create/activate/delete) breaks the run and passes through unchanged — so
    an agent edit between two user edits keeps them in separate runs. Mirrors how
    editors group an editing session (e.g. Google Docs version history) and keeps
    the raw event log append-only.
    """

    result: list[ProvenanceEntry] = []
    run: list[ProvenanceEntry] = []

    def flush() -> None:
        if not run:
            return
        if len(run) == 1:
            result.append(run[0])
        else:
            newest, oldest = run[0], run[-1]  # newest-first ordering
            detail = dict(newest.detail)
            paths = _coalesced_paths(run)
            if paths:
                detail["paths"] = paths
            result.append(
                newest.model_copy(
                    update={
                        "edit_count": len(run),
                        "first_at": oldest.created_at,
                        "summary": f"Edited {len(run)} times",
                        "detail": detail,
                    }
                )
            )
        run.clear()

    for entry in entries:
        if _is_user_edit(entry):
            # Only merge a contiguous run by the SAME actor — in a shared project
            # (DP10) two users' adjacent edits must stay distinct rows, not collapse
            # into one mis-attributed "Edited N times".
            if run and run[-1].actor != entry.actor:
                flush()
            run.append(entry)
            continue
        flush()
        result.append(entry)
    flush()
    return result


SemanticProjectReadinessStatus = Literal["empty", "indexing", "ready", "failed"]


class SemanticProjectReadiness(BaseModel):
    """Whether a project's MDL is initialized and stabilized enough for the Copilot.

    The MDL Copilot must only begin editing work once the base layer has been
    onboarded and is no longer being written. ``ready`` is the single gate the
    frontend (spinner) and backend (409) both consult:

    - ``empty``    — never onboarded; no active models and no job running.
    - ``indexing`` — an onboarding/reset job is in flight; copilot is blocked.
    - ``ready``    — at least one active model and nothing in flight.
    - ``failed``   — the last onboarding failed; needs a retry.
    """

    status: SemanticProjectReadinessStatus
    ready: bool
    has_active_models: bool
    active_model_count: int = 0
    running_job_id: str | None = None
    detail: str = ""


#: One precondition that contributes to whether the agent runs in semantic-SQL
#: mode. ``met`` — satisfied; ``blocked`` — unsatisfied and required; ``runtime``
#: — only knowable when a query runs (not asserted ahead of time); ``not_applicable``
#: — moot given another unmet factor (e.g. active models when no project is picked).
SemanticFactorState = Literal["met", "blocked", "runtime", "not_applicable"]

#: Who can clear a blocking factor — drives the UI's remediation copy and whether
#: the badge shows a user-actionable warning (``user``) versus a factual,
#: not-here-fixable note (``operator``/``database``).
SemanticFactorFixableBy = Literal["operator", "database", "user", "runtime"]


class SemanticModeFactor(BaseModel):
    """A single named precondition for semantic-SQL mode, with its current state."""

    key: str
    label: str
    state: SemanticFactorState
    blocking: bool
    detail: str
    fixable_by: SemanticFactorFixableBy


class SemanticModeStatus(BaseModel):
    """Whether the AI SQL agent will apply semantic rewrite in the current scope.

    ``mode`` reflects what the user actually experiences — ``semantic`` only when
    every deterministic precondition (factors 1-7) is met — rather than the narrow
    authoring-guidance flag, which is ``True`` even on an unsupported dialect and
    would mislead the badge. The runtime factor (semantic context loaded) is
    advisory and never blocks the ``semantic`` verdict ahead of a query.
    """

    mode: Literal["semantic", "native"]
    factors: list[SemanticModeFactor]
    blocking_factors: list[str] = Field(default_factory=list)
    user_fixable_blocker: bool = False


class SemanticDocumentTextRequest(BaseModel):
    """Create a semantic source document from pasted text."""

    filename: str = "document.md"
    text: str
    content_type: str = "text/markdown"


class SemanticJob(BaseModel):
    """Async semantic-layer job (e.g. schema onboarding) for polling."""

    id: str = Field(default_factory=_new_id)
    kind: str
    status: SemanticJobStatus = "running"
    project_id: str | None = None
    result: OnboardingResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
