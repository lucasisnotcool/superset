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
"""Shared helpers for the Seagate semantic-layer evaluation notebooks.

The notebooks are thin HTTP clients against a **running** Superset AI Agent, so
they automatically inherit whatever model provider and Wren settings the agent
was started with (``superset_ai_agent/.env``). This module concentrates all the
logic — authentication, the agent/semantic-layer client, the project lifecycle,
the ``test_queries.md`` parser, and an assistive grader — so the notebooks stay
short and reproducible.

Four experiments are run, in order, against one agent. They form a *monotonic*
progression on a single semantic project (each step only adds state), so no
teardown is needed between them — only a one-time clean baseline at the start:

1. ``basic``        — agent against the DB, no semantic layer.
2. ``context_dump`` — same, with the BI glossary prepended into the prompt.
3. ``wren_base``    — agent + onboarded base Wren layer (auto-activated).
4. ``wren_bi``      — agent + Wren layer enriched from the BI glossary.

Auth note: the shipped ``.env`` runs the agent in ``superset_session`` /
``user_session`` mode, so every agent call must carry a valid Superset identity.
:class:`AgentClient` logs into Superset (JWT), then forwards the bearer token (and
a CSRF token) on every agent request — exactly the identity the SQL Lab panel
forwards through the proxy in normal use.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

#: Repo-relative location of the Seagate fixture (glossary + graded queries).
FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent / "dev_fixtures" / "seagate_manufacturing"
)
GLOSSARY_PATH = FIXTURE_DIR / "bi_glossary.md"
TEST_QUERIES_PATH = FIXTURE_DIR / "test_queries.md"

EXPERIMENTS = (
    "basic",
    "context_dump",
    "wren_base",
    "wren_bi",
    # Combined: enriched Wren layer AND the full glossary in the prompt.
    "wren_bi_context",
)


@dataclass
class EvalConfig:
    """Endpoints and credentials for an evaluation run.

    Defaults target a native dev setup (agent on ``:8097``, Superset on
    ``:8088``). For the Docker smoke stack set ``agent_base_url`` to
    ``http://localhost:8090/ai-agent`` and ``superset_base_url`` to
    ``http://localhost:8090``. Everything is overridable via env vars or
    keyword arguments so a notebook can set them in one config cell.
    """

    agent_base_url: str = "http://localhost:8097"
    superset_base_url: str = "http://localhost:8088"
    username: str = "admin"
    password: str = "admin"  # noqa: S105 - dev default; override via env
    provider: str = "db"
    # The DB / schema the Seagate tables live in. ``database_id=None`` triggers
    # auto-discovery by ``database_name`` (see AgentClient.resolve_database_id).
    database_name: str = "examples"
    database_id: int | None = None
    schema_name: str = "seagate"
    catalog_name: str | None = None
    results_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent / "results"
    )
    request_timeout: int = 180

    @classmethod
    def from_env(cls, **overrides: Any) -> "EvalConfig":
        """Build config from ``EVAL_*`` env vars, with keyword overrides on top."""
        env = {
            "agent_base_url": os.getenv("EVAL_AGENT_BASE_URL"),
            "superset_base_url": os.getenv("EVAL_SUPERSET_BASE_URL"),
            "username": os.getenv("EVAL_SUPERSET_USERNAME"),
            "password": os.getenv("EVAL_SUPERSET_PASSWORD"),
            "database_name": os.getenv("EVAL_DATABASE_NAME"),
            "database_id": (
                int(os.getenv("EVAL_DATABASE_ID"))
                if os.getenv("EVAL_DATABASE_ID")
                else None
            ),
            "schema_name": os.getenv("EVAL_SCHEMA_NAME"),
        }
        env = {k: v for k, v in env.items() if v is not None}
        env.update(overrides)
        cfg = cls(**env)
        cfg.results_dir.mkdir(parents=True, exist_ok=True)
        return cfg


# --------------------------------------------------------------------------- #
# Agent / Superset client
# --------------------------------------------------------------------------- #


def _model_names(mdl_content: str) -> set[str]:
    """Return the set of model names in an MDL manifest JSON string (best-effort)."""
    try:
        manifest = json.loads(mdl_content)
    except (ValueError, TypeError):
        return set()
    return {m.get("name") for m in manifest.get("models", []) if m.get("name")}


class AgentError(RuntimeError):
    """Raised when an agent or Superset call fails in a way worth surfacing."""


class AgentClient:
    """HTTP client for the running agent, carrying a forwarded Superset identity."""

    def __init__(self, config: EvalConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.auth_headers: dict[str, str] = {}

    # --- auth ------------------------------------------------------------- #

    def login(self) -> dict[str, Any]:
        """Log into Superset (JWT) and capture the bearer + CSRF token.

        The agent (``superset_session`` mode) validates the forwarded identity by
        calling Superset ``GET /api/v1/me/`` and then reuses the same credentials
        for governed metadata/SQL calls. Returns the ``/me/`` payload as a check.
        """
        login = self.session.post(
            f"{self.config.superset_base_url}/api/v1/security/login",
            json={
                "username": self.config.username,
                "password": self.config.password,
                "provider": self.config.provider,
                "refresh": True,
            },
            timeout=self.config.request_timeout,
        )
        if login.status_code != 200:
            raise AgentError(
                f"Superset login failed ({login.status_code}): {login.text[:300]}"
            )
        token = login.json().get("access_token")
        if not token:
            raise AgentError("Superset login returned no access_token.")
        self.auth_headers["Authorization"] = f"Bearer {token}"
        # Best-effort CSRF token (some mutating Superset routes want it). Bearer
        # auth is generally CSRF-exempt, so a failure here is non-fatal.
        try:
            csrf = self.session.get(
                f"{self.config.superset_base_url}/api/v1/security/csrf_token/",
                headers=self.auth_headers,
                timeout=self.config.request_timeout,
            )
            if csrf.status_code == 200:
                self.auth_headers["X-CSRFToken"] = csrf.json()["result"]
        except requests.RequestException:
            pass
        return self.whoami()

    def whoami(self) -> dict[str, Any]:
        """Return the Superset ``/me/`` identity the forwarded token resolves to."""
        me = self.session.get(
            f"{self.config.superset_base_url}/api/v1/me/",
            headers=self.auth_headers,
            timeout=self.config.request_timeout,
        )
        if me.status_code != 200:
            raise AgentError(
                f"/api/v1/me/ failed ({me.status_code}); identity not established."
            )
        return me.json().get("result", me.json())

    # --- low-level request helpers --------------------------------------- #

    def _agent(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = {**self.auth_headers, **kwargs.pop("headers", {})}
        return self.session.request(
            method,
            f"{self.config.agent_base_url}{path}",
            headers=headers,
            timeout=self.config.request_timeout,
            **kwargs,
        )

    def _superset(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = {**self.auth_headers, **kwargs.pop("headers", {})}
        return self.session.request(
            method,
            f"{self.config.superset_base_url}{path}",
            headers=headers,
            timeout=self.config.request_timeout,
            **kwargs,
        )

    @staticmethod
    def _ok(resp: requests.Response, what: str) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise AgentError(f"{what} -> {resp.status_code}: {resp.text[:400]}")
        return resp.json() if resp.content else {}

    # --- discovery -------------------------------------------------------- #

    def health(self) -> dict[str, Any]:
        return self._ok(self._agent("GET", "/health"), "GET /health")

    def resolve_database_id(self) -> int:
        """Return ``config.database_id``, discovering it by name if unset."""
        if self.config.database_id is not None:
            return self.config.database_id
        resp = self._superset("GET", "/api/v1/database/?q=(page_size:200)")
        data = self._ok(resp, "GET /api/v1/database/")
        want = self.config.database_name.lower()
        for row in data.get("result", []):
            if row.get("database_name", "").lower() == want:
                self.config.database_id = int(row["id"])
                return self.config.database_id
        names = [r.get("database_name") for r in data.get("result", [])]
        raise AgentError(
            f"Database {self.config.database_name!r} not found. Available: {names}. "
            "Set EvalConfig.database_id explicitly."
        )

    # --- text-to-SQL ------------------------------------------------------ #

    def query(
        self,
        question: str,
        *,
        execute: bool = True,
        extra_context: str | None = None,
    ) -> dict[str, Any]:
        """Run one ``/agent/query`` turn. ``extra_context`` is prepended (exp 2)."""
        prompt = question
        if extra_context:
            prompt = (
                f"{extra_context}\n\n"
                "--- Using the reference material above where relevant, answer this "
                f"question ---\n{question}"
            )
        body = {
            "question": prompt,
            "database_id": self.resolve_database_id(),
            "schema_name": self.config.schema_name,
            "catalog_name": self.config.catalog_name,
            "execute": execute,
        }
        return self._ok(
            self._agent("POST", "/agent/query", json=body), "POST /agent/query"
        )

    # --- semantic project lifecycle -------------------------------------- #

    def list_projects(self) -> list[dict[str, Any]]:
        db_id = self.resolve_database_id()
        path = (
            f"/agent/semantic-layer/projects?database_id={db_id}"
            f"&schema_name={self.config.schema_name}"
        )
        return self._ok(self._agent("GET", path), "GET projects") or []

    def resolve_project(self, *, create_if_missing: bool = True) -> dict[str, Any]:
        body = {
            "database_id": self.resolve_database_id(),
            "schema_name": self.config.schema_name,
            "catalog_name": self.config.catalog_name,
            "create_if_missing": create_if_missing,
        }
        return self._ok(
            self._agent("POST", "/agent/semantic-layer/projects/resolve", json=body),
            "POST projects/resolve",
        )

    def delete_project(self, project_id: str) -> None:
        self._ok(
            self._agent("DELETE", f"/agent/semantic-layer/projects/{project_id}"),
            "DELETE project",
        )

    def clean_baseline(self) -> int:
        """Archive any existing Seagate project so experiments 1/2 see no MDL.

        Returns the number of projects archived. Safe to call repeatedly.
        """
        removed = 0
        for project in self.list_projects():
            self.delete_project(project["id"])
            removed += 1
        return removed

    # --- onboarding ------------------------------------------------------- #

    def onboard(self, project_id: str, *, poll_timeout: int = 600) -> dict[str, Any]:
        """Start onboarding (auto-activates base models) and block until done."""
        job = self._ok(
            self._agent("POST", f"/agent/semantic-layer/projects/{project_id}/onboard"),
            "POST onboard",
        )
        return self.poll_job(project_id, job["id"], timeout=poll_timeout)

    def poll_job(
        self, project_id: str, job_id: str, *, timeout: int = 600, interval: float = 2.0
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            job = self._ok(
                self._agent(
                    "GET",
                    f"/agent/semantic-layer/projects/{project_id}/jobs/{job_id}",
                ),
                "GET job",
            )
            if job.get("status") in {"completed", "failed"}:
                if job["status"] == "failed":
                    raise AgentError(f"Job failed: {job.get('error')}")
                return job
            if time.time() > deadline:
                raise AgentError(f"Job {job_id} did not finish within {timeout}s.")
            time.sleep(interval)

    # --- documents + enrichment ------------------------------------------ #

    def create_document_from_text(
        self, project_id: str, text: str, filename: str
    ) -> dict[str, Any]:
        body = {"filename": filename, "text": text, "content_type": "text/markdown"}
        return self._ok(
            self._agent(
                "POST",
                f"/agent/semantic-layer/projects/{project_id}/documents/text",
                json=body,
            ),
            "POST documents/text",
        )

    def enrich(self, project_id: str, document_id: str) -> dict[str, Any]:
        return self._ok(
            self._agent(
                "POST",
                f"/agent/semantic-layer/projects/{project_id}"
                f"/documents/{document_id}/enrich",
            ),
            "POST enrich",
        )

    def list_mdl_files(self, project_id: str) -> list[dict[str, Any]]:
        path = f"/agent/semantic-layer/projects/{project_id}/mdl-files"
        return self._ok(self._agent("GET", path), "GET mdl-files") or []

    def create_mdl_file(
        self, project_id: str, path: str, content: str, source_type: str = "manual"
    ) -> dict[str, Any]:
        body = {"path": path, "content": content, "source_type": source_type}
        route = f"/agent/semantic-layer/projects/{project_id}/mdl-files"
        return self._ok(self._agent("POST", route, json=body), "POST mdl-files")

    def update_mdl_file(
        self,
        project_id: str,
        file_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if content is not None:
            body["content"] = content
        if status is not None:
            body["status"] = status
        return self._ok(
            self._agent(
                "PATCH",
                f"/agent/semantic-layer/projects/{project_id}/mdl-files/{file_id}",
                json=body,
            ),
            "PATCH mdl-files",
        )

    def apply_enrichment(
        self, project_id: str, proposal: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist an enrichment proposal as the active MDL and supersede the base.

        This implementation's enrichment re-emits the **whole** manifest (all
        models, enriched) into a single file at ``proposed_path`` — which differs
        from the onboarding paths (``models/<table>.json``). Activating it
        *alongside* the base files produces ``Duplicate model name`` materialization
        errors (verified against the live stack). So after activating the enriched
        file we **deactivate every base file whose models it fully supersedes**,
        leaving one clean active manifest. The next query lazily re-indexes from it
        (content-checksum rebuild), so enriched semantics reach retrieval.
        """
        path = proposal["proposed_path"]
        content = proposal["proposed_content"]
        existing = {f["path"]: f for f in self.list_mdl_files(project_id)}
        if path in existing:
            file = self.update_mdl_file(
                project_id, existing[path]["id"], content=content
            )
        else:
            file = self.create_mdl_file(
                project_id, path, content, source_type="enriched_markdown"
            )
        file = self.update_mdl_file(project_id, file["id"], status="active")
        # Deactivate base files now superseded by the enriched manifest.
        enriched_models = _model_names(content)
        for other in self.list_mdl_files(project_id):
            if other["id"] == file["id"] or other.get("status") != "active":
                continue
            other_models = _model_names(other.get("content", ""))
            if other_models and other_models.issubset(enriched_models):
                self.update_mdl_file(project_id, other["id"], status="draft")
        return file

    def activate_all(self, project_id: str) -> int:
        """Ensure every non-deleted MDL file is active. Returns count activated."""
        activated = 0
        for file in self.list_mdl_files(project_id):
            if file.get("status") != "active":
                self.update_mdl_file(project_id, file["id"], status="active")
                activated += 1
        return activated


