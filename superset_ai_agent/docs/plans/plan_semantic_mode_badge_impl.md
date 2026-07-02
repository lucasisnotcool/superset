# Implementation Plan — Semantic Mode Badge (AI SQL Agent)

Companion to `plan_semantic_mode_badge_spec.md`. This is a **sequential, resumable
checklist**: do phases in order, tick boxes as you go, honor the `Depends on` / `Blocks`
notes. Each step lists the exact entrypoint (file:line), the change, the pattern/source it
follows, and an acceptance check.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked (note why).

---

## BUILD STATUS — Phases 1–6 implemented (guidance-flag fast-follow 6.3 deferred)

Shipped in this session:
- **Phase 1** `semantic_layer/engine/mode.py` (`evaluate_semantic_factors`, `guidance_enabled`)
  + `SemanticModeStatus`/`SemanticModeFactor`/`SemanticFactorState` in `semantic_layer/schemas.py`
  + 9 factor-matrix tests (`tests/unit_tests/superset_ai_agent/test_semantic_mode.py`). ✅
- **Phase 2** both graphs call `guidance_enabled` (behavior-preserving). 252 graph/app tests pass. ✅
- **Phase 3** `GET /agent/semantic-layer/mode-status` in `app.py` + 2 endpoint tests
  (`test_semantic_layer_api.py`). ✅
- **Phase 4** `getSemanticModeStatus` + types in `AiAgentPanel/api.ts`. ✅
- **Phase 5** `AiAgentPanel/SemanticModeBadge.tsx` (Tag + Popover hover/focus), wired left of
  the selector in `index.tsx` with a scope-keyed fetch effect. ✅
- **Phase 6** 5 badge jest tests pass; 17 panel tests still pass; ruff/prettier clean.
  **6.3 (guidance-flag dialect fix) intentionally deferred** — changes agent behavior, ship
  separately (see §8 / D-IMPL-4).

Pre-existing unrelated failures (verified by stashing this work — they fail on a clean
master too): `test_llm_usage_store::test_by_day_buckets_on_utc_date` (date-rollover flake)
and `test_multi_schema_schema_index::test_bulk_activate_fetches_live_schema_once...`
(schema-fetch-count). **Not caused by this change.**

Known gaps / risks carried forward → see §13 "Post-build gaps".

---

## 0. Critical design decision — read before coding

**The badge MUST be driven by the full 8-factor set, NOT the existing 2-factor
`semantic_sql_mode` boolean.** This is the crux of the whole feature.

The current flag at `graph.py:1070` / `conversation_graph.py:1743` is only:
```python
semantic_sql_mode = config.wren_semantic_sql_enabled and engine.name != "passthrough"
```
That is factors **1 & 2 only**. It is `True` on Oracle (engine name is still `"wren_core"`)
even though the engine **degrades to passthrough at call time** because Oracle has no
wren-core dialect (factor 4). **That exact gap caused the incident**: guidance says "write
semantic SQL," the engine silently can't rewrite, the model's semantic SQL hits Oracle raw →
`ORA-00904`. If the badge mirrored this flag it would show a green "Semantic" badge on Oracle
— actively lying. So:

- **Badge "Semantic" state ⇔ factors 1–7 all met** (factor 8 is runtime/best-effort).
- **Decision D-IMPL-1:** the badge reflects *"will semantic rewrite actually apply to my next
  query in this scope?"*, not the narrow authoring flag. **Recommended & assumed below.**
- **Related finding (fast-follow, out of scope here):** the authoring-guidance flag itself
  should also incorporate dialect support so guidance isn't injected when the engine will
  degrade. Tracked as Phase 6, optional. Do not silently fold it into the badge work.

---

## 1. Requirements

**Functional**
- R-F1: Badge sits **left** of the project selector (`index.tsx:1330` `<Flex>`).
- R-F2: Two states — `Semantic` (all factors met) / `Native` (≥1 blocker). Binary (spec D5).
- R-F3: Hover **and focus** surface lists all 8 factors, each with met / blocked /
  n-a / runtime state and a one-line reason; amber `WarningOutlined` beside each **blocking**
  factor.
