# Plan — close the 4 document-upload UX gaps

**Status:** PROPOSAL (not implemented). Source-audited against the working tree.
Closes the four gaps flagged after the Tier-1 binary-upload pass. Companions:
`document_format_tier1_plan.md` (Tier 1 as-built), `document_format_support_study.md`.

**Hard constraints (unchanged):** do not touch the upload→MDL enrichment hot path;
keep the document-format work additive. **Cross-agent safety:** the onboarding /
provenance agent has landed (uncommitted but complete) in `index.tsx`,
`CopilotPanel.tsx`, `api.ts`, `schemas.py`, `app.py`, `store.py`. Those files are
now editable on top of, but the plan is **phased by ownership** so the
green (my-only-files) work lands first and the shared-file work is explicit.

Gap → requirement map:
- **G1** — upload buried in a deprecation-framed dialog → discoverable, non-deprecated home.
- **G2** — no client-side size guard → pre-upload size check, drift-free from backend.
- **G3** — drag-drop accepts anything (picker filters by `accept`) → drop/pick parity.
- **G4** — "Save all" on a documents-only batch is a mislabeled no-op → correct affordance.

---

## G1 — A discoverable, non-deprecated home for document upload

### Current state (source-backed)
- Source documents upload through `SemanticLayerImportDialog`, opened by the
  **"Add…"** button in the MDL browser pane (`SemanticLayerEditor/index.tsx:787`),
  mounted at `index.tsx:959`.
- The dialog title is `t('Add to semantic layer')` and it **leads with a blanket
  deprecation alert** — "Markdown enrichment here is deprecated. The MDL Copilot
  reads your uploaded documents… Upload JSON here for raw MDL files."
  (`SemanticLayerImportDialog.tsx:~432-442`). The dropzone copy is MDL/Markdown-first.
- Net effect: a user wanting to upload a PDF/XLSX **for the Copilot to read** has no
  obvious entry — the only door is labelled "Add…" and framed around deprecated MDL
  enrichment.

### New requirement
**R-G1:** Uploading a source document (CSV/HTML/PDF/Word/Excel/PowerPoint) for the
Copilot + viewer must be a clearly-labelled, first-class action — visually distinct
from, and not gated behind, the deprecated markdown→MDL enrichment framing.

### Spec
Two parts; Part A is GREEN (my dialog file only), Part B is the AMBER entry point.

**S-G1a (GREEN — reframe the dialog, `SemanticLayerImportDialog.tsx`):**
1. Retitle the modal to cover both intents, e.g. `t('Upload documents & MDL')`
   (`SemanticLayerImportDialog.tsx:~474`).
2. Make the dropzone copy **document-first**: primary line
   "Drop documents (PDF, Word, Excel, PowerPoint, CSV, HTML), MDL JSON, or
   Markdown"; secondary line explains each lane.
3. **Demote the deprecation alert to contextual:** render it only when a
   `kind: 'enrichment'` (markdown) item is staged — not as an always-on banner. A
   user uploading a PDF never sees the enrichment-deprecation noise.
   - Implementation: `const hasEnrichment = items.some(i => i.kind === 'enrichment')`;
     wrap the existing `enrichment-deprecation-notice` Alert in `{hasEnrichment && …}`.

**S-G1b (AMBER — dedicated entry point, `SemanticLayerEditor/index.tsx`,
optionally `CopilotPanel.tsx`):**
4. Add an explicit **"Upload document"** affordance where users expect to manage
   source docs. Recommended: a second button beside "Add…" in the browser pane
   (`index.tsx:~783-791`) that opens the *same* dialog (no duplicate upload logic).
   Optionally, a matching CTA in the Copilot bootstrap rail
   (`CopilotPanel.tsx`, `onOnboard`-adjacent) since the Copilot "reads your
   uploaded documents".
   - Minimal contract: reuse `setShowImportDialog(true)`; no new dialog. Label +
     icon (`Icons.FileAddOutlined`) make the document path discoverable.

> **Product decision needed (record in the doc):** do we want a *separate*
> document-upload dialog/section, or is the reframed shared dialog (S-G1a) plus a
> second button (S-G1b) sufficient? **Recommendation: reframed shared dialog +
> second button** — closes discoverability with the least surface and zero logic
> duplication. A fully separate "Documents" manager is a larger follow-up only if
> product wants document CRUD (list/delete/re-index) outside the workspace tree
> (which already shows `raw/` docs + status badges).

