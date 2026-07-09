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

"""The Benchmark Authoring Agent (plan P2.1-P2.5, R1/R2/R3/R10).

Turns a parsed :class:`~.corpus_csv.CorpusDraft` into a reviewable
:class:`AuthoringDraft`. **Deterministic-first**: rows where the human already
supplied ground truth are validated but never re-emitted through the model
(the fork's paraphrase-drift rule), and the synthesized context doc quotes the
human context rows verbatim. The model is consulted only for what humans left
open: authoring ``answer_spec`` for bare questions (``extract``), inventing new
questions from context + schema (``generate``), and capability tagging of what
it authored.

Mirrors the MDL Copilot loop (``copilot/loop.py``): a bounded tool-calling loop
whose tool results feed validation errors back for self-correction, emitting
``AgentStep`` progress through an injected sink. Every gold-SQL candidate is
executed through an injected :data:`SqlExecutor` before it can be marked
``verified`` (DP-B4/R2) — SQL that errors or returns nothing is kept but
flagged ``needs_review``, never silently accepted.

Pure module: the model client, SQL executor, schema summary, and step sink are
all injected, so the whole agent unit-tests offline with fakes.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent, independent of Superset
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from superset_ai_agent.evals.authoring.capability_vocab import (
    unknown_tags,
    vocab_prompt_block,
)
from superset_ai_agent.evals.authoring.corpus_csv import (
    ANSWER_TYPES,
    CorpusDraft,
    DraftItem,
)
from superset_ai_agent.llm.base import ChatMessage, ModelClient, ToolSpec
from superset_ai_agent.schemas import AgentStep

logger = logging.getLogger(__name__)

AuthoringMode = Literal["extract", "generate", "both"]

#: Per-candidate self-correction budget for failing gold SQL.
_SQL_RETRIES = 2

#: Rows echoed back to the model from a successful probe (cost control).
_PROBE_PREVIEW_ROWS = 5


@dataclass(frozen=True)
class SqlProbe:
    """Outcome of executing one gold-SQL candidate against the real DB."""

    ok: bool
    row_count: int = 0
    columns: tuple[str, ...] = ()
    rows_preview: tuple[dict[str, Any], ...] = ()
    error: str | None = None


#: Executes a candidate gold SQL against the project's database (injected by
#: the route; a fake in tests). Must never raise — errors come back in .error.
SqlExecutor = Callable[[str], SqlProbe]

StepSink = Callable[[AgentStep], None]


class AuthoredItem(BaseModel):
    """One draft benchmark item with authoring provenance (pre-review)."""

    question: str
    answer_type: str
    answer_spec: dict[str, Any]
    capability_tags: list[str] = Field(default_factory=list)
    target_schema: str | None = None
    notes: str | None = None
    source_row: int | None = None
    #: ``human`` = spec came from the CSV; ``extracted`` = model authored the
    #: spec for a human question; ``generated`` = model invented the question.
    origin: Literal["human", "extracted", "generated"] = "human"
    #: ``verified`` = gold SQL executed and returned rows (or non-SQL spec was
    #: well-formed); ``needs_review`` = a human must look before trusting it.
    validation: Literal["verified", "needs_review"] = "needs_review"
    problems: list[str] = Field(default_factory=list)


class AuthoringDraft(BaseModel):
    """The reviewable output of one authoring pass. Never auto-committed."""

    items: list[AuthoredItem] = Field(default_factory=list)
    #: Verbatim-quoted context doc (P2.4); None when the CSV had no context rows.
    context_doc: str | None = None
    warnings: list[str] = Field(default_factory=list)
    steps_taken: int = 0
    model_failed: bool = False


# --------------------------------------------------------------------------- #
# Deterministic parts (no model)
# --------------------------------------------------------------------------- #


def schema_summary_from_tables(
    tables_by_schema: dict[str, dict[str, set[str]]],
    *,
    max_tables: int = 80,
    max_columns: int = 40,
) -> str:
    """Render a compact ``schema.table(col, …)`` grounding block for the prompt.

    Accepts the ``SchemaIndex.tables_by_schema`` shape without importing it
    (keeps this module pure). Columns may be empty under lazy reflection —
    table names alone still ground generation. Truncation is announced, never
    silent (no-silent-caps rule).
    """

    lines: list[str] = []
    total = 0
    for schema in sorted(tables_by_schema):
        for table in sorted(tables_by_schema[schema]):
            if total >= max_tables:
                remaining = sum(len(t) for t in tables_by_schema.values()) - total
                lines.append(f"… {remaining} more table(s) omitted")
                return "\n".join(lines)
            cols = sorted(tables_by_schema[schema][table])
            shown = ", ".join(cols[:max_columns])
            if len(cols) > max_columns:
                shown += f", … {len(cols) - max_columns} more"
            lines.append(f"{schema}.{table}({shown})" if shown else f"{schema}.{table}")
            total += 1
    return "\n".join(lines)


def assemble_context_doc(
    draft: CorpusDraft, *, title: str = "Business context"
) -> str | None:
    """Quote the human context rows verbatim into one Markdown doc (P2.4).

    Deliberately NOT model-synthesized: regenerating reference prose from an
    LLM has corrupted ground truth before (paraphrase drift). Structure is
    added around the quotes, never inside them.
    """

    if not draft.contexts:
        return None
    lines = [f"# {title}", ""]
    for ctx in draft.contexts:
        lines.append(ctx.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _validate_spec(answer_type: str, spec: dict[str, Any]) -> list[str]:
    """Shape problems for one proposed spec (deterministic)."""

    if answer_type not in ANSWER_TYPES:
        return [f"answer_type {answer_type!r} not in {ANSWER_TYPES}"]
    if answer_type == "gold_sql":
        has_sql = bool(str(spec.get("sql") or "").strip())
        return [] if has_sql else ["gold_sql spec has no sql"]
    if answer_type == "eval_note":
        has_note = bool(str(spec.get("note") or "").strip())
        return [] if has_note else ["eval_note spec has no note"]
    if not any(k in spec for k in ("nums", "names", "trap", "zero")):
        return ["expected_values spec asserts nothing (need nums/names/trap/zero)"]
    return []


def _probe_gold_sql(
    item: AuthoredItem, execute_sql: SqlExecutor, emit: StepSink
) -> SqlProbe:
    probe = execute_sql(str(item.answer_spec.get("sql") or ""))
    if probe.ok and probe.row_count > 0:
        item.validation = "verified"
        emit(
            AgentStep(
                kind="authoring_sql_probe",
                summary=f"gold SQL verified ({probe.row_count} rows)",
            )
        )
    else:
        item.validation = "needs_review"
        problem = (
            f"gold SQL failed: {probe.error}"
            if not probe.ok
            else "gold SQL returned no rows (fine only for a negative question)"
        )
        item.problems.append(problem)
        emit(
            AgentStep(
                kind="authoring_sql_probe",
                summary=problem,
                status="warning",
            )
        )
    return probe


def validate_human_items(
    corpus: CorpusDraft, execute_sql: SqlExecutor, emit: StepSink
) -> list[AuthoredItem]:
    """Pass-through + validate rows the human fully specified (no model)."""

    out: list[AuthoredItem] = []
    for row in corpus.items:
        if row.needs_authoring or row.answer_spec is None:
            continue
        item = AuthoredItem(
            question=row.question,
            answer_type=row.answer_type or "",
            answer_spec=dict(row.answer_spec),
            capability_tags=list(row.capability_tags),
            target_schema=row.target_schema,
            notes=row.notes,
            source_row=row.source_row,
            origin="human",
        )
        problems = _validate_spec(item.answer_type, item.answer_spec)
        if problems:
            item.problems.extend(problems)
        elif item.answer_type == "gold_sql":
            _probe_gold_sql(item, execute_sql, emit)
        else:
            item.validation = "verified"
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# The model loop (extract / generate)
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT = """You author benchmark test items for a SQL analytics agent.

