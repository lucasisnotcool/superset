# Feature Spec — Semantic Mode Badge for the AI SQL Agent

Status: Draft for review
Owner: AI SQL Agent
Related: [[ai-sql-project-selection]], [[mdl-lab-request-storm]], the mode-incoherence
analysis (native vs semantic SQL), `plan_ai_sql_project_selection_spec.md`

---

## 1. Problem & intent

### 1.1 Origin (dev intent)
A live incident showed the agent emitting a Frankenstein query — semantic constructs
(`is_hold`, `stage_step_owner`) hand-written into **native** Oracle SQL — then failing
`ORA-00904` repeatedly. Root cause (see the mode-incoherence analysis): the agent's
**semantic-vs-native mode is invisible** to both the model and the user. The user had to
*ask* the agent "did you write semantic or native SQL?", and the agent could only answer
by inspecting its own output after the fact — and even confabulated a non-existent "mode
was turned on mid-conversation" story.

The mode is governed by **8 backend factors** (below), several of which are silent
deployment/database facts. For the incident's Oracle database, semantic mode is in fact
**structurally impossible** (Oracle is not a supported wren-core dialect), yet nothing in
the UI said so.

### 1.2 Feature intent
Make the otherwise-invisible mode, and the specific factors blocking it, **transparent and
diagnosable at a glance** — turning "why isn't semantic on?" from a support question into a
hover. This is the UI complement to the prompt/error-enrichment fixes in the analysis.

### 1.3 Intent alignment (dev ↔ feature ↔ user ↔ UI)
| Layer | Statement |
|---|---|
| **Dev intent** | Surface the real `semantic_sql_mode` + its blocking factors from server truth, so mode is never invisible or confabulated. |
| **Feature spec** | A status badge (left of the project selector) + a hover diagnostic listing every factor with per-factor state, amber-flagging blockers. |
| **User intent** | "Is the agent using my semantic layer right now? If not, why, and can I fix it?" |
| **UI** | At-a-glance badge (status) → hover tooltip (diagnosis) → amber icon on each blocker (what to fix). |

---

## 2. Scope

**In scope:** a read-only status badge + factor tooltip in the AI SQL agent panel; one new
(or extended) backend endpoint exposing mode + factors; reuse of existing badge/tooltip/icon
patterns.

**Out of scope:** changing how mode is *computed* or *enforced* (that's the separate
prompt/error-enrichment spec); auto-fixing blockers; MDL editing. The badge **reports**; it
does not change behavior.

---

## 3. The factors (server truth)

The mode is computed identically in `graph.py:1070` and `conversation_graph.py:1743`:

```python
semantic_sql_mode = (
    self.config.wren_semantic_sql_enabled
    and self.semantic_engine.name != "passthrough"
)
```

…but that boolean collapses 8 underlying conditions. The badge must expose all 8 so a
"false" is explainable:

| # | Factor (user-facing label) | Checks | Where | Blocks when | Class |
|---|---|---|---|---|---|
| 1 | **Semantic SQL enabled** | `wren_semantic_sql_enabled` | config.py:260 (default `False`) | flag off | Deployment |
| 2 | **Engine = wren-core** | `wren_engine != passthrough` | factory.py:28 (default `wren_core`) | engine = passthrough | Deployment |
| 3 | **Engine installed** | wren-core package importable | wren_core_engine.py:66 | not importable | Deployment |
| 4 | **Database dialect supported** | backend ∈ supported map | engine/base.py:47 | Oracle, SQLite, unknown | **Database** |
| 5 | **Wren integration enabled** | `wren_enabled` | config.py:115 (default `True`) | flag off | Deployment |
| 6 | **Project/schema selected** | `request.schema_name` or pinned project | graph.py:447 (`wren_require_schema_scope`) | nothing selected | **Scope (user-fixable)** |
| 7 | **Active semantic models** | project has ≥1 active MDL file | graph.py:503 | `file_count == 0` | **Scope (user-fixable)** |
| 8 | **Semantic context loaded** | context fetch succeeds | http_client.py:71 | exception / unavailable | Runtime (query-time) |

