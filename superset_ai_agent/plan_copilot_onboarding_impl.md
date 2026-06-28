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

# Impl Checklist: Copilot-Driven (Auto-)Onboarding + Coverage Surfacing

**Status:** BUILD CHECKLIST — **Phases 1–5 BUILT + tested (2026-06-28); Phase 6 DEFERRED (security, see §8).** Resumable across sessions.

---

## As-built status (2026-06-28)

| Phase | State | Evidence |
|---|---|---|
| **P1 — find_tables + read_document** | ✅ Built, tested | `SchemaIndex.search` + `_find_tables`/`_read_document` in `tools.py`; `skills/onboarding.md` doc-driven section. Tests: `test_copilot_tools.py`, `test_copilot_document_tools.py`, `test_mdl_validator.py`. |
| **P2 — AutoOnboardModal** | ✅ Built, tested | `SemanticLayerEditor/AutoOnboardModal.tsx` (+ `.test.tsx`, 5 tests). |
| **P3 — Copilot kickstart + relabel** | ✅ Built, tested | `CopilotPanel.tsx` (`CopilotKickstart`, `submitTurn`/`runKickstart`, relabel to "Auto-onboard"/"Onboard manually"); `index.tsx` modal mount + `AUTO_ONBOARD_MESSAGE`. Tests: `CopilotPanel.test.tsx` (kickstart fires once / read-only no-op), `index.test.tsx` (button opens modal). |
| **P4 — coverage badge + running indicator** | ✅ Built, tested | `SemanticProject.coverage_score` (schemas.py) populated in the list route (app.py, DP-A path a); `ProjectBrowser.tsx` `%`-badge; `MdlProvenanceDialog.tsx` running indicator; R9 inline score already existed. Tests: `ProjectBrowser.test.tsx`, `MdlProvenanceDialog.test.tsx`, `test_copilot_api.py`. |
| **P5 — run_coverage self-review tool** | ✅ Built, tested | `_run_coverage` in `tools.py` (read-only, per-turn capped + memoized); `model_client`/`embedder`/`instructions` threaded `run_copilot`→toolset (service.py). Tests in `test_copilot_document_tools.py`. |
| **P6 — add_project_schema** | ⛔ **DEFERRED** | The toolset cannot prove DB access to a new schema, introspect its tables into the (turn-fixed) `schema_index`, or persist project membership — all three are app-layer. Shipping it in the toolset would grant cross-schema access **without** the access proof the existing `addSchema` resolve path enforces (security hole, B6/R2 High). DP-C recommended defer; **decision left to the user** (needs a cross-layer access-prover + context re-introspection design). Current safe path: the user-driven "Add schema" widen (re-resolve proves access). |

**Test totals after this work:** backend `tests/unit_tests/superset_ai_agent/` = **942 passed, 11 skipped**; frontend `AiAgentPanel/` = **31 suites, 271 passed**. ruff/prettier clean on all changed files.

---

**Spec / rationale:** `plan_copilot_onboarding_spec.md` (the "why"). This doc is the "how" + ordered checklist.
**Canonical refs:** `MDL_PROVENANCE_AND_COVERAGE.md` (coverage run policy, provenance), `plan_mdl_lab_spec.md` (F4 ungated copilot, ProjectBrowser), `plan_tool_call_provenance_spec.md` (tool-call ledger), `skills/onboarding.md` (agent discipline).

> Symbols are stable; **line numbers drift — grep the symbol.** Check a box only when its "Done when" gate is green.

---

## 0. Decisions locked by the user (do not re-litigate)

| # | Decision | Consequence for this build |
|---|---|---|
| **D1** | **Demote, don't delete, the picker.** | Keep `OnboardingTablePicker` and the background job. Relabel the empty-state button to **"Onboard manually"**; add a primary **"Auto-onboard"** button beside it. |
| **D2** | **Auto-onboard = doc-select/upload modal → attach to chat → paste a standard user message → auto-send to kickstart the Copilot.** | New `AutoOnboardModal` + a kickstart seam into `CopilotPanel`. No new backend endpoint for the flow itself. |
| **D3** | **Metrics get NO distinct provenance verb.** | `propose_metric` (if built) folds under ledger verb `write`. **Do not** extend `ToolActionKind`. |
| **D4** | **Coverage surfacing, not a new annotation.** Coverage is shown (a) per generated version in the provenance UI, (b) as a *running* indicator while a run is in flight, (c) as a **% badge** on the MDL browser entry / project UI. Reuse the **existing run policy** (latest wins, supersede on new state, background) — already implemented. | No `coverage_checked` event/annotation (the §7/DP3 idea from the spec is **dropped**). Instead: surface the existing `coverage_completed` events + `getCoverageStatus` running state + a browser-row badge. |

