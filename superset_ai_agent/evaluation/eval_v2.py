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
"""v2 evaluation harness — cross-schema, coverage, repeated-run, Copilot, distractors.

This extends :mod:`eval_common` (which stays the working legacy single-schema
harness) with the experiments E6-E10 from ``EVAL_V2_SPEC.md``:

* **E6** repeated-run convergence — :meth:`AgentClientV2.enrich_round` +
  :func:`provenance_kind_counts`.
* **E7** coverage as a metric — :meth:`AgentClientV2.project_coverage` /
  :meth:`AgentClientV2.wait_for_coverage`.
* **E8** Copilot path — :meth:`AgentClientV2.copilot_turn` (SSE) +
  :meth:`AgentClientV2.copilot_apply`.
* **E9** distractor discrimination — :func:`table_selection_metrics` /
  :func:`sql_references_tables`.
* **E10** cross-schema — multi-schema :meth:`AgentClientV2.resolve_project`.

The pure functions (parsing, metrics) are module-level and unit-tested offline; the
client methods are thin wrappers around them so most of the harness is testable
without a live server.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset
import time
from pathlib import Path
from typing import Any, Iterable

import eval_common as ec

#: Superset's ``database.backend`` value(s) that provide real schemas (R4).
POSTGRES_BACKENDS = frozenset({"postgresql", "postgres"})

#: Max attachment chars the UI slices to before sending a Copilot turn
#: (CopilotPanel.tsx ``MAX_ATTACHMENT_CHARS``). The server truncates again to its
#: own ceiling; we mirror the UI so the harness is faithful.
MAX_ATTACHMENT_CHARS = 200_000

#: The EXACT production auto-onboard message (index.tsx ``AUTO_ONBOARD_MESSAGE``).
#: Note it asks the Copilot to onboard the doc's tables AND enrich them in one turn,
#: so a single auto-onboard turn already includes a first enrichment pass.
AUTO_ONBOARD_MESSAGE = (
    "Read the attached document(s) and onboard the tables they describe from "
    "this database, then add the relationships and enrich the models with the "
    "definitions, synonyms, and metrics the document specifies. Show me one "
    "changeset to review."
)

#: An additional Copilot *enrichment refinement* pass on an already-built MDL —
#: used by E12 to test whether passes beyond the first add value (the E6 question,
#: on the Copilot path). Deliberately phrased as "improve what's missing".
COPILOT_ENRICH_MESSAGE = (
    "Review the current MDL models against the attached business glossary and "
    "improve them: add any definitions, synonyms/aliases, custom metrics (e.g. "
    "Golden Yield, True Pass Rate), region rollups, and calendar/shift mappings "
    "the glossary specifies but the models do not yet capture. Show me one "
    "changeset to review."
)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def fixture_dir(fixture_name: str = "seagate_multi") -> Path:
    return ec.FIXTURE_DIR.parent / fixture_name


def load_table_manifest(fixture_name: str = "seagate_multi") -> dict[str, Any]:
    """Read ``tables.json`` (relevant/distractor sets, schema map) for a fixture."""
    path = fixture_dir(fixture_name) / "tables.json"
    return json.loads(path.read_text(encoding="utf-8"))


def text_attachment(
    filename: str, text: str, *, content_type: str = "text/markdown"
) -> dict[str, Any]:
    """Build a Copilot ``MessageAttachment`` (inline text fed into the user turn).

    This is the correct channel for handing the glossary to the Copilot (E8): the
    server renders ``text`` into the prompt under a ``### {filename}`` header
    (``_attachments_text``), so the document is structurally separated from the
    instruction rather than embedded in the message body. The text is sliced to
    ``MAX_ATTACHMENT_CHARS`` to mirror the UI (the server truncates again to its own
    ceiling).
    """
    truncated = len(text) > MAX_ATTACHMENT_CHARS
    return {
        "filename": filename,
        "content_type": content_type,
        "text": text[:MAX_ATTACHMENT_CHARS],
        "truncated": truncated,
    }


# --------------------------------------------------------------------------- #
# Pure logic — SSE parsing (E8)
# --------------------------------------------------------------------------- #
def parse_sse_stream(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Parse an SSE byte/text stream into a list of decoded ``data:`` JSON objects.

    The agent emits ``event: <type>\\ndata: <json>\\n\\n`` frames where the JSON
    payload already carries its own ``type`` (and ``changeset``/``agent_step``/
    ``detail``), so we key off the decoded data and ignore the ``event:`` line.
    Non-JSON or comment lines are skipped.
    """
    events: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            events.append(json.loads(payload))
        except ValueError:
            continue
    return events


