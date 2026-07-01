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

"""Coverage audit — detect information lost in markdown → MDL conversion.

A reverse reconciliation (cf. the generative enrichment flow): decompose a source
document into atomic *claims* (stage A), flatten the MDL + instructions into
*facts* (stage B), align each claim to candidate facts and LLM-judge whether it is
captured (stage C), then aggregate a coverage report with remediation suggestions
(stage D). Advisory, not a gate — see ``wren_mdl_copilot.md`` coverage risks.
"""

from __future__ import annotations

import hashlib
import json  # noqa: TID251 - standalone agent JSON contract
import logging
from collections.abc import Callable
from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel, Field

from superset_ai_agent.llm.base import ChatMessage, ModelClient
from superset_ai_agent.llm.embeddings import Embedder
from superset_ai_agent.prompts.registry import get_prompt
from superset_ai_agent.semantic_layer.copilot.schemas import (
    CoverageClaim,
    CoverageFinding,
    CoverageReport,
    CoverageStatus,
    OverreachFinding,
)

logger = logging.getLogger(__name__)

#: Candidate MDL facts shown to the judge per claim (keyword-ranked).
CANDIDATES_PER_CLAIM = 6

#: Coverage ordering, most → least conservative (loss-surfacing wins ties).
_STATUS_RANK: dict[CoverageStatus, int] = {"missing": 0, "partial": 1, "covered": 2}


def _majority_finding(votes: list[CoverageFinding]) -> CoverageFinding:
    """Pick the majority status across votes; ties break to the more conservative.

    Conservative = lower coverage (missing < partial < covered), so a split jury
    surfaces potential loss rather than hiding it. Carries the matched/suggestion
    text from a vote that agrees with the winning status.
    """

    counts: dict[CoverageStatus, int] = {}
    for finding in votes:
        counts[finding.status] = counts.get(finding.status, 0) + 1
    winner = min(counts, key=lambda status: (-counts[status], _STATUS_RANK[status]))
    representative = next(v for v in votes if v.status == winner)
    return CoverageFinding(
        claim=votes[0].claim,
        status=winner,
        matched=representative.matched,
        rationale=representative.rationale,
        suggestion=representative.suggestion,
    )


class MdlFact(BaseModel):
    """One unit of semantic content the MDL already encodes."""

    kind: str
    ref: str
    text: str


class _ClaimExtraction(BaseModel):
    claims: list[CoverageClaim] = Field(default_factory=list)


class _RawFinding(BaseModel):
    claim_id: str
    status: CoverageStatus = "missing"
    matched: str = ""
    rationale: str = ""
    suggestion: str = ""


class _Judgement(BaseModel):
    findings: list[_RawFinding] = Field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in "".join(
            char.lower() if char.isalnum() else " " for char in text
        ).split()
        if len(token) > 2
    }


# -- Stage A: claim extraction ---------------------------------------------


#: Max chars of a provider/parse error surfaced to the user — enough to name the
#: cause (timeout, 401, rate limit, connection refused) without dumping a payload.
_REASON_MAX_CHARS = 240


