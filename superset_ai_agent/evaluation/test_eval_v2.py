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
"""Offline unit tests for the v2 harness pure logic (no live server needed)."""

from __future__ import annotations

import json  # noqa: TID251 - standalone eval tooling, independent of Superset

import eval_common as ec
import eval_v2 as v2


# --- SSE parsing (E8) ------------------------------------------------------- #
def _sse(*events):
    out = []
    for ev in events:
        out.append(f"event: {ev['type']}")
        out.append("data: " + json.dumps(ev))
        out.append("")
    return out


def test_parse_sse_stream_decodes_data_frames():
    lines = _sse(
        {"type": "progress", "agent_step": {"n": 1}},
        {"type": "complete", "changeset": {"items": [{"path": "models/x.json"}]}},
    )
    events = v2.parse_sse_stream(lines)
    assert [e["type"] for e in events] == ["progress", "complete"]


def test_parse_sse_stream_handles_bytes_and_skips_noise():
    lines = [
        b"event: progress",
        b"data: " + json.dumps({"type": "progress"}).encode(),
        b"",
        b": keep-alive comment",
        b"data: not-json",
        b"",
    ]
    events = v2.parse_sse_stream(lines)
    assert events == [{"type": "progress"}]


def test_changeset_and_error_extraction():
    ok = v2.parse_sse_stream(_sse({"type": "complete", "changeset": {"id": "c1"}}))
    assert v2.changeset_from_events(ok) == {"id": "c1"}
    assert v2.error_from_events(ok) is None

    bad = v2.parse_sse_stream(_sse({"type": "error", "detail": "boom"}))
    assert v2.changeset_from_events(bad) is None
    assert v2.error_from_events(bad) == "boom"


# --- active models from MDL files (E6) -------------------------------------- #
def _mdl(models, status="active"):
    content = json.dumps({"models": [{"name": m} for m in models]})
    return {"status": status, "content": content}


def test_active_models_unions_only_active_files():
    files = [
        _mdl(["seagate_sites", "seagate_work_orders"], "active"),
        _mdl(["seagate_drive_skus"], "draft"),  # ignored
        _mdl(["seagate_shipments"], "active"),
    ]
    assert v2.active_models_from_files(files) == {
        "seagate_sites",
        "seagate_work_orders",
        "seagate_shipments",
    }


# --- provenance aggregation (E6) -------------------------------------------- #
def test_provenance_kind_counts():
    entries = [
        {"kind": "onboarding"},
        {"kind": "enrichment"},
        {"kind": "enrichment"},
        {"kind": "coverage"},
    ]
    assert v2.provenance_kind_counts(entries) == {
        "onboarding": 1,
        "enrichment": 2,
        "coverage": 1,
    }


# --- SQL distractor leakage (E9) -------------------------------------------- #
def test_sql_references_tables_detects_distractor_touch():
    distractors = ["seagate_finance_ledger", "seagate_iot_sensor_logs"]
    clean = "SELECT SUM(units_completed) FROM seagate_ops.seagate_production_events"
    leak = "SELECT units FROM seagate_ops.seagate_finance_ledger"
    assert v2.sql_references_tables(clean, distractors) == []
    assert v2.sql_references_tables(leak, distractors) == ["seagate_finance_ledger"]
    assert v2.sql_references_tables(None, distractors) == []


# --- table-selection metrics (E9) ------------------------------------------- #
RELEVANT = ["seagate_sites", "seagate_production_lines", "seagate_work_orders"]
DISTRACTORS = ["seagate_finance_ledger", "seagate_iot_sensor_logs"]


def test_perfect_selection_scores_one():
    m = v2.table_selection_metrics(RELEVANT, RELEVANT, DISTRACTORS)
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0
    assert m["distractor_inclusions"] == []
    assert m["missed_relevant"] == []


def test_distractor_inclusion_lowers_precision():
    selected = RELEVANT + ["seagate_finance_ledger"]
    m = v2.table_selection_metrics(selected, RELEVANT, DISTRACTORS)
    assert m["recall"] == 1.0
    assert m["precision"] == 0.75  # 3 of 4 known-selected are relevant
    assert m["distractor_inclusions"] == ["seagate_finance_ledger"]
    assert m["distractor_inclusion_rate"] == 0.5  # 1 of 2 distractors