You are given business context, a database schema summary, and open questions.
For each open question (and, in generate mode, for new questions you invent),
produce ONE item via the propose_items tool:

- answer_type "gold_sql": a single SELECT statement that answers the question
  on the given schema. It will be EXECUTED to verify it; prefer simple, exact
  SQL over clever SQL. Use only tables/columns from the schema summary.
- answer_type "expected_values": only when the correct answer is a small fixed
  set of numbers/names you can state with certainty from the given material.
- answer_type "eval_note": when the answer cannot be pinned to SQL or values —
  write a strict grading rubric: what a correct answer MUST contain, in one to
  three sentences. Judges grade strictly against your note.

Tag every item with capability_tags from this vocabulary (0-3 tags):
{vocab}

Rules:
- NEVER answer from world knowledge; only the provided context and schema.
- Do not restate or alter the human's question text; author only what is asked.
- If a question cannot be answered from the schema, propose it as eval_note
  with a rubric saying what evidence a correct answer needs.
- When a tool result reports a failed SQL probe, fix the SQL and re-propose
  that item (same question) — or downgrade it to eval_note if it cannot work.
- Call finish when every open question has a proposed item.
"""

_PROPOSE_ITEMS = ToolSpec(
    name="propose_items",
    description="Propose one or more authored benchmark items.",
    parameters={
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer_type": {"type": "string", "enum": list(ANSWER_TYPES)},
                        "answer_spec": {"type": "object"},
                        "capability_tags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "target_schema": {"type": "string"},
                    },
                    "required": ["question", "answer_type", "answer_spec"],
                },
            }
        },
        "required": ["items"],
    },
)

_FINISH = ToolSpec(
    name="finish",
    description="Declare the authoring pass complete.",
    parameters={
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
)


def _user_prompt(
    open_questions: list[DraftItem],
    context_doc: str | None,
    schema_summary: str,
    mode: AuthoringMode,
    generate_count: int,
) -> str:
    parts: list[str] = []
    if context_doc:
        parts.append(f"## Business context (verbatim)\n{context_doc}")
    parts.append(f"## Schema summary\n{schema_summary or '(none provided)'}")
    if open_questions and mode in ("extract", "both"):

        def _line(q: DraftItem) -> str:
            tags = f" (suggested tags: {', '.join(q.capability_tags)})"
            return f"- [row {q.source_row}] {q.question}" + (
                tags if q.capability_tags else ""
            )

        listed = "\n".join(_line(q) for q in open_questions)
        parts.append(f"## Open questions needing ground truth\n{listed}")
    if mode in ("generate", "both"):
        parts.append(
            f"## Generation request\nAdditionally invent up to {generate_count} NEW "
            "questions a business user would ask of this schema, each with its "
            "authored item. Cover different capability tags."
        )
    return "\n\n".join(parts)


def _accept_proposed(
    proposals: list[dict[str, Any]],
    open_by_question: dict[str, DraftItem],
    execute_sql: SqlExecutor,
    emit: StepSink,
    draft: AuthoringDraft,
    seen_questions: set[str],
    retries: dict[str, int],
) -> list[str]:
    """Validate proposed items into the draft; return per-item feedback lines."""

    feedback: list[str] = []
    for prop in proposals:
        question = str(prop.get("question") or "").strip()
        if not question:
            feedback.append("rejected: an item had no question")
            continue
        key = question.casefold()
        answer_type = str(prop.get("answer_type") or "")
        raw_spec = prop.get("answer_spec")
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        problems = _validate_spec(answer_type, spec or {})
        if problems:
            feedback.append(f"rejected {question[:60]!r}: {'; '.join(problems)}")
            continue

        source = open_by_question.get(key)
        tags = [str(t).lower() for t in prop.get("capability_tags") or []]
        for tag in unknown_tags(tags):
            draft.warnings.append(
                f"unknown capability tag {tag!r} on {question[:60]!r}"
            )
        item = AuthoredItem(
            question=source.question if source else question,
            answer_type=answer_type,
            answer_spec=dict(spec or {}),
            capability_tags=tags or (list(source.capability_tags) if source else []),
            target_schema=(
                str(prop.get("target_schema") or "")
                or None
                or (source.target_schema if source else None)
            ),
            notes=source.notes if source else None,
            source_row=source.source_row if source else None,
            origin="extracted" if source else "generated",
        )

        if answer_type == "gold_sql":
            probe = _probe_gold_sql(item, execute_sql, emit)
            retry_left = retries.get(key, 0) < _SQL_RETRIES
            if item.validation == "needs_review" and retry_left:
                retries[key] = retries.get(key, 0) + 1
                feedback.append(
                    f"SQL probe for {question[:60]!r} failed "
                    f"({item.problems[-1]}); fix and re-propose this item"
                )
                continue  # do not accept yet — give the model its retry
            if probe.ok and probe.row_count > 0:
                preview = json.dumps(
                    list(probe.rows_preview)[:_PROBE_PREVIEW_ROWS], default=str
                )[:400]
                feedback.append(
                    f"accepted {question[:60]!r} (verified, "
                    f"{probe.row_count} rows, cols {list(probe.columns)[:6]}, "
                    f"preview {preview})"
                )
            else:
                feedback.append(
                    f"accepted {question[:60]!r} flagged needs_review "
                    f"({item.problems[-1]})"
                )
        else:
            item.validation = "verified"
            feedback.append(f"accepted {question[:60]!r} ({answer_type})")

        if key in seen_questions:
            # Re-proposal replaces the earlier attempt for the same question.
            draft.items = [i for i in draft.items if i.question.casefold() != key]
        seen_questions.add(key)
        draft.items.append(item)
    return feedback


def _handle_tool_calls(
    calls: list[Any],
    messages: list[ChatMessage],
    *,
    open_by_question: dict[str, DraftItem],
    execute_sql: SqlExecutor,
    emit: StepSink,
    draft: AuthoringDraft,
    seen: set[str],
    retries: dict[str, int],
) -> bool:
    """Process one turn's tool calls; True when the model called finish."""

    finished = False
    for call in calls:
        if call.name == "finish":
            emit(
                AgentStep(
                    kind="authoring_done",
                    summary=str(call.arguments.get("summary") or "done"),
                )
            )
            messages.append(
                ChatMessage(
                    role="tool", content="ok", tool_call_id=call.id, name=call.name
                )
            )
            finished = True
            continue
        if call.name != "propose_items":
            messages.append(
                ChatMessage(
                    role="tool",
                    content=f"unknown tool {call.name}",
                    tool_call_id=call.id,
                    name=call.name,
                )
            )
            continue
        proposals = call.arguments.get("items") or []
        emit(
            AgentStep(
                kind="authoring_propose",
                summary=f"model proposed {len(proposals)} item(s)",
            )
        )
        feedback = _accept_proposed(
            proposals, open_by_question, execute_sql, emit, draft, seen, retries
        )
        messages.append(
            ChatMessage(
                role="tool",
                content="\n".join(feedback) or "no items received",
                tool_call_id=call.id,
                name=call.name,
            )
        )
    return finished