# --------------------------------------------------------------------------- #
# test_queries.md parser
# --------------------------------------------------------------------------- #

_LEVEL_RE = re.compile(r"^##\s+(L\d)\b", re.MULTILINE)
_Q_RE = re.compile(r"^\*\*Q(\d+)[^*]*\*\*\s*(.*)$")
_ANSWER_RE = re.compile(r"(?:Answer|Correct answer)\s*:\s*(.+)", re.IGNORECASE)
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers(text: str | None) -> list[float]:
    if not text:
        return []
    out: list[float] = []
    for tok in _NUM_RE.findall(text):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            continue
    return out


def _new_question(
    qid: str, level: str | None, text: str, is_trap: bool
) -> dict[str, Any]:
    return {
        "id": qid,
        "level": level,
        "question": text.strip(),
        "expected_raw": None,
        "expected_numbers": [],
        "is_trap": is_trap,
        "multi_value": False,
        "_locked": False,
    }


def _apply_answer(current: dict[str, Any], expected: str) -> None:
    """Record the ground-truth Answer line onto the current question."""
    current["expected_raw"] = expected
    current["expected_numbers"] = _numbers(expected)
    lowered = expected.lower()
    if any(w in lowered for w in ("undefined", "not applicable", "refus")):
        current["is_trap"] = True
    # Multi-value: several comma-separated names or >1 distinct number, or L4.
    current["multi_value"] = (
        len(current["expected_numbers"]) > 1
        or "," in expected
        or current["level"] == "L4"
    )


