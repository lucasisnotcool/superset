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

# Follow-up Implementation Plan & Checklist: Provenance & Coverage Gap Closure (Phase 3)

**Companion to:** `plan_provenance_and_coverage_spec.md` (rationale) and
`plan_provenance_and_coverage_impl.md` (Phases 1–2, *delivered* 2026-06-28 — see its
"Implementation status" + "Remaining risks & gaps" sections, which this plan closes).
**Scope:** `superset_ai_agent/` (FastAPI) + `superset-frontend/src/SqlLab/components/AiAgentPanel/`.
**Audience:** future agent sessions — resumable, ordered checklist. Tick `[x]` as you go; do not reorder. Each task lists **entrypoints/touchpoints (file:line)**, **requirements**, **acceptance**, **depends-on**.

> Line numbers are anchors captured 2026-06-28 against `master` *after* the Phase 1–2 work landed in the working tree. Symbols are stable; re-grep if a line drifted.

---

## How to use this checklist

- Work top-to-bottom within a gap; gaps **G1–G6 are mutually independent** and can be done in any order or in parallel branches.
- After each task: run the named tests; run `pre-commit run ruff --files <changed>` and `pre-commit run mypy --files <changed>` (the **enforced** config — bare `ruff`/`eslint` are noisy/broken in this checkout, see G6).
- Keep all provenance/coverage emission **best-effort** (try/except, log-and-continue) — never fail a write because telemetry failed (mirror `app.py::_emit_mdl_provenance` and `_emit_agent_apply_provenance:1060`).
- Every new Python file needs the ASF license header; new TS uses the `/** ASF */` header (copy an existing sibling).

---

## Gap → task map (from the Phase 1–2 risk log)

| Gap (impl doc) | This plan | Priority | Ships value |
| --- | --- | --- | --- |
| R6 enrichment attribution incomplete | **G1** | High | enrichment provenance shows the docs actually used |
| Findings not tagged per document | **G2** | High | directory coverage report is drillable by document |
| Badge polls; dialog not live | **G3** | Medium | instant coverage status via existing SSE |
| `refreshSignal` over-fires | **G4** | Low | fewer redundant status calls |
| Stage-granular cancel; union overreach | **G5** | n/a | **accept** (documented non-goals) |
| ESLint env broken; models.py mypy noise | **G6** | Low | unblocks frontend lint gate |

---

## G1 — Complete enrichment document attribution

**Why:** Today only documents the agent pulled via `search_documents` are recorded
(`tools.py::_search_documents:385` → `Changeset.referenced_document_ids`,
`copilot/schemas.py:93`). Two real sources are missed: (a) inline message
**attachments** (flattened to text by `app.py::_attachments_text:1748`, so they carry
no id and never reach apply), and (b) documents surfaced by `list_documents`
(`tools.py::_list_documents:344`). Result: an enrichment driven by an attachment is
mis-classified `copilot_edit` with no document chips.

> **Decision D-FU-1 — what counts as "used"?** Options: (a) only `search_documents`
> (today); (b) + attachments; (c) + `list_documents` enumeration. `list_documents`
> returns *every* doc, so counting it would mark almost every turn "enrichment" —
> too noisy. **Recommendation: (b)** — add attachments, keep `list_documents` out.
> Label the chips "documents referenced" (not "used"), matching the spec's R6 framing.

- [ ] **G1.1 Carry attachment filenames onto the changeset.**
  - **Touchpoints:** `copilot/schemas.py` — add `referenced_attachments: list[str] = Field(default_factory=list)` to `Changeset` (beside `referenced_document_ids:93`). The Copilot turn/stream handlers already have `request.attachments` (`app.py:2239/2295/2326`); after `run_copilot(...)` returns the changeset and **before** it is persisted as an artifact, stamp `changeset.referenced_attachments = [a.filename for a in request.attachments]`. Do this in both the non-stream turn and the stream `commit` path.
  - **Requirement:** attachments are ephemeral (no id) — store filename only.
  - **Acceptance:** unit test — a turn with one attachment yields a changeset whose `referenced_attachments == ["<name>"]`.
  - **Depends-on:** none.