- R-F4: Amber adornment on the badge itself **only** when a **user-fixable** blocker exists
  (factors 6–7); deployment/database blockers → plain grey badge (spec §5.2).
- R-F5: State comes from server truth via one endpoint; FE never recomputes factor logic.
- R-F6: Refetch on scope change (db/catalog/schema/project) and on selector open; never per
  keystroke.

**Non-functional**
- R-N1: No badge/endpoint logic drift from the graphs (shared helper — Phase 2).
- R-N2: No request storm ([[mdl-lab-request-storm]]): debounce + cache by scope key.
- R-N3: WCAG 1.4.13 (dismissible/hoverable/persistent) + 1.4.1 (icon+text+color, not color
  alone); trigger keyboard-focusable.
- R-N4: No raw config/env-var names leaked in user copy (spec R3).

---

## 2. Dependencies & blockers (global)
- **B1:** Phase 3+ (endpoint, FE) **depend on** Phase 1 (shared helper) landing first — it is
  the single source of truth. Do not build the FE against a hand-rolled shape.
- **B2:** Factor 4 needs the DB backend string → `SupersetClient.get_database_dialect(
  database_id)` (`client.py:163`). Confirm it returns a backend token (e.g. `"oracle"`,
  `"postgresql"`) before Phase 1.
- **B3:** Factor 7 needs active MDL files → `active_mdl_file_store.list(project_id,
  owner_id=...)` filtered to `status == "active"` (pattern: `app.py:2573`, `2450`).
- **B4:** No new DB migration or persistent state is required (all factors derive from config,
  engine availability, dialect map, and existing stores). Confirm this stays true.

---

## 3. Phase 1 — Shared backend mode-status helper  *(foundation; blocks all)*
`Depends on: B2, B3` · `Blocks: Phases 2–5`

- [ ] **1.1** Create `superset_ai_agent/semantic_layer/engine/mode.py` (new file, ASF header).
  Co-locate with `base.py`'s `resolve_dialect`/`BACKEND_TO_WREN_DIALECT` (`base.py:47–64`)
  since dialect support (factor 4) lives there.
- [ ] **1.2** Define pydantic models (mirror `SemanticProjectReadiness`,
  `semantic_layer/schemas.py:652`):
  ```python
  SemanticFactorState = Literal["met", "blocked", "not_applicable", "runtime"]
  class SemanticModeFactor(BaseModel):
      key: str; label: str; state: SemanticFactorState
      blocking: bool; detail: str
      fixable_by: Literal["operator", "user", "database", "runtime"]
  class SemanticModeStatus(BaseModel):
      mode: Literal["semantic", "native"]
      factors: list[SemanticModeFactor]
      blocking_factors: list[str]
      user_fixable_blocker: bool   # drives R-F4 amber-on-badge
  ```
  Put these in `semantic_layer/schemas.py` (next to `SemanticProjectReadiness`) so they're
  importable by both the helper and the route.
- [ ] **1.3** Implement the pure evaluator:
  ```python
  def evaluate_semantic_factors(*, config: AgentConfig, engine: SemanticEngine,
      backend: str | None, schema_selected: bool, project_selected: bool,
      has_active_models: bool, context_loaded: bool | None = None) -> SemanticModeStatus
  ```
  Factor → check mapping (all 8, in this order):
  | key | check | blocked when | fixable_by |
  |---|---|---|---|
  | `semantic_sql_enabled` | `config.wren_semantic_sql_enabled` (`config.py:260`) | False | operator |
  | `engine_wren_core` | `config.wren_engine == "wren_core"` (`config.py:259`) | passthrough | operator |
  | `engine_installed` | `engine.is_available()` (`wren_core_engine.py:66`) | False | operator |
  | `dialect_supported` | `resolve_dialect(backend) is not None` (`base.py:64`) | None (Oracle/SQLite) | database |
  | `wren_enabled` | `config.wren_enabled` (`config.py:115`) | False | operator |
  | `scope_selected` | `schema_selected or project_selected` (`graph.py:447`, gate `wren_require_schema_scope` `config.py:141`) | neither | **user** |
  | `active_models` | `has_active_models` (`graph.py:503`, `app.py:2573`) | False | **user** |
  | `context_loaded` | `context_loaded` (`http_client.py:71`) | None→`runtime`, False→blocked | runtime |
  - `mode = "semantic"` iff every factor 1–7 is `met` (factor 8 not required; `runtime` ≠
    blocking when unknown).
  - `user_fixable_blocker = any(f.blocking and f.fixable_by == "user")`.
  - `detail` copy: user-fixable → imperative ("Select a schema or pin a project");
    operator/database → factual, **no env-var names** (R-N4): e.g. "This database's dialect
    isn't supported by the semantic engine."
