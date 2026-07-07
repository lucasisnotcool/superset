<!--
AI Lab consolidation — audit of the shipped agent surfaces + re-spec of a
first-class, workbench-style, all-in-one AI Lab.
Status: PROPOSED (awaiting DP-L1..L8 sign-off). Owner: AI Agent team.
Companions: plan_testing_platform_impl.md (as-built ledger of the testing
platform), evaluation/TESTING_PLATFORM_SPEC.md (Revision 2), MDL_LAB.md.
-->

# AI Lab — Audit & Re-spec (first-class workbench)

**Problem statement (stakeholder, 2026-07-03):** the pieces all exist and work,
but they are scattered — MDL Lab lives inside a SqlLab side panel, Prompts and
Usage are orphaned admin pages under Settings/Manage, and nothing presents the
*one workflow* they actually form. Consolidate into a single **AI Lab**:
first-class, workbench-style, fully integrated — *in addition to* the existing
surfaces (SqlLab chat stays; existing platforms are not removed).

---

## 1. Audit — what is shipped today (as-built inventory)

### 1.1 Entry points (the fragmentation, concretely)

| # | Surface | Route/host | Persona/gate | How you find it |
|---|---|---|---|---|
| E1 | **AI SQL chat agent** | SqlLab → AiAgentPanel (mounted in `SqlLab/components/AppLayout` + `TabbedSqlEditors`) | any user w/ DB access | side panel in SQL Lab |
| E2 | **MDL Lab** (SemanticLayerEditor) | inside E1 — tabs: Models (workspace tree + editor + detail panes: Copilot, Coverage, Documents, Changeset review, Provenance), Instructions, Golden queries, **Benchmarks**, Graph | project read/write (db-access-derived) | a mode of the SqlLab panel |
| E3 | **AI Agent Usage** | `/ai-agent/usage/` (`src/pages/AiAgentUsage/`) | Admin (FAB menu `Manage ▸`) | Settings menu |
| E4 | **AI Agent Prompts** | `/ai-agent/prompts/` (`src/pages/AiAgentPrompts/`) | Admin (FAB menu `Manage ▸`) | Settings menu |
| E5 | Recovery/scientist notifications | RecoveryBanner + events SSE — visible **only while the panel is open** | project users | invisible otherwise |

There is no home, no cross-navigation (E4 tells you to "measure with
benchmarks" but cannot link to E2; the benchmark handoff says "review in the
Copilot panel" with no link), and no URL-addressable state inside E1/E2 (you
cannot deep-link to "this run's comparison").

### 1.2 Backend inventory (one service, already unified)

- FastAPI service (`superset_ai_agent/app.py`, ~90 routes under `/agent/*`),
  behind the `/ai-agent` proxy; **the backend is already consolidated** — the
  fragmentation is purely frontend.
- 25 `ai_agent_*` tables (Alembic 0001–0020) incl. the testing platform
  (`ai_agent_eval_*`, 0019) and prompt registry
  (`ai_agent_prompt_versions/_labels`, 0020).
- Shared infra: `ThreadJobRunner` + `ai_agent_jobs`, durable events + SSE
  (`/events`, per-project `/projects/{id}/events`), `MeteredModelClient`
  telemetry, feature flags on `AgentConfig`.

### 1.3 Feature inventory → future Lab section (audit of ~30 shipped features)

| Feature (shipped) | Lives today | Persona | Lab section (target) |
|---|---|---|---|
| NL→SQL chat, conversations, execute/stream | E1 | user | **Playground** |
| Project resolve/pin, schema inference | E1/E2 | user | global project switcher |
| MDL workspace (files CRUD, activation, bulk status, validation) | E2 Models | curator | **Model Studio** |
| Copilot (stream, apply, changeset review, autopilot) | E2 Models pane | curator | Model Studio |
| Coverage (auto audits, badge, report modal, progress) | E2 Models pane | curator | Model Studio |
| Recovery agent (suggestions, dismiss, diff dialog) | E2 banner | curator | Model Studio + Activity |
| Schema graph | E2 Graph | curator | Model Studio |
| Documents (upload, extract, chunks, RAG, dedupe) | E2 Models pane | curator | **Knowledge** |
| Instructions (DB-tied, global + semantic recall) | E2 Instructions | user | Knowledge |
| Golden queries (promote, recall, onboarding flags) | E2 Golden queries | curator | Knowledge |
| NL→SQL memory pairs (DB-tied learning) | implicit | system | Knowledge (read view — gap A6b) |
| **Benchmarks** (items, dry-run, runs, trials/pass^k, compare+CI, matrix sweep, overrides, capability breakdown) | E2 Benchmarks | curator | **Benchmarks** |
| Scientist (analyze, taxonomy report, handoff→changeset, auto-analyze flag) | E2 run modal | curator | Benchmarks + Activity |
| OTel score export, CI gate module | API/lib only | dev | Benchmarks (export button — gap) |
| Prompt registry (versions, candidate→promote, diff, reset) | E4 | **Admin** | **Prompts** (admin-gated section) |
| LLM usage telemetry | E3 | **Admin** | **Telemetry** (admin-gated section) |
| Onboarding (auto/manual, table picker, enrichment) | E2 modals | curator | Model Studio |

