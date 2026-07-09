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
"""Prepare step 2b/2c: generate/extract a question corpus with validated ground truth.

Reads dumped ``inputs/`` + the introspected schema, asks the model (prompt §9.2) for
typed test items (``gold_sql`` or ``eval_note`` + capability tags), then **validates
each gold_sql by executing it** — SQL that errors or returns nothing is dropped or
flagged, never trusted (R13/DP-11). Writes ``fixture/questions.csv``.

The generation/validation/CSV logic is pure (injected chat + execute callables) so it
is unit-tested without a model or a DB. Only ``main`` touches the live stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rig import corpus as corpus_mod, scoring

from prepare import _agent_pass as ap

CORPUS_SYSTEM = (
    "You are building an evaluation set for a text-to-SQL agent. Given the business "
    "context, the introspected schema (tables/columns), and any questions already in "
    "the inputs, produce a JSON array of test items. For each item output an object "
    "with: 'question' (natural language); 'answer_type' = 'gold_sql' when a "
    "deterministic answer exists (then include runnable 'gold_sql' using ONLY the "
    "introspected schema/columns) or 'eval_note' when correctness is judgemental "
    "(then include a one-to-three sentence 'eval_note' rubric of what a correct answer "
    "must satisfy); and 1-3 'capability_tags' from {slang, join, xschema, bridge, "
    "metric, trap, negative, temporal, multihop, distractor}. Prefer gold_sql. Never "
    "reference a column that is not in the schema. Output ONLY the JSON array."
)


@dataclass
class CorpusReport:
    kept: list[corpus_mod.QuestionRecord] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


def items_from_reply(reply: str) -> list[dict[str, Any]]:
    """Parse the model reply into a list of item dicts."""

    data = ap.extract_json(reply)
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ValueError("corpus reply is not a JSON array of items")
    return [d for d in data if isinstance(d, dict)]


def to_records(
    items: list[dict[str, Any]],
) -> tuple[list[corpus_mod.QuestionRecord], list[str]]:
    """Normalize items into records, assigning Q-ids where missing. De-dupes ids."""

    records: list[corpus_mod.QuestionRecord] = []
    errors: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(items, start=1):
        try:
            rec = corpus_mod.record_from_dict(item, position=i)
        except (ValueError, TypeError) as ex:
            errors.append(f"item {i}: {ex}")
            continue
        if rec.id in seen:
            rec = corpus_mod.QuestionRecord(
                id=f"{rec.id}_{i}",
                question=rec.question,
                answer_type=rec.answer_type,
                answer_spec=rec.answer_spec,
                capability_tags=rec.capability_tags,
                notes=rec.notes,
            )
        seen.add(rec.id)
        records.append(rec)
    return records, errors


def validate(
    records: list[corpus_mod.QuestionRecord],
    execute_sql: Callable[[str], scoring.GoldResult] | None,
    *,
    drop_invalid: bool = True,
) -> CorpusReport:
    """Validate gold_sql items by executing them (R13). Non-SQL items pass through.

    ``execute_sql`` returns a :class:`~rig.scoring.GoldResult` or raises. When it is
    ``None`` (no live DB), gold_sql items are kept but *flagged* as unvalidated.
    """

    report = CorpusReport()
    for rec in records:
        if rec.answer_type != "gold_sql":
            report.kept.append(rec)
            continue
        sql = str(rec.answer_spec.get("sql") or "")
        if execute_sql is None:
            report.kept.append(rec)
            report.flagged.append(f"{rec.id}: gold_sql UNVALIDATED (no DB connection)")
            continue
        try:
            gold = execute_sql(sql)
        except Exception as ex:  # noqa: BLE001 - a broken gold query is not ground truth
            report.dropped.append(f"{rec.id}: gold_sql failed to execute: {ex}")
            if not drop_invalid:
                report.kept.append(rec)
                report.flagged.append(f"{rec.id}: kept despite execution error")
            continue
        if not gold.rows:
            report.dropped.append(f"{rec.id}: gold_sql returned no rows")
            if not drop_invalid:
                report.kept.append(rec)
                report.flagged.append(f"{rec.id}: kept despite empty result")
            continue
        report.kept.append(rec)
    return report


def generate_corpus(
    inputs_text: str,
    schema_text: str,
    *,
    chat: Callable[[str, str], str],
    execute_sql: Callable[[str], scoring.GoldResult] | None,
    drop_invalid: bool = True,
) -> CorpusReport:
    """End-to-end pure generation: chat -> parse -> records -> validate."""

    user = (
        f"BUSINESS CONTEXT & INPUTS:\n{inputs_text}\n\n"
        f"INTROSPECTED SCHEMA:\n{schema_text}\n\n"
        "Produce the JSON array of test items now."
    )
    reply = chat(CORPUS_SYSTEM, user)
    report = CorpusReport()
    try:
        items = items_from_reply(reply)
    except ValueError as ex:
        report.parse_errors.append(str(ex))
        return report
    records, errors = to_records(items)
    report.parse_errors.extend(errors)
    validated = validate(records, execute_sql, drop_invalid=drop_invalid)
    validated.parse_errors = report.parse_errors
    return validated


def to_csv(report: CorpusReport) -> str:
    return corpus_mod.records_to_csv(report.kept)
