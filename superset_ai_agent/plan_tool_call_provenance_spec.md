<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Feature Spec: Tool-Call-Level MDL Provenance

**Status:** IMPLEMENTED (P1–P3), tested, in the working tree (2026-06-28). See §12 "As-built" for what landed and where it deviated from the spec. Original status: SPEC — source-audited against the working tree on 2026-06-28.
**Scope:** `superset_ai_agent/semantic_layer/**` (FastAPI) + `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/**`.
**Builds on / supersedes the R-B6 deferral in:** `plan_onboarding_selection_and_provenance_impl.md` §B4 / "As-built status".
**Canonical reference for the existing feature:** `MDL_PROVENANCE_AND_COVERAGE.md`.
**Relates to:** `plan_mdl_lab_spec.md` §5.6 (multi-user attribution, agent-driven onboarding).

> Symbols are stable; line numbers drift (the MDL Lab work lands continuously). Grep the symbol, not the line.

---

## 0. Why this spec exists (the three asks)

The user asked to close one documented gap and add two capabilities, all in the same surface (the Provenance timeline):

1. **R-B6 — enrichment → source-document link.** The Copilot apply path writes `source_type="copilot"` with **no `source_document_id`**, so an applied enrichment is attributed only at the *changeset* level, never at the *file* level.
2. **Tool-call-level provenance (NEW).** Track the individual MDL-mutating **tool calls** an agent makes, not just the top-level changeset, because the toolset is about to grow. Capture them at fine grain, **aggregate and present summarily** — *"agent wrote to `<file 1>`, `<file 2>`, …"*. A motivating future tool is an **LLM-driven onboarding tool** the agent can call at **any point** in a conversation, so onboarding entries must no longer be assumed to sit at the start of history.
3. **Multi-user attribution (from `plan_mdl_lab_spec.md`).** Provenance must show *who* did *what* once a project is shared by DB access, not a blanket "You".

**Policy held constant:** reset still wipes the provenance timeline (`PROVENANCE_EVENT_TYPES` purge on reset). This spec must not let any new event type escape that purge.

---

## 1. As-built audit (claims checked against code, not docs)

The user asked to verify that the relevant code is implemented as the planning docs claim. It mostly is — but the **R-B6 framing in `plan_onboarding_selection_and_provenance_impl.md` is stale**, because a later follow-up (`MDL_PROVENANCE_AND_COVERAGE.md`, Phase 3) moved the goalposts. Reconciliation:

| Claim (source doc) | Code reality | Verdict |
|---|---|---|
| "Apply writes `source_type="copilot"`, no `source_document_id`" (impl §B4, R-B6) | `apply_changeset_items` hardcodes `source_type="copilot"` on create; never sets `source_document_id`; `update` sets neither ([copilot/service.py](superset_ai_agent/semantic_layer/copilot/service.py) `apply_changeset_items`). | ✅ **True** — still open. |
| "Enrichment shows as a **generic `mdl_created`/`mdl_updated`** entry" (impl R-B6) | **Outdated.** The follow-up added `_emit_agent_apply_provenance` → emits **`document_enriched`** (kind `enrichment`) when the turn referenced documents, else **`mdl_agent_edit`** (kind `copilot_edit`) ([app.py](superset_ai_agent/app.py) apply route; `apply_provenance_payload` in service.py). Enrichment **is** classified and carries document chips at the **changeset** level via `Changeset.referenced_document_ids`. | ⚠️ **Stale claim.** Residual is **per-file**, not "no enrichment entry". |
| MDL file model has `source_type` **and** `source_document_id` columns | Both exist: `source_type String(64)`, `source_document_id String(36)` ([persistence/models.py](superset_ai_agent/persistence/models.py) `AiAgentSemanticMdlFile`). The `source_document_id` column is **never populated by the agent path**. | ✅ Columns ready; write path is the gap. |
| `ChangesetItem` could carry `source_document_id` with "a small contract add" | `ChangesetItem` fields = `op, path, file_id, current_content, proposed_content, validation, summary` — **no** `source_document_id`, **no** `source_type` ([copilot/schemas.py](superset_ai_agent/semantic_layer/copilot/schemas.py)). | ✅ Contract add still required. |
| Reset purges provenance by `PROVENANCE_EVENT_TYPES` | `reset_semantic_project` → `delete_project_events(..., types=PROVENANCE_EVENT_TYPES)`; documents preserved ([app.py](superset_ai_agent/app.py) `reset_semantic_project`). | ✅ True. |
| Multi-user attribution ("track users") is **unbuilt** (implied by mdl_lab spec being a future ask) | **Already implemented.** `ProvenanceEntry` carries `actor`, `actor_name`, `actor_type`, `is_self`; `actor_name` is **captured at write time** (`identity.username or identity.email`, app.py); `is_self` is **computed at read time** (`entry.actor == identity.owner_id`, `get_project_provenance`); `list_project_events` **ignores `owner_id`** (project-scoped); the dialog renders "You" only for self, else the teammate's name ([MdlProvenanceDialog.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/MdlProvenanceDialog.tsx)). | ⚠️ **Largely done** — only residual gaps (below). |
| Agent can onboard tables today | **True** — `propose_onboard_table` / `propose_onboard_tables` / `propose_relationships` already exist as toolset methods ([copilot/tools.py](superset_ai_agent/semantic_layer/copilot/tools.py) `MdlToolset`). They stage into the working set and surface as a generic agent edit, **not** as a distinguishable "onboard" in provenance. | ✅ Tools exist; provenance does not distinguish them. |
| Toolset accumulates per-tool-call records | **False — this is the core new gap.** The toolset keeps only end-state (`_working` dict, `_summaries`, `_referenced_document_ids`); `build_changeset()` **diffs working-vs-original** at turn end. The *identity of each tool call* (which tool, with what intent, touching which files) is **lost**. | ❌ **The capture seam to build.** |

