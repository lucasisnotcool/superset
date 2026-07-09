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

"""Semi-structured CSV -> draft benchmark records (plan P1.2, DP-B3).

The DP-B3 contract removes the highest-variance authoring decision (document
segmentation) by declaring columns instead of asking an LLM to guess structure:

- ``type``       optional: ``context`` | ``question`` (inferred when absent)
- ``question``   the NL question (required on question rows)
- exactly one of ``gold_sql`` | ``expected_values`` | ``eval_note`` filled marks
  the human-provided ground truth; **all three empty** means the authoring agent
  must draft it (``needs_authoring=True``)
- ``answer_type`` optional explicit override (must agree with the filled cell)
- ``capability_tags`` optional ``;``/``,``-separated (unknown tags warn)
- ``target_schema`` / ``context`` / ``notes`` optional

Pure module: no FastAPI, no LLM, no I/O beyond the passed text — the UI's
"validate" dry-run is just this parse. Cell forms for ``expected_values`` stay
behaviorally aligned with the research rig's ``evaluation/rig/corpus.py`` (the
rig may not be imported from the app package — the dependency points the other
way), and the emitted spec is exactly what ``evals.typed_spec.score_expected_values``
accepts (``nums``/``names``/``absent``/``trap``/``zero``/``tolerance``).
"""

from __future__ import annotations

import csv
import io
import json  # noqa: TID251 - standalone agent, independent of Superset
from dataclasses import dataclass, field
from typing import Any

from superset_ai_agent.evals.authoring.capability_vocab import unknown_tags
from superset_ai_agent.evals.schemas import MAX_ITEMS_PER_BENCHMARK

ANSWER_TYPES = ("gold_sql", "expected_values", "eval_note")

#: Keys score_expected_values understands; anything else in a JSON cell errors.
_EXPECTED_SPEC_KEYS = {"nums", "names", "absent", "trap", "zero", "tolerance"}

_ROW_TYPES = ("question", "context")


@dataclass(frozen=True)
class DraftContext:
    """One context row — raw material for the synthesized BI doc (P2.4)."""

    text: str
    source_row: int


@dataclass
class DraftItem:
    """One drafted benchmark item, pre-review.

    ``answer_type is None`` (with ``needs_authoring=True``) means the human
    provided only the question; the authoring agent must draft the ground truth.
    """

    question: str
    answer_type: str | None
    answer_spec: dict[str, Any] | None
    capability_tags: tuple[str, ...] = ()
    target_schema: str | None = None
    notes: str | None = None
    source_row: int = 0
    needs_authoring: bool = False


