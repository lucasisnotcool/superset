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

"""Assemble the explain-and-audit timeline (ai_agent_explain_and_audit.md).

A single, ordered, typed view of what happened between a user's message and the
agent's final response — built *post hoc* from the graph's ``TraceEvent``s plus
the late-bound ``WrenContextArtifact``/``AuditInfo`` carriers. The graphs keep
emitting trace exactly as before; nothing here changes agent behavior.

Each node's ``TraceEvent.details`` is made self-describing at emission (so live
streaming carries the same information as the final response); the carriers are
only a fallback when a detail field is absent.
"""

from __future__ import annotations

import re
from typing import Any

from superset_ai_agent.schemas import (
    AgentStep,
    AuditInfo,
    BuildArtifactsDetail,
    DRAFT_STEP_KINDS,
    DraftDetail,
    DryPlanDetail,
    ExecuteSqlDetail,
    IntentDetail,
    LoadContextDetail,
    LoadWrenContextDetail,
    PlanSemanticSqlDetail,
    RecalledExample,
    ReflectDetail,
    RepairDetail,
    RetrievedChunk,
    TraceEvent,
    ValidateSqlDetail,
    WrenContextArtifact,
    WrenRetrievalArtifact,
)

_ROW_COUNT_RE = re.compile(r"returned\s+([\d,]+)\s+row")
_DB_NAME_RE = re.compile(r"from\s+database\s+(.+?)\.?$")

#: Max characters of a retrieved chunk's text surfaced to the explain UI. The
#: full text already ships on ``wren_context``; the timeline copy is for display,
#: so it is bounded to keep the response payload small (A1 risk note).
_CHUNK_TEXT_LIMIT = 280

#: Max characters of a recalled example's SQL surfaced to the explain UI (B1).
_EXAMPLE_SQL_LIMIT = 280


def compact_recalled_examples(recalled: list[Any]) -> list[dict[str, Any]]:
    """Trim recalled NL->SQL pairs to the display payload for trace details (B1).

    Called at trace-emission time so the draft step is self-describing (the same
    contract as every other node), keeping ``native_sql`` bounded.
    """

    compact: list[dict[str, Any]] = []
    for example in recalled or []:
        if not isinstance(example, dict):
            continue
        question = example.get("question")
        if not isinstance(question, str) or not question.strip():
            continue
        native_sql = example.get("native_sql")
        if isinstance(native_sql, str) and len(native_sql) > _EXAMPLE_SQL_LIMIT:
            native_sql = native_sql[:_EXAMPLE_SQL_LIMIT].rstrip() + "…"
        entry: dict[str, Any] = {"question": question, "native_sql": native_sql}
        # Surface provenance (F3/2C) so the UI can show *where each query came
        # from*: a curated golden query (verified or not, with its name) vs a
        # learned runtime example, and whether a learned one came from outside
        # the active project's onboarded tables (out_of_scope -> not in_scope).
        meta = example.get("result_meta")
        meta = meta if isinstance(meta, dict) else {}
        if meta.get("golden"):
            entry["source"] = "golden"
            entry["verified"] = bool(meta.get("verified"))
            name = meta.get("name")
            if isinstance(name, str) and name.strip():
                entry["name"] = name
        else:
            entry["source"] = "memory"
            entry["in_scope"] = not bool(meta.get("out_of_scope"))
        compact.append(entry)
    return compact


def build_agent_timeline(
    trace: list[TraceEvent],
    *,
    wren_context: WrenContextArtifact | None = None,
    audit: AuditInfo | None = None,
    artifacts: list[Any] | None = None,
) -> list[AgentStep]:
    """Map a turn's trace into the ordered, typed explain-and-audit timeline.

    ``wren_context``/``audit`` enrich the matching steps when the per-event
    details are sparse (e.g. an older trace, or a step that runs before the
    carrier is finalized). ``artifacts`` are used only for best-effort
    ``artifact_id`` attribution by SQL match.
    """

    steps: list[AgentStep] = []
    draft_seen = 0
    sql_to_artifact = _artifact_sql_index(artifacts)
    for index, event in enumerate(trace):
        if event.step in DRAFT_STEP_KINDS:
            draft_seen += 1
        attempt_index = max(draft_seen - 1, 0)
        duration_ms = _duration_ms(trace, index)
        detail = _detail_from_event(event, wren_context=wren_context, audit=audit)
        steps.append(
            AgentStep(
                kind=event.step,
                status=event.status,
                summary=event.summary,
                started_at=event.created_at,
                duration_ms=duration_ms,
                attempt_index=attempt_index,
                artifact_id=_artifact_id_for(event, detail, sql_to_artifact),
                detail=detail,
            )
        )
    return steps


