# MDL Copilot — Attach dialog (pick existing `raw/` docs + drag-drop/browse upload)

**Status:** IMPLEMENTED & test-green (uncommitted working tree). Frontend-only;
reuses existing endpoints. Built on top of the unified-attach ingestion lane
(`plan_unified_attach_ingestion_spec.md`).

## Goal
Replace the Copilot **Attach** bare hidden `<input type=file>` with a dialog that
lets the user **(a) select from this project's existing `raw/` documents** and
**(b) upload new ones** via a drag-and-drop area that also opens the OS file
browser on click/keyboard. Mirrors the dual-option file-attach pattern (pick
existing knowledge item + upload), with click-to-browse fallback for a11y.

## What shipped
- **New component** `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/AttachDocumentDialog.tsx`:
  - Self-contained `Modal` (built-in footer: Cancel + primary `Attach (N)`).
  - **Dropzone** (custom div, theme-token dashed border): native drag-over
    highlight, `onDrop`, click + Enter/Space → hidden `<input>` (sibling, so its
    click doesn't bubble back and re-trigger the picker — that was a real bug the
    keyboard test caught). `role="button"`, `tabIndex`, `aria-label`,
    `aria-disabled` for keyboard/SR users.
  - **Drop/pick parity + pre-checks:** `isAcceptedFile` (extension allow-list,
    same set as the composer `accept`) + client-side size guard
    (`DEFAULT_MAX_DOCUMENT_BYTES = 10_000_000`, matches `wren_max_document_bytes`).
    Rejected files surface a single dismissible "Skipped N file(s)" `Alert`; the
    backend stays authoritative (degrade-closed).
  - **Existing docs:** `listProjectDocuments(projectId)` on open → multi-select
    `Checkbox` rows (filename + size via `formatBytes` + `DocumentStatusTag` for
    pending/error/needs_ocr). Loading `Skeleton`, error `Alert` + Retry, `Empty`
    state. Reuses the same `documentStatus.tsx` helpers and conventions as the
    sibling `AutoOnboardModal`.
  - **Uploads** go through the shared `useDocumentIngestion(projectId)` hook
    (upload + dedup + vectorize + its own toasts); results are appended,
    **auto-selected**, and `onDocumentsChanged()` fires so the editor tree
    refreshes. **No re-upload for existing picks** — selecting an already-extracted
    doc grounds the turn from its `extracted_text`, which the list payload carries
    (`_document_from_model` includes it).
  - **Replace semantics:** the dialog is seeded from the current `attachedDocs`
    (pre-checked) and `onConfirm(selected)` replaces the staged set (deselect →
    chip removed; uploads add). Cancel commits nothing.
- **`CopilotPanel.tsx`:** removed the hidden input / `fileInputRef` /
  `useDocumentIngestion` / `handleAttach`; the Attach button now
  `onClick={() => setAttachOpen(true)}`; added `handleAttachConfirm(docs)` =
  `setAttachedDocs(docs)` + re-arm `attachPollGaveUp`. The existing live status
  poll, `attachmentsForSend` inline grounding, and the Send gate are **unchanged**
  and now operate on whatever set the dialog commits.

## Endpoints (all pre-existing — no backend change)
- `GET …/projects/{id}/documents` → `listProjectDocuments` (existing `raw/` docs).
- `POST …/projects/{id}/documents` → `uploadProjectSourceDocument` (via the hook).
- `GET …/documents/{id}` → `getSemanticDocument` (the parent's status poll).

## Tests
- `AttachDocumentDialog.test.tsx` (15): list/dropzone render, pre-check seeded
  attachments, select+confirm hands off selection (with `extracted_text`),
  deselect removes, upload → ingest + auto-select + `onDocumentsChanged`, drop
  ingest, unsupported-type drop skipped (drop/pick parity), oversize rejected
  pre-upload, drag highlight, keyboard activation, empty state, load-error+Retry,
  cancel commits nothing, no-write disables + blocks upload, ingesting state.
- `CopilotPanel.test.tsx` (29, updated): `attachFile`/`attachViaInput` helpers now
  drive the dialog (open → upload → confirm); added `listProjectDocuments` mock
  (default `[]`). All prior attach/poll/grounding/Send-gate assertions still hold.
- Full `src/SqlLab/components/AiAgentPanel` Jest = 322 passed. `tsc --noEmit`
  clean (exit 0). prettier clean. oxlint deferred to CI (native binary not
  installed locally).

## Residual risks / UX expectation gaps
1. **Cancel does not undo an in-dialog upload.** Uploading persists to the
   workspace immediately (shared ingestion model); Cancel only discards the
   *attachment selection*. The file remains in the tree (and a success toast
   already fired). Matches `AutoOnboardModal`. A user may expect Cancel to remove
   the just-uploaded file — it does not.
2. **No per-file upload progress.** Only a global "Uploading…" hint + disabled
   primary while `isIngesting`. Large async-extract files still show their status
   via `DocumentStatusTag` once listed/staged.
3. **No search/filter** in the existing-docs list (scroll only, 260px). Fine for
   small corpora; a search box is a future enhancement for large projects.
4. **Dialog status is a snapshot at open.** Rows don't live-poll while the dialog
   is open (the composer chips do, via the parent poll). Reopening re-fetches.
5. **Error/needs_ocr docs are selectable.** They attach with no grounding text;
   the row + chip show the status (consistent with existing composer behavior).
6. **Size guard uses a constant**, not the backend-reported cap. If an operator
   raises `WREN_MAX_DOCUMENT_BYTES`, the client hint lags until the constant is
   updated (or wired to `/health.max_document_bytes` — a known follow-up from
   `plan_document_upload_ux_gaps.md` G2). Backend remains authoritative.
7. **Visual QA not performed** (no live agent/embedder in session). Covered by
   unit/integration tests with mocked network, not by eye.

## Out of scope / not changed
- Backend, the ingestion pipeline, dedup, vectorization, the enrichment hot path.
- The editor's standalone "Upload document" button (separate ingress, unchanged).