def changeset_from_events(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the ``complete`` event's changeset, or None (with error if present)."""
    for event in events:
        if event.get("type") == "complete":
            return event.get("changeset")
    return None


def error_from_events(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if event.get("type") == "error":
            return event.get("detail") or "unknown copilot error"
    return None


# --------------------------------------------------------------------------- #
# Pure logic — active models, provenance, distractor metrics (E6/E9)
# --------------------------------------------------------------------------- #
def active_models_from_files(files: list[dict[str, Any]]) -> set[str]:
    """Union of model names across every *active* MDL file."""
    names: set[str] = set()
    for f in files:
        if f.get("status") == "active":
            names |= ec._model_names(f.get("content", ""))
    return names


def provenance_kind_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        kind = entry.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def sql_references_tables(sql: str | None, tables: Iterable[str]) -> list[str]:
    """Return the subset of ``tables`` named in ``sql`` (case-insensitive substring)."""
    if not sql:
        return []
    lowered = sql.lower()
    return sorted({t for t in tables if t.lower() in lowered})


def _item_mdl(item: dict[str, Any]) -> dict[str, Any]:
    """Parse a changeset item's MDL content (best-effort)."""
    content = item.get("content") or item.get("proposed_content") or ""
    if isinstance(content, dict):
        return content
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return {}


def models_from_changeset(items: list[dict[str, Any]]) -> set[str]:
    """Model names proposed across a changeset (independent of activation).

    Lets E11 measure which tables the Copilot *chose* even if the changeset can't be
    activated (the relationships bug). Reads ``models[].name`` from each item.
    """
    names: set[str] = set()
    for item in items:
        for model in _item_mdl(item).get("models", []) or []:
            if isinstance(model, dict) and model.get("name"):
                names.add(model["name"])
    return names


def table_selection_metrics(
    selected: Iterable[str],
    relevant: Iterable[str],
    distractors: Iterable[str],
) -> dict[str, Any]:
    """Precision/recall of table selection vs the relevant set, plus distractor leak.

    ``selected`` are the model/table names the agent put in the active MDL.
    Names outside ``relevant ∪ distractors`` are ignored for precision (they are
    neither a hit nor a known distractor). Recall is over the full relevant set.
    """
    selected_s = {s.lower() for s in selected}
    relevant_s = {r.lower() for r in relevant}
    distractor_s = {d.lower() for d in distractors}

    true_pos = selected_s & relevant_s
    false_distractors = selected_s & distractor_s
    missed = relevant_s - selected_s
    known_selected = true_pos | false_distractors

    precision = len(true_pos) / len(known_selected) if known_selected else 1.0
    recall = len(true_pos) / len(relevant_s) if relevant_s else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "selected_relevant": sorted(true_pos),
        "distractor_inclusions": sorted(false_distractors),
        "distractor_inclusion_rate": round(
            len(false_distractors) / len(distractor_s), 3
        )
        if distractor_s
        else 0.0,
        "missed_relevant": sorted(missed),
    }


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class AgentClientV2(ec.AgentClient):
    """Multi-schema-aware client with coverage / provenance / Copilot helpers."""

    def __init__(
        self, config: ec.EvalConfig, *, schema_names: list[str] | None = None
    ) -> None:
        super().__init__(config)
        #: Full schema scope for the project (primary + secondaries). Defaults to
        #: the single configured schema for back-compat.
        self.schema_names = schema_names or [config.schema_name]

    # --- preconditions (R1, R4) ----------------------------------------- #
    def database_backend(self) -> str:
        resp = self._superset("GET", f"/api/v1/database/{self.resolve_database_id()}")
        data = self._ok(resp, "GET database")
        return (data.get("result") or {}).get("backend", "")

    def assert_eval_preconditions(
        self, *, require_postgres: bool = True
    ) -> dict[str, Any]:
        """Fail fast on a non-Postgres examples DB; warn about the memory loop.

        Postgres is enforceable (multi-schema needs real schemas — R4). The
        learning-loop confound (R1) is **not** API-readable: ``/health`` does not
        expose ``WREN_MEMORY_STORE``. The operator must set it to ``none`` out of
        band; we surface that as a loud warning rather than silently trusting it.
        """
        health = self.health()
        backend = self.database_backend()
        warnings: list[str] = []
        if require_postgres and backend not in POSTGRES_BACKENDS:
            raise ec.AgentError(
                f"Multi-schema eval requires Postgres; the examples DB backend is "
                f"{backend!r}. SQLite has no real schemas (EVAL_V2_SPEC.md R4)."
            )
        warnings.append(
            "WREN_MEMORY_STORE is not exposed by any API; the operator MUST start "
            "the agent with WREN_MEMORY_STORE=none for a fair grounding ablation "
            "(RESULTS.md F1 / EVAL_V2_SPEC.md R1). This guard cannot verify it."
        )
        return {"backend": backend, "health": health, "warnings": warnings}

    # --- multi-schema resolve (E10) ------------------------------------- #
    def resolve_project(  # type: ignore[override]
        self, *, create_if_missing: bool = True
    ) -> dict[str, Any]:
        body = {
            "database_id": self.resolve_database_id(),
            "schema_name": self.config.schema_name,
            "schema_names": self.schema_names,
            "catalog_name": self.config.catalog_name,
            "create_if_missing": create_if_missing,
        }
        return self._ok(
            self._agent("POST", "/agent/semantic-layer/projects/resolve", json=body),
            "POST projects/resolve",
        )

    # --- coverage (E6/E7) ------------------------------------------------ #
    def project_coverage(self, project_id: str) -> float | None:
        for project in self.list_projects():
            if project.get("id") == project_id:
                return project.get("coverage_score")
        return None

    def wait_for_coverage(
        self,
        project_id: str,
        *,
        timeout: int = 180,
        interval: float = 3.0,
        stable_reads: int = 2,
    ) -> float | None:
        """Poll the project list until ``coverage_score`` is non-null and stable.

        Coverage runs as a debounced background job (no public refresh route), so we
        wait for the score to settle: it must be the same non-null value for
        ``stable_reads`` consecutive polls before we trust it.
        """
        deadline = time.time() + timeout
        seen: list[float] = []
        last: float | None = None
        while time.time() < deadline:
            last = self.project_coverage(project_id)
            if last is not None:
                seen.append(last)
                if len(seen) >= stable_reads and len(set(seen[-stable_reads:])) == 1:
                    return last
            time.sleep(interval)
        return last

    # --- provenance (E6/E8/E9) ------------------------------------------ #
    def provenance(self, project_id: str) -> list[dict[str, Any]]:
        path = f"/agent/semantic-layer/projects/{project_id}/provenance"
        return self._ok(self._agent("GET", path), "GET provenance") or []

    def active_model_names(self, project_id: str) -> set[str]:
        return active_models_from_files(self.list_mdl_files(project_id))

    # --- repeated-run convergence (E6) ---------------------------------- #
    def enrich_round(
        self, project_id: str, document_id: str, *, wait_coverage: bool = True
    ) -> dict[str, Any]:
        """One enrich→apply→activate→(coverage) round; returns the per-round signal.

        Grading the 15-question sweep per round is orchestrated by the notebook
        (it needs the question set); this captures the intrinsic per-round state:
        active models, coverage, and the provenance kind-count delta.

        Enrichment is non-deterministic and a round can emit an MDL the engine-gated
        activation rejects (422 "MDL failed validation", e.g. a duplicated model —
        RESULTS.md F9). That is a *failed round*, not a fatal error: we record it
        (``error`` set, ``activated`` False) and leave the prior active MDL in place
        so the convergence loop continues rather than aborting.
        """
        error: str | None = None
        proposed_path: str | None = None
        try:
            proposal = self.enrich(project_id, document_id)
            proposed_path = proposal.get("proposed_path")
            self.apply_enrichment(project_id, proposal)
            activated = True
        except ec.AgentError as ex:
            error = str(ex)
            activated = False
        coverage = self.wait_for_coverage(project_id) if wait_coverage else None
        prov = self.provenance(project_id)
        return {
            "coverage": coverage,
            "active_models": sorted(self.active_model_names(project_id)),
            "provenance_kinds": provenance_kind_counts(prov),
            "proposed_path": proposed_path,
            "activated": activated,
            "error": error,
        }

    # --- Copilot path (E8) ---------------------------------------------- #
    def copilot_turn(
        self,
        project_id: str,
        message: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        conversation_id: str | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Drive one Copilot turn over the SSE stream; return changeset + steps.

        Returns ``{"changeset": ..., "error": ..., "events": [...]}``. The changeset
        items are *proposals* — call :meth:`copilot_apply` to persist the accepted
        subset (they land as drafts, then activate via ``mdl-files`` PATCH).
        """
        body: dict[str, Any] = {
            "message": message,
            "attachments": attachments or [],
            "conversation_id": conversation_id,
        }
        if max_steps is not None:
            body["max_steps"] = max_steps
        resp = self._agent(
            "POST",
            f"/agent/semantic-layer/projects/{project_id}/copilot/stream",
            json=body,
            stream=True,
        )
        if resp.status_code >= 400:
            raise ec.AgentError(
                f"copilot/stream -> {resp.status_code}: {resp.text[:400]}"
            )
        events = parse_sse_stream(resp.iter_lines())
        return {
            "changeset": changeset_from_events(events),
            "error": error_from_events(events),
            "events": events,
        }

    def copilot_apply(
        self,
        project_id: str,
        items: list[dict[str, Any]],
        *,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        body = {"items": items, "conversation_id": conversation_id}
        return self._ok(
            self._agent(
                "POST",
                f"/agent/semantic-layer/projects/{project_id}/copilot/apply",
                json=body,
            ),
            "POST copilot/apply",
        )

    # --- full Copilot build: turn → apply → activate (E8/E11/E12) -------- #
    def copilot_build(
        self,
        project_id: str,
        message: str,
        *,
        glossary: str | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """One end-to-end Copilot build: stream a turn, apply its changeset, activate.

        The glossary (if given) is handed to the Copilot as an inline attachment
        (the channel the UI uses). Returns the turn result plus ``items`` (changeset
        size), ``applied``, ``activated``, and ``activate_error`` (a 422 on activation
        — e.g. an overlay file invalid on its own — is captured, not raised, so a
        sweep can continue and the failure is visible). Activation prefers the atomic
        ``bulk-status`` route and falls back to per-file (see ``activate_all``).
        """
        attachments = None
        if glossary:
            attachments = [text_attachment("bi_glossary.md", glossary)]
        turn = self.copilot_turn(
            project_id, message, attachments=attachments, max_steps=max_steps
        )
        raw_items = (
            [] if turn.get("error") else (turn.get("changeset") or {}).get("items", [])
        )
        # Selection metrics come from the changeset the Copilot actually produced.
        # Relationships-only files now activate natively (they are valid project
        # fragments — empty_root admits them and the bulk-status route validates the
        # merged manifest), so the changeset is applied and activated as-is.
        proposed_models = sorted(models_from_changeset(raw_items))
        applied = activated = False
        activate_error: str | None = None
        if raw_items:
            self.copilot_apply(project_id, raw_items)
            applied = True
            try:
                self.activate_all(project_id)
                activated = True
            except ec.AgentError as ex:
                activate_error = str(ex)
        return {
            **turn,
            "items": len(raw_items),
            "proposed_models": proposed_models,
            # Retained at 0 for result-shape stability (notebooks read this key); the
            # relationship fold was removed once relationships-only files activate.
            "relationships_folded": 0,
            "applied": applied,
            "activated": activated,
            "activate_error": activate_error,
        }

    def auto_onboard(
        self,
        project_id: str,
        glossary: str,
        *,
        upload: bool = True,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Drive the production auto-onboard turn (selective onboard + first enrich).

        Replicates the UI's ``AUTO_ONBOARD_MESSAGE`` flow: the doc is first persisted
        as a project document (so the Copilot's ``read_document``/coverage tools see
        it — the UI does this via its ingestion pipeline) and also attached inline,
        then the onboard-and-enrich turn runs and its changeset is applied + activated.
        Unlike the deterministic ``/onboard`` (which models *every* table in scope),
        the Copilot selects only the tables the glossary describes — the E9/E11 crux.
        """
        if upload:
            self.create_document_from_text(project_id, glossary, "bi_glossary.md")
        return self.copilot_build(
            project_id, AUTO_ONBOARD_MESSAGE, glossary=glossary, max_steps=max_steps
        )

    def copilot_enrich_pass(
        self,
        project_id: str,
        glossary: str,
        *,
        max_steps: int | None = None,
        wait_coverage: bool = True,
    ) -> dict[str, Any]:
        """One additional Copilot enrichment-refinement pass on an existing MDL (E12).

        The Copilot-path analogue of ``enrich_round`` (which uses the deterministic
        endpoint). Used to test whether passes beyond the first add value — the E6
        question, re-asked on the path the product actually ships.
        """
        result = self.copilot_build(
            project_id, COPILOT_ENRICH_MESSAGE, glossary=glossary, max_steps=max_steps
        )
        result["coverage"] = (
            self.wait_for_coverage(project_id) if wait_coverage else None
        )
        result["active_models"] = sorted(self.active_model_names(project_id))
        result["provenance_kinds"] = provenance_kind_counts(self.provenance(project_id))
        return result

    # --- distractor discrimination (E9) --------------------------------- #
    def selection_metrics(
        self, project_id: str, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Table-selection precision/recall vs the fixture's R/D sets."""
        return table_selection_metrics(
            self.active_model_names(project_id),
            manifest["relevant_tables"],
            manifest["distractor_tables"],
        )

    def out_of_scope_schema_pulled_in(
        self, project: dict[str, Any], manifest: dict[str, Any]
    ) -> list[str]:
        """Return out-of-scope schemas the project erroneously includes (E9 scope)."""
        ref_schemas = {
            s
            for t, s in manifest["table_schema"].items()
            if t in manifest["distractor_tables"] and s not in self.schema_names
        }
        return sorted(ref_schemas & set(project.get("schema_names") or []))
