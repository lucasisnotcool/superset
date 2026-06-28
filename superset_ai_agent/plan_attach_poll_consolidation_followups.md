# Poll Consolidation, Pending-only Fetch, MDL Sniff & Give-up Wording — Follow-up Plan

> **Status:** SPEC / proposal. **Not implemented.** Closes the four residual gaps
> from
> [`plan_attach_tree_gate_json_followups.md` §11](plan_attach_tree_gate_json_followups.md):
> - **G1 — two independent polls:** the editor tree poll (`listSemanticDocuments`)
>   and the composer chip poll (`getSemanticDocument`) both run when the Copilot is
>   open and a doc is pending — redundant GETs.
> - **G2 — tree poll re-fetches the whole list each tick** (heavier with a large
>   document set) instead of just the pending docs.
> - **G3 — MDL sniff 1 MB cap:** a genuinely MDL-shaped file ≥ 1 MB gets no notice.
> - **G4 — give-up wording is generic:** *Still processing in the background* for any
>   pending status; doesn't say which phase is slow.
>
> All `file:line` anchors verified at authoring time — re-grep if the tree moved.
> Written to be used as a working checklist (§10) by a future agent session.

---

## 1. Requirements (testable)

| # | Requirement |
| --- | --- |
| **R1** | There is **one** document-status poll. The composer issues **no** document-status network requests of its own; it derives chip status, grounding text, and the Send-gate/give-up from the shared poll's data (G1). |
| **R2** | The poll fetches **only pending documents** (per-doc `getSemanticDocument`), not the entire list each tick (G2). The one-time initial load may still list. |
| **R3** | The composer's live chip, inline grounding (`extracted_text`), and Send-gate/give-up behave exactly as today (chip goes live; Send gated while pending; gives up after the cap; grounds on finished text) — only the data **source** changes (G1). |
| **R4** | The MDL-shape notice fires for MDL-shaped JSON up to the server upload maximum — no sub-limit silently skips a real MDL file (G3). |
| **R5** | The give-up cue names the actual phase (e.g. *Extracting…* vs *Queued…*) and that it is *taking longer than expected* (G4). |
| **R6** | *(non-functional)* Bounded + cancel-safe poll; reuse `isPendingDocumentStatus` + `getDocumentStatusMeta`; **no backend change**; no `any`; degrade-closed (no `documents` prop → chip simply isn't live, composer still works). |

---

## 2. Current state (source-backed)

- **Two polls exist.**
  - Editor: while `documents.some(isPendingDocumentStatus)`, re-fetch
    **`listSemanticDocuments(scope)`** (whole list) and `setDocuments`
    ([`index.tsx:770-809`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx),
    constants `DOCUMENT_POLL_*` at `:249-250`, `documentStatusSignature` at `:253`).
  - Composer: while a staged attachment is pending, poll **`getSemanticDocument(id)`**
    per pending doc and patch `attachedDocs`
    ([`CopilotPanel.tsx:291-338`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx),
    constants `ATTACH_POLL_*` at `:114-115`, `attachPollGaveUp` at `:152`).
  - When the Copilot is open with a pending attachment, **both** run (G1).
- **The list payload already includes `extracted_text`.** Both list routes are
  `response_model=list[SemanticDocument]` and return the store rows unchanged
  ([`app.py:2806,3072`](app.py); `SemanticDocument.extracted_text` in
  [`schemas.py:106`](semantic_layer/schemas.py)). **So one poll can feed both the
  tree (status) and the composer (status + grounding text)** — no per-surface fetch
  is fundamentally required.
- **The composer is a child of the editor**, which owns `documents`
  ([`index.tsx:284`]) and renders `<CopilotPanel … onDocumentsChanged={refresh}>`
  ([`index.tsx:1213-1220`]). Data flows parent→child; the editor is the natural
  single owner of the poll.
- **The give-up signal lives in the composer** (`attachPollGaveUp`) and drives the
  chip cue ([`CopilotPanel.tsx:994`]), the give-up note ([:1016]), and the Send-gate
  ([:366-367,1079,1094]). The chip/note text is a constant string (G4).
- **The MDL sniff caps at 1 MB.** `isLikelyMdlJson` returns false for `file.size ≥
  MDL_SNIFF_MAX_BYTES (1_000_000)` before parsing
  ([`useDocumentIngestion.ts:43,54`](../superset-frontend/src/SqlLab/components/AiAgentPanel/useDocumentIngestion.ts)).
  The server upload ceiling is `wren_max_document_bytes` (10 MB), so files between
  1–10 MB reach the sniff but are skipped (G3).

---

## 3. Best practices (industry standard)

- **Single source of truth for async job status.** One poller owns the lifecycle;
  views subscribe. Avoids duplicate requests and divergent state — the React-idiomatic
  parent-owns-state, child-subscribes pattern.
- **Poll the delta, not the world.** Fetch only the in-flight items (by id), not the
  full collection each tick — standard for status polling at scale. (A batch
  "get-many-by-ids" endpoint is the next step beyond per-id; noted, not required.)
- **Classify by content read from the head of the stream.** JSON top-level keys
  appear at the start; a bounded prefix read classifies a file in O(1) regardless of
  total size. (Or simply parse up to the upload ceiling.)
- **Status messages should name the phase.** A progress affordance that states *what*
  is slow beats a generic "processing".

---

## 4. Decision points

| # | Decision | Recommendation | Rationale / trade |
| --- | --- | --- | --- |
| **D1** | How to consolidate the two polls (G1)? | **(A) Editor owns the single poll; pass `documents` + a `documentsPollGaveUp` flag to `CopilotPanel`, which drops its own poll and derives everything from props.** | Parent owns state, child subscribes — idiomatic and keeps the tree live even when the Copilot is closed. The list/get payload carries `extracted_text`, so the composer loses nothing. Alternatives: (B) keep both, dedupe by pausing one — messy, stateful coupling; (C) leave G1, only fix G2 — doesn't close the gap. |
| **D2** | Poll granularity (G2)? | **Per-pending `getSemanticDocument(id)` (the existing composer-poll mechanism, moved up), patched into `documents` by id.** Keep the one-time initial `listSemanticDocuments` in `refresh`. | Fetches only the few extracting docs, each carrying its own `extracted_text` — strictly lighter than re-listing all docs (with all their text) every tick. A backend batch endpoint would be lighter still but needs a server change (out of scope). |
| **D3** | MDL sniff cap (G3)? | **Raise `MDL_SNIFF_MAX_BYTES` to the upload ceiling (10 MB)** so every uploadable JSON is classified by a full, robust parse. | Zero false positives (full parse), trivial change; the rare ≤10 MB JSON parse costs tens of ms. Alternative: prefix-scan (`file.slice(0, 64 KB)`) for O(1) — but a truncated prefix can't be `JSON.parse`d, forcing a regex sniff with small false-positive risk. Full parse to 10 MB is simpler and exact. |
| **D4** | Give-up wording (G4)? | **Derive the cue from the actual status:** `t('%s — taking longer than expected', getDocumentStatusMeta(status).label)` for the chip; a phase-named note ("%s is still extracting / queued …"). | Tells the user which phase is slow using the existing status labels — no new status vocabulary. |

> **D1 is the load-bearing refactor.** It moves the poll mechanism out of
> `CopilotPanel`, so the composer's **poll tests change their data source** (drive
> via a changing `documents` prop instead of a mocked `getSemanticDocument`). The
> observable behavior (chip live, gate, give-up, grounding) is unchanged — only the
> test wiring moves. Called out as a Phase-3 dependency (§10).

