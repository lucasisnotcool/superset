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

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from superset_ai_agent.schemas import ExecutionResult, InsightCard

ColumnKind = Literal["categorical", "numeric", "time", "unknown"]

NUMERIC_HINTS = (
    "amount",
    "avg",
    "average",
    "count",
    "gross",
    "max",
    "mean",
    "metric",
    "min",
    "moves",
    "num",
    "percent",
    "rate",
    "revenue",
    "sum",
    "total",
    "value",
)
LOW_SIGNAL_NUMERIC_NAMES = {"id", "pk", "key"}


@dataclass(frozen=True)
class ColumnProfile:
    """Simple profile for one result column."""

    name: str
    kind: ColumnKind
    non_null_count: int
    null_count: int


@dataclass(frozen=True)
class ResultAnalysis:
    """Detected shape of an execution result."""

    row_count: int
    profiles: list[ColumnProfile]
    category_column: str | None
    time_column: str | None
    numeric_columns: list[str]
    primary_metric: str | None
    is_empty: bool
    is_single_row: bool
    is_likely_truncated: bool


@dataclass(frozen=True)
class ArtifactBundle:
    """Deterministic analytics artifacts for one execution result."""

    answer_summary: str
    insight_cards: list[InsightCard]
    data_preview: ExecutionResult
    recommended_followups: list[str]


def profile_result(
    result: ExecutionResult,
    *,
    row_limit: int,
    question: str = "",
) -> ResultAnalysis:
    """Classify returned columns and identify primary dimensions and measures."""

    columns = _result_columns(result)
    profiles = [_profile_column(result.rows, column) for column in columns]
    numeric_columns = [
        profile.name
        for profile in profiles
        if profile.kind == "numeric" and not _is_low_signal_numeric(profile.name)
    ]
    if not numeric_columns:
        numeric_columns = [
            profile.name for profile in profiles if profile.kind == "numeric"
        ]
    time_column = next(
        (profile.name for profile in profiles if profile.kind == "time"),
        None,
    )
    category_column = detect_category_column(result, profiles)
    primary_metric = detect_primary_metric(question, numeric_columns)
    return ResultAnalysis(
        row_count=result.row_count,
        profiles=profiles,
        category_column=category_column,
        time_column=time_column,
        numeric_columns=numeric_columns,
        primary_metric=primary_metric,
        is_empty=result.row_count == 0 or not result.rows,
        is_single_row=result.row_count == 1 or len(result.rows) == 1,
        is_likely_truncated=result.is_truncated or result.row_count >= row_limit,
    )


def is_numeric_value(value: Any) -> bool:
    """Return true for numeric scalars and numeric strings."""

    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return False
        try:
            Decimal(text.rstrip("%"))
        except InvalidOperation:
            return False
        return True
    return False


def is_time_value(value: Any) -> bool:
    """Return true for date/datetime values and parseable date-like strings."""

    if isinstance(value, (date, datetime)):
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if len(text) < 7:
        return False
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def detect_category_column(
    result: ExecutionResult,
    profiles: list[ColumnProfile],
) -> str | None:
    """Choose the best string-like grouping column."""

    categorical_profiles = [
        profile
        for profile in profiles
        if profile.kind == "categorical" and profile.non_null_count > 0
    ]
    if not categorical_profiles:
        return None

    def score(profile: ColumnProfile) -> tuple[int, int, str]:
        values = {
            str(row.get(profile.name))
            for row in result.rows
            if row.get(profile.name) is not None
        }
        lower_name = profile.name.lower()
        name_score = (
            1
            if any(
                token in lower_name
                for token in ("stage", "type", "name", "category", "status")
            )
            else 0
        )
        return (name_score, -len(values), profile.name)

    return sorted(categorical_profiles, key=score, reverse=True)[0].name


def detect_primary_metric(question: str, numeric_columns: list[str]) -> str | None:
    """Choose the numeric measure most relevant to the user question."""

    if not numeric_columns:
        return None
    question_tokens = set(_tokens(question))
    for column in numeric_columns:
        column_tokens = set(_tokens(column))
        if question_tokens & column_tokens:
            return column
    for column in numeric_columns:
        if any(hint in column.lower() for hint in NUMERIC_HINTS):
            return column
    return numeric_columns[0]


