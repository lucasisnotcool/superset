<!--
Feature spec — surface VIEW provenance in the Explain UI. Follows the view
query-time surfacing work (plan_views_surfacing_impl.md): once an answer can be
grounded by an MDL view, the Explain dialog must show clearly WHERE a view was
used and WHERE it came from. Source-backed; ends with a sequential checklist.
-->

# Feature Spec — Explain UI: View Provenance

Surfacing impl: [`plan_views_surfacing_impl.md`](plan_views_surfacing_impl.md) ·
Memory: [[views-parity-spec]] · Created: 2026-06-30 · Status: **IMPLEMENTED & green
(S1–S5). BE explain + FE AuditInfoPanel/ExplainDialog tests pass; prettier clean.**

## 0. Intent
After the surfacing work a text-to-SQL answer can be grounded by an MDL **view**
(a vetted, named query). The Explain dialog must make that **provenance** legible:
the user should see, at a glance and in detail, **which view(s) grounded the
answer and what they are** — the same object/column-level lineage principle the
industry applies to AI explainability (trace which vetted transformation produced
the output; surface it for debugging and governance). Today the Explain UI shows
models, relationships, retriever mode, and recalled examples — but **not views**,
even though views now flow through the pipeline.

## 1. Current state (source-verified)
- **Explain dialog:** [`ExplainDialog.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/ExplainDialog.tsx)
  → [`AgentStepDetail.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AgentStepDetail.tsx).
  The `wren_context` case (L507) renders rows: Available, **Matched models** (L515),
  Retriever, Retrieved chunks, Context items, Recalled examples, MDL path, plus
  `RetrievedChunks` (L349) grouped by model with a "matched" badge.
- **At-a-glance:** [`AuditInfoPanel.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/AuditInfoPanel.tsx)
  shows badges: Engine, Retrieval (mode + chunk count), Reused learned examples.
- **Data already present after the surfacing work:**
  - `WrenContextArtifact.matched_views` exists (backend `schemas.py:211`); the FE
    type does **not** carry it yet.
  - View chunks (`kind="view"`) already flow into `retrieved_chunks` via
    [`explain.py:_retrieved_chunks`](explain.py#L318) (carries `kind`,`name`,`text`,
    `score`), because `manifest_to_schema_items` now emits them.
  - `RetrievedChunk.kind` already renders as a `<Tag>` ([AgentStepDetail.tsx:336]),
    so a view chunk shows a "view" tag — **but it lands in the headerless
    `GROUPLESS` bucket with relationships** (`groupChunksByModel`, L306), so it is
    not visually identifiable as a *view*, and is not marked "used".

## 2. Gaps — three provenance levels
| # | Level | Gap |
|---|-------|-----|
| **G1** | At-a-glance (AuditInfoPanel) | No "view(s) surfaced" badge — a user scanning the audit strip can't tell a view grounded the answer. |
| **G2** | Detail (wren_context step) | No "Matched views" row paralleling "Matched models"; the explain step detail (`LoadWrenContextDetail`) has no `matched_views`. |
| **G3** | Lineage (retrieved chunks) | View chunks aren't grouped/labelled as views and aren't badged "used" — so "where it came from" (the view's description text) isn't clearly attributable to a *view*. |

## 3. Spec (what to build)
1. **Backend — `LoadWrenContextDetail.matched_views`** ([schemas.py:309](schemas.py#L309))
   + populate from the merged context in [explain.py:_wren_context_detail](explain.py#L291).
2. **FE types** ([api.ts](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts)):
   add `matched_views: string[]` to `WrenContextArtifact` (L113) and
   `LoadWrenContextDetail` (L170).
3. **FE AuditInfoPanel (G1):** a `Surfaced N view(s)` badge when `matched_views`
   is non-empty (mirrors the "Reused N learned example(s)" badge).
4. **FE AgentStepDetail (G2 + G3):**
   - a **"Matched views"** row in the `wren_context` case (parallel to Matched
     models), shown only when non-empty.
   - in `RetrievedChunks`, render `kind="view"` chunks in a dedicated **"Views"**
     group with a header, badging each whose name is in `matched_views` as
     **"used"** (mirrors the model "matched" badge). The chunk's `text` (the view's
     description) is the "where it came from" evidence.

## 4. Decisions (recommendation in bold)
- **D1 — reuse the existing chunk infra vs a new component.** → **Reuse.**
  `RetrievedChunk.kind` already carries `"view"`; add a group + badge, no new
  component. Minimal surface, consistent with models/relationships.
- **D2 — wording: "used" vs "matched/surfaced".** `matched_views` means the view
  was **surfaced into the prompt**, not provably **selected from** in the final SQL
  (that needs SQL parsing — a separate, harder signal). → **Use "matched"/"surfaced"
  wording** (parallel to "Matched models") to avoid overclaiming; the per-chunk
  "used" badge is acceptable inside the retrieval list (it means "this view was put
  in front of the model"), but the at-a-glance badge says **"Surfaced N view(s)"**.
- **D3 — native views.** → already excluded upstream (A1/A2 skip `dialect` views),
  so they never reach the Explain UI. No extra guard needed; assert it in a test.

## 5. Risks & mitigations
| Risk | Mitigation |
|---|---|
| **RX1 — "used" overclaims** (surfaced ≠ selected-from). | "matched"/"surfaced" wording (D2); a true "referenced in final SQL" signal is a noted future enhancement (parse `native_sql` for view names). |
| **RX2 — Clutter / empty state.** | All view rows/badges/groups render only when `matched_views`/view-chunks are non-empty — zero change to view-less projects. |
| **RX3 — A view chunk with no description is opaque.** | The skill (B2) already requires `properties.description`; the chunk still shows the view name + "view" tag. |
| **RX4 — FE/BE type drift** (snake_case `matched_views`). | Keep the field name identical across `schemas.py`, `explain.py`, and `api.ts`; covered by the explain serialization test. |

## 6. User intent ↔ actual UI
| User expectation | Delivered |
|---|---|
| "Did the AI use one of my views?" | At-a-glance **"Surfaced N view(s)"** badge in the audit strip. |
| "Which view?" | **"Matched views"** row in the Explain `wren_context` step. |
| "What is that view / why was it relevant?" | The view appears in **Retrieved chunks** under a **Views** group, with a **"used"** badge and its **description** text (the recall key) — i.e. where it came from. |
| "Was it a native passthrough?" | Native views are never surfaced (excluded upstream) — only governed semantic views appear. |

## 7. Implementation checklist (sequential)
- [ ] **S1 (BE):** `LoadWrenContextDetail.matched_views` + populate in
  `_wren_context_detail`. Test: explain serialization carries `matched_views`.
- [ ] **S2 (FE types):** `matched_views` on `WrenContextArtifact` + `LoadWrenContextDetail`.
- [ ] **S3 (FE AuditInfoPanel):** "Surfaced N view(s)" badge. Test: badge shows when
  `matched_views` non-empty, hidden when empty.
- [ ] **S4 (FE AgentStepDetail):** "Matched views" row + Views chunk group with
  "used" badge. Tests: row shows matched views; a `kind="view"` chunk renders under
  a Views header with a "used" badge when matched; no views → nothing extra.
- [ ] **S5 (gate):** FE + BE tests green; lint; risks/UI-gap notes.

## 8. Out of scope
- A provably-"selected-from" signal (parse the final SQL for the view name) —
  future enhancement; the honest signal today is "surfaced/matched".
- Native-view provenance (native views aren't surfaced).
- Clickable view→definition navigation from the Explain dialog (future).