**Net residual after the audit — three concrete gaps:**
- **G1 (R-B6, per-file):** no `source_document_id` on the changeset item or persisted file; enrichment links exist only at changeset granularity.
- **G2 (tool-call capture):** no per-tool-call ledger; provenance can't say "the agent *onboarded* 3 tables then *wrote* 4 files" — only "the agent edited 7 paths".
- **G3 (multi-user residual):** onboarding-job events stamp `actor` but **not** `actor_name`, so a teammate sees a bare owner id for onboarding rows. Everything else in attribution is done.

---

## 2. Goals / non-goals

**Goals**
- **F1 — Per-tool-call capture.** Record each MDL-mutating tool call (verb, affected paths, grounding docs, arg *shape*, status) during an agent turn; persist the ledger inside the existing apply event.
- **F2 — Summary-first presentation.** Render one timeline row per apply, with a per-verb rollup ("Onboarded 3 tables · Wrote 4 files · Added 2 relationships") that expands to the per-file / per-doc detail. (The literal ask: *"agent wrote to file1, file2, …"*.)
- **F3 — Onboard-anywhere.** Agent-driven onboarding is a tool-call verb that can appear at **any** position in the timeline; nothing assumes onboarding is first.
- **F4 — Close R-B6.** `ChangesetItem.source_document_id` contract add → persisted file carries `source_document_id` + `source_type="enriched_markdown"` → per-file enrichment attribution.
- **F5 — Finish multi-user attribution.** Stamp `actor_name` on the remaining emit path (onboarding job) so every row attributes correctly under sharing.

**Non-goals (this iteration)**
- **One timeline row per tool call.** Rejected (DP1) — keep one row per *apply*; tool calls are detail *within* the row. Matches the existing "one event per changeset" decision and avoids event-table explosion.
- **Version control / diff-restore of MDL.** Provenance stays an **audit log**, not VCS (unchanged non-goal from the reference doc).
- **Capturing read-only tool calls** (`list_*`, `read_mdl_file`, `validate_*`) in the timeline. Only **mutating** calls + the document-grounding signal are provenance-relevant (matches "`list_documents` is deliberately not an enrichment signal").
- **Raw tool-argument payloads** in the event. We store **arg *shapes*/summaries** (OTel-aligned, see §3), never raw MDL JSON (it is already the file content).
- Re-designing coverage, the SSE stream, the event table, or the reset contract.

---

## 3. Industry patterns informing the design