- [ ] **G1.2 Fold attachments into the apply provenance payload.**
  - **Touchpoints:** `app.py::_emit_agent_apply_provenance:1060` already reads the changeset via `changeset_from_conversation` and resolves `referenced_document_ids` → `{id, filename}`. Extend it to append `{id: None, filename: a}` for each `changeset.referenced_attachments`. Classification (`service.py::apply_provenance_payload`) must treat **either** docs **or** attachments as the enrichment signal — change `is_enrichment = bool(documents)` to also count attachment entries (entries with `id is None`).
  - **Requirement:** keep the `documents` detail shape `{id, filename}` so the UI chips (`MdlProvenanceDialog.tsx::documentRefs`) render unchanged.
  - **Acceptance:** API test — apply after a turn that *only* attached a file → one `enrichment` (not `copilot_edit`) entry whose `detail.documents` includes the attachment filename with `id: null`.
  - **Depends-on:** G1.1.

---

## G2 — Tag coverage findings with their source document

**Why:** `run_directory_coverage` (`coverage.py:685`) unions claims from all documents
and judges them together, then `aggregate_report` (`coverage.py:551`) flattens to a
single finding list. A reader cannot tell which document a missing claim came from —
a real gap for a multi-document project drilling into the report.

> **Decision D-FU-2 — where to attach the source?** The claim is the carrier. Options:
> (a) add `document_id`/`document_filename` to `CoverageClaim`; (b) keep a parallel
> map claim→doc through judging. The judge maps findings back to claims by index
> (`judge_coverage:326`), so tagging the *claim* and copying onto the finding is the
> least invasive. **Recommendation: (a)** — stamp the source on each claim at
> extraction time in the directory runner, then copy onto its finding.

- [ ] **G2.1 Add source fields to the finding (and optionally the claim).**
  - **Touchpoints:** `copilot/schemas.py::CoverageFinding:203` — add `document_id: str | None = None`, `document_filename: str = ""`. (Leave `CoverageClaim:184` unchanged; carry the source in a local list parallel to `all_claims` to avoid widening the LLM-facing claim contract.)
  - **Acceptance:** schema round-trips with the new optional fields defaulted.
  - **Depends-on:** none.