### Touchpoints
- GREEN: `SemanticLayerEditor/SemanticLayerImportDialog.tsx` (mine).
- AMBER: `SemanticLayerEditor/index.tsx` (other agent's, landed) — 1 button + reuse
  existing state; `CopilotPanel.tsx` (optional).

### Tests
- `SemanticLayerImportDialog.test.tsx`: deprecation notice is **absent** until a
  `.md` file is staged; present after. Title/dropzone copy assertions.
- `index.test.tsx` (AMBER): the new "Upload document" button opens the dialog.

---

## G2 — Client-side size guard (drift-free from the backend cap)

### Current state
- Backend rejects > `wren_max_document_bytes` (default **10 MB**, `config.py:127`)
  with HTTP 400; the dialog surfaces the 400 as a generic error **after** a full
  upload round-trip.
- The cap is **not exposed to the FE** (not in `HealthResponse`,
  `schemas.py:450-465`). The FE already fetches `getAgentHealth()`
  (`api.ts:679`, called at `AiAgentPanel/index.tsx:677`).

### New requirement
**R-G2:** Oversized files must be rejected **before** upload with a clear,
size-aware message, and the threshold must track the backend config (no hard-coded
drift when an operator changes `WREN_MAX_DOCUMENT_BYTES`).

### Spec
**S-G2a (backend — expose the cap):** add `max_document_bytes: int` to
`HealthResponse` (`schemas.py`) and populate it in `health()`
(`app.py:~549`) from `app_config.wren_max_document_bytes`. (Disjoint region from the
provenance agent's health/route work.)

**S-G2b (FE api type):** add `max_document_bytes?: number` to `AgentHealthResponse`
(`api.ts:433`). (I already edit `api.ts`; additive field.)

**S-G2c (FE guard — `SemanticLayerImportDialog.tsx`):**
1. On dialog open, read the cap via `getAgentHealth()` (lazy, self-contained — no
   prop-drilling through the other agent's `index.tsx`). Fall back to a documented
   `DEFAULT_MAX_DOCUMENT_BYTES = 10_000_000` constant if the field is absent.
2. In `stageFiles`, before classifying/uploading, check `file.size > maxBytes`. If
   over, stage the item as `status: 'error'` with
   `t('File is too large (%(size)s). The limit is %(max)s.', { size, max })` using a
   shared `formatBytes` helper — and **do not** call the upload endpoint.
3. Extract `formatBytes` (currently private in `DocumentDetailPane.tsx:74-78`) into
   a shared helper (e.g. `documentStatus.tsx` or a small `documentFormat.ts`) and
   reuse in both. (Both files are mine.)

### Touchpoints
- Backend: `schemas.py` (HealthResponse), `app.py` (health()).
- FE (mine): `api.ts` (type), `SemanticLayerImportDialog.tsx`, shared `formatBytes`.

### Tests
- `test_semantic_layer_api` (py): `/health` returns `max_document_bytes` == config.
- `api.test.ts`: `getAgentHealth` parses `max_document_bytes`.
- `SemanticLayerImportDialog.test.tsx`: a file over the cap is rejected pre-upload
  (no POST to `/documents`), shows the size message; a file under proceeds.

---

## G3 — Drag-drop / file-picker parity

### Current state
- `onDrop` calls `stageFiles(event.dataTransfer.files)` with **no filter**
  (`SemanticLayerImportDialog.tsx:374-377`); unsupported dropped files become
  per-file `error` items.
- The picker filters by the `accept` attribute, so unsupported files can't even be
  picked — an inconsistency between the two input paths.

### New requirement
**R-G3:** Dropping files must behave like the picker — unsupported types are
rejected **consistently and clearly**, without manufacturing per-file error rows.

### Spec
**S-G3 (`SemanticLayerImportDialog.tsx`):**
1. Add a single predicate `isAcceptedFile(name) = isJson || isMarkdown ||
   isSourceDocument` (compose the existing three).
2. In `onDrop`, partition `dataTransfer.files` into accepted vs rejected. Pass only
   accepted files to `stageFiles`. If any were rejected, set a single dialog-level
   `error`: `t('Skipped %(n)s unsupported file(s). Accepted: documents, MDL JSON, ' +
   'Markdown.', { n })` — not N error rows.
3. Reuse `isAcceptedFile` in the `stageFiles` placeholder classification so the
   "supported" decision lives in one place (DRY with the picker's `accept`).

### Touchpoints
- FE (mine): `SemanticLayerImportDialog.tsx` only. **Fully GREEN.**

### Tests
- `SemanticLayerImportDialog.test.tsx`: simulate a drop with a mix (one `.xlsx`, one
  `.png`) → the `.xlsx` stages, the `.png` does **not** create an item, and the
  "Skipped 1 unsupported file" message shows. (Drop via `fireEvent.drop` with a
  `dataTransfer.files` list, since `userEvent.upload` respects `accept`.)

---

## G4 — Documents-only batch → correct footer affordance

### Current state
- `persistAll` only acts on `status: 'pending' | 'draft'` items
  (`SemanticLayerImportDialog.tsx:~431-456`); document items are terminal
  (`'uploaded'`), so a documents-only batch makes "Save all" a no-op that then
  `close()`s. The footer always reads `t('Save all')`
  (`SemanticLayerImportDialog.tsx:~475-485`).

### New requirement
**R-G4:** The primary footer action must match what's actually stageable — never
imply "saving" when there is nothing to apply (documents are already persisted).

### Spec
**S-G4 (`SemanticLayerImportDialog.tsx`):**
1. Derive `const hasApplyable = items.some(i => i.status === 'pending' || i.status
   === 'draft')`.
2. Footer button: label `hasApplyable ? t('Save all') : t('Done')`; `onClick`
   `hasApplyable ? persistAll : close`; keep `disabled` when `items.length === 0`.
3. (Polish) when `!hasApplyable && items.length > 0`, the document rows already
   show their own status; no extra apply step is implied.

### Touchpoints
- FE (mine): `SemanticLayerImportDialog.tsx` only. **Fully GREEN.**

### Tests
- `SemanticLayerImportDialog.test.tsx`: a documents-only batch shows a **"Done"**
  button that closes (calls `onHide`) and issues **no** MDL `mdl-files` POST; a batch
  with a staged JSON shows **"Save all"**.

---

## Phased checklist (ordered by safety)

**Phase 1 — GREEN (my files only; no cross-agent risk):**
- [ ] **G3** drop/pick parity (`isAcceptedFile`, `onDrop` filter, single skip msg). +test.
- [ ] **G4** documents-only footer (`hasApplyable`, Save all/Done). +test.
- [ ] **G1a** dialog reframe (title, document-first copy, contextual deprecation
      alert). +test.
- [ ] **G2c-partial** size guard in the dialog using `DEFAULT_MAX_DOCUMENT_BYTES`
      constant + shared `formatBytes` (works before the backend field lands). +test.

**Phase 2 — backend cap exposure (disjoint regions; other agent landed):**
- [ ] **G2a** `HealthResponse.max_document_bytes` (`schemas.py`) + `health()`
      populate (`app.py`). +py test.
- [ ] **G2b** `AgentHealthResponse.max_document_bytes` (`api.ts`) + dialog reads it
      via `getAgentHealth()` (replaces the constant fallback). +api.test.ts.

**Phase 3 — AMBER entry point (shared FE files; coordinate):**
- [ ] **G1b** "Upload document" button in `index.tsx` (reuse `setShowImportDialog`);
      optional Copilot CTA. +index.test.tsx.

**Close-out:** `pre-commit run` (py), `tsc --noEmit` + `jest` (FE); update
`document_format_tier1_plan.md` Part F with the UX-gap closures.

---

## Risks & notes
- **R1 — Cross-agent file churn (Phase 3 / G2a).** `index.tsx`, `app.py`,
  `schemas.py` carry the other agent's uncommitted work. Edit only after confirming
  those files are clean (`git status`), re-Read immediately before each Edit, keep
  changes small and disjoint (button add; new schema field; one health line).
- **R2 — Health round-trip latency (G2c).** Fetching health on dialog open adds a
  cheap GET; cache it / fall back to the constant so the guard is never blocked on
  the network.
- **R3 — `formatBytes` extraction.** Moving it out of `DocumentDetailPane` is a
  refactor of a working component — keep it a pure move + import, covered by the
  existing detail-pane tests.
- **R4 — Drop event in jsdom (G3 test).** `userEvent.upload` honors `accept`, so the
  rejection path must be tested via `fireEvent.drop` with a synthetic `dataTransfer`.
- **R5 — Product scope (G1).** If product wants a full document **manager**
  (list/delete/re-index outside the tree), that's a larger, separate feature; this
  plan deliberately reuses the tree (which already lists `raw/` docs with status
  badges) and the existing dialog.
