# Extending Document Upload for BI — Format-Support Feasibility Study

> **Status update:** **Tier 1 is IMPLEMENTED & green** (xlsx + pptx, CSV→Markdown
> tables, size cap → 10 MB, background extraction for large files, image-only PDF
> → `needs_ocr` tagging). See `document_format_tier1_plan.md` Part F (as-built) for
> the landed details. Tier 2 (OCR backend) and Tier 3 remain deferred. RAG over
> these documents is the imminent next stage (seam already in place).

**Scope:** Evaluate extending `superset_ai_agent` document upload to the document
types common BI teams actually have. Covers (1) current capability, (2) the
industry-standard parser landscape, (3) a tech-stack evaluation against the
decision points raised. Companion to `uploaded_documents_rag_and_crud.md`
(the as-built RAG/CRUD suite) — read that first for the ingestion pipeline.

**Hard constraint (unchanged):** we do **not** touch the upload→MDL enrichment
hot path (`enrich_project_document` / `propose_mdl_from_document`). Everything
here is additive at the extractor / storage / config layer.

---

## 1. Current capability (as-built)

### 1.1 Formats accepted today — **7**
`config.py:135-143` (`wren_allowed_document_types`):

| MIME | Class | Parser | Output fidelity |
|---|---|---|---|
| `text/plain` | text | stdlib decode | text (1:1) |
| `text/markdown` | text | stdlib decode | text (1:1) |
| `text/html` | text | stdlib `html.parser` (`_HtmlTextExtractor`) | visible text, tags stripped |
| `application/pdf` | text/mixed | **pypdf** | text layer only |
| `…wordprocessingml.document` (.docx) | text | **python-docx** | paragraph text |
| `text/csv` | tabular | stdlib `csv` | rows flattened to `a \| b \| c` |
| `application/json` | tabular | stdlib `json` | canonicalised (sorted-key) text |

Source: `extractors.py:49-167`. Missing optional dep (pypdf / python-docx) →
`ValueError`, caught upstream → document saved with `status="error"`
(`documents.py:117-124`). Deps pinned in `requirements-ai-agent.txt:49-50`
(`pypdf>=4.0,<6.0`, `python-docx>=1.1,<2.0`).

### 1.2 The defining characteristic: **everything collapses to plain text**
Every extractor emits a flat string. Tables lose their grid (CSV → pipe-joined
rows; DOCX/PDF tables → run-on text); headings, sheets, slides, and cell
coordinates are gone before chunking. This is fine for prose enrichment but is
the root limitation for *tabular* and *graphical* BI content (see §3a).

### 1.3 What's persisted — original bytes **and** parsed text (already built)
Contrary to a "text-only store," the suite already keeps the **original file**:

- `file_storage.py:26-138` — `DocumentStorage` protocol with **two backends**:
  `LocalDocumentStorage` (`{agent_storage_dir}/documents/{id}/{file}`) and
  `S3DocumentStorage` (`s3://…`), selected by `document_storage` config
  (`config.py:99-103`).
- Per document we store: original blob, SHA256 `checksum`, `storage_uri`,
  `extracted_text` (≤ limit), `extracted_text_preview` (2k), `summary`, `status`
  (`persistence/models.py:125-149`).
- Download endpoint returns the **original bytes** (`app.py:2316-2338`,
  `Content-Disposition: attachment`).

**Implication for the study:** the "store original vs. store text only" question
(3b1) is *already decided in favour of storing both* — see §3b1.

### 1.4 Scale limits in force today
| Limit | Value | Where |
|---|---|---|
| Max upload size | **2 MB** | `wren_max_document_bytes` (`config.py:127`) |
| FastAPI body default | ~25 MB | Starlette default (not overridden) |
| Extracted-text retention | 200k chars (~50k tok) | `wren_document_extract_char_limit` (`config.py:133`) |
| Enrichment prompt budget | 20k chars | `wren_document_prompt_char_budget` (`config.py:134`) |
| Chunk size | 2k chars | `_DEFAULT_MAX_SECTION_CHARS` (`document_chunks.py:44`) |
| Chunks/doc (effective) | ~100 | 200k ÷ 2k |
| Retrieval top-k | 8 | `wren_document_retrieve_k` (`config.py:149`) |
| File count | **uncapped** | — (LanceDB + blob grow unbounded) |

Extraction is **synchronous on the request thread** (`documents.py:92-116`) — no
background job for ingestion. Coverage-report cost is the other scaling axis
(§3b2).

---

