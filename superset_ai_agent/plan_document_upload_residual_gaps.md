# Plan — residual document-upload gaps (post Phase 1–3)

**Status:** PROPOSAL (not implemented). Source-audited against the working tree.
Follow-up to `plan_document_upload_ux_gaps.md` (gaps G1–G4, landed). Closes the four
residual items noted after Phase 2/3.

**Constraints:** additive only; degrade-closed; do not regress the landed upload UX.
**Cross-agent note:** several touchpoints (`app.py`, `CopilotPanel.tsx`, `index.tsx`,
`api.ts`) carry the onboarding/provenance agent's now-landed (uncommitted) work. They
are editable, but each step below lists a **blocker** to re-confirm the file is clean
(`git status`) and to re-Read immediately before editing.

Residual-gap map:
- **RG1** — pre-existing `I001` in `app.py` import block (the other agent's imports).
- **RG2** — no "Upload document" CTA in the Copilot panel (only the browser-pane button).
- **RG3** — the dialog refetches `/health` on every open (no dedupe/cache).
- **RG4** — `getAgentHealth()` failure in the dialog is silent (by design; make it deliberate).

---

## RG1 — Fix the `I001` import-block lint in `app.py`

### Current state (source-backed)
- `ruff check superset_ai_agent/app.py --select I001` →
  `app.py:18:1: I001 [*] Import block is un-sorted or un-formatted` (auto-fixable).
- The unsorted entries are the **other agent's** additions (confirmed via
  `git diff HEAD superset_ai_agent/app.py`): `OnboardingRequest`,
  `provenance_from_event`, `ProvenanceEntry` inside a multi-line
  `from … import ( … )` block. **HEAD is I001-clean** and my diff touches no imports.

### Requirement
**R-RG1:** `app.py` passes `ruff check` (the pre-commit `ruff` hook runs
`ruff check --fix --show-fixes`, so CI will block on this).

### Entrypoints / touchpoints
- `superset_ai_agent/app.py` (import block, lines ~18–60).

### Spec
- Run `python -m ruff check --fix superset_ai_agent/app.py` (sorts the block
  deterministically; no semantic change). Equivalent to letting `pre-commit run`
  auto-fix at commit time.
- Verify: `python -m ruff check superset_ai_agent/app.py` → "All checks passed!".

### Tests
- None (mechanical). Re-run the agent suite once as a smoke check
  (`pytest tests/unit_tests/superset_ai_agent/ -q`).

### Blockers / dependencies
- **Blocker:** reorders import lines the other agent added. It is purely mechanical
  (isort), but to avoid a confusing cross-author diff, do it **at commit time** (let
  `pre-commit run` apply it) **or** immediately before pushing, after their work is
  committed. No functional dependency. Lowest priority; zero risk.

---

## RG2 — "Upload document" CTA in the Copilot panel

### Current state (source-backed)
- Document upload is reachable only from the browser-pane button (`index.tsx:~792`,
  "Upload document" → `setShowImportDialog(true)`).
- `CopilotPanel` has **no upload affordance**. Its props
  (`CopilotPanelProps`, `CopilotPanel.tsx:75`) expose `onOnboard: () => void`
  (`:89`), used by the bootstrap CTAs (`:683`, `:705`). The panel renders two
  branches: **not-onboarded/bootstrap** (`CopilotPanel.tsx:~690-710`, the
  "Onboard this schema" button) and **onboarded/ready** (`:713+`, the chat view).
- `index.tsx` renders `<CopilotPanel … onOnboard={() => setShowOnboardPicker(true)}>`
  (`index.tsx:~932-938`); `setShowImportDialog` is already in scope there (`:270`).

### Requirement
**R-RG2:** The Copilot panel offers a discoverable "Upload document" action that
opens the existing shared upload dialog, surfaced where "the Copilot reads your
uploaded documents" is most relevant (the **ready/active** view; optionally the
bootstrap view too). No duplicate upload logic — reuse `setShowImportDialog(true)`.

### Entrypoints / touchpoints
- `CopilotPanel.tsx`: add `onUpload?: () => void` to `CopilotPanelProps` (`:75-89`);
  destructure it (`:108`); render a small secondary button/link
  (`Icons.UploadOutlined`, `buttonSize="small"`, `data-test="copilot-upload"`) in
  the **ready** branch (confirm exact anchor at `:713+`) and optionally beneath the
  bootstrap "Onboard" button (`:709`). Gate on `canWrite`.
- `index.tsx`: pass `onUpload={() => setShowImportDialog(true)}` at the
  `<CopilotPanel>` render (`:~932-938`).