**Key consequences for design:**
- Factors **1–5** are static per deployment/database — knowable without a query, identical
  for every user of that DB. (Factor 4 is the Oracle killer.)
- Factors **6–7** are the only ones a user can act on *in this UI* (pick a schema, activate a
  project). These deserve the most actionable copy.
- Factor **8** is only truly known after a query runs → represent as "evaluated at query
  time" and hydrate from the last query response when available.

---

## 4. Can the frontend get this today? → No.

None of the existing endpoints (`/health`, `/projects/{id}/readiness`,
`/projects/{id}/state`) expose factors 1–5 or the composed `semantic_sql_mode`. The FE can
only *infer* a blocked state from `wren_context.available == false` on a past query — which
is exactly the post-hoc, can't-explain-why situation we're fixing.

**Decision D1 — data source.** Reuse the exact `graph.py` mode computation server-side; do
**not** reimplement factor logic in TypeScript.
**Recommendation:** new lightweight endpoint
`GET /agent/semantic-sql-mode-status?database_id&catalog&schema&project_id`. Rationale:
- The badge must render **proactively** on project/schema selection, before any query.
- A dedicated endpoint computes factors 1–7 deterministically from current scope; factor 8
  is annotated "query-time" and hydrated opportunistically from the latest
  `AgentQueryResponse.wren_context`.

Response shape:
```jsonc
{
  "mode": "semantic" | "native",          // = all factors met
  "factors": [
    { "key": "semantic_sql_enabled", "label": "Semantic SQL enabled",
      "state": "met" | "blocked" | "not_applicable" | "runtime",
      "blocking": true,                      // blocked AND required for semantic
      "detail": "Enabled by the operator.",  // why + (if blocked) how to fix
      "fixable_by": "operator" | "user" | "database" }
    // …one per factor 1–8
  ],
  "blocking_factors": ["database_dialect_supported"]  // convenience
}
```

---

## 5. UX design (grounded in research)

### 5.1 Placement & shape
Per request: **left of the project selector**, inside the existing
`<Flex align="center" gap={4}>` chip row (`AiAgentPanel/index.tsx:1330`).

Research consensus: use a **labeled pill** (not a bare dot) when the status word must be
readable — Atlassian Lozenge, Polaris Badge, Carbon Tag. The chip row has room for a short
label, and "Semantic"/"Native" is the whole point. Reuse the existing **`Tag`** pattern
already used by `CoverageBadge.tsx` (color variants `success`/`warning`/`default`).

### 5.2 Badge states & color (D2)
**Native is NOT an error — it's a valid fallback.** Every design system maps OFF/fallback to
**neutral grey (`default`)**, ON/healthy to **green (`success`)**, and reserves **red** for
failures. So:

| Mode | Label | Tag color | Icon |
|---|---|---|---|
| Semantic active (all 8 met) | `Semantic` | `success` (green) | `CheckCircleOutlined` |
| Native (≥1 blocker) | `Native` | `default` (grey) | `WarningOutlined` (amber) **only if ≥1 user-fixable blocker** |

**Recommendation:** grey badge for native, with a small amber `WarningOutlined` adornment on
the badge **only when a user-fixable blocker exists** (factors 6–7) — i.e. "you can turn this
on." When the only blockers are deployment/database (1–5, esp. Oracle), keep the badge plain
grey (nothing the user can do here) — surfacing amber there would be NN/g's "overstated
severity" anti-pattern. Color is never the only signal: label + icon + color (WCAG 1.4.1).

### 5.3 The hover diagnostic (the core of the feature)
Pattern source: GitHub merge-box / password-strength checklist / CI deployment gates — a
**list of preconditions, each with a discrete pass/blocked state and its own icon**, and the
load-bearing UX rule from NN/g + deployment-gate best practice: **never block without
explaining why and what to do next.**

