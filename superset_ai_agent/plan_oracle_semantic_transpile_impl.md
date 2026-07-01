# Implementation Plan — Oracle Semantic Mode via Dialect Finalization (sqlglot transpile)

Companion to [[wren-oracle-support]] and `plan_semantic_mode_badge_impl.md`. Sequential,
resumable checklist. Legend: `[ ]` todo · `[~]` wip · `[x]` done · `[!]` blocked.

---

## BUILD STATUS — all 6 phases implemented

- **Phase 1** `semantic_layer/engine/dialect_finalize.py` (`POST_TRANSPILE_DIALECTS`,
  `finalize_native_sql`, `finalization_guidance`, Oracle identifier upcasing) +
  `wren_dialect_finalize_enabled` config + 19 unit tests. ✅
- **Phase 2** finalize wired into `plan_semantic_sql_step`; `PlanStepResult` gains
  `finalized_dialect` + `canonical_native_sql`; config threaded through both graphs. ✅
- **Phase 3** `oracle` added to `BACKEND_TO_WREN_DIALECT`; LLM guidance addendum via
  `_compose_semantic_guidance` in both graphs; degrade-warning → `engine_warnings`. ✅
- **Phase 4** badge disclosure: `dialect_supported` detail + `SemanticModeStatus
  .dialect_finalized_by` + FE type + popover note + jest. ✅
- **Phase 5 (upgraded from "verify" to a real fix)** — empirically found the ORA-00904
  root cause: SQLAlchemy's Oracle dialect reflects stored `ID` as lowercase `id`
  (`normalize_name('ID')=='id'`), so the MDL/wren-core output is lowercase-quoted and
  fails on Oracle. Implemented an Oracle-only uppercase-fold of lowercase quoted
  identifiers (`"id"`→`"ID"`); reserved words stay quoted, genuine mixed-case preserved. ✅
- **Phase 6** end-to-end graph test proves Oracle execution receives
  `FETCH FIRST … ROWS ONLY` + `"ID"`; kill-switch test; full agent suite 1212 passed
  (1 pre-existing unrelated failure — `test_multi_schema_schema_index`, fails on clean
  master too); ruff + prettier clean. ✅

**Also shipped (D-2):** `mssql` routed through finalize — fixes its identical latent
`LIMIT` bug (T-SQL emits `TOP`; no identifier upcasing, correctly).

Residual gaps → §13.

---

## 0. Goal & deliverables
Enable **semantic mode on Oracle** (19c) at parity with Postgres/wren-supported DBs, by adding
the SQL-dialect finalization stage that Wren's own pipeline has and our embedded integration
skipped. Specifically:
1. **D1 — Semantic rewrite runs on Oracle** and produces executable Oracle SQL.
2. **D2 — Truthful UI:** the semantic badge flips to "Semantic" for Oracle *and* discloses that
   a transpile step is in use (not a silent behavior).
3. **D3 — LLM is informed:** the agent knows its semantic SQL is transpiled to Oracle, and any
   transpile degradation/failure is communicated back through the existing repair/reflection loop.
4. **D4 — General seam:** the mechanism is gated per-backend (`oracle` today) with a clean map
   for future non-wren-native dialects — no Oracle-specific branching scattered through the code.

**Non-goal:** changing how wren-core rewrites; MDL authoring tooling (covered as a dependency,
Phase 5). This adds a *finalization* stage; it does not touch stage-1 rewrite.

---