### Spec
- `onUpload` is optional (`?`) so the panel renders without it (tests/other mounts).
- Button copy: `t('Upload document')`; tooltip mirrors the browser-pane one.
- Placement recommendation: the **ready view** (most salient for "Copilot reads your
  docs"); a bootstrap-view copy is optional. Keep it secondary (not primary) so it
  doesn't compete with onboarding/chat.

### Tests
- `CopilotPanel.test.tsx`: when `onUpload` is provided and state is ready, clicking
  the "Upload document" control calls `onUpload`; absent prop → no crash.
- `index.test.tsx`: the Copilot "Upload document" CTA opens the dialog
  (`semantic-import-dropzone` appears) — mirrors the existing browser-pane test.

### Blockers / dependencies
- **Blocker:** edits `CopilotPanel.tsx` + `index.tsx` (other agent's landed files).
  Confirm clean + re-Read before editing. Independent of RG1/RG3/RG4.

---

## RG3 — Stop refetching `/health` on every dialog open

### Current state (source-backed)
- The dialog fetches the cap on each open:
  `SemanticLayerImportDialog.tsx` `useEffect([show])` → `getAgentHealth()` →
  `setMaxDocumentBytes(...)`, `.catch(() => undefined)`.
- **The component that already holds health state is a sibling, not an ancestor:**
  `AiAgentPanel/index.tsx` fetches health (`:677`) into `health` state (`:630`), but
  it does **not** render `SemanticLayerEditor` — that is mounted by
  `TabbedSqlEditors/index.tsx:273` with only `databaseId/catalogName/schemaName`
  props (`SemanticLayerEditorProps`, `index.tsx:232-235`). So a simple "lift to the
  parent and pass a prop" is **not available** without hoisting state to a shared
  ancestor (TabbedSqlEditors or a store) — a larger refactor.

### Requirement
**R-RG3:** Opening the upload dialog must not trigger a fresh `/health` round-trip
when a recent result is available, **without** cross-tree prop-drilling or a refactor
of the SqlLab component hierarchy.

### Entrypoints / touchpoints
- `AiAgentPanel/api.ts`: `getAgentHealth` (`:~679`).
- (No change needed in the dialog if the cache is transparent.)

### Spec (recommended: transparent cached fetch)
- Add a tiny module-level memo around health in `api.ts`, e.g.
  `getAgentHealthCached(maxAgeMs = 60_000)` that returns the in-flight or last
  promise if younger than `maxAgeMs`, else refetches. Keep `getAgentHealth`
  (uncached) for callers that want freshness.
- Point the dialog's `useEffect` at `getAgentHealthCached()`. Repeated opens reuse
  the cached result; the first open (or a stale cache) fetches once.
- Pure-additive, self-contained in `api.ts` + a one-line swap in the dialog. No
  sibling/ancestor coupling.

### Spec (alternative, rejected unless product wants shared health)
- Hoist health to a shared ancestor / Redux slice and prop-drill `maxDocumentBytes`
  to `SemanticLayerEditor` → dialog. Rejected for v1: touches `TabbedSqlEditors` and
  the editor prop contract for a marginal gain over the cache.

### Tests
- `api.test.ts`: `getAgentHealthCached` issues one network call across two calls
  within `maxAgeMs`; refetches after expiry (mock timers).
- `SemanticLayerImportDialog.test.tsx`: opening twice does not double-count
  `/health` calls (assert `callHistory.calls('…/health')` length). NOTE: the test
  harness clears routes per test; assert within a single test that re-mounts.

### Blockers / dependencies
- **Blocker:** edits `api.ts` (shared, landed) + the dialog (mine). **Pairs with
  RG4** (same health path) — implement together. Independent of RG1/RG2.

---

## RG4 — Make the silent health-failure fallback deliberate

### Current state
- On health-fetch failure the dialog swallows the error (`.catch(() => undefined)`)
  and keeps `DEFAULT_MAX_DOCUMENT_BYTES`. This is **correct** for a best-effort
  guard (the backend still enforces the real cap), but it is undocumented at the
  call site beyond a brief comment and emits no signal for debugging.

### Requirement
**R-RG4:** The fallback stays **non-blocking and degrade-closed** (no user-facing
error, no upload block), but the decision is explicit and minimally observable for
operators/devs.

### Entrypoints / touchpoints
- `SemanticLayerImportDialog.tsx` (the `getAgentHealth` `.catch`), folded into RG3's
  cached fetch if adopted.

### Spec
- Keep the silent fallback for the **user** (no UI error). Add a single dev-facing
  `logging.debug`-equivalent: `console.debug('[ai-agent] health unavailable; using
  default upload cap')` in the catch (guarded so it doesn't spam — once per failure).
- Reaffirm the rationale in a code comment (degrade-closed; backend is source of
  truth). **Do NOT** add a user-visible warning — that would contradict the
  best-effort design and add noise for an operator who simply hasn't exposed health.
- If RG3's cache is adopted, the cache helper owns the catch and the debug log, so
  every caller inherits the behavior.

### Tests
- `SemanticLayerImportDialog.test.tsx` (already covers the path indirectly): when
  `/health` errors/returns no `max_document_bytes`, the guard still uses the default
  (a 10 MB+1 file is rejected; a 1 MB file proceeds). Optionally spy on
  `console.debug`.

### Blockers / dependencies
- **Blocker:** edits the dialog (mine) and/or `api.ts` (shared) if folded into RG3.
  **Do together with RG3.** Lowest code volume of the four.

---

## Sequential checklist (for future sessions)

Ordered to minimize cross-agent file churn and group shared concerns. Each item is
independently shippable except where noted.

- [ ] **RG2 — Copilot upload CTA.** (a) `CopilotPanel.tsx`: add optional `onUpload`
      prop + secondary "Upload document" button in the ready view (confirm anchor at
      `:713+`); (b) `index.tsx`: pass `onUpload={() => setShowImportDialog(true)}`.
      Tests: CopilotPanel + index. **Blocker:** other agent's files — confirm clean,
      re-Read first. _Dep:_ none.
- [ ] **RG3 — Cached health fetch.** Add `getAgentHealthCached(maxAgeMs=60_000)` to
      `api.ts`; point the dialog's `useEffect` at it. Tests: `api.test.ts` (one call
      within window; refetch after expiry) + dialog (no double `/health`).
      **Blocker:** shared `api.ts`. _Dep:_ **bundle with RG4** (same code path).
- [ ] **RG4 — Deliberate silent fallback.** Fold the `.catch` + a once-per-failure
      `console.debug` into RG3's cache helper (or the dialog catch if RG3 deferred);
      reaffirm the degrade-closed comment; keep zero user-facing error. Tests: guard
      still defaults on health failure. **Blocker:** dialog/`api.ts`. _Dep:_ **after
      RG3** (or merged into it).
- [ ] **RG1 — `app.py` I001.** Run `ruff check --fix superset_ai_agent/app.py` (or
      let `pre-commit run` do it). Verify clean. **Do LAST, at commit time**, so the
      isort touches the other agent's imports only once, on the final tree. _Dep:_
      other agent's work committed (so the import block is final). _Smoke:_ re-run
      the agent pytest suite.
- [ ] **Close-out:** `tsc --noEmit` + `jest src/SqlLab/components/AiAgentPanel` +
      `pytest tests/unit_tests/superset_ai_agent/` + `pre-commit run --files <changed>`;
      update `plan_document_upload_ux_gaps.md` / this doc with what landed.

### Dependency graph
```
RG2  ── independent ─────────────► ship anytime
RG3 ─┬─ same health path ─► RG4   ► ship together
RG1  ── needs other agent committed ► ship LAST (commit-time isort)
```

---

## Risks & notes
- **R1 — RG1 cross-author diff.** Auto-isort will reorder the other agent's imports;
  harmless but can look like "my" change. Do it at commit time / after their commit
  so the diff is attributed cleanly.
- **R2 — RG3 cache staleness.** A 60 s memo means an operator who changes
  `WREN_MAX_DOCUMENT_BYTES` sees the new cap after ≤60 s / next reload. Acceptable
  for a UX hint; the backend enforces immediately. Tune `maxAgeMs` if needed.
- **R3 — RG2 placement.** Putting an upload CTA in the chat-ready view risks clutter;
  keep it small/secondary. If product prefers, place it only in the panel header.
  Confirm the exact ready-view anchor before coding (`CopilotPanel.tsx:713+`).
- **R4 — RG4 over-signalling.** Resist adding a user-facing "health unavailable"
  banner — it contradicts best-effort and confuses operators who never exposed
  `/health`. Dev `console.debug` only.
- **R5 — Sibling-tree health (RG3).** The "lift to parent" idea from the original
  note is not viable as stated (AiAgentPanel ≠ ancestor of SemanticLayerEditor); the
  cache is the pragmatic substitute. A real shared health store is a separate, larger
  refactor out of scope here.
```