**Open decision points still needing a call** (see §6): DP-A (coverage % source for the list badge: extend list endpoint vs per-row fetch), DP-B (build the `run_coverage` agent self-review tool now or defer), DP-C (cross-schema `add_project_schema` now or defer).

---

## 1. What already exists (DO NOT REBUILD — verified in tree)

- ✅ **Ungated Copilot** on empty projects; empty-state banner already references doc-driven onboarding ([CopilotPanel.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) `needsOnboarding`, ~L802-844).
- ✅ **Onboarding tools:** `propose_onboard_table(s)`, `propose_relationships` ([copilot/tools.py](semantic_layer/copilot/tools.py)); build from the permission-filtered `SchemaIndex`; reject unknown tables (R1).
- ✅ **BI-doc Attach + RAG:** `useDocumentIngestion.ingest()`, `search_documents`/`list_documents` tools, project-scoped vectors.
- ✅ **Background coverage run policy:** supersede + `claim()` CAS + debounce + idempotency; emits `coverage_completed` ([coverage_store.py](semantic_layer/coverage_store.py), `_schedule_coverage`/`_run_coverage_job` in [app.py](app.py)). **This IS the "latest/supersede/background" policy D4 asks for.**
- ✅ **Coverage UI:** `CoverageBadge.tsx` (header badge; `getCoverageStatus`; SSE via `useProjectEvents` + 30s poll), `CoverageReportModal.tsx::CoverageReportBody`, and a **"View report" drill-in** already in `MdlProvenanceDialog.tsx`.
- ✅ **Provenance timeline** with `coverage_completed` (kind `coverage`) entries; tool-call ledger (`ToolCallRecord`, verbs `write|delete|onboard|relate`) per `plan_tool_call_provenance_spec.md`.
- ✅ **ProjectBrowser** ([ProjectBrowser.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/ProjectBrowser.tsx)) with rows fed from `listSemanticProjects` (no coverage field yet).

**Net new work:** (P0) `find_tables` + `read_document` tools; (P1) Auto-onboard modal + Copilot kickstart seam + relabel; (P2) coverage % browser badge + provenance running indicator; (P3, optional) `run_coverage` self-review tool; (P4, optional) `add_project_schema`.

---

## 2. Requirements (clear, testable)

**R-Tools**
- R1. `find_tables(query, schema?, limit=10)` returns only the top-ranked physical tables (with columns+types) from the project's permission-filtered `SchemaIndex` — never the whole schema. Read-only; not in the ledger.
- R2. `read_document(document_id, max_chars?)` returns the full extracted text (bounded, `truncated` flag) of one project document. Read-only.
- R3. Both tools are pure reads: no `_working` mutation, no `ToolCallRecord`, no provenance.
- R4. `skills/onboarding.md` instructs the agent to prefer `find_tables` for doc→table mapping and `read_document` for extraction, keeping `get_physical_schema` for grounding/validation only.

**R-AutoOnboard**
- R5. Empty-state offers two buttons: **"Auto-onboard"** (primary) and **"Onboard manually"** (secondary → existing picker). The existing "Onboard this schema" copy is replaced.
- R6. Auto-onboard opens a modal listing the project's existing documents (multi-select) + an upload control (reuses `ingest()`); the user picks/uploads ≥1 doc and confirms.
- R7. On confirm, the selected documents are attached to the Copilot chat **and** a standard templated message is sent automatically (one turn), kickstarting the loop. The message text is visible in the transcript (not hidden).
- R8. The kickstart fires **exactly once** per confirm (no re-fire on re-render); it no-ops if the project is read-only.

**R-Coverage-Surfacing**
- R9. Each successfully generated/active MDL version shows its coverage result in the provenance timeline (already true via `coverage_completed`); verify the score renders inline on the entry, not only behind "View report".
- R10. While a coverage run is in flight for the current version, the provenance UI shows a **running indicator** (icon/row) driven by `getCoverageStatus.status === 'analysing'`/`running`.
- R11. The MDL browser row (and the project workspace strip) shows a **coverage % badge** per project, degrading gracefully when no run exists.
- R12. No change to the run policy (latest/supersede/background) and no new event type (reset-purge contract intact).

