<!--
Licensed to the Apache Software Foundation (ASF) under one or more
contributor license agreements.  See the NOTICE file distributed with
this work for additional information regarding copyright ownership.
The ASF licenses this file to You under the Apache License, Version 2.0
(the "License"); you may not use this file except in compliance with
the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Implementation plan — onboarding UI gating & user flow

**Status:** ✅ implemented. Companion: `plan_copilot_parity_spec.md`
(File 2, not yet built). Authoritative as-built record is `wren_mdl_copilot.md` §AB.
See **§9 As-built notes** for deviations from this plan.

## 0. Goal (one sentence)

On an **empty** semantic layer, the MDL Copilot is UI-blocked behind explicit
**onboarding** (the required first step), onboarding is shown honestly as a
**separate one-shot process** (not as Copilot chat), and **reset no longer
auto-onboards** — first-open and post-reset converge on one "empty → Onboard"
path, with nothing else (no Copilot turn, no other Wren skill/agent activity)
running before a successfully onboarded MDL is ready.

## 1. Decisions locked in this conversation

1. **Onboarding is a single-shot process, shown as a separate process — not chat.**
   Verified (see §2): onboarding is a single structured-output call using
   `prompts/wren_onboarding.md`, deterministic structure + LLM semantic overlay,
   written + auto-activated directly. It is **not** the Copilot agent and shares
   **no** session with it. Therefore the rail must render onboarding as a distinct
   bootstrap-progress view, never as synthetic chat bubbles ("show the truth").
2. **Remove auto-onboard.** First open lands on the empty/Onboard state, not an
   automatic job.
3. **Reset = delete-only.** Drop the auto re-onboard so reset returns to the same
   empty/Onboard state; onboarding is always an explicit click.
4. **Gate coverage/inspector by UI-hide, not a backend gate** (lowest perf cost).
   The Copilot panel already hosts these; disable/hide them until ready. No new
   backend check on `/copilot/coverage`.
5. **Contract:** onboarding is always first on an empty layer; the backend 409 on
   `/copilot` and `/copilot/stream` remains the hard guarantee that no Copilot turn
   runs before ready.
6. **Transcript survival across empty↔ready within a session** is handled here by
   keeping the panel mounted; **durable persistence + "start new chat" threads are
   File 2's scope** (do not duplicate that work here).

## 2. Current behavior (source-backed — what we are changing)

### Onboarding is a single-shot run, NOT the Copilot
- `onboard_schema_project` → one `wren_client.generate_base_model(...)` call, then
  write + auto-activate each model: `semantic_layer/onboarding.py:82-137`.
- Default `wren_adapter="llm"` (`config.py:108`) → `LlmWrenClient.generate_base_model`
  makes **one** `_call_model("wren_onboarding", payload)`
  (`integrations/wren/llm_client.py:440`), a single structured `chat(..., format_schema=...)`
  (`llm_client.py:580-594`). Structure is deterministic (`model_from_dataset`);
  the model only overlays semantics. `FileWrenClient` (no model client / non-llm
  adapter) is fully deterministic.
- This path uses `prompts/wren_onboarding.md`; the Copilot uses
  `prompts/mdl_copilot.md` + skills + tools (`semantic_layer/copilot/loop.py`).
  Different prompt, no tools, no changeset, **no conversation/session**.

### First open auto-onboards (to be removed)
- `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx:626-637`
  — effect fires `runOnboard` when `mdlFiles.length === 0 && canWrite`.

### Rail gating is a 2-state local boolean (to be upgraded to 4 states)
- `copilotReady = hasActiveModels && !isOnboarding` — `index.tsx:603-604`.
- Placeholder (spinner vs "Onboard schema" button) — `index.tsx:897-948`.
- `CopilotPanel` is only mounted when ready (`index.tsx:898`), so its transcript
  (local state, `CopilotPanel.tsx:83`) is **destroyed** when the rail falls back
  to the gate (e.g. after reset).

### Reset = delete + auto-onboard (to become delete-only)
- `reset_semantic_project` deletes every MDL file then `_start_onboarding_job(...)`
  — `superset_ai_agent/app.py:1881-1905` (delete at `:1904`, onboard at `:1905`).

### Readiness signal already exists (reuse it)
- `SemanticProjectReadiness{status: empty|indexing|ready|failed, ...}` +
  `GET /agent/semantic-layer/projects/{id}/readiness` — `app.py get_project_readiness`,
  schema in `semantic_layer/schemas.py`. Derived from active files + in-flight
  onboarding jobs via `JobStore.list_for_project`; **no migration**.
- `_require_project_ready` → 409 wired on `run_project_copilot` (`app.py:1590`) and
  `stream_project_copilot` (`app.py:1628`) only. Coverage (`app.py:1519`),
  inspector (`:1478`), workspace (`:1427`), apply (`:1699`) are not gated (by design).

## 3. Target user flow

