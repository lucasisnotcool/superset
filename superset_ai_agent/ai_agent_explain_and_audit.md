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

# AI Agent — Explain & Audit UI: Implementation Plan

## 1. Goal

Present a single, sequential, human-readable view of everything that happens
between a user sending a message and receiving a final response: intent, schema
context loaded, MDL/semantic context retrieved, learned examples recalled, the
SQL draft, semantic→native rewrites, dry-plan diagnostics, validation, repairs,
execution, and the final answer artifacts.

The view is a **lightbox/dialog**, structured like a conversation but where each
box represents exactly **one user message** and shows the ordered chain of steps
from that message to the agent's final output, including retries.

This is an **explainability + audit surfacing** effort. It must not change agent
behavior, SQL generation, execution governance, or the security model. It only
makes the already-captured trace/provenance legible.

## 2. Current state (what already exists)

Backend already captures everything the UI needs, but it is scattered across
three carriers and the live stream is lossy. See
[`wren_enrich_and_retrieve.md`](wren_enrich_and_retrieve.md) for the full
pipeline; the relevant carriers are:

- **`TraceEvent{step, status, summary, details: dict[str, Any]}`** — emitted by
  every graph node ([`schemas.py`](schemas.py)). Streamed live, but **`details`
  is dropped** on the wire (see Seam 1).
- **`WrenContextArtifact`** — `matched_models`, `context_items`, `retrieval`
  (`WrenRetrievalArtifact`), `dry_plan`, `retrieval_mode`,
  `retrieved_item_count`, `recalled_example_count`. Only on the final
  `artifact.wren_context`.
- **`AuditInfo`** — `semantic_sql`, `native_sql`, `engine`, `executed_sql`,
  `query_id`, `row_limit`, `adapter`, … Only on the final `artifact.audit`.

Graph nodes (both `ConversationGraph` and `TextToSqlGraph`) and their emitted
`step` names:

```
load_conversation → classify_intent → [answer_directly]
  → load_context → load_wren_context → draft_response/draft_sql
  → dry_plan_with_wren → plan_semantic_sql → validate_sql
  → {repair_sql | correct_semantic_sql} → execute_sql
  → build_artifacts → reflect_sql_outcome
```

Streaming entrypoints: `run_stream` and `execute_approved_sql_stream`
(`conversation_graph.py`), surfaced at
`POST /agent/conversations/{id}/messages/stream` and `…/execute-sql/stream`
(`app.py`). The one-shot `POST /agent/query` (`TextToSqlGraph`) is **buffered
only** — no SSE.

## 3. Design: a single typed step timeline

Collapse the three carriers into one ordered, typed contract emitted both live
(enriched `progress` frames) and on the final response. This is the spine of the
whole feature; the FE renders each lightbox box from one stream with no
cross-carrier correlation.

### 3.1 New schema (`superset_ai_agent/schemas.py`)

```python
class AgentStepKind(str, Enum):
    load_conversation = "load_conversation"
    classify_intent   = "classify_intent"
    answer_directly   = "answer_directly"
    load_context      = "load_context"
    load_wren_context = "load_wren_context"
    draft_sql         = "draft_sql"          # draft_response in conversation graph
    dry_plan          = "dry_plan_with_wren"
    plan_semantic_sql = "plan_semantic_sql"
    validate_sql      = "validate_sql"
    repair_sql        = "repair_sql"
    correct_semantic_sql = "correct_semantic_sql"
    execute_sql       = "execute_sql"
    build_artifacts   = "build_artifacts"
    reflect           = "reflect_sql_outcome"
    error             = "conversation_error"

class AgentStep(BaseModel):
    kind: AgentStepKind | str          # str fallback so a new node never breaks render
    status: Literal["ok", "warning", "error"] = "ok"
    summary: str
    started_at: datetime
    duration_ms: int | None = None
    attempt_index: int = 0             # which SQL iteration (Seam 5)
    artifact_id: str | None = None     # ties a step to its produced SQL artifact
    detail: AgentStepDetail | None = None   # discriminated payload (Seam 3)
```

