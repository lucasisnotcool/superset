# Uploaded Documents: RAG, Viewer & Agentic CRUD — Implementation Plan

> Status: **Phase 0–1 implemented & green; Phase 2–4 ready to build.** Source-backed
> checklist for turning uploaded documents into first-class, RAG-enabled, viewable,
> agent-mutable artifacts.
>
> **Coordinated with [`wren_mdl_copilot.md`](wren_mdl_copilot.md)** — the "Wren MDL
> Copilot" effort is now **finished** (uncommitted in the shared tree). This plan is
> the **persistent document RAG + CRUD lane**; Copilot owns the **editor shell,
> workspace tree, tool-calling (`MdlToolset`), and the agentic edit loop**. Wherever
> the two meet, this plan *consumes* Copilot infrastructure rather than duplicating
> it. The concrete, re-anchored integration map is **§0.6**; the prior coordination
> rules are **§0.5** (kept for history, interlocks now resolved).
>
> Scope boundary (product direction): **we do NOT touch the upload→MDL hot path**
> (`enrich_project_document` → `LlmWrenClient.propose_mdl_from_document`).
> Everything here is additive around *storage, display, retrieval, and CRUD* of
> the original document.

---

## 0. Background — how documents are handled today

All paths below are under `superset_ai_agent/` unless noted.

### 0.1 Documents are already persisted (twice)

Every upload is durably stored before extraction runs
([`semantic_layer/documents.py:36`](semantic_layer/documents.py) `create_document`):

| Layer | What | Where |
| --- | --- | --- |
| Blob | Raw original bytes | `LocalDocumentStorage` → `file://{agent_storage_dir}/documents/{id}/{file}` **or** `S3DocumentStorage` → `s3://…` ([`semantic_layer/file_storage.py`](semantic_layer/file_storage.py)) |
| Relational | Metadata + full extracted text | table `ai_agent_semantic_documents` ([`persistence/models.py:133`](persistence/models.py) `AiAgentSemanticDocument`) |

There is **no delete, no TTL, no cleanup**. The raw file is *not* discarded
after enrichment; generated MDL back-references it via
`MdlFile.source_document_id`. In Wren's workspace layout (Copilot §1.1) these are
the **`raw/`** source documents.

### 0.2 Storage row columns (`AiAgentSemanticDocument`)

`id, project_id, owner_id, database_id, catalog_name, schema_name, dataset_ids,
filename, content_type, size_bytes, checksum (sha256), storage_uri, status
(uploaded|extracted|error), summary, extracted_text, extracted_text_preview,
warnings (JSON), error, created_at, updated_at`. Indexed on
`project_id, owner_id, database_id, checksum, status`.

### 0.3 Extraction is text-only (the one real capability gap)

[`semantic_layer/extractors.py`](semantic_layer/extractors.py)
`CompositeDocumentExtractor` supports exactly: `text/plain`, `text/markdown`,
`text/csv`, `application/json`. **PDF / DOCX / XLSX / HTML / images raise
`ValueError`.** Allowed types further gated by config
`wren_allowed_document_types`; max size `wren_max_document_bytes` (2 MB); extract
truncated at `wren_document_extract_char_limit` (200 K).

### 0.4 Document → LLM selection is keyword matching, NOT RAG

[`semantic_layer/document_chunks.py`](semantic_layer/document_chunks.py):
- `chunk_sections(text, *, max_chars)` — splits on blank lines, hard-splits
  oversize blocks. **Chunking already exists.**
- `select_relevant_sections(text, *, terms, budget)` — ranks chunks by
  set-intersection token overlap with schema terms, greedily packs into a char
  budget. **No embeddings touch document chunks.** Chunks are computed
  per-enrichment and thrown away (never persisted, never vectorized).

### 0.5 RAG infrastructure already exists — just not pointed at documents

The vector machinery is built and shipping for *MDL schema / SQL pairs /
instructions*:

- Embedders: `OpenAiEmbedder`, `OllamaEmbedder`, `NullEmbedder` +
  `create_embedder(config)` ([`llm/embeddings.py`](llm/embeddings.py)).
  `Embedder` protocol: `is_available()`, `dimensions()`, `signature()`,
  `embed(texts)`.
- Retrievers: `KeywordRetriever`/`EmbeddingRetriever`/`LanceDbRetriever` behind a
  `Retriever` protocol, index-once-per-checksum, degrade-closed to keyword
  ([`semantic_layer/schema_retriever.py`](semantic_layer/schema_retriever.py)).
- Per-row mutable vector store: `LanceVectorCache` —
  `upsert/remove/search`, embedder-signature-namespaced tables
  ([`semantic_layer/vector_cache.py`](semantic_layer/vector_cache.py)). **This is
  the exact primitive document-chunk RAG needs.**