| Pattern / source | What it prescribes | How we apply it |
|---|---|---|
| **OpenTelemetry GenAI semantic conventions** — `invoke_agent` span with child `execute_tool {name}` spans; tool attributes under `gen_ai.tool.*` (name, call.id, type); **content captured in span *events*, not attributes**, and **opt-in** because args can be sensitive. | Tool calls are first-class, named, and structured; **fine content lives nested inside the coarse unit, not as sibling rows**; default to metadata over raw content. | Our `ToolCallRecord` mirrors an `execute_tool` span (tool name + verb + affected paths + status); the ledger is nested in the **one** apply event (the `invoke_agent`-equivalent unit), not exploded into N timeline rows; we store **arg summaries**, not raw JSON. |
| **"Minimum viable AI-agent audit trail"** — six event types: *tool invocation, model invocation, data access, policy decision, identity assertion, error*; record **parameter *shapes* classified by sensitivity, not raw values**; record **what + why**. | Log the *shape* of what a tool did and *why* (grounding), enough to baseline/detect misuse without retaining payloads. | `ToolCallRecord.action` (verb = what), `source_document_ids` (why/grounding), `args_summary` (shape), `status`+`detail` (error). Identity = existing `actor`/`actor_name`. |
| **Activity-feed aggregation** ("Alex and 35 others wrote on John's wall"; roll-up notifications; Aggregator pattern) — group by *(actor, verb)*, collapse to a summary with a count and an expand. | Don't show N near-identical rows; show one summarised row with a count, expandable to members. | The per-verb rollup ("Wrote 7 files", "Onboarded 3 tables") **is** the aggregator output; the expand reveals the member files/docs. Re-uses the project's existing read-time `coalesce_user_runs` philosophy (collapse at projection, keep the log append-only). |
| **Semantic-layer governance (dbt / Cube Cloud / Looker)** — change history & lineage are *governance* surfaces; "from semantic definition to query result, auditable", with **who/when/what** on each model change. | The audience for this timeline is governance: a reviewer wants who-changed-which-model-and-from-which-source. | Per-file rows tie a model file to its **source document** (R-B6) and its **actor** (multi-user) — the lineage triple *(model, source, author)* the governance tools expose. |

**Design takeaways:** (1) capture fine, present coarse; (2) verbs are named at dispatch, not reverse-engineered from a diff; (3) store shapes + grounding, not raw payloads; (4) aggregation is a read-time projection over an append-only log.

---

## 4. Design

### 4.1 Capture layer — a tool-call ledger on the toolset (F1, F3)

`MdlToolset` already accumulates turn state (`_working`, `_summaries`, `_referenced_document_ids`). Add a parallel **ledger**:

```python
ToolActionKind = Literal["write", "delete", "onboard", "relate"]  # extensible verb set

class ToolCallRecord(BaseModel):
    tool: str                              # "write_mdl_file" | "propose_onboard_tables" | ...
    action: ToolActionKind                 # semantic verb (named at dispatch, not diffed)
    paths: list[str] = Field(default_factory=list)            # files THIS call touched
    source_document_ids: list[str] = Field(default_factory=list)  # docs grounding THIS call (R-B6)
    args_summary: dict[str, Any] = Field(default_factory=dict)    # SHAPE only: table names, counts
    status: Literal["ok", "error"] = "ok"
    detail: str | None = None              # onboarded table names / rejection reason
```

- Each **mutating** tool (`write_mdl_file`, `delete_mdl_file`, `propose_onboard_table`, `propose_onboard_tables`, `propose_relationships`, and **any future tool**) appends one record on dispatch. Read-only tools do not.
- `args_summary` is **sensitivity-aware** (OTel/audit pattern): table names, paths, counts, join types — **never** the raw MDL JSON (it is already the file content/diff).
- At turn end, `build_changeset()` stamps `Changeset.tool_calls: list[ToolCallRecord]`. The existing working-vs-original **diff stays the source of truth for `items`/applied paths**; the ledger *annotates* it with verb + grounding (resolves R1, §6).
- **Onboard-anywhere falls out for free:** because a verb is recorded wherever the tool fires, an onboard mid-conversation produces an `action="onboard"` record at that point — no positional assumption anywhere.

### 4.2 R-B6 — per-file source document (F4)

- **Contract add:** `ChangesetItem` gains `source_document_id: str | None = None` and `source_type: MdlFileSourceType | None = None`.
- **Stamp at generation:** when a write tool runs **immediately grounded** on a single document (the doc the model/relationship was derived from), the toolset stamps that item's `source_document_id` and `source_type="enriched_markdown"`. Grounded-on-many or grounded-on-none → leave `source_document_id=None` (changeset-level `referenced_document_ids` still carries the set). See **DP2** for the attribution rule.
- **Persist:** `apply_changeset_items` reads `item.source_document_id` / `item.source_type`, passes them to `MdlFileCreateRequest` (and the update path), so the **persisted file row** finally carries them. `actor_type_for` already maps `enriched_markdown → agent`, and the timeline already renders `document_enriched` with chips — so a per-file link now shows the **specific** source doc, not just the turn's doc set.

