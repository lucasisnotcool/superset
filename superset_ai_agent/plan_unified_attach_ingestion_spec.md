# Unified Document Ingestion — Attach & Upload Share One Pipeline

> **Status:** SPEC / proposal. **Not implemented.** This document is written to be
> picked up by a future agent session as a working checklist (see §10). All claims
> are source-backed with `file:line` references valid at authoring time — re-grep
> if the tree has moved.
>
> **One-line goal:** Collapse the two document lanes into a single ingestion
> pipeline (upload → dedup → persist → vectorize → show in the file browser), and
> expose it through **two** ingress points that differ in exactly one way:
> - **Copilot "Attach"** runs the pipeline **and** inlines the document into the
>   current chat turn.
> - **"Upload document"** button runs the **identical** pipeline **without**
>   attaching anything to a chat.

---

## 1. Requirements (testable)

| # | Requirement |
| --- | --- |
| **R1** | Attaching file(s) in the Copilot persists each as a `raw/` document, dedups by content, vectorizes non-duplicates, shows them in the workspace tree, **and** inlines the server-extracted text into the current turn for immediate grounding. |
| **R2** | The "Upload document" button performs the **identical** persist + dedup + vectorize + tree-refresh pipeline, with **no** chat attachment and **no** inline grounding. |
| **R3** | Both ingress points call **one** shared ingestion implementation; the only code divergence is the chat-inline step (R1 only). No second copy of upload/dedup/toast logic. |
| **R4** | Re-ingesting byte-identical content within the same project creates **no** new document row, chunk rows, or vectors; the pre-existing document is returned, surfaced in the tree, and the user is notified it was reused. |
| **R5** | Vectorization auto-runs on each newly persisted document when indexing is enabled; **degrade-closed** — persist + extraction still succeed (document is keyword-retrievable + viewable) when indexing is disabled or no embedder is configured. |
| **R6** | Binary/office documents (PDF, DOCX, XLSX, PPTX, HTML, CSV) are ingestible from **both** ingress points via server-side extraction — not just client-readable text files. |
| **R7** | Every ingress enforces write permission + scope isolation, reusing the existing upload-route authorization (`authorize_semantic_project(..., permission="write")`). |
| **R8** | The legacy staging/classification dialog (`SemanticLayerImportDialog`) and the redundant Copilot-header "Upload" button are removed; no dead state, props, or imports remain. |

---

## 2. Current state (source-backed)

Two deliberately separate lanes today
([`uploaded_documents_rag_and_crud.md` §0.7](uploaded_documents_rag_and_crud.md)):

| Lane | Entry point | Pipeline | Persist | Vectorize | In tree |
| --- | --- | --- | --- | --- | --- |
| **Ephemeral (Attach)** | Copilot "Attach" — [`CopilotPanel.tsx:928`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) | `file.text()` → `MessageAttachment` → inlined into the prompt by `_attachments_text` ([`app.py:1498`](app.py)) | ❌ | ❌ | ❌ |
| **Persistent (Upload)** | "Upload document" ([`index.tsx:900`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)) + Copilot-header "Upload" ([`CopilotPanel.tsx:547`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx)) → `SemanticLayerImportDialog` → `uploadProjectSourceDocument` → `register_document` + `extract_document` | ✅ | ✅ (gated) | ✅ (`raw/`) |

Constraining facts (each verified):

- **Attach is text-only.** `handleAttach` does `await file.text()`, stores
  `MessageAttachment {filename, content_type, text, truncated}`, `accept=".json,
  .md,.txt,.yml,.yaml,.csv,text/*"` — binary docs **cannot** be attached.
  ([`CopilotPanel.tsx:234-252`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx), accept at `:921`)
- **No upload-time dedup.** `register_document` hashes bytes to `checksum`
  (`hashlib.sha256`, [`documents.py:75`](semantic_layer/documents.py)) then
  unconditionally `store.save_document(...)` ([`documents.py:91`](semantic_layer/documents.py)).
  Re-uploading identical bytes makes a **new** row → new chunks → new vectors.
