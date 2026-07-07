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

"""Curation-time consistency linter (C4, plan_sql_agent_doc_grounding_spec.md).

No production BI copilot arbitrates "the semantic layer says X, the golden
query says Y" at runtime — conflicts are pushed to curation time (Databricks
Genie's mutually-consistent-instructions doctrine; Snowflake validates against
the verified-query repository). This module is that curation-time pass:
deterministic, read-only checks over a project's grounding artifacts, surfaced
to the curator via a project route. Findings never block anything.

Checks:

- ``golden_unknown_reference`` — a golden query's semantic SQL references a
  table/model that is neither an MDL model/view nor a physical
  ``tableReference`` of the active manifest (stale exemplars actively hurt:
  they are recalled verbatim into prompts).
- ``golden_conflicting_duplicates`` — two golden entries with the same
  normalized question but different SQL (which one should ground the prompt?).
- ``duplicate_metric_conflict`` — the same metric name defined differently in
  two active MDL files (the merge picks one; the curator should).
- ``instruction_unknown_identifier`` — an instruction mentions a snake_case
  identifier that matches no model/column/table name (heuristic, warning).
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent MDL parsing
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from superset_ai_agent.semantic_layer.golden_queries import (
    find_golden_queries_file,
    parse_golden_queries,
)
from superset_ai_agent.semantic_layer.memory_store import refs_from_sql

logger = logging.getLogger(__name__)

#: snake_case identifier candidates in instruction prose (≥2 segments), the
#: heuristic that keeps prose words from being flagged.
_IDENTIFIER_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


class ConsistencyFinding(BaseModel):
    """One curation-time inconsistency between grounding artifacts."""

    code: str
    severity: Literal["warning", "error"] = "warning"
    subject: str
    message: str


class ConsistencyReport(BaseModel):
    """The linter's result for one project."""

    project_id: str
    findings: list[ConsistencyFinding] = Field(default_factory=list)
    checked_golden_queries: int = 0
    checked_instructions: int = 0
    checked_metrics: int = 0