- [ ] **G2.2 Thread the source through the directory runner.**
  - **Touchpoints:** `coverage.py::run_directory_coverage:685` — when extending `all_claims`, build a parallel `sources: list[CoverageDocument]` aligned by index. After `judge_coverage` returns `findings` (same order/length as claims — verify against `judge_coverage`'s `_all_missing` and degrade paths, which preserve order), copy `document_id`/`document_filename` from `sources[i]` onto `findings[i]` before `aggregate_report`.
  - **Risk:** if a degrade path ever returns a different-length finding list, the zip mis-aligns. **Mitigation:** guard with `if len(findings) == len(all_claims)` before tagging; else skip tagging (counts still correct).
  - **Acceptance:** unit test — 2 docs, 1 claim each; the two findings carry the correct `document_filename`.
  - **Depends-on:** G2.1.
- [ ] **G2.3 Surface the document in the report UI.**
  - **Touchpoints:** `superset-frontend/.../api.ts::CoverageFinding` (mirror the two new fields); `CoverageReportModal.tsx::CoverageReportBody:46` — render the finding's `document_filename` as a small tag/secondary line; optionally group findings by document.
  - **Acceptance:** Jest — a report with two documents shows both filenames against their findings.
  - **Depends-on:** G2.2.

---

## G3 — Live coverage status via the existing SSE stream (replace polling)

**Why:** `CoverageBadge` polls `coverage/status` every 4s while `analysing`
(`CoverageBadge.tsx:24/64`); the open provenance dialog never refreshes. There is
already a project event stream — `app.py::get_project_semantic_layer_events:3451`
(`/agent/semantic-layer/projects/{id}/events`, `text/event-stream`) and a client
factory `api.ts::createProjectSemanticLayerEventSource:1854`. `coverage_completed`
already flows through it (it is appended via `_append_semantic_event`). Subscribe and
refetch on that event instead of timer polling.

> **Decision D-FU-3 — SSE vs keep polling.** SSE reuses shipped infra and removes the
> up-to-4s lag, but adds a long-lived connection per open editor. **Recommendation:**
> subscribe via the existing factory; keep a **single low-frequency safety poll**
> (e.g. 30s) as a fallback for missed events / proxies that buffer SSE. Best of both.

- [ ] **G3.1 Badge subscribes to the project event stream.**
  - **Touchpoints:** `CoverageBadge.tsx` — on mount (per `projectId`), open `createProjectSemanticLayerEventSource(projectId)`; on a message whose `type` is `coverage_completed` (or any `mdl_*`/`onboarding_*` that changes the active set), call `poll()` once. Drop the 4s `setTimeout` chain; replace with a 30s fallback interval. Close the EventSource on unmount/projectId change.
  - **Acceptance:** Jest with the existing `MockEventSource` harness (see `api.test.ts:967`) — dispatching a `coverage_completed` message triggers a status refetch; the component unsubscribes on unmount.
  - **Depends-on:** none.
- [ ] **G3.2 (optional) Live-refresh the open provenance dialog.**
  - **Touchpoints:** `MdlProvenanceDialog.tsx` — while `open`, subscribe to the same stream and call `load()` on a provenance-relevant event. Keep it behind the existing `open` guard so a closed dialog holds no connection.
  - **Acceptance:** Jest — an event while open re-fetches the timeline.
  - **Depends-on:** G3.1 (shares the subscribe helper — factor a small `useProjectEvents(projectId, onEvent)` hook to avoid duplication).

---

## G4 — Make the badge re-fetch precise (active-set changes only)

**Why:** the badge re-fetches on `refreshSignal={mdlFiles}` (`index.tsx:797`), which
changes on *draft* edits too — those never schedule coverage, so the status is
unchanged. Harmless but chatty.

- [ ] **G4.1 Key the signal on the active set.**
  - **Touchpoints:** `index.tsx:797` — pass a derived value that only changes when the active set changes, e.g. `refreshSignal={mdlFiles.filter(f => f.status === 'active').map(f => f.id + f.checksum).join(',')}` (mirrors the backend `_active_mdl_checksum` notion). Once **G3** lands, `refreshSignal` becomes a secondary trigger and this is mostly cosmetic.
  - **Acceptance:** Jest — toggling a draft file does not refetch status; activating one does.
  - **Depends-on:** none (do after G3 to avoid rework).

---

## G5 — Accepted non-goals (record the decision; no code)

- [ ] **G5.1 Stage-granular cancellation is intended.** A superseded run finishes its
  current in-flight LLM call before yielding at the next `should_cancel` check
  (`coverage.py::_raise_if_cancelled`). Killable workers are out of scope; the cost
  ceiling is one extra model call per superseded run. **No action** — leave the note.
- [ ] **G5.2 Union-level overreach is intended.** `include_overreach` flags MDL facts
  unsupported by *any* document; per-document overreach is not meaningful for an
  advisory signal. **No action.** Revisit only if users ask "unsupported except by X".

---

## G6 — Tooling/infra (unblock the gates)

- [ ] **G6.1 Fix the frontend ESLint invocation.** This checkout ships a legacy
  `.eslintrc` but the installed ESLint is v10 (flat-config only), so lint could not be
  run during Phase 1–2. **Touchpoints:** confirm what CI actually runs
  (`superset-frontend/package.json` lint script + `.pre-commit-config.yaml`); if CI
  pins an older ESLint, run lint via that path, else migrate config / set
  `ESLINT_USE_FLAT_CONFIG`. **Decision D-FU-4:** prefer matching CI's exact invocation
  over changing config. **Acceptance:** `eslint` runs clean on the four changed
  frontend files (`api.ts`, `MdlProvenanceDialog.tsx`, `CoverageBadge.tsx`, `index.tsx`).
  **Depends-on:** none. (Investigation task — may be environment-only.)
- [ ] **G6.2 (optional) `persistence/models.py` mypy baseline.** The new
  `AiAgentCoverageRun` emits the same 2 `Column`-typing errors every existing model
  does; the file isn't mypy-clean at baseline. Only worth doing as a whole-file
  `Mapped[...]`/`mapped_column` typing pass (out of scope for one model). **No action**
  unless the team adopts SQLAlchemy 2.0 typed models repo-wide.

---

## Consolidated decision points

| ID | Decision | Recommendation |
| --- | --- | --- |
| D-FU-1 | What counts as an enrichment "document" | search results **+ attachments**; exclude `list_documents` enumeration |
| D-FU-2 | Where to attach a finding's source document | tag the **claim** at extraction, copy onto the finding |
| D-FU-3 | Live status: SSE vs poll | **SSE** via the existing project-events stream + 30s safety poll |
| D-FU-4 | ESLint v10 vs legacy config | match **CI's** invocation before changing config |

## Risks & mitigations

| # | Risk | Mitigation | Task |
| --- | --- | --- | --- |
| 1 | Finding↔claim index drift on a degrade path mis-tags documents | Guard tagging on equal lengths; skip if mismatched (counts stay correct) | G2.2 |
| 2 | Per-open-editor SSE connection load | Single shared `useProjectEvents` hook; 30s fallback poll; close on unmount | G3 |
| 3 | Attachment filenames are user-supplied (could be long/odd) | They already pass through `_attachments_text`; treat as display-only, no parsing | G1 |
| 4 | Widening `CoverageFinding` breaks stored-report deserialization | New fields are optional with defaults; old reports validate unchanged | G2.1 |
| 5 | Classifying attachments as enrichment inflates "enrichment" entries | Only when attachments are actually present on the turn; `list_documents` still excluded | G1.2 |

## Source references (current anchors)

- Enrichment signal: `copilot/tools.py::_search_documents:364` (tracks ids at `:385`), `_list_documents:344`, `build_changeset:433` (stamps at `:484`); `copilot/schemas.py` `Changeset.referenced_document_ids:93`, `MessageAttachment:143`; `app.py::_attachments_text:1748`, `_emit_agent_apply_provenance:1060`, `service.py::apply_provenance_payload`.
- Coverage engine: `copilot/coverage.py` `run_directory_coverage:685`, `CoverageDocument:677`, `judge_coverage:326`, `aggregate_report:551`; `copilot/schemas.py` `CoverageFinding:203`, `CoverageReport:224`, `CoverageRun:264`.
- Coverage status/SSE: `app.py::get_coverage_status:1964`, `get_project_semantic_layer_events:3451`; `api.ts::createProjectSemanticLayerEventSource:1854`; `CoverageBadge.tsx` (`POLL_MS:24`, mount `index.tsx:797`); `api.test.ts::MockEventSource:967`.
- UI: `MdlProvenanceDialog.tsx` (`documentRefs`, coverage drill-in), `CoverageReportModal.tsx::CoverageReportBody:46`.

## Industry-pattern sources (rationale)
- SSE + low-frequency fallback poll for live status (avoid pure polling) — server-sent events guidance: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- Provenance/lineage "which source did this come from" tagging — OpenLineage facets model: https://openlineage.io/docs/spec/facets/

---

## Implementation status (Phase 3 delivered 2026-06-28)

All gaps G1–G6 are implemented and tested. Re-anchored against the tree *after*
the multi-schema feature landed (commit `d83567ab0d` + working-tree multi-schema
work); all G1–G6 anchors held.

**Test results**
- Backend `tests/unit_tests/superset_ai_agent/`: **867 passed, 11 skipped**.
- My frontend suites (MdlProvenanceDialog, CoverageBadge, CoverageReportModal,
  useProjectEvents): **17 passed**.
- ruff (enforced) + prettier clean on changed files; changed logic files mypy-clean.

**Pre-existing failures (NOT from this work):** `ExplainDialog.test.tsx` and
`AiAgentPanel/index.test.tsx` (2 tests) fail on schema-qualified SQL rendering
(`SELECT … FROM sales.orders`). Confirmed identical failures with my `api.ts` +
`SemanticLayerEditor/index.tsx` changes stashed — they are collateral from the
other agent's multi-schema feature changing table-reference rendering in the
**SQL-agent** suites, unrelated to provenance/coverage. Flag to that work's owner.

### What shipped, by gap

- **G1.** `Changeset.referenced_attachments` (`copilot/schemas.py`), stamped after
  `run_copilot` in both the turn (`app.py` ~2259) and stream (~2376) handlers;
  `_emit_agent_apply_provenance` folds attachments into `detail.documents` as
  `{id: None, filename}` and they now count toward the enrichment classification
  (`service.py::apply_provenance_payload`).
- **G2.** `CoverageFinding.document_id`/`document_filename`; `run_directory_coverage`
  carries a per-claim `sources` list and tags findings (length-guarded against
  judge degrade paths); UI renders a source-document tag per finding
  (`CoverageReportModal.tsx`).
- **G3.** New shared `useProjectEvents` hook (named-SSE-frame aware) + `COVERAGE_EVENT_TYPES`;
  `CoverageBadge` and the open `MdlProvenanceDialog` subscribe and refetch live;
  badge polling cut from 4s-while-analysing to a 30s safety net.
- **G4.** Badge `refreshSignal` keyed on the **active** file set (id+checksum), not
  all `mdlFiles`.
- **G5.** Accepted non-goals confirmed in code (stage-granular cancel, union overreach).
- **G6.** Resolved the "ESLint broken" note: the project lints with **oxlint**
  (`npm run lint` → `oxlint --config oxlint.json`); there is no ESLint config.

### Decision deltas from the plan

- **D-FU-1 adjusted:** attachments are stamped onto the changeset at the *turn*
  handler (where `request.attachments` lives), not mined from `Changeset.steps` —
  the steps never carried attachment metadata. Same outcome, cleaner source.
- **D-FU-3 implemented as designed:** SSE primary + 30s fallback poll.

## Remaining risks & expectation/UI gaps (next session)

1. **oxlint not runnable locally.** Its native binary (`oxlint.darwin-universal.node`)
   is not installed in this checkout, so the lint gate could not be executed here.
   Prettier + Jest are clean and the code matches existing patterns; CI will lint.
   *Action:* reinstall oxlint platform binary (`npm i` / optional-dep) to verify.
2. **Attachment attribution is filename-only.** Attachments have no document id, so
   an enrichment chip from an attachment shows the filename with no link/preview
   (vs. a real document chip which carries an id). Expected — attachments are
   ephemeral; no further action unless attachments become first-class documents.
3. **Per-document tagging needs the judge to preserve order.** Findings are tagged
   by zip-with-sources, guarded on equal length; if a future judge path reorders
   or drops findings, tagging silently skips (counts stay correct, source tags
   vanish) rather than mis-attributing. Acceptable, but note for judge changes.
4. **Directory report groups by tag, not layout.** Findings show a source-document
   tag but are not visually grouped/collapsible by document. For a project with
   many documents the flat list is long. Follow-up: optional group-by-document
   accordion in `CoverageReportBody`.
5. **SSE connection per surface.** The badge (always, while a project is open) and
   the provenance dialog (while open) each hold one EventSource. Shared via the
   `useProjectEvents` hook but not de-duplicated into a single per-project
   connection. Fine at current scale; revisit if more surfaces subscribe.
6. **Dialog live-refresh is paused during a coverage drill-in** (by design, so the
   open report isn't yanked away). A run completing while the user reads a report
   won't update the timeline until they navigate back — acceptable.
7. **Multi-schema interaction unverified for coverage.** The other agent added
   multi-schema projects after the coverage feature; `_active_mdl_checksum` /
   `_coverage_documents` operate on the active file set regardless of schema, so
   they should be correct, but no test exercises coverage on a multi-schema
   project. Follow-up: add one multi-schema directory-coverage test.