`AgentStepDetail` is a **discriminated union** keyed on `kind`, each variant
carrying only the fields that step actually produces. Minimum viable set (extend
incrementally):

- `load_context` → `{dataset_count, database_name, retrieval: WrenRetrievalArtifact | None}`
- `load_wren_context` → `{available, matched_models, retrieval_mode, retrieved_item_count, context_item_count, project_id, mdl_path}`
- `draft_sql` → `{response_type, model, recalled_example_count, instruction_count}`
- `dry_plan` → `{available, diagnostics: list[str]}` (reuse `dry_plan_diagnostics`)
- `plan_semantic_sql` → `{engine, rewritten, semantic_sql, native_sql, referenced_tables, warnings}`
- `validate_sql` → `{is_valid, dialect, errors}`
- `repair_sql` / `correct_semantic_sql` → `{errors, dry_plan_diagnostics, attempt}`
- `execute_sql` → `{row_count, sql, error, executed_sql, query_id, adapter}`
- `build_artifacts` → `{insight_card_count, chart_type, has_data_preview}`
- `reflect` → `{outcome, remaining_sql_iterations, retry_feedback}`

### 3.2 Backend assembly

Add a pure helper module `superset_ai_agent/explain.py`:

```python
def build_agent_timeline(
    trace: list[TraceEvent],
    *,
    wren_context: WrenContextArtifact | None,
    audit: AuditInfo | None,
    artifacts: list[ConversationArtifact] | None = None,
) -> list[AgentStep]: ...
```

It maps each `TraceEvent` to an `AgentStep`, pulling step-specific fields out of
`event.details` and folding the late-bound provenance (`wren_context`, `audit`)
onto the matching steps (`load_wren_context`, `plan_semantic_sql`,
`execute_sql`). This keeps the mapping in **one tested place** instead of the FE.

Surface the timeline two ways:

1. **Final response.** Add `timeline: list[AgentStep] = []` to
   `AgentQueryResponse` and `ConversationTurnResponse` (and, for chat history,
   to `ConversationArtifact` so reopened conversations re-render). Populate in
   `TextToSqlGraph.run`, `ConversationGraph.run`, and `run_stream`/execute paths.
2. **Live stream.** Replace the lossy `_progress_event` with a typed
   `AgentStep`-shaped `progress` frame (Seam 1). Emit one per newly produced
   trace entry, carrying `detail`. The existing `complete` frame already carries
   the full response (now including `timeline`).

### 3.3 Timing & attempt grouping

- Stamp `started_at`/`duration_ms` at emission. Cheapest correct approach: record
  a per-node wall-clock around each node body in the graph (a tiny decorator or a
  `time.monotonic()` pair folded into the `TraceEvent`). If we don't want to
  touch `TraceEvent`, compute deltas in `build_agent_timeline` from per-event
  `created_at` — but that requires adding `created_at` to `TraceEvent` (smaller
  change, recommended).
- `attempt_index` is derived from `sql_iterations` at emit time;
  `correct_semantic_sql`/`repair_sql`/`execute_sql` increment within a turn so
  the FE can group boxes by attempt (Seam 5).

## 4. Frontend plan

### 4.1 Data layer (`AiAgentPanel/api.ts`)
- Add `AgentStep` + `AgentStepDetail` TS types mirroring the backend union.
- Extend `ConversationProgressEvent` to carry the `AgentStep` payload (keep
  `summary` for the existing one-line `ProgressBubble`).
- Add `timeline` to `ConversationTurnResponse`/`ConversationArtifact`/the
  one-shot response types.
- `consumeConversationStream` already demuxes `progress`/`complete`; have it
  accumulate streamed `AgentStep`s into an array the dialog can render live.

### 4.2 Components (`AiAgentPanel/`)
- **`ExplainDialog.tsx`** — the lightbox. Antd `Modal` from
  `@superset-ui/core/components`. Props: `{ userMessage, steps: AgentStep[],
  finalArtifact }`. Renders a vertical timeline (antd `Steps`/`Timeline`), one
  block per step, grouped by `attempt_index`, status-colored, with an
  expandable typed detail renderer per `kind`.