**R-Provenance-Parity** (only if P3/P4 land)
- R13. Any new *mutating* tool appends a `ToolCallRecord`; `run_coverage` (read-only) does not and creates no provenance.

---

## 3. Sequential build checklist

> Each phase ends green per `CLAUDE.md` (pytest + Jest for touched suites + `pre-commit run`). Phases are ordered by dependency; **blockers noted inline**.

### Phase 0 — Preflight (no code)
- [ ] **0.1** Re-grep the symbols in §1 to confirm they still exist (the tree moves). Note any drift in this doc.
- [ ] **0.2** Confirm `WREN_COPILOT_ENABLED` + coverage flags are on in the dev env; load an empty project and confirm the Copilot opens ungated.
- [ ] **0.3** Resolve open DPs **DP-A** (coverage badge data source) and **DP-B/DP-C** (build or defer self-review / schema-add). Record answers in §6. *Blocker for: Phase 4 (DP-A), Phase 5 (DP-B), Phase 6 (DP-C).*

### Phase 1 — `find_tables` + `read_document` tools (P0, backend, read-only) — **no blockers**
- [ ] **1.1** `SchemaIndex` (imported in [copilot/tools.py](semantic_layer/copilot/tools.py) L53): add a ranking helper `search(query, schema=None, limit=10) -> list[TableMatch]` over table+column names. **Use keyword/substring ranking** (reuse the pattern of `keyword_rank_chunks` in `copilot/coverage.py`); embedder optional later (DP7 in spec → degrade-to-keyword). *Done when:* unit test ranks "customer orders" above unrelated tables; returns ≤limit; respects the `schema` filter and the permission-filtered index.
- [ ] **1.2** `MdlToolset._find_tables(args)` handler + `ToolSpec` in `specs()`; wire into `dispatch()`. Returns `{tables:[{schema,table,columns:[{name,type}],score}]}`. **Not** in `_MUTATING_ACTIONS`.
- [ ] **1.3** Document full-text accessor: confirm the toolset's document store exposes one doc's `extracted_text` (it already uses `list_project_chunks` for search; add `get_document`/text read if absent). *Blocker check:* the toolset must hold a reader that can fetch a single document's text by id — verify in the `MdlToolset.__init__` deps before writing the handler.
- [ ] **1.4** `MdlToolset._read_document(args)` handler + `ToolSpec` + `dispatch()`. Returns `{filename,text,truncated}`; default `max_chars` ~50–100KB (match the 200KB attach slice ceiling; R4 in spec). Read-only.
- [ ] **1.5** `skills/onboarding.md`: add guidance (R4) — `find_tables` for doc→table mapping, `read_document` for extraction, `get_physical_schema` for grounding/validation.
- [ ] **1.6** Tests: `tests/unit_tests/superset_ai_agent/test_copilot_tools.py` — find_tables ranking + limit + schema filter + degrade-to-keyword; read_document truncation; both absent from the ledger.
- **Done when:** new tools dispatch, return bounded results, change no state, and the agent can map a doc entity → a physical table without `get_physical_schema`.

### Phase 2 — Auto-onboard modal (P1, frontend, new file) — **no blockers** (parallel to Phase 3)
- [ ] **2.1** New `SemanticLayerEditor/AutoOnboardModal.tsx`: props `{open, projectId, canWrite, existingDocuments, onCancel, onConfirm(documents: SemanticDocument[])}`. Body = a multi-select list of `existingDocuments` (checkboxes; reuse the `documentStatus` badges) + an upload `<input type=file multiple>` that calls the shared `useDocumentIngestion.ingest()` and appends results to the selection. Confirm is enabled only with ≥1 selected and no still-extracting doc. *Pattern source:* mirror the gating/structure of `OnboardingTablePicker.tsx` (search + selection + confirm) but flat (no schema tree). *Done when:* its own RTL test passes (select existing, upload new, confirm emits the union; confirm disabled while extracting).
- [ ] **2.2** `AutoOnboardModal.test.tsx`.