---

## 5. Entrypoints & touchpoints

| # | File:symbol | Change |
| --- | --- | --- |
| T1 | [`index.tsx:770-809`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) doc poll | Replace the whole-list re-fetch with: collect `documents.filter(isPendingDocumentStatus)` → `Promise.all(getSemanticDocument(id))` → patch into `documents` by id (change-guarded via `documentStatusSignature`). Import `getSemanticDocument`. Keep bounded + cancel-safe. (G2/R2) |
| T2 | `index.tsx` — give-up state | Add `documentsPollGaveUp` state. Set `true` when the poll hits the cap with docs still pending; reset to `false` when the effect re-arms with pending docs (new attach / status change). (R3/G1 — feeds the composer) |
| T3 | [`index.tsx:1213-1220`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) `<CopilotPanel>` | Pass `documents={documents}` and `documentsPollGaveUp={documentsPollGaveUp}`. |
| T4 | [`CopilotPanel.tsx` `CopilotPanelProps`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) | Add `documents?: SemanticDocument[]` and `documentsPollGaveUp?: boolean`. |
| T5 | `CopilotPanel.tsx:291-338,114-115,152,55` | **Remove** the chip poll effect, `ATTACH_POLL_*` constants, `attachPollGaveUp` state, and the `getSemanticDocument` import. (G1) |
| T6 | `CopilotPanel.tsx` — sync effect | Add an effect: when the `documents` prop changes, reconcile each `attachedDocs` entry's status/`extracted_text` from `documents` by id (merge; keep the staged object if not yet present in `documents`). Replaces the old poll as the live-status source. (R3) |
| T7 | `CopilotPanel.tsx:366-367,994,1016,1079,1094` | Swap local `attachPollGaveUp` → the `documentsPollGaveUp` prop in the Send-gate, chip cue, and note. (G1/R3) |
| T8 | `CopilotPanel.tsx:994,1016` give-up text | Make the chip cue + note **status-aware** (D4): `getDocumentStatusMeta(doc.status).label` + "taking longer than expected"; note names the phase per doc. (G4/R5) |
| T9 | [`useDocumentIngestion.ts:43`](../superset-frontend/src/SqlLab/components/AiAgentPanel/useDocumentIngestion.ts) | Raise `MDL_SNIFF_MAX_BYTES` to `10_000_000` (the upload ceiling). (G3/R4) |

