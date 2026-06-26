# Tier 1 Document-Format Expansion — Implementation Plan & Agent Checklist

**Status:** ✅ **IMPLEMENTED & green** (all 11 steps). See **Part F — As-built log**
at the end for what landed, deviations, and verification. Source-backed against the
tree at time of writing. Companion docs: `document_format_support_study.md` (why),
and `uploaded_documents_rag_and_crud.md` §8 (the as-built ingestion/CRUD pipeline).

**This document is dual-purpose:**
- **Part A** is the technical specification (exact files, signatures, behaviour).
- **Part B** is a sequential checklist for agent passes — do the steps in order,
  tick them, run the named tests before moving on. Each step is self-contained so
  a later agent can resume from the checklist + the cited code with full context.

---

## 0. Goal & guardrails

**Goal (Tier 1 only):**
1. Add **`.xlsx`** (openpyxl → per-sheet Markdown tables).
2. Add **`.pptx`** (python-pptx → per-slide text + tables).
3. **Upgrade CSV** from pipe-rows (`a | b | c`) to header-aware **Markdown tables**.
4. **Raise the size cap** and **route large/slow extraction to background jobs**.
5. **Flag image-only PDFs** as `status="needs_ocr"` instead of saving empty text.
6. Leave **OCR seams + tagging** (no OCR implementation).
7. Keep the **vectorisation layer abstractable** — all extractors emit canonical
   text so the future RAG pass needs no format-specific code (RAG itself is out of
   scope, imminent-next).