def test_missed_relevant_lowers_recall():
    selected = ["seagate_sites"]
    m = v2.table_selection_metrics(selected, RELEVANT, DISTRACTORS)
    assert m["precision"] == 1.0
    assert round(m["recall"], 3) == 0.333
    assert set(m["missed_relevant"]) == {
        "seagate_production_lines",
        "seagate_work_orders",
    }


def test_unknown_names_ignored_for_precision():
    # A renamed model not in R or D should not be counted against precision.
    selected = RELEVANT + ["some_view_model"]
    m = v2.table_selection_metrics(selected, RELEVANT, DISTRACTORS)
    assert m["precision"] == 1.0


# --- manifest loader -------------------------------------------------------- #
def test_load_table_manifest_round_trips():
    manifest = v2.load_table_manifest("seagate_multi")
    assert set(manifest["relevant_tables"])
    assert set(manifest["distractor_tables"])
    assert manifest["database_name"] == "examples"


def test_postgres_backend_constant():
    assert "postgresql" in v2.POSTGRES_BACKENDS
    assert "sqlite" not in v2.POSTGRES_BACKENDS


# --- copilot attachment contract (E8) --------------------------------------- #
def test_text_attachment_matches_message_attachment_shape():
    att = v2.text_attachment("bi_glossary.md", "patty = drive unit")
    # Fields the server's MessageAttachment expects.
    assert att == {
        "filename": "bi_glossary.md",
        "content_type": "text/markdown",
        "text": "patty = drive unit",
        "truncated": False,
    }


def test_text_attachment_allows_content_type_override():
    att = v2.text_attachment("notes.txt", "x", content_type="text/plain")
    assert att["content_type"] == "text/plain"


def test_text_attachment_truncates_to_ui_ceiling():
    big = "x" * (v2.MAX_ATTACHMENT_CHARS + 50)
    att = v2.text_attachment("big.md", big)
    assert len(att["text"]) == v2.MAX_ATTACHMENT_CHARS
    assert att["truncated"] is True
    small = v2.text_attachment("small.md", "x")
    assert small["truncated"] is False


# --- auto-onboard / Copilot build messages (E11/E12) ------------------------ #
def test_auto_onboard_message_matches_production_wording():
    # Must stay in lockstep with index.tsx AUTO_ONBOARD_MESSAGE.
    msg = v2.AUTO_ONBOARD_MESSAGE
    assert "onboard the tables they describe" in msg
    assert "enrich the models" in msg
    assert "one\n        changeset" not in msg  # single line, no stray wrapping
    assert msg.endswith("Show me one changeset to review.")


def test_copilot_enrich_message_targets_refinement():
    msg = v2.COPILOT_ENRICH_MESSAGE
    assert "improve them" in msg
    assert "Golden Yield" in msg


# --- copilot_build orchestration (offline, faked HTTP) ---------------------- #
class _FakeV2(v2.AgentClientV2):
    """AgentClientV2 with the HTTP-touching methods stubbed, to test orchestration."""

    def __init__(self, turn_result, *, activate_exc=None):
        super().__init__(ec.EvalConfig())
        self._turn_result = turn_result
        self._activate_exc = activate_exc
        self.calls = []

    def copilot_turn(self, project_id, message, *, attachments=None, **kw):
        self.calls.append(("turn", message, attachments))
        return self._turn_result

    def copilot_apply(self, project_id, items, **kw):
        self.calls.append(("apply", len(items)))
        return []

    def activate_all(self, project_id):
        self.calls.append(("activate",))
        if self._activate_exc:
            raise self._activate_exc
        return 1

    def create_document_from_text(self, project_id, text, filename, **kw):
        self.calls.append(("upload", filename))
        return {"id": "doc1"}

    def wait_for_coverage(self, project_id, **kw):
        return 0.5

    def active_model_names(self, project_id):
        return {"m1"}

    def provenance(self, project_id):
        return [{"kind": "enrichment"}]


