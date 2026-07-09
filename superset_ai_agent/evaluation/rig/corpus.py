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
"""Question corpus: CSV <-> normalized records (+ a legacy-markdown shim).

The corpus is the fixture-agnostic replacement for the hand-formatted
``test_queries.md`` + hardcoded ``seagate_scoring.EXPECTED``. One row = one test
item, carrying a *typed expected answer* whose vocabulary is the in-app platform's
``AnswerType`` (``gold_sql`` / ``expected_values`` / ``eval_note``) so items are
portable between the research rig and the in-app Benchmarks feature.

A record's ``answer_spec`` is shaped exactly for the scorer that consumes it
(``rig.scoring``): ``{"sql": ...}`` for gold_sql, ``{"note": ...}`` for eval_note,
and ``{"nums"|"names"|"trap"|"zero", "tolerance"}`` for expected_values — the same
dict ``evals.typed_spec.score_expected_values`` already understands.
"""

from __future__ import annotations

import csv
import io
import json  # noqa: TID251 - standalone eval tooling, independent of Superset
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The typed-answer vocabulary (mirrors ``evals.schemas.AnswerType``).
ANSWER_TYPES = ("gold_sql", "expected_values", "eval_note")

#: CSV columns. ``id``/``question``/``answer_type`` are required; the three
#: answer columns are conditionally required by ``answer_type``; the rest optional.
REQUIRED_COLUMNS = ("id", "question", "answer_type")
ANSWER_COLUMNS = {
    "gold_sql": "gold_sql",
    "eval_note": "eval_note",
    "expected_values": "expected_values",
}
OPTIONAL_COLUMNS = ("capability_tags", "tolerance", "notes")

#: Capability tags the scoreboard knows about (unknown tags warn, never fail).
KNOWN_TAGS = frozenset(
    {
        "slang",
        "join",
        "join1",
        "xschema",
        "xschema2",
        "xschema3",
        "bridge",
        "metric",
        "trap",
        "negative",
        "temporal",
        "multihop",
        "distractor",
        "golden",
        "viewable",
    }
)


@dataclass(frozen=True)
class QuestionRecord:
    """One normalized test item, ready for the harness + scorer."""

    id: str
    question: str
    answer_type: str
    answer_spec: dict[str, Any]
    capability_tags: tuple[str, ...] = ()
    notes: str | None = None