def step_from_event(
    event: TraceEvent,
    *,
    attempt_index: int = 0,
) -> AgentStep:
    """Map a single trace event to a step for live streaming (no carriers yet)."""

    return AgentStep(
        kind=event.step,
        status=event.status,
        summary=event.summary,
        started_at=event.created_at,
        attempt_index=attempt_index,
        detail=_detail_from_event(event),
    )


def attempt_index_at(trace: list[TraceEvent], index: int) -> int:
    """Attempt (drafting cycle) the event at ``index`` belongs to, 0-based."""

    draft_seen = sum(
        1 for event in trace[: index + 1] if event.step in DRAFT_STEP_KINDS
    )
    return max(draft_seen - 1, 0)


def _duration_ms(trace: list[TraceEvent], index: int) -> int | None:
    if index + 1 >= len(trace):
        return None
    delta = trace[index + 1].created_at - trace[index].created_at
    millis = int(delta.total_seconds() * 1000)
    return millis if millis >= 0 else None


def _detail_from_event(
    event: TraceEvent,
    *,
    wren_context: WrenContextArtifact | None = None,
    audit: AuditInfo | None = None,
) -> Any:
    handler = _DETAIL_HANDLERS.get(event.step)
    if handler is None:
        return None
    return handler(event, wren_context, audit)


def _detail_intent(event: TraceEvent, _wc: Any, _audit: Any) -> Any:
    details = event.details or {}
    return IntentDetail(intent=details.get("intent"), reason=details.get("reason"))


def _detail_draft(event: TraceEvent, wren_context: Any, _audit: Any) -> Any:
    details = event.details or {}
    raw_examples = details.get("recalled_examples")
    examples = [
        RecalledExample.model_validate(item)
        for item in compact_recalled_examples(
            raw_examples if isinstance(raw_examples, list) else []
        )
    ]
    return DraftDetail(
        response_type=details.get("response_type"),
        model=details.get("model"),
        recalled_example_count=_recalled_count(details, wren_context),
        recalled_examples=examples,
    )


def _detail_dry_plan(event: TraceEvent, _wc: Any, _audit: Any) -> Any:
    details = event.details or {}
    return DryPlanDetail(
        available=bool(details.get("available", True)),
        diagnostics=_dry_plan_diagnostics(details),
    )


def _detail_validate(event: TraceEvent, _wc: Any, _audit: Any) -> Any:
    details = event.details or {}
    return ValidateSqlDetail(
        is_valid=event.status == "ok",
        dialect=details.get("dialect"),
        errors=_str_list(details.get("errors")),
    )


def _detail_repair(event: TraceEvent, _wc: Any, _audit: Any) -> Any:
    details = event.details or {}
    return RepairDetail(
        errors=_str_list(details.get("errors") or details.get("warnings")),
        dry_plan_diagnostics=_str_list(details.get("dry_plan_diagnostics")),
        attempt=details.get("attempt"),
    )


def _detail_build(event: TraceEvent, _wc: Any, _audit: Any) -> Any:
    details = event.details or {}
    return BuildArtifactsDetail(
        insight_card_count=int(details.get("insight_card_count", 0) or 0),
        chart_type=details.get("chart_type"),
        has_data_preview=bool(details.get("has_data_preview", False)),
    )


def _detail_reflect(event: TraceEvent, _wc: Any, _audit: Any) -> Any:
    details = event.details or {}
    return ReflectDetail(
        outcome=details.get("outcome"),
        remaining_sql_iterations=details.get("remaining_sql_iterations"),
        retry_feedback=details.get("retry_feedback"),
    )