def _parsed_files(files: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for file in files:
        try:
            data = json.loads(getattr(file, "content", "") or "")
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            parsed.append((str(getattr(file, "path", "")), data))
    return parsed


def _collect_model_names(
    model: dict[str, Any],
    names: dict[str, set[str]],
) -> None:
    name = str(model.get("name") or "").lower()
    if name:
        names["models"].add(name)
    ref = model.get("tableReference")
    if isinstance(ref, dict):
        table = str(ref.get("table") or "").lower()
        schema = str(ref.get("schema") or "").lower()
        if table:
            names["physical"].add(table)
            if schema:
                names["physical"].add(f"{schema}.{table}")
    for column in model.get("columns", []) or []:
        if isinstance(column, dict) and column.get("name"):
            names["columns"].add(str(column["name"]).lower())


def _known_names(parsed: list[tuple[str, dict[str, Any]]]) -> dict[str, set[str]]:
    """Model/view/column and physical-table name sets across the active files."""

    names: dict[str, set[str]] = {"models": set(), "columns": set(), "physical": set()}
    for _, data in parsed:
        for model in data.get("models", []) or []:
            if isinstance(model, dict):
                _collect_model_names(model, names)
        for view in data.get("views", []) or []:
            if isinstance(view, dict) and view.get("name"):
                names["models"].add(str(view["name"]).lower())
        for metric in data.get("metrics", []) or []:
            if isinstance(metric, dict) and metric.get("name"):
                names["columns"].add(str(metric["name"]).lower())
    return names


def _norm_question(question: str) -> str:
    return " ".join(question.lower().split())


def _golden_findings(
    files: list[Any],
    names: dict[str, set[str]],
) -> tuple[list[ConsistencyFinding], int]:
    """Stale-reference + conflicting-duplicate checks over the golden file."""

    findings: list[ConsistencyFinding] = []
    golden_entries: list[Any] = []
    golden_file = find_golden_queries_file(files)
    if golden_file is not None:
        try:
            golden_entries = parse_golden_queries(golden_file.content).queries
        except (ValueError, TypeError) as ex:
            findings.append(
                ConsistencyFinding(
                    code="golden_file_unparseable",
                    severity="error",
                    subject=str(getattr(golden_file, "path", "queries.json")),
                    message=f"queries.json could not be parsed: {ex}",
                )
            )
    known_tables = names["models"] | names["physical"]
    for entry in golden_entries:
        tables, _ = refs_from_sql(entry.semantic_sql or "")
        unknown = sorted(
            table
            for table in {t.lower() for t in tables}
            if table not in known_tables and table.split(".")[-1] not in known_tables
        )
        if unknown:
            findings.append(
                ConsistencyFinding(
                    code="golden_unknown_reference",
                    severity="error",
                    subject=entry.name or entry.question,
                    message=(
                        "Golden query references tables absent from the active "
                        f"manifest: {', '.join(unknown)}. Stale exemplars are "
                        "recalled verbatim into prompts — update or retire it."
                    ),
                )
            )
    by_question: dict[str, str] = {}
    for entry in golden_entries:
        key = _norm_question(entry.question)
        prior = by_question.get(key)
        sql_norm = " ".join((entry.semantic_sql or "").split())
        if prior is not None and prior != sql_norm:
            findings.append(
                ConsistencyFinding(
                    code="golden_conflicting_duplicates",
                    severity="error",
                    subject=entry.question,
                    message=(
                        "Two golden queries share this question with different "
                        "SQL — recall may ground on either. Keep one."
                    ),
                )
            )
        by_question.setdefault(key, sql_norm)
    return findings, len(golden_entries)


def _metric_findings(
    parsed: list[tuple[str, dict[str, Any]]],
) -> tuple[list[ConsistencyFinding], int]:
    """Same metric name defined differently in two active files."""

    findings: list[ConsistencyFinding] = []
    metric_defs: dict[str, tuple[str, str]] = {}
    checked = 0
    for path, data in parsed:
        for metric in data.get("metrics", []) or []:
            if not isinstance(metric, dict) or not metric.get("name"):
                continue
            checked += 1
            name = str(metric["name"]).lower()
            definition = json.dumps(metric, sort_keys=True, default=str)
            prior = metric_defs.get(name)
            if prior is not None and prior[1] != definition:
                findings.append(
                    ConsistencyFinding(
                        code="duplicate_metric_conflict",
                        severity="error",
                        subject=str(metric["name"]),
                        message=(
                            f"Metric defined differently in {prior[0]} and "
                            f"{path} — the merge picks one silently; align or "
                            "remove one definition."
                        ),
                    )
                )
            metric_defs.setdefault(name, (path, definition))
    return findings, checked


def _instruction_findings(
    instructions: list[str],
    names: dict[str, set[str]],
) -> list[ConsistencyFinding]:
    """Instructions naming snake_case identifiers absent from the manifest."""

    findings: list[ConsistencyFinding] = []
    known = names["models"] | names["columns"] | names["physical"]
    for text in instructions:
        for identifier in sorted(set(_IDENTIFIER_RE.findall(text.lower()))):
            if identifier in known:
                continue
            findings.append(
                ConsistencyFinding(
                    code="instruction_unknown_identifier",
                    severity="warning",
                    subject=identifier,
                    message=(
                        f"Instruction mentions '{identifier}', which matches no "
                        "model, column, or table in the active manifest — a "
                        "typo or a stale reference?"
                    ),
                )
            )
    return findings


def lint_project_consistency(
    *,
    project_id: str,
    files: list[Any],
    instructions: list[str] | None = None,
) -> ConsistencyReport:
    """Run every deterministic check over the project's active artifacts.

    ``files`` is the project's active MDL file set (golden ``queries.json``
    included); ``instructions`` are the scope's instruction texts. Degrades
    closed per artifact: an unparseable file simply contributes nothing.
    """

    parsed = _parsed_files(files)
    names = _known_names(parsed)
    instruction_texts = instructions or []
    golden, checked_golden = _golden_findings(files, names)
    metric, checked_metrics = _metric_findings(parsed)
    return ConsistencyReport(
        project_id=project_id,
        findings=[
            *golden,
            *metric,
            *_instruction_findings(instruction_texts, names),
        ],
        checked_golden_queries=checked_golden,
        checked_instructions=len(instruction_texts),
        checked_metrics=checked_metrics,
    )