## 1. Verified findings (empirical — the plan rests on these)
Probed against installed `wren-core-py` 0.7.x + `sqlglot`:
- wren-core with `data_source="oracle"` emits canonical DataFusion-ish SQL: quoted identifiers
  **matching the MDL's authored case**, `LIMIT n`, `count(1)`, `NULLS LAST`, `CAST(:p AS TIMESTAMP)`.
  It does **not** convert `LIMIT`→`FETCH FIRST` (that's the stage we add).
- `sqlglot.transpile(wren_out, read="postgres", write="oracle")` converts `LIMIT 100`
  → `FETCH FIRST 100 ROWS ONLY`, drops `AS` from table aliases, preserves binds/`NULLS LAST`
  → **valid Oracle SQL**. `read` dialect (postgres/trino/duckdb) gives identical output; use
  `postgres` (closest to DataFusion output).
- **Identifier case is decisive:** with columns authored UPPERCASE (Oracle's stored case),
  output `"AMOUNT"` matches `AMOUNT` → no ORA-00904. Lowercase authoring → `"amount"` → ORA-00904.
  → Phase 5 (authoring case) is a hard correctness dependency, not optional.
- Warning→LLM path confirmed: `PlanStepResult.warnings` → `engine_warnings` (graph.py:792,
  conversation_graph.py:1253) → folded into repair prompt (graph.py:861, conversation_graph.py:1392)
  → reflection observations. `semantic_sql_instructions` (graph.py:1084) injects guidance.

---

## 2. Design

### 2.1 The finalization seam (one shared place, both graphs)
Add a **dialect-finalization** call inside `plan_semantic_sql_step`
([planning.py:88](superset_ai_agent/semantic_layer/engine/planning.py#L88)), right after
`engine.plan_sql(...)`. Both graphs route through this function, so neither can drift.

```
engine.plan_sql → planned.native_sql (canonical) → finalize_native_sql(native_sql, backend)
    → executable dialect SQL (+ any transpile warnings)
```

### 2.2 New module — `semantic_layer/engine/dialect_finalize.py`
```python
# Superset backend -> sqlglot write-dialect. THE per-dialect seam: add a backend
# here to route its wren-core output through a finalizing transpile. wren-native
# dialects (postgres, bigquery, ...) are absent → no-op.
POST_TRANSPILE_DIALECTS: dict[str, str] = {"oracle": "oracle"}
# Assumed shape of wren-core's output (DataFusion ≈ postgres; not a native sqlglot dialect).
WREN_OUTPUT_READ_DIALECT = "postgres"

class FinalizeResult(BaseModel):
    sql: str
    target_dialect: str | None   # set when a transpile was applied
    transpiled: bool
    warnings: list[str]          # non-correctable (a re-draft can't fix a transpile gap)

def finalize_native_sql(native_sql: str, *, backend: str | None,
                        enabled: bool = True) -> FinalizeResult: ...
```
Behavior:
- Backend not in map (or `enabled` False, or empty SQL) → `transpiled=False`, sql unchanged.
- In map → `sqlglot.transpile(native_sql, read=WREN_OUTPUT_READ_DIALECT, write=target)[0]`.
- **Degrade closed:** on any sqlglot error, return the ORIGINAL sql + a warning
  `"Could not finalize SQL for <target>: <err>; running un-transpiled."` (non-correctable).

### 2.3 Enabling Oracle
Add `"oracle": "oracle"` to `BACKEND_TO_WREN_DIALECT`
([base.py:47](superset_ai_agent/semantic_layer/engine/base.py#L47)). This simultaneously:
(a) makes `resolve_dialect("oracle")` non-None → wren-core receives `data_source="oracle"`
(OracleDialect identifier quoting); (b) flips the badge factor `dialect_supported` → met.
**Must ship in the same change as the finalize step** — otherwise the badge (and guidance) would
claim Oracle support while emitting `LIMIT` → the exact false-green we built the badge to prevent.

---

## 3. Sequential phases

### Phase 1 — `dialect_finalize` module + unit tests  ·  Blocks: all
- [ ] **1.1** Create `semantic_layer/engine/dialect_finalize.py` (ASF header) with the map,
  `FinalizeResult`, and `finalize_native_sql` (§2.2). Export from `engine/__init__.py`.
- [ ] **1.2** Add optional kill-switch config `wren_dialect_finalize_enabled: bool = True`
  ([config.py](superset_ai_agent/config.py), near `wren_engine`) + `WREN_DIALECT_FINALIZE_ENABLED`
  env. (Decision D-1: map-only vs +flag — recommend +flag for safe rollback.)
- [ ] **1.3** Unit tests (`tests/unit_tests/superset_ai_agent/test_dialect_finalize.py`):
  Oracle LIMIT→FETCH FIRST; non-mapped backend (postgres) is a no-op; malformed SQL degrades
  with a warning + original SQL; empty SQL no-op; disabled flag no-op.
  **Acceptance:** the realistic query from §1 transpiles to `FETCH FIRST ... ROWS ONLY`.

### Phase 2 — wire finalization into the shared planning step  ·  Depends: 1
- [ ] **2.1** In `plan_semantic_sql_step` ([planning.py:88](superset_ai_agent/semantic_layer/engine/planning.py#L88)),
  after `planned = engine.plan_sql(...)`: call `finalize_native_sql(planned.native_sql,
  backend=getattr(context.database,"backend",None), enabled=config...)`. Use its `.sql` as
  `native_sql`, append `.warnings` to `warnings` (NOT `correctable_warnings` — transpile gaps
  aren't re-draftable). Thread the config flag in (add a param; both graphs pass `self.config`).
- [ ] **2.2** Record provenance: `PlanStepResult` gains `finalized_dialect: str | None` and keeps
  the pre-finalize SQL available for audit (extend `with_engine_provenance` at planning.py:119 to
  stamp both). Lets debugging see canonical vs Oracle SQL.
- [ ] **2.3** Test: `plan_semantic_sql_step` with a fake engine returning `LIMIT` SQL + an Oracle
  `context.database.backend` yields finalized `FETCH FIRST` native_sql. **Acceptance:** both graphs'
  existing semantic tests still pass (`test_graph_semantic_engine.py`).

### Phase 3 — enable Oracle + LLM communication  ·  Depends: 2
- [ ] **3.1** Add `"oracle": "oracle"` to `BACKEND_TO_WREN_DIALECT` (base.py:47). Re-verify no
  other `resolve_dialect` consumer breaks (grep: planning.py, mode.py only).
- [ ] **3.2** **LLM awareness (D3):** extend the semantic guidance so the agent knows the target.
  Add a per-dialect addendum appended to `_SEMANTIC_SQL_GUIDANCE` when the backend is finalized:
  *"Your semantic SQL is rewritten by the engine and then transpiled to native <Oracle> SQL.
  Prefer ANSI-standard SQL and semantic-layer metrics; avoid engine-specific functions that may
  not transpile."* Inject via the existing `semantic_sql_instructions` payload key
  (graph.py:1084, conversation_graph.py:1750) — see §4.
- [ ] **3.3** **LLM failure feedback (D3):** confirm finalize warnings flow to the repair prompt &
  reflection. They already will (Phase 2.1 → `engine_warnings` → graph.py:861 /
  conversation_graph.py:1392). Add a test asserting a transpile-degrade warning reaches
  `engine_warnings`. **Acceptance:** an execution failure on transpiled SQL surfaces with the
  transpile provenance in the observation the LLM sees.

### Phase 4 — badge communication (D2)  ·  Depends: 3
- [ ] **4.1** Backend: `evaluate_semantic_factors`
  ([mode.py](superset_ai_agent/semantic_layer/engine/mode.py)) — when the backend is in
  `POST_TRANSPILE_DIALECTS`, set factor `dialect_supported.detail` to *"Supported via
  transpilation to <Oracle> SQL."* and add a status field `dialect_finalized_by: str | None`
  to `SemanticModeStatus` (schemas.py). Pass the backend into `evaluate_semantic_factors`
  (already receives it) and compute the note there.
- [ ] **4.2** Endpoint already returns the enriched status (no route change).
- [ ] **4.3** FE: `SemanticModeStatus` type gains `dialect_finalized_by?: string | null`
  ([api.ts](superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts)). In
  `SemanticModeBadge.tsx`, when present and mode is semantic, render a subtle note in the popover
  subhead: *"Native SQL is transpiled to Oracle."* (reuses the existing Subhead/factor styling —
  no new component). **Acceptance:** on Oracle the badge shows green "Semantic" and the popover
  discloses the transpile.
- [ ] **4.4** Jest: badge shows the transpile note when `dialect_finalized_by` is set.

### Phase 5 — MDL authoring case dependency (correctness gate)  ·  Parallel, but gates real use
- [ ] **5.1** Verify onboarding authors Oracle model columns in **physical stored case**. The
  Copilot's `get_physical_schema` returns real (uppercase) names; if onboarding/`propose_onboard_*`
  copies them verbatim, models are correct by construction. Confirm with an Oracle project.
- [ ] **5.2** If manual/legacy MDL has lowercase columns for an Oracle DB, they will ORA-00904.
  Document the rule (Oracle MDL columns must match physical case) in the Copilot prompt /
  onboarding docs. (This is the root of the original incident — [[wren-oracle-support]].)
- [ ] **5.3** Optional hardening: a validation warning when an Oracle project has model columns
  whose case differs from `get_physical_schema`. Defer unless 5.1 shows drift.

### Phase 6 — end-to-end + docs
- [ ] **6.1** Integration-style test: semantic query on an Oracle-backed `AgentContext` produces
  `FETCH FIRST` native SQL and semantic-mode guidance. Also assert the **mssql** latent-LIMIT
  fix (add `"mssql"` to `POST_TRANSPILE_DIALECTS`? — see D-2).
- [ ] **6.2** `pre-commit run --all-files`; update `wren_full.md`/ARCHITECTURE notes: document the
  finalization stage + the per-dialect map seam.

---

## 4. LLM communication design (D3) — detail
Two channels, both reusing existing infra:
1. **Awareness (proactive):** a dialect addendum in `semantic_sql_instructions`. The agent already
   writes model-qualified semantic SQL in semantic mode; the addendum tells it the *target* is
   Oracle and to prefer portable SQL/metrics. This shapes generation to reduce non-transpilable
   constructs (e.g. it won't lean on Postgres-only functions).
2. **Failure/degradation (reactive):** `finalize_native_sql` warnings are **non-correctable**
   engine warnings. They ride the existing path into (a) the repair prompt
   (`errors = [*validation.errors, *engine_warnings, ...]`) and (b) reflection's `sql_observations`.
   So when transpiled SQL fails to execute, the LLM sees "this was transpiled to Oracle; <error>"
   and can adjust (simplify, drop the offending function) or the reflection loop can `clarify`.
   Because they're *non-correctable*, they never trigger the hallucination re-draft loop (which
   can't fix a transpiler gap) — correct routing.

Design rule: **transpile provenance must be attached to the observation**, so the agent never
sees a bare Oracle error divorced from "your semantic SQL was transpiled." Include the target
dialect in the warning string.

## 5. Badge communication design (D2) — detail
- Factor `dialect_supported` stays **met** (semantic mode genuinely works) but its `detail`
  changes to disclose the mechanism: *"Supported via transpilation to Oracle SQL."*
- New `SemanticModeStatus.dialect_finalized_by` (e.g. `"oracle"`) → badge popover subhead adds one
  line. Keeps the *essential* state (green "Semantic") truthful while disclosing the how — matching
  the truthfulness principle the badge was built on. No amber (this is a supported path, not a
  blocker).

## 6. Entry points & touchpoints (quick map)
- **New:** `semantic_layer/engine/dialect_finalize.py` (map + `finalize_native_sql`)
- `semantic_layer/engine/planning.py:88` — finalization call site (the seam)
- `semantic_layer/engine/base.py:47` — `BACKEND_TO_WREN_DIALECT` += oracle
- `semantic_layer/engine/mode.py` + `semantic_layer/schemas.py` — badge factor detail + `dialect_finalized_by`
- `graph.py:106` / `conversation_graph.py:120` — `_SEMANTIC_SQL_GUIDANCE` addendum
- `graph.py:1084` / `conversation_graph.py:1750` — `semantic_sql_instructions` injection
- `graph.py:792,861` / `conversation_graph.py:1253,1392` — engine_warnings storage + repair-prompt fold
- `config.py` — `wren_dialect_finalize_enabled` kill switch
- FE: `AiAgentPanel/api.ts` (type), `SemanticModeBadge.tsx` (popover note)

## 7. Risks & mitigations
| # | Risk | Mitigation |
|---|---|---|
| R1 | Transpile fails / mangles a query → broken SQL | Degrade closed: on error return original + non-correctable warning; per-query try/except. |
| R2 | **False-green if map ships without finalize** | Ship base.py map entry + finalize in ONE change; Phase 1→3 ordering enforces it. |
| R3 | Identifier case (lowercase MDL) still ORA-00904 | Phase 5 authoring-case gate; onboarding uses physical (uppercase) names; optional case-drift validation. |
| R4 | `read="postgres"` mis-parses some DataFusion output | Verified on realistic query; degrade-closed on parse error; `read` is a one-line constant to tune. |
| R5 | Boolean literals / pre-23c constructs (`= TRUE`) | Known gap; surfaced as an execution error → LLM/reflection; document in the guidance addendum. |
| R6 | Per-query transpile latency | sqlglot parse of already-small rewritten SQL is cheap; only for mapped backends; measured if needed. |
| R7 | Double-finalization or non-idempotence | Only applied once, in the single shared planning step; passthrough/postgres skip. |

## 8. Decision points
| ID | Decision | Recommendation |
|---|---|---|
| D-1 | Map-only gate vs +config kill-switch | **+`wren_dialect_finalize_enabled` flag** (default on) for safe rollback. |
| D-2 | Also route `mssql` through finalize now (latent LIMIT bug) | **Yes** — add `"mssql":"tsql"` to the map + a test; it's the same fix and mssql is already "supported" but broken. Ship together. |
| D-3 | `read` dialect for wren-core output | **`postgres`** (verified); keep as a named constant to tune. |
| D-4 | Store pre-transpile SQL in audit | **Yes** — cheap, invaluable for debugging Oracle issues. |
| D-5 | Case-drift validation (Phase 5.3) | **Defer** until 5.1 shows onboarding produces lowercase; add only if needed. |

## 9. Future seams
- **Add a backend:** one line in `POST_TRANSPILE_DIALECTS` (+ ensure it's in `BACKEND_TO_WREN_DIALECT`).
  Candidates: any dialect wren-core's inner-dialect renders incompletely.
- **Swap the finalizer:** `finalize_native_sql` is the single chokepoint — could later call a
  richer transpiler or WrenAI's `read="wren"` sqlglot dialect if vendored.
- **Per-dialect read override:** if some backend needs a different `read`, widen the map value to
  `{write, read}`.

## 13. Post-build gaps (expectation ↔ implementation) — honest notes
1. **Quoted-lowercase Oracle schemas (rare) will break.** The uppercase-fold assumes
   Oracle's *standard* uppercase storage (columns created unquoted). A table deliberately
   created with quoted-lowercase columns (`"id"`) is stored lowercase, yet SQLAlchemy
   reflects it lowercase too — **indistinguishable from a normalized uppercase column at the
   metadata level.** We uppercase it → `"ID"` → ORA-00904 for that (anti-pattern) schema.
   No metadata-only fix exists; a future `wren_oracle_fold_identifiers` opt-out is the seam.
2. **Boolean / pre-23c constructs.** wren-core may emit `= TRUE`; Oracle (pre-23ai) has no
   BOOLEAN. Not rewritten by sqlglot → surfaces as an execution error to the LLM/reflection
   (the guidance addendum tells the agent to prefer portable SQL). Rare in 19c schemas.
3. **`read="postgres"` is an approximation** of wren-core's DataFusion output. Verified on
   realistic queries; a construct that mis-parses degrades closed (original SQL + warning).
   One-line constant to tune (`WREN_OUTPUT_READ_DIALECT`).
4. **Result-set column headers are uppercased** on Oracle (`AS "n"` → `AS "N"`). Cosmetic and
   matches Oracle convention; the agent reads results by the returned names so it's transparent.
5. **Live-env verification still owed.** All evidence is from probing `wren-core-py` 0.7.x +
   sqlglot locally; the end-to-end test uses a fake engine/executor. A real Oracle-19c run
   (Thick mode) should confirm a semantic query round-trips — that's the one thing the unit
   tests can't cover.
6. **Identifier fold is Oracle-only by design.** Snowflake also folds uppercase but isn't in
   `POST_TRANSPILE_DIALECTS`; if added later, it must join `UPPERCASE_FOLD_DIALECTS`.

## 10. Definition of done
- [ ] Oracle: semantic query → `FETCH FIRST` native SQL, executes on 19c; badge green "Semantic"
  with the transpile disclosure; guidance addendum present; transpile-degrade warning reaches the LLM.
- [ ] mssql latent LIMIT bug fixed (D-2).
- [ ] Non-mapped backends unchanged (no-op); both graphs' semantic tests green.
- [ ] Unit + wiring + badge tests pass; `pre-commit` clean; `wren_full.md`/ARCHITECTURE updated.