- [ ] **1.4** Unit-test the matrix (`tests/.../test_semantic_mode.py`): Oracle→blocked
  dialect+native; flag off→native; no schema→native+user_fixable; happy path→semantic;
  factor-8 unknown→still semantic. **Acceptance:** Oracle case yields
  `mode=="native"`, `blocking_factors==["dialect_supported"]`, `user_fixable_blocker==False`.

**Source/pattern:** `base.py:47` (dialect map), `factory.py` (engine), spec §3 + §10.

---

## 4. Phase 2 — Wire graphs to the shared helper  *(no-drift; recommended)*
`Depends on: Phase 1` · `Blocks: nothing (R-N1 guarantee)`

- [ ] **2.1** Replace the inline boolean at `graph.py:1070` and `conversation_graph.py:1743`
  with a call into the shared module so factors 1&2 can never diverge. Keep the *authoring
  guidance* semantics identical for now (still gate on factors 1&2 only — see D-IMPL-1
  related finding; do **not** change guidance behavior in this phase).
  - Minimal version: expose `guidance_enabled(config, engine)` in `mode.py` returning
    `config.wren_semantic_sql_enabled and engine.name != "passthrough"`, call it in both
    graphs. This centralizes the literal without behavior change.
- [ ] **2.2** Run the agent graph tests to confirm zero behavior change.
  **Acceptance:** existing graph/conversation tests pass unchanged.

---

## 5. Phase 3 — Backend endpoint + schema
`Depends on: Phase 1` · `Blocks: Phase 4–5`

- [ ] **3.1** Add route in `app.py` next to `get_project_readiness` (`app.py:2583`). Because
  factors 6–7 are scope-dependent and 1–5 are global, accept scope as query params:
  ```python
  @api.get("/agent/semantic-layer/mode-status", response_model=SemanticModeStatus)
  def get_semantic_mode_status(
      fastapi_request: Request, database_id: int,
      catalog: str | None = None, schema: str | None = None,
      project_id: str | None = None,
      identity: AgentIdentity = identity_dependency,
  ) -> SemanticModeStatus: ...
  ```
  - **Decision D-IMPL-2:** project-scoped path vs query-param scope. **Recommend query-param**
    (above) — the badge must render before a project is pinned (factor 6 = "nothing selected"
    is a valid, displayable state). A `{project_id}` path can't represent "no project."
- [ ] **3.2** In the handler:
  - Authorize: if `project_id` present, `authorize_semantic_project(... permission="read")`
    (`app.py:2599`); else authorize by database access (reuse the db-access check used by
    other scope endpoints).
  - `backend = superset_client.get_database_dialect(database_id)` (`client.py:163`).
  - `has_active_models =` any active file via `active_mdl_file_store.list(project_id,
    owner_id=...)` when `project_id` else `False` (`app.py:2573`).
  - `engine = create_semantic_engine(config)` (`factory.py`) — or reuse a shared instance.
  - Return `evaluate_semantic_factors(config=config, engine=engine, backend=backend,
    schema_selected=bool(schema), project_selected=bool(project_id),
    has_active_models=has_active_models, context_loaded=None)`.
- [ ] **3.3** Register `SemanticModeStatus` in the OpenAPI/response models list if the app
  maintains one. **Acceptance:** `GET …/mode-status?database_id=<oracle-db>` returns
  `mode:"native"` with the dialect factor blocked; a Postgres db with enabled flags + pinned
  ready project returns `mode:"semantic"`.