- **`AgentStepDetail.tsx`** — switch on `step.kind` → a small dedicated renderer
  (SQL diff for `plan_semantic_sql`, models/chunks list for `load_wren_context`,
  error/diagnostics for `repair_sql`, row-count/executed-sql for `execute_sql`).
  Unknown kind → fall back to the raw `summary` (forward-compatible).
- Trigger: an "Explain" affordance on each assistant turn / artifact in
  `index.tsx`, opening the dialog with that turn's `timeline`. During a live
  turn, the dialog can open and fill from the streamed steps.
- Reuse `AuditInfoPanel` inside the final-step block rather than duplicating the
  badge logic.

### 4.3 Backward-compatible rendering
The current flat `Trace` `<details>` and raw `wren_context` JSON dump in
`index.tsx` stay until the dialog ships, then are removed. Keep the one-line
`ProgressBubble` (it reads `summary`, unchanged).

## 5. Touchpoints (by file)

Backend:
- `superset_ai_agent/schemas.py` — `AgentStep`, `AgentStepKind`,
  `AgentStepDetail` union; `timeline` field on `AgentQueryResponse`; (optionally
  `created_at` on `TraceEvent`).
- `superset_ai_agent/explain.py` — **new** `build_agent_timeline` mapper.
- `superset_ai_agent/conversations/schemas.py` — `timeline` on
  `ConversationTurnResponse` and `ConversationArtifact`.
- `superset_ai_agent/conversation_graph.py` — `_progress_event` → typed step;
  populate `timeline` in `run`/`run_stream`/`execute_approved_sql*`; thread
  `attempt_index`/timing into emitted events.
- `superset_ai_agent/graph.py` — populate `timeline` in `run`; (no stream today —
  see Risk R5).
- `superset_ai_agent/app.py` — no route changes required; the streaming SSE
  serializer `_conversation_sse` already passes typed dicts. Confirm
  `model_dump(mode="json")` covers the new enum/union.

Frontend (`superset-frontend/src/SqlLab/components/AiAgentPanel/`):
- `api.ts` — types + stream accumulation.
- `ExplainDialog.tsx`, `AgentStepDetail.tsx` — **new**.
- `index.tsx` — "Explain" trigger; mount dialog; retire the raw trace/JSON
  `<details>` once parity is reached.
- `AuditInfoPanel.tsx` — reused inside the dialog (no change, or minor extract).
- Tests: `index.test.tsx`, `api.test.ts`, new `ExplainDialog.test.tsx`.

## 6. Risks & mitigations

**R1 — Live stream is lossy (`details` dropped).** `_progress_event` only sends
`step/status/summary`, so a step-by-step *live* reveal is impossible without a
contract change.
→ *Mitigation:* enrich `progress` frames to the typed `AgentStep` (§3.2). Low
risk: additive, the `complete` frame is unchanged, and old clients that read
`summary` keep working.

**R2 — Sensitive data exposure in details.** Steps carry SQL, executed SQL,
schema names, dry-plan errors, and recalled example metadata. The dialog widens
what a user *sees at once*, so anything leaked here is leaked under that user's
own authorization.
→ *Mitigation:* the timeline is built from data already returned to the same
caller on the same governed turn — no new authorization surface. Do **not** add
raw row data or other users' learned examples to step details; cap
`referenced_tables`/diagnostics lengths; never include `AuditInfo.client_id`/
`source_hash` beyond what `AuditInfoPanel` already shows. Confirm the timeline is
filtered through the same identity-scoped response path (it rides on
`ConversationTurnResponse`, so it inherits per-owner scoping).

**R3 — Payload bloat / context duplication.** Folding `wren_context` + `audit`
onto steps and also keeping them on the artifact duplicates data and grows the
SSE/JSON payload, especially with retries (cumulative trace × multiple
artifacts).
→ *Mitigation:* steps reference compact summaries (counts, names, truncated
lists), not full `context_items` dumps. Keep the single authoritative
`wren_context`/`audit` on the artifact; steps carry only the projected fields
they render. Bound `referenced_tables` and `diagnostics`. Reuse
`cap_context_items` philosophy.