def run_authoring(
    *,
    corpus: CorpusDraft,
    model_client: ModelClient,
    execute_sql: SqlExecutor,
    schema_summary: str = "",
    mode: AuthoringMode = "extract",
    generate_count: int = 5,
    max_steps: int = 8,
    model: str | None = None,
    on_step: StepSink | None = None,
) -> AuthoringDraft:
    """One authoring pass: corpus draft -> reviewable AuthoringDraft (R1)."""

    emit: StepSink = on_step or (lambda step: None)
    draft = AuthoringDraft(warnings=list(corpus.warnings))
    draft.context_doc = assemble_context_doc(corpus)
    if draft.context_doc:
        emit(
            AgentStep(
                kind="authoring_context",
                summary=(
                    f"context doc assembled from {len(corpus.contexts)} "
                    "row(s), verbatim"
                ),
            )
        )

    emit(
        AgentStep(kind="authoring_validate", summary="validating human-provided items")
    )
    human = validate_human_items(corpus, execute_sql, emit)
    draft.items.extend(human)
    seen = {i.question.casefold() for i in human}

    open_questions = [
        row
        for row in corpus.items
        if row.needs_authoring and row.question.casefold() not in seen
    ]
    needs_model = (mode in ("extract", "both") and open_questions) or mode in (
        "generate",
        "both",
    )
    if not needs_model:
        emit(AgentStep(kind="authoring_done", summary="nothing needed the model"))
        return draft

    open_by_question = {q.question.casefold(): q for q in open_questions}
    retries: dict[str, int] = {}
    messages = [
        ChatMessage(
            role="system",
            content=_SYSTEM_PROMPT.format(vocab=vocab_prompt_block()),
        ),
        ChatMessage(
            role="user",
            content=_user_prompt(
                open_questions, draft.context_doc, schema_summary, mode, generate_count
            ),
        ),
    ]
    specs = [_PROPOSE_ITEMS, _FINISH]

    while draft.steps_taken < max_steps:
        draft.steps_taken += 1
        try:
            result = model_client.chat(messages, tools=specs, model=model)
        except Exception as ex:  # pylint: disable=broad-except - never abort review
            logger.warning("Authoring model call failed: %s", ex)
            emit(
                AgentStep(
                    kind="authoring_error",
                    summary=f"model call failed: {ex}",
                    status="error",
                )
            )
            draft.model_failed = True
            break

        if not result.tool_calls:
            # A bare text reply: nudge once toward the tools, then stop.
            messages.append(ChatMessage(role="assistant", content=result.content or ""))
            messages.append(
                ChatMessage(
                    role="user",
                    content="Use propose_items / finish; free text is not recorded.",
                )
            )
            continue

        messages.append(
            ChatMessage(
                role="assistant",
                content=result.content or "",
                tool_calls=result.tool_calls,
            )
        )
        if _handle_tool_calls(
            result.tool_calls,
            messages,
            open_by_question=open_by_question,
            execute_sql=execute_sql,
            emit=emit,
            draft=draft,
            seen=seen,
            retries=retries,
        ):
            break
    else:
        draft.warnings.append(f"authoring stopped at the {max_steps}-step budget")

    still_open = [
        q.question for q in open_questions if q.question.casefold() not in seen
    ]
    for question in still_open:
        draft.warnings.append(f"no item authored for question: {question[:80]!r}")
    return draft
