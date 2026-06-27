<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Feature Spec: Onboarding as a First-Class Background Task

Status: **proposed** · Author: AI Agent (with codebase audit) · Date: 2026-06-28

## 0. TL;DR

Onboarding is **already backgrounded** at the protocol level: the endpoint
returns immediately (`202`-style, job submitted to a daemon-thread runner), the
job state lives in a store, the editor polls it, and a reload re-discovers an
in-flight job via `readiness.running_job_id`. So this is **not** a "make it
async" project — the async plumbing exists. It is a **"close the gaps that still
make it feel and behave like a foreground wait"** project.

The five real gaps:

1. **Perception** — the Copilot rail renders a blocking "indexing" bootstrap; the
   user reads it as "I must wait here," even though they're free to leave.
2. **Cross-session awareness** — `pendingJobId` is component-local. Leave the
   panel and nothing tells you when onboarding finishes.
3. **Durability** — the default in-memory job store loses jobs on agent restart,
   and under multi-worker it is invisible across workers, which silently breaks
   reload-recovery and readiness.
4. **Concurrency** — no guard; two onboarding submissions for one project run in
   parallel and clobber each other's MDL files.
5. **Progress** — only `running | completed | failed`; a multi-minute LLM job
   shows an indeterminate spinner with no step or count.

Recommendation: ship Phases 1–2 (durability + concurrency guard + truthful
re-entry/notification) as the correctness core, then Phase 3 (progress + backoff)
as the UX polish. No new infrastructure (no Celery/Redis/SSE) is required; the
existing job store + poll model covers it.

---

## 1. Goal & non-goals

**Goal.** The user triggers onboarding and is immediately free to do anything
else — close the panel, switch SQL Lab tabs, reload, or walk away — and is
reliably informed when it finishes (success or failure), from wherever they are,
with the system as the single source of truth.

