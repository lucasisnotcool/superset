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

"""Tests for the explain-and-audit timeline builder."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from superset_ai_agent.explain import (
    attempt_index_at,
    build_agent_timeline,
    step_from_event,
)
from superset_ai_agent.schemas import (
    AgentStep,
    AuditInfo,
    KNOWN_AGENT_STEP_KINDS,
    TraceEvent,
    WrenContextArtifact,
    WrenRetrievalArtifact,
)

_BASE = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)


def _event(
    step: str, summary: str = "", *, offset: float = 0.0, **details
) -> TraceEvent:
    status = details.pop("status", "ok")
    return TraceEvent(
        step=step,
        status=status,
        summary=summary,
        details=details,
        created_at=_BASE + timedelta(seconds=offset),
    )


def test_build_timeline_maps_each_step_to_a_typed_detail() -> None:
    trace = [
        _event("load_context", "Loaded 2 dataset(s) from database analytics.",
               dataset_count=2, database_name="analytics", retrieval=None),
        _event("load_wren_context", "Loaded Wren semantic context.",
               available=True, matched_models=["orders"], retrieval_mode="embedding",
               retrieved_item_count=3, context_items=[{}, {}], project_id="p1"),
        _event("draft_response", "Generated a SQL draft.",
               response_type="sql", model="gpt"),
        _event("plan_semantic_sql", "Rewrote semantic SQL to native SQL.",
               engine="wren", rewritten=True, semantic_sql="SELECT 1",
               native_sql="SELECT 2", referenced_tables=["orders"], warnings=[]),
        _event("validate_sql", "SQL passed read-only validation.",
               dialect="postgresql", errors=[]),
        _event("execute_sql", "Executed SQL and returned 1,234 row(s).",
               row_count=1234),
        _event("build_artifacts", "Built artifacts.",
               insight_card_count=2, chart_type="bar", has_data_preview=True),
    ]
    timeline = build_agent_timeline(trace)

    by_kind = {step.kind: step for step in timeline}
    assert by_kind["load_context"].detail.dataset_count == 2
    assert by_kind["load_context"].detail.database_name == "analytics"
    assert by_kind["load_wren_context"].detail.matched_models == ["orders"]
    assert by_kind["load_wren_context"].detail.context_item_count == 2
    assert by_kind["draft_response"].detail.response_type == "sql"
    assert by_kind["plan_semantic_sql"].detail.semantic_sql == "SELECT 1"
    assert by_kind["plan_semantic_sql"].detail.native_sql == "SELECT 2"
    assert by_kind["validate_sql"].detail.is_valid is True
    assert by_kind["execute_sql"].detail.row_count == 1234
    assert by_kind["build_artifacts"].detail.has_data_preview is True


def test_validate_sql_detail_reflects_error_status() -> None:
    trace = [
        _event("validate_sql", "SQL failed read-only validation.",
               status="error", dialect="sqlite", errors=["no such table"]),
    ]
    detail = build_agent_timeline(trace)[0].detail
    assert detail.is_valid is False
    assert detail.errors == ["no such table"]


def test_unknown_step_degrades_to_summary_with_no_detail() -> None:
    timeline = build_agent_timeline([_event("brand_new_node", "future work")])
    assert timeline[0].kind == "brand_new_node"
    assert timeline[0].detail is None
    assert timeline[0].summary == "future work"


def test_durations_are_derived_from_event_timestamps() -> None:
    trace = [
        _event("load_context", "Loaded 1 dataset(s) from database x.", offset=0),
        _event("draft_sql", "Generated an initial SQL draft.", offset=1.5),
        _event("validate_sql", "SQL passed read-only validation.", offset=2.0),
    ]
    timeline = build_agent_timeline(trace)
    assert timeline[0].duration_ms == 1500
    assert timeline[1].duration_ms == 500
    # The final step has no successor, so no duration.
    assert timeline[2].duration_ms is None


def test_attempt_index_groups_steps_by_draft_cycle() -> None:
    trace = [
        _event("load_context", "Loaded 1 dataset(s) from database x."),
        _event("draft_response", "Generated a SQL draft."),
        _event("validate_sql", "ok"),
        _event("execute_sql", "Executed SQL and returned 0 row(s)."),
        _event("reflect_sql_outcome", "retry"),
        _event("draft_response", "Generated a SQL draft."),
        _event("validate_sql", "ok"),
        _event("execute_sql", "Executed SQL and returned 5 row(s)."),
    ]
    attempts = [step.attempt_index for step in build_agent_timeline(trace)]
    # Pre-draft + first cycle = 0; everything from the second draft onward = 1.
    assert attempts == [0, 0, 0, 0, 0, 1, 1, 1]
    assert attempt_index_at(trace, 5) == 1


def test_dry_plan_diagnostics_are_extracted_and_deduped() -> None:
    trace = [
        _event("dry_plan_with_wren", "Collected Wren dry-plan metadata.",
               status="warning", available=False, error="dup", errors=["dup", "other"]),
    ]
    detail = build_agent_timeline(trace)[0].detail
    assert detail.available is False
    assert detail.diagnostics == ["dup", "other"]


def test_carriers_backfill_sparse_steps() -> None:
    # An older/sparse trace with no per-event detail: the carriers fill the gap.
    trace = [
        _event("load_wren_context", "Loaded Wren semantic context."),
        _event("plan_semantic_sql", "Rewrote semantic SQL to native SQL.",
               engine="wren", rewritten=True),
        _event("execute_sql", "Executed SQL and returned 3 row(s)."),
    ]
    wren_context = WrenContextArtifact(
        enabled=True,
        available=True,
        matched_models=["orders"],
        retrieval=WrenRetrievalArtifact(candidate_table_names=["orders"]),
        recalled_example_count=4,
    )
    audit = AuditInfo(
        semantic_sql="SELECT a", native_sql="SELECT b", executed_sql="SELECT b",
        engine="wren", query_id=99, adapter="rest",
    )
    timeline = build_agent_timeline(trace, wren_context=wren_context, audit=audit)
    by_kind = {step.kind: step for step in timeline}
    assert by_kind["load_wren_context"].detail.matched_models == ["orders"]
    assert by_kind["load_wren_context"].detail.recalled_example_count == 4
    assert by_kind["plan_semantic_sql"].detail.semantic_sql == "SELECT a"
    assert by_kind["plan_semantic_sql"].detail.native_sql == "SELECT b"
    assert by_kind["execute_sql"].detail.executed_sql == "SELECT b"
    assert by_kind["execute_sql"].detail.query_id == 99


def test_row_count_parsed_from_summary_when_detail_absent() -> None:
    trace = [_event("execute_sql", "Executed SQL and returned 12,001 row(s).")]
    assert build_agent_timeline(trace)[0].detail.row_count == 12001


def test_artifact_id_matched_by_sql() -> None:
    class _Artifact:
        id = "art-1"
        sql = "SELECT 1 FROM t"
        trace: list = []
        wren_context = None
        audit = None

    trace = [_event("execute_sql", "Executed SQL and returned 1 row(s).",
                    sql="SELECT 1 FROM t;")]
    step = build_agent_timeline(trace, artifacts=[_Artifact()])[0]
    assert step.artifact_id == "art-1"


def test_step_from_event_is_json_round_trippable() -> None:
    event = _event("plan_semantic_sql", "Rewrote semantic SQL to native SQL.",
                   engine="wren", rewritten=True, semantic_sql="SELECT 1",
                   native_sql="SELECT 1", referenced_tables=["t"], warnings=[])
    step = step_from_event(event, attempt_index=2)
    payload = step.model_dump(mode="json")
    restored = AgentStep.model_validate(payload)
    assert restored.detail.kind == "plan_semantic_sql"
    assert restored.attempt_index == 2


def test_known_step_kinds_cover_every_step_emitted_by_the_graphs() -> None:
    """Drift guard: a new node must register its name in KNOWN_AGENT_STEP_KINDS.

    Scans the graph source for ``step="..."`` literals so adding a node without
    updating the timeline contract fails CI (ai_agent_explain_and_audit.md R4).
    """

    root = Path(__file__).resolve().parents[3] / "superset_ai_agent"
    emitted: set[str] = set()
    for name in ("graph.py", "conversation_graph.py", "app.py"):
        text = (root / name).read_text(encoding="utf-8")
        emitted.update(re.findall(r'step="([a-z_]+)"', text))
    missing = emitted - KNOWN_AGENT_STEP_KINDS
    assert not missing, f"Unregistered step kinds: {sorted(missing)}"