- **Upload pipeline already persists + extracts + vectorizes.** The route
  `upload_project_source_document` ([`app.py:2396-2463`](app.py)) calls
  `register_document` then `extract_document`, inline for small files and on
  `active_job_runner` for files over `wren_document_async_threshold_bytes`
  ([`app.py:2438`](app.py)). `extract_document` runs `_index_document_chunks`
  best-effort on success ([`documents.py:94,214`](semantic_layer/documents.py)).
- **Vectorization is best-practice already** — idempotent chunk ids
  (`uuid5`), status tracking (`uploaded|extracting|extracted|needs_ocr|error`),
  gated on `wren_document_indexing_enabled` + a configured `document_index`,
  degrade-closed. **No change needed to vectorization itself.**
- **No `kind`/`category` on documents.** "MDL file" vs "BI document" is
  `MdlFile` table vs `SemanticDocument` table ([`schemas.py:93`](semantic_layer/schemas.py)),
  inferred from extension by the dialog — not a stored attribute.
- **The import dialog stages + classifies** (`mdl | enrichment | document`) and
  adds a diff-review step for MDL JSON. Under the decided design (§4) none of
  that survives — every ingested file is a document.

---

## 3. Best practices (researched)

RAG-ingestion guidance converges on the primitives this codebase already has,
plus the one it lacks (upload dedup):