**Source/pattern:** `app.py:2583–2602` (readiness route), `app.py:2440` (`_project_readiness`
store access), `client.py:163`.

---

## 6. Phase 4 — Frontend API client + types
`Depends on: Phase 3` · `Blocks: Phase 5`

- [ ] **4.1** In `superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts`, add types
  mirroring the backend models (place near `SemanticProjectReadiness`, `api.ts:655`):
  `SemanticFactorState`, `SemanticModeFactor`, `SemanticModeStatus`.
- [ ] **4.2** Add fetcher using the existing `requestJson<T>` + `getAgentBaseUrl()` helpers
  (`api.ts:~1840`):
  ```ts
  export const getSemanticModeStatus = (
    scope: { databaseId: number; catalog?: string|null; schema?: string|null; projectId?: string|null }
  ): Promise<SemanticModeStatus> =>
    requestJson(`/agent/semantic-layer/mode-status?…querystring…`);
  ```
  Mirror `getProjectReadiness`/`getProjectSemanticLayerState` (the panel already calls these).
  **Acceptance:** typed call compiles; returns parsed `SemanticModeStatus`.

---

## 7. Phase 5 — Badge component + panel integration
`Depends on: Phase 4`

- [ ] **5.1** New `SemanticModeBadge.tsx` in `AiAgentPanel/`. Build from existing house
  patterns (no new design primitives):
  - **Badge:** `Tag` from `@superset-ui/core/components`, color `success` (semantic) /
    `default` (native) — exactly the variant pattern in `CoverageBadge.tsx:133`.
  - **Label:** always-visible text `Semantic` / `Native` (carries the *essential* state so the
    hover surface only holds supplementary detail — WCAG 1.4.1 + spec §5.4).
  - **Overlay:** `Popover` (`@superset-ui/core/components`) opened on **hover AND focus**,
    `trigger={['hover','focus']}` (AntD), Esc-dismissible. Content = factor list using
    `List` (pattern: `features/alerts/buildErrorTooltipMessage.tsx:24`) inside a styled
    container (pattern: `CustomizationsBadge/index.tsx:242–306`).
  - **Per-factor icon:** `Icons.CheckCircleOutlined` (met, `theme.colorSuccess`),
    `Icons.WarningOutlined` (blocked, `theme.colorWarning` — pattern `ControlHeader.tsx:149`),
    `Icons.MinusOutlined` (n/a, `theme.colorTextSecondary`), neutral dot (runtime).
  - **Badge amber adornment:** render a small `WarningOutlined` on the badge only when
    `status.user_fixable_blocker` (R-F4).
  - **A11y:** trigger `role="button"` `tabIndex={0}` `aria-label` summarizing mode (R-N3).
- [ ] **5.2** Insert into the panel at `index.tsx:1330`, **before** `<ProjectSelectWrap>`
  inside the existing `<Flex align="center" gap={4}>` (left of the dropdown).
- [ ] **5.3** Fetch wiring in `index.tsx`:
  - Scope from `useSelector(getActiveQueryEditor)` (`index.tsx:~671,715–722`): `dbId`,
    `catalog`, `schema`; plus `selectedProjectId` (`index.tsx:711`).
  - Call `getSemanticModeStatus` in an effect keyed on `[dbId,catalog,schema,selectedProjectId]`;
    **debounce + cache by scope key** (R-N2 / [[mdl-lab-request-storm]]). Also refetch on
    selector open via the existing `onOpenChange`/`refreshSemanticProjects` hook
    (`index.tsx:1339`).
  - Hydrate factor 8 (`context_loaded`) opportunistically from the latest
    `AgentQueryResponse.wren_context.available` when present (spec §3 factor 8).
- [ ] **5.4** Reconcile with the existing right-side `SemanticLayerStateBadge`
  (`SemanticLayerStateBadge.tsx`, rendered at `index.tsx:1351`): **fold its doc-count into the
  new badge's `active_models` factor row and remove the right badge** (spec D4). If the chip
  row still reads cleanly with both, keeping it is acceptable — note the choice here:
  `[ ] folded / [ ] kept` → ____.
  **Acceptance:** on an Oracle scope the badge shows grey `Native`, no amber-on-badge, and the
  popover shows `⚠ Database dialect supported`; on a ready Postgres project it shows green
  `Semantic`.

