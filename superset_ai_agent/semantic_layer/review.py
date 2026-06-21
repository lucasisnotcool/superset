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
from typing import Any

from superset_ai_agent.conversations.store import DEFAULT_OWNER_ID
from superset_ai_agent.semantic_layer.schemas import (
    SemanticDocument,
    SemanticLayerReviewRequest,
    SemanticUpdate,
)
from superset_ai_agent.semantic_layer.store import SemanticLayerStore


def propose_updates(
    document: SemanticDocument,
    *,
    max_updates: int = 8,
) -> list[SemanticUpdate]:
    """Create deterministic review candidates from extracted document text."""

    text = document.extracted_text or ""
    if not text.strip():
        return []

    updates: list[SemanticUpdate] = [
        SemanticUpdate(
            kind="model_description",
            target=_scope_target(document),
            value={
                "description": _summarize_text(text),
                "source": document.filename,
            },
            confidence=0.55,
            source_document_id=document.id,
        )
    ]
    for line in _meaningful_lines(text):
        lowered = line.lower()
        if "synonym" in lowered or "also known as" in lowered:
            updates.append(
                SemanticUpdate(
                    kind="synonym",
                    target=_scope_target(document),
                    value={"text": line},
                    confidence=0.5,
                    source_document_id=document.id,
                )
            )
        elif "metric" in lowered or "measure" in lowered or "=" in line:
            updates.append(
                SemanticUpdate(
                    kind="metric",
                    target=_scope_target(document),
                    value={"definition": line},
                    confidence=0.45,
                    source_document_id=document.id,
                )
            )
        elif "?" in line or lowered.startswith(("show ", "list ", "compare ")):
            updates.append(
                SemanticUpdate(
                    kind="example",
                    target=_scope_target(document),
                    value={"question": line},
                    confidence=0.5,
                    source_document_id=document.id,
                )
            )
        if len(updates) >= max_updates:
            break
    return updates[:max_updates]


def apply_review(
    store: SemanticLayerStore,
    *,
    document_id: str,
    request: SemanticLayerReviewRequest,
    owner_id: str = DEFAULT_OWNER_ID,
    reviewer_id: str | None = None,
) -> SemanticDocument:
    """Apply a human review decision to proposed semantic updates."""

    document = store.get_document(document_id, owner_id=owner_id)
    updates_by_id = {update.id: update for update in document.proposed_updates}
    now = _utc_now()

    for update_id in request.approved_update_ids:
        if update_id in updates_by_id:
            updates_by_id[update_id] = _reviewed_update(
                updates_by_id[update_id],
                approved=True,
                reviewer_id=reviewer_id,
                notes=request.notes,
                now=now,
            )
    for update_id in request.rejected_update_ids:
        if update_id in updates_by_id:
            updates_by_id[update_id] = _reviewed_update(
                updates_by_id[update_id],
                approved=False,
                reviewer_id=reviewer_id,
                notes=request.notes,
                now=now,
            )
    for edited_update in request.edited_updates:
        updates_by_id[edited_update.id] = _reviewed_update(
            edited_update.model_copy(
                update={"source_document_id": document.id},
            ),
            approved=edited_update.approved,
            reviewer_id=reviewer_id,
            notes=request.notes or edited_update.review_notes,
            now=now,
        )

    reviewed_updates = list(updates_by_id.values())
    store.save_updates(document.id, reviewed_updates, owner_id=owner_id)
    has_approved = any(
        update.reviewed and update.approved for update in reviewed_updates
    )
    document = document.model_copy(
        update={
            "status": "approved" if has_approved else "extracted",
            "proposed_updates": reviewed_updates,
            "updated_at": now,
        }
    )
    return store.update_document(document, owner_id=owner_id)


def _reviewed_update(
    update: SemanticUpdate,
    *,
    approved: bool,
    reviewer_id: str | None,
    notes: str | None,
    now: datetime,
) -> SemanticUpdate:
    return update.model_copy(
        update={
            "reviewed": True,
            "approved": approved,
            "reviewer_id": reviewer_id,
            "review_notes": notes,
            "reviewed_at": now,
            "updated_at": now,
        }
    )


def _scope_target(document: SemanticDocument) -> dict[str, Any]:
    return {
        "database_id": document.scope.database_id,
        "schema_name": document.scope.schema_name,
        "dataset_ids": document.scope.dataset_ids,
    }


def _summarize_text(text: str) -> str:
    lines = _meaningful_lines(text)
    summary = " ".join(lines[:3])
    return summary[:500].strip()


def _meaningful_lines(text: str) -> list[str]:
    return [
        line.strip(" -\t") for line in text.splitlines() if len(line.strip(" -\t")) >= 8
    ]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