**Backend:** none (R6).

---

## 6. Design notes

- **Editor poll (T1) keeps the change-guard.** Patch only changed rows (status or
  `extracted_text`) so an unchanged tick keeps `documents` identity stable — no tree
  churn, no effect re-arm. Same `documentStatusSignature` discipline (extend it to
  include a text-present marker if grounding staleness shows up).
- **Give-up reset (T2).** Reset `documentsPollGaveUp=false` at the start of an effect
  run that has pending docs (a real re-arm), so a later attach clears a prior
  give-up. Set `true` only on cap-with-pending. (Mirror the composer's prior
  `attachPollGaveUp` lifecycle, now owned by the editor.)
- **Composer sync (T6) vs. just-attached race.** On attach, `handleAttach` stages the
  upload-response doc (already has `extracted_text` for small files) and calls
  `onDocumentsChanged` → editor `refresh` → `documents` includes it. The sync effect
  merges by id and prefers the `documents` copy when present, else keeps the staged
  object — so a doc is never blanked during the brief window before the editor list
  catches up.
- **Degrade-closed (R6).** `documents` defaults to `[]`; with no prop the chips show
  their attach-time status (not live) and the gate uses give-up=false — i.e. today's
  pre-poll behavior. The editor always provides the prop, so this is only a safety net.
- **MDL sniff (T9).** Full parse to 10 MB; the server already rejects >10 MB, so no
  uploadable file is skipped. Keep the `try/catch` (unparseable → not MDL).

---

## 7. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| **Consolidation regresses chip/gate/grounding (R3).** | Behavior is unchanged; only the data source moves. Keep the existing CopilotPanel behavior tests, re-pointed to drive via the `documents` prop. Add a "no `getSemanticDocument` called from the composer" assertion. |
| **Composer poll tests must be rewritten** (mechanism change). | Expected + scoped (Phase 3). The *assertions* (chip live, gate, give-up, fresh-text-on-send) stay; only the stimulus changes (prop update vs timer + mocked fetch). |
| **Editor poll now N parallel GETs per tick** (one per pending doc). | Only **pending** docs (typically 1–few extracting at once) — strictly fewer bytes than re-listing every doc with full text. A batch endpoint is the future step (noted, out of scope). |
| **Give-up flag threading / stale gate.** | Single owner (editor) with explicit reset-on-re-arm (T2/§6); covered by a give-up + re-arm test. |
| **Sync effect churn** (documents prop changes each patch). | Reconcile is a cheap id-map merge; `attachedDocs` only updates when a staged id's status/text actually changed (guard). |
| **`index.test` live-tree test points at the list endpoint.** | Update it to mock the single-doc `getSemanticDocument` GET that the poll now calls (Phase 2 dependency). |
| **MDL parse cost at 10 MB.** | Rare; tens of ms; off the hot path (runs post-upload, per ingest). Acceptable vs. the robustness gain. |

---

## 8. Out of scope
- A backend **batch document-status endpoint** (`GET …/documents?ids=…`) — the
  cleanest long-term answer to G2, but a server change. Per-id GETs suffice now; note
  as the next optimization.
- Relaxing the Send-gate (G2-of-the-prior-plan / D2 there) — still deferred.
- Push/SSE status streaming; OCR; the doc→MDL hot path.