**Non-goals.**
- Removing the Copilot's dependency on a ready semantic layer. The Copilot
  *legitimately* cannot edit a layer that is still being written
  ([app.py:1702-1706](app.py#L1702-L1706) `_require_project_ready` → `409`). We
  reframe the wait; we do not fake readiness.
- Introducing a distributed task queue (Celery/RQ/arq). The job is
  per-project, low-frequency, and already runs off-request; a broker is
  disproportionate (see §6 D4).
- Changing what onboarding *does* (`onboard_schema_project`) beyond emitting
  progress.

---

## 2. Current state (ground truth, with citations)

### 2.1 Backend — it is already off-request

- Endpoint `POST /agent/semantic-layer/projects/{id}/onboard` creates a job and
  submits it to a runner, then returns the job (`running` in prod):
  [`_start_onboarding_job`](app.py#L2545-L2634), [`onboard_semantic_project`](app.py#L2641-L2666).
- Runner is a **daemon thread** — request returns immediately:
  [`ThreadJobRunner`](semantic_layer/jobs.py#L242-L246). `InlineJobRunner`
  ([jobs.py:235-239](semantic_layer/jobs.py#L235-L239)) runs synchronously and is
  test-only.
- The work — schema introspection → LLM base-model generation → per-model
  validation → auto-activation → retrieval reindex → coverage audit — runs for
  **tens of seconds to several minutes**:
  [`onboard_schema_project`](semantic_layer/onboarding.py#L59-L147), reindex at
  [app.py:2600-2605](app.py#L2600-L2605).
- Job state: `SemanticJob{status: running|completed|failed, result, error}`
  ([schemas.py:533-543](semantic_layer/schemas.py#L533-L543)). Stores:
  [`InMemoryJobStore`](semantic_layer/jobs.py#L63-L119) (default, ephemeral,
  process-local) or [`SqlAlchemyJobStore`](semantic_layer/jobs.py#L122-L177)
  (durable, cross-worker); selected by `_create_job_store`
  ([app.py:3609-3623](app.py#L3609-L3623)).
- Readiness derives state from active files + in-flight jobs and **exposes the
  running job id** — the re-entry hook:
  [`_project_readiness`](app.py#L1647-L1700).

### 2.2 Frontend — it already polls and re-discovers

- `runOnboard` starts the job; on `running` it stores `pendingJobId` and hands
  off to a background poller rather than blocking on success
  ([index.tsx:596-627](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L596-L627)).
- Poll loop: every `2000ms`, up to `450` attempts (~15 min), with unmount
  cleanup; on terminal state it toasts + `refresh()`s
  ([index.tsx:640-695](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L640-L695)).
- Re-entry: on mount, `refresh()` reads `readiness.running_job_id` into
  `readinessJobId`; `pollJobId = pendingJobId ?? (indexing ? readinessJobId : null)`
  resumes polling after a reload
  ([index.tsx:633-634](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L633-L634)).
- Gate: Copilot shows a bootstrap view, not chat, while `indexing`; the file
  tree / MDL editor stay usable
  ([CopilotPanel.tsx:678-754](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx#L678-L754)).

### 2.3 What this means

The architecture is sound and already matches the **Async Request-Reply**
pattern (submit → poll a status resource → terminal state). The gaps are in
**durability, concurrency safety, scope of awareness, and feedback granularity**
— not in the core async model.

---

## 3. Gap analysis — intent vs. reality

| # | Dev intent (from code/comments) | Actual behavior | User intent | Actual UI |
|---|---|---|---|---|
| G1 | "Hand off to background poller… Copilot opens automatically when ready" | True *only while the editor stays mounted in this tab* | "Start it and walk away" | A spinner that reads as "wait here"; leaving forfeits the only notifier |
| G2 | Readiness is "the source of truth for readiness" | True per-process; **InMemoryJobStore is invisible across workers and lost on restart** | "It should just be running safely" | Multi-worker/restart → reload shows `empty` or wedged `indexing` |
| G3 | (none — unguarded) | Two `POST /onboard` create two threads that clobber MDL files | "Clicking twice shouldn't break it" | Double-submit silently corrupts the layer |
| G4 | "one-time setup step, separate from the Copilot chat" | Correct, but only surfaced *inside* the gated rail | "Tell me when it's done, wherever I am" | No global/cross-panel completion signal |
| G5 | Job is `running` until terminal | A 3-minute job is an indeterminate spinner | "Is it stuck or working?" | No step/percent; 15-min foreground cap then "refresh to check" |

---

## 4. Target behavior (the spec)

### 4.1 Lifecycle contract (formalize the Async Request-Reply pattern)

- `POST …/onboard` is **idempotent per active job**: if an onboarding job for the
  project is already `running`, return **that** job (HTTP `200`) instead of
  creating a second (HTTP `202`). The response always carries the authoritative
  job id. *(Closes G3.)*
- The job store is the **single source of truth**. The UI never infers
  completion from anything but the job/readiness resource. *(Reinforces G2/G4.)*
- A `running` job that has not progressed within a TTL (no heartbeat) is reaped
  to `failed` so readiness cannot wedge on `indexing` forever after a process
  death. *(Closes the orphaned-job risk under durable stores.)*

### 4.2 Durable-by-default in production

- `SqlAlchemyJobStore` is the **documented, validated production default**;
  in-memory is dev/test only. On startup, if the store is in-memory **and** the
  process is multi-worker, log a loud warning (readiness/recovery will be
  unreliable). *(Closes G2.)*

### 4.3 Truthful, portable re-entry & notification

- Persist the in-flight `{projectId → jobId}` to `localStorage` on submit so a
  full reload (before the editor re-mounts) can resume immediately, and so a
  lightweight **panel-level** watcher can surface a completion toast even when
  the user is on a different SQL Lab tab within the app. Clear it on terminal.
  *(Closes G1/G4 without new infra.)*
- On completion while the user is away from the rail, raise a global toast
  ("Semantic layer for *schema* is ready") via the existing
  `addSuccessToast/addDangerToast` channel — the same toast the poll loop already
  fires, just hoisted so it is not lost when the rail is unmounted.

### 4.4 Progress feedback

- Extend `SemanticJob` with an optional, coarse `progress` (a step label + an
  optional `done/total` for the per-model loop): `introspecting → generating →
  validating (k/n) → activating → indexing`. `onboard_schema_project` updates it
  at each stage boundary (cheap; it already iterates per proposal). The rail
  renders the step text under the spinner. **No percent/ETA** — LLM latency makes
  them dishonest. *(Closes G5.)*

### 4.5 Polling discipline

- Replace the fixed `2000ms` with **capped exponential backoff** (e.g. 1s → 2s →
  5s, cap 5s) and honor a `Retry-After`/poll-interval hint if the backend sends
  one. Keep the ~15-min budget but, on exhausting it, **leave readiness-driven
  recovery armed** (so the rail still flips to `ready` on the next `refresh`)
  rather than fully giving up. *(Industry: avoid self-inflicted DDoS; Azure.)*

### 4.6 UI/copy alignment (dev intent ↔ user flow ↔ UI)

- Bootstrap copy shifts from a passive "wait" to an **explicit walk-away**
  affordance: "Onboarding is running in the background — you can keep working or
  leave this panel; we'll notify you when the Copilot is ready." This aligns the
  *rational dev intent* (it's already fire-and-forget) with the *actual UI*.
- The picker already closes on confirm ([index.tsx:1122]) — keep that; it is the
  correct "released" signal.

---

## 5. Phasing

- **Phase 1 — Correctness core (backend).** Single-active-job idempotency guard
  (§4.1); durable-store-by-default + multi-worker warning (§4.2); stale-job
  reaper (§4.1). *Exit:* double-submit returns one job; restart/multi-worker
  reload recovers; no permanently-wedged `indexing`.
- **Phase 2 — Truthful re-entry & notification (frontend).** localStorage
  rehydration + hoisted completion toast (§4.3); backoff (§4.5); copy alignment
  (§4.6). *Exit:* leave the panel/tab, get notified on completion; reload mid-job
  resumes without a flash of `empty`.
- **Phase 3 — Progress (full-stack, non-blocking).** `progress` field + per-stage
  updates + rail rendering (§4.4). *Exit:* the rail shows the current stage and
  `k/n` model count.

---

## 6. Decision points & recommendations

- **D1 — Completion notification mechanism.** Options: (a) polling +
  localStorage rehydration [**recommended**]; (b) Server-Sent Events; (c)
  webhooks. *Recommend (a):* reuses the existing poll/readiness model, no new
  infra, and works under multi-worker once the store is shared (§4.2). SSE adds a
  long-lived connection per client and worker-affinity complications for a
  low-frequency event; webhooks are server-to-server and don't fit a browser
  client.
- **D2 — Progress granularity.** Options: (a) coarse step + `k/n`
  [**recommended**]; (b) percent/ETA. *Recommend (a):* honest and cheap. LLM
  generation dominates wall-clock and is unpredictable, so a percent bar would
  stall and mislead.
- **D3 — Double-submit semantics.** Options: (a) return existing running job,
  `200` [**recommended**]; (b) reject with `409`. *Recommend (a):* friendlier and
  idempotent; the client just polls the returned id either way. A client-side
  idempotency key (Stripe-style) is optional hardening, not required for a single
  modal trigger.
- **D4 — Execution substrate.** Options: (a) keep `ThreadJobRunner` +
  `SqlAlchemyJobStore` [**recommended now**]; (b) adopt Celery/Redis (the parent
  Superset already runs Celery). *Recommend (a):* the daemon-thread model is
  adequate for this workload and avoids standing up a broker in the agent
  service. Revisit (b) only if onboarding volume or worker fan-out grows — the
  durable store keeps that migration path open.
- **D5 — Cancellation.** Options: (a) none for now [**recommended**]; (b)
  cooperative cancel flag checked between stages. *Recommend deferring:*
  onboarding is short and idempotent-by-overwrite; the reaper (§4.1) handles the
  stuck-job case. Add (b) later alongside the `progress` stage boundaries (cheap
  once those checkpoints exist).
- **D6 — Block the Copilot during indexing?** **Keep blocking** — it's a real
  data dependency ([`_require_project_ready`](app.py#L1702-L1706)). The fix is
  framing + notification (§4.3, §4.6), not faux-readiness.

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Orphaned `running` job wedges readiness on `indexing` forever (durable store + process death) | TTL/heartbeat reaper marks stale jobs `failed` (§4.1); readiness then surfaces `failed` with a retry CTA |
| Multi-worker with in-memory store → reload sees `empty`, recovery silently broken | Make `SqlAlchemyJobStore` the prod default; loud startup warning on in-memory+multi-worker (§4.2) |
| Double-submit clobbers MDL files | Single-active-job guard returns the existing job (§4.1, D3) |
| Concurrent LLM onboarding jobs inflate cost | Same single-active guard caps it to one job per project |
| localStorage job pointer goes stale (job already finished) | Treat it as a hint only; always reconcile against `getProjectReadiness` before trusting; clear on terminal |
| Notification spam across tabs | One terminal toast per job id; de-dupe on the persisted pointer |
| Backoff hides a genuinely stuck job | Keep the ~15-min budget + reaper; readiness remains the truth on next refresh (§4.5) |

---

## 8. Alignment summary (the four lenses)

- **Clear dev intent ↔ spec.** Comments already say "hand off to background
  poller… opens automatically when ready." The spec makes that literally true
  beyond a single mounted component (durability + cross-session notify).
- **Rational dev intent ↔ feature spec.** The chosen substrate (threads + durable
  store + Async Request-Reply) is the minimal change that satisfies fire-and-
  forget; no broker is introduced without a workload that needs it.
- **User intent ↔ UI flow.** "Start it and walk away, tell me when it's done" →
  picker closes on confirm, work continues off-screen, a global toast lands on
  completion, reload/re-entry never loses the job.
- **Feature spec ↔ actual UI.** Bootstrap copy stops implying a foreground wait
  and states the background reality + the notify-on-ready promise the backend can
  now keep.

---

## 9. Source references

Code entrypoints: onboarding job lifecycle
[app.py:2545-2666](app.py#L2545-L2666); job runners/stores
[jobs.py](semantic_layer/jobs.py); readiness
[app.py:1647-1700](app.py#L1647-L1700); the work
[onboarding.py:59-147](semantic_layer/onboarding.py#L59-L147); frontend
orchestration/poll/re-entry
[index.tsx:596-695](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx#L596-L695);
gate [CopilotPanel.tsx:678-754](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx#L678-L754).

Industry patterns (rationale):
- Async Request-Reply (`202` → poll status resource → terminal), idempotency
  keys, `Retry-After`, job expiry — Microsoft Azure Architecture Center,
  "Asynchronous Request-Reply Pattern."
- Fire-and-forget background jobs, durable state as the single source of truth,
  partial-failure recoverability — Azure "Best Practices for Background Jobs."
- Backoff/rate-limiting on status polling; meaningful failure detail — Zuplo,
  "Asynchronous Operations in REST APIs."
- Background-task UX (visibility, control, confidence; thoughtful microcopy) —
  LogRocket, "UI patterns for async workflows, background jobs, and data
  pipelines."
