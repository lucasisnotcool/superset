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
"""v3 evaluation harness — semantic VIEWS and golden QUERIES (+ shared memory).

Extends :mod:`eval_v2` (the cross-schema/Copilot harness) with the experiments
for the two features shipped after v2:

* **Views** (semantic, MDL-Copilot-authored) — E13 authoring quality, E14
  query-time lift, E15 the native-vs-semantic authoring gate (spec D6 / impl
  Step 6.5).
* **Golden queries** (``queries.json``) + DB-scoped shared memory / access-aware
  recall — E16 golden-recall accuracy lift, E17 recall-merge / verified signal.

The pure functions (view parsing, authoring metrics, recall-signal detection) are
module-level and unit-tested offline; the client methods on :class:`AgentClientV3`
are thin wrappers so most of the harness is testable without a live server.
"""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset
from typing import Any, Iterable

import eval_v2 as ev2

#: The reserved path for the project-scoped golden-query file (mirrors
#: ``semantic_layer/golden_queries.GOLDEN_QUERIES_PATH``).
GOLDEN_QUERIES_PATH = "queries.json"

#: A Copilot turn that asks for reusable **views** to be authored from a document
#: describing standard cross-model reports. Views require a *document-grounded*
#: trigger (spec D1: never invented from raw schema), so the message points the
#: Copilot at the attached "standard reports" addendum.
VIEW_AUTHOR_MESSAGE = (
    "The attached document defines several standard, reusable analyses that join "
    "across models (cross-schema joins, window functions, CTEs). For each such "
    "reusable pattern, author a semantic VIEW over the MDL model names so analysts "
    "can query it by name. Give every view a clear properties.description. Show me "
    "one changeset to review."
)