---

## 9. Test plan

**Frontend**
- `useDocumentIngestion.test` (G3): an MDL-shaped JSON of ~2 MB now **fires** the
  notice (was silent under the 1 MB cap); a >10 MB file stays silent.
- `index.test` (G2): the live-tree poll calls **`getSemanticDocument`** for the
  pending doc (not the list) and the tree advances; a settled list isn't polled.
- `CopilotPanel.test` (G1/R3) — **re-point** the poll tests:
  - the composer makes **no** `getSemanticDocument` call (assert the mock is unused);
  - updating the `documents` prop from `extracting`→`extracted` advances the chip and
    enables Send (R3);
  - with `documentsPollGaveUp` prop true while a doc is pending → Send enabled, chip
    shows the **status-aware** give-up cue + note (G4/R5);
  - on send, the inline payload uses the `documents`-sourced `extracted_text` (R3).

---

## 10. Sequential checklist (blockers & dependencies)

> Top-to-bottom. `[ ]` todo · `[x]` done.

### Phase 0 — Verify anchors (no code)
- [ ] Re-grep T1–T9; confirm both polls + `attachPollGaveUp` + the list
      `response_model` (incl. `extracted_text`) unchanged. **Blocker:** none.

### Phase 1 — MDL sniff cap (G3)
> **Blocker:** P0. **Independent** (hook only). **Unblocks:** nothing.
- [ ] T9 raise `MDL_SNIFF_MAX_BYTES` to 10 MB.
- [ ] `useDocumentIngestion.test`: ~2 MB MDL JSON fires; >10 MB stays silent.

### Phase 2 — Pending-only editor poll (G2)
> **Blocker:** P0. **Independent of the composer** (`index.tsx` only). **Unblocks:** P3 (provides per-doc fresh data + give-up flag).
- [ ] T1 poll → per-pending `getSemanticDocument`, patch by id (change-guarded);
      import `getSemanticDocument`.
- [ ] T2 add `documentsPollGaveUp` state (set on cap, reset on re-arm).
- [ ] `index.test`: update the live-tree test to mock the single-doc GET.

### Phase 3 — Consolidate the composer onto the shared poll (G1) + give-up wording (G4)
> **Blocker:** P2 (editor must provide `documents` with fresh `extracted_text` + the
> give-up flag). **Dependency:** **re-point** the CopilotPanel poll tests (mechanism,
> not behavior).
- [ ] T3 pass `documents` + `documentsPollGaveUp` into `<CopilotPanel>`.
- [ ] T4 add the two props.
- [ ] T5 remove the chip poll effect, `ATTACH_POLL_*`, `attachPollGaveUp`, the
      `getSemanticDocument` import.
- [ ] T6 add the `documents`→`attachedDocs` sync effect (merge by id).
- [ ] T7 swap give-up signal to the prop in gate/chip/note.
- [ ] T8 status-aware give-up wording.
- [ ] Re-point `CopilotPanel.test` poll cases (§9); assert the composer makes no
      `getSemanticDocument` call.

### Phase 4 — Verify & polish
> **Blocker:** P1–P3.
- [ ] Touched suites green; `tsc --noEmit` clean on touched files; `prettier` clean
      (`oxlint`/`eslint` in CI). The 2 pre-existing `AiAgentPanel` failures
      (prior plan §12 #7) remain untouched.
- [ ] Manual QA (needs live agent + embedder): open Copilot + attach a >1 MB file →
      exactly **one** poller hits the network (verify in devtools); tree + chip both
      advance; let it time out → status-aware give-up cue; attach a 2 MB MDL `.json` →
      notice fires.
- [ ] Update [`plan_attach_tree_gate_json_followups.md` §11](plan_attach_tree_gate_json_followups.md)
      (mark these gaps closed) + the `document-rag-suite` memory; note the batch
      endpoint as the remaining optimization.

---

**Sources:**
in-repo editor doc poll ([`SemanticLayerEditor/index.tsx:770-809`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)) ·
in-repo composer chip poll ([`CopilotPanel.tsx:291-338`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx)) ·
list `response_model` incl. `extracted_text` ([`app.py:3072`](app.py), [`schemas.py:106`](semantic_layer/schemas.py)) ·
[Unstructured — RAG pipeline best practices](https://unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline) ·
[Open WebUI — Knowledge (async ingest + status)](https://docs.openwebui.com/features/workspace/knowledge/)