The rail has **two visual modes**: a **bootstrap-process view** (empty / indexing
/ failed) and the **Copilot chat** (ready). Driven by `readiness.status`.

| `readiness.status` | Mode | What the user sees | Action |
|---|---|---|---|
| **empty** (first open *or* post-reset) | bootstrap | Help text + **Onboard** button; no chat composer | click Onboard → POST `/onboard` |
| **indexing** (job running) | bootstrap | Spinner + "Onboarding…" progress; result summary/warnings appended on finish | none (auto-advances) |
| **ready** | chat | Full Copilot chat (composer enabled) | normal turns |
| **failed** (last onboarding failed, no active MDL) | bootstrap | Error + **Retry onboarding** button | click Retry → POST `/onboard` |

Transitions: `empty → [Onboard] → indexing → ready`; `failed → [Retry] → indexing`.
A Copilot turn is impossible outside **ready** (backend 409 is the hard gate).

### Help text (concrete strings)
- **empty:** "The MDL Copilot turns on after onboarding. Onboarding reads this
  schema's permission-filtered tables and builds the base semantic layer — the
  required first step. Nothing else runs until it's ready."  · button: "Onboard this schema"
- **indexing:** "Onboarding — building the base semantic layer from your schema.
  This is a one-time setup step, separate from the Copilot chat. The Copilot opens
  automatically when it's ready."
- **failed:** "Onboarding didn't finish: {error}. Check this schema's access and
  try again."  · button: "Retry onboarding"

## 4. Change list (no code here — precise targets)

### Backend
- **B1.** `reset_semantic_project` (`app.py:1881-1905`) → **delete-only**: remove the
  `_start_onboarding_job(...)` call and the now-unneeded pre-delete context fetch;
  return 200 (no job). Update `runReset`/`resetSemanticProject` client + the FE
  reset handler accordingly.
- **B2.** *(none for coverage)* — UI-hide per decision 4. Leave `/copilot/coverage`
  ungated. (If the contract is later tightened, add `_require_project_ready` there.)

### Frontend (`SemanticLayerEditor/`)
- **F1.** Remove the auto-onboard effect (`index.tsx:626-637`) and the
  `onboardedProjectsRef` guard if it becomes unused.
- **F2.** Drive the rail off the **four** readiness states (not the 2-state
  boolean). Source: `getProjectReadiness` (already in `api.ts`) **or** extend the
  local derivation to include `failed` (no active models + last onboarding job
  failed). Prefer the endpoint for the `failed`/`indexing` truth; keep a local
  fast-path for `ready`.
- **F3.** Keep `CopilotPanel` **mounted** in all states; move the gate UI (help
  text, Onboard/Retry button, spinner, bootstrap progress) into the panel's
  composer region, gated by `readiness`. Pass `readiness` + `onOnboard` as props.
  (This preserves the transcript across empty↔ready within a session; durable
  persistence is File 2.)
- **F4.** **Disable/hide** the coverage + inspector header buttons in `CopilotPanel`
  when `readiness.status !== 'ready'`.
- **F5.** `resetProject` (`index.tsx:549`) → call delete-only reset, then
  `refresh()`; the rail returns to **empty** with the transcript intact.

## 5. Tests

### Backend
- `reset` deletes all MDL and **does not** create an onboarding job; readiness after
  reset is `empty`; a subsequent `/onboard` produces active models → `ready`.
- Existing readiness + 409 tests stay green (`test_copilot_api.py`).

### Frontend (RTL, `SemanticLayerEditor/index.test.tsx`)
- First open with empty MDL → shows **Onboard** button, **no** auto-onboard POST,
  no chat composer.
- `indexing` → progress view (spinner), composer hidden.
- `ready` → chat composer present; `copilot-not-ready` absent.
- `failed` → Retry button + error text.
- After reset → returns to **empty**/Onboard with transcript preserved; reset issues
  **no** onboard POST.
- Coverage + inspector buttons disabled/absent until `ready`.

## 6. Risks / edge cases
- **Readiness source drift:** if F2 uses the endpoint, ensure the rail refetches
  readiness after onboarding completes and after reset (tie to `refresh()`).
- **Concurrent onboard clicks:** disable the Onboard button while `indexing`;
  backend `_start_onboarding_job` + per-file `MdlFileExistsError` already degrade
  safely.
- **Keep-mounted vs File 2:** F3 keeps the panel mounted so the transcript survives
  within a session. Page reload still loses it until File 2 lands — call this out
  in the UI copy or accept it as a known interim limitation.
- **`FileWrenClient` path:** when no model client is configured, onboarding is fully
  deterministic and very fast; the `indexing` view may flash briefly — fine.

## 7. Out of scope (→ File 2: `plan_copilot_parity_spec.md`)
- Durable Copilot conversation persistence (survives reload / cross-device).
- Multi-turn Copilot memory (history fed into `run_copilot_loop`).
- "Start new chat" as a real thread (here it is only an in-session transcript).
- Thread list / resume / rename / delete.