### 0.6 Existing document API surface (`app.py`, FastAPI)

| Method | Path | Handler | Status |
| --- | --- | --- | --- |
| POST | `/agent/semantic-layer/documents` | `upload_semantic_document` | ✅ |
| POST | `/agent/semantic-layer/projects/{pid}/documents` | `upload_project_source_document` | ✅ |
| POST | `/agent/semantic-layer/projects/{pid}/documents/text` | `create_project_source_document_from_text` | ✅ |
| POST | `…/documents/{id}/enrich` | `enrich_project_document` | ✅ (hot path — untouched) |
| GET | `/agent/semantic-layer/documents` | `list_semantic_documents` | ✅ |
| GET | `/agent/semantic-layer/documents/{id}` | `get_semantic_document` | ✅ |
| **DELETE / PUT / download / chunks** | — | — | ❌ **absent** |

Store protocol ([`semantic_layer/store.py`](semantic_layer/store.py)) confirms the
gap: `save_document, list_documents, list_project_documents, get_document,
update_document` — **no `delete_document`**. (Only `MdlFile` has soft-delete.)

---

## 0.5 Coordination with Wren MDL Copilot (READ FIRST)

The Copilot effort ([`wren_mdl_copilot.md`](wren_mdl_copilot.md)) is **in flight**
(working tree shows its Phase 0: `llm/*` tool-calling, `config.py`,
`semantic_layer/copilot/`, `test_model_client_tools.py`). It restructures the
exact surfaces this plan originally targeted. The two plans are now explicitly
divided:

| Concern | Owner | Why |
| --- | --- | --- |
| LLM **tool-calling** contract (`llm/base.py` + providers) | **Copilot** | This plan's agent tools (§4.7) ride on it; do not re-invent. |
| **`ToolRegistry`** (`semantic_layer/copilot/tools.py`) | **Copilot** | Document agent tools register here, not in a parallel mechanism. |
| **Workspace tree** UI + `GET …/workspace` (`WorkspaceNode`, incl. `kind="document"`) | **Copilot** | Documents are `raw/` nodes in *their* tree. This plan supplies the **document node detail pane**, not a competing browser. |
| **Agentic edit loop / Changeset / Copilot chat** | **Copilot** | MDL-file CRUD with diff review. Disjoint from document CRUD. |
| **Conversation attachments** (inline, no-RAG, no-persist) | **Copilot** | The *ephemeral* lane. This plan is the *persistent* lane (§0.7). |
| Document **storage, chunking, embedding, retrieval, dedup, viewer, CRUD** | **This plan** | The persistent document RAG corpus. |
| Broadened **extraction** (PDF/DOCX/HTML) | **This plan** | Copilot never touches `extractors.py`. |

### Retractions vs the prior draft of this plan
- ❌ **Retracted:** a standalone "Documents tab" in `SemanticLayerEditor`. The
  Copilot Workspace tree already enumerates `raw/` documents (`WorkspaceNode.kind
  == "document"`). A second browser would duplicate UI and product surface.
- ✅ **Replaced with:** a **document detail/RAG pane** rendered in the center
  (Editor/Diff) area when a `raw/` document node is selected in the Copilot
  Workspace tree.

### Interlocks (must be coordinated, not assumed)
1. **Migration numbering.** Both plans add migrations after
   `0006_drop_semantic_overlay`. **This plan claims `0007_document_chunks`**
   (Phase 2, earlier); Copilot's snapshot/versioning migration (its Phase 7)
   must take the **subsequent** number with `down_revision="0007_document_chunks"`.
   One linear alembic chain — no fork. *Confirm with the Copilot agent before
   writing the file.*
2. **Shared working tree.** If both agents share one tree (the Copilot edits are
   visible in `git status`), avoid full-file `Write` on shared files; use scoped
   `Edit` on disjoint regions, and land shared-file edits **after Copilot commits
   its Phase 0** (§5 sequencing). If isolated worktrees, conflicts resolve at
   merge time instead.
3. **Tool registration.** Document agent tools (§4.7) are registered through the
   Copilot `ToolRegistry`; they then surface read-only in the Copilot Inspector
   (its FR6 Tools tab) automatically — no extra UI.

## 0.6 RE-ANCHOR — Copilot finished; concrete integration map

The Copilot work has **landed** (uncommitted, shared tree). The §0.5 interlocks are
**resolved** and the integration targets are now concrete symbols, not assumptions.
The previously yellow/red shared files are **stable** (Copilot no longer editing),
so **Phase 2–4 are unblocked**.

### Interlock status
1. **Migration — RESOLVED.** Copilot added **no** migration. `0007_document_chunks`
   is the sole head (`alembic heads → ['0007_document_chunks']`). No fork.