---

## 8. Phase 6 — Tests + (optional) guidance-flag fast-follow
`Depends on: Phases 1,3,5`

- [ ] **6.1** Backend: factor-matrix unit tests (done in 1.4) + endpoint test (authz, Oracle,
  happy path).
- [ ] **6.2** FE: Jest/RTL test for `SemanticModeBadge` — state mapping (semantic/native),
  amber-only-when-user-fixable, factor rows render with correct icons, popover opens on
  **focus** (keyboard), Esc dismisses. Use `@superset-ui/core` test render
  (`spec/helpers/testing-library.tsx`).
- [ ] **6.3** *(Optional, related finding)* Make the **authoring-guidance** flag incorporate
  dialect support so semantic guidance is not injected when the engine will degrade
  (`graph.py:1070` / `conversation_graph.py:1743`). This directly addresses the incident's
  mechanism but changes agent behavior — **gate behind review, ship separately** from the
  badge. Cross-reference the mode-incoherence analysis.

---

## 9. Risks & mitigations (impl-specific)
| # | Risk | Mitigation | Phase |
|---|---|---|---|
| R1 | Badge drifts from real mode (FE/endpoint reimplement logic) | Single `evaluate_semantic_factors`; graphs call shared helper (Phase 2) | 1,2 |
| R2 | **False green on Oracle** (using the 2-factor flag) | Badge uses full 8-factor set; dialect factor blocks (D-IMPL-1) | 1 |
| R3 | Request storm on scope/focus churn | Debounce + cache by scope key; refetch only on scope change/open (R-N2) | 5 |
| R4 | Endpoint authz gap (scope params bypass project authz) | Authorize project when `project_id`, else db-access check (3.2) | 3 |
| R5 | Leaking infra/env detail in copy | `fixable_by`-keyed copy, no var names (R-N4) | 1,5 |
| R6 | Hover-only essential info / non-focusable trigger (a11y) | Essential state in visible label; Popover on hover+focus; focusable trigger (R-N3) | 5 |
| R7 | Factor 8 asserted pre-query (stale "loaded") | `context_loaded=None` → `runtime` state, never "met" pre-query | 1,5 |
| R8 | Two redundant "semantic layer" chips | Fold/retire right badge (5.4 / spec D4) | 5 |

---

## 10. Decision points (impl)
| ID | Decision | Recommendation |
|---|---|---|
| D-IMPL-1 | Badge driven by 2-factor flag vs full 8 factors | **Full 8 factors** — else false-green on Oracle. |
| D-IMPL-2 | Endpoint scoped `{project_id}` path vs query-param scope | **Query-param scope** — must render before a project is pinned. |
| D-IMPL-3 | Fold vs keep the existing right-side state badge | **Fold into new badge tooltip** (revisit if row uncrowded). |
| D-IMPL-4 | Ship guidance-flag dialect fix (6.3) with badge | **No — separate PR**, it changes agent behavior. |

---

## 11. Touchpoint index (quick map for future sessions)
**Backend**
- `semantic_layer/engine/mode.py` *(new)* — `evaluate_semantic_factors`, `guidance_enabled`
- `semantic_layer/schemas.py:652` area — `SemanticModeStatus`/`SemanticModeFactor`
- `semantic_layer/engine/base.py:47,64` — `BACKEND_TO_WREN_DIALECT`, `resolve_dialect`
- `semantic_layer/engine/factory.py` — `create_semantic_engine`
- `semantic_layer/engine/wren_core_engine.py:66` — `is_available`
- `graph.py:1070`, `conversation_graph.py:1743` — flag call-sites (Phase 2)
- `app.py:2583` area — new `get_semantic_mode_status` route; `app.py:2440` `_project_readiness`
  (store-access pattern); `2573` active-files pattern