### 1.4 The scoping model (authoritative answer to "who does a prompt edit affect?")

Every configurable layer, its storage, and its blast radius:

| Layer | Storage | Scope / blast radius | Editable by | Takes effect |
|---|---|---|---|---|
| **Prompt templates** (`text_to_sql.md`, `mdl_copilot.md`, judge/coverage prompts…) | `ai_agent_prompt_versions` + `_labels`; repo files as fallback | **GLOBAL — the entire agent deployment.** No owner_id, project_id, or database column exists on these tables; `get_prompt(name)` resolves the `production` label for *every* request, every user, every database, every project. | Admin only (`require_admin`, defense-in-depth on API + menu) | ≤5s per worker (TTL cache; promote/reset invalidate immediately on the serving worker; other workers within 5s). Saving a **candidate** changes nothing until promoted. Reset returns to the repo file. |
| Instructions | `ai_agent_instructions` (scope_hash keyed by **DB fingerprint**) | one physical database — all users sharing it | any user with that DB access | next agent turn |
| Golden queries | project `queries.json` MDL file | one **project** | project write | next turn (recall) |
| NL→SQL memory pairs | `ai_agent_nl_sql_examples` (DB fingerprint) | one physical database | learning loop (auto) | next turn |
| MDL files / semantic model | `ai_agent_semantic_mdl_files` | one project | project write | on activation |
| Benchmarks/runs | `ai_agent_eval_*` (project_id) | one project | project read/write | n/a (measurement) |
| Model choice per run | run config | one benchmark run | project write | that run only |

**So: an admin who edits AND promotes a prompt at `/ai-agent/prompts/` changes
the behavior of that prompt for ALL users, ALL databases, and ALL projects on
that deployment** (multi-worker deployments converge within the 5s TTL because
all workers read the same agent DB). This is by design — prompt templates are
system code, and the candidate→promote discipline plus per-version audit
(who/when/comment) treats them that way. The per-tenant/per-domain tuning
knobs are the *scoped* layers above (instructions, golden queries, MDL), which
non-admins already control. **Audit finding A7:** the UI does not state this
blast radius anywhere — an admin could reasonably assume "prompts for my
project." The Lab must label the Prompts section "Global — affects every user
and database on this deployment," and per-project prompt overrides are
explicitly a non-goal until a real need appears (DP-L7).

### 1.5 Audit findings (what's wrong, numbered)

- **A1 Discoverability.** The richest surface (MDL Lab + Benchmarks) is
  invisible unless you open SQL Lab and notice the side panel. No top-nav
  presence for a flagship capability.
- **A2 Broken workflow seams.** The actual loop — *Model → Ground → Test →
  Diagnose → Fix → Verify* — crosses E2-tabs, modals, and admin pages with no
  links across the seams (prompts↛benchmarks, handoff↛changeset review,
  analysis↛copilot).
- **A3 No URL state.** Nothing inside the panel is addressable: no shareable
  link to a run, a comparison, a changeset, a document. Kills collaboration
  and support ("send me the link").
- **A4 Notification fragmentation.** Recovery suggestions, benchmark
  completion, analysis-ready events are SSE-delivered but only render while
  the panel happens to be open; nothing aggregates jobs/events/reports.
- **A5 Persona split is a page split.** Admin (prompts/usage) vs curator
  (studio/benchmarks) surfaces are different apps, though the admin tuning
  loop *requires* the curator's benchmark evidence.
- **A6 Orphaned artifacts.** (a) `scientist` conversations are persisted but
  listed nowhere; (b) DB-tied memory pairs have no read/manage view; (c) OTel
  export and the CI gate have no UI affordance.
- **A7 Prompt blast radius unlabeled** (see 1.4).
- **A8 No prompt playground.** The prompts page edits blind — no "try this
  candidate against a question / a benchmark subset" before promoting.
- **A9 Space.** Benchmarks/compare/matrix inside a SqlLab splitter panel is
  cramped; the workbench work deserves full-page real estate.