@dataclass
class CorpusLoad:
    """Result of loading a corpus: the good records + per-row problems."""

    records: list[QuestionRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- #
# expected_values cell -> spec
# --------------------------------------------------------------------------- #
def _parse_expected_values(  # noqa: C901 - one branch per accepted cell form
    cell: str, tolerance: str | None
) -> dict[str, Any]:
    """Parse an ``expected_values`` cell into a ``score_expected_values`` spec.

    Accepts (in priority order): a JSON object (advanced escape hatch, e.g.
    ``{"names":["Vantage"],"absent":["Nimbus"]}``); the keywords ``trap`` / ``zero``;
    or a comma-separated list where numeric tokens become ``nums`` and the rest
    become ``names``. ``tolerance`` (when given) is attached for numeric matching.
    """

    text = (cell or "").strip()
    if not text:
        raise ValueError("expected_values is empty")
    spec: dict[str, Any]
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("expected_values JSON must be an object")
        spec = parsed
    elif text.lower() == "trap":
        spec = {"trap": True}
    elif text.lower() == "zero":
        spec = {"zero": True}
    else:
        nums: list[float] = []
        names: list[str] = []
        for tok in (t.strip() for t in text.split(",")):
            if not tok:
                continue
            try:
                nums.append(float(tok.replace(",", "")))
            except ValueError:
                names.append(tok)
        spec = {}
        if nums:
            spec["nums"] = nums
        if names:
            spec["names"] = names
        if not spec:
            raise ValueError(f"expected_values {text!r} parsed to nothing")
    if tolerance:
        try:
            spec["tolerance"] = float(tolerance)
        except ValueError as ex:
            raise ValueError(f"tolerance {tolerance!r} is not a number") from ex
    return spec


def _build_answer_spec(answer_type: str, row: dict[str, str]) -> dict[str, Any]:
    """Construct the scorer-shaped ``answer_spec`` for one row (may raise)."""

    if answer_type == "gold_sql":
        sql = (row.get("gold_sql") or "").strip()
        if not sql:
            raise ValueError("answer_type=gold_sql but gold_sql column is empty")
        return {"sql": sql}
    if answer_type == "eval_note":
        note = (row.get("eval_note") or "").strip()
        if not note:
            raise ValueError("answer_type=eval_note but eval_note column is empty")
        return {"note": note}
    if answer_type == "expected_values":
        return _parse_expected_values(
            row.get("expected_values") or "", row.get("tolerance")
        )
    raise ValueError(
        f"unknown answer_type {answer_type!r}; expected one of {ANSWER_TYPES}"
    )


def _parse_tags(cell: str | None) -> tuple[str, ...]:
    """``"metric;temporal"`` -> ``("metric", "temporal")`` (``;`` or ``,``)."""

    if not cell:
        return ()
    raw = cell.replace(",", ";")
    return tuple(t.strip() for t in raw.split(";") if t.strip())


def _coerce_expected_cell(value: Any) -> str:
    """Normalize a generator's ``expected_values`` field to a parseable cell."""

    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(
            value
        )  # JSON escape hatch (handled by _parse_expected_values)
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def record_from_dict(data: dict[str, Any], *, position: int = 0) -> QuestionRecord:
    """Normalize one generator-produced item into a :class:`QuestionRecord`.

    Reuses the same spec/tag logic as CSV loading so generated and hand-authored
    corpora are identical. Raises ``ValueError`` on an unusable item.
    """

    qid = str(data.get("id") or f"Q{position}").strip()
    question = str(data.get("question") or "").strip()
    answer_type = str(data.get("answer_type") or "").strip()
    if not question:
        raise ValueError("empty question")
    if answer_type not in ANSWER_TYPES:
        raise ValueError(f"answer_type {answer_type!r} not in {ANSWER_TYPES}")
    row = {
        "gold_sql": str(data.get("gold_sql") or ""),
        "eval_note": str(data.get("eval_note") or ""),
        "expected_values": _coerce_expected_cell(data.get("expected_values")),
        "tolerance": (
            str(data["tolerance"]) if data.get("tolerance") is not None else None
        ),
    }
    spec = _build_answer_spec(answer_type, row)
    raw_tags = data.get("capability_tags") or ()
    tags = (
        _parse_tags(raw_tags)
        if isinstance(raw_tags, str)
        else tuple(str(t).strip() for t in raw_tags if str(t).strip())
    )
    return QuestionRecord(
        id=qid,
        question=question,
        answer_type=answer_type,
        answer_spec=spec,
        capability_tags=tags,
        notes=(str(data.get("notes")).strip() or None) if data.get("notes") else None,
    )


# --------------------------------------------------------------------------- #
# CSV loading
# --------------------------------------------------------------------------- #
def load_corpus_csv(source: str | Path) -> CorpusLoad:  # noqa: C901 - per-row checks
    """Load a question corpus from a CSV path or raw CSV text.

    Fatal issues (missing required headers) populate ``errors`` and return no
    records. Per-row issues are accumulated in ``errors`` with the row's id/line so
    the whole file is validated in one pass (the dumber agent gets every problem at
    once, not one-at-a-time). Unknown capability tags produce ``warnings``.
    """

    text = (
        Path(source).read_text(encoding="utf-8")
        if isinstance(source, Path) or "\n" not in str(source)
        else str(source)
    )
    load = CorpusLoad()
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        load.errors.append(f"CSV missing required column(s): {', '.join(missing)}")
        return load

    seen_ids: set[str] = set()
    for line_no, row in enumerate(reader, start=2):  # header is line 1
        qid = (row.get("id") or "").strip()
        question = (row.get("question") or "").strip()
        answer_type = (row.get("answer_type") or "").strip()
        where = f"row {line_no} (id={qid or '?'})"
        if not qid:
            load.errors.append(f"{where}: empty id")
            continue
        if qid in seen_ids:
            load.errors.append(f"{where}: duplicate id")
            continue
        seen_ids.add(qid)
        if not question:
            load.errors.append(f"{where}: empty question")
            continue
        if answer_type not in ANSWER_TYPES:
            load.errors.append(
                f"{where}: answer_type {answer_type!r} not in {ANSWER_TYPES}"
            )
            continue
        try:
            spec = _build_answer_spec(answer_type, row)
        except (ValueError, TypeError) as ex:
            load.errors.append(f"{where}: {ex}")
            continue
        tags = _parse_tags(row.get("capability_tags"))
        for tag in tags:
            if tag not in KNOWN_TAGS:
                load.warnings.append(f"{where}: unknown capability tag {tag!r}")
        load.records.append(
            QuestionRecord(
                id=qid,
                question=question,
                answer_type=answer_type,
                answer_spec=spec,
                capability_tags=tags,
                notes=(row.get("notes") or "").strip() or None,
            )
        )
    if not load.records and not load.errors:
        load.errors.append("CSV has a valid header but no data rows")
    return load


def records_to_csv(records: list[QuestionRecord]) -> str:
    """Serialize records back to the canonical CSV (round-trips ``load_corpus_csv``)."""

    cols = [
        "id",
        "question",
        "answer_type",
        "capability_tags",
        "gold_sql",
        "eval_note",
        "expected_values",
        "tolerance",
        "notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()
    for rec in records:
        row = {c: "" for c in cols}
        row.update(
            id=rec.id,
            question=rec.question,
            answer_type=rec.answer_type,
            capability_tags=";".join(rec.capability_tags),
            notes=rec.notes or "",
        )
        spec = rec.answer_spec
        if rec.answer_type == "gold_sql":
            row["gold_sql"] = spec.get("sql", "")
        elif rec.answer_type == "eval_note":
            row["eval_note"] = spec.get("note", "")
        else:  # expected_values: emit JSON for lossless round-trip
            ev = {k: v for k, v in spec.items() if k != "tolerance"}
            row["expected_values"] = json.dumps(ev)
            if "tolerance" in spec:
                row["tolerance"] = str(spec["tolerance"])
        writer.writerow(row)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Legacy-markdown shim (Phase 6 / Seagate reuse — R6)
# --------------------------------------------------------------------------- #
def from_markdown_and_expected(
    md_path: str | Path,
    expected: dict[str, dict[str, Any]],
    capability: dict[str, tuple[str, ...]] | None = None,
) -> CorpusLoad:
    """Build a corpus from the legacy ``test_queries.md`` + an ``EXPECTED`` dict.

    Lets the existing Seagate fixture feed the new harness unchanged (R6): question
    text comes from the markdown parser, ground truth from the hardcoded
    ``seagate_scoring.EXPECTED`` (already in ``score_expected_values`` shape), and
    tags from ``seagate_scoring.CAPABILITY``. Import is local so this module has no
    hard dependency on the eval package when only CSV loading is used.
    """

    import eval_common as ec  # noqa: PLC0415 - optional legacy dependency

    load = CorpusLoad()
    by_id = {q["id"]: q for q in ec.parse_test_queries(Path(md_path))}
    capability = capability or {}
    for qid, spec in expected.items():
        q = by_id.get(qid)
        if q is None:
            load.warnings.append(f"{qid}: in EXPECTED but not in markdown; skipped")
            continue
        load.records.append(
            QuestionRecord(
                id=qid,
                question=q["question"],
                answer_type="expected_values",
                answer_spec=dict(spec),
                capability_tags=tuple(capability.get(qid, ())),
            )
        )
    return load