@dataclass
class CorpusDraft:
    """Parse result: records + row-level diagnostics (errors skip the row)."""

    items: list[DraftItem] = field(default_factory=list)
    contexts: list[DraftContext] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _cell(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _parse_tags(cell: str) -> tuple[str, ...]:
    if not cell:
        return ()
    parts = cell.replace(";", ",").split(",")
    return tuple(dict.fromkeys(p.strip().lower() for p in parts if p.strip()))


def _parse_expected_cell(cell: str) -> dict[str, Any]:
    """Parse an ``expected_values`` cell into a typed-spec dict.

    Accepted forms (aligned with the research rig):
    - a JSON object using the typed-spec keys, e.g. ``{"nums": [42]}``;
    - ``trap`` / ``zero`` keywords;
    - ``nums: 1, 2.5`` / ``names: a; b`` shorthand (both may appear, ``|``-separated);
    - a bare list of numbers (``42`` / ``1, 2``) or, failing that, of names.
    """

    text = cell.strip()
    if text.startswith("{"):
        parsed = json.loads(text)  # caller wraps errors with row context
        if not isinstance(parsed, dict):
            raise ValueError("expected_values JSON must be an object")
        unknown = set(parsed) - _EXPECTED_SPEC_KEYS
        if unknown:
            raise ValueError(f"unknown expected_values keys {sorted(unknown)}")
        return parsed

    lowered = text.lower()
    if lowered == "trap":
        return {"trap": True}
    if lowered == "zero":
        return {"zero": True}
    return _parse_expected_shorthand(text)


def _split_cell_list(raw: str) -> list[str]:
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def _parse_expected_shorthand(text: str) -> dict[str, Any]:
    """``nums:``/``names:`` prefixes, else a bare number/name list."""

    spec: dict[str, Any] = {}
    for part in text.split("|"):
        part = part.strip()
        low = part.lower()
        if low.startswith("nums:"):
            spec["nums"] = [float(x) for x in _split_cell_list(part[5:])]
        elif low.startswith("names:"):
            spec["names"] = _split_cell_list(part[6:])
    if spec:
        return spec

    tokens = _split_cell_list(text)
    if not tokens:
        raise ValueError("expected_values cell is empty")
    try:
        return {"nums": [float(t) for t in tokens]}
    except ValueError:
        return {"names": tokens}


def _answer_spec(answer_type: str, cell: str) -> dict[str, Any]:
    if answer_type == "gold_sql":
        return {"sql": cell}
    if answer_type == "eval_note":
        return {"note": cell}
    return _parse_expected_cell(cell)


def _row_type(row: dict[str, str], where: str, draft: CorpusDraft) -> str | None:
    explicit = _cell(row, "type").lower()
    if explicit:
        if explicit not in _ROW_TYPES:
            draft.errors.append(f"{where}: type {explicit!r} not in {_ROW_TYPES}")
            return None
        return explicit
    if _cell(row, "question") or any(_cell(row, c) for c in ANSWER_TYPES):
        return "question"
    if _cell(row, "context"):
        return "context"
    return None  # fully empty row — silently skipped


def _parse_question_row(
    row: dict[str, str], where: str, row_num: int, draft: CorpusDraft
) -> None:
    question = _cell(row, "question")
    if not question:
        draft.errors.append(f"{where}: question row with no question text")
        return

    filled = [t for t in ANSWER_TYPES if _cell(row, t)]
    if len(filled) > 1:
        draft.errors.append(
            f"{where}: multiple answer cells filled ({', '.join(filled)}); "
            "fill exactly one"
        )
        return

    explicit_type = _cell(row, "answer_type").lower() or None
    if explicit_type and explicit_type not in ANSWER_TYPES:
        draft.errors.append(
            f"{where}: answer_type {explicit_type!r} not in {ANSWER_TYPES}"
        )
        return
    if explicit_type and filled and explicit_type != filled[0]:
        draft.errors.append(
            f"{where}: answer_type {explicit_type!r} contradicts the filled "
            f"{filled[0]!r} cell"
        )
        return

    answer_type = filled[0] if filled else explicit_type
    spec: dict[str, Any] | None = None
    if filled:
        try:
            spec = _answer_spec(answer_type or "", _cell(row, filled[0]))
        except (ValueError, TypeError) as ex:
            draft.errors.append(f"{where}: bad {filled[0]} cell: {ex}")
            return

    tags = _parse_tags(_cell(row, "capability_tags"))
    for tag in unknown_tags(list(tags)):
        draft.warnings.append(f"{where}: unknown capability tag {tag!r}")

    draft.items.append(
        DraftItem(
            question=question,
            answer_type=answer_type,
            answer_spec=spec,
            capability_tags=tags,
            target_schema=_cell(row, "target_schema") or None,
            notes=_cell(row, "notes") or None,
            source_row=row_num,
            needs_authoring=not filled,
        )
    )


def parse_corpus_csv(text: str) -> CorpusDraft:
    """Parse the DP-B3 CSV into a :class:`CorpusDraft` (the validate dry-run)."""

    draft = CorpusDraft()
    reader = csv.DictReader(io.StringIO(text))
    headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    if "question" not in headers and "context" not in headers:
        draft.errors.append(
            "CSV must declare a 'question' and/or 'context' column "
            f"(got: {headers or 'no header row'})"
        )
        return draft

    for row_num, raw in enumerate(reader, start=2):  # 1-based + header row
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        where = f"row {row_num}"
        kind = _row_type(row, where, draft)
        if kind == "question":
            _parse_question_row(row, where, row_num, draft)
        elif kind == "context":
            ctx = _cell(row, "context") or _cell(row, "question")
            if ctx:
                draft.contexts.append(DraftContext(text=ctx, source_row=row_num))

    if len(draft.items) > MAX_ITEMS_PER_BENCHMARK:
        draft.warnings.append(
            f"{len(draft.items)} questions exceed the per-benchmark ceiling "
            f"({MAX_ITEMS_PER_BENCHMARK}); the import route will reject the overflow"
        )
    return draft
