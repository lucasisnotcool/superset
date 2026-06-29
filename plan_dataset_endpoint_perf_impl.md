# Dataset endpoint performance — implementation plan & checklist

**Status:** IMPLEMENTED (Tracks 0/A/B/C) · **Owner:** aekyr · **Created:** 2026-06-29 · **Implemented:** 2026-06-29

> See **§12 Implementation results** at the bottom for what shipped, test counts,
> live-container verification evidence, and residual risks/gaps.

A source-backed, resumable checklist to reduce load from repeated
`GET /api/v1/dataset/?q=…` (list) and `GET /api/v1/dataset/<id>` (detail) calls.
Future agent sessions: tick boxes as you go, respect the dependency/blocker notes,
and do not skip the verification step at the end of each track.

---

## 0. Background & evidence (verified against source)

| Fact | Source |
|---|---|
| Detail `get` is a custom override; serializes via marshmallow `show_model_schema.dump(table)` (NOT the `.data` property, so no `select_star`/`health_check_message` on this path) | [superset/datasets/api.py:1219](superset/datasets/api.py#L1219), dump at [:1286](superset/datasets/api.py#L1286) |
| When the client sends **no** `columns:` projection, the full `show_select_columns` (every `columns.*` + `metrics.*`) is dumped — the heaviest payload | [superset/datasets/api.py:1280](superset/datasets/api.py#L1280) (`pruned_select_cols`), `show_select_columns` at [:152](superset/datasets/api.py#L152) |
| No eager-loading on the REST read path (unlike the MCP path) → extra relationship round-trips per request | DAO call [api.py:1266](superset/datasets/api.py#L1266); MCP precedent [mcp_service/dataset/tool/get_dataset_info.py:118](superset/mcp_service/dataset/tool/get_dataset_info.py#L118) |
| No `@etag_cache` / cache decorator on dataset `get` or `get_list` | grep of [superset/datasets/api.py](superset/datasets/api.py) |
| Frontend `cachedSupersetGet` deduplicates **by exact endpoint string**, caching the in-flight Promise in a module-level `Map` for the page lifetime | [utils/cacheWrapper.ts:20](superset-frontend/src/utils/cacheWrapper.ts#L20), [utils/cachedSupersetGet.ts:25](superset-frontend/src/utils/cachedSupersetGet.ts#L25) |
| `GroupByFilterCard` fetches bare `/api/v1/dataset/<id>` (no projection → heaviest) and its effect re-fires on every dashboard filter change | [GroupByFilterCard.tsx:470](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L470), deps [:504](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L504) |
| AI-agent onboarding picker re-lists **all schemas on every `window` focus** + re-checks write permission on every open; list helper is an uncached paginated loop | focus listener [OnboardingTablePicker.tsx:511](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L511); `loadAll` [:480](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L480); permission [:503](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L503); list loop [api.ts:1742](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L1742) |
| Stray NUL byte (~offset 3194) makes `OnboardingTablePicker.tsx` read as binary to grep/ripgrep | [OnboardingTablePicker.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx) |

### Log fingerprints (use to confirm the real culprit before/after changes)
- `…/dataset/?q=(columns:!(id,table_name),filters:!((col:database…),(col:schema…)),…,page_size:1000)` → AI-agent `listAllRegisteredTables` loop (Track B).
- `…/dataset/_info?q=(keys:!(permissions))` → AI-agent `getDatasetWritePermission` (Track B).
- `…/dataset/<id>` **with no query string** → `GroupByFilterCard` / `ViewQuery` (Track C / heaviest payload, Track A).
- `…/dataset/<id>?q=(columns:!(columns.column_name,…))` → ColumnSelect / FiltersConfigForm (already projected, low priority).

---

## 1. Scope

**In scope (this plan):**
- **Track 0** — strip the NUL byte (pre-task; unblocks text tooling).
- **Track A** — server-side: eager-load the detail read + per-user-safe conditional-GET (ETag).
- **Track B** — AI-agent onboarding picker: stop the focus-storm + cache the permission check.
- **Track C** — `GroupByFilterCard`: column projection + effect stabilization.

**Deferred (appendix, not required for "the three"):**
- **Track D** — typeahead pickers (ChartCreation / ChartList / SaveDatasetModal): add `columns:` projection + confirm debounce.

---

## 2. Shared requirements / global definition-of-done

- [ ] R-G1 — No behavior change to any endpoint's response **shape** unless explicitly noted (clients depend on field presence).
- [ ] R-G2 — Every code change carries proper type hints (Python: mypy-clean; TS: no `any`) per `CLAUDE.md`.
- [ ] R-G3 — `pre-commit run --all-files` passes (mypy, ruff/pylint, eslint, prettier) before any push.
- [ ] R-G4 — New/changed behavior covered by unit tests (prefer unit over integration per `CLAUDE.md`).
- [ ] R-G5 — No security regression: a principal must never receive a dataset payload the `DatasetDAO` `base_filter` would deny them (see Track A risk A-R1).
- [ ] R-G6 — Capture a before/after of the fingerprinted log lines (or DevTools Network panel) to prove the reduction; attach to the PR's TESTING section.

---

## 3. Decision points (resolve before coding the affected track)

| # | Decision | Options | Recommendation | Blocks |
|---|---|---|---|---|
| D1 | How to add eager-loading to the detail read | (a) add optional `options=` param to shared `BaseDAO.find_by_id_or_uuid`; (b) build a local eager query inside the `get` override (must re-apply `base_filter` for security) | **(a)** — additive/optional, defaults to no-op, mirrors how the MCP path already threads `query_options`; lowest duplication. Keep `base_filter` enforcement inside the DAO where it already lives. | Track A1 |
| D2 | Server caching strategy for detail | (i) shared server-side response cache via `@etag_cache(cache=…)`; (ii) per-user-safe **conditional-GET only** (ETag/Last-Modified → 304, `Cache-Control: private`), no shared body store; (iii) both | **(ii)** — avoids the cross-principal cache-leak (A-R1) entirely, is browser/proxy standard, and the FE already dedupes within a session so a shared server store buys little. Implement a manual ETag in the `get` override so the expensive `dump` is skipped on 304. | Track A2 |
| D3 | Also cache/optimize `get_list`? | (a) leave as-is; (b) ETag the list too | **(a) defer** — `get_list` is FAB-generated (no override in `datasets/api.py`); a correct `Last-Modified` needs `max(changed_on)` over the filtered set, and list payloads are already lighter (no nested columns/metrics). Revisit only if fingerprint logs show list `?q=` dominates after Track B. | Track A (scope) |
| D4 | Track B focus-reload: remove vs. guard | (a) delete the `window.focus` reload; (b) reload only when returning from the Add-Dataset tab; (c) debounce/throttle | **(b)** — preserves the original intent (reflect newly-registered tables) without re-listing on every focus. Fall back to (c) if return-detection proves flaky. | Track B2 |

---

## 4. Track 0 — strip the NUL byte (PRE-TASK)

**Why first:** while present, grep/ripgrep treat `OnboardingTablePicker.tsx` as binary, which hides Track B/C-adjacent searches and can confuse linters.
**Entrypoint:** [OnboardingTablePicker.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx) (~byte offset 3194).

- [ ] T0.1 — Confirm the byte: `grep -aobn $'\x00' <file>` (expect one hit ~3194).
- [ ] T0.2 — Strip it: `LC_ALL=C tr -d '\000' < file > file.tmp && mv file.tmp file` (or an editor "remove null bytes"). Do **not** otherwise reformat.
- [ ] T0.3 — Verify text-clean: `ripgrep -n "listAllRegisteredTables" <file>` now returns a normal line match (no "binary file matches").
- [ ] T0.4 — `git diff --stat` shows only the byte removed (no spurious line-ending churn). Run `prettier`/`eslint` on the file.

**Risk:** an editor may rewrite line endings → noisy diff. **Mitigation:** use the `tr`/`sed` byte-strip, then `git diff` to confirm a 0-line logical change. **Dependency:** none. **Unblocks:** clean grep for B/C.

---

## 5. Track A — server-side: eager-load detail + conditional-GET (ETag)

> Highest leverage: reduces per-call cost regardless of which client fires, and adds 304 short-circuiting so repeats skip serialization. **Depends on D1, D2.**

**Entrypoints / touchpoints:**
- [superset/datasets/api.py:1219](superset/datasets/api.py#L1219) — the `get` override (add eager-load + ETag).
- [superset/daos/base.py:200](superset/daos/base.py#L200) — `find_by_id_or_uuid` (add optional `options=`), per **D1(a)**.
- [superset/daos/dataset.py](superset/daos/dataset.py) — `DatasetDAO` (no change expected; inherits base).
- Pattern references: [mcp_service/dataset/tool/get_dataset_info.py:118](superset/mcp_service/dataset/tool/get_dataset_info.py#L118) (eager options), [utils/cache.py:170](superset/utils/cache.py#L170) + [views/core.py:295](superset/views/core.py#L295) (etag precedent).

### A1 — eager-load the detail relationships (do first; pure perf)
- [ ] A1.1 — Add an optional keyword param to `BaseDAO.find_by_id_or_uuid(... , options: list | None = None)`; when provided, apply `query = query.options(*options)` **after** `base_filter` is applied (so visibility filtering is unchanged). Keep default `None` → byte-identical behavior for all other callers (R-G1, R-G5).
- [ ] A1.2 — In the dataset `get` override, pass eager options mirroring the MCP path:
      `subqueryload(SqlaTable.columns)`, `subqueryload(SqlaTable.metrics)`, `joinedload(SqlaTable.database)`, and `selectinload(SqlaTable.owners)` (owners is many-to-many). Import from `sqlalchemy.orm`.
- [ ] A1.3 — Sanity-check query count with SQL echo / `db.session` profiling on a wide dataset (e.g. 100+ columns): expect the per-relationship lazy loads to collapse into a small fixed set of queries.
- [ ] A1.4 — Unit test: assert the response for a multi-column/metric dataset is unchanged field-for-field vs. baseline (guards R-G1).

### A2 — per-user-safe conditional-GET (ETag/304), per **D2(ii)**
- [ ] A2.1 — In the `get` override, **after** `find_by_id_or_uuid` (cheap, indexed) but **before** the expensive `show_model_schema.dump`, compute a strong ETag from stable inputs: `table.id`, `table.changed_on` (or `changed_on_utc`), `VERSION_STRING`/`VERSION_SHA` (see `__repr__` precedent in [dashboards/api.py:449](superset/dashboards/api.py#L449)), and the raw `q`/`include_rendered_sql` args (different projections ⇒ different bodies).
- [ ] A2.2 — If `request.if_none_match` matches, return `Response(status=304)` with the `ETag` header — **skipping the dump entirely** (this is the latency win). 404/visibility behavior stays first (find returns `None` ⇒ 404 ⇒ no ETag).
- [ ] A2.3 — On the 200 path, set `resp.set_etag(etag)`, `resp.cache_control.private = True`, and a small `resp.cache_control.max_age` (decision: start conservative, e.g. 0–60s, so a client revalidates rather than serves stale schema). Document the value.
- [ ] A2.4 — Unit tests: (a) first GET returns 200 + `ETag`; (b) repeat GET with `If-None-Match` returns 304 and an empty body; (c) after a dataset edit (changed_on bumps) the ETag changes and a 200 is returned; (d) a principal denied by `base_filter` still gets 404, never 304 (R-G5).

**Risks & mitigations (Track A):**
- **A-R1 (security — cross-principal cache leak):** a *shared* server-side body cache keyed only on id/args would let user B receive user A's cached `/dataset/<id>` body, bypassing `base_filter` 404. **Mitigation:** chose **D2(ii)** — conditional-GET returns only 304 (no body) and bodies stay in each browser's private cache; the visibility `find` runs on every request. If a future change adopts a shared cache, it MUST pass a `raise_for_access` that mirrors `DatasetDAO` visibility or include the principal in the cache key. (Threat-model row: data-bearing *datasets/datasources* in `SECURITY.md`.)
- **A-R2 (stale schema):** ETag derived from `changed_on` won't change if a related column/metric is mutated without bumping the parent `changed_on`. **Mitigation:** verify dataset edits bump `SqlaTable.changed_on`; if column/metric edits don't, fold a cheap `max(changed_on)` of columns/metrics into the ETag, or keep `max_age` near 0 so clients revalidate.
- **A-R3 (DAO blast radius):** `find_by_id_or_uuid` is shared by every DAO. **Mitigation:** optional param defaulting to `None`; add a base-DAO unit test that the no-arg path is unchanged.
- **A-R4 (mypy/typing on options list):** annotate as `list[ORMOption] | None` (or the type SQLAlchemy exposes) to stay mypy-clean (R-G2).

**Verification (Track A):** re-fetch the same dataset twice from a fresh tab; second request should be `304` in DevTools. Confirm query count dropped on the 200 path. Run `pre-commit run mypy` + dataset API tests.

---

## 6. Track B — AI-agent onboarding picker (stop the focus-storm)

> The most likely *automatic* repeater in this deployment. **Depends on Track 0** (clean grep) and **D4**.

**Entrypoints / touchpoints (all in [OnboardingTablePicker.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx)):**
- `loadSchema` [:431](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L431) → calls `listAllRegisteredTables` [:440](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L440).
- `loadAll` [:480](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L480).
- on-open reload effect [:485](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L485).
- `getDatasetWritePermission` effect [:503](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L503).
- `window.focus` reload effect [:511](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L511).
- Helper/permission API: [api.ts:1742](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L1742) (`listAllRegisteredTables`), [api.ts:1807](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L1807) (`getDatasetWritePermission`).

### B1 — cache the write-permission check (constant per session)
- [ ] B1.1 — `getDatasetWritePermission` returns a session-stable boolean; memoize it (module-level promise cache in `api.ts`, same shape as the existing `/health` reuse note near [api.ts:729](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L729), **or** route it through `cachedSupersetGet` since the endpoint string is constant). The on-open effect [:503](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L503) then hits cache after the first open.
- [ ] B1.2 — Preserve the permissive-on-failure behavior (`catch → setCanRegister(true)`).

### B2 — stop re-listing all schemas on every window focus (per **D4(b)**)
- [ ] B2.1 — Replace the unconditional `window.focus → loadAll()` ([:511](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L511)) with a guarded reload: only reload when the user actually launched the Add-Dataset tab. Implementation: set a ref flag when the "Add Dataset" action opens `ADD_DATASET_URL` (new tab); on focus, reload **only if** the flag is set, then clear it.
- [ ] B2.2 — Fallback (D4(c)) if return-detection is unreliable: debounce/throttle the focus reload (e.g. ignore focus events within N seconds of the last load) so rapid window switching can't storm the list endpoint.
- [ ] B2.3 — Keep the on-open initial `loadAll` [:496](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/OnboardingTablePicker.tsx#L496) intact (correct behavior).

### B3 — (optional) reduce the list-loop footprint
- [ ] B3.1 — Confirm `listAllRegisteredTables` already projects `columns:['id','table_name']` ([api.ts:1754](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L1754)) — it does; no payload change needed. Only the **call frequency** (B2) is the problem. Leave the pagination loop as-is.

**Risks & mitigations (Track B):**
- **B-R1 (stale picker after external registration):** guarding the focus reload could miss a table registered in another tab not via the Add-Dataset button. **Mitigation:** keep a manual "refresh" affordance (the modal already reloads on open); the Add-Dataset return path covers the common case.
- **B-R2 (memoized permission goes stale if the user's role changes mid-session):** acceptable — role changes mid-session are rare and the create POST still enforces server-side ([api.ts createDataset](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L1695)). Document it.
- **B-R3 (test coverage):** update `OnboardingTablePicker.test.tsx` and `api.test.ts` for the new focus-guard + permission memoization.

**Verification (Track B):** open the picker, switch windows repeatedly → fingerprint log line for the list loop should fire on open (and on Add-Dataset return) but **not** on every focus. `getDatasetWritePermission` fires at most once per session.

---

## 7. Track C — GroupByFilterCard projection + effect stabilization

> Cuts the heaviest single payload and stops render thrash. Network is already deduped by `cachedSupersetGet`, so this is primarily a **payload-size + re-render** fix, not a network-count fix. **Depends on Track 0** (clean grep).

**Entrypoint / touchpoints (all in [GroupByFilterCard.tsx](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx)):**
- `dependencies` memo [:427](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L427) (recomputes on every `effectiveDataMask` change [:443](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L443)).
- fetch effect [:445](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L445), endpoint [:470](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L470), deps `[dataset, dependencies, dispatch]` [:504](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L504).
- Fields actually consumed: `result.table_name` [:474](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L474); `result.columns[].filterable | .verbose_name | .column_name` [:477-:483](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L477).
- Projection pattern to mirror: [ColumnSelect.tsx:96](superset-frontend/src/dashboard/components/nativeFilters/FiltersConfigModal/FiltersConfigForm/ColumnSelect.tsx#L96) (`rison.encode({ columns: ['columns.column_name', …] })`).

### C1 — add a column projection (shrinks payload ~90%)
- [ ] C1.1 — Change the endpoint from `/api/v1/dataset/${datasetId}` to `/api/v1/dataset/${datasetId}?q=${rison.encode({ columns: ['table_name','columns.column_name','columns.verbose_name','columns.filterable'] })}`. Use `import rison from 'rison'` (as ColumnSelect does).
- [ ] C1.2 — Verify the consumed fields still resolve from the projected response (`table_name`, and each `columns[]` entry's `column_name`/`verbose_name`/`filterable`). Note: the `.name` fallback at [:481-:482](superset-frontend/src/dashboard/components/nativeFilters/FilterBar/FilterControls/GroupByFilterCard.tsx#L481) is dead (API field is `column_name`); leave it untouched to avoid scope creep unless adding a test.

### C2 — stabilize the effect so it stops re-firing on every filter change
- [ ] C2.1 — The effect only needs `datasetId`; `dependencies` is in the deps array but the fetch doesn't use it. Remove `dependencies` from the effect deps and key the effect on the resolved `datasetId` (compute `datasetId` outside the effect via `useMemo`, mirror ColumnSelect's `useChangeEffect(datasetId, …)`). This also removes the redundant `setLoading` churn.
- [ ] C2.2 — Confirm `dispatch` is stable (it is, from `useDispatch`) so it's safe in deps.
- [ ] C2.3 — Confirm no other consumer of `dependencies` is removed (it's only used by this effect here — verify with grep now that Track 0 made the file text-clean).

**Risks & mitigations (Track C):**
- **C-R1 (projection omits a field a future change needs):** keep the projection list adjacent to the consuming `.map` and comment why each field is requested.
- **C-R2 (behavior change if `dependencies` actually mattered):** evidence says the fetch body ignores `dependencies`; the value-dependent filtering happens elsewhere. **Mitigation:** add a test that changing an unrelated filter's value does **not** re-trigger the column fetch.
- **C-R3 (cache interaction):** projected URL is a different cache key than the bare URL — fine; it just creates one new (smaller) cache entry. No invalidation concerns.

**Verification (Track C):** load a dashboard with a group-by customization, change other filters repeatedly → exactly one `/dataset/<id>?q=…` fetch (projected), no re-fetch on filter changes, smaller payload in DevTools.

---

## 8. Track D — typeahead pickers (DEFERRED appendix)

Not part of "the three"; pick up only if post-Track-B fingerprint logs still show heavy list `?q=` from typeahead.
- Touchpoints: [ChartCreation/index.tsx:252](superset-frontend/src/pages/ChartCreation/index.tsx#L252), [ChartList/index.tsx:122](superset-frontend/src/pages/ChartList/index.tsx#L122), [SaveDatasetModal/index.tsx:316](superset-frontend/src/SqlLab/components/SaveDatasetModal/index.tsx#L316).
- Work: add `columns:!(id,table_name)` projection to the rison query; confirm the underlying `AsyncSelect` debounces search (the `@superset-ui/core` Select typically does — verify before claiming a fix). User-driven, so lower priority.

---

## 9. Master sequential checklist (with blockers)

Order is chosen so blockers clear first; Tracks A/B/C are otherwise independent and can be parallelized across sessions/PRs once Track 0 is done.

- [ ] **S1.** Track 0 — strip NUL byte. *(blocker for grep-accuracy in B/C; ~5 min)*
- [ ] **S2.** Resolve decision points D1, D2 (Track A) and D4 (Track B). *(blocks A & B coding)*
- [ ] **S3.** Track A1 — eager-loading. *(no external blocker after S2; pure perf, ship first)*
- [ ] **S4.** Track A2 — conditional-GET ETag. *(depends on S3 being in the same `get` override; same PR recommended)*
- [ ] **S5.** Track B1 — memoize permission check. *(depends on S1)*
- [ ] **S6.** Track B2 — guard focus reload. *(depends on S1; D4 resolved in S2)*
- [ ] **S7.** Track C1 — add projection. *(depends on S1)*
- [ ] **S8.** Track C2 — stabilize effect. *(depends on S1; same PR as S7)*
- [ ] **S9.** Per-track verification (sections 5–7) + capture before/after fingerprint logs (R-G6).
- [ ] **S10.** `pre-commit run --all-files` green; update tests; open PR(s) with conventional-commit titles (`perf(dataset): …`, `perf(ai-agent): …`, `perf(dashboard): …`).
- [ ] **S11.** (Optional) Track D, only if logs still warrant.

**Suggested PR split:** PR-1 = Track 0 + Track A (server); PR-2 = Track B (AI agent); PR-3 = Track C (dashboard). Independent review surfaces, independent rollback.

---

## 10. Consolidated risk register

| ID | Risk | Severity | Mitigation | Track |
|---|---|---|---|---|
| A-R1 | Shared cache leaks dataset payload across principals | High (security) | Conditional-GET only (D2(ii)); no shared body store; visibility `find` every request | A |
| A-R2 | ETag stale if child column/metric edit doesn't bump parent `changed_on` | Med | Verify changed_on bumps; else fold child `max(changed_on)`; keep `max_age` low | A |
| A-R3 | Shared base-DAO change affects all callers | Med | Optional param default `None`; regression test | A |
| B-R1 | Guarded focus reload misses external registration | Low | Reload on open + Add-Dataset return; manual refresh exists | B |
| B-R2 | Memoized permission stale on mid-session role change | Low | Server still enforces on create POST; documented | B |
| C-R2 | Removing `dependencies` dep changes behavior if it mattered | Low | Evidence shows fetch ignores it; add regression test | C |
| G | Pre-commit/mypy/eslint failures | Med | R-G2/R-G3; run before push | all |

---

## 11. Notes for the next session
- This file is the source of truth; update the **Status** line and tick boxes as you progress.
- Before claiming any track "done", re-run the relevant section-9 verification and paste the before/after fingerprint evidence into the PR.
- If fingerprint logs after Track B show the dominant lines are **detail `<id>` with no query string from a non-GroupBy caller**, search again (file is now text-clean) — there may be an additional bare-detail caller worth a projection.

---

## 12. Implementation results (2026-06-29)

All four tracks implemented, unit-tested, and lint/type-checked. Backend (Track A)
additionally verified live against the running `superset-superset-1` container.

### What shipped

| Track | Change | Files |
|---|---|---|
| 0 | NUL separator byte rewritten as the ` ` escape (runtime-identical, grep-safe) + warning comment | `OnboardingTablePicker.tsx` |
| A1 | Additive `options=` on `BaseDAO.find_by_id_or_uuid` (applied after `base_filter`); eager `subqueryload(columns/metrics)`, `selectinload(owners)`, `joinedload(database)` in dataset `get` | `daos/base.py`, `datasets/api.py` |
| A2 | Child-aware strong ETag + `If-None-Match` 304 (skips serialization); `Cache-Control: private, max-age=0` | `datasets/api.py` (`_detail_etag` + `get`) |
| B1 | Session-memoized `getDatasetWritePermission` (successes cached, failures retry, in-flight dedup) + `resetDatasetWritePermissionCache` test hook | `AiAgentPanel/api.ts` |
| B2 | `window.focus` reload gated on a "returned from Add-Dataset tab" ref flag, threaded to the virtualized row via `itemData.markAddDatasetOpened` | `OnboardingTablePicker.tsx` |
| C1 | Column projection on the card's dataset fetch (`table_name`, `columns.column_name/verbose_name/filterable`) | `GroupByFilterCard.tsx` |
| C2 | Effect keys on a memoized primitive `datasetId` (exported `resolveDatasetId`); removed the dead `dependencies`/`filters`/`mergeExtraFormData`/`NativeFilterType`/`Filters` churn | `GroupByFilterCard.tsx` |

### Tests (all green)
- **Python — 11 passed**: `tests/unit_tests/datasets/api_tests.py` (ETag 200→304 round-trip; child-edit ETag invalidation [A-R2]; eager-load options wiring) + `tests/unit_tests/datasets/dao/dao_tests.py` (`options` applied / `None` no-op).
- **Frontend — 58 passed** across 3 suites: `api.test.ts` (memoize / dedupe-concurrent / don't-cache-failures), `OnboardingTablePicker.test.tsx` (plain focus = NO refetch; Register-click then focus = refetch; +19 existing), `GroupByFilterCard.test.tsx` (`resolveDatasetId` normalization; projected-query render asserting no `metrics`).

### Quality gates
- PASS: `mypy`, `ruff`, `ruff-format`, `pylint` (Python); `prettier-frontend`, `custom-rules-frontend` (FE); `tsc --noEmit` clean for all changed files (it caught a real scope bug — the row-anchor `onClick` couldn't see the parent ref — fixed by threading `markAddDatasetOpened` through `itemData`).
- NOT RUN (environment-blocked, not code): `gitleaks` (SSL cert verify failed downloading the hook), `oxlint` (broken native binding `MODULE_NOT_FOUND`). Action for a clean env / CI: run `pre-commit run --all-files` before pushing.

### Live container verification (Track A; backend source is bind-mounted + auto-reloaded)
- `GET /api/v1/dataset/14` -> `200`, `ETag "bc10..."`, `Cache-Control: private, max-age=0`, body 5791 B.
- Repeat with `If-None-Match` -> `304 NOT MODIFIED`, empty body (serialization skipped).
- Stale `If-None-Match` -> `200` + fresh body.
- C1 projected request -> `200`, 532 B vs 5791 B (~91% smaller), keys `['columns','table_name']`, column fields `['column_name','filterable','verbose_name']`, no `metrics`. Confirms the projection column names are valid.

### Residual risks & expectation/UI gaps
- **A-G1 (scope of the ETag win):** the FE `cachedSupersetGet` promise-cache means in-session repeat detail calls never reach the network, so they never send `If-None-Match`. The 304 benefit therefore lands on full page reloads, new tabs, cross-user, and bare-`SupersetClient.get` callers — not on already-deduped in-session traffic. The per-call cost win comes from eager-loading (every real call) + projection (Track C). Net: load drops, but the ETag is not a silver bullet for the in-session dedup case.
- **A-R2 residual:** ETag is child-aware (column/metric `changed_on` folded in — unit-verified). It still won't change if metadata is mutated out-of-band without bumping any `changed_on` (e.g. a raw SQL UPDATE to the metadata DB — outside the trust boundary). `max-age=0` bounds exposure to a single revalidation.
- **A-G2:** `max-age=0` skips serialization on revalidation, not the request (a cheap 304 round-trip still happens, plus the eager find). A positive `max-age` would also cut requests but trade staleness; left conservative by design (D2).
- **A-G3:** eager-load query-count reduction is unit-tested at the wiring level (`options` passed) but not profiled against the live DB with SQL echo. Inferred, not measured.
- **D3 deferred:** `get_list` (`?q=` list) remains uncached server-side; its volume is addressed by Track B instead. Revisit if post-deploy logs still show list `?q=` dominating.
- **B-G1 (UI expectation):** the picker no longer refreshes on every window focus — only after returning from the Add-Dataset tab opened via its own link. A user who registers a dataset in an independently-opened tab won't see it until they reopen the picker. Intended tradeoff (D4(b)); reopening still reloads. No debounce fallback (D4(c)) was added since the flag guard is reliable.
- **B-R2:** memoized write-permission is session-stable; a mid-session role grant isn't reflected until reload (create POST still server-enforced).
- **C-G1:** the card's column options are dataset-static (the fetch never used filter state); removing the `dependencies` dep only deletes dead re-fires. If a future cascading requirement needs filter-aware columns it must be re-added deliberately. The dead `col.name` fallback is left in place (API returns `column_name`) to avoid scope creep.
- **FE-DEPLOY:** Tracks B/C are verified by unit tests only; not exercised in a live browser this session. A Playwright/manual pass (open picker -> alt-tab repeatedly -> confirm no `/dataset/?q=` storm; open a group-by dashboard -> confirm one projected `/dataset/<id>?q=` and no re-fetch on filter change) would close the empirical loop. Backend (A) is verified live.