### Phase 3 — Copilot kickstart seam + relabel (P1, frontend) — **depends on Phase 2** (modal emits the docs)
- [ ] **3.1** `CopilotPanel.tsx`: extend `CopilotPanelProps` with `kickstart?: { token: number; message: string; documents: SemanticDocument[] }`. Add a `useEffect([kickstart?.token])` that, once per new token: sets `attachedDocs` to the docs, sets `input` to the message (for transcript visibility), and calls a new `runKickstart(message, docs)` that **bypasses the attachedDocs state race** — it `ensureConversation()` → builds `MessageAttachment[]` directly from `docs` (same slice logic as `attachmentsForSend`, CopilotPanel.tsx ~L352-364) → `streamCopilot(...)`. Guard: no-op if `!canWrite` or token already seen (R8). *Source:* send pipeline at CopilotPanel.tsx `handleSend` ~L375-430.
- [ ] **3.2** `CopilotPanel.tsx` empty-state banner (~L802-844): replace the single "Onboard this schema" button with **"Auto-onboard"** (primary, calls a new `onAutoOnboard` prop) + **"Onboard manually"** (secondary, calls existing `onOnboard`). Update the copy. *Requirement R5.*
- [ ] **3.3** `index.tsx`: add `showAutoOnboard` state + mount `AutoOnboardModal` (sibling to `OnboardingTablePicker` at ~L1653). Pass `existingDocuments={documents}`. On confirm, set a `kickstart` object with a fresh `token` (e.g. an incrementing counter — **not** `Date.now()` if any test relies on determinism) and `showCopilot=true`, then pass `kickstart` down to `CopilotPanel` (mounted ~L1604-1612). Wire `onAutoOnboard={() => setShowAutoOnboard(true)}`.
- [ ] **3.4** Standard kickstart message constant (editable copy), e.g.: *"Read the attached document(s) and onboard the tables they describe from this database, then add the relationships and enrich the models. Show me one changeset to review."* Keep it as a named constant for reuse/i18n.
- [ ] **3.5** Tests: `CopilotPanel.test.tsx` (kickstart attaches docs + sends once, read-only no-ops, token guard) + `index.test.tsx` (Auto-onboard button → modal → confirm → CopilotPanel receives kickstart + becomes visible).
- **Done when:** clicking Auto-onboard on an empty project, selecting a doc, and confirming results in one Copilot turn with the doc attached and the templated message in the transcript.