- **A10 SqlLab coupling.** Components live under `src/SqlLab/components/…`,
  entangling reuse; the Lab must not require a SqlLab tab to exist.

Positive audit notes (keep, don't rebuild): backend already unified; authz
layering is correct and complete (project fingerprint authz, admin gates,
`SECURITY.md`-consistent); the component vocabulary (panels, changeset review,
event hooks) is reusable as-is; test coverage is strong (~131 BE + 31 FE
platform tests + full agent suite).

---

## 2. Re-spec — AI Lab as a first-class workbench

### 2.1 Product principles

1. **One loop, one place.** The Lab is organized around the flywheel:
   *Model → Ground (knowledge) → Test (benchmarks) → Diagnose (scientist) →
   Fix (copilot changeset) → Verify (re-run + CI'd compare)* — every section
   links forward and backward along this loop.
2. **Project-centric.** A global project switcher (database → project) scopes
   every section; global things (Prompts, Telemetry) are visibly labeled
   global.
3. **Additive, not a migration.** SqlLab keeps its chat panel (thin companion
   with "Open in AI Lab" deep links); existing admin URLs redirect. No
   backend semantic changes; `SECURITY.md` boundaries untouched.
4. **Role-adaptive, single app.** Curator sections always visible (subject to
   project authz); admin sections (Prompts, Telemetry) appear only for
   admins — one app, not two.
5. **Addressable everything.** Every entity gets a URL.

### 2.2 Information architecture

```
/ai-lab/                                  ← top-nav item "AI Lab" (+ FAB menu)
  /ai-lab/p/:projectId/overview           Overview: readiness, coverage badge,
                                          latest benchmark score + trend,
                                          activity feed, quick actions
  /ai-lab/p/:projectId/playground         Chat w/ pinned project; per-message
                                          "save as golden / add to benchmark";
                                          model picker (mirrors run sweeps)
  /ai-lab/p/:projectId/studio             Model Studio: workspace tree, file
                                          editor, schema graph, Copilot,
                                          coverage, changeset review,
                                          onboarding    [today: E2 Models+Graph]
  /ai-lab/p/:projectId/knowledge          Documents / Instructions / Golden
                                          queries / Memory pairs (read view)
  /ai-lab/p/:projectId/benchmarks         Items, runs (single/trials/matrix),
       /runs/:runId                       run detail (verdicts, previews,
       /runs/:runId/compare/:otherId      overrides, capability tags)
                                          + Scientist reports list + OTel export
  /ai-lab/prompts        (admin)          Prompt registry, GLOBAL banner,
                                          diff, candidate→promote, playground
                                          try-out (DP-L6)
  /ai-lab/telemetry      (admin)          LLM usage + eval spend view
```

Layout: slim left nav (sections) + header (project switcher, readiness chip,
notification bell) + content area + **right Activity drawer** (collapsible,
global): running jobs, recent events, recovery suggestions, scientist
reports — the single home for everything that today only flashes by on SSE.

### 2.3 Workflow threading (closing the A2 seams)

- Benchmark run detail → *Analyze failures* → report finding → **"Fix in
  Copilot"** deep link (`/studio?conversation=…`) opens the seeded changeset in
  the existing review panel; after apply, a **"Verify: re-run benchmark"**
  call-to-action returns to `/benchmarks` with the baseline pre-selected for
  comparison.
- Prompts: *Save candidate* → **"Measure it"** button starts a benchmark run
  (project + benchmark picker) and shows the comparison inline; *Promote*
  confirm-dialog displays the global-blast-radius warning + the latest
  measurement if one exists (still advisory — DP-L7).
- Playground answer → *Promote to golden* / *Add to benchmark* (both APIs
  exist) → Knowledge/Benchmarks respectively.
- Activity drawer items deep-link to their entity (run, changeset, report).

### 2.4 Component & code strategy (frontend-only consolidation)

- New `src/pages/AiLab/` shell (lazy route `/ai-lab/*` with nested routing) —
  mirrors the AiAgentUsage page pattern, not a new framework.
- **Move** `SqlLab/components/AiAgentPanel/*` → `src/features/aiLab/`
  (api.ts, SemanticLayerEditor panels, hooks), leaving thin re-exports at the
  old paths for one release so the SqlLab panel keeps working unchanged
  (DP-L2). Jest suites move with the components — they are the regression
  net for this refactor.
- SqlLab AiAgentPanel becomes: chat + mini status strip + "Open in AI Lab"
  links (keeps E1 muscle memory; drops the cramped full editor over time).
- `/ai-agent/usage/` and `/ai-agent/prompts/` routes render redirects to the
  Lab sections; their FAB menu links are replaced by one top-level **AI Lab**
  link (`initialization/__init__.py`), admin items shown by section gating
  instead of menu gating.

### 2.5 Backend additions (small; the backend is already a platform)

- **B1** `GET /agent/semantic-layer/projects/{id}/activity` — aggregated feed
  (recent events + active jobs + latest recovery/scientist artifacts) so the
  Activity drawer needs one call + the existing SSE.
- **B2** `GET /agent/conversations?kind=scientist&project_id=…` filter (list
  endpoint already exists; add kind/project filters) — closes A6a.
- **B3** Memory-pairs read/list endpoint (DB-scoped, read-only) — closes A6b.
- **B4** Prompt playground endpoint: `POST /agent/admin/prompts/{name}/try`
  — run one question through the agent with a **request-scoped prompt
  override** (no label change). Needs `get_prompt` to honor a per-request
  override map (contextvar) — the only semantics-adjacent change; candidate
  stays non-live (DP-L6).
- Nothing else: benchmarks, copilot, coverage, prompts, telemetry APIs are
  used as-is.

### 2.6 Decision points

| # | Decision | Recommendation |
|---|---|---|
| DP-L1 | Standalone `/ai-lab/` app vs expanding the SqlLab panel | **Standalone top-nav app**; SqlLab panel stays as companion (A1/A9/A10) |
| DP-L2 | Move components vs duplicate | **Move to `src/features/aiLab/` + temporary re-exports**; never fork |
| DP-L3 | Old admin routes | Redirect for one release, then remove menu links; keep API paths forever |
| DP-L4 | URL scheme | `/ai-lab/p/:projectId/:section/…` as in 2.2; project id in path (shareable), not local state |
| DP-L5 | Activity drawer scope | Project-scoped feed + global admin toasts; powered by B1 + existing SSE |
| DP-L6 | Prompt playground (A8) | Build B4 with request-scoped override (contextvar); candidate never leaks to other requests |
| DP-L7 | Per-project prompt overrides | **Defer.** Keep prompts global + labeled; scoped tuning stays in instructions/golden/MDL. Revisit only on a concrete multi-tenant need |
| DP-L8 | SqlLab editor removal | Keep full editor in the panel until Lab P2 ships, then reduce panel to chat+links (feature-flagged fallback) |

### 2.7 Phasing (each independently shippable; tests per item, per the working agreement)

| Phase | Work | Exit criteria |
|---|---|---|
| **L-P0 Shell** | `/ai-lab/` route + nav + project switcher + Overview (readiness/coverage/latest-run cards from existing APIs) + FAB menu link + redirects from E3/E4 | Lab reachable from top nav; jest tests for shell + overview; old URLs land in the right section |
| **L-P1 Move & mount** | Move components (DP-L2); mount Studio, Knowledge, Benchmarks, Prompts, Telemetry sections; URL deep links for runs/compare | All existing panel jest suites green from the new home; SqlLab panel unchanged via re-exports |
| **L-P2 Threading** | A2 seam links (fix-in-copilot, verify-rerun, measure-candidate); Activity drawer (B1, B2); scientist reports list; OTel export button | Loop walkable end-to-end without leaving the Lab |
| **L-P3 Polish** | Prompt playground (B4/DP-L6); memory view (B3); global-scope banners (A7); SqlLab panel slimming (DP-L8); Cypress/Playwright happy-path E2E | Gap log clean or explicitly accepted |

### 2.8 Risks & mitigations

| Risk | Mitigation |
|---|---|
| Component move breaks SqlLab panel | Re-exports + the existing 31+ jest tests run from both paths in CI during transition |
| Hidden SqlLab/Redux coupling in moved components | Audit hit-list: components use `useDispatch` (toasts) + props only — no SqlLab store reads found in the panels built this cycle; verify the older panels (Copilot/Coverage) during L-P1 |
| Route-level auth confusion | Lab page itself is unauthenticated-shell + per-API authz (same as today); admin sections hidden client-side AND admin-gated server-side (already true) |
| Two surfaces drift (panel vs Lab) | Single component source (DP-L2); panel is composition, not copy |
| Prompt playground leaks a candidate globally | B4 uses request-scoped override only; never touches labels; test asserts cross-request isolation |

---

**Status: PROPOSED.** Sign-off needed on DP-L1..L8, then an implementation
checklist (`plan_ai_lab_impl.md`) gets authored from §2.7 with per-item
touchpoints, following the fork working agreement.