#: step name -> ``(event, wren_context, audit) -> detail`` handler. A step with
#: no entry degrades to a bare summary (forward-compatible, R4).
_DETAIL_HANDLERS = {
    "load_context": lambda e, wc, _a: _load_context_detail(e, e.details or {}, wc),
    "classify_intent": _detail_intent,
    "load_wren_context": lambda e, wc, _a: _wren_context_detail(e.details or {}, wc),
    "draft_sql": _detail_draft,
    "draft_response": _detail_draft,
    "approved_sql": _detail_draft,
    "answer_directly": _detail_draft,
    "dry_plan_with_wren": _detail_dry_plan,
    "plan_semantic_sql": lambda e, _wc, audit: _plan_semantic_detail(
        e.details or {}, audit
    ),
    "validate_sql": _detail_validate,
    "repair_sql": _detail_repair,
    "correct_semantic_sql": _detail_repair,
    "execute_sql": lambda e, _wc, audit: _execute_detail(e, e.details or {}, audit),
    "duplicate_sql": lambda e, _wc, audit: _execute_detail(e, e.details or {}, audit),
    "build_artifacts": _detail_build,
    "reflect_sql_outcome": _detail_reflect,
}


def _load_context_detail(
    event: TraceEvent,
    details: dict[str, Any],
    wren_context: WrenContextArtifact | None,
) -> LoadContextDetail:
    retrieval = _retrieval_from(details, wren_context)
    dataset_count = details.get("dataset_count")
    database_name = details.get("database_name")
    if dataset_count is None or database_name is None:
        parsed_count, parsed_name = _parse_load_context_summary(event.summary)
        dataset_count = dataset_count if dataset_count is not None else parsed_count
        database_name = database_name or parsed_name
    return LoadContextDetail(
        dataset_count=int(dataset_count or 0),
        database_name=database_name,
        retrieval=retrieval,
    )


def _wren_context_detail(
    details: dict[str, Any],
    wren_context: WrenContextArtifact | None,
) -> LoadWrenContextDetail:
    source: dict[str, Any] = {}
    if wren_context is not None:
        source = wren_context.model_dump()
    # The load_wren_context node dumps the full WrenContextArtifact into details,
    # so prefer the event's own snapshot (live) and fall back to the carrier.
    merged = {**source, **details} if details else source
    context_items = merged.get("context_items") or []
    if not isinstance(context_items, list):
        context_items = []
    return LoadWrenContextDetail(
        available=bool(merged.get("available", False)),
        project_id=merged.get("project_id"),
        mdl_path=merged.get("mdl_path"),
        matched_models=_str_list(merged.get("matched_models")),
        matched_views=_str_list(merged.get("matched_views")),
        retrieval_mode=merged.get("retrieval_mode"),
        retrieved_item_count=int(merged.get("retrieved_item_count", 0) or 0),
        context_item_count=len(context_items),
        recalled_example_count=int(merged.get("recalled_example_count", 0) or 0),
        retrieved_chunks=_retrieved_chunks(context_items),
        warnings=_str_list(merged.get("warnings")),
    )


def _retrieved_chunks(context_items: list[Any]) -> list[RetrievedChunk]:
    """Map retriever ``context_items`` into bounded display chunks (A1).

    Only items the Retriever seam produced (``source == "retriever"``) carry
    rankable schema text; legacy ``fetch_context`` items (e.g. relationship
    bundles) have no per-chunk text and are left to the count badge.
    """

    chunks: list[RetrievedChunk] = []
    for item in context_items:
        if not isinstance(item, dict) or item.get("source") != "retriever":
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if len(text) > _CHUNK_TEXT_LIMIT:
            text = text[:_CHUNK_TEXT_LIMIT].rstrip() + "…"
        score = item.get("score")
        chunks.append(
            RetrievedChunk(
                kind=item.get("kind"),
                name=item.get("name"),
                model=item.get("model"),
                text=text,
                retriever=item.get("retriever"),
                score=float(score) if isinstance(score, (int, float)) else None,
            )
        )
    return chunks