### 4.3 Provenance event detail (one event per apply)

`apply_provenance_payload` folds the ledger into the existing detail:

```python
detail = {
    "actor": owner_id, "actor_name": ..., "source_type": "copilot",
    "conversation_id": ...,
    "summary": label,
    "ops": {"create": n, "update": n, "delete": n},   # unchanged
    "paths": [...],                                     # unchanged (authoritative from applied files)
    "documents": [{id, filename}, ...],                # unchanged
    "tool_calls": [ToolCallRecord, ...],               # NEW
    "action_summary": {"onboard": 3, "write": 4, "relate": 2, "delete": 0},  # NEW, derived rollup
}
```

- **Still one `document_enriched` / `mdl_agent_edit` event per apply** (DP1). No new top-level `SemanticLayerEventType`, so the **`PROVENANCE_EVENT_TYPES` purge-on-reset contract is unchanged** — `tool_calls` ride *inside* an already-purged event (policy held, §0).
- `action_summary` is the pre-computed aggregator input for the UI rollup.

### 4.4 Presentation layer — summary-first, expandable (F2)

In `MdlProvenanceDialog.tsx`, an `enrichment`/`copilot_edit` entry with `detail.tool_calls` renders:

- **Headline rollup line** from `action_summary`: e.g. *"Onboarded 3 tables · Wrote 4 files · Added 2 relationships"*. Zero-count verbs omitted. (Aggregator pattern.)
- **Truncated member list** under it: *"`orders.json`, `customers.json`, `line_items.json` +4 more"* — the literal "agent wrote to file1, file2, …" ask. Click **+N more** / a caret to expand the full per-file list, each file showing its verb badge and (if present) its **source-document chip** (R-B6).
- Existing **document chips**, **"View conversation"**, **actor tag** are unchanged.
- **Back-compat:** entries without `detail.tool_calls` (history written before this lands) fall back to the current `paths`/`ops` rollup — no migration, no breakage (DP1, R6).

### 4.5 Multi-user attribution residual (F5, G3)

- Add `actor_name=identity.username or identity.email` to the onboarding-job emits (`onboarding_started/completed/failed` in `_start_onboarding_job`) — the one emit path that currently omits it. Everything else (`is_self`, project-scoped reads, dialog rendering) is already in place and unchanged.

---

## 5. Decision points

> Recommendations follow existing in-repo patterns and the §3 industry standards. Confirm the starred ones before build.

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **DP1 ★** | Capture granularity | (a) one event per **apply**, tool calls nested in `detail`; (b) one event per **tool call** | **(a)**. Matches the locked "one event per changeset" decision (reference §6), avoids event-table explosion + a coalescing redesign, and mirrors OTel's *nested* `execute_tool` spans + the aggregator pattern. (b) would multiply timeline rows and break reset-volume bounds. |
| **DP2 ★** | Per-file `source_document_id` attribution rule | (a) stamp only when a write is grounded on **exactly one** doc; (b) stamp the **first** referenced doc; (c) defer per-file, keep changeset-level only | **(a)**. Honest: a single-FK column can't represent "derived from 3 docs"; one-doc writes (the common enrichment case) get a precise link, multi/zero-doc writes keep `None` and rely on the existing changeset-level `documents` set. (b) risks mis-attribution; (c) leaves R-B6 open. |
| **DP3** | How agent onboarding surfaces | (a) a `ToolActionKind="onboard"` **verb** inside the existing `enrichment`/`copilot_edit` event; (b) a **new** top-level `agent_onboarding` provenance kind | **(a)**. `plan_mdl_lab_spec.md` §5.6.4 explicitly says *no new provenance taxonomy* for agent onboarding; the verb + rollup gives the distinguishable "Onboarded N tables" line without a new event type (keeps the reset frozenset and kind enum stable). |
| **DP4** | Tool-argument capture | (a) **shape/summary only** (table names, paths, counts, join types); (b) full raw args | **(a)**. OTel + audit-trail guidance: log parameter shapes, not values; raw MDL JSON is already the file content, so storing it again is bloat + duplication. |
| **DP5** | UI density | (a) collapsed rollup + expand-on-demand; (b) always-expanded per-file list | **(a)**. Keeps the timeline scannable (the whole point of "present summarily"); expand serves the governance/debug audience. |
| **DP6** | Ledger vs. diff as path source-of-truth | (a) applied-file set authoritative for `paths`; ledger annotates verb/grounding; (b) ledger authoritative | **(a)**. The diff already reconciles what *actually* persisted (a tool can stage then overwrite); the ledger can over- or under-count. Use applied files for `paths`, ledger for the verb/doc annotation (R1). |