2. **Shared tree — settled.** Nothing committed yet; both change-sets coexist.
   Copilot is done, so shared-file edits (`app.py`, `config.py`, `schemas.py`) are
   now safe scoped `Edit`s. `config.py`/`app.py`/`schemas.py` currently hold *only*
   Copilot's edits (this plan never touched them) — a clean base.
3. **Tool registration — concrete.** The registry is **`MdlToolset`**
   ([`copilot/tools.py`](semantic_layer/copilot/tools.py)) with `specs()` +
   `dispatch()` + `_handler` methods and **constructor DI**. Document tools extend it.

### Landed integration points (file:symbol)

| Target | Where | State | This plan's action |
| --- | --- | --- | --- |
| LLM tool-calling | [`llm/base.py`](llm/base.py) `ToolSpec`/`ToolCall`/`ModelResult.tool_calls`, `chat(..., tools=)` | ✅ done | Tools return `dict`; conform to `ToolSpec`. |
| Tool registry | [`copilot/tools.py`](semantic_layer/copilot/tools.py) `MdlToolset.specs()/dispatch()`, ctor `(files, *, schema_index, deep_validate)` | ✅ MDL-only | Extend ctor with `document_index/document_store/project_id/owner_id`; add specs+handlers+dispatch entries. |
| Tool invocation | [`copilot/service.py`](semantic_layer/copilot/service.py) `run_copilot(...)` builds the toolset | ✅ | Thread document deps through `run_copilot` → `MdlToolset`. |
| Workspace schema | [`copilot/schemas.py`](semantic_layer/copilot/schemas.py) `WorkspaceNode` (`kind` incl. `"document"`, has `file_id`) | ⚠️ `"document"` kind exists; **no `document_id` field** | Add `document_id: str \| None` to `WorkspaceNode` (selection key for doc nodes). |
| Workspace builder | [`copilot/workspace.py`](semantic_layer/copilot/workspace.py) `build_workspace_tree(files, *, instruction_count, document_count, …)` | ⚠️ takes `document_count`, **does not enumerate docs**; route doesn't pass it | Accept `documents: list[SemanticDocument]`; emit child `kind="document"` nodes under `raw/`. |
| Workspace route | `app.py` `get_project_workspace` (~1326) — `authorize_semantic_project(req, pid, owner_id=, permission="read")`, `_require_copilot_enabled()` | ✅ | Pass `documents=` into the builder; this is the **auth pattern to mirror** for project doc routes. |
| App wiring | `app.py` create_app, after `active_retriever = …` (~305) | ✅ slot exists | Add `active_document_index = create_document_index(app_config, active_embedder)`. |
| Config | [`config.py`](config.py) has `wren_max_document_bytes`, `wren_document_extract_char_limit`, `wren_document_prompt_char_budget`, `wren_allowed_document_types`, `wren_vector_index` | ✅ | Add `wren_document_indexing_enabled` (gate), `wren_document_retrieve_k`, `wren_document_dup_threshold` (+ `from_env`). |
| Frontend shell | [`SemanticLayerEditor/index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx) — 3-pane grid; `WorkspaceTree` (Models tab); center = `EditorHost` for MDL | ✅ | Add `selectedDocumentId` state + center-pane branch: `kind==='document'` → `DocumentDetailPane`. |
| Workspace tree (FE) | [`WorkspaceTree.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/WorkspaceTree.tsx) — `selectable: node.kind==='mdl'`, `onSelectFile(fileId)` | ⚠️ docs not selectable | Make `kind==='document'` selectable; add `onSelectDocument(documentId)` (or widen `onSelect`). |
| API client | [`api.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts) — `WorkspaceNode` type, doc fns present | ✅ | Add `deleteDocument/downloadDocument/listDocumentChunks/retrieveDocumentChunks/findDuplicateChunks/summarizeDocument` + `DocumentChunk` type; add `document_id` to `WorkspaceNode`. |

### Corrected assumptions (vs the original draft)
- Registry is **`MdlToolset`**, not a generic `ToolRegistry`; tools are **constructor-injected**, not globally registered.
- Project-route auth is **`authorize_semantic_project(..., permission="read"|"write")`** + `_require_copilot_enabled()`, *not* `authorize_semantic_scope`/`SemanticPermission`. Document-id routes (chunks/content/delete/reindex/summarize) mirror the **existing** `get_semantic_document` auth (scope-resolved).
- The center pane is an **Ace `EditorHost`** (no Monaco); a read-only document pane sits beside it under a `kind==='document'` branch.
- `WorkspaceNode` needs a new **`document_id`** field; reusing `file_id` for documents would be semantically wrong and is avoided.
- **Decision:** gate new document RAG/CRUD routes behind a dedicated
  `wren_document_indexing_enabled` flag (default off) rather than `wren_copilot_enabled`,
  so document features can ship independent of the copilot loop. Existing upload/list/get
  routes stay ungated (unchanged).

## 0.7 Two document lanes — keep them distinct
- **Ephemeral (Copilot FR7):** message attachments, inlined into the prompt,
  no RAG, no persistence. Owned by Copilot.
- **Persistent (this plan):** uploaded `raw/` documents, chunked + embedded +
  indexed, viewable and agent-mutable. Owned here.
- *Future convergence (out of scope):* an attachment could be "promoted" to a
  persistent document; note as a seam, do not build.

---

## 1. Goals, non-goals, requirements

### 1.1 Goals
1. **View** persisted documents + extracted text (document detail pane inside the
   Copilot Workspace tree).
2. **Document RAG** — chunk, embed, index documents for semantic retrieval by the
   agent and (optionally) the enrichment selector; surface view-chunks and
   duplicate-chunk detection.
3. **Agentic CRUD** — delete, re-index, summarise, dedup via API + tools
   registered in the Copilot `ToolRegistry`, keeping blob/row/vector consistent.
4. **Broaden extraction** beyond plain text (PDF/DOCX/HTML).

### 1.2 Non-goals
- No change to `enrich_project_document` contract or the doc→MDL prompt (hot path
  frozen). RAG may *optionally* feed the selector behind a flag (§5 Phase 4),
  default off.
- **No competing document browser** — integrate with the Copilot Workspace tree.
- No new tool-calling mechanism — reuse Copilot's.
- No new vector DB — reuse LanceDB + `Embedder`.
- No cross-tenant/global search; preserve `owner_id` + scope isolation.

### 1.3 Functional requirements (testable)
- **R1** Uploading an extractable document persists ≥1 chunk row and (when an
  embedder is available) upserts into the document vector store.
- **R2** retrieve(query, scope) over document chunks returns top-k chunk ids by
  cosine when embeddings available, else keyword overlap (degrade-closed).
- **R3** Deleting a document removes its blob, row, **and** all chunk rows +
  vector entries (no orphans).
- **R4** Re-indexing re-chunks + re-embeds idempotently; embedder-signature change
  forces a rebuild.
- **R5** Duplicate detection returns chunk pairs above a cosine threshold and/or
  identical chunk checksums, within a document and across a project.
- **R6** The document detail pane renders a selected `raw/` document's extracted
  text + chunk list; raw-file download works.
- **R7** Every new endpoint enforces the same `authorize_semantic_scope`
  (`READ` for reads, `WRITE` for mutations) as existing document routes.
- **R8** Document agent tools are invokable through the Copilot agent loop and
  appear in the Copilot Inspector Tools list.

### 1.4 Non-functional
- Degrade-closed everywhere (no embedder / no LanceDB → keyword, still
  functional).
- New Python typed + mypy-clean; ASF headers on new files; new config via
  `AgentConfig` + `from_env`.
- Frontend: `@superset-ui/core/components` only, Emotion + theme tokens, no `any`,
  no direct antd; **build on the Copilot Workspace tree**, do not fork it.

---

## 2. UI design

### 2.1 Entrypoint — document node detail pane (NOT a new tab)

The Copilot Workspace tree (its FR1, `GET …/workspace`) lists `raw/` documents as
`WorkspaceNode { kind: "document" }`. **Selecting a document node renders this
plan's detail pane in the center Editor/Diff area** (where MDL files render their
editor). This reuses the Copilot shell's selection + layout entirely.

```
┌─ Workspace (Copilot) ─┬──── Center pane: DOCUMENT DETAIL (this plan) ─────┐
│ models/               │  raw/orders.md   · text/markdown · 12 KB · ●ext    │
│  orders ●             │   [ Download ] [ Re-index ] [ Summarize ] [Delete] │
│ relationships.json    │  ── sub-tabs: [Text] [Chunks] [Summary] ─────────  │
│ raw/                  │  Text:   <SafeMarkdown / <pre> for csv·json>       │
│  ▸ orders.md   (sel)  │  Chunks: chunk cards — #idx · chars · [dup] · text │
│  ▸ glossary.csv       │          click → scroll Text pane to char range    │
│  ▸ notes.txt   ⚠err   │  Summary: agent summary + [Regenerate]             │
│ target/ mdl.json 🔒   │                                                     │
└───────────────────────┴─────────────────── Copilot chat (Copilot) ────────┘
```

- **Header actions**: `Button` (`@superset-ui/core/components`) — `Download`
  (link to new content endpoint), `Re-index` / `Summarize` (tertiary),
  `Delete` (danger → `ConfirmModal`, the component already used for "Reset
  semantic layer?").
- **Sub-tabs** (`Tabs`): **Text** (`SafeMarkdown` for md, `<pre>` for csv/json),
  **Chunks** (cards: index, char count, duplicate badge, snippet; click scrolls
  Text to `char_start`), **Summary** (agent summary + `Regenerate`).
- **Find duplicates**: a project-level action (toolbar/command), opens a `Modal`
  listing near-duplicate chunk pairs (cosine ≥ threshold) with both snippets and
  a similarity %. May also surface as a Copilot tool (§4.7).

### 2.2 RAG visibility in chat
When the Copilot agent retrieves document chunks (via the §4.7 tool), the chunks
flow through Copilot's existing `agent_step` / trace primitives
(`AgentStepDetail`, new `mdl_edit`/`doc_context` kind) — no new layout. This is a
Copilot-owned rendering; this plan only emits the tool result.

### 2.3 Declared deviations & mitigations

| Deviation | Why | Mitigation |
| --- | --- | --- |
| Document content rendered raw (`SafeMarkdown`/`<pre>`), not the Ace editor used for MDL. | Documents are read-only arbitrary text, not editable JSON. | If "modify document" (§5 Phase 3) ships, switch the Text sub-tab to the Ace `EditorHost` the MDL pane uses. |
| Raw-file `Download` is a direct link bypassing the `requestJson` wrapper. | `requestJson` assumes JSON; binary needs raw `fetch`+blob. | Add `downloadDocument(id)` in `api.ts`, mirroring how `requestForm` already deviates for uploads. |
| `Find duplicates` is manual / on-demand, not auto-on-upload (phase 1). | Cross-doc cosine scans are O(n²)-ish; inline would slow uploads. | Cheap exact-checksum match may warn on upload (Phase 3); cosine scan stays manual/background. |
| Detail pane depends on the Copilot Workspace tree existing. | Avoids a duplicate browser; reuses their shell. | **Gated on Copilot Phase 1** (§5). Until then, document RAG/CRUD is API-only + testable without UI. |
| Frontend fetching stays local-state `useEffect`. | Matches AiAgentPanel + the Copilot shell. | Reuse the `isMounted`-guarded refresh pattern. |

---

## 3. Backend data model & wiring

### 3.1 New table — `ai_agent_document_chunks`

Migration **`persistence/migrations/versions/0007_document_chunks.py`**
(`down_revision="0006_drop_semantic_overlay"`; Copilot's versioning migration
chains after this — §0.5 interlock 1). New model `AiAgentDocumentChunk` in
[`persistence/models.py`](persistence/models.py):

| Column | Type | Notes |
| --- | --- | --- |
| `id` | String(36) PK | |
| `document_id` | String(36), index | logical FK → `ai_agent_semantic_documents.id`; codebase uses no DB-level FKs — cascade in code |
| `owner_id` | String(255), index | isolation parity with documents |
| `project_id` | String(36), index, nullable | project-scoped dup scans |
| `chunk_index` | Integer | order within document |
| `text` | Text | chunk content |
| `checksum` | String(128), index | sha256(text) → exact-dup detection |
| `char_start` / `char_end` | Integer | offsets into `extracted_text` (UI scroll-to) |
| `embedded` | Boolean | whether a vector exists in the vector store |
| `created_at` | DateTime(tz) | |

> Decision: **vectors live in `LanceVectorCache`, not in this row.** The row is
> the durable system-of-record; the vector store is the rebuildable,
> signature-keyed index — mirrors MDL schema retrieval and keeps degrade-closed.

### 3.2 Pydantic schema — `DocumentChunk`

Add to [`semantic_layer/schemas.py`](semantic_layer/schemas.py) beside
`SemanticDocument`: `id, document_id, chunk_index, text, checksum, char_start,
char_end, embedded`; plus `DocumentChunkMatch` (pair + cosine) for dup results.
(Additive region — §0.5 interlock 2: land after Copilot's `schemas.py` edits, or
in a disjoint block.)

### 3.3 Store protocol extensions

Extend `SemanticLayerStore` ([`semantic_layer/store.py`](semantic_layer/store.py))
+ impl ([`semantic_layer/sqlalchemy_store.py`](semantic_layer/sqlalchemy_store.py)):
- `delete_document(document_id, *, owner_id) -> None` — **the missing CRUD
  primitive**; caller orchestrates blob + chunk + vector removal.
- `save_chunks(document_id, chunks, *, owner_id) -> list[DocumentChunk]`
- `list_chunks(document_id, *, owner_id) -> list[DocumentChunk]`
- `delete_chunks(document_id, *, owner_id) -> None`
- `list_project_chunks(project_id, *, owner_id) -> list[DocumentChunk]`

Add `_chunk_to_model` / `_chunk_from_model` mirroring `_document_to_model` /
`_document_from_model` ([`sqlalchemy_store.py:281`](semantic_layer/sqlalchemy_store.py)).
These are *document-store* methods — disjoint from the `MdlFileStore` CRUD the
Copilot loop reuses (`mdl_files.py`), so no method-level collision.

### 3.4 Document vector store (chunk RAG)

New `semantic_layer/document_retriever.py`, **reusing `LanceVectorCache`**
(`vector_cache.py`) with `collection="document_chunks"`:
- index: per chunk, `cache.upsert(scope_key=…, row_id=chunk.id, text=chunk.text)`.
- retrieve: `cache.search(scope_key=…, query=…, k=…)` → chunk ids → hydrate via
  `store.list_chunks`.
- delete: `cache.remove(scope_key=…, row_id=chunk.id)` per chunk.
- `scope_key`: reuse the canonical scope-key builder used by `schema_retriever` /
  `vector_cache` callers (grep `scope_key` in [`app.py`](app.py)).
- degrade-closed: `is_available()` false → fall back to
  `select_relevant_sections` keyword ranking over `list_chunks`.

### 3.5 Hook chunking + indexing into upload

In [`semantic_layer/documents.py`](semantic_layer/documents.py) `create_document`,
**after** `truncate_to_sections` (~line 87) and before the final
`store.update_document`:

```
chunks  = chunk_sections(extracted_text)                      # existing fn
records = store.save_chunks(document.id, build_chunk_records(chunks), owner_id=…)
document_index.index(records, scope_key=…, owner_id=…)         # best-effort
```

New collaborators (`document_index`) threaded through `create_document` kwargs,
constructed once in `create_app` (§3.6). Wrap indexing in try/except logging an
`index_failed` semantic event (type already exists), so extraction success is
never blocked by embedder failure. **Copilot does not edit `documents.py`** — no
write collision here.

### 3.6 App wiring (`app.py`)

Beside existing singletons (`active_embedder`, `active_retriever`,
`active_semantic_layer_store`, `active_document_storage`,
`active_document_extractor`, ~`app.py:269`), add:
- `active_document_index = create_document_index(app_config, active_embedder)`
  (factory mirroring `create_retriever`).

⚠️ This block is also touched by Copilot's singleton wiring — **disjoint lines,
but same hunk region**; apply after Copilot commits Phase 0 (§0.5 interlock 2).

### 3.7 New / changed endpoints (`app.py`)

All reuse `authorize_semantic_scope(request, scope, identity=…, permission=…)` —
`READ` for GET, `WRITE` for mutations — like `upload_semantic_document`
([`app.py:779`](app.py)); project-scoped routes reuse the project-write check from
`upload_project_source_document`.

| Method | Path | Handler | Perm |
| --- | --- | --- | --- |
| GET | `…/documents/{id}/chunks` | `list_document_chunks` | READ |
| GET | `…/documents/{id}/content` | `download_document` | READ (stream blob; not JSON) |
| DELETE | `…/documents/{id}` | `delete_document_endpoint` | WRITE |
| POST | `…/documents/{id}/reindex` | `reindex_document` | WRITE |
| POST | `…/projects/{pid}/documents/duplicates` | `find_duplicate_chunks` | READ |
| POST | `…/documents/{id}/summarize` | `summarize_document` | WRITE |
| GET | `…/documents/{id}/retrieve?q=` | `retrieve_document_chunks` | READ |

Delete-orchestration (R3): vectors → chunk rows → blob → document row, each
best-effort-logged. Centralise in one `delete_document_cascade` helper in
`documents.py` (single mutation choke point, like `create_document`).

### 3.8 Agent tools (register in the Copilot ToolRegistry)

Register in `semantic_layer/copilot/tools.py` (Copilot-owned; §0.5 interlock 3) —
**do not build a parallel tool dispatcher**:
- `retrieve_document_chunks(query, scope, k)` → top-k chunk RAG
- `find_duplicate_documents(project_id)` → checksum + cosine pairs
- `summarize_document(document_id)` → richer LLM summary
- `delete_document(document_id)` → cascade delete (WRITE-gated)

These ride Copilot's tool-calling contract (`llm/base.py`) and surface in its
Inspector Tools tab automatically (R8). They are thin wrappers over §3.7 logic.

---

## 4. Config additions (`config.py`)

Additive to `AgentConfig` + `from_env`, following `wren_document_*` / `embedder_*`
patterns. ⚠️ Copilot already edits `config.py` (its copilot flags) — add these in
a disjoint block, after its Phase 0 commit (§0.5 interlock 2).

| Key | Env | Default | Purpose |
| --- | --- | --- | --- |
| `wren_document_chunk_max_chars` | `WREN_DOCUMENT_CHUNK_MAX_CHARS` | 2000 | chunk size cap |
| `wren_document_vector_index` | `WREN_DOCUMENT_VECTOR_INDEX` | `memory` | `memory` \| `lancedb` |
| `wren_document_retrieve_k` | `WREN_DOCUMENT_RETRIEVE_K` | 8 | top-k chunks |
| `wren_document_dup_threshold` | `WREN_DOCUMENT_DUP_THRESHOLD` | 0.92 | near-dup cosine cutoff |
| `wren_document_selection` | `WREN_DOCUMENT_SELECTION` | `keyword` | Phase 4 enrichment flag |

---

## 5. Implementation sequencing (checklist)

Phase 0–1 are **implemented, tested, lint/type-clean** (green ring). Copilot is
finished, so Phase 2–4 are **unblocked** — they edit now-stable shared files via
scoped `Edit`s. `[ ]` = todo, `[x]` = done. Insertion points reference §0.6.

### Phase 0 — Backend data layer ✅ DONE
- [x] Migration `0007_document_chunks.py` (sole alembic head — interlock resolved)
- [x] `AiAgentDocumentChunk` model ([`persistence/models.py`](persistence/models.py))
- [x] `DocumentChunk` / `DocumentChunkMatch` + `build_chunk_records` /
      `keyword_rank_chunks` — homed in
      [`document_chunks.py`](semantic_layer/document_chunks.py) (kept out of the
      shared `schemas.py`; re-export in Phase 2 only if an API needs it)
- [x] Store: `delete_document` (cascade), `save_chunks`, `list_chunks`,
      `delete_chunks`, `list_project_chunks` + mappers — on **both**
      `sqlalchemy_store.py` and `memory.py`
- [x] [`document_retriever.py`](semantic_layer/document_retriever.py):
      `DocumentChunkIndex` + `find_exact_duplicate_matches` + `create_document_index`
- [x] Tests (24): `test_document_chunk_records.py`, `test_document_chunk_store.py`
      (parametrized over both stores), `test_document_retriever.py` — R1–R5
      (keyword + fake-cache cosine paths; cascade; dedup)

### Phase 1 — Broaden extraction ✅ DONE (extractor capability)
- [x] HTML (stdlib), PDF (`pypdf`), DOCX (`python-docx`) behind `DocumentExtractor`
      ([`extractors.py`](semantic_layer/extractors.py)); missing optional dep →
      clear `ValueError` (→ `status="error"`), not a crash
- [x] Tests: HTML extraction; missing-dep messaging; round-trips skip when dep absent
- [ ] **Deferred to Phase 2** (needs shared `config.py`): extend
      `wren_allowed_document_types` so HTML/PDF/DOCX pass the upload gate end-to-end.

### Phase 2 — Wire-up + CRUD/RAG endpoints (🟢 ready; shared files now stable)
- [ ] `config.py`: add `wren_document_indexing_enabled` (gate, default off),
      `wren_document_retrieve_k`, `wren_document_dup_threshold`, and the
      HTML/PDF/DOCX additions to `wren_allowed_document_types` (+ `from_env`)
- [ ] `app.py`: `active_document_index = create_document_index(app_config,
      active_embedder)` after `active_retriever` (~305); `_create_document_index`
      helper if needed
- [ ] `documents.py`: chunk+index hook in `create_document` (best-effort,
      `index_failed` event on failure) + `delete_document_cascade`
      (vectors → chunks → blob → row)
- [ ] Endpoints (mirror existing doc-route auth for `{id}` routes,
      `authorize_semantic_project` for project routes): `chunks`, `content`
      (download), `DELETE`, `reindex`, `duplicates`, `summarize`, `retrieve`
      (§3.7) — R3, R5, R6, R7
- [ ] `api.ts`: `deleteDocument`, `downloadDocument`, `listDocumentChunks`,
      `retrieveDocumentChunks`, `findDuplicateChunks`, `summarizeDocument` +
      `DocumentChunk` type (additive)
- [ ] Tests: upload→chunk→index (R1); download bytes; perms; dedup; reindex
      idempotent; HTML upload end-to-end

### Phase 3 — Agent tools via `MdlToolset` (🟢 ready)
- [ ] Extend `MdlToolset.__init__` (`copilot/tools.py`) with
      `document_index/document_store/project_id/owner_id`; add `specs()` +
      `_handler`s + `dispatch()` entries for `retrieve_document_chunks`,
      `find_duplicate_documents`, `summarize_document`, `delete_document` — R8
- [ ] Thread document deps through `run_copilot` (`copilot/service.py`) → toolset
- [ ] Tools surface in the Copilot Inspector Tools tab automatically
- [ ] Tests: dispatch round-trip; tool appears in `specs()`; mirror
      `test_copilot_tools.py`

### Phase 4 — Document node + detail pane UI (🟢 ready; build on Copilot shell)
- [ ] Backend: `WorkspaceNode.document_id` field (`copilot/schemas.py`);
      `build_workspace_tree` enumerates `documents` as `kind="document"` children
      under `raw/` (`copilot/workspace.py`); `get_project_workspace` passes
      `documents=active_semantic_layer_store.list_project_documents(...)`
- [ ] `WorkspaceTree.tsx`: make `kind==='document'` selectable; surface
      `onSelectDocument(documentId)`; `api.ts` `WorkspaceNode.document_id`
- [ ] `index.tsx`: `selectedDocumentId` state + center-pane branch →
      `DocumentDetailPane` (new) with Text / Chunks / Summary sub-tabs (§2.1)
- [ ] Header actions: Download / Re-index / Summarize / Delete (`ConfirmModal`);
      chunk cards w/ dup badge + scroll-to-range; project "Find duplicates" modal
- [ ] Tests (Jest/RTL): document node selectable → detail renders; delete refreshes
      tree; chunk click scrolls — R6

### Phase 5 — Optional: RAG-fed enrichment (behind flag; hot path default)
- [ ] `wren_document_selection = keyword | embedding` (default `keyword`); when
      `embedding`, `propose_mdl_from_document` consults `document_retriever` —
      **same prompt shape + response contract**, only chosen sections differ

### Phase 5 — Optional: RAG-fed enrichment (behind flag, hot path default)
- [ ] Flag `wren_document_selection = keyword | embedding` (default `keyword`)
- [ ] When `embedding`, `propose_mdl_from_document` section selection consults
      `document_retriever` instead of `select_relevant_sections` — **same prompt
      shape, same response contract**; only chosen sections differ
- [ ] This is the *only* touch near the hot path; opt-in, reversible

---

## 6. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| **Collision with Copilot on shared files** (`app.py`, `config.py`, `schemas.py`, `api.ts`). | Land after Copilot Phase 0 commit; disjoint hunks; scoped `Edit` not full `Write` (§0.5). |
| **Migration fork** (two children of 0006). | This plan owns `0007`; Copilot chains after (§0.5 interlock 1) — confirm before writing. |
| **Duplicate document browser** vs Copilot Workspace tree. | Retracted standalone tab; render as document-node detail pane (§2.1). |
| **Three-store drift** (blob/row/vector). | Single `delete_document_cascade` / `create_document` choke points; best-effort + logged; `reindex` repairs. |
| **Embedder-absent deployments.** | Degrade-closed to keyword everywhere (R2). |
| **Embedder model change** invalidating vectors. | Signature-namespaced LanceDB tables; `reindex` rebuilds (R4). |
| **Cross-doc dup scan cost.** | Manual/background; cheap checksum pass first. |
| **Security/scoping.** | Every route reuses `authorize_semantic_scope` (READ/WRITE) + `owner_id`/scope filters (R7); mirror `upload_project_source_document` project-write check. New extraction deps = operator-trust decision, gated + degrade-closed. |

---

## 7. Key files index (touch list)

*Backend (new — 🟢 no collision)*: `persistence/migrations/versions/0007_document_chunks.py`,
`semantic_layer/document_retriever.py`.

*Backend (edit — mine alone 🟢)*: `semantic_layer/documents.py`,
`semantic_layer/store.py`, `semantic_layer/sqlalchemy_store.py`,
`persistence/models.py` (append model), `semantic_layer/extractors.py`.

*Backend (edit — shared with Copilot 🟡, sequence after their Phase 0)*:
`app.py` (routes + singleton), `config.py` (keys), `semantic_layer/schemas.py`
(schemas).

*Backend (register into Copilot-owned 🟡)*: `semantic_layer/copilot/tools.py`.

*Reused as-is*: `vector_cache.py`, `llm/embeddings.py`, `document_chunks.py`,
`file_storage.py`, `access.py`; Copilot's `llm/base.py` tool-calling contract.

*Frontend (new — 🔴 gated on Copilot Workspace tree)*: document detail pane
component (+ chunk/dup sub-components), rendered inside the Copilot shell for
`kind=="document"` nodes.

*Frontend (edit — shared 🟡)*: `AiAgentPanel/api.ts` (additive fns + types).
**Not touched:** `SemanticLayerEditor/index.tsx` directly — the detail pane plugs
into the Copilot-rebuilt shell rather than the current ScrollList layout.