## 8. Sequencing
Do **File 1 first** (it establishes the always-mounted, readiness-driven rail and
the bootstrap-vs-chat split). File 2 then swaps the in-session transcript for a
persisted thread and adds multi-turn + "start new chat" on top of the same panel.

## 9. As-built notes (deviations from the plan above)

Implemented as described, with these concrete decisions the plan deferred:

- **Reset response shape (B1).** Originally proposed as `204 No Content`; shipped
  as **`200` with `{"deleted": <count>}`** instead. Reason: the FE `requestJson`
  helper calls `response.json()` unconditionally (a 204 empty body would throw),
  and the existing delete convention (`delete_mdl_file` → `{"deleted": true}`)
  already returns a small JSON body. `reset_semantic_project` is now a plain
  delete loop returning the count; the `_onboarding_context` pre-fetch and
  `_start_onboarding_job` call were removed. `_onboarding_context` is retained —
  still used by the `/onboard` route.
- **Reset client (no poll).** `runReset` no longer wraps `pollSemanticJob`; it is
  now `resetSemanticProject(projectId) → Promise<{deleted:number}>`. `runOnboarding`
  still polls (unchanged).
- **Bootstrap lives inside `CopilotPanel` (F3).** The gate UI moved from
  `index.tsx`'s `CopilotRail` into `CopilotPanel` itself, which now takes
  `readinessStatus` + `readinessDetail` + `onOnboard` props and renders the
  bootstrap view (replacing **both** the transcript area and the composer) when
  `status !== 'ready'`. The panel is always mounted under `showCopilot && project`,
  so the in-session transcript survives empty↔ready transitions. The
  `copilot-not-ready` testid + a new `copilot-onboard` testid live in the panel.
- **`failed` requires the endpoint (F2/G5).** The local "no active models + last
  job failed" fallback was dropped — `failed` is derivable only from the backend
  job history. The rail reads `getProjectReadiness` (added to `refresh()`'s
  `Promise.all`, tolerant of failure) for `empty`/`ready`/`failed`, and uses the
  local `isOnboarding` boolean for the `indexing` window (a foreground onboard
  blocks until terminal, so readiness is not polled during it). A local
  `hasActiveModels` fast-path covers `ready` while readiness is still loading.
- **Copy the plan didn't enumerate.** The reset **confirmation modal** body and
  **success toast** were rewritten (they previously promised an auto-rebuild); the
  browser-pane **Reset button** label was decoupled from `isOnboarding` so a
  rail-initiated onboard no longer mislabels it "Resetting…".

### Files changed
- `superset_ai_agent/app.py` — `reset_semantic_project` delete-only.
- `superset-frontend/.../AiAgentPanel/api.ts` — `resetSemanticProject`/`runReset`.
- `superset-frontend/.../SemanticLayerEditor/index.tsx` — F1, F2, F5 + copy.
- `superset-frontend/.../SemanticLayerEditor/CopilotPanel.tsx` — F3, F4.

### Tests
- `tests/unit_tests/superset_ai_agent/test_copilot_api.py` — `test_reset_deletes_all_mdl_and_does_not_reonboard`, `test_reset_on_empty_project_is_a_noop`.
- `tests/unit_tests/superset_ai_agent/test_semantic_layer_api.py` — rewrote
  `test_reset_deletes_all_mdl_then_reonboards` → `…_and_does_not_reonboard`.
- `…/SemanticLayerEditor/CopilotPanel.test.tsx` — empty/indexing/failed bootstrap
  states + props on existing renders.
- `…/SemanticLayerEditor/index.test.tsx` — no-auto-onboard, click-to-onboard,
  delete-only reset; readiness route mocked.
- Result: backend 559 passed / 6 skipped; FE editor suites 15/15; `tsc` clean;
  prettier clean. (`oxlint` native binary missing in this environment — not run.)

### Remaining risks / expectation gaps
- **Page reload still loses the transcript** — keep-mounted only survives
  empty↔ready *within a session*. Durable persistence is **File 2**. Until then,
  a reload after edits drops the chat history (no data loss — drafts are persisted
  server-side; only the conversation transcript is ephemeral).
- **`apply` route stays ungated** (decision 4 = UI-hide). It is unreachable before
  `ready` in practice (a changeset only exists after a 409-gated turn), so the
  "nothing runs before ready" contract holds; revisit if `apply` ever becomes
  reachable independently.
- **Readiness fetch is best-effort** — if `GET /readiness` fails, the rail falls
  back to the local `hasActiveModels` heuristic, which cannot show `failed`
  (degrades to `empty`/`ready`). Acceptable: the backend 409 remains the hard gate
  regardless of what the rail renders.
- **`FileWrenClient`/deterministic onboarding** completes near-instantly, so the
  `indexing` view may flash briefly — cosmetic only.
