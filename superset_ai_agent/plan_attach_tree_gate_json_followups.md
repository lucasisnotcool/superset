# Live Tree, Give-up Cue & Precise JSON Notice — Follow-up Plan

> **Status:** SPEC / proposal. **Not implemented.** Closes three residual gaps from
> [`plan_attach_grounding_ux_followups.md` §11](plan_attach_grounding_ux_followups.md):
> - **G1 — tree isn't live:** a doc added via the **Upload document** button shows a
>   stale status in the workspace tree until the next manual refresh.
> - **G3 — no give-up cue:** after the ~3-min poll cap, Send re-enables while the
>   chip still reads *Extracting…* (ambiguous).
> - **G4 — heuristic JSON notice:** a legitimate `.json` *data* file triggers the
>   "to create an MDL model…" toast.
>
> **G2 (relax the multi-attach Send-gate) is DEFERRED by decision.** It is a product
> pivot — unblocking Send trades away guaranteed first-turn grounding for a
> just-attached large file (see **D2**). The current all-or-nothing gate is **kept
> unchanged**; G2 is documented here (§4 D2) but **not implemented**. A welcome
> side effect: the prior gate tests stay valid — **no test rewrite** is needed.
>
> All `file:line` anchors verified at authoring time — re-grep if the tree moved.
> Written to be used as a working checklist (§10) by a future agent session.

---

## 1. Requirements (testable)

| # | Requirement |
| --- | --- |
| **R1** | A still-extracting document appears in the workspace tree with a status that advances to terminal **without a manual refresh**, whether it was added via Copilot **Attach** or the **Upload document** button (G1). |
| **R2** | *(DEFERRED — G2)* Relaxing the Send-gate so the composer is not blocked by pending attachments. **Not in this plan's scope** (D2). The existing all-or-nothing gate is retained. |
| **R3** | When the status poll gives up on a still-pending attachment, its chip shows a **distinct "still processing in the background"** cue (not a perpetual *Extracting…*), and the composer explains that Send was re-enabled while extraction continues (G3). |
| **R4** | The "stored as a document / author MDL in the editor" notice fires **only when an ingested JSON file is MDL-shaped**, not for arbitrary JSON data files (G4). |
| **R5** | *(non-functional)* Bounded + cancel-safe polling; reuse `isPendingDocumentStatus` + the onboarding-poller shape; **no backend change**; no `any`; `@superset-ui/core` only; degrade-closed. |

---

## 2. Current state (source-backed)

- **The tree is built from editor `documents` state**, which is fetched once per
  `refresh()` and never polled:
  `documents` state at [`index.tsx:284`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx);
  `refresh` calls `listSemanticDocuments(scope)` → `setDocuments`
  ([`index.tsx:373,396,403`]); the tree is `treeFromFiles(mdlFiles, documents)`
  and `WorkspaceTree` renders each doc's `status`
  ([`WorkspaceTree.tsx:48,101-112`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/WorkspaceTree.tsx),
  status badge via `getDocumentStatusMeta`). So a large file uploaded via the
  button sits at `extracting` in the tree until something calls `refresh` again.
- **A proven poll shape already lives in the editor** — the onboarding poller
  ([`index.tsx:241-242,698-741`]): `setTimeout` loop, `cancelled` cleanup,
  `attemptsLeft` cap, transient-error tolerance. Reuse it.
- **The composer Send-gate is all-or-nothing.** `attachmentBlocksSend =
  pendingAttachments.length > 0 && !attachPollGaveUp`
  ([`CopilotPanel.tsx:362-367`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx)),
  fed into the Send `disabled` and a `handleSend` guard ([:371,1077]) with a
  "Waiting for %s…" tooltip ([:1064-1065]).
- **`attachmentsForSend` inlines every staged doc** regardless of readiness
  ([`CopilotPanel.tsx:346`]) — fine today only because the gate guarantees they're
  all `extracted` first.
- **The chip label is `getDocumentStatusMeta(doc.status)`**, shown while pending or
  attention ([`CopilotPanel.tsx:987,1003`]). It has no notion of "the poll gave
  up", so a timed-out doc keeps showing *Extracting…*.
- **The JSON notice is content-blind.** `isJsonDocument` matches on
  `content_type`/filename only ([`useDocumentIngestion.ts:30`](../superset-frontend/src/SqlLab/components/AiAgentPanel/useDocumentIngestion.ts)),
  and the notice fires for any JSON ([:118-122]). The hook's `ingest(files)`
  already has the raw `File[]` ([:68,76]) — so a content sniff is possible without
  a new fetch.

---