**R4 — Mapping drift between nodes and the timeline builder.** New/renamed graph
nodes silently fall out of the typed union and render as bare summaries.
→ *Mitigation:* `kind: AgentStepKind | str` with a raw-summary fallback so an
unknown step degrades gracefully (never errors). Add a unit test asserting every
`step` string emitted by both graphs has a matching `AgentStepKind` — fails CI
when a node is added without updating the mapper.

**R5 — One-shot `/agent/query` has no stream.** `TextToSqlGraph` returns buffered
only, so the live reveal applies to chat only.
→ *Mitigation:* ship `timeline` on the buffered `AgentQueryResponse` and have the
dialog render it statically for that path. Add an SSE variant later only if a
live one-shot view is needed — out of scope for v1.

**R6 — Retry grouping ambiguity.** A turn can loop draft→execute→reflect→draft
and emit several artifacts against a cumulative trace; naive rendering shows a
confusing flat list and mis-attributes the `execute_sql` error event (which is
deliberately stamped with its `sql`).
→ *Mitigation:* `attempt_index` + `artifact_id` on each step; the dialog groups
boxes by attempt. Preserve the existing `details.sql` on execute-error events for
attribution.

**R7 — Timing accuracy.** Adding `duration_ms` tempts intrusive instrumentation.
→ *Mitigation:* prefer adding `created_at` to `TraceEvent` and deriving deltas in
the mapper — no node-body changes, monotonic per turn. Treat timing as
best-effort/optional; the UI must render without it.

**R8 — Persistence & history re-render.** Reopening a past conversation must
re-render the timeline; trace is on the artifact but the new `timeline` must be
too, and the SQLAlchemy store must round-trip it.
→ *Mitigation:* add `timeline` to `ConversationArtifact` (already JSON-serialized
in the store). Verify `test_semantic_layer_sqlalchemy_store`/conversation store
tests cover the new field; it is additive and defaults to `[]` for old rows.

**R9 — Behavioral neutrality.** This is an observability feature; any change to
node logic risks altering SQL/exec outcomes.
→ *Mitigation:* nodes keep emitting `TraceEvent` exactly as now; the timeline is
assembled *from* that trace post hoc in `build_agent_timeline`. No routing,
prompt, validation, or execution code changes. Snapshot-test that existing
trace/SQL outputs are byte-identical before/after.

**R10 — Enrichment vs. retrieval conflation.** Document upload/enrich events flow
on a *separate* `SemanticLayerEvent` SSE feed, not the conversation trace. Mixing
them into the turn timeline would mislead ("how the layer was built" vs. "what
this answer used").
→ *Mitigation:* the turn lightbox sources **only** the turn's `timeline` +
`wren_context` (which MDL/models this answer used). Keep enrichment provenance in
the existing Semantic Layer editor surfaces; do not merge the two feeds.

## 7. Phasing

1. **B1 — Contract.** `AgentStep`/`AgentStepKind`/`AgentStepDetail`,
   `build_agent_timeline`, `timeline` on responses + `ConversationArtifact`,
   `created_at` on `TraceEvent`. Unit tests incl. the node-coverage guard (R4).
2. **B2 — Live stream.** Typed `progress` frames; stream accumulation. Tests for
   stream demux + lossless detail.
3. **F1 — Static dialog.** `ExplainDialog` + `AgentStepDetail` rendering the
   final `timeline` (works for chat history and one-shot). Retire raw trace/JSON
   `<details>`.
4. **F2 — Live dialog.** Fill the dialog from streamed steps during a running
   turn; attempt grouping.
5. **V — Verification.** Behavioral-neutrality snapshot tests (R9); payload-size
   check (R3); `pre-commit run --all-files`; `npm run test` + `npm run type`.

## 8. Out of scope (v1)

- Live SSE for the one-shot `/agent/query` (R5).
- Surfacing raw result rows or other users' learned examples in steps (R2/R3).
- Merging the enrichment event feed into the turn timeline (R10).
- Any change to agent routing, prompts, validation, or execution governance (R9).