def test_copilot_build_happy_path_applies_and_activates():
    turn = {
        "changeset": {"items": [{"path": "models/x.json"}]},
        "error": None,
        "events": [],
    }
    client = _FakeV2(turn)
    out = client.copilot_build("p", "msg", glossary="g")
    assert out["items"] == 1
    assert out["applied"] is True
    assert out["activated"] is True
    assert out["activate_error"] is None
    assert ("apply", 1) in client.calls
    assert ("activate",) in client.calls
    # glossary was passed as an attachment, not embedded in the message.
    _, msg, attachments = client.calls[0]
    assert msg == "msg"
    assert attachments
    assert attachments[0]["filename"] == "bi_glossary.md"


def test_copilot_build_turn_error_skips_apply():
    client = _FakeV2({"changeset": None, "error": "boom", "events": []})
    out = client.copilot_build("p", "msg")
    assert out["items"] == 0
    assert out["applied"] is False
    assert out["activated"] is False
    assert all(c[0] != "apply" for c in client.calls)


def test_copilot_build_captures_activate_422():
    turn = {
        "changeset": {"items": [{"path": "rel/x.json"}]},
        "error": None,
        "events": [],
    }
    client = _FakeV2(turn, activate_exc=ec.AgentError("422 invalid manifest"))
    out = client.copilot_build("p", "msg")
    assert out["applied"] is True
    assert out["activated"] is False
    assert "422" in out["activate_error"]


def test_copilot_build_no_items_does_not_apply():
    client = _FakeV2({"changeset": {"items": []}, "error": None, "events": []})
    out = client.copilot_build("p", "msg")
    assert out["items"] == 0
    assert out["applied"] is False


def test_auto_onboard_uploads_then_builds_with_production_message():
    turn = {
        "changeset": {"items": [{"path": "models/x.json"}]},
        "error": None,
        "events": [],
    }
    client = _FakeV2(turn)
    client.auto_onboard("p", "GLOSSARY TEXT")
    assert ("upload", "bi_glossary.md") in client.calls
    turn_calls = [c for c in client.calls if c[0] == "turn"]
    assert turn_calls[0][1] == v2.AUTO_ONBOARD_MESSAGE


# --- relationships-only activation (native after empty_root fix) ------------- #
def _model_item(name, op="update"):
    content = json.dumps({"models": [{"name": name, "columns": []}]})
    return {"op": op, "path": f"models/{name}.json", "content": content}


def _rel_item(a, b):
    content = json.dumps({"relationships": [{"name": f"{a}_{b}", "models": [a, b]}]})
    return {"op": "create", "path": f"relationships/{a}_{b}.json", "content": content}


def test_models_from_changeset_reads_proposed_model_names():
    items = [
        _model_item("seagate_sites"),
        _model_item("seagate_work_orders"),
        _rel_item("seagate_sites", "seagate_work_orders"),
    ]
    assert v2.models_from_changeset(items) == {"seagate_sites", "seagate_work_orders"}


def test_copilot_build_applies_relationship_files_natively():
    # Relationships-only files are valid project fragments, so the build applies the
    # whole changeset as-is (no fold) and lets bulk-status validate/activate the
    # merged manifest. The result reports 0 relationships folded.
    turn = {
        "changeset": {"items": [_model_item("m1"), _rel_item("m1", "m2")]},
        "error": None,
        "events": [],
    }
    client = _FakeV2(turn)
    out = client.copilot_build("p", "msg")
    assert out["items"] == 2  # raw changeset size
    assert out["relationships_folded"] == 0
    assert out["proposed_models"] == ["m1"]
    # Every changeset item is applied unchanged, including the relationship file.
    applied_calls = [c for c in client.calls if c[0] == "apply"]
    assert applied_calls[0][1] == 2


def test_copilot_enrich_pass_adds_coverage_and_models():
    turn = {
        "changeset": {"items": [{"path": "models/x.json"}]},
        "error": None,
        "events": [],
    }
    client = _FakeV2(turn)
    out = client.copilot_enrich_pass("p", "g")
    assert out["coverage"] == 0.5
    assert out["active_models"] == ["m1"]
    assert out["provenance_kinds"] == {"enrichment": 1}