def _plan_semantic_detail(
    details: dict[str, Any],
    audit: AuditInfo | None,
) -> PlanSemanticSqlDetail:
    semantic_sql = details.get("semantic_sql")
    native_sql = details.get("native_sql")
    if audit is not None:
        semantic_sql = semantic_sql or audit.semantic_sql
        native_sql = native_sql or audit.native_sql
    return PlanSemanticSqlDetail(
        engine=details.get("engine"),
        rewritten=bool(details.get("rewritten", False)),
        semantic_sql=semantic_sql,
        native_sql=native_sql,
        referenced_tables=_str_list(details.get("referenced_tables")),
        warnings=_str_list(details.get("warnings")),
    )


def _execute_detail(
    event: TraceEvent,
    details: dict[str, Any],
    audit: AuditInfo | None,
) -> ExecuteSqlDetail:
    row_count = details.get("row_count")
    if row_count is None and event.status == "ok":
        row_count = _parse_row_count(event.summary)
    return ExecuteSqlDetail(
        row_count=row_count,
        sql=details.get("sql"),
        executed_sql=audit.executed_sql if audit is not None else None,
        query_id=audit.query_id if audit is not None else None,
        adapter=audit.adapter if audit is not None else None,
        error=details.get("error"),
        is_duplicate=event.step == "duplicate_sql",
    )


def _retrieval_from(
    details: dict[str, Any],
    wren_context: WrenContextArtifact | None,
) -> WrenRetrievalArtifact | None:
    raw = details.get("retrieval")
    # Tolerate an older trace that dumped the retrieval artifact at the top level.
    if not isinstance(raw, dict) and details and "candidate_table_names" in details:
        raw = details
    if isinstance(raw, dict):
        try:
            return WrenRetrievalArtifact.model_validate(raw)
        except Exception:  # noqa: S110  # pylint: disable=broad-except
            # Best-effort enrichment only; fall through to the carrier below.
            pass
    if wren_context is not None:
        return wren_context.retrieval
    return None


def _artifact_sql_index(artifacts: list[Any] | None) -> dict[str, str]:
    index: dict[str, str] = {}
    for artifact in artifacts or []:
        sql = getattr(artifact, "sql", None)
        artifact_id = getattr(artifact, "id", None)
        if sql and artifact_id:
            index.setdefault(_sql_key(sql), artifact_id)
    return index


def _artifact_id_for(
    event: TraceEvent,
    detail: Any,
    sql_to_artifact: dict[str, str],
) -> str | None:
    if not sql_to_artifact:
        return None
    sql = getattr(detail, "sql", None) or event.details.get("sql")
    if not sql:
        return None
    return sql_to_artifact.get(_sql_key(str(sql)))


def _recalled_count(
    details: dict[str, Any],
    wren_context: WrenContextArtifact | None,
) -> int:
    if "recalled_example_count" in details:
        return int(details.get("recalled_example_count", 0) or 0)
    if wren_context is not None:
        return wren_context.recalled_example_count
    return 0


def _dry_plan_diagnostics(dry_plan: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    error = dry_plan.get("error")
    if isinstance(error, str) and error.strip():
        raw.append(error.strip())
    errors = dry_plan.get("errors")
    if isinstance(errors, list):
        for item in errors:
            text = (item if isinstance(item, str) else str(item)).strip()
            if text:
                raw.append(text)
    seen: set[str] = set()
    deduped: list[str] = []
    for text in raw:
        if text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _parse_load_context_summary(summary: str) -> tuple[int | None, str | None]:
    count_match = re.search(r"Loaded\s+(\d+)\s+dataset", summary)
    count = int(count_match.group(1)) if count_match else None
    name_match = _DB_NAME_RE.search(summary)
    name = name_match.group(1).strip() if name_match else None
    return count, name


def _parse_row_count(summary: str) -> int | None:
    match = _ROW_COUNT_RE.search(summary)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _sql_key(sql: str) -> str:
    return " ".join(sql.strip().rstrip(";").split())