- `integrations/superset/client.py:163` — `get_database_dialect`
- `config.py:115,141,259,260` — `wren_enabled`, `wren_require_schema_scope`, `wren_engine`,
  `wren_semantic_sql_enabled`

**Frontend**
- `AiAgentPanel/api.ts:655` area — types; `:~1840` — `requestJson`/`getAgentBaseUrl` + new
  `getSemanticModeStatus`
- `AiAgentPanel/index.tsx:1330` — insertion point (Flex); `:711,715–722` — scope/selection
  state; `:1339` — `onOpenChange` refetch hook; `:1351` — existing right badge
- `AiAgentPanel/SemanticModeBadge.tsx` *(new)*
- Pattern refs: `CoverageBadge.tsx:133` (Tag), `CustomizationsBadge/index.tsx:242–306`
  (rich overlay), `ControlHeader.tsx:149` (amber WarningOutlined),
  `features/alerts/buildErrorTooltipMessage.tsx:24` (List in overlay)
- `AiAgentPanel/SemanticLayerStateBadge.tsx` — fold/retire (5.4)

---

## 13. Post-build gaps (expectation ↔ implementation)
Honest notes for review — where the shipped UI may differ from what the user pictured:

1. **Right-side state badge kept, not folded (D4 deferred).** The user asked to *add* a left
   mode badge; I did exactly that and left the existing right-side `SemanticLayerStateBadge`
   (project/doc-count) in place. Result: two chips flank the dropdown. The spec recommended
   folding doc-count into the new tooltip. **Decision still open** — keep both, or fold. Low
   effort either way; left as-is to honor the literal ask and minimize churn.
2. **Factor 7 ("active models") is project-scoped only.** When a *schema* is selected but no
   project is pinned, the endpoint reports `has_active_models=false` rather than resolving
   whether the schema maps to a project with active models. Honest (the agent has no pinned
   models to ground on) but it could read "No active models" where a user expects "a project
   covers this schema." A follow-up could resolve the schema→project candidate read-only.
3. **Factor 8 ("context loaded") never turns green in the badge today.** The FE passes
   `context_loaded=undefined` (always "checked at query time"). The spec's opportunistic
   hydration from the last query's `wren_context.available` is **not yet wired** — so the
   runtime row stays informational. Deterministic factors 1–7 fully drive the verdict, so
   this doesn't cause false-greens; it just means the runtime row is never a ✓.
4. **No client-side cache beyond effect-dep keying.** R-N2 is satisfied by keying the fetch
   on scope primitives (one request per scope change, not per keystroke), but there is no
   explicit scope-keyed memo/TTL. If scope toggles rapidly back and forth it refetches. Add a
   small cache if telemetry shows churn.
5. **Popover trigger is hover+focus (not click).** Matches the user's "on hover" ask and is
   WCAG-1.4.13 compliant (essential state is in the always-visible label). If product wants a
   click/toggletip instead, it's a one-line `trigger` change.
6. **Copy is generic by design (R-N4).** Blocking reasons avoid env-var/engine names
   ("turned off for this deployment", not `WREN_SEMANTIC_SQL_ENABLED=false`). An operator
   debugging a deployment won't get the exact flag from the badge — intentional; revisit if
   this panel becomes admin-gated.
7. **Deepest fix still deferred (6.3).** The badge makes the mode *visible*, but the original
   incident's mechanism — guidance injected on an unsupported dialect — is only fixed when the
   guidance flag itself incorporates dialect support. Tracked, not shipped here.

## 12. Definition of done
- [ ] Endpoint returns correct `SemanticModeStatus` for: Oracle (native/dialect-blocked),
  flag-off (native), no-schema (native/user-fixable), ready-Postgres-project (semantic).
- [ ] Badge renders left of selector; green `Semantic` / grey `Native`; amber-on-badge only
  for user-fixable blockers; popover lists 8 factors with correct icons + reasons.
- [ ] Graphs call the shared helper; agent tests unchanged (R-N1).
- [ ] No request storm (verified: one fetch per scope change, cached).
- [ ] A11y: keyboard-focusable, popover on focus, Esc-dismiss, not color-alone.
- [ ] Backend + FE tests green; `pre-commit run --all-files` clean.