## 2. Industry standard — formats & parsers

Two paradigms dominate in 2026 ([LlamaIndex parser comparison](https://www.llamaindex.ai/insights/document-parser-comparison-2025),
[Mixpeek](https://mixpeek.com/curated-lists/best-document-parsing-tools)):

1. **Rule/heuristic engines** — fast, cheap, self-hosted, format-specific:
   Apache Tika (1000+ types), Unstructured.io (30+), Microsoft **MarkItDown**
   (everything → Markdown), pypdf/PyMuPDF, openpyxl, python-pptx.
2. **Layout/vision engines** — higher fidelity on complex/scanned docs, heavier
   or paid: IBM **Docling** (open-source, ML layout), **LlamaParse** (VLM, API),
   **Mistral OCR**, cloud OCR (AWS Textract, Azure Document Intelligence, Google
   Document AI), Tesseract / **PaddleOCR** (self-hosted OCR).

Selected, relevant tools:

| Tool | License / host | Covers | Notes for us |
|---|---|---|---|
| **MarkItDown** (Microsoft) | MIT, self-host | pdf, docx, **xlsx**, **pptx**, html, images(+OCR), audio | One lib → Markdown; ~30-50% fewer tokens than raw text ([AIBuilderClub](https://www.aibuilderclub.com/blog/markitdown-microsoft-convert-files-markdown-llm)). Lowest-maintenance breadth play. |
| **openpyxl** / **python-pptx** | MIT/BSD, self-host | xlsx / pptx | Targeted, tiny deps; pair with a Markdown-table emitter. |
| **IBM Docling** | MIT, self-host | pdf/docx/pptx layout → JSON/MD | Best open table/layout fidelity; ships ML models → heavier, slower; non-PDF still maturing ([Mixpeek](https://mixpeek.com/curated-lists/best-document-parsing-tools)). |
| **PaddleOCR 3.0** | Apache-2, self-host | scanned PDF/image OCR + tables | Recommended for self-hosted RAG with no per-page cost ([MarkTechPost](https://www.marktechpost.com/2025/11/02/comparing-the-top-6-ocr-optical-character-recognition-models-systems-in-2025/)). |
| **Tesseract 5.5** | Apache-2, self-host | OCR (100+ langs) | Reliable on clean print, weak on tables. |
| **LlamaParse / Reducto** | API, paid | complex+scanned, charts | Best accuracy; **data leaves the trust boundary** + per-page cost. |
| **AWS Textract / Azure DI / Google Doc AI** | API, paid | scanned, tables, forms, KV | Layout-aware JSON; external dependency + cost. |
| **VLM (GPT-4o / Claude / Mistral OCR)** | API or self-host | charts, scanned, diagrams | We already call an LLM (`model_client`) — architecturally natural for graphical content. |

Note: Unstructured.io's quality is reported to have regressed and is "not
recommended anymore" in 2025 roundups ([LlamaIndex](https://www.llamaindex.ai/insights/document-parser-comparison-2025))
— I would not adopt it.

---

## 3. Evaluation against the decision points

### 3a1 — Text-first documents
**Already strong.** txt/md/html/docx + PDF-text-layer cover the canonical
enrichment inputs: **data dictionaries, business glossaries, metric/KPI
definitions, runbooks, onboarding/governance docs, requirements specs, and
Confluence/Notion/Google-Docs exports** (which export as HTML/MD/DOCX/PDF — all
supported). This is the highest-value class and it maps directly to MDL
enrichment (turning prose definitions into model/column/metric descriptions).

- **Gaps (low value):** `.rtf`, `.odt` (OpenDocument), `.epub`, `.eml/.msg`
  email. Rare in BI; defer.
- **Quality gap (real):** HTML/DOCX/PDF **tables** inside otherwise-text docs are
  flattened. A Markdown-table-aware extractor (MarkItDown/Docling) would lift
  retrieval quality even for "text-first" files.

### 3a2 — Tabular-first documents
**Biggest, highest-value gap.**

- **Excel `.xlsx` is unsupported** — and Excel is the BI lingua franca for data
  dictionaries, column-mapping sheets, KPI catalogs, and sample data. Add via
  **openpyxl** (each sheet → Markdown table, preserve header row, label sheet
  names) or **MarkItDown**. Multi-sheet workbooks should chunk per sheet.
- **CSV is supported but lossily** — `a | b | c` pipe rows drop the header→value
  association that makes a column glossary retrievable. Re-emit CSV as a
  Markdown table (header row + alignment) so chunks carry column semantics.
  Detect title/description rows above the header ([Medium/RAG-over-Excel](https://medium.com/@sangitapokhrel911/parsing-word-csv-excel-json-and-sql-data-for-retrieval-augmented-generation-rag-a0798b8d5405)).
- **JSON** is canonicalised but nested structures flatten poorly; acceptable.
- **Out of scope (belongs to the dataset path, not docs):** Parquet/Avro/ORC and
  raw warehouse extracts. These are *data*, ingested through Superset datasets —
  not enrichment documents. Don't route them here.

**Business value: highest of the three.** xlsx + better CSV directly improve the
semantic descriptions an analyst gets, because that's where definitions live.

### 3a3 — Image-based / graphically-encoded content
**Unsupported, and partly invisible today.** A scanned (image-only) PDF passes
the allow-list, pypdf finds no text layer, and it saves as `status="extracted"`
with near-empty text — a **silent** failure. Standalone images (PNG/JPG) are
rejected.

Options, cheapest → richest:
1. **Detect & flag image-only PDFs** (text-layer length ≈ 0) → mark
   `status="needs_ocr"` instead of silently empty. *Near-zero cost; do this
   regardless.*
2. **Self-hosted OCR** — PaddleOCR (best self-host tables) or Tesseract (clean
   print). No per-page cost, stays in-boundary, but adds a native/model dep and
   latency.
3. **VLM page rendering** — render page → image → vision LLM via the existing
   `model_client`. Best for **charts, ER diagrams, dashboard screenshots** where
   meaning is graphical, not textual. Per-page token cost.
4. **Cloud OCR** (Textract/Azure DI/Google) — best accuracy on messy scans/forms,
   but external dependency + cost + **data leaves the operator boundary**.

**Business value: medium, effort high, fidelity fuzzy** (a chart → reliable
semantic fact is not guaranteed). Recommend (1) now; (2)/(3) as an opt-in
backend; (4) only operator-configured.

### 3a4 — Other formats worth a position
- **PPTX (PowerPoint)** — exec decks, KPI/governance slides; medium-high value,
  cheap via python-pptx or MarkItDown. Best ROI after xlsx.
- **TSV** — trivial (CSV path with `\t`).
- **RTF / ODT / ODS / EPUB / email** — low BI value; defer.
- **Parquet/Avro/ORC** — explicitly **not** documents (see 3a2).

### 3b1 — Store original file, or only parsed text?
**Already built and already storing both** (`file_storage.py`, Local + S3,
`storage_uri` + checksum + download endpoint). So the marginal *new* dev cost to
keep originals is **zero** — the question is really "should we keep it?" and the
answer is **yes**:

- **Re-parse without re-upload.** Storing the original is what lets us upgrade a
  parser (add OCR, swap pypdf→Docling) and `reindex_document` *existing* files.
  Text-only would force users to re-upload every doc on every parser improvement —
  a strict regression given we're about to broaden parsing.
- **Download / audit / dedup-by-checksum** already depend on the blob.
- **Stability cost: low** — the path is implemented and tested; S3 is optional
  and mirrors the existing optional-embedder pattern (degrade gracefully when
  unset). Dropping original storage would *remove* capability, not add it.

**Recommendation: keep original-file storage; it is the enabler for the entire
parser-upgrade roadmap.** The only operational cost is disk/object-store growth,
addressed by the S3 backend + an optional retention/prune policy.

### 3b2 — What scale can we run at?
**File size.** 2 MB is too low for real xlsx/pptx/PDF. Raise
`wren_max_document_bytes` to ~10-25 MB (stay under the 25 MB Starlette body
default, or raise both). **But** size interacts with two real ceilings:
- **Synchronous extraction blocks the worker.** A large PDF/xlsx — and *any* OCR
  or VLM pass — can take seconds-to-minutes. Above a few MB, ingestion should
  move to the existing background-job mechanism (`semantic_layer/jobs.py`), not
  run inline on the upload request.
- **200k-char retention ≈ 50k tokens.** Big documents truncate to whole sections
  at ingestion; that's by design, but a 25 MB scanned report will far exceed it.

**File count.** Uncapped today. Blob store + LanceDB (`document_chunks`
collection, ~100 chunks/doc) grow unbounded — fine for hundreds–thousands of
docs; for more, add prune/archival and an S3 backend.

**Coverage reporting is the dominant scaling cost** (`copilot/coverage.py`):
- It rebuilds the **entire project MDL fact index per run** — O(models × columns
  × metrics × relationships × instructions) (`coverage.py:174-260`).
- It is **LLM-bound**: O(claims × votes × candidate-facts) judge calls
  (`coverage.py:325-429`). Big document + big MDL + `votes>1` = many LLM calls.
- Cache is **per-worker in-memory** (`InMemoryCoverageCache`, `coverage.py:511`)
  — no shared/persistent cache; cold on every worker.

So coverage scales with *document size × MDL size × votes*, not just document
count. Before pushing large documents through it: cap claim count, scope facts to
likely-relevant models, default `votes=1`, and consider a persistent cache.

---

## 4. Tech-stack evaluation & recommendation

**Current stack:** pure-Python, stdlib + pypdf + python-docx, synchronous,
degrade-closed, plain-text output. *Strengths:* lightweight, no native deps,
self-hosted, zero per-page cost, fully inside the trust boundary. *Weaknesses:*
flattens structure, no Excel, no OCR, silent on scanned PDFs, blocks on big
files.

Given Superset's **self-hosted, governance-sensitive, degrade-closed** posture
(the same posture that made embedder/S3/LanceDB all *optional* backends), the
recommended direction is **self-hosted, optional-dependency parsers with
structure-preserving (Markdown) output**, escalating to vision/cloud only as
operator-configured opt-ins.

### Recommended roadmap (value ÷ cost)

**Tier 1 — high value, low cost (do first):**
1. **Add `.xlsx`** (openpyxl → per-sheet Markdown tables, keep headers).
2. **Add `.pptx`** (python-pptx → per-slide text/tables).
3. **Upgrade CSV** output from pipe-rows to Markdown tables (header-aware).
4. **Raise size cap** for these types (~10-25 MB) **and route >~2 MB / OCR / VLM
   ingestion to background jobs** (`jobs.py`) instead of the request thread.
5. **Flag image-only PDFs** as `needs_ocr` instead of saving empty text.

   *Optional consolidation:* adopt **MarkItDown** as a single converter for
   pdf/docx/xlsx/pptx/html → Markdown, replacing several bespoke extractors and
   cutting maintenance + tokens. Evaluate its dependency weight against the
   targeted-lib approach; either is defensible.

**Tier 2 — medium value, medium cost (opt-in backend):**
6. **OCR for scanned PDFs/images** behind a config flag, mirroring the optional-
   embedder pattern: **PaddleOCR** (self-host, tables, no per-page cost) as
   default; **VLM via existing `model_client`** for chart/diagram comprehension.
   Degrade-closed when unconfigured.

**Tier 3 — defer:** RTF/ODT/EPUB/email; cloud OCR (Textract/Azure DI) only as an
operator-configured backend for teams that accept the external dependency;
Parquet/Avro stay on the dataset path, never the document path.

**Cross-cutting:** keep original-file storage (§3b1) as the re-parse enabler;
make coverage incremental/scoped before sending large docs through it (§3b2).

### What I'd explicitly avoid
- **Unstructured.io** — reported quality regression in 2025.
- **Default cloud/LlamaParse** — breaks the self-hosted/in-boundary default and
  adds per-page cost; acceptable only as an operator opt-in.
- **Routing data files (Parquet/CSV-as-data) into enrichment** — wrong pipeline.

---

## 5. Sources
Codebase: `config.py`, `extractors.py`, `documents.py`, `file_storage.py`,
`persistence/models.py`, `document_chunks.py`, `copilot/coverage.py`, `app.py`
(file:line cites inline above). Industry:
[LlamaIndex parser comparison 2025](https://www.llamaindex.ai/insights/document-parser-comparison-2025) ·
[Mixpeek best parsing tools 2026](https://mixpeek.com/curated-lists/best-document-parsing-tools) ·
[Apache Tika](https://github.com/apache/tika) ·
[MarkItDown guide](https://www.aibuilderclub.com/blog/markitdown-microsoft-convert-files-markdown-llm) ·
[Top OCR models 2025 (MarkTechPost)](https://www.marktechpost.com/2025/11/02/comparing-the-top-6-ocr-optical-character-recognition-models-systems-in-2025/) ·
[Best OCR software (LlamaIndex)](https://www.llamaindex.ai/insights/best-ocr-software) ·
[Parsing Word/CSV/Excel/JSON for RAG](https://medium.com/@sangitapokhrel911/parsing-word-csv-excel-json-and-sql-data-for-retrieval-augmented-generation-rag-a0798b8d5405) ·
[ks excel-parser](https://github.com/knowledgestack/excel-parser)
