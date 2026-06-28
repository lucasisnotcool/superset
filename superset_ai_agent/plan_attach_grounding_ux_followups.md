# Attach Grounding & Status UX — Follow-up Plan

> **Status:** SPEC / proposal. **Not implemented.** Closes the three interaction
> gaps left open in
> [`plan_unified_attach_ingestion_spec.md` §12](plan_unified_attach_ingestion_spec.md)
> after the attach/upload unification shipped:
> - **#2 — live attach status chip** (chip is a snapshot today)
> - **#3 — large-file inline-grounding race** (>1 MB docs extract async; the first
>   turn after attaching may carry empty inline text)
> - **#4 — dropped UI MDL-JSON import** needs user-facing copy (silent today)
>
> All `file:line` anchors verified at authoring time — re-grep if the tree moved.
> Designed to be picked up by a future agent session as a working checklist (§10).

---

## 1. Requirements (testable)

| # | Requirement |
| --- | --- |
| **R1** | While a freshly attached document is in a **pending** status (`uploaded`/`extracting`), its composer chip reflects the current status and transitions to the terminal label (`Extracted`/`Needs OCR`/`Error`) **without a manual refresh**. |
| **R2** | **Send is disabled while any attached document is pending**, with a tooltip naming the file(s); it re-enables when every attached doc reaches a terminal status **or** the poll gives up (so a hung/failed extraction never permanently blocks the composer). |
| **R3** | On Send, inline grounding uses the **latest** `extracted_text` (a large doc that finished extracting is grounded); a doc with no extractable text contributes no inline text (degrade-closed — RAG still holds its chunks). |
| **R4** | Attaching/uploading a JSON (MDL-type) file surfaces a **one-time** informational notice that it is stored as a document and MDL authoring is via the editor/Copilot; a release/`UPDATING`-style note records the dropped UI MDL-JSON import (#4). |
| **R5** | Polling is **bounded** (interval + max attempts), **cancels** on unmount / new-chat / chip removal, tolerates transient fetch failures, and never blocks the UI thread. |
| **R6** | *(non-functional)* **No backend changes** — reuse `getSemanticDocument`; mirror the existing onboarding-poll pattern; no `any`; `@superset-ui/core` components only. |

---

## 2. Current state (source-backed)

- **Chip is a one-shot snapshot.** `attachedDocs: SemanticDocument[]` is set once
  from the upload response; the chip renders `getDocumentStatusMeta(doc.status)`
  and never updates ([`CopilotPanel.tsx:136,898-899`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx)).
  Nothing re-fetches the doc.
- **Large files extract async.** Over `wren_document_async_threshold_bytes`
  (1 MB, [`config.py:133`](config.py)) the upload route sets `status="extracting"`
  and extracts on a background thread; the response row has **no**
  `extracted_text` yet ([`app.py:2438-2450`](app.py)).
- **Inline grounding reads the snapshot.** `attachmentsForSend` maps
  `doc.extracted_text` ([`CopilotPanel.tsx:269-280`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx));
  for a still-extracting large doc this is empty → the **first** turn isn't
  grounded inline (RAG covers later turns).
- **A single-doc getter already exists.** `getSemanticDocument(documentId)` →
  `GET …/documents/{id}` ([`api.ts:1785`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts)).
  No new endpoint needed.
- **Status semantics live in one place.** `getDocumentStatusMeta(status)` +
  `DocumentStatusTag` ([`documentStatus.tsx:54,108`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/documentStatus.tsx)).
  **Pending** = `uploaded`/`extracting`; **terminal** = `extracted`/`needs_ocr`/
  `error` (+ legacy `indexed`/`approved`). There is **no** `isPending` helper yet.
- **A proven poll pattern exists.** The onboarding poller in the editor —
  `setTimeout` loop, `cancelled` cleanup flag, `attemptsLeft` cap, transient-error
  tolerance ([`index.tsx:240-241,619-690`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)).
  Mirror it; do not invent a new shape.
- **MDL-JSON import is silently a document.** Per decision D1-A of the prior plan,
  attaching `.json` makes a `raw/` document, not an MDL model. The
  attach/upload tooltips mention the formats but nothing tells a user who *expected*
  MDL import where that capability went.

---

## 3. Best practices (industry standard)

- **Poll-to-terminal on async ingestion.** Long-running extract/index jobs expose
  a status the client polls until a terminal state — exactly the
  `uploaded→extracting→extracted` lifecycle already modeled. (Open WebUI, Unstructured
  ingestion pipelines.)
- **Optimistic stage + reconcile by id.** Stage the item immediately, then patch it
  from authoritative server state keyed by id — what the onboarding poller does.
- **Degrade-closed grounding.** Never hard-fail a turn because extraction is slow;
  prefer "wait if cheap, else proceed and let retrieval catch up."
- **Announce removed capabilities at the point of action + in release notes.** A
  silently dropped path is a support burden; a one-time inline notice plus a
  changelog entry is the standard mitigation.

---

## 4. Decision points

| # | Decision | Recommendation | Rationale |
| --- | --- | --- | --- |
| **D1** | Block Send while a doc is extracting? | **Yes — disable Send while any attached doc is pending**, re-enable on terminal **or** poll-giveup. | Guarantees first-turn grounding for the common case without an indefinite block; small files (already `extracted`) never block, so zero regression. Alternative (send-anyway) reintroduces the exact #3 gap. |
| **D2** | How to surface the dropped MDL-JSON import (#4)? | **One-time info toast on JSON ingest** *(in the shared hook)* **+ a note in `superset_ai_agent/README.md` / release copy.** | Discoverable at the moment of action and recorded for operators. Toast fires once per ingest call (not per file) to avoid spam. |
| **D3** | Where does the poll live? | **CopilotPanel-local**, over its staged `attachedDocs`. | The chips are CopilotPanel state; the editor tree is a separate surface (D4). Keeps the poll lifecycle tied to the composer. |
| **D4** | Also live-update the **tree** for large Upload-button files? | **Defer (out of primary scope).** | #2 is specifically the attach chip. The tree already shows status on `refresh`, and the detail pane shows live status. Note as an optional extension, don't build now. |
| **D5** | Home for the pending/terminal helper? | **Add `isPendingDocumentStatus(status)` to `documentStatus.tsx`.** | Centralizes status semantics beside `getDocumentStatusMeta`; reused by the poll stop-condition and the Send gate. |
| **D6** | Poll interval / cap? | **1500 ms interval, ~120 attempts (~3 min) cap.** | Extraction is far faster than onboarding (which uses 2 s × 450 ≈ 15 min); 3 min is generous for a ≤10 MB doc. On cap, treat as terminal (R2). |

No open decisions block implementation; each is resolved against an existing
pattern or the cited best practice.

---

## 5. Entrypoints & touchpoints

| # | File:symbol | Change |
| --- | --- | --- |
| T1 | [`documentStatus.tsx:54`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/documentStatus.tsx) | **Add** `export const isPendingDocumentStatus = (status: string): boolean` → `status === 'uploaded' || status === 'extracting'`. (D5) |
| T2 | [`CopilotPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) imports | Import `getSemanticDocument` from `../api`; `isPendingDocumentStatus` from `./documentStatus`. |
| T3 | `CopilotPanel.tsx` — **new poll effect** | While `attachedDocs.some(isPendingDocumentStatus)`, every D6-interval `getSemanticDocument(id)` for each pending doc and reconcile into `attachedDocs` by id; stop when none pending or cap hit; `cancelled` cleanup; drop a doc that 404s. Mirror the onboarding poller ([`index.tsx:619-690`]). (R1/R3/R5) |
| T4 | [`CopilotPanel.tsx:949-958`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) Send button | Compute `const pendingAttachment = attachedDocs.some(isPendingDocumentStatus)`; add to `disabled`; wrap in a `Tooltip` naming the pending file(s). (R2) |
| T5 | `CopilotPanel.tsx` chip render ([:898]) | Already uses `getDocumentStatusMeta`; show the label for **pending** states too (currently only `attention` states show — `uploaded` is quiet). Decision: show a hint whenever `isPendingDocumentStatus(doc.status)` so the user sees progress. |
| T6 | [`useDocumentIngestion.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/useDocumentIngestion.ts) | After collecting results, if any ingested doc is JSON (`content_type` includes `json` or filename ends `.json`), dispatch **one** `addInfoToast` (D2/R4). |
| T7 | [`superset_ai_agent/README.md`](README.md) (+ release copy) | Note: UI MDL-JSON import was removed; `.json` is ingested as a document; author MDL via the editor/Copilot. (R4) |

**Backend:** none (R6). `getSemanticDocument` is authorized + scope-scoped already.

---

## 6. Frontend design notes

**Poll (T3), mirroring the onboarding poller:**
```ts
useEffect(() => {
  const pending = attachedDocs.filter(d => isPendingDocumentStatus(d.status));
  if (!pending.length) return undefined;
  let cancelled = false;
  let attemptsLeft = ATTACH_POLL_MAX_ATTEMPTS;     // D6
  let timer: ReturnType<typeof setTimeout>;
  const poll = async () => {
    const fresh = await Promise.all(
      pending.map(d => getSemanticDocument(d.id).catch(() => null)),
    );
    if (cancelled) return;
    setAttachedDocs(prev =>
      prev.map(d => fresh.find(f => f?.id === d.id) ?? d),
    );
    attemptsLeft -= 1;
    const stillPending = fresh.some(f => f && isPendingDocumentStatus(f.status));
    if (stillPending && attemptsLeft > 0) {
      timer = setTimeout(poll, ATTACH_POLL_INTERVAL_MS);
    }
    // attemptsLeft === 0: stop; the doc keeps its last status (still "pending"
    // would re-arm Send via R2's give-up clause — see T4 note below).
  };
  timer = setTimeout(poll, ATTACH_POLL_INTERVAL_MS);
  return () => { cancelled = true; clearTimeout(timer); };
}, [attachedDocs]);
```
> **T4 give-up nuance:** "re-enable on poll-giveup" (R2) means the Send gate must
> not rely on `isPendingDocumentStatus` *forever*. Track a `pollExhausted` flag (or
> a per-doc "gave up" set) so that once the cap is hit, the gate stops blocking even
> if the status is still `extracting`. Simplest: a `attachPollGaveUp` boolean state
> set true when `attemptsLeft` reaches 0; Send disabled = `pendingAttachment && !attachPollGaveUp`.
> Reset it whenever `attachedDocs` changes (new attach).

> **Effect-dependency caution:** depending on the whole `attachedDocs` array
> re-arms the effect every time the poll patches state. That's acceptable (each run
> recomputes `pending` and stops when empty), but guard against a tight loop: only
> `setAttachedDocs` when something actually changed (compare status/updated_at), or
> the effect will reschedule needlessly. Mirror the editor's ref-based guard if
> churn shows up in tests.

**JSON notice (T6):** emit in the hook so Attach *and* Upload behave identically
(R3 parity from the prior plan). One toast per `ingest()` call.

---

## 7. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| **setState after unmount / poll leak.** | `cancelled` flag + `clearTimeout` in cleanup (mirrors onboarding poller). |
| **Send permanently disabled if extraction hangs/fails.** | Cap + `attachPollGaveUp` (T4); `error`/`needs_ocr` are terminal → not pending → Send enabled. |
| **Tight re-render loop** from array-dep effect. | Only `setAttachedDocs` on real change; optional ref guard (§6). Covered by a "does not refetch a settled doc" test. |
| **Doc deleted elsewhere mid-poll (404).** | `.catch(() => null)` keeps the prior row; optionally drop on repeated 404. Never throws. |
| **Chip removed while polling.** | `pending` recomputed from current `attachedDocs` each run; removed id isn't refetched. |
| **Toast spam on multi-file JSON.** | One info toast per `ingest()` call, not per file (T6). |
| **Over-blocking the common case.** | Small files arrive `extracted` → never pending → Send never blocks. Only >1 MB async docs gate. |
| **Timer flakiness in tests.** | Use jest fake timers or resolve `getSemanticDocument` straight to a terminal status; assert chip transition + Send enable/disable. |

---

## 8. Out of scope
- Live-updating the **workspace tree** for Upload-button large files (D4 — optional extension).
- Client-side type/size pre-validation (§12 gap #1 — separate follow-up).
- OCR for `needs_ocr` docs; cosine near-dup; the doc→MDL hot path.
- Any backend change (R6).

---

## 9. Test plan

**Frontend**
- `documentStatus.test`: `isPendingDocumentStatus` true for `uploaded`/`extracting`,
  false for `extracted`/`needs_ocr`/`error`/legacy.
- `CopilotPanel.test`:
  - poll transitions a chip `extracting → extracted` without manual refresh (R1);
  - Send disabled while a doc is `extracting`, enabled once `extracted` (R2);
  - Send re-enables after the poll cap is hit while still extracting (R2 give-up);
  - on Send, the inline `MessageAttachment.text` carries the **post-extraction**
    `extracted_text` (R3);
  - settled (`extracted`) attachment is **not** re-fetched (loop guard).
- `useDocumentIngestion.test`: a `.json` ingest fires exactly one info toast; a
  non-JSON ingest fires none (R4).

---

## 10. Sequential checklist (blockers & dependencies)

> Top-to-bottom. `[ ]` todo · `[x]` done.

### Phase 0 — Verify anchors (no code) ✅ DONE
- [x] Re-grep T1–T7 anchors; confirmed `getSemanticDocument` + onboarding poller
      shapes unchanged.

### Phase 1 — Status helper ✅ DONE
- [x] T1 `isPendingDocumentStatus` in `documentStatus.tsx` + `documentStatus.test.ts`
      (3 tests). Green.

### Phase 2 — Live chip + grounding poll (#2, #3) ✅ DONE
- [x] T2 imports (`getSemanticDocument`, `isPendingDocumentStatus`).
- [x] T3 poll effect — reconcile by id, change-guarded (stable identity when
      unchanged), bounded (1500 ms × 120), cancel-safe (R1/R3/R5).
- [x] T5 chip shows the live status label while pending.
- [x] T4 Send gate (`attachmentBlocksSend`) + `attachPollGaveUp` give-up + tooltip
      naming the pending file(s); `handleSend` also guards (Enter bypasses
      `disabled`). Reset on attach / send / new-chat.
- [x] `CopilotPanel.test` — 4 new cases (poll→extracted+ungate, loop-guard,
      give-up re-enable, fresh-text-on-send). Suite **20 passed**.

### Phase 3 — MDL-JSON notice (#4) ✅ DONE
- [x] T6 one-time JSON info toast in `useDocumentIngestion` (once per ingest, not
      per file).
- [x] T7 `README.md` "Document Ingestion (MDL Copilot)" section + removed-capability
      note (release copy).
- [x] `useDocumentIngestion.test` — 3 new cases (JSON notice once / once-for-many /
      none-for-non-JSON). Suite **7 passed**.

### Phase 4 — Verify & polish ✅ DONE
- [x] Touched suites green (documentStatus 3, useDocumentIngestion 7, CopilotPanel
      20, editor index 12 = **42**); `tsc --noEmit` clean on touched files;
      `prettier` clean. (`oxlint`/`eslint` in CI — no local config.) The 2
      pre-existing `AiAgentPanel` failures (prior plan §12 #7) are untouched.
- [ ] **Manual QA — NOT run** (needs live agent + embedder): attach a >1 MB PDF →
      chip *Extracting…*, Send disabled → flips to *Extracted*, Send enables,
      turn grounded; attach `.json` → info toast, lands in `raw/`. **See §11 gaps.**
- [x] Updated [`plan_unified_attach_ingestion_spec.md` §12](plan_unified_attach_ingestion_spec.md)
      (gaps #2/#3/#4 marked closed) + the `document-rag-suite` memory.

---

## 11. As-built notes — residual risks & UX expectation gaps

Implemented and test-green (42 touched-suite tests). Honest gaps between code and
user expectation:

1. **✅ CLOSED — workspace tree is now live.** An editor-level document-status poll
   ([`plan_attach_tree_gate_json_followups.md`](plan_attach_tree_gate_json_followups.md)
   G1/T1) advances the tree badge for **both** ingress paths without a manual
   refresh.
2. **DEFERRED (G2) — Send-gate vs. multi-attach timing.** Kept all-or-nothing by
   decision; revisit per that plan's D2 (trades guaranteed first-turn grounding for
   responsiveness).
3. **✅ CLOSED — give-up cue.** The chip now shows *Still processing in the
   background* (not a perpetual *Extracting…*) and a note explains Send is usable
   while extraction continues (G3/T4/T5). The ~3-min window itself is unchanged
   (tune `ATTACH_POLL_MAX_ATTEMPTS` if needed).
4. **Poll cost.** One `getSemanticDocument` per pending doc per 1500 ms per open
   Copilot. Bounded and tiny, but a deployment that attaches many large docs at
   once multiplies the GETs. No batched "get-many" endpoint exists; per-doc is fine
   at expected scale.
5. **Effect re-arm on each status change.** The poll effect depends on
   `attachedDocs`; a real status change re-runs it and resets `attemptsLeft`. So the
   cap is "max attempts *without progress*," not a hard wall-clock — correct for
   hang-detection, but worth knowing when reading the give-up test.
6. **✅ CLOSED — JSON notice precision.** Now sniffs MDL shape (size-capped parse +
   top-level key check); a legitimate `.json` data file gets **no** notice (G4/T6).
7. **Manual/visual QA not performed** — no live agent + embedder in this session;
   the async-extraction → live-chip → grounded-send loop is covered by unit tests
   with fake timers + mocked `getSemanticDocument`, not by eye. The real backend's
   `extracting → extracted` transition timing is assumed, not observed.
8. **Enter-to-send is guarded in `handleSend`** (the TextArea `onPressEnter` calls
   it), so the gate holds even though the disabled prop only covers the button —
   verified by the gate tests via the button, **not** via a literal Enter keypress.
   A dedicated Enter-key test would close that small coverage seam.

---

**Sources:**
[Open WebUI — Knowledge (async ingest + status)](https://docs.openwebui.com/features/workspace/knowledge/) ·
[Unstructured — RAG pipeline best practices](https://unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline) ·
in-repo onboarding poller ([`SemanticLayerEditor/index.tsx:619-690`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx))