def _consume_line(current: dict[str, Any], line: str) -> None:
    """Fold a body line into the current question (continuation or Answer)."""
    # The question ends at the first bullet ("- Fact needed: ..."). Until then,
    # fold any wrapped continuation of the question line into the question text.
    if line.startswith("-"):
        current["_locked"] = True
    elif not current["_locked"] and line and not line.startswith("**"):
        current["question"] = f"{current['question']} {line}".strip()
    answer_match = _ANSWER_RE.search(line)
    if answer_match and not current["expected_raw"]:
        _apply_answer(current, answer_match.group(1).strip())


def parse_test_queries(path: Path = TEST_QUERIES_PATH) -> list[dict[str, Any]]:
    """Parse the graded fixture into question records with ground truth.

    Each record: ``id`` (e.g. ``"Q1"``), ``level`` (``L1``..``L4``), ``question``,
    ``expected_raw`` (the Answer line), ``expected_numbers``, ``is_trap`` (Q12),
    and ``multi_value`` (heuristic: an answer with several names/numbers).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    level = None
    current: dict[str, Any] | None = None
    for raw in lines:
        line = raw.strip()
        level_match = _LEVEL_RE.match(raw)
        if level_match:
            level = level_match.group(1)
            continue
        q_match = _Q_RE.match(line)
        if q_match:
            if current:
                records.append(current)
            current = _new_question(
                f"Q{q_match.group(1)}", level, q_match.group(2), "trap" in line.lower()
            )
            continue
        if current is not None:
            _consume_line(current, line)
    if current:
        records.append(current)
    for rec in records:
        rec.pop("_locked", None)
    return records


# --------------------------------------------------------------------------- #
# Running an experiment
# --------------------------------------------------------------------------- #


def _wren_summary(resp: dict[str, Any]) -> dict[str, Any]:
    wc = resp.get("wren_context") or {}
    return {
        "wren_enabled": wc.get("enabled"),
        "wren_available": wc.get("available"),
        "matched_models": wc.get("matched_models", []),
        "retrieved_item_count": wc.get("retrieved_item_count"),
        "warnings": wc.get("warnings", []),
    }


def _result_value(resp: dict[str, Any]) -> Any:
    """Best-effort single scalar from the executed result (for auto-grading)."""
    er = resp.get("execution_result") or {}
    rows = er.get("rows") or []
    if len(rows) == 1 and len(rows[0]) == 1:
        return next(iter(rows[0].values()))
    return None


def run_experiment(
    client: AgentClient,
    name: str,
    questions: list[dict[str, Any]],
    *,
    extra_context: str | None = None,
    save: bool = True,
) -> list[dict[str, Any]]:
    """Run every question for one experiment and capture the outcome."""
    if name not in EXPERIMENTS:
        raise ValueError(f"Unknown experiment {name!r}; expected one of {EXPERIMENTS}.")
    results: list[dict[str, Any]] = []
    for q in questions:
        try:
            resp = client.query(
                q["question"], execute=True, extra_context=extra_context
            )
            er = resp.get("execution_result") or {}
            results.append(
                {
                    "id": q["id"],
                    "level": q["level"],
                    "question": q["question"],
                    "status": resp.get("status"),
                    "sql": resp.get("sql"),
                    "answer_summary": resp.get("answer_summary"),
                    "result_rows": er.get("rows", []),
                    "result_value": _result_value(resp),
                    "row_count": er.get("row_count"),
                    "wren": _wren_summary(resp),
                    "error": None,
                }
            )
        except Exception as ex:  # noqa: BLE001 - capture, never abort the sweep
            results.append(
                {
                    "id": q["id"],
                    "level": q["level"],
                    "question": q["question"],
                    "status": "error",
                    "sql": None,
                    "answer_summary": None,
                    "result_rows": [],
                    "result_value": None,
                    "row_count": None,
                    "wren": {},
                    "error": str(ex),
                }
            )
    if save:
        out = client.config.results_dir / f"{name}.json"
        out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return results


def load_results(config: EvalConfig, name: str) -> list[dict[str, Any]]:
    return json.loads((config.results_dir / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #

_REFUSAL_HINTS = (
    "undefined",
    "not applicable",
    "no valid",
    "cannot",
    "can't",
    "excluded by definition",
    "does not apply",
    "n/a",
    "not defined",
    "no golden yield",
)


def _close(a: float, b: float) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) <= max(0.01 * scale, 0.01)


def grade_one(q: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Assistive grade for one question. Verdicts are deliberately conservative.

    Verdicts:
      - ``match``         — expected number found in the agent's answer/result.
      - ``mismatch``      — single-value question, expected number not found.
      - ``trap_ok``       — trap question and the agent refused / called it undefined.
      - ``trap_failed``   — trap question but the agent returned a confident number.
      - ``manual_review`` — multi-value / non-numeric; needs a human/LLM judge.
      - ``error``         — the agent call errored.
    """
    if result.get("error") or result.get("status") == "error":
        return {
            "verdict": "error",
            "expected": q["expected_raw"],
            "got": result.get("error"),
        }

    answer = (result.get("answer_summary") or "")
    got_text = f"{answer} {result.get('result_value')} {result.get('result_rows')}"
    got_nums = _numbers(got_text)

    if q["is_trap"]:
        refused = any(h in answer.lower() for h in _REFUSAL_HINTS)
        return {
            "verdict": "trap_ok" if refused else "trap_failed",
            "expected": "refusal / undefined",
            "got": answer[:200],
        }

    expected_nums = q.get("expected_numbers") or []
    if not expected_nums or q.get("multi_value"):
        return {
            "verdict": "manual_review",
            "expected": q["expected_raw"],
            "got": (answer[:200] or str(result.get("result_value"))),
        }

    target = expected_nums[0]
    hit = any(_close(target, g) for g in got_nums)
    value = result.get("result_value")
    return {
        "verdict": "match" if hit else "mismatch",
        "expected": q["expected_raw"],
        "got": (str(value) if value is not None else answer[:200]),
    }


def grade_experiment(
    questions: list[dict[str, Any]], results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {r["id"]: r for r in results}
    graded: list[dict[str, Any]] = []
    for q in questions:
        r = by_id.get(q["id"], {})
        verdict = grade_one(q, r)
        graded.append({"id": q["id"], "level": q["level"], **verdict})
    return graded


def score_summary(graded: list[dict[str, Any]]) -> dict[str, int]:
    """Count verdicts (``match``/``mismatch``/``trap_ok``/... ) for a quick headline."""
    summary: dict[str, int] = {}
    for row in graded:
        summary[row["verdict"]] = summary.get(row["verdict"], 0) + 1
    return summary


def read_glossary(path: Path = GLOSSARY_PATH) -> str:
    return path.read_text(encoding="utf-8")
