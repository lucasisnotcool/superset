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

"""Coverage audit — markdown→MDL information-loss detection (stages A–D)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone agent JSON contract
from typing import Any

import pytest

from superset_ai_agent.llm.base import ChatMessage, ModelResult, ToolSpec
from superset_ai_agent.semantic_layer.copilot.coverage import (
    aggregate_report,
    build_mdl_facts,
    CoverageCancelledError,
    CoverageDocument,
    extract_claims,
    InMemoryCoverageCache,
    judge_coverage,
    judge_overreach,
    run_coverage_audit,
    run_directory_coverage,
)
from superset_ai_agent.semantic_layer.copilot.coverage_eval import (
    GoldLabel,
    score_coverage,
)
from superset_ai_agent.semantic_layer.copilot.schemas import (
    CoverageClaim,
    CoverageFinding,
)
from superset_ai_agent.semantic_layer.schemas import MdlFile

MDL = json.dumps(
    {
        "models": [
            {
                "name": "orders",
                "description": "Customer orders",
                "tableReference": {"schema": "public", "table": "orders"},
                "columns": [
                    {
                        "name": "net_amount",
                        "type": "DOUBLE",
                        "description": "Gross minus refunds",
                    }
                ],
            }
        ],
        "metrics": [{"name": "revenue", "expression": "SUM(net_amount)"}],
        "relationships": [
            {
                "name": "order_customer",
                "models": ["orders", "customers"],
                "joinType": "MANY_TO_ONE",
                "condition": "orders.customer_id = customers.id",
            }
        ],
    }
)


def _file(content: str = MDL, status: str = "active") -> MdlFile:
    return MdlFile(
        project_id="p1",
        path="models/orders.json",
        filename="orders.json",
        content=content,
        checksum="x",
        status=status,  # type: ignore[arg-type]
    )


class ScriptedModel:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls = 0

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        format_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> ModelResult:
        self.calls += 1
        return ModelResult(content=self._contents.pop(0))

    def is_reachable(self) -> bool:
        return True

    def list_models(self) -> list[Any]:
        return []


class RaisingModel(ScriptedModel):
    def chat(self, *args: Any, **kwargs: Any) -> ModelResult:
        raise RuntimeError("provider down")


# -- Stage B: deterministic, no LLM ----------------------------------------


def test_build_mdl_facts_flattens_models_columns_metrics_relationships() -> None:
    facts = build_mdl_facts([_file()], instructions=["Patty means drive_unit"])

    refs = {fact.ref for fact in facts}
    assert "model:orders" in refs
    assert "column:orders.net_amount" in refs
    assert "metric:revenue" in refs
    assert "relationship:order_customer" in refs
    assert "instruction:0" in refs
    net = next(f for f in facts if f.ref == "column:orders.net_amount")
    assert "Gross minus refunds" in net.text


def test_build_mdl_facts_skips_unparseable_and_deleted() -> None:
    facts = build_mdl_facts(
        [_file(content="{not json"), _file(status="deleted")],
    )
    assert facts == []


# -- Stage A + C + D with a scripted model ---------------------------------

CLAIMS_JSON = json.dumps(
    {
        "claims": [
            {
                "kind": "definition",
                "subject": "net_amount",
                "statement": "net_amount is gross minus refunds",
                "source_quote": "net amount = gross - refunds",
            },
            {
                "kind": "synonym",
                "subject": "drive_unit",
                "statement": "a drive unit is called a patty",
                "source_quote": "we call it a patty",
            },
        ]
    }
)

JUDGE_JSON = json.dumps(
    {
        "findings": [
            {
                "claim_id": "c0",
                "status": "covered",
                "matched": "column:orders.net_amount",
                "rationale": "description matches",
            },
            {
                "claim_id": "c1",
                "status": "missing",
                "suggestion": "add instruction: patty means drive_unit",
            },
        ]
    }
)


def test_extract_claims_parses_structured_output() -> None:
    model = ScriptedModel([CLAIMS_JSON])

    claims = extract_claims(model, document_text="some doc")

    assert claims is not None
    assert len(claims) == 2
    assert claims[0].kind == "definition"
    assert claims[1].subject == "drive_unit"


def test_extract_claims_empty_for_blank_document() -> None:
    model = ScriptedModel([])
    assert extract_claims(model, document_text="   ") == []


def test_judge_coverage_maps_findings_by_claim_id() -> None:
    claims = [
        CoverageClaim(kind="definition", subject="net_amount", statement="x"),
        CoverageClaim(kind="synonym", subject="drive_unit", statement="y"),
    ]
    model = ScriptedModel([JUDGE_JSON])

    findings = judge_coverage(model, claims, build_mdl_facts([_file()]))

    assert findings[0].status == "covered"
    assert findings[0].matched == "column:orders.net_amount"
    assert findings[1].status == "missing"


class FakeEmbedder:
    """Deterministic embedder: vector = per-keyword presence, for cosine ranking."""

    _VOCAB = ["net", "amount", "revenue", "patty", "drive", "order"]

    def is_available(self) -> bool:
        return True

    def dimensions(self) -> int:
        return len(self._VOCAB)

    def signature(self) -> str:
        return "fake:1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0 if word in text.lower() else 0.0 for word in self._VOCAB]
            for text in texts
        ]


def test_judge_coverage_uses_embedder_for_candidate_ranking() -> None:
    # The embedder is available, so candidate ranking goes through the embedding
    # path; judging still maps findings by id. Proves the wiring + degrade seam.
    claims = [CoverageClaim(kind="metric", subject="revenue", statement="revenue")]
    model = ScriptedModel(
        [json.dumps({"findings": [{"claim_id": "c0", "status": "covered"}]})]
    )

    findings = judge_coverage(
        model,
        claims,
        build_mdl_facts([_file()]),
        embedder=FakeEmbedder(),
    )

    assert findings[0].status == "covered"


def test_judge_coverage_degrades_closed_to_missing() -> None:
    claims = [CoverageClaim(statement="x")]
    findings = judge_coverage(RaisingModel([]), claims, [])

    assert findings[0].status == "missing"


def test_run_coverage_audit_aggregates_score() -> None:
    model = ScriptedModel([CLAIMS_JSON, JUDGE_JSON])

    report = run_coverage_audit(
        model,
        document_text="doc",
        files=[_file()],
        instructions=[],
        document_id="d1",
        document_filename="glossary.md",
    )

    assert report.total == 2
    assert report.covered == 1
    assert report.missing == 1
    assert report.score == 0.5
    assert report.document_filename == "glossary.md"


def test_run_coverage_audit_handles_extraction_failure() -> None:
    report = run_coverage_audit(RaisingModel([]), document_text="doc", files=[_file()])

    assert report.total == 0
    assert any("extraction failed" in w.lower() for w in report.warnings)


def test_multi_vote_takes_majority_status() -> None:
    # Three votes: covered, covered, missing → majority covered.
    def vote(status: str) -> str:
        return json.dumps({"findings": [{"claim_id": "c0", "status": status}]})

    claims = [CoverageClaim(kind="metric", subject="revenue", statement="revenue")]
    model = ScriptedModel([vote("covered"), vote("covered"), vote("missing")])

    findings = judge_coverage(model, claims, build_mdl_facts([_file()]), votes=3)

    assert model.calls == 3
    assert findings[0].status == "covered"


def test_multi_vote_ties_break_conservatively() -> None:
    # Split jury covered/missing (1–1 after a dropped vote) → conservative "missing".
    def vote(status: str) -> str:
        return json.dumps({"findings": [{"claim_id": "c0", "status": status}]})

    claims = [CoverageClaim(statement="x")]
    model = ScriptedModel([vote("covered"), vote("missing")])

    findings = judge_coverage(model, claims, [], votes=2)

    assert findings[0].status == "missing"


def test_audit_cache_returns_identical_report_without_recompute() -> None:
    cache = InMemoryCoverageCache()
    model = ScriptedModel([CLAIMS_JSON, JUDGE_JSON])

    first = run_coverage_audit(model, document_text="doc", files=[_file()], cache=cache)
    # Second run: cache hit → no further model calls, identical report.
    second = run_coverage_audit(
        model, document_text="doc", files=[_file()], cache=cache
    )

    assert model.calls == 2  # only the first run hit the model (extract + judge)
    assert second == first


OVERREACH_JSON = json.dumps(
    {
        "findings": [
            {
                "fact_ref": "metric:revenue",
                "supported": False,
                "rationale": "No claim mentions revenue.",
            }
        ]
    }
)


def test_judge_overreach_flags_unsupported_facts() -> None:
    claims = [CoverageClaim(kind="definition", subject="id", statement="id")]
    model = ScriptedModel([OVERREACH_JSON])

    overreach = judge_overreach(model, claims, build_mdl_facts([_file()]))

    assert len(overreach) == 1
    assert overreach[0].fact_ref == "metric:revenue"
    assert overreach[0].fact_kind == "metric"
    assert overreach[0].supported is False


def test_run_coverage_audit_includes_overreach_when_requested() -> None:
    model = ScriptedModel([CLAIMS_JSON, JUDGE_JSON, OVERREACH_JSON])

    report = run_coverage_audit(
        model,
        document_text="doc",
        files=[_file()],
        include_overreach=True,
    )

    assert report.unsupported == 1
    assert report.overreach[0].fact_ref == "metric:revenue"


def test_score_coverage_computes_accuracy_and_per_status() -> None:
    predicted = [
        CoverageFinding(
            claim=CoverageClaim(statement="net amount is gross minus refunds"),
            status="covered",
        ),
        CoverageFinding(
            claim=CoverageClaim(statement="a drive unit is a patty"),
            status="covered",  # wrong: gold says missing
        ),
    ]
    gold = [
        GoldLabel(statement="net amount is gross minus refunds", status="covered"),
        GoldLabel(statement="a drive unit is a patty", status="missing"),
        GoldLabel(statement="orders belong to customers", status="missing"),  # missed
    ]

    metrics = score_coverage(predicted, gold)

    assert metrics.total == 3
    assert metrics.matched == 2
    assert metrics.correct == 1
    assert metrics.accuracy == round(1 / 3, 3)
    # "missing" gold of 2, none predicted correctly → recall 0
    assert metrics.per_status["missing"].recall == 0.0
    assert metrics.per_status["covered"].support == 1


def test_aggregate_report_weights_partial_half() -> None:
    findings = [
        CoverageFinding(claim=CoverageClaim(statement="a"), status="covered"),
        CoverageFinding(claim=CoverageClaim(statement="b"), status="partial"),
        CoverageFinding(claim=CoverageClaim(statement="c"), status="missing"),
    ]
    report = aggregate_report(findings)

    assert report.score == round((1 + 0.5) / 3, 3)


# -- Feature B: directory-level aggregate + cancellation -------------------


def test_run_directory_coverage_unions_claims_across_documents() -> None:
    # Two documents each yield one claim; both are judged together (union).
    model = ScriptedModel(
        [
            json.dumps(
                {"claims": [{"kind": "definition", "subject": "a", "statement": "x"}]}
            ),
            json.dumps(
                {"claims": [{"kind": "synonym", "subject": "b", "statement": "y"}]}
            ),
            json.dumps(
                {
                    "findings": [
                        {"claim_id": "c0", "status": "covered", "matched": "m"},
                        {"claim_id": "c1", "status": "missing"},
                    ]
                }
            ),
        ]
    )
    report = run_directory_coverage(
        model,
        documents=[
            CoverageDocument("d1", "a.md", "doc a"),
            CoverageDocument("d2", "b.md", "doc b"),
        ],
        files=[_file()],
    )

    assert report.total == 2
    assert report.covered == 1
    assert report.missing == 1
    assert report.score == 0.5


def test_run_directory_coverage_no_documents_is_a_noop() -> None:
    model = ScriptedModel([])  # never called
    report = run_directory_coverage(model, documents=[], files=[_file()])

    assert report.total == 0
    assert report.score == 1.0
    assert model.calls == 0
    assert any("No documents" in w for w in report.warnings)


def test_run_directory_coverage_cancels_before_judging() -> None:
    # should_cancel trips after the first claim extraction → no judge call.
    model = ScriptedModel(
        [json.dumps({"claims": [{"kind": "definition", "statement": "x"}]})]
    )
    state = {"calls": 0}

    def should_cancel() -> bool:
        state["calls"] += 1
        return state["calls"] > 1  # allow the first stage, cancel before judging

    with pytest.raises(CoverageCancelledError):
        run_directory_coverage(
            model,
            documents=[CoverageDocument("d1", "a.md", "doc a")],
            files=[_file()],
            should_cancel=should_cancel,
        )