**Invariants that MUST NOT change (verify, don't rebuild):**
- **Access == project (DB) access.** Every document route already authorizes via
  `authorize_semantic_project(request, project_id, permission=read|write)`
  (`app.py:513-537`), which calls `require_project_permission` and resolves the
  project's governed database. A caller who can access the DB can read/write its
  BI docs; one who cannot gets 403/404. **No new, weaker access path may be
  introduced.** New/changed code re-uses this gate verbatim. (Step 9 audits it.)
- **Degrade-closed.** Missing optional parser dep → `ValueError` →
  `status="error"`, never a crashed upload (`extractors.py:42-47`,
  `documents.py:117-124`).
- **Do NOT touch the upload→MDL enrichment hot path**
  (`enrich_project_document` / `propose_mdl_from_document`).
- **Original bytes stay stored** (`file_storage.py`) — they are the re-parse
  enabler for the OCR/RAG roadmap.

**Explicitly out of scope (this plan):** OCR execution, vectorisation/RAG changes,
cloud parsers, Parquet/Avro, RTF/ODT/email.

---

## PART A — Technical specification

### A.1 Canonical-text contract (the vectorisation seam)

The extraction layer's single job is to turn any supported file into one
**canonical UTF-8 text representation**. Everything downstream
(`truncate_to_sections` → `build_chunk_records` → `DocumentChunkIndex`) consumes
*only that text* and is format-agnostic (`documents.py:92-164`). Tier 1 preserves
this contract, so the imminent RAG pass needs no format-specific code.

Two rules make the canonical text RAG-friendly **now**, cheaply:
1. **Tabular content becomes GitHub-flavoured Markdown tables** (header row +
   `---` separator). LLMs and keyword recall both handle these well.
2. **Provenance is encoded as section headers** separated by blank lines:
   `## Sheet: <name>` (xlsx), `## Slide <n>` (pptx). The existing section chunker
   splits on blank lines (`document_chunks.chunk_sections`), so each sheet/slide
   stays a retrievable unit, and a future RAG pass can parse provenance from the
   header without a schema change. **Seam, not implementation:** do not add
   per-chunk metadata columns now — just guarantee the headers exist.

> Known limitation to record (not fix now): a single table larger than the 2k
> chunk cap is hard-split on word boundaries (`document_chunks.py:76-92`), which
> can cut mid-row. Table-aware chunking is a RAG-stage concern; flag it, defer it.

### A.2 Extractor changes — `semantic_layer/extractors.py`

Current dispatch is a type→function ladder in
`CompositeDocumentExtractor.extract_text` (`extractors.py:49-63`). Extend it.

**A.2.1 New MIME constants** (beside `_DOCX_CONTENT_TYPE`, `extractors.py:26-29`):
```python
_XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
```

**A.2.2 Extraction-signal exceptions** (new, top of module). These are the OCR
tagging seam:
```python
class DocumentExtractionError(ValueError):
    """Base for extraction failures (kept a ValueError for existing handlers)."""

class NeedsOcrError(DocumentExtractionError):
    """Raised when a file has no extractable text layer and needs OCR.

    OCR itself is out of scope; raising this is the seam. `create_document`
    maps it to status="needs_ocr" (not "error") so a future OCR backend can pick
    these up. Subclasses ValueError so the existing endpoint 400-handling and the
    `except ValueError` in `_validate_document` callers keep working.
    """
```

**A.2.3 Markdown-table helper** (module-level, shared by CSV + xlsx + pptx
tables):
```python
def _rows_to_markdown_table(rows: list[list[str]]) -> str:
    """Render rows as a GitHub-flavoured Markdown table (first row = header).

    - Drops fully-empty trailing rows/cols; pads ragged rows.
    - Escapes pipes/newlines in cells. Returns "" for no data.
    """
```
Implementation notes: normalise to a rectangular grid (max width across rows),
`str(cell).replace("|","\\|").replace("\n"," ").strip()`, build
`| h1 | h2 |` / `| --- | --- |` / data rows. If only one row, still emit it as a
header-only table.

**A.2.4 CSV upgrade** — replace `_extract_csv` (`extractors.py:77-81`):
read with `csv.reader`, collect rows, return `_rows_to_markdown_table(rows)`.
(Keep the empty-input → "" behaviour.)

**A.2.5 XLSX** — new `_extract_xlsx(content)`:
```python
def _extract_xlsx(content: bytes) -> str:
    try:
        import openpyxl  # optional dependency
    except ImportError as ex:
        raise ValueError("XLSX extraction requires 'openpyxl'.") from ex
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    blocks: list[str] = []
    for ws in wb.worksheets:
        rows = [
            ["" if c is None else str(c) for c in row]
            for row in ws.iter_rows(values_only=True)
        ]
        rows = [r for r in rows if any(cell.strip() for cell in r)]
        if not rows:
            continue
        table = _rows_to_markdown_table(rows)
        blocks.append(f"## Sheet: {ws.title}\n\n{table}")
    wb.close()
    return _strip_nul("\n\n".join(blocks))
```
- `data_only=True` ⇒ stores last-computed values, **not formulas** (formulas are
  lost; acceptable for semantic text — record it). `read_only=True` streams large
  sheets without loading the whole workbook.

**A.2.6 PPTX** — new `_extract_pptx(content)`:
```python
def _extract_pptx(content: bytes) -> str:
    try:
        from pptx import Presentation  # optional dependency: python-pptx
    except ImportError as ex:
        raise ValueError("PPTX extraction requires 'python-pptx'.") from ex
    prs = Presentation(io.BytesIO(content))
    blocks: list[str] = []
    for index, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_table:
                rows = [
                    [cell.text for cell in row.cells]
                    for row in shape.table.rows
                ]
                parts.append(_rows_to_markdown_table(rows))
            elif shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
        if parts:
            blocks.append(f"## Slide {index}\n\n" + "\n\n".join(parts))
    return _strip_nul("\n\n".join(blocks))
```

**A.2.7 PDF image-only detection** — amend `_extract_pdf`
(`extractors.py:146-155`): after building `pages`, if the reader has pages but
**every** page yielded empty text, raise `NeedsOcrError`:
```python
    pages = [page.extract_text() or "" for page in reader.pages]
    non_empty = [p.strip() for p in pages if p.strip()]
    if reader.pages and not non_empty:
        raise NeedsOcrError("PDF has no extractable text layer; OCR required.")
    return _strip_nul("\n\n".join(non_empty))
```

**A.2.8 Dispatch** — add to `extract_text` (`extractors.py:49-63`), before the
final `raise`:
```python
        if normalized_type == _XLSX_CONTENT_TYPE:
            return _extract_xlsx(content)
        if normalized_type == _PPTX_CONTENT_TYPE:
            return _extract_pptx(content)
```
Update the class docstring (`extractors.py:40-47`) to list xlsx/pptx and the
needs-OCR signal.

### A.3 Status lifecycle — `semantic_layer/schemas.py`

Extend `SemanticDocumentStatus` (`schemas.py:31-38`) with two states:
```python
SemanticDocumentStatus = Literal[
    "uploaded",
    "extracting",   # NEW: queued/running background extraction
    "extracted",
    "needs_ocr",    # NEW: image-only / no text layer; OCR seam
    "needs_review",
    "approved",
    "indexed",
    "error",
]
```
No DB migration required — `status` is a free String column
(`persistence/models.py`), the Literal is app-level validation only.

### A.4 `create_document` split for async — `semantic_layer/documents.py`

Today `create_document` does validate → store blob → save row → **extract inline**
→ index, all on the request thread (`documents.py:47-133`). Split the extraction
tail so the endpoint can run it inline (small files) or in a background thread
(large files), **without changing existing call sites' behaviour**.

**A.4.1 New: `extract_document(...)`** — the extraction tail, callable on its own:
```python
def extract_document(
    document_id: str,
    *,
    owner_id: str,
    config: AgentConfig,
    store: SemanticLayerStore,
    storage: DocumentStorage,
    extractor: DocumentExtractor,
    document_index: DocumentChunkIndex | None = None,
) -> SemanticDocument:
    """Extract text for an already-registered document and update its status.

    Reads the original bytes from blob storage (so it is safe to run on a
    background thread without holding content in memory). Maps NeedsOcrError →
    status='needs_ocr'; any other failure → status='error'. Indexes chunks on
    success when indexing is enabled. Never raises (records failure on the row)."""
```
Behaviour = lines `92-133` of today's `create_document`, except:
- load the row via `store.get_document(document_id, owner_id=owner_id)`,
- read bytes via `storage.read(document.storage_uri)`,
- add `except NeedsOcrError` **before** the broad `except`, setting
  `status="needs_ocr"` + a warning, and **skip** indexing for that status.

**A.4.2 Refactor `create_document`** to: validate → write blob → save row
(`status="uploaded"`) → `return extract_document(...)`. Net effect identical to
today for all current callers (text endpoint, tests, conversation uploads at
`app.py:834`). Keep the signature unchanged so nothing else breaks.

**A.4.3 New: `register_document(...)`** — validate + write blob + save row
(`status="uploaded"`) and **return without extracting**. This is the fast path the
async endpoint uses; `create_document` becomes `register_document` +
`extract_document`.

### A.5 Async routing in the upload endpoint — `app.py`

`upload_project_source_document` (`app.py:1994-2038`) currently calls
`create_document` inline. Change to size-based routing using the existing
`active_job_runner` (`app.py:301`, `ThreadJobRunner` in prod / `InlineJobRunner`
in tests):

```python
document = register_document(... )           # fast: validate + blob + row
if document.size_bytes <= app_config.wren_document_async_threshold_bytes:
    document = extract_document(document.id, ...)            # inline (small)
else:
    store.update_document(status="extracting", ...)          # mark queued
    active_job_runner.submit(
        lambda: extract_document(document.id, ...)            # background
    )
    document = store.get_document(document.id, ...)          # returns 'extracting'
```
- The background closure captures the app-level `active_*` singletons exactly like
  `_run_onboarding` (`app.py:1803-1846`).
- **Pollable state is the document row** (`GET …/documents` /
  `…/documents/{id}` are project-scoped already), so we do NOT need the
  `OnboardingResult`-typed `JobStore` here — avoid coupling extraction to that
  result type. The `JobRunner` (thread) is all we reuse.
- `InlineJobRunner` in tests ⇒ async path still completes synchronously, so tests
  stay deterministic.

> Note: FastAPI `UploadFile` spools to a temp file; the effective size gate is
> `wren_max_document_bytes` enforced in `_validate_document` (`documents.py:227`).
> A deployment behind nginx/proxy may impose its own body cap — document, don't
> code around it.

### A.6 Config — `config.py`

In `AgentConfig` (`config.py`):
- **Allow-list** (`config.py:135-143`): append the two MIME types
  `…spreadsheetml.sheet` and `…presentationml.presentation`.
- **Size cap** (`config.py:127`): raise default
  `wren_max_document_bytes: int = 10_000_000` (10 MB). *Tests that assert the 2 MB
  default must be updated — see Step 10.*
- **Async threshold (new):**
  `wren_document_async_threshold_bytes: int = 1_000_000` — extraction above this
  runs in the background thread.
- **OCR seam (new, reserved, unused this plan):**
  `wren_document_ocr_enabled: bool = False` — present so a future OCR backend has
  a flag; Tier 1 only *tags* `needs_ocr`, never reads this. Add a one-line comment
  pointing at `extract_document`'s `needs_ocr` branch as the slot-in point.
- `from_env` bindings for the new ints/bool + broaden the
  `WREN_ALLOWED_DOCUMENT_TYPES` default. Mirror existing `from_env` style
  (`config.py` `from_env`).

### A.7 Dependencies — `requirements-ai-agent.txt`

Add beside pypdf/python-docx (`requirements-ai-agent.txt:49-50`):
```
openpyxl>=3.1,<4.0
python-pptx>=1.0,<2.0
```
Both are pure-Python, MIT/BSD, no native build. **Headless-Linux safe:** openpyxl,
python-pptx, pypdf and python-docx all read the OOXML zip (the XML inside the
file) directly — **no Microsoft Office, no LibreOffice, no COM/win32com.** Same
behaviour on the Windows dev box and the Office-less Linux deploy. (Avoid, for
future Office work: `docx2pdf`, `comtypes`/`win32com`, `unoconv`/`libreoffice
--headless` — those *do* require a native app and would fail on the deploy box.)

> **Decision — targeted libs over Microsoft MarkItDown (rejected; do not
> re-litigate).** MarkItDown was evaluated as a single-lib consolidation and
> rejected as excessive for this controlled pipeline:
> (1) its **core** (non-optional) deps include `magika~=0.6.1` → **onnxruntime**,
> an ML runtime used only to *guess content-type* — which we already have from the
> upload + allow-list (dead weight; upstream issue #1234 asks to make it optional);
> (2) it would **swap parsers we deliberately keep** — PDF via pdfminer.six/
> pdfplumber instead of `pypdf` (breaking our `NeedsOcrError` empty-PDF detection)
> and DOCX via `mammoth` instead of `python-docx`;
> (3) it emits its **own markdown shape**, so we'd post-process anyway to get the
> `## Sheet:`/`## Slide n` provenance the RAG seam (A.1) needs — negating the
> "one lib" benefit;
> (4) larger surface (`requests` + bundled YouTube/Azure/audio converters) inside
> the Superset trust boundary.
> MarkItDown does **not** need native Office either, so that concern doesn't favour
> it. Industry-standard for *known-content-type* ingestion is targeted libraries —
> which this plan uses. mypy: project sets
`ignore_missing_imports=true`, so **no `# type: ignore`** on the imports (adding
one trips `warn_unused_ignores` — same rule that bit pypdf/docx; see
`uploaded_documents_rag_and_crud.md` §8.3).

### A.8 `.env.example`

Update `WREN_ALLOWED_DOCUMENT_TYPES` to include the two new MIME types and note
`WREN_MAX_DOCUMENT_BYTES` (10 MB default) + `WREN_DOCUMENT_ASYNC_THRESHOLD_BYTES`.
Do **not** touch `superset_ai_agent/.env` secrets.

---

## PART B — Sequential implementation checklist

> Run from repo root. Backend tests:
> `pytest tests/unit_tests/superset_ai_agent/ -q`. After all steps:
> `pre-commit run --files <changed>` (ruff, ruff-format, mypy). Tick each box.

- [x] **Step 1 — Deps.** Add `openpyxl` + `python-pptx` to
  `requirements-ai-agent.txt` (A.7). Install into the active venv so tests can
  import them. _Done when:_ `python -c "import openpyxl, pptx"` succeeds.

- [x] **Step 2 — Markdown-table helper + CSV upgrade.** In `extractors.py`: add
  `_rows_to_markdown_table` (A.2.3) and rewrite `_extract_csv` to use it (A.2.4).
  _Test:_ new `test_document_extractors.py` cases — CSV → table has header `---`
  row, ragged rows padded, pipes escaped, empty CSV → "".

- [x] **Step 3 — XLSX.** Add `_XLSX_CONTENT_TYPE`, `_extract_xlsx`, dispatch
  branch (A.2.1/A.2.5/A.2.8). _Test:_ build a workbook in-memory with openpyxl
  (2 sheets), assert `## Sheet:` headers + Markdown tables + multi-sheet
  separation; missing-dep path simulated → `ValueError`.

- [x] **Step 4 — PPTX.** Add `_PPTX_CONTENT_TYPE`, `_extract_pptx`, dispatch
  branch (A.2.6/A.2.8). _Test:_ build a deck with python-pptx (a text box + a
  table across 2 slides), assert `## Slide 1` / `## Slide 2`, table rendered.

- [x] **Step 5 — OCR signal + PDF detection.** Add `DocumentExtractionError` /
  `NeedsOcrError` (A.2.2); amend `_extract_pdf` to raise `NeedsOcrError` on a
  text-less PDF (A.2.7). _Test:_ a PDF whose pages extract to "" raises
  `NeedsOcrError`; a normal text PDF still returns text. (Use a tiny pypdf-built
  fixture or monkeypatch `PdfReader`.)

- [x] **Step 6 — Status literal.** Extend `SemanticDocumentStatus` with
  `extracting`, `needs_ocr` (A.3). _Test:_ `SemanticDocument(status="needs_ocr")`
  validates.

- [x] **Step 7 — documents.py split.** Implement `register_document` +
  `extract_document`; refactor `create_document` to compose them; map
  `NeedsOcrError` → `status="needs_ocr"` (skip indexing) and broad → `error`
  (A.4). _Test:_ extend `test_document_indexing.py` —
  (a) `create_document` of a text file still ends `extracted` + chunks present;
  (b) a needs-OCR PDF ends `needs_ocr`, no chunks, blob still stored, original
  downloadable; (c) `register_document` alone leaves `status="uploaded"`,
  `extract_document` then advances it.

- [x] **Step 8 — Config.** Allow-list += 2 MIME types; raise
  `wren_max_document_bytes`→10 MB; add `wren_document_async_threshold_bytes` +
  `wren_document_ocr_enabled` + `from_env` bindings (A.6). Update `.env.example`
  (A.8). _Test:_ `from_env` picks up the new vars; xlsx/pptx now pass
  `_validate_document`.

- [x] **Step 9 — Async upload routing + access audit.** Wire size-based routing
  into `upload_project_source_document` via `active_job_runner` (A.5). **Then
  audit every document route** (list/upload/text/content/chunks/retrieve/reindex/
  delete/summarize/duplicates) and assert each calls
  `authorize_semantic_project(..., permission=read|write)` with the correct
  permission (mutations ⇒ `write`, reads ⇒ `read`). Fix any that don't. _Test:_
  in `test_document_api.py` — (a) small upload extracts inline (Inline runner);
  (b) an over-threshold upload returns `status="extracting"` then resolves to
  `extracted` after the inline runner completes; (c) **access**: a caller without
  the project's DB access gets 403/404 on upload *and* on
  download/delete/chunks (parametrised).

- [x] **Step 10 — Sweep + green.** `grep -rn "2_000_000\|wren_max_document_bytes\|WREN_MAX_DOCUMENT_BYTES" tests/` and update any 2 MB-default assertions.
  Update `test_specs`/factory tests if extractor/config surface counts changed.
  Run full backend suite + `pre-commit run --files <changed>`; resolve ruff
  (PT018/C416/E501/I001 are the usual offenders — see
  `uploaded_documents_rag_and_crud.md` §8) and mypy.
  _Done when:_ `pytest tests/unit_tests/superset_ai_agent/ -q` green, pre-commit
  clean.

- [x] **Step 11 — Docs.** Update `document_format_support_study.md` (mark Tier 1
  shipped), and append an as-built note here (what landed, deviations). Add the
  new statuses + the OCR/`needs_ocr` seam to
  `uploaded_documents_rag_and_crud.md` §8 so the RAG-stage agent inherits context.

> **Frontend (optional, separate pass):** the editor already renders documents and
> a status; surface `needs_ocr` / `extracting` as badges and accept the new file
> types in the upload picker. Not required for backend correctness — split into a
> follow-up if not in this pass.

---

## PART C — Test matrix (what "green" means)

| Area | File | Key assertions |
|---|---|---|
| CSV→MD | `test_document_extractors.py` | header + `---`, padding, pipe-escape, empty→"" |
| XLSX | `test_document_extractors.py` | per-sheet `## Sheet:`, tables, missing-dep `ValueError` |
| PPTX | `test_document_extractors.py` | per-slide `## Slide n`, table + text, missing-dep `ValueError` |
| PDF OCR signal | `test_document_extractors.py` | text-less → `NeedsOcrError`; normal → text |
| Status | schema test | `needs_ocr`, `extracting` validate |
| documents split | `test_document_indexing.py` | inline still `extracted`+chunks; `needs_ocr` no chunks, blob kept; register→extract staging |
| Async route | `test_document_api.py` | small=inline; large=`extracting`→`extracted`; **DB-access denial 403/404 on read+write routes** |
| Regression | existing suite | 2 MB-default tests updated; no enrichment-path change |

---

## PART D — Risks, seams & deferred work

- **OCR (seam only):** `NeedsOcrError` + `status="needs_ocr"` +
  `wren_document_ocr_enabled` flag are the entire OCR surface here. A future pass
  slots a backend into `extract_document`'s `needs_ocr` branch. No OCR code now.
- **Vectorisation/RAG (imminent next, out of scope):** guaranteed by the
  canonical-text contract (A.1) — markdown tables + `## Sheet/Slide` headers chunk
  cleanly through the *unchanged* pipeline. Deferred RAG-stage item: **table-aware
  chunking** (a >2k-char table currently hard-splits mid-row) and **per-chunk
  provenance metadata** (parse from the section headers we now emit).
- **xlsx formulas lost** under `data_only=True` (values kept). Acceptable for
  semantic text; record it.
- **Scale:** large files now go async, but extraction still runs on a **daemon
  thread in one worker** (`ThreadJobRunner`); status lives on the document row
  (cross-worker visible via DB store). True multi-worker/durable queuing (Celery)
  remains the documented future (`jobs.py:24-27`). **Coverage reporting** is still
  the dominant cost on large docs (rebuilds the whole MDL fact index per run,
  `coverage.py:174-260`) — unchanged here, flagged in the study.
- **Access model:** unchanged by design — `authorize_semantic_project` already
  scopes docs by DB access; Step 9's audit makes that a tested invariant rather
  than an assumption.

## PART E — File touch list (for conflict-checking future passes)
`semantic_layer/extractors.py` · `semantic_layer/documents.py` ·
`semantic_layer/schemas.py` · `config.py` · `app.py` (upload route only) ·
`requirements-ai-agent.txt` · `.env.example` · tests:
`test_document_extractors.py`, `test_document_indexing.py`, `test_document_api.py`.
No DB migration. No change to `file_storage.py`, `document_chunks.py`,
`document_retriever.py`, or the enrichment/coverage paths.

---

## PART F — As-built log (READ FIRST on resume)

**Outcome:** all 11 steps landed; backend suite green
(`598 passed, 9 skipped`); my changed code is ruff-, ruff-format-, and
mypy-clean. Uncommitted, in the shared working tree.

### F.1 What landed (by file)
- **`semantic_layer/extractors.py`** — `_XLSX_CONTENT_TYPE` / `_PPTX_CONTENT_TYPE`
  consts; `DocumentExtractionError` + `NeedsOcrError`; `_rows_to_markdown_table`
  + `_escape_cell` (shared GFM-table renderer); `_extract_csv` rewritten to a
  Markdown table; `_extract_xlsx` (openpyxl, `read_only=True, data_only=True`,
  `## Sheet:` blocks); `_extract_pptx` (python-pptx, `## Slide n`, text frames +
  tables); `_extract_pdf` now raises `NeedsOcrError` when pages exist but no text
  layer; dispatch + class docstring updated.
- **`semantic_layer/documents.py`** — split into `register_document` (validate +
  blob + row, `status="uploaded"`) and `extract_document` (reads bytes back from
  blob storage, maps `NeedsOcrError`→`needs_ocr` and skips indexing, broad
  →`error`); `create_document` now composes the two (back-compat for all existing
  callers + tests).
- **`semantic_layer/schemas.py`** — `SemanticDocumentStatus` += `extracting`,
  `needs_ocr`.
- **`config.py`** — allow-list += xlsx + pptx MIME types; `wren_max_document_bytes`
  default `2_000_000`→`10_000_000`; new `wren_document_async_threshold_bytes`
  (1 MB) and `wren_document_ocr_enabled` (reserved seam, unused); `from_env`
  bindings for both.
- **`app.py`** — upload route routes by size: `register_document` then inline
  `extract_document` (≤ threshold) **or** `status="extracting"` +
  `active_job_runner.submit(extract)` (> threshold). Imports updated.
- **`requirements-ai-agent.txt`** — `openpyxl>=3.1,<4.0`, `python-pptx>=1.0,<2.0`
  (installed: openpyxl 3.1.5, python-pptx 1.0.2, lxml 6.1.1, pypdf 5.9.0,
  python-docx 1.2.0). **`.env.example`** — broadened types, 10 MB cap, async
  threshold, OCR-seam var.
- **Tests** — `test_document_extractors.py` (+11), `test_document_indexing.py`
  (+4: register/extract staging, needs_ocr), `test_document_api.py` (+4: inline vs
  background routing, deferred-runner mid-flight `extracting`, oversize 400),
  `test_config.py` (+ async-threshold + ocr asserts).

### F.2 Access audit result (Step 9) — PASS, no code change needed
All 13 document routes already enforce DB-scoped access: project routes via
`authorize_semantic_project` (resolves the project's DB → `require_project_permission`),
document routes via `authorize_semantic_scope` / `_load_authorized_document`
(checks the document `scope`, which carries `database_id`). Mutations use `WRITE`,
reads use `READ`. **DB-scoped write-denial is canonically covered** by
`test_semantic_layer_access.py` (read-only principal → `PermissionError`; the
upload route uses `permission="write"`). I did not duplicate that at the API layer
(the API harness uses a permissive service-account identity), per scope discipline.

### F.3 Deviations from the spec
- **Async-routing tests** use a custom `_DeferredJobRunner` to deterministically
  observe the mid-flight `extracting` state (the spec left this implicit). The
  `needs_ocr`-via-API and real-image-PDF cases are covered at the unit layer
  (`test_document_indexing.py`, `test_document_extractors.py`) rather than the API
  layer, since the harness can't synthesize a true scanned PDF cheaply.
- **Frontend pass not done** (it was marked optional). `extracting` / `needs_ocr`
  badges and the upload picker's new accepted types are still TODO — flagged as a
  gap below.

### F.4 Known gaps / risks (for the next pass)
- **UI not updated:** the editor won't show `extracting`/`needs_ocr` distinctly,
  and the upload picker may not advertise `.xlsx`/`.pptx` `accept` types. Backend
  accepts them regardless. *Expectation gap if a user uploads a scanned PDF: they
  see `needs_ocr` only via the API/status, not a friendly UI message yet.*
- **Pre-existing lint/type debt (NOT mine):** `test_config.py` has 9 pre-existing
  ruff `S108`/`S105` findings (test literals), and the wider tree has 41 pre-existing
  mypy errors (28 in `persistence/models.py`) — all present at HEAD, none in files
  I changed. Left untouched to keep this diff scoped.
- **xlsx formulas not preserved** (`data_only=True` → values only).
- **RAG seam intact, unused:** markdown tables + `## Sheet/Slide` headers chunk
  through the existing pipeline unchanged. Deferred RAG items remain table-aware
  chunking + provenance metadata (Part D).
- **Scale:** background extraction is a daemon thread on one worker
  (`ThreadJobRunner`); durable queueing still future. Coverage reporting cost on
  large docs unchanged.