---

## 6. Risks & mitigations

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| **R1** | **Ledger/diff drift** — a tool records a write that a later tool overwrites/reverts, so `tool_calls` paths ≠ applied paths. | Med | DP6: applied-file set is authoritative for `paths`/`ops`; `tool_calls` annotate verbs/grounding only. Reconcile member lists against applied paths at projection; drop ledger paths not in the applied set. |
| **R2** | **Dangling `source_document_id`** — the source doc is later deleted. | Low | Column stays **nullable, no hard FK** (best-effort, matches the existing swallow when resolving `documents` filenames); resolve filename at read, omit chip if missing. |
| **R3** | **Payload bloat** — a huge turn (many tool calls / paths) inflates the event JSON. | Low/Med | Cap `tool_calls` and per-record `paths` (e.g. 100), set a `truncated: true` flag the UI shows as "+N more (truncated)"; the existing 500-entry server cap + reset bound the table. |
| **R4** | **Onboarding mis-attribution under sharing** (G3). | Low | F5: stamp `actor_name` on onboarding emits; test a second user sees the onboarder's name, not a bare id. |
| **R5** | **Reset leak** — a future mutation gets a new event type omitted from `PROVENANCE_EVENT_TYPES` and survives reset. | Med | Tool calls add **no** new event type (DP3), so nothing escapes today. Add a guard test: every event type a mutating route/apply can emit ∈ `PROVENANCE_EVENT_TYPES`. |
| **R6** | **Back-compat** — pre-existing events lack `detail.tool_calls`. | Low | `detail.tool_calls` optional; UI falls back to the current `paths`/`ops` rollup. No migration (schemaless `payload`). |
| **R7** | **Sensitive data in `args_summary`** (e.g. a table/column name that is itself sensitive). | Low | DP4 caps capture to names/counts already visible in the schema the user can access (project is DB-bound; no cross-boundary widening). Document that `args_summary` is shape-only; no values/content. |
| **R8** | **Verb taxonomy churn** as the toolset grows (new tools don't map to the four verbs). | Low | `ToolActionKind` is a closed `Literal` *plus* a `tool` string; an unmapped tool defaults to `action="write"` and still shows its `tool` name in the expand. Extending the verb set is an additive enum change, no migration. |

---

## 7. Intent alignment (dev ↔ spec ↔ user/UI)

| Layer | Stated intent | Spec realization | Verification |
|---|---|---|---|
| **User (ask 2)** | "If an agent runs write on 7 files, just show *agent wrote to file1, file2, …*." | Per-verb rollup line + truncated member list + expand (§4.4). | RTL: an apply with 7 write tool-calls renders one row "Wrote 7 files" + "file1, file2, file3 +4 more"; expand lists all 7. |
| **User (ask 2, future)** | "An LLM onboard tool can be called at any point; onboarding entries may not be at the start." | Onboard is a `ToolActionKind` verb recorded wherever it fires; no positional assumption (§4.1, DP3). | Unit: an onboard tool call in the middle of a turn produces an `action="onboard"` record; timeline shows "Onboarded N tables" at that apply's position. |
| **Dev (R-B6)** | "Close the enrichment→source-document link via a small `ChangesetItem.source_document_id` add." | Contract add + apply persists `source_document_id`/`source_type` on the file (§4.2). | Unit: a doc-grounded enrichment apply writes a file row with `source_document_id` set + `source_type="enriched_markdown"`; UI shows the per-file source chip. |
| **Dev (capture model)** | "Track tool calls, but the toolset just diffs end-state today." | Add a ledger parallel to `_working`; verbs named at dispatch; diff stays authoritative for paths (§4.1, DP6). | Unit: `build_changeset()` stamps `tool_calls`; paths reconcile to applied files. |
| **User (mdl_lab)** | "Provenance tracks users." | Already built; finish the onboarding `actor_name` gap (§4.5, F5). | Two-user test: each row shows "You" for self, the teammate's name otherwise, including onboarding rows. |
| **Policy** | "Reset still resets provenance." | No new event type; tool calls ride inside purged events (§4.3, R5). | Test: after reset, the timeline (incl. tool-call detail) is empty; document events survive. |

---

## 8. Phasing (each ends green per `CLAUDE.md`)

1. **P1 — Backend capture + R-B6 (foundation).** `ToolCallRecord` + `ToolActionKind`; toolset ledger appended by each mutating tool; `Changeset.tool_calls`; `ChangesetItem.source_document_id`/`source_type`; `apply_changeset_items` persists them; `apply_provenance_payload` folds `tool_calls` + `action_summary`. Reset-leak guard test (R5). *(Backend; no migration — schemaless `payload` + existing file columns.)*
2. **P2 — Multi-user residual.** `actor_name` on onboarding-job emits (F5/G3); two-user attribution test. *(Backend; small.)*
3. **P3 — Frontend aggregation UI.** `api.ts` types (`ToolCallRecord`, `action_summary`, `ProvenanceEntry.detail` shape); `MdlProvenanceDialog.tsx` rollup line + truncated member list + expand + per-file source chips; back-compat fallback. *(Frontend.)*

P1 is the only ordering constraint (P3 renders what P1 emits; P2 is independent and can land alongside either).

---

## 9. File touchpoints

> Symbols stable; re-grep lines. New types are isolation-safe; the hot files (`tools.py`, `service.py`, `schemas.py`, `app.py`, `api.ts`, `MdlProvenanceDialog.tsx`) take single-writer edits.

### P1 — capture + R-B6
| File:symbol | Change |
|---|---|
| `semantic_layer/copilot/schemas.py::ChangesetItem, Changeset, (new) ToolCallRecord, ToolActionKind` | Add `source_document_id`/`source_type` to `ChangesetItem`; add `tool_calls` to `Changeset`; define the record + verb enum. |
| `semantic_layer/copilot/tools.py::MdlToolset` (write_mdl_file, delete_mdl_file, propose_onboard_table(s), propose_relationships, build_changeset) | Append a `ToolCallRecord` per mutating dispatch; stamp per-item `source_document_id` when single-doc grounded; stamp `Changeset.tool_calls`. |
| `semantic_layer/copilot/service.py::apply_changeset_items, apply_provenance_payload` | Persist `source_document_id`/`source_type` on create/update; fold `tool_calls` + derived `action_summary` into `detail`. |
| `tests/unit_tests/superset_ai_agent/test_copilot_service.py`, `test_provenance_*.py` | Ledger stamping; R-B6 file attribution; reset-leak guard (R5); payload-cap (R3). |

### P2 — multi-user residual
| File:symbol | Change |
|---|---|
| `app.py::_start_onboarding_job` | Add `actor_name=identity.username or identity.email` to onboarding emits. |
| `tests/.../test_provenance_api.py` | Two-user onboarding attribution. |

### P3 — frontend
| File:symbol | Change |
|---|---|
| `AiAgentPanel/api.ts::ProvenanceEntry (detail typing), (new) ToolCallRecord, ToolActionKind` | Type the `tool_calls`/`action_summary`/per-file `source_document_id` detail shape. |
| `SemanticLayerEditor/MdlProvenanceDialog.tsx` | Per-verb rollup line, truncated member list + expand, per-file verb badge + source-document chip; back-compat fallback to `paths`/`ops`. |
| `SemanticLayerEditor/MdlProvenanceDialog.test.tsx` | Rollup, truncation/expand, per-file source chip, fallback for legacy entries. |

---

## 10. Open questions for the user

- **DP2 attribution strictness:** is "stamp `source_document_id` only when a write is grounded on exactly one document" acceptable, or do you want a richer many-docs-per-file link (which needs a join table, not the single column R-B6 proposed)?
- **Expand affordance (DP5):** inline expand within the row (recommended) vs. a drill-in panel like the coverage "View report"?
- **Verb set:** are `write / delete / onboard / relate` the right initial verbs, or do you already know the next tools (e.g. `annotate`, `tag`, `deprecate`) so we seed the enum now?

---

## 12. As-built (2026-06-28)

All three phases landed and are tested. Backend `tests/unit_tests/superset_ai_agent/` **926 passed, 11 skipped**; the provenance dialog suite **10 passed**; ruff + prettier clean on changed files; no new mypy errors (the one app.py protocol-variance note is pre-existing at HEAD). Two **pre-existing, unrelated** FE failures remain (`ExplainDialog.test.tsx`, `AiAgentPanel/index.test.tsx` — multi-schema `SELECT … FROM sales.orders` SQL rendering in the SQL agent, not provenance).

**What landed**
- **P1 capture + R-B6.** `ToolActionKind` + `ToolCallRecord` ([copilot/schemas.py]); a ledger on `MdlToolset` (`_tool_calls`, `_grounding_watermark`, `_file_grounding`) appended for every mutating dispatch via `_record_mutation` + `_summarize_mutation` ([copilot/tools.py]); `Changeset.tool_calls` + per-item `ChangesetItem.source_document_id`/`source_type` stamped in `build_changeset`. `apply_changeset_items` persists `source_document_id` + `source_type="enriched_markdown"` (doc-grounded) else `copilot`; `MdlFileUpdateRequest` gained the two fields and both stores apply them. `apply_provenance_payload` folds `tool_calls` + derived `action_summary` (rollup over the *full* ledger) with a `TOOL_CALL_DETAIL_CAP=100` member cap ([copilot/service.py]).
- **P2 attribution.** `_append_semantic_event` gained an `actor_name` kwarg (merged into detail). Stamped on the **onboarding** emits (started/completed/failed) **and** — a broader gap than the spec flagged — on the **Copilot apply** path (`_emit_agent_apply_provenance`), which previously had no `actor_name` either. Verified by a signed-header two-user API test (Alice onboards → Bob sees "Alice", `is_self=False`).
- **P3 UI.** `api.ts` `ToolActionKind`/`ToolCallRecord` types; `MdlProvenanceDialog` renders a per-verb **rollup** line (`provenance-rollup`), a truncated **member-file** list (`provenance-file`, `MEMBER_PREVIEW=3`) with **+N more / Show less** toggle, and per-file **source-document chips** (`path ← spec.md`). Back-compat: entries without `tool_calls` fall back to `detail.paths`.

**Deviations from the written spec**
1. **Grounding heuristic (DP2) implemented as a "since-last-mutation" watermark.** `source_document_id` is attributed to a write iff exactly one document was searched *since the previous mutating call*. Consequence: a second consecutive write with no fresh search is left unlinked (tested in `test_grounding_watermark_attributes_only_the_first_write_after_a_search`). Honest under-attribution, never mis-attribution; the changeset-level `documents` set stays complete. This is a stricter, more deterministic reading of DP2(a) than the spec implied.
2. **`actor_name` scope widened** to the apply path (see P2) — the spec only named onboarding.
3. **No `MdlFileUpdateRequest` re-stamp for un-grounded agent edits** — an agent update without a source doc deliberately leaves the file's existing `source_type` untouched (so a hand-authored `manual` file isn't relabelled `copilot`); only doc-grounded updates re-stamp.

### Sources
- [OpenTelemetry — Semantic Conventions for GenAI agent & framework spans (`invoke_agent` / `execute_tool {name}`, `gen_ai.tool.*`, content-in-events, opt-in capture)](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- [OpenTelemetry blog — Inside the LLM Call: GenAI Observability (tool-call span tree, content stored as span events, not attributes)](https://opentelemetry.io/blog/2026/genai-observability/)
- [ARMO — What to Log for AI Agent Activity: the Minimum Viable Audit Trail (six event types; parameter *shapes* by sensitivity, not values; what + why)](https://www.armosec.io/blog/minimum-viable-audit-trail/)
- [GetStream — Aggregated Feeds Demystified (group by actor+verb, "Alex and 35 others", roll-up/aggregator pattern)](https://getstream.io/blog/aggregated-feeds-demystified/)
- [Enterprise Integration Patterns — Aggregator (roll-up notifications to reduce row noise)](https://bigkevmcd.github.io/patterns/events/aggregator/2019/08/11/aggregator-pattern-part-1.html)
- [Cube — semantic-layer governance: lineage, query history, audit as Cube Cloud features](https://cube.dev/articles/dbt-semantic-layer-alternatives-2026)