Content (reuse `CustomizationsBadge.tsx`'s rich-`Tooltip` pattern, `index.tsx:275`):
```
Semantic mode: NATIVE
The agent is writing native SQL. Semantic-layer relationships and
calculated columns are not used.

Factors:
  ✓  Semantic SQL enabled
  ✓  Engine: wren-core
  ✓  Engine installed
  ⚠  Database dialect supported — Oracle is not supported by the
       semantic engine. (deployment)
  ✓  Wren integration enabled
  ⚠  Project / schema selected — pick a schema or a project to ground on.
  –  Active semantic models — n/a until a project is selected
  ·  Semantic context loaded — evaluated when a query runs
```
- ✓ green check = met; ⚠ amber `WarningOutlined` = **blocking**; – grey dash =
  not-applicable; · = runtime/deferred.
- Each blocker row carries one-line remediation keyed to `fixable_by`
  (user → imperative "pick a schema"; operator/database → factual "not supported", no
  false promise of a user fix).
- Amber sits **beside the specific blocking factor**, exactly as requested — not on
  non-blocking rows (avoids severity inflation).

### 5.4 Tooltip vs Popover (D3)
The user asked for **hover**. But the converging W3C/design-system guidance pushes back on a
plain hover *tooltip* for this content, and it's worth heeding:
- WAI-ARIA APG: "tooltip widgets do not receive focus" → they **cannot reliably hold a list
  with icons or future remediation links**; rich/interactive content should be a non-modal
  dialog/popover (or click-triggered toggletip).
- NN/g + Carbon + Polaris: **essential/actionable info must not be hover-only** — SR/keyboard
  users may never reach it. The detailed factor breakdown is exactly that.
- WCAG **1.4.13** (Content on Hover or Focus, AA): any hover/focus surface must be
  **dismissible** (Esc), **hoverable** (mouse can move onto it), and **persistent** (no timer
  auto-close).

**Recommendation:** keep **hover** as the trigger (as requested) but render the diagnostic as
a **`Popover`** (`@superset-ui/core/components`), opened on **hover AND focus**, satisfying
1.4.13. Crucially, keep the *essential* status — the word "Semantic"/"Native" — in the
**always-visible badge label**, so the hover surface carries only the *supplementary* factor
detail, not the essential state. This honors "on hover" while staying accessible and leaving
room for future "Activate project" / "Learn more" links (which a tooltip could not hold). A
plain rich `Tooltip` (house style: `CustomizationsBadge`, `TaskStatusIcon`) remains an
acceptable v1 fallback *only* because the badge label already carries the essential state —
but Popover is the durable choice.

### 5.5 Accessibility (non-negotiable)
- Trigger is keyboard-focusable (`role="button"`, `tabIndex={0}`); tooltip opens on **hover
  AND focus** (WAI-ARIA) — never hover-only.
- Status conveyed by **icon + text + color**, never color alone (WCAG 1.4.1).
- Amber/green/grey tokens via theme (`theme.colorWarning`, `colorSuccess`,
  `colorTextSecondary`) for ≥3:1 non-text contrast.

---

## 6. Relationship to the existing `SemanticLayerStateBadge` (D4)
A `SemanticLayerStateBadge` already renders **right** of the dropdown, showing project name /
document count (`SemanticLayerStateBadge.tsx`). It is **not** a mode indicator.
**Recommendation:** add the new **mode** badge on the left (this feature); leave the existing
state badge on the right but **fold its doc-count into the new tooltip's factor-7 row** to
avoid two overlapping "semantic layer" chips. Alternatively (D4-alt) retire the right badge
entirely and consolidate all semantic-layer status into the left badge + tooltip. Lean toward
consolidation if the chip row feels crowded.

---

## 7. Risks & mitigations
| # | Risk | Mitigation |
|---|---|---|
| R1 | **Badge drifts from actual mode** (FE reimplements factor logic) | Compute server-side reusing the literal `graph.py` expression; FE renders, never decides. Single source of truth. |
| R2 | **Request storm** (badge refetches on every keystroke/focus) — we already hit this in MDL Lab ([[mdl-lab-request-storm]]) | Fetch only on scope change (db/catalog/schema/project); debounce; cache by scope key; reuse the auth-cache pattern from the storm fix. Endpoint is cheap (no per-request authz N+1). |
| R3 | **Leaking infra detail** (env var names, engine internals) to non-operators | User-facing labels, not raw flag names. Remediation copy keyed by `fixable_by`: users get imperatives; operator/database blockers get factual statements, no secrets. Config is operator-domain per SECURITY.md — reporting "disabled by operator" is fine. |
| R4 | **Accessibility regressions** (color-only, hover-only) | icon+text+color; hover+focus; keyboard-reachable trigger (§5.5). |
| R5 | **Factor 8 is query-time** → tooltip shows stale/unknown runtime state | Mark it "evaluated when a query runs"; hydrate from latest `wren_context` when present; never assert success pre-query. |
| R6 | **"Native = broken" misread** | Neutral grey + copy framing native as a valid mode; amber only on user-fixable blockers. |
| R7 | **Stale factors** after user activates a project / changes schema in another surface | Refetch on dropdown open (reuse existing `refreshSemanticProjects` on `onOpenChange`) and on project/schema change. |

---

## 8. Decision points (summary)
| ID | Decision | Recommendation |
|---|---|---|
| D1 | New endpoint vs extend existing / infer on FE | **New lightweight endpoint**, server computes all factors. |
| D2 | Badge color for native + when to show amber | **Grey native**; amber adornment only when a **user-fixable** blocker (factors 6–7) exists. |
| D3 | Tooltip vs Popover | **Popover on hover+focus** (WCAG 1.4.13), essential state in the visible label; rich Tooltip only an acceptable v1 fallback since the label already carries the state. |
| D4 | Keep / merge the existing right-side state badge | **Keep mode badge left; fold doc-count into tooltip**; consider retiring the right badge if crowded. |
| D5 | Two-state (Semantic/Native) vs three-state (add "Degraded") | **Two-state.** Mode is binary in code; "degraded" adds ambiguity. Blockers live in the tooltip, not a third badge color. |
| D6 | Show remediation env-var names for operator blockers | **No raw var names in UI**; factual copy + link to docs. (Revisit if this panel is admin-gated.) |

---

## 9. Implementation outline (for a follow-up `_impl.md`)
**Backend**
1. Extract the mode computation into one shared function returning a structured
   `SemanticModeStatus` (factors 1–8 + composed mode), used by both graphs AND the endpoint —
   guarantees no drift (R1).
2. Add `GET /agent/semantic-sql-mode-status` in `app.py`; schema in `schemas.py`.
3. Unit-test each factor's blocked/met mapping (esp. factor 4 = Oracle blocked).

**Frontend**
4. `api.ts`: `getSemanticModeStatus(scope)` + `SemanticModeStatus` type.
5. New `SemanticModeBadge.tsx` (Tag + rich Tooltip + per-factor icon rows), placed left in
   the `<Flex>` at `index.tsx:1330`.
6. Fetch on scope change + dropdown open; cache by scope key; hydrate factor 8 from last
   query response.
7. Fold/retire the existing right-side `SemanticLayerStateBadge` per D4.

**Tests:** unit-test badge state mapping (semantic/native, amber-when-user-fixable), tooltip
factor rows, and keyboard/focus a11y.

---

## 10. Key citations (research backing)
- **Native = neutral, not red; green=ON, grey=OFF/fallback, amber=blocking-recoverable, red=failure:**
  Carbon status-indicator pattern; Atlassian Lozenge; Polaris Badge; Ant Tag/Badge; NN/g
  indicators/validations (amber overstatement anti-pattern).
- **"Explain why blocked + what to do next" (precondition checklist):** GitHub required status
  checks / branch protection; `react-password-checklist` (live met/unmet ticks); CI
  deployment gates ("when a gate fails, explain why"); NN/g "Why Disabled Buttons Hurt UX".
- **Color-not-alone, icon+text+color, hover+focus:** WCAG 1.4.1; PatternFly status-and-severity.
- **Directly analogous mode/readiness surfaces:** Power BI storage-mode in status bar + table
  tooltip; Tableau live-vs-extract icon in Data pane; Looker LookML-validation status
  (green "No errors" / red count); **dbt's explicit two-mode pattern — "check whether the
  Semantic Layer can answer first, otherwise fall back to text-to-SQL"** (validates surfacing
  the mode boundary at all).