## 3. Best practices (industry standard)

- **Poll list-to-terminal for async jobs** (same lifecycle the chip poll already
  uses): the tree is just another view of the same `extracting→extracted` state.
- **Non-blocking optimistic send with per-item readiness** — chat UIs attach what's
  ready and reconcile the rest, rather than freezing the composer (Slack/Notion
  file-attach behavior). Pairs with a clear "still processing" affordance.
- **Distinct terminal-vs-timeout states** — a job that exceeded its watch budget
  should not look identical to one still actively progressing.
- **Classify by content, not extension** — MIME/extension is a hint; a cheap
  shape sniff (top-level keys) is the standard way to avoid false positives on a
  generic `.json`.

---

## 4. Decision points

| # | Decision | Recommendation | Rationale / trade |
| --- | --- | --- | --- |
| **D1** | Where does the tree poll live & what does it fetch? | **Editor-level effect** polling `listSemanticDocuments(scope)` while `documents.some(isPendingDocumentStatus)`. | One poll makes the tree live for **both** ingress paths (both land in `documents`). Reuses the onboarding-poller shape + `isPendingDocumentStatus`. Alternative (per-doc `getSemanticDocument`) duplicates the composer poll and needs id bookkeeping; list-refetch is simpler and matches `refresh`. |
| **D2** | Relax the Send gate (G2)? | **DEFERRED — keep the gate unchanged.** | A product pivot: unblocking Send would **supersede** the prior gate-based closure of the large-file grounding race (a just-attached large file would no longer be guaranteed grounded on the first turn). Deferred by decision; documented for a future revisit. Retaining the gate also keeps the prior gate tests valid (no rewrite). When revisited: remove the gate, inline ready docs only, exclude+notice pending. |
| **D3** | Give-up cue (with the gate retained) | **When `attachPollGaveUp` and a chip is still pending: (a) show a distinct warning label** (*Still processing in the background*) instead of *Extracting…*, **and (b) show a one-line composer note** explaining Send is now usable while extraction continues (it'll ground later turns). | With the gate kept, the gate already *re-enables* Send on give-up; the chip + note remove the ambiguity of "why is Send active while it still says Extracting?" Pure component state — no new mechanism. |
| **D4** | JSON precision | **Sniff MDL shape: for `.json` files under a size cap (1 MB), `JSON.parse` and require an MDL top-level key (`models`/`relationships`/`views`/`dataSource`/`enumDefinitions`/`metrics`).** Notice fires only on a match. | Precise + bounded (data JSON over the cap, or lacking MDL keys, is silent). Alternatives: prefix-regex (cheaper, less robust); neutral reword (loses the "where did MDL import go" guidance). |

---

## 5. Entrypoints & touchpoints

| # | File:symbol | Change |
| --- | --- | --- |
| T1 | [`index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) — new constants + effect | Add `DOCUMENT_POLL_INTERVAL_MS` (2000) + `DOCUMENT_POLL_MAX_ATTEMPTS` (~120). Add an effect: while `documents.some(isPendingDocumentStatus)`, every interval refetch `listSemanticDocuments(scope)` and `setDocuments` **change-guarded** (only when a status map differs); bounded + `cancelled` cleanup, mirroring the onboarding poller ([:698-741]). Import `isPendingDocumentStatus`. (G1/R1) |
| ~~T2~~ | `CopilotPanel.tsx` `attachmentsForSend` | **DEFERRED (G2)** — ready-only inline filter. Not changed; the gate still guarantees all staged docs are `extracted` before send. |
| ~~T3~~ | `CopilotPanel.tsx` Send gate | **DEFERRED (G2)** — gate removal. `attachmentBlocksSend`, the `handleSend` guard, and the "Waiting for…" tooltip are **kept as-is**. |
| T4 | `CopilotPanel.tsx` — give-up note | When `attachPollGaveUp && pendingAttachments.length > 0`, render a persistent `Typography.Text type="secondary"` note under the chips: extraction is still running in the background, you can send now, and the file(s) (named) will be available to later turns. **Only on give-up** (the normal pending case stays gated). (G3/R3) |
| T5 | `CopilotPanel.tsx:1003,987` chip label | When `attachPollGaveUp && isPendingDocumentStatus(doc.status)`, show a distinct warning label (*Still processing…*) instead of `getDocumentStatusMeta` *Extracting…*. (G3/R3) |
| T6 | [`useDocumentIngestion.ts:30,118`](../superset-frontend/src/SqlLab/components/AiAgentPanel/useDocumentIngestion.ts) | Replace `isJsonDocument(document)` with a content sniff over the original `File`: `isLikelyMdlJson(file)` (size-capped `JSON.parse` + MDL top-level key check). Trigger the notice from the per-file ingest loop where the `File` is in scope, collecting a single "any MDL json?" flag; emit one toast after the loop (still once per ingest). (G4/R4) |
| T7 | `documentStatus.tsx` *(optional)* | If the give-up label is reused elsewhere, add a tiny helper; otherwise inline it in CopilotPanel (T5). Default: **inline**, no new export. |

**Backend:** none (R5).

---

## 6. Design notes

- **T1 change-guard (avoid tree churn):** compute `next = await listSemanticDocuments(scope)`; only `setDocuments` when the `{id: status}` map differs from current, so an unchanged poll doesn't re-render the tree or re-arm the effect. Mirror the editor's existing ref-guard discipline.
- **Double-poll overlap (composer chip vs tree):** both are bounded and independent (different surfaces). Accept for now; a future consolidation could have the editor own a single document-status poll and pass status down to the composer. Note, don't build.
- **Give-up note (T4) is scoped to give-up only:** while a doc is *normally* extracting, the retained gate keeps Send disabled (the "Waiting for…" tooltip already explains it). The note appears only once the poll has given up and Send has re-enabled — exactly the ambiguous window G3 targets.
- **T6 sniff bound:** skip files ≥ 1 MB (treat as data); for the rest, `try { JSON.parse(await file.text()) }` and check `typeof parsed === 'object' && parsed && (MDL_KEYS.some(k => k in parsed))`. Any parse error → not MDL → no notice.

---

## 7. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| **Two polls (tree + chip) for the same docs.** | Both bounded (interval + cap) and tiny; independent surfaces. Note as a consolidation seam; don't over-engineer. |
| **Tree poll re-render churn / effect re-arm loop.** | Change-guard `setDocuments` by status-map equality (T1, §6). Covered by a "settled tree is not refetched into a new array" test. |
| **JSON parse cost on a large data file.** | 1 MB size cap before `JSON.parse` (T6/D4); larger → silent. |
| **MDL sniff false negative** (unusual MDL lacking the listed keys). | User merely doesn't get the guidance notice; harmless. Keys list covers Wren MDL top-level shape. |
| **Prior gate tests stay valid (G2 deferred).** | Because the gate is **unchanged**, the earlier Phase-2 gate tests (*Send disabled while extracting*, *give-up re-enables*) keep passing — **no rewrite**. Only **add** a G3 chip-cue/note assertion. |
| **Onboarding poll + document poll running together.** | Independent effects, both in the editor, both bounded; no shared state mutation conflict (`setDocuments` vs onboarding's job state). |

---

## 8. Out of scope
- **G2 — relaxing the Send-gate (DEFERRED, D2).** The all-or-nothing gate is kept;
  the ready-only inline filter, gate removal, and exclude-pending notice are
  documented (D2, ~~T2~~/~~T3~~) but not built in this plan.
- Consolidating the tree poll and the composer chip poll into one source of truth.
- A WebSocket/SSE push for document status (polling is the established pattern).
- Backend changes; OCR; cosine near-dup; the doc→MDL hot path.
- Per-attachment "remove only the pending ones" controls (the chip's existing close button already removes individually).

---

## 9. Test plan

**Frontend**
- `index.test` (G1): a `documents` entry returned `extracting` then `extracted` on
  the 2nd `listSemanticDocuments` call advances in the tree without a manual
  refresh; a fully-terminal list is **not** refetched into a new array (guard).
- `CopilotPanel.test` (G3) — **add** (do not rewrite; the gate is unchanged):
  - `attachPollGaveUp` while pending → chip shows the distinct *Still processing*
    cue, not *Extracting…*, **and** the give-up note is rendered (R3);
  - the prior gate tests (*Send disabled while extracting*, *give-up re-enables
    Send*) remain and must still pass.
- `useDocumentIngestion.test` (G4): MDL-shaped `.json` (has `models`) → one notice;
  data `.json` (`{"rows":[…]}`) → **no** notice; `.json` ≥ 1 MB → no notice
  (R4). Keep the "non-JSON → no notice" case.

---

## 10. Sequential checklist (blockers & dependencies)

> Top-to-bottom. `[ ]` todo · `[x]` done. The three feature phases are
> independent (different files); do them in any order. **G2 is deferred**, so no
> prior test needs rewriting.

### Phase 0 — Verify anchors (no code) ✅ DONE
- [x] Re-grepped T1/T4/T5/T6; onboarding poller + `attachmentsForSend` +
      `isJsonDocument` shapes confirmed.

### Phase 1 — Precise JSON notice (G4) ✅ DONE
- [x] T6 `isLikelyMdlJson(file)` (1 MB-capped `JSON.parse` + MDL top-level key
      check); notice fires once per ingest only when matched. Reworded copy
      ("This looks like an MDL file…").
- [x] `useDocumentIngestion.test` — rewrote the positive cases to MDL-shaped JSON +
      added data-JSON (no notice) + oversized (no notice) cases. **9 passed.**
      (Note: jsdom doesn't implement `File.text()/.size` — tests stub them.)

### Phase 2 — Live workspace tree (G1) ✅ DONE
- [x] T1 document-status poll in `index.tsx` (bounded 2 s × 120, cancel-safe,
      change-guarded via `documentStatusSignature`); imports
      `isPendingDocumentStatus`. Covers both ingress paths (both land in
      `documents`).
- [x] `index.test` — a doc returned `extracting` then `extracted` advances in the
      tree with no manual refresh. **13 passed.**

### Phase 3 — Give-up cue (G3) — gate retained ✅ DONE
- [x] T5 chip shows *Still processing in the background* when `attachPollGaveUp`
      and the doc is still pending (replaces the misleading *Extracting…*).
- [x] T4 give-up note under the chips (only when `attachPollGaveUp`).
- [x] Updated the existing give-up test to assert the new cue + note; **no gate
      rewrite** (gate unchanged). **CopilotPanel.test 20 passed.**

### Phase 4 — Verify & polish ✅ DONE
- [x] Touched suites green (hook 9, documentStatus 3, CopilotPanel 20, editor index
      13 = **45**); `tsc --noEmit` clean on touched files; `prettier` clean
      (`oxlint`/`eslint` in CI). The 2 pre-existing `AiAgentPanel` failures
      (prior plan §12 #7) remain untouched.
- [ ] Manual QA (needs live agent + embedder): upload a >1 MB file via the button →
      tree status advances live (G1); let a poll time out → chip shows the
      *Still processing* cue + the give-up note appears (G3); attach a data `.json`
      → **no** MDL notice, attach an MDL `.json` → notice (G4).
- [x] Updated [`plan_attach_grounding_ux_followups.md` §11](plan_attach_grounding_ux_followups.md)
      (G1/G3/G4 closed; G2 deferred) + the `document-rag-suite` memory.

---

## 11. As-built notes — residual risks & UX gaps

Implemented and test-green (45 touched-suite tests). Honest gaps:

1. **G2 still open by decision.** The all-or-nothing Send-gate is unchanged; with
   multiple attachments, Send stays disabled until **all** settle (or the poll
   gives up). Revisit per D2 if/when first-turn-grounding is traded for
   responsiveness.
2. **Two independent polls** (editor tree `listSemanticDocuments` + composer chip
   `getSemanticDocument`) can run at once for the same doc when the Copilot is
   open *and* the tree has a pending doc. Both bounded; minor redundant GETs.
   Consolidation seam noted, not built.
3. **Tree poll resets its cap on each status change** (effect depends on
   `documents`), so the ~4-min cap is "max attempts *without progress*", not a hard
   wall-clock — correct for hang-detection, same as the composer poll.
4. **Tree poll re-fetches the whole list** each tick (not a single-doc GET). Fine at
   expected scale; a large document set makes each poll heavier than the composer's
   per-doc fetch.
5. **MDL sniff reads file text client-side** for `.json` under 1 MB. A 1 MB
   *MDL-shaped* file that a user genuinely meant as a model gets **no** notice
   (size cap). Acceptable: real MDL models are small; the cap protects against
   parsing multi-MB data JSON.
6. **Give-up cue wording is generic.** The chip says *Still processing in the
   background* for any pending status at give-up (covers `extracting`/`uploaded`);
   it doesn't distinguish *why* it's slow. Good enough for the ambiguity G3 targets.
7. **Manual/visual QA not performed** — no live agent + embedder this session. The
   live-tree transition, give-up cue, and MDL sniff are covered by unit/integration
   tests (real timers for the tree poll; fake timers for the chip poll; stubbed
   `File.text()` for the sniff), not observed by eye.

---

**Sources:**
[Open WebUI — Knowledge (async ingest + status)](https://docs.openwebui.com/features/workspace/knowledge/) ·
[Unstructured — RAG pipeline best practices](https://unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline) ·
in-repo onboarding poller ([`SemanticLayerEditor/index.tsx:698-741`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)) ·
in-repo composer chip poll ([`CopilotPanel.tsx:289`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx))