# --------------------------------------------------------------------------- #
# Pure logic — views (E13/E14/E15)
# --------------------------------------------------------------------------- #
def _file_views(content: str | dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort extract ``views[]`` from one MDL file's content."""
    body: dict[str, Any]
    if isinstance(content, dict):
        body = content
    else:
        try:
            body = json.loads(content or "")
        except (ValueError, TypeError):
            return []
    views = body.get("views") if isinstance(body, dict) else None
    return [v for v in (views or []) if isinstance(v, dict)]


def views_from_files(
    files: list[dict[str, Any]], *, only_active: bool = True
) -> list[dict[str, Any]]:
    """Collect view objects across MDL files (active by default)."""
    out: list[dict[str, Any]] = []
    for f in files:
        if only_active and f.get("status") != "active":
            continue
        out.extend(_file_views(f.get("content", "")))
    return out


def views_from_changeset(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect views proposed across a Copilot changeset (independent of activation)."""
    out: list[dict[str, Any]] = []
    for item in items:
        out.extend(_file_views(ev2._item_mdl(item)))
    return out


def view_is_semantic(view: dict[str, Any]) -> bool:
    """A view is *semantic* (engine-expanded) iff it carries no ``dialect`` marker.

    Native (``dialect``-set) views are kept off the wren-core manifest; in Phase 1
    the Copilot is only taught to author semantic views, so a non-semantic view is
    a signal worth surfacing.
    """
    return not (view.get("dialect"))


def view_references_physical_schema(
    view: dict[str, Any], physical_schemas: Iterable[str]
) -> bool:
    """True if a view statement hand-qualifies a physical ``schema.`` prefix.

    The load-bearing cross-schema guidance is "write the statement over MODEL
    names, never physical ``schema.table``". A semantic view that names a physical
    schema is the failure mode the skill text warns against (it bypasses the model
    layer); detecting it quantifies prompt adherence (spec §5.6 / R2).
    """
    stmt = (view.get("statement") or "").lower()
    return any(f"{s.lower()}." in stmt for s in physical_schemas)


def view_authoring_metrics(
    views: list[dict[str, Any]], *, physical_schemas: Iterable[str] = ()
) -> dict[str, Any]:
    """Summarise a set of authored views (E13)."""
    physical_schemas = list(physical_schemas)
    names = sorted({v.get("name", "") for v in views if v.get("name")})
    with_desc = sum(1 for v in views if (v.get("properties") or {}).get("description"))
    semantic = [v for v in views if view_is_semantic(v)]
    native = [v for v in views if not view_is_semantic(v)]
    phys = [v for v in semantic if view_references_physical_schema(v, physical_schemas)]
    return {
        "count": len(views),
        "names": names,
        "with_description": with_desc,
        "description_rate": round(with_desc / len(views), 3) if views else 0.0,
        "semantic": len(semantic),
        "native": len(native),
        "semantic_referencing_physical_schema": len(phys),
        "physical_leak_names": sorted(v.get("name", "") for v in phys if v.get("name")),
    }


def sql_uses_any(sql: str | None, names: Iterable[str]) -> list[str]:
    """Subset of ``names`` (e.g. view names) named in ``sql`` (case-insensitive)."""
    return ev2.sql_references_tables(sql, names)


# --------------------------------------------------------------------------- #
# Pure logic — golden queries / recall (E16/E17)
# --------------------------------------------------------------------------- #
def parse_golden_queries(content: str | dict[str, Any]) -> list[dict[str, Any]]:
    """Read the ``queries[]`` entries from a ``queries.json`` file content."""
    body: dict[str, Any]
    if isinstance(content, dict):
        body = content
    else:
        try:
            body = json.loads(content or "")
        except (ValueError, TypeError):
            return []
    items = body.get("queries") if isinstance(body, dict) else None
    return [q for q in (items or []) if isinstance(q, dict)]


def find_golden_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the ``queries.json`` MDL file (any status), if present."""
    for f in files:
        path = (f.get("path") or "").strip().lstrip("/").lower()
        if path == GOLDEN_QUERIES_PATH:
            return f
    return None


def recalled_example_count(resp: dict[str, Any]) -> int | None:
    """Number of few-shot pairs the draft node recalled, if the response exposes it.

    The agent's ``/agent/query`` response carries a ``wren_context`` whose
    ``recalled_example_count`` (when memory/golden recall is enabled) reflects how
    many pairs — runtime memory *and* golden queries, post access-filter+merge —
    were injected. ``None`` means the field was not present (older build / memory
    off).
    """
    wc = resp.get("wren_context") or {}
    for key in ("recalled_example_count", "recalled_examples", "example_count"):
        val = wc.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, list):
            return len(val)
    return None


def _norm_sql(sql: str | None) -> str:
    return " ".join((sql or "").lower().split())


def sql_matches_golden(produced_sql: str | None, golden_sql: str | None) -> bool:
    """Heuristic: did the agent's SQL reproduce the golden query's shape?

    A strong attribution signal for E16 — if the produced SQL is (modulo
    whitespace/case) the golden's semantic SQL, the golden was almost certainly the
    few-shot the model leaned on. Substring either way to tolerate a wrapping
    LIMIT/ORDER BY the executor adds.
    """
    a, b = _norm_sql(produced_sql), _norm_sql(golden_sql)
    if not a or not b:
        return False
    return a == b or b in a or a in b


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class AgentClientV3(ev2.AgentClientV2):
    """v2 client + view-authoring and golden-query helpers."""

    # --- views ----------------------------------------------------------- #
    def active_views(self, project_id: str) -> list[dict[str, Any]]:
        return views_from_files(self.list_mdl_files(project_id), only_active=True)

    def author_views(
        self,
        project_id: str,
        document_text: str,
        *,
        message: str = VIEW_AUTHOR_MESSAGE,
        upload: bool = True,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Drive a Copilot turn that authors views from a reusable-pattern doc.

        Returns the :meth:`copilot_build` result plus ``proposed_views`` (parsed
        from the raw changeset, so authoring is measured even if a later activation
        step trips) and ``active_views`` (post-activation ground truth).
        """
        if upload:
            self.create_document_from_text(
                project_id, document_text, "views_addendum.md"
            )
        result = self.copilot_build(
            project_id, message, glossary=document_text, max_steps=max_steps
        )
        result["proposed_views"] = views_from_changeset(
            (result.get("changeset") or {}).get("items", [])
        )
        result["active_views"] = self.active_views(project_id)
        return result

    # --- golden queries -------------------------------------------------- #
    def promote_golden_query(
        self,
        project_id: str,
        question: str,
        semantic_sql: str,
        *,
        name: str | None = None,
        use_as_onboarding: bool = False,
        usage_guidance: str | None = None,
    ) -> dict[str, Any]:
        """Promote a verified pair into the project's ``queries.json`` (draft)."""
        body: dict[str, Any] = {
            "question": question,
            "semantic_sql": semantic_sql,
            "use_as_onboarding": use_as_onboarding,
        }
        if name:
            body["name"] = name
        if usage_guidance:
            body["usage_guidance"] = usage_guidance
        return self._ok(
            self._agent(
                "POST",
                f"/agent/semantic-layer/projects/{project_id}/golden-queries/promote",
                json=body,
            ),
            "POST golden-queries/promote",
        )

    def golden_queries(self, project_id: str) -> list[dict[str, Any]]:
        """All golden-query entries in the project's ``queries.json`` (any status)."""
        f = find_golden_file(self.list_mdl_files(project_id))
        return parse_golden_queries(f.get("content", "")) if f else []

    def golden_file_status(self, project_id: str) -> str | None:
        f = find_golden_file(self.list_mdl_files(project_id))
        return f.get("status") if f else None

    def activate_golden(self, project_id: str) -> str | None:
        """Activate just the ``queries.json`` file (recall reads the *active* file).

        Targets the golden file by id so models are not re-validated; returns the
        post-activation status.
        """
        f = find_golden_file(self.list_mdl_files(project_id))
        if not f:
            return None
        if f.get("status") != "active":
            self.update_mdl_file(project_id, f["id"], status="active")
        return self.golden_file_status(project_id)

    def query_signal(
        self, question: str, *, extra_context: str | None = None
    ) -> dict[str, Any]:
        """Run a query and capture the SQL + recall signal for golden attribution."""
        resp = self.query(question, execute=True, extra_context=extra_context)
        er = resp.get("execution_result") or {}
        return {
            "sql": resp.get("sql"),
            "rows": er.get("rows", []),
            "answer_summary": resp.get("answer_summary"),
            "recalled_examples": recalled_example_count(resp),
            "matched_models": (resp.get("wren_context") or {}).get(
                "matched_models", []
            ),
            "status": resp.get("status"),
        }