- **Content-hash dedup before embedding** (SHA256): if the hash was seen,
  ingestion is a no-op — the standard idempotency + cost-control mechanism
  (reported 30–50% embedding-cost reduction). We store `checksum` but never act
  on it. ([RAG VDB ingestion](https://medium.com/@shekhar.manna83/rag-architecture-best-practice-vector-database-ingestion-6a7aecaa5ae4), [Unstructured](https://unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline))
- **Idempotent upsert keyed by `document_id + chunk_id`.** Already done.
- **Per-document status to avoid reprocessing.** Already done.
- **Unified "attach → knowledge base" UX** — an attached file becomes a
  first-class, persisted, reusable knowledge item, not a per-message throwaway.
  This is the direction here. ([Open WebUI — Knowledge](https://docs.openwebui.com/features/workspace/knowledge/))

**Takeaway:** the heavy infra is already best-practice. This change is *one dedup
primitive + wiring two thin ingress points onto one shared function.*

---

## 4. Target architecture

```
                ┌──────────────────────────── shared ───────────────────────────┐
 Attach (chat) ─┤  useDocumentIngestion.ingest(files)                            │
                │   → uploadProjectSourceDocument(projectId, file)   [per file]  │
                │      → POST …/projects/{pid}/documents                         │
                │         → register_document  ── checksum dedup ──► existing?    │
                │            ├ new: write blob + row, extract, VECTORIZE          │
                │            └ dup: return existing, no new row/chunks/vectors    │
                │      → returns SemanticDocument {…, deduplicated}               │
                │   → toast (new / "reusing"), refresh document list             │
 Upload (btn) ──┤                                                                │
                └──────────────────────────── shared ───────────────────────────┘
                         │                                            │
            Attach ONLY: ▼ push MessageAttachment(extracted_text)     ▼ (nothing)
                         + show chip in composer                   Upload: done
```

**Decided behaviors (locked):**
- **D1 = (A):** every ingested file (any extension, incl. `.json`) becomes a
  `raw/` document and is vectorized. No MDL-file routing; **UI MDL-JSON import is
  dropped** (MDL is authored in the editor / via Copilot changesets).
- **D2 = (A):** Attach inlines the **server-extracted** text for the attaching
  turn; Upload inlines nothing.
- **D8 = ADJUSTED:** **Keep** an "Upload document" button, but reimplement it as a
  direct file picker through the shared pipeline (identical to Attach minus the
  chat step). **Delete** `SemanticLayerImportDialog` (staging/classification/diff)
  and the redundant Copilot-header "Upload" button.

---

## 5. Entrypoints & touchpoints

### 5.1 Backend (Python — `superset_ai_agent/`)

| # | File:symbol | Change |
| --- | --- | --- |
| BE-1 | [`semantic_layer/store.py:41`](semantic_layer/store.py) `SemanticLayerStore` | Add `find_document_by_checksum(project_id, checksum, *, owner_id) -> SemanticDocument | None` to the Protocol. |
| BE-2 | [`semantic_layer/sqlalchemy_store.py`](semantic_layer/sqlalchemy_store.py) | Implement BE-1: indexed query on `project_id`+`checksum`+`owner_id`, newest-first. |
| BE-3 | [`semantic_layer/memory.py`](semantic_layer/memory.py) | Implement BE-1 for the in-memory store (test parity). |
| BE-4 | [`semantic_layer/documents.py:48`](semantic_layer/documents.py) `register_document` | Before `save_document` ([:91]), call BE-1; if hit, short-circuit: return the existing doc as `model_copy(update={"deduplicated": True})` — skip blob write + save. |
| BE-5 | [`semantic_layer/schemas.py:93`](semantic_layer/schemas.py) `SemanticDocument` | Add transient response field `deduplicated: bool = False` (NOT persisted; the SQLAlchemy mapper never sets it → defaults False on reads). |
| BE-6 | [`app.py:2396`](app.py) `upload_project_source_document` | After `register_document`, `if document.deduplicated: return document` (already extracted+indexed) **before** the `_extract()` / job-submit block. Other callers unaffected. |

> The other two upload routes (`upload_semantic_document` [`app.py:818`], the
> `…/documents/text` route [`app.py:2469`]) call `register_document` too — they
> inherit dedup for free. Confirm they tolerate the early-return shape (they
> already return a `SemanticDocument`).

### 5.2 Frontend (TS — `superset-frontend/src/SqlLab/components/AiAgentPanel/`)

| # | File:symbol | Change |
| --- | --- | --- |
| FE-1 | `useDocumentIngestion.ts` (**new**) | Shared hook: `ingest(files: File[]) => Promise<{document: SemanticDocument; deduplicated: boolean}[]>`. Per file: `uploadProjectSourceDocument(projectId, file)`; map errors to a rejected result + danger toast; emit "reusing %s" vs success toasts. **No UI state** — pure ingestion + notifications, reused by both callers (R3). |
| FE-2 | [`api.ts` `uploadProjectSourceDocument`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts) | Confirm `SemanticDocument` type carries `deduplicated?: boolean`; add the field. No new endpoint. |
| FE-3 | [`CopilotPanel.tsx:234` `handleAttach`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) | Replace `file.text()` path with `ingest(files)`; for each result push a `MessageAttachment {filename, content_type, text: document.extracted_text, …}` (R1 inline) + a status chip; then call new prop `onDocumentsChanged()`. Broaden the hidden input `accept` ([:921]) to the full doc set. |
| FE-4 | [`CopilotPanel.tsx` `CopilotPanelProps`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx) | Add `onDocumentsChanged?: () => void`; **remove** `onUpload` prop + the header "Upload" button ([:540-558]). |
| FE-5 | [`index.tsx:892-908` Upload button](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) | Keep the button; rewire its `onClick` to open a hidden file `<input>` → `ingest(files)` → `refresh()`. Remove `setShowImportDialog`. |
| FE-6 | [`index.tsx:1041` `<CopilotPanel>`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) | Pass `onDocumentsChanged={refresh}`; drop `onUpload={…}`. |
| FE-7 | [`index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) `SemanticLayerImportDialog` usage + import + `showImportDialog` state | **Delete** (D8). |
| FE-8 | `SemanticLayerEditor/SemanticLayerImportDialog.tsx` + its test | **Delete** the component + `SemanticLayerImportDialog.test.tsx` (verify no other importer first). |
| FE-9 | [`SemanticLayerEditor/documentStatus.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor) `getDocumentStatusMeta` | Reuse for the attach chip's live status (no change; consume it). |

### 5.3 Config — none
Behavior keys already exist: `wren_document_indexing_enabled` (vectorize gate),
`wren_document_async_threshold_bytes`, `wren_max_document_bytes`,
`wren_allowed_document_types`. **Keep degrade-closed:** indexing-disabled deploys
still persist + extract (R5).

---

## 6. Decision points

| # | Decision | Resolution | Rationale |
| --- | --- | --- | --- |
| **D1** | Attached `.json` → MDL file or document? | ✅ **(A) document** | Fully unified, one path; "in the browser regardless of type" satisfied. **Trade:** UI MDL-JSON import dropped — note in release copy so it's not a silent regression. |
| **D2** | Does Attach still inline into the turn? | ✅ **(A) inline now + RAG later** | Agent sees the file immediately without waiting on embedding; inline the **server-extracted** text (handles binary) from the upload response, not client bytes. |
| **D3** | Dedup scope | **Per-project** (`project_id`+`checksum`) | Matches how docs are listed/shown (`list_project_documents`, [`store.py:57`]); column already indexed. Same bytes in another project is a distinct artifact. |
| **D4** | Dedup match key | **Checksum only** (byte-identical) | Best-practice content hash. Renamed identical bytes = duplicate (don't re-embed); same-name/different-bytes = new doc. Toast names the existing file. |
| **D5** | Dedup location | **Backend choke point** `register_document` | Single authoritative, race-free path reused by all 3 upload routes; mirrors the existing `create_document`/`delete_document_cascade` choke-point design. |
| **D6** | Duplicate handling | **Success + reuse + notify** | Toast "Already in this project — reusing `<file>`"; reveal the existing doc; (Attach) still inline it. No new row/chunks/vectors. |
| **D7** | Async UX while extracting | **Live status chip + tree badge** | Reuse `getDocumentStatusMeta`; don't block Send; inline uses whatever text exists, RAG catches up at `extracted`. |
| **D8** | Upload button & dialog | ✅ **Keep button (direct picker), delete dialog + header-Upload** | Per the adjusted ask: Upload == Attach minus chat. Staging/classification/diff is obsolete once every file is a document. |
| **D9** | Accept types + size | `.json,.md,.markdown,.txt,.csv,.html,.pdf,.docx,.xlsx,.pptx`; size cap server-side (`wren_max_document_bytes`, 10 MB) | Match the doc set the extractor supports; surface rejects as toasts. |
| **D10** | Where does shared FE logic live? | **`useDocumentIngestion` hook** (FE-1) | Avoids duplicating upload/dedup/toast logic across `index.tsx` and `CopilotPanel.tsx` (R3). Alternative — a callback threaded from the editor — couples the two more tightly; hook is cleaner and independently testable. |

No open decisions remain; all are resolved with a recommendation grounded in an
existing codebase pattern or the cited best practice.

---

## 7. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| **Re-extraction on the dedup path** (route runs `_extract` again). | BE-6 early-returns before `_extract` for `deduplicated` docs (already `extracted`); even if missed, `chunk_id` `uuid5` makes re-index idempotent — wasteful, not corrupting. |
| **Transient `deduplicated` field leaks into persistence.** | It lives only on the pydantic `SemanticDocument` (BE-5), never on the SQLAlchemy model/mapper → always False on reads. Add a test asserting a reloaded doc has `deduplicated=False`. |
| **Indexing disabled / no embedder.** | Degrade-closed: persist + extract still run; vectors skipped; doc is in the tree + keyword-retrievable (R5). Don't gate persistence on the embedder. |
| **Large file latency** (>1 MB async extract). | Status `extracting`; chip + tree show progress; Send not blocked; RAG catches up at `extracted` (D7). |
| **Losing MDL-JSON import** (D1-A). | Intentional. Document in release/UX copy; Copilot + editor cover MDL authoring. Flag to the user before shipping. |
| **Two ingress points drift apart** (R3 violated over time). | One `useDocumentIngestion` hook is the only upload path; both callers import it; the only allowed divergence is CopilotPanel's `MessageAttachment` push. Lint/review guard: no `uploadProjectSourceDocument` call outside the hook. |
| **`needs_ocr` (image-only PDF).** | Persist + show status; no chunks/vectors (existing behavior). Surface status, never error. |
| **Disallowed type / oversize.** | Server rejects (`ValueError` → 400, [`app.py:2424`]); hook maps to a danger toast; no chip. |
| **Permission / scope.** | Both ingress use `uploadProjectSourceDocument` → route enforces `permission="write"` + scope (R7). Attach/Upload already gated on `canWrite`. |
| **Dead code after dialog removal.** | Grep for `SemanticLayerImportDialog`, `showImportDialog`, `onUpload` to zero before closing FE-7/FE-8. |

---

## 8. Backend dedup — detail

```python
# documents.py — register_document, replacing the tail (around :84-91)
checksum = hashlib.sha256(content).hexdigest()
existing = store.find_document_by_checksum(           # BE-1
    project_id, checksum, owner_id=owner_id,
) if project_id else None
if existing is not None:                              # D4/D5/D6
    return existing.model_copy(update={"deduplicated": True})
# …unchanged: build SemanticDocument, storage.write, store.save_document
```

```python
# app.py — upload_project_source_document, after register_document (≈:2423)
if document.deduplicated:                             # BE-6
    return document                                   # already extracted+indexed
# …unchanged: _extract() inline / job-submit, event, get_document
```

`find_document_by_checksum` (sqlalchemy): `SELECT … WHERE project_id=? AND
checksum=? AND owner_id=? ORDER BY created_at DESC LIMIT 1`, mapped via the
existing `_document_from_model` ([`sqlalchemy_store.py`](semantic_layer/sqlalchemy_store.py)).

---

## 9. Test plan

**Backend**
- `find_document_by_checksum` round-trips on both stores (memory + sqlalchemy);
  returns None when absent; newest-first on collision.
- `register_document`: 2nd identical upload → `deduplicated=True`, same `id`, no
  new chunks/vectors; distinct project + same bytes → 2 docs (D3).
- `upload_project_source_document`: dedup path skips `_extract` (assert the job
  runner / index not called the 2nd time); reloaded doc has `deduplicated=False`.
- Indexing-disabled: persists + extracts, no vectors, dedup still works (R5).

**Frontend**
- `useDocumentIngestion.test.ts`: maps upload results; dedup → "reusing" toast;
  error → danger toast; no UI coupling.
- `CopilotPanel.test.tsx`: Attach → `ingest` called, status chip rendered,
  `MessageAttachment` added (R1), `onDocumentsChanged` fired; header "Upload"
  button gone; `onUpload` prop gone.
- `index.test.tsx`: "Upload document" button → `ingest` + `refresh`, **no**
  `MessageAttachment`, no dialog; `showImportDialog`/dialog import removed.
- Delete `SemanticLayerImportDialog.test.tsx`.

---

## 10. Sequential checklist (with blockers & dependencies)

> Work top-to-bottom. Each phase lists its **blockers** (what must be true to
> start) and **unblocks** (what it enables). `[ ]` todo · `[x]` done.

### Phase 0 — Verify baseline (no code) ✅ DONE
- [x] Re-grep all `file:line` anchors in §5 (tree may have moved). **Blocker:** none.
- [x] Confirm `register_document` is the sole checksum site and all 3 upload
      routes funnel through it ([`app.py:818,2396,2469`]). **Unblocks:** P1.

### Phase 1 — Backend dedup primitive ✅ DONE
> **Blocker:** Phase 0. **Independent of all frontend phases.**
- [x] BE-1 add `find_document_by_checksum` to `SemanticLayerStore` Protocol.
- [x] BE-2 implement on `sqlalchemy_store.py`.
- [x] BE-3 implement on `memory.py`.
- [x] BE-5 add transient `deduplicated: bool = False` to `SemanticDocument`.
- [x] BE-4 short-circuit in `register_document` (uses BE-1, BE-5). **Also** added
      the same guard in `create_document` so the text route + small-upload path
      dedup too (not just the async upload route).
- [x] BE-6 early-return in `upload_project_source_document` (uses BE-4).
- [x] Backend tests (§9): store (6, parametrized × both stores), `create_document`
      (5: dedup / no-reindex spy / distinct-project / no-project), API route (1).
      Full `tests/unit_tests/superset_ai_agent/` = **804 passed, 11 skipped**.
- [x] `ruff` + `ruff-format` clean on touched files. (`mypy` not installed in this
      venv — defer to CI, per `uploaded_documents_rag_and_crud.md` §8.5.)

### Phase 2 — Shared frontend ingestion hook ✅ DONE
> **Blocker:** P1 merged/available (the hook reads `deduplicated`). **Unblocks:** P3, P4.
- [x] FE-2 `SemanticDocument` TS type has `deduplicated?: boolean`.
- [x] FE-1 created `useDocumentIngestion.ts` (upload + dedup-aware toasts).
- [x] `useDocumentIngestion.test.ts` (4 tests: upload / dedup-reuse / per-file
      error isolation / no-project no-op). Green.

### Phase 3 — Wire Copilot "Attach" onto the pipeline (R1) ✅ DONE
> **Blocker:** P2. **Depends on** FE-1, FE-2.
- [x] FE-3 rewrote `handleAttach` (ingest → stage persisted docs → derive
      `MessageAttachment` from `extracted_text` at send → status chip);
      broadened `accept` (D9).
- [x] FE-4 added `onDocumentsChanged`; removed `onUpload` prop + header "Upload"
      button. **Bug caught by tests:** a stale `setAttachments([])` in
      `startNewChat` (renamed to `setAttachedDocs`) would have thrown on "New
      chat" — fixed.
- [x] FE-6 pass `onDocumentsChanged={refresh}` from `index.tsx`.
- [x] `CopilotPanel.test.tsx` updated (16 pass): control-gone, attach-persists +
      refresh, status-hint chip, inline-grounding payload.

### Phase 4 — Rewire "Upload document" button (R2) + delete dialog (R8) ✅ DONE
> **Blocker:** P2.
- [x] FE-5 rewired the button to a hidden file input → `ingest` → `refresh`
      (no inline); `isIngesting` drives loading/disabled.
- [x] FE-7 deleted `SemanticLayerImportDialog` usage + import + `showImportDialog`.
- [x] FE-8 deleted `SemanticLayerImportDialog.tsx` + its test (no other importer).
- [x] `index.test.tsx` updated (12 pass): button present, dialog gone, file choice
      routes through the shared hook.
- [x] Grep `SemanticLayerImportDialog|showImportDialog|onUpload` in source →
      zero (one expected hit is the test asserting `copilot-upload` is absent).

### Phase 5 — Verify & polish ✅ DONE
> **Blocker:** P1–P4.
- [x] Full `tests/unit_tests/superset_ai_agent/` = **804 passed, 11 skipped**;
      `AiAgentPanel` Jest = **201 passed, 2 pre-existing failures** unrelated to
      this change (`AiAgentPanel/index.test.tsx` SQL-artifact render +
      `ExplainDialog.test.tsx` — both fail identically with this change stashed —
      see §12); `tsc --noEmit` clean on touched files; `prettier` clean;
      `oxlint`/`eslint` deferred to CI (no local config — memory note).
- [x] Updated the `document-rag-suite` memory + this spec; `uploaded_documents…`
      §0.7 note added.
- [ ] **Manual QA (not run here — needs a live agent + embedder):** attach a PDF →
      appears in `raw/`, vectorized, grounded in the turn; re-attach → "reusing"
      toast, no duplicate node; Upload button → same minus chat. **See §12 gaps.**
- [ ] **Flag the dropped UI MDL-JSON import (D1-A) in release/UX copy** — owner action.

---

## 12. As-built notes — residual risks & UX expectation gaps

Implemented and test-green; the items below are the honest gaps between what the
code now does and what a user might expect. None block merge; several want a
follow-up or a product decision.

**Verified by tests**
- Dedup is byte-exact, per-project, owner-isolated, newest-wins; the dedup path
  skips re-extraction *and* re-vectorization (spy-asserted). Transient
  `deduplicated` never persists. Attach and Upload share one hook; Attach also
  inlines `extracted_text` into the turn.

**Residual risks / gaps**
1. **No client-side type/size pre-check.** Rejections (disallowed type, >10 MB)
   surface only after the round-trip, as a danger toast from the server's 400.
   Acceptable (server is authoritative) but the `accept` filter is the only
   pre-hint. *Gap:* a `.pages` or `.zip` slips past `accept` only to 400.
2. **Status chip is a snapshot, not live.** A large file attached as
   `extracting` shows "· Extracting…" at attach time and does **not** auto-update
   in the composer (the **tree** is the live surface and does refresh). If the
   user sends immediately, inline grounding uses whatever `extracted_text` exists
   (possibly empty for a still-extracting large file) — RAG catches up next turn.
   *Expectation gap:* a user may expect the chip to flip to "ready" in place.
3. **Inline grounding races large-file extraction.** For files over the 1 MB
   async threshold, `extracted_text` may be empty on the upload response, so the
   *first* turn after attaching a big PDF may not be grounded inline (RAG still
   indexes it for later turns). Small files (the common case) extract inline and
   are grounded immediately. *Mitigation option (future):* disable Send until the
   attached docs report `extracted`, or poll the doc status in the composer.
4. **Dropped UI MDL-JSON import (D1-A).** Attaching/uploading a `.json` now makes
   a `raw/` document, **not** an MDL model. Intended, but a user who previously
   imported hand-authored MDL JSON via the dialog will not find that path. Needs
   release-note/UX copy. (MDL authoring remains via the editor + Copilot.)
5. **Per-project dedup means cross-project re-upload re-embeds.** By design (D3),
   but a user moving the same file across schemas pays embedding twice.
6. **`needs_ocr` / `error` documents still attach.** They appear in the tree and
   as a chip with the status, but contribute no chunks/grounding. The chip shows
   the status; there's no hard block. Matches existing upload behavior.
7. **Two pre-existing Jest failures** (`AiAgentPanel/index.test.tsx` "sends a
   conversation message and renders SQL artifact"; `ExplainDialog.test.tsx`
   "surfaces typed detail…") fail identically with this change stashed — they are
   **not** caused by this work, but they do mean the `AiAgentPanel` suite is not
   100% green on this branch. Worth a separate fix.
8. **Manual/visual QA not performed** (no live agent+embedder in this session).
   The end-to-end attach→vectorize→reuse loop is covered by unit/integration
   tests with mocked network, not by eye.

## 11. Out of scope
- The `enrich` (doc→MDL) hot path — untouched.
- Cosine near-duplicate detection (exact-checksum only here).
- OCR for image-only PDFs (`needs_ocr` seam unchanged).
- Retroactively promoting historical ephemeral attachments.
- Cross-project / global document search.

---

**Sources:**
[RAG VDB ingestion best practice](https://medium.com/@shekhar.manna83/rag-architecture-best-practice-vector-database-ingestion-6a7aecaa5ae4) ·
[Unstructured — RAG pipeline best practices](https://unstructured.io/insights/rag-systems-best-practices-unstructured-data-pipeline) ·
[RAG pipeline deep dive (ingestion/chunking/embedding)](https://dev.to/derrickryangiggs/rag-pipeline-deep-dive-ingestion-chunking-embedding-and-vector-search-2877) ·
[Open WebUI — Knowledge](https://docs.openwebui.com/features/workspace/knowledge/)