def compute_category_stats(
    result: ExecutionResult,
    *,
    category_column: str,
    metric_column: str,
) -> list[dict[str, Any]]:
    """Aggregate returned rows by category and metric."""

    grouped: dict[str, Decimal] = {}
    for row in result.rows:
        category_value = row.get(category_column)
        metric_value = row.get(metric_column)
        if category_value is None or not is_numeric_value(metric_value):
            continue
        category = str(category_value)
        grouped[category] = grouped.get(category, Decimal("0")) + _to_decimal(
            metric_value
        )
    return [
        {"category": category, "value": value}
        for category, value in sorted(
            grouped.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]


def build_answer_summary(
    *,
    question: str,
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> str:
    """Create a concise deterministic answer summary."""

    if analysis.is_empty:
        return "No rows were returned for this query."
    scope = "returned rows" if analysis.is_likely_truncated else "result"
    if analysis.category_column and analysis.primary_metric:
        stats = compute_category_stats(
            result,
            category_column=analysis.category_column,
            metric_column=analysis.primary_metric,
        )
        if stats:
            leader = stats[0]
            return (
                f"{leader['category']} has the highest "
                f"{_display_name(analysis.primary_metric)} in the {scope}."
            )
    if analysis.primary_metric:
        total = _sum_numeric_column(result, analysis.primary_metric)
        return (
            f"The {scope} includes {result.row_count} row(s) with total "
            f"{_display_name(analysis.primary_metric)} of {_format_number(total)}."
        )
    return f"The query returned {result.row_count} row(s)."


def build_insight_cards(
    *,
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> list[InsightCard]:
    """Create up to three cards for top, spread, and lowest insights."""

    if analysis.is_empty:
        return [
            InsightCard(
                title="No rows returned",
                description="Try broadening filters or selecting a different grouping.",
                severity="warning",
            )
        ]

    if analysis.is_single_row:
        return _single_row_cards(result, analysis)

    if analysis.category_column and analysis.primary_metric:
        cards = _category_metric_cards(result, analysis)
        if cards:
            return cards[:3]

    if analysis.primary_metric:
        total = _sum_numeric_column(result, analysis.primary_metric)
        return [
            InsightCard(
                title=f"Total {_display_name(analysis.primary_metric)}",
                value=_format_number(total),
                metric=analysis.primary_metric,
            ),
            InsightCard(
                title="Rows returned",
                value=result.row_count,
                description=(
                    "The result may be truncated."
                    if analysis.is_likely_truncated
                    else None
                ),
                severity="warning" if analysis.is_likely_truncated else "info",
            ),
        ]

    return [InsightCard(title="Rows returned", value=result.row_count)]


def build_recommended_followups(
    *,
    question: str,
    analysis: ResultAnalysis,
) -> list[str]:
    """Suggest safe follow-up analytics questions."""

    if analysis.is_empty:
        return [
            "Broaden the filters and try again",
            "Show the available categories for this dataset",
        ]
    followups: list[str] = []
    if analysis.category_column and analysis.primary_metric:
        followups.extend(
            [
                f"Show {_display_name(analysis.primary_metric)} over time",
                f"Break this down by another category",
                f"Show the top 10 {_display_name(analysis.category_column)} values",
            ]
        )
    elif analysis.primary_metric:
        followups.extend(
            [
                f"Show {_display_name(analysis.primary_metric)} by category",
                "Compare this with the previous period",
            ]
        )
    else:
        followups.extend(
            [
                "Summarize these rows by category",
                "Show a count by status",
            ]
        )
    return followups[:3]


def build_artifact_bundle(
    *,
    question: str,
    result: ExecutionResult,
    row_limit: int,
) -> ArtifactBundle:
    """Build summary, cards, preview, and follow-up suggestions."""

    analysis = profile_result(result, row_limit=row_limit, question=question)
    data_preview = result.model_copy(
        update={
            "rows": result.rows[:row_limit],
            "is_truncated": analysis.is_likely_truncated,
        }
    )
    return ArtifactBundle(
        answer_summary=build_answer_summary(
            question=question,
            result=result,
            analysis=analysis,
        ),
        insight_cards=build_insight_cards(result=result, analysis=analysis),
        data_preview=data_preview,
        recommended_followups=build_recommended_followups(
            question=question,
            analysis=analysis,
        ),
    )


def _profile_column(rows: list[dict[str, Any]], column: str) -> ColumnProfile:
    values = [row.get(column) for row in rows]
    non_null_values = [value for value in values if value is not None]
    numeric_count = sum(1 for value in non_null_values if is_numeric_value(value))
    time_count = sum(1 for value in non_null_values if is_time_value(value))
    kind: ColumnKind = "unknown"
    if non_null_values:
        if numeric_count == len(non_null_values):
            kind = "numeric"
        elif time_count == len(non_null_values):
            kind = "time"
        else:
            kind = "categorical"
    return ColumnProfile(
        name=column,
        kind=kind,
        non_null_count=len(non_null_values),
        null_count=len(values) - len(non_null_values),
    )


def _result_columns(result: ExecutionResult) -> list[str]:
    if result.columns:
        return result.columns
    first_row = result.rows[0] if result.rows else {}
    return list(first_row.keys())


def _single_row_cards(
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> list[InsightCard]:
    row = result.rows[0] if result.rows else {}
    cards = [
        InsightCard(
            title=_display_name(column),
            value=_format_value(row.get(column)),
            metric=column,
        )
        for column in analysis.numeric_columns[:3]
    ]
    return cards or [InsightCard(title="Rows returned", value=result.row_count)]


def _category_metric_cards(
    result: ExecutionResult,
    analysis: ResultAnalysis,
) -> list[InsightCard]:
    if not analysis.category_column or not analysis.primary_metric:
        return []
    stats = compute_category_stats(
        result,
        category_column=analysis.category_column,
        metric_column=analysis.primary_metric,
    )
    if not stats:
        return []
    total = sum((item["value"] for item in stats), Decimal("0"))
    leader = stats[0]
    lowest = stats[-1]
    spread = leader["value"] - lowest["value"]
    percent = (leader["value"] / total) * Decimal("100") if total else Decimal("0")
    cards = [
        InsightCard(
            title="Top category",
            value=_format_number(leader["value"]),
            metric=analysis.primary_metric,
            category=str(leader["category"]),
            description=(
                f"{leader['category']} contributes {_format_number(percent)}% "
                f"of returned {_display_name(analysis.primary_metric)}."
            ),
            severity="success",
        )
    ]
    if len(stats) > 1:
        cards.append(
            InsightCard(
                title="Spread",
                value=_format_number(spread),
                metric=analysis.primary_metric,
                description=(
                    f"Difference between {leader['category']} and "
                    f"{lowest['category']}."
                ),
            )
        )
        cards.append(
            InsightCard(
                title="Lowest category",
                value=_format_number(lowest["value"]),
                metric=analysis.primary_metric,
                category=str(lowest["category"]),
                description=f"{lowest['category']} is lowest in the returned rows.",
            )
        )
    return cards


def _sum_numeric_column(result: ExecutionResult, column: str) -> Decimal:
    total = Decimal("0")
    for row in result.rows:
        value = row.get(column)
        if is_numeric_value(value):
            total += _to_decimal(value)
    return total


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        text = value.strip().replace(",", "").rstrip("%")
        return Decimal(text)
    return Decimal(str(value))


def _tokens(text: str) -> list[str]:
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in text
    )
    return [token for token in normalized.split() if token]


def _display_name(name: str) -> str:
    return name.replace("_", " ").strip() or name


def _format_number(value: Decimal | int | float) -> str:
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if decimal == decimal.to_integral():
        return f"{int(decimal):,}"
    return f"{float(decimal):,.2f}".rstrip("0").rstrip(".")


def _format_value(value: Any) -> str | int | float | None:
    if value is None:
        return None
    if is_numeric_value(value):
        return _format_number(_to_decimal(value))
    return str(value)


def _is_low_signal_numeric(column: str) -> bool:
    lower = column.lower()
    return lower in LOW_SIGNAL_NUMERIC_NAMES or lower.endswith("_id")