def _short(text: str, limit: int = _REASON_MAX_CHARS) -> str:
    """Collapse whitespace and truncate so a reason stays one readable line."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


class ExtractionOutcome(NamedTuple):
    """Result of claim extraction: ``claims`` is ``None`` only on failure, and
    ``error`` then carries a human-readable reason to surface to the user."""

    claims: list[CoverageClaim] | None
    error: str | None = None


def extract_claims(
    model_client: ModelClient,
    *,
    document_text: str,
    model: str | None = None,
) -> ExtractionOutcome:
    """Extract atomic claims from a document.

    Returns an :class:`ExtractionOutcome`: ``claims`` is a (possibly empty) list on
    success, or ``None`` on failure with ``error`` set to a categorized,
    user-facing reason (prompt unavailable / provider error / unparseable
    response) so the coverage report can explain *why* extraction failed instead
    of only that it did.
    """

    if not document_text.strip():
        return ExtractionOutcome([])
    try:
        prompt = get_prompt("coverage_extract")
    except OSError:
        logger.warning("coverage_extract prompt missing.")
        return ExtractionOutcome(
            None,
            "the coverage extraction prompt is unavailable on the server "
            "(deployment issue)",
        )
    try:
        result = model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content=document_text),
            ],
            model=model,
            format_schema=_ClaimExtraction.model_json_schema(),
        )
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Coverage claim extraction failed (provider): %s", ex)
        return ExtractionOutcome(
            None,
            f"the model provider could not be reached or returned an error "
            f"({type(ex).__name__}: {_short(str(ex))})",
        )
    try:
        data = json.loads(result.content)
        return ExtractionOutcome(_ClaimExtraction.model_validate(data).claims)
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Coverage claim extraction failed (parse): %s", ex)
        return ExtractionOutcome(
            None,
            "the model returned a response that could not be read as claims "
            f"({type(ex).__name__})",
        )


# -- Stage B: MDL fact index -----------------------------------------------


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _column_fact(model_name: str, column: dict[str, Any]) -> MdlFact | None:
    name = _str(column.get("name"))
    if not name:
        return None
    raw_props = column.get("properties")
    props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
    parts = [
        name,
        _str(column.get("type")),
        _str(column.get("description")),
        _str(props.get("displayName")),
        _str(props.get("alias")),
        _str(column.get("expression")),
    ]
    return MdlFact(
        kind="column",
        ref=f"column:{model_name}.{name}",
        text=" ".join(part for part in parts if part).strip(),
    )


def build_mdl_facts(  # noqa: C901 - flat manifest walk over several entity kinds
    files: list[Any],
    *,
    instructions: list[str] | None = None,
) -> list[MdlFact]:
    """Flatten the project's MDL files + instructions into semantic facts.

    ``files`` are MDL file objects with a ``.content`` JSON string. Unparseable
    files are skipped (best-effort). Synonyms live in instructions, so they are
    included as facts.
    """

    facts: list[MdlFact] = []
    for file in files:
        content = getattr(file, "content", None)
        status = getattr(file, "status", "active")
        if not isinstance(content, str) or status == "deleted":
            continue
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        for model in payload.get("models", []) or []:
            if not isinstance(model, dict):
                continue
            name = _str(model.get("name"))
            if _str(model.get("description")):
                facts.append(
                    MdlFact(
                        kind="model",
                        ref=f"model:{name}",
                        text=f"{name} {model.get('description')}".strip(),
                    )
                )
            for column in model.get("columns", []) or []:
                if isinstance(column, dict):
                    fact = _column_fact(name, column)
                    if fact:
                        facts.append(fact)
        for metric in payload.get("metrics", []) or []:
            if isinstance(metric, dict) and _str(metric.get("name")):
                facts.append(
                    MdlFact(
                        kind="metric",
                        ref=f"metric:{metric.get('name')}",
                        text=" ".join(
                            part
                            for part in [
                                _str(metric.get("name")),
                                _str(metric.get("expression")),
                                _str(metric.get("description")),
                            ]
                            if part
                        ),
                    )
                )
        for rel in payload.get("relationships", []) or []:
            if isinstance(rel, dict) and _str(rel.get("name")):
                models = rel.get("models")
                facts.append(
                    MdlFact(
                        kind="relationship",
                        ref=f"relationship:{rel.get('name')}",
                        text=" ".join(
                            part
                            for part in [
                                _str(rel.get("name")),
                                " ".join(models) if isinstance(models, list) else "",
                                _str(rel.get("joinType")),
                                _str(rel.get("condition")),
                            ]
                            if part
                        ),
                    )
                )
    for index, instruction in enumerate(instructions or []):
        if instruction.strip():
            facts.append(
                MdlFact(
                    kind="instruction",
                    ref=f"instruction:{index}",
                    text=instruction.strip(),
                )
            )
    return facts


def _keyword_rank(claim: CoverageClaim, facts: list[MdlFact], k: int) -> list[MdlFact]:
    """Top-``k`` facts by token overlap with the claim (deterministic fallback)."""

    query = _tokens(f"{claim.subject} {claim.statement}")
    if not query:
        return facts[:k]
    scored = sorted(
        facts,
        key=lambda fact: len(query & _tokens(f"{fact.ref} {fact.text}")),
        reverse=True,
    )
    return [fact for fact in scored if query & _tokens(f"{fact.ref} {fact.text}")][:k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class _FactRanker:
    """Ranks candidate facts for a claim, embedding-backed when available.

    Embeds every fact once (in-memory cosine), degrading closed to keyword overlap
    when no embedder is configured or embedding fails — so candidate grounding is
    never a hard dependency (gap #5: keyword-only can over-report loss).
    """

    def __init__(self, facts: list[MdlFact], embedder: Embedder | None) -> None:
        self._facts = facts
        self._vectors: list[list[float]] | None = None
        if embedder is not None and embedder.is_available() and facts:
            try:
                self._vectors = embedder.embed([f"{f.ref} {f.text}" for f in facts])
                self._embedder: Embedder | None = embedder
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Coverage fact embedding failed; keyword rank: %s", ex)
                self._vectors = None
                self._embedder = None
        else:
            self._embedder = None

    def rank(self, claim: CoverageClaim, k: int) -> list[MdlFact]:
        if self._vectors is None or self._embedder is None:
            return _keyword_rank(claim, self._facts, k)
        try:
            query_vec = self._embedder.embed([f"{claim.subject} {claim.statement}"])[0]
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Coverage claim embedding failed; keyword rank: %s", ex)
            return _keyword_rank(claim, self._facts, k)
        scored = sorted(
            zip(self._facts, self._vectors, strict=False),
            key=lambda pair: _cosine(query_vec, pair[1]),
            reverse=True,
        )
        return [fact for fact, _vec in scored][:k]


# -- Stage C: alignment / judging ------------------------------------------


def judge_coverage(  # noqa: C901 - retrieval + voting + degrade paths in one seam
    model_client: ModelClient,
    claims: list[CoverageClaim],
    facts: list[MdlFact],
    *,
    model: str | None = None,
    candidates_per_claim: int = CANDIDATES_PER_CLAIM,
    embedder: Embedder | None = None,
    votes: int = 1,
) -> list[CoverageFinding]:
    """Judge each claim's coverage against candidate MDL facts.

    Degrades closed: on provider/parse failure every claim is reported ``missing``
    so the report errs toward surfacing potential loss rather than hiding it.
    """

    if not claims:
        return []

    ranker = _FactRanker(facts, embedder)
    payload_claims = []
    candidate_refs: dict[str, set[str]] = {}
    for index, claim in enumerate(claims):
        claim_id = f"c{index}"
        candidates = ranker.rank(claim, candidates_per_claim)
        candidate_refs[claim_id] = {fact.ref for fact in candidates}
        payload_claims.append(
            {
                "claim_id": claim_id,
                "kind": claim.kind,
                "subject": claim.subject,
                "statement": claim.statement,
                "candidate_facts": [
                    {"ref": fact.ref, "kind": fact.kind, "text": fact.text}
                    for fact in candidates
                ],
            }
        )

    def _all_missing(reason: str) -> list[CoverageFinding]:
        logger.warning("Coverage judging degraded: %s", reason)
        return [
            CoverageFinding(
                claim=claim,
                status="missing",
                rationale="Coverage could not be judged automatically.",
            )
            for claim in claims
        ]

    try:
        prompt = get_prompt("coverage_judge")
    except OSError:
        return _all_missing("coverage_judge prompt missing")

    def _one_vote() -> list[CoverageFinding] | None:
        try:
            result = model_client.chat(
                [
                    ChatMessage(role="system", content=prompt),
                    ChatMessage(
                        role="user",
                        content=json.dumps({"claims": payload_claims}, default=str),
                    ),
                ],
                model=model,
                format_schema=_Judgement.model_json_schema(),
            )
            judged = _Judgement.model_validate(json.loads(result.content)).findings
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Coverage judge vote failed: %s", ex)
            return None
        by_id = {raw.claim_id: raw for raw in judged}
        vote: list[CoverageFinding] = []
        for index, claim in enumerate(claims):
            raw = by_id.get(f"c{index}")
            if raw is None:
                vote.append(
                    CoverageFinding(
                        claim=claim,
                        status="missing",
                        rationale="No judgement returned for this claim.",
                    )
                )
            else:
                vote.append(
                    CoverageFinding(
                        claim=claim,
                        status=raw.status,
                        matched=raw.matched,
                        rationale=raw.rationale,
                        suggestion=raw.suggestion,
                    )
                )
        return vote

    ballots = [vote for _ in range(max(1, votes)) if (vote := _one_vote()) is not None]
    if not ballots:
        return _all_missing("all judge votes failed")
    if len(ballots) == 1:
        return ballots[0]
    return [
        _majority_finding([ballot[index] for ballot in ballots])
        for index in range(len(claims))
    ]


# -- Bidirectional: MDL over-reach (claims unsupported by the document) ------


class _RawOverreach(BaseModel):
    fact_ref: str
    supported: bool = False
    rationale: str = ""


class _OverreachJudgement(BaseModel):
    findings: list[_RawOverreach] = Field(default_factory=list)


def judge_overreach(
    model_client: ModelClient,
    claims: list[CoverageClaim],
    facts: list[MdlFact],
    *,
    model: str | None = None,
) -> list[OverreachFinding]:
    """Flag MDL facts unsupported by any claim. Degrades to ``[]`` (flags nothing).

    Over-reach is advisory and the conservative degrade is to *not* flag — a judge
    failure should not paint every fact as unsupported.
    """

    if not facts:
        return []
    try:
        prompt = get_prompt("coverage_overreach")
    except OSError:
        logger.warning("coverage_overreach prompt missing.")
        return []
    payload = {
        "claims": [
            {"kind": c.kind, "subject": c.subject, "statement": c.statement}
            for c in claims
        ],
        "mdl_facts": [
            {"fact_ref": f.ref, "kind": f.kind, "text": f.text} for f in facts
        ],
    }
    try:
        result = model_client.chat(
            [
                ChatMessage(role="system", content=prompt),
                ChatMessage(role="user", content=json.dumps(payload, default=str)),
            ],
            model=model,
            format_schema=_OverreachJudgement.model_json_schema(),
        )
        judged = _OverreachJudgement.model_validate(json.loads(result.content))
    except Exception as ex:  # pylint: disable=broad-except
        logger.warning("Coverage over-reach judging failed: %s", ex)
        return []
    kinds = {f.ref: f.kind for f in facts}
    return [
        OverreachFinding(
            fact_ref=raw.fact_ref,
            fact_kind=kinds.get(raw.fact_ref, ""),
            supported=False,
            rationale=raw.rationale,
        )
        for raw in judged.findings
        if not raw.supported and raw.fact_ref in kinds
    ]


# -- Caching (determinism on repeat) ---------------------------------------


class CoverageCache(Protocol):
    """Cache of coverage reports keyed on (document + manifest + model + votes)."""

    def get(self, key: str) -> CoverageReport | None: ...

    def set(self, key: str, report: CoverageReport) -> None: ...


class InMemoryCoverageCache:
    """Process-local cache. Repeat audits of unchanged inputs are identical + free.

    Per-worker only (a multi-worker deployment caches independently); a persistent
    backend is a deferred follow-on.
    """

    def __init__(self) -> None:
        self._store: dict[str, CoverageReport] = {}

    def get(self, key: str) -> CoverageReport | None:
        return self._store.get(key)

    def set(self, key: str, report: CoverageReport) -> None:
        self._store[key] = report


def audit_cache_key(
    document_text: str,
    facts: list[MdlFact],
    *,
    model: str | None,
    votes: int,
    include_overreach: bool = False,
) -> str:
    """Stable key over the audit inputs — same inputs ⇒ same cached report."""

    digest = hashlib.sha256()
    digest.update(document_text.encode("utf-8"))
    for text in sorted(fact.text for fact in facts):
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
    digest.update(f"|{model}|{votes}|{include_overreach}".encode())
    return digest.hexdigest()


# -- Stage D: orchestration + aggregation ----------------------------------


def aggregate_report(
    findings: list[CoverageFinding],
    *,
    document_id: str | None = None,
    document_filename: str = "",
    warnings: list[str] | None = None,
) -> CoverageReport:
    """Aggregate findings into a scored report (partial weighted 0.5)."""

    covered = sum(1 for f in findings if f.status == "covered")
    partial = sum(1 for f in findings if f.status == "partial")
    missing = sum(1 for f in findings if f.status == "missing")
    total = len(findings)
    score = (covered + 0.5 * partial) / total if total else 1.0
    return CoverageReport(
        document_id=document_id,
        document_filename=document_filename,
        findings=findings,
        total=total,
        covered=covered,
        partial=partial,
        missing=missing,
        score=round(score, 3),
        warnings=warnings or [],
    )


class CoverageCancelledError(Exception):
    """Raised when a ``should_cancel`` check trips between coverage stages.

    The audit cannot kill an in-flight LLM call, so supersession takes effect at
    the next stage boundary. Callers must not persist a partial result.
    """


def _raise_if_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise CoverageCancelledError()


# -- Live progress (Feature C) ----------------------------------------------

#: Coarse pipeline position per stage, for the UI stepper (Extract → Build →
#: Judge → Aggregate). Over-reach reuses the Judge slot (it is a second judge).
_PHASE_INDEX: dict[str, int] = {
    "extracting": 0,
    "building_facts": 1,
    "judging": 2,
    "checking_overreach": 2,
    "aggregating": 3,
}
_PHASE_TOTAL = 4


class CoverageProgress(NamedTuple):
    """One coarse progress tick emitted at a coverage-audit stage boundary.

    Advisory only — the same ``should_cancel`` seams carry these, so a callback
    failure must never break the audit (see ``_report_progress``).
    """

    stage: str
    detail: str = ""
    current: int = 0
    total: int = 0
    phase_index: int = 0
    phase_total: int = _PHASE_TOTAL


#: Polled at stage boundaries to surface live progress; mirrors ``should_cancel``.
ProgressCallback = Callable[[CoverageProgress], None]


def _report_progress(
    progress_cb: ProgressCallback | None,
    stage: str,
    *,
    detail: str = "",
    current: int = 0,
    total: int = 0,
) -> None:
    """Emit a progress tick; swallow any callback error (progress is advisory)."""

    if progress_cb is None:
        return
    try:
        progress_cb(
            CoverageProgress(
                stage=stage,
                detail=detail,
                current=current,
                total=total,
                phase_index=_PHASE_INDEX.get(stage, 0),
                phase_total=_PHASE_TOTAL,
            )
        )
    except Exception:  # pylint: disable=broad-except
        logger.warning("Coverage progress callback failed.", exc_info=True)


def run_coverage_audit(
    model_client: ModelClient,
    *,
    document_text: str,
    files: list[Any],
    instructions: list[str] | None = None,
    document_id: str | None = None,
    document_filename: str = "",
    model: str | None = None,
    embedder: Embedder | None = None,
    votes: int = 1,
    cache: CoverageCache | None = None,
    include_overreach: bool = False,
    should_cancel: Callable[[], bool] | None = None,
) -> CoverageReport:
    """Full coverage audit: extract → flatten → judge → aggregate.

    Cached on (document + manifest + model + votes + overreach) so a repeat audit of
    unchanged inputs is identical and free. ``votes`` > 1 runs the judge multiple
    times and takes the majority status per claim (ties break conservatively).
    ``include_overreach`` additionally flags MDL facts unsupported by the document.
    ``should_cancel`` is polled at each stage boundary; a True result raises
    ``CoverageCancelledError`` so a superseded background run stops without persisting.
    """

    facts = build_mdl_facts(files, instructions=instructions)
    key = audit_cache_key(
        document_text,
        facts,
        model=model,
        votes=votes,
        include_overreach=include_overreach,
    )
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    _raise_if_cancelled(should_cancel)
    outcome = extract_claims(model_client, document_text=document_text, model=model)
    if outcome.claims is None:
        # A provider failure is transient — do not cache it. Surface the reason so
        # the user can tell a deployment issue from a provider outage from bad output.
        return aggregate_report(
            [],
            document_id=document_id,
            document_filename=document_filename,
            warnings=[
                f"Claim extraction failed, so no coverage could be computed: "
                f"{outcome.error}."
            ],
        )
    claims = outcome.claims
    if not claims:
        report = aggregate_report(
            [],
            document_id=document_id,
            document_filename=document_filename,
            warnings=["No modelable claims were found in the document."],
        )
    else:
        _raise_if_cancelled(should_cancel)
        findings = judge_coverage(
            model_client,
            claims,
            facts,
            model=model,
            embedder=embedder,
            votes=votes,
        )
        warnings: list[str] = []
        if not facts:
            warnings.append(
                "The project has no MDL semantics yet; everything is missing."
            )
        report = aggregate_report(
            findings,
            document_id=document_id,
            document_filename=document_filename,
            warnings=warnings,
        )
        if include_overreach:
            _raise_if_cancelled(should_cancel)
            overreach = judge_overreach(model_client, claims, facts, model=model)
            report.overreach = overreach
            report.unsupported = len(overreach)
    if cache is not None:
        cache.set(key, report)
    return report


class CoverageDocument(NamedTuple):
    """One project document fed into a directory-level coverage run."""

    document_id: str
    filename: str
    text: str


def run_directory_coverage(
    model_client: ModelClient,
    *,
    documents: list[CoverageDocument],
    files: list[Any],
    instructions: list[str] | None = None,
    model: str | None = None,
    embedder: Embedder | None = None,
    votes: int = 1,
    include_overreach: bool = False,
    should_cancel: Callable[[], bool] | None = None,
    progress_cb: ProgressCallback | None = None,
) -> CoverageReport:
    """Audit the whole MDL directory against the union of project documents.

    The forward direction asks "did the active MDL capture everything the
    documents say?" by extracting claims from every document and judging them
    together against the active MDL facts (Feature B, decision D2 = union). With
    no documents the run is a no-op (score 1.0, a warning). ``should_cancel`` is
    polled per document and per stage so a superseded run stops promptly.
    ``progress_cb`` rides the same stage boundaries to surface live progress.
    """

    _report_progress(progress_cb, "building_facts")
    facts = build_mdl_facts(files, instructions=instructions)
    if not documents:
        return aggregate_report(
            [],
            warnings=["No documents to audit; coverage is vacuously complete."],
        )

    all_claims: list[CoverageClaim] = []
    # Source document aligned by index with ``all_claims`` (so each finding can be
    # tagged back to the document its claim came from).
    sources: list[CoverageDocument] = []
    warnings: list[str] = []
    failed_docs = 0
    total_docs = len(documents)
    for index, document in enumerate(documents):
        _raise_if_cancelled(should_cancel)
        _report_progress(
            progress_cb,
            "extracting",
            detail=document.filename,
            current=index + 1,
            total=total_docs,
        )
        outcome = extract_claims(
            model_client, document_text=document.text, model=model
        )
        if outcome.claims is None:
            failed_docs += 1
            # Name the document AND the reason so the user can act (retry a
            # provider blip vs. fix a deployment/config issue).
            warnings.append(
                f"Claim extraction failed for {document.filename}: {outcome.error}."
            )
            continue
        all_claims.extend(outcome.claims)
        sources.extend([document] * len(outcome.claims))

    if not all_claims:
        # Distinguish "nothing to model" from "extraction never succeeded" — the
        # latter is a failure to explain, not a clean 0-claim document set.
        if failed_docs == total_docs:
            warnings.append(
                f"Claim extraction failed for all {total_docs} document(s); "
                "coverage could not be computed. See the reasons above."
            )
        else:
            warnings.append("No modelable claims were found across the documents.")
        return aggregate_report([], warnings=warnings)

    _raise_if_cancelled(should_cancel)
    _report_progress(
        progress_cb,
        "judging",
        detail=f"{len(all_claims)} claims vs {len(facts)} facts",
    )
    findings = judge_coverage(
        model_client,
        all_claims,
        facts,
        model=model,
        embedder=embedder,
        votes=votes,
    )
    # Tag each finding with its source document. Guarded on equal length: every
    # judge path (incl. the degrade-to-all-missing seam) returns one finding per
    # claim in order, but if that ever changes we skip tagging rather than mis-map.
    if len(findings) == len(sources):
        for finding, source in zip(findings, sources, strict=False):
            finding.document_id = source.document_id
            finding.document_filename = source.filename
    if not facts:
        warnings.append("The project has no MDL semantics yet; everything is missing.")
    _report_progress(progress_cb, "aggregating")
    report = aggregate_report(findings, warnings=warnings)
    if include_overreach:
        _raise_if_cancelled(should_cancel)
        _report_progress(progress_cb, "checking_overreach")
        overreach = judge_overreach(model_client, all_claims, facts, model=model)
        report.overreach = overreach
        report.unsupported = len(overreach)
    return report