### Phase 4 — Coverage % browser badge + provenance running indicator (P2, frontend ± backend) — **blocker: DP-A (0.3)**
- [ ] **4.1 (DP-A path a — recommended):** extend the project-list response with a lightweight coverage score so the browser doesn't open N SSE/status connections. Backend: `GET /agent/semantic-layer/projects` includes `coverage_score`/`coverage_status` from `latest_complete(project)` (batch read; no per-row job). Frontend: add `coverageScore?: number|null` to `SemanticProject` + `ProjectBrowserProject`, map it (index.tsx ~L785-798), render a `<Tag>` in `RowMeta` (ProjectBrowser.tsx ~L321-328). *Rationale:* `CoverageBadge` self-drives an SSE per mount (PROVENANCE doc known-gap #5); 50 rows = 50 EventSources — avoid. *Done when:* the browser shows a % tag per project with one list request, no per-row SSE.
- [ ] **4.1-alt (DP-A path b):** if not extending the endpoint, embed `<CoverageBadge projectId={project.id} />` in the row (it's loosely coupled, only needs `projectId`) and **cap** how many rows mount it (e.g. only the active/visible page) to bound EventSource count. Document the cap (R7 mitigation).
- [ ] **4.2** Provenance running indicator (R10): in `MdlProvenanceDialog.tsx`, when `getCoverageStatus(projectId).status` is `analysing`/`running`, render a top synthetic row/icon "Coverage analysing…". Reuse `useProjectEvents` + `getCoverageStatus` (already imported by `CoverageBadge`). *Done when:* starting a run (e.g. activate a file) shows the indicator, which clears to a `coverage_completed` entry on finish.
- [ ] **4.3** Verify R9: the existing `coverage_completed` entry renders its **score inline** (not only behind "View report"). If only the drill-in shows it, add the score to the entry summary in `MdlProvenanceDialog.tsx`.
- [ ] **4.4** Tests: `ProjectBrowser.test.tsx` (coverage tag renders / absent when null), `MdlProvenanceDialog.test.tsx` (running indicator on analysing; inline score).
- **Done when:** a project's coverage % is visible in the browser and the workspace strip; an in-flight run is visible in the provenance dialog; nothing changed in the run policy or event types (R12).

### Phase 5 — `run_coverage` agent self-review tool (P3, OPTIONAL) — **blocker: DP-B (0.3)** + a real dependency
- [ ] **5.1** *Dependency / blocker:* `run_directory_coverage` needs a `model_client` (+ optional `embedder`). Confirm `MdlToolset` can be constructed with these (today the **loop** holds `model_client`, the toolset may not). If absent, thread them into `MdlToolset.__init__` from the loop/service. **Do not** proceed until this injection path is clear.
- [ ] **5.2** `MdlToolset._run_coverage(args)` + `ToolSpec`: audit the **working set** (`_working` MDL) against the project documents via `run_directory_coverage`; return `{score,total,covered,partial,missing,findings[]}`. Read-only; **no provenance, no persistence** (D4 — the authoritative run is still the post-activation background job).
- [ ] **5.3** Cost guards (spec R3): cap self-audits per turn (e.g. 2); reuse the coverage **cache** so an unchanged working set re-audits free.
- [ ] **5.4** `skills/onboarding.md` / `enrich-context.md`: instruct the agent to `run_coverage` after onboarding to find gaps, then enrich, then re-audit — implementing user-flow step 3.
- [ ] **5.5** Tests: tool returns findings; capped; cache hit on unchanged set; emits no event/ledger record.
- **Done when:** the agent can self-audit its draft MDL against the docs in-conversation and refine before handing off — with zero provenance side effects.

### Phase 6 — `add_project_schema` cross-schema tool (P4, OPTIONAL) — **blocker: DP-C (0.3)** + security gate
- [ ] **6.1** `MdlToolset._add_project_schema(schema)`: stage a membership add. **Security-critical (spec R2):** the schema must be re-proven against the user's Superset DB access (the R1 access proof from the multi-schema spec) — route the proof through the app layer, reject unproven pre-apply. Mutating → ledger verb `onboard`.
- [ ] **6.2** `propose_onboard_tables` can then target the newly added schema (already schema-aware).
- [ ] **6.3** Tests (security): cross-schema onboard from a BI doc succeeds for a proven schema; an unproven schema is **rejected** pre-apply. Map to `SECURITY.md` (principal = role with that DB's access).
- **Done when:** a cross-schema BI doc produces one reviewable changeset that adds the schema (access-proven), onboards its tables, and wires joins.

---

## 4. File touchpoints (grouped; hot files take single-writer edits)

| File:symbol | Phase | Change |
|---|---|---|
| `semantic_layer/copilot/tools.py::MdlToolset.specs, _find_tables(new), _read_document(new), dispatch` ★ | 1 | Two read-only tools + specs + dispatch. |
| `semantic_layer/schema_*` (the `SchemaIndex` definition) | 1 | Add `search()` keyword ranker. |
| `skills/onboarding.md` (+ `enrich-context.md`) | 1, 5 | Tool-usage guidance. |
| `SemanticLayerEditor/AutoOnboardModal.tsx` (+ `.test.tsx`) **new** | 2 | Doc multi-select + upload modal. |
| `SemanticLayerEditor/CopilotPanel.tsx::CopilotPanelProps, kickstart effect, runKickstart, empty-state banner` ★ | 3 | Kickstart seam + relabel buttons (`onAutoOnboard`). |
| `SemanticLayerEditor/index.tsx::showAutoOnboard, kickstart, modal mount, CopilotPanel props` ★ | 3, 4 | Mount modal; pass kickstart; map coverage score. |
| `AiAgentPanel/api.ts::SemanticProject (coverage_score), ProvenanceEntry` ★ | 4 | (DP-A path a) coverage field on list type. |
| `SemanticLayerEditor/ProjectBrowser.tsx::ProjectBrowserProject, RowMeta` | 4 | Coverage % tag per row. |
| `SemanticLayerEditor/MdlProvenanceDialog.tsx` | 4 | Running indicator + inline score. |
| `app.py::list projects route, _coverage for list; (P5) toolset model_client wiring; (P6) schema-add access proof` ★ | 4, 5, 6 | List coverage; tool deps; access gate. |
| `semantic_layer/copilot/coverage.py` | 5 | Working-set audit entry callable from the tool. |
| `tests/unit_tests/superset_ai_agent/test_copilot_tools.py`, `test_copilot_coverage.py`, `test_copilot_onboarding.py` | 1,5,6 | Backend tests. |

★ = high-contention; one writer at a time (see `plan_mdl_lab_spec.md` §12 hot-file list).

---

## 5. Risks & mitigations (build-specific)

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| **B1** | **Kickstart double-fire** — the `useEffect` re-runs and sends twice. | Med | Token guard (R8): track the last-handled `kickstart.token` in a ref; fire only on change. Test the guard. |
| **B2** | **attachedDocs state race** — auto-send reads stale empty `attachedDocs`. | Med | `runKickstart` builds `MessageAttachment[]` directly from the passed `documents`, not from state (Phase 3.1). |
| **B3** | **N SSE connections** from per-row `CoverageBadge` in the browser. | Med | DP-A path a (list-endpoint coverage, one request) is the recommendation; path b caps mounts to the visible page. |
| **B4** | **`run_coverage` cost / loop** (LLM-heavy extract+judge). | Med | Per-turn cap + cache reuse (Phase 5.3); tool is optional (DP-B); authoritative run stays the background job. |
| **B5** | **`run_coverage` toolset dependency** — toolset lacks `model_client`. | Med | Phase 5.1 is a hard blocker; thread deps from the loop before building the handler. |
| **B6** | **Cross-schema access bypass** via `add_project_schema`. | **High (sec)** | Phase 6.1 access proof; reject unproven pre-apply; security test (6.3). Defer (DP-C) if not ready. |
| **B7** | **`read_document` context blow-up** on a huge doc. | Low | `max_chars` bound + `truncated` flag; agent falls back to `search_documents`. |
| **B8** | **Manual-onboard discoverability** lost after relabel. | Low | Keep "Onboard manually" visible as the secondary button (D1); don't hide it behind a menu. |
| **B9** | **Coverage version mismatch** — the browser % reflects a superseded run. | Low | Read `latest_complete` only; the `stale` flag from `getCoverageStatus` already distinguishes active-checksum ≠ last run; surface "stale" in the badge. |

---

## 6. Open decision points (resolve in Phase 0.3)

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **DP-A** | Coverage % data source for the browser badge | (a) extend the project-list endpoint with `coverage_score` (one batch read); (b) per-row `CoverageBadge` (self-driving, N SSE) | **(a)**. Avoids N EventSources (PROVENANCE doc known-gap #5) and matches `plan_mdl_lab_spec.md` R7 "lazy/batch the badges." |
| **DP-B** | Build the `run_coverage` self-review tool now? | (a) Phase 5 now; (b) defer — rely on post-activation background coverage + the agent re-reading docs | **(a) if** step-3 in-conversation self-review is wanted (it directly implements "Copilot reviews the onboarding"); else defer. It is the only way the agent sees a coverage signal on **drafts** (background coverage only runs on **active** MDL). |
| **DP-C** | Build `add_project_schema` now? | (a) Phase 6 now; (b) defer to a cross-schema iteration | Defer unless cross-schema BI docs are a near-term need; it carries the security gate (B6). Single-schema Auto-onboard works without it. |
| **DP-D** | Coverage trigger for Copilot-generated versions | (a) keep activation as the only trigger (drafts not audited by the background job); (b) also schedule on copilot apply | **(a)**. Coverage audits **active** MDL (drafts aren't queryable); the `run_coverage` tool (DP-B) covers the draft self-review need. Don't audit drafts in the background. |

---

## 7. Final acceptance gate (the user's three-step flow, end to end)

- [ ] **A1 (step 1)** On an empty project, **Auto-onboard** → modal → select/upload BI doc(s) → confirm.
- [ ] **A2 (step 2)** The Copilot receives the doc(s) + the templated message, calls `read_document` + `find_tables`, and proposes one reviewable changeset onboarding the named tables (+ relationships) — R1-validated, human-approved.
- [ ] **A3 (step 3)** The Copilot reviews coverage (via `run_coverage` if DP-B=build, else after activation), reports gaps, and refines (enrich / metrics via `write_mdl_file`).
- [ ] **A4 (surfacing)** After activation, the provenance timeline shows the agent's onboarding (verb rollup + doc chips) **and** the coverage result for that version; an in-flight run shows the running indicator; the MDL browser row shows the coverage % badge.
- [ ] **A5 (parity)** "Onboard manually" still opens the picker and runs the background job; nothing in the run policy, event types, or reset-purge contract changed.
- [ ] **A6 (green)** `pytest tests/unit_tests/superset_ai_agent/` + touched Jest suites + `pre-commit run` all clean.

---

### Sources
- [Anthropic — Writing effective tools for agents (few well-named tools; agent-ready, token-bounded results over raw dumps)](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [WrenAI — agent onboarding/generate-mdl skills + human-in-the-loop](https://github.com/Canner/WrenAI)
- [OpenTelemetry — GenAI tool-call spans (named verb + shape + status, content nested/opt-in)](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- Internal: `MDL_PROVENANCE_AND_COVERAGE.md`, `plan_mdl_lab_spec.md`, `plan_tool_call_provenance_spec.md`, `skills/onboarding.md`.
