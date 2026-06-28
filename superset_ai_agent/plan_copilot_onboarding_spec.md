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

# Feature Spec: Copilot-Driven Onboarding (Replace the Manual Onboarding Flow)

**Status:** SPEC — ready for build review. Source-audited against the working tree on 2026-06-28.
**Scope:** `superset_ai_agent/semantic_layer/**` (FastAPI) + `superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/**`.
**Builds on (all shipped):** `plan_mdl_lab_spec.md` §5.5 (F4 — Copilot drives onboarding, ungated chat), `plan_tool_call_provenance_spec.md` (tool-call ledger), `MDL_PROVENANCE_AND_COVERAGE.md` (coverage + provenance), `plan_unified_attach_ingestion_spec.md` (BI-doc RAG), `skills/onboarding.md` (agent onboarding discipline).
**Relates to:** `plan_onboarding_background_task_spec.md` (whole-schema job), `plan_copilot_parity_spec.md`.

> Symbols are stable; line numbers drift (MDL Lab work lands continuously). Grep the symbol, not the line.

---

## 0. Framing — this is a *promotion*, not a greenfield build

The user's ask reads as "remove the manual onboarding flow, replace it with a prompt-based agentic workflow." The honest, source-verified position is that **most of the agentic workflow already exists and ships today**; what is missing is making it the *primary* path, *retiring* the manual picker, and closing a small, concrete set of **tool** and **provenance** gaps. Spelling this out is the most useful thing this spec can do — it keeps us from re-building shipped machinery.

**Already built (verified in the working tree):**
- The Copilot is **ungated** on an empty project (`plan_mdl_lab_spec.md` §5.5 / F4 marked implemented; the 409 readiness gate was removed). An empty project opens straight into a usable chat.
- **Onboarding-as-tools** exist: `propose_onboard_table`, `propose_onboard_tables`, `propose_relationships` on `MdlToolset` ([copilot/tools.py](semantic_layer/copilot/tools.py)). Each builds models from the **permission-filtered `SchemaIndex`** (catalog structure), stages a reviewable changeset, and rejects unknown tables (the R1 invariant).
- **BI-doc ingestion + RAG** is wired into the chat: the CopilotPanel **Attach** button uploads documents through the unified pipeline (persist → dedup → extract → vectorize), and the agent retrieves over them via `search_documents` / `list_documents` ([copilot/tools.py](semantic_layer/copilot/tools.py), `plan_unified_attach_ingestion_spec.md`).
- An **onboarding skill** already scripts the doc-driven flow turn-by-turn ([skills/onboarding.md](skills/onboarding.md)) and an **enrich-context skill** scripts semantic refinement ([skills/enrich-context.md](skills/enrich-context.md)).
- **Provenance** records agent applies (`document_enriched` / `mdl_agent_edit`) with document chips, and the **tool-call ledger** (`ToolCallRecord`, verbs `write|delete|onboard|relate`) is specified and partly landed (`plan_tool_call_provenance_spec.md`).
- **Coverage audit** runs as a background job auditing the union of the project's BI docs against the active MDL ([copilot/coverage.py](semantic_layer/copilot/coverage.py)).

**Therefore the real deliverable is three things:**
1. **Re-route the default onboarding entry** from the manual `OnboardingTablePicker` to a **guided Copilot onboarding conversation** seeded by a BI-doc upload (the user's 3-step flow).
2. **Close the tool gaps** that today force the agent to either dump the whole schema into context or fall back to raw `write_mdl_file` for things that deserve typed, provenance-bearing verbs — chiefly **table discovery**, **full-document read**, **self-serve coverage**, and **typed metric authoring**.
3. **Extend provenance** to the new tool verbs so the new flow stays as explainable as the manual one (the literal "agent onboarded these tables from this doc" timeline).

The manual picker is **kept as a fallback/power-user path** (DP1), not deleted, because (a) it is the only flow that works when there is no BI doc, and (b) the whole-schema background job has durability properties the conversational path does not yet have.

---

## 1. As-built map — how the tools work together today

The user asked *where capabilities are found, when they are run, and where they are provided*. The five capability families and their wiring:

| Capability | Where it lives (provided) | How it is invoked (when run) | Surface |
|---|---|---|---|
| **Onboard** (table → base MDL) | `MdlToolset.propose_onboard_table(s)` ([copilot/tools.py](semantic_layer/copilot/tools.py)); whole-schema variant `onboard_schema_project` ([semantic_layer/onboarding.py](semantic_layer/onboarding.py)) | **Tool path:** agent calls the tool mid-conversation → stages into `_working` → changeset. **Job path:** `POST .../projects/{id}/onboard` → async daemon-thread job ([app.py](app.py) `_start_onboarding_job`) | Copilot chat **or** `OnboardingTablePicker` |
| **Enrich** (semantics from BI docs) | LLM in `run_copilot_loop` ([copilot/loop.py](semantic_layer/copilot/loop.py)) grounded by `search_documents` + the `enrich-context` skill; document extraction in [extractors.py](semantic_layer/extractors.py) | Agent retrieves doc chunks → authors `write_mdl_file` overlays (descriptions, `displayName`/`synonyms`, calculated columns, metrics) → changeset | Copilot chat (Attach → ask) |
| **MDL read / write / validate** | `read_mdl_file`, `write_mdl_file`, `delete_mdl_file`, `validate_project`, `get_physical_schema`, `list_mdl_files` ([copilot/tools.py](semantic_layer/copilot/tools.py)) | Agent dispatches per turn; `write_mdl_file` validates on write and restores carried-forward `properties`; `build_changeset()` diffs `_working` vs `_originals` at turn end | Copilot chat (read-only tools are invisible; mutating tools become changeset items) |
| **Coverage audit** (information-loss vs BI docs) | `run_coverage_audit` / `run_directory_coverage` ([copilot/coverage.py](semantic_layer/copilot/coverage.py)); 4 stages: extract claims → flatten MDL to facts → judge → aggregate | **Background only** — `_schedule_coverage` fires on onboarding complete + on MDL activation; cached + supersession-safe. **Not** an agent tool. | A badge + "View report" dialog; `GET .../coverage/latest` |
| **Provenance** (audit log) | `SemanticLayerEvent` timeline → `ProvenanceEntry`; `apply_provenance_payload` ([copilot/service.py](semantic_layer/copilot/service.py)); tool-call ledger `ToolCallRecord` ([copilot/schemas.py](semantic_layer/copilot/schemas.py)) | Emitted on every apply (`_emit_agent_apply_provenance`) and onboarding job lifecycle; project-scoped; reset purges `PROVENANCE_EVENT_TYPES` | `MdlProvenanceDialog.tsx` timeline |

**The agent loop that binds them** ([copilot/loop.py](semantic_layer/copilot/loop.py)): a **multi-turn tool-calling loop** (default 8 steps, env `WREN_COPILOT_MAX_STEPS`) with **one validation self-correction retry**. System prompt = `prompts/mdl_copilot.md` + a mode banner (`grill` conservative / `autopilot`) + the relevant **skills** (`onboarding`, `generate-mdl`, `enrich-context`) + recalled operator instructions. Tools are passed as JSON-Schema `ToolSpec`s. Mutating dispatches append to the tool-call ledger; read-only ones do not.

**The intended end-to-end choreography (already scripted in `skills/onboarding.md`):**
1. *Preflight* — readiness + scope check (read-only).
2. *Ground structure* — `get_physical_schema` to read the real catalog; never invent tables/columns.
3. *Onboard* — `propose_onboard_table(s)` for the tables in scope → drafts, auto-validated.
4. *Enrich* — `search_documents` → `write_mdl_file` overlays (descriptions, synonyms, metrics, relationships).
5. *Validate* — `validate_project` before finishing.
6. *Review* — human accepts the changeset; apply persists drafts; activation is a separate human step; `_schedule_coverage` then audits the docs.

**The gap between this and the user's flow** is narrow but real: step 2 forces a **whole-schema dump** (no targeted lookup), step 4's grounding is **top-k chunks** (no full-doc read), and step 6's review is **human-only** (the agent cannot run coverage to self-check before handing off). Section 6 closes exactly these.

---

## 2. Feasibility verdict

**Replacing the manual flow as the *default* is feasible and low-risk**, because the conversational path reuses the same propose→review→apply→activate contract, the same R1 access invariants, and the same provenance/coverage machinery as the manual path. The agent is not given new authority — it stages **reviewable changesets** a human approves, exactly like the manual picker's output is reviewed.

**Fully *deleting* the manual flow is not recommended (DP1).** Two capabilities only the manual/job path has today:
- **No-document onboarding.** When a user has no BI doc, "onboard this schema" is still the fastest correct action; the conversational path has nothing to ground on.
- **Durability at scale.** The background job survives reloads, polls, and re-discovers readiness ([plan_onboarding_background_task_spec.md](plan_onboarding_background_task_spec.md)); the conversational onboard is bounded by the 8-step loop and a live SSE stream. For a 200-table schema, the job is the right tool.

So the recommendation is **promote the Copilot flow to primary, demote the picker to a secondary "Onboard whole schema" affordance** — not delete it.

---

## 3. Goals / non-goals

**Goals**
- **F1 — BI-doc-first onboarding.** The default empty-project experience is "upload a BI doc → the Copilot reads it, maps named entities to physical tables, onboards them, wires relationships, enriches semantics → you review one changeset." (User flow steps 1–2.)
- **F2 — Agent self-review before handoff.** The agent can audit its own output against the BI doc (coverage) and report gaps in-conversation, then refine — the user's step 3 ("onboarding is reviewed by Copilot and further refinements are made").
- **F3 — Targeted discovery tools.** The agent can *find* the few tables a document names without dumping the entire schema into context.
- **F4 — Typed authoring verbs.** Onboarding-class mutations (metrics, schema-add) get first-class, provenance-bearing tools instead of raw `write_mdl_file`.
- **F5 — Provenance parity.** Every new mutating tool maps to a `ToolActionKind` verb and is captured in the tool-call ledger; the timeline distinguishes "onboarded N tables from `spec.pdf`" from "wrote N files."
- **F6 — Demote, don't delete, the manual picker** (DP1).

**Non-goals (this iteration)**
- Deleting `OnboardingTablePicker` or the background onboarding job.
- Giving the agent **deploy/activate** authority (activation stays a human action — `skills/onboarding.md` invariant).
- Letting the agent **run data queries** during onboarding (the skill's "never query data before base MDL exists" rule holds; the optional preview tool in §6 is gated and post-onboard).
- Auto-registering Superset datasets from the agent (DP4 — deferred).
- Redesigning coverage, the SSE stream, the event table, or the reset contract.
- Multi-agent / parallel onboarding orchestration.

---

## 4. Industry patterns informing the design

| Pattern / source | What it prescribes | How we apply it |
|---|---|---|
| **WrenAI agent skills** (`wren-onboarding`, `wren-generate-mdl`) + **human-in-the-loop** | Onboarding is an agent *skill* producing reviewable artifacts, not a wizard; the human approves. | Already mirrored by `skills/onboarding.md` + the changeset review path; this spec makes it the default entry. |
| **Anthropic tool-design guidance** — few, well-named, "right-altitude" tools; return *agent-ready* context, not raw dumps; token-efficient results; consolidate multi-step ops into one high-value tool | Replace "dump the whole schema" with a **search/lookup** tool that returns only the relevant slice; consolidate onboard-then-relate where it reduces round-trips. | `find_tables` (targeted discovery) over `get_physical_schema` (full dump); `propose_onboard_tables` already consolidates the per-table loop. |
| **RAG grounding completeness** — chunk retrieval can silently drop the entity a doc is "about"; provide a whole-document read for extraction tasks | Give the agent a `read_document` so entity/join extraction sees the full spec, not top-k chunks. | New `read_document` tool (§6); mirrors the `_schema_index_for_project` CR3 choice to ground on the *complete* scope, not a ranked top-k. |
| **OpenTelemetry GenAI semantic conventions** — `execute_tool {name}` spans; capture verb + arg *shape* + status; content in nested events, opt-in | Each new mutating tool is a named verb with a shape-only `args_summary`; read-only tools are not timeline rows. | Extends the `ToolCallRecord` verb set; read-only discovery/coverage tools stay out of the ledger (§7). |
| **Semantic-layer governance** (dbt/Cube/Looker) — change history ties each model to *who*, *when*, *from which source* | The timeline must say which BI doc grounded which onboarded model. | `source_document_id` per file (R-B6) + the per-verb rollup carry the lineage triple. |
| **"Minimum viable AI-agent audit trail"** — log tool invocation, data access, and *why* (grounding); shapes not values | The new flow's value depends on auditability — a doc-driven onboard must be traceable to its doc. | New verbs carry `source_document_ids` grounding; coverage self-review is a recorded artifact. |

**Takeaways:** (1) targeted lookup beats schema dumps; (2) full-doc read beats top-k for extraction; (3) consolidate to fewer high-value verbs; (4) every new verb is named at dispatch and provenance-bearing.

---

## 5. Design — the guided Copilot onboarding flow

### 5.1 Entry routing (F1, F6)

On an **empty** project, the CopilotPanel's onboard banner ([CopilotPanel.tsx](superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/CopilotPanel.tsx)) changes from a single "Onboard this schema" button to a **primary "Start with a BI document"** affordance plus a secondary **"Onboard whole schema"** (today's picker). Selecting the primary path:
1. Opens the **Attach** flow (existing `useDocumentIngestion`) to upload one or more BI docs.
2. On extraction-complete, seeds the chat with a **system-suggested first turn** (a templated prompt, editable): *"Read the attached document(s) and onboard the tables they describe from this database, then wire up the relationships and enrich the models. Show me one changeset to review."*
3. Runs the existing `streamCopilot` loop. The agent executes the `skills/onboarding.md` choreography, now using the new discovery + full-read tools (§6).

No new endpoint — this is a **UI re-route + a seeded prompt** over the existing stream/apply contract.

### 5.2 The three user-flow steps mapped to mechanics

| User step | Mechanic | New capability needed |
|---|---|---|
| **1. Upload BI docs for a database** | Existing Attach → unified ingestion → project-scoped RAG | none (shipped) |
| **2. Copilot reads doc, onboards needed tables** | `read_document` (full text) → `find_tables` (map named entities → physical tables) → `propose_onboard_tables` → `propose_relationships` | `read_document`, `find_tables` (F3) |
| **3. Copilot reviews onboarding, refines semantics** | `run_coverage` (self-audit vs the doc) → report gaps → `write_mdl_file` / `propose_metric` overlays → `validate_project` → human accepts | `run_coverage` tool, `propose_metric` (F2/F4) |

### 5.3 Why the agent needs help mapping doc → tables

`get_physical_schema` returns **every** table/column in the project's (possibly multi-schema) scope — for a real warehouse that is thousands of columns, blowing the context window and burying the handful of tables the doc names. The doc says "customers," "orders," "line items"; the agent needs a **fuzzy lookup** that returns just those candidates with enough column hints to onboard confidently. That is `find_tables` (§6.1). This is the single highest-leverage tool gap for the user's flow.

### 5.4 Keeping the human-in-the-loop contract intact

Nothing changes in the trust/authority model:
- The agent **proposes**; `apply_changeset_items` persists **drafts**; **activation stays human**.
- The R1 invariant (`tableReference.schema` ∈ proven schema set; columns ∈ catalog) still rejects any hallucinated or out-of-scope model **pre-apply**.
- Coverage still runs server-side on activation; the new `run_coverage` *tool* is read-only and grounds the agent's self-review — it does not replace the authoritative background run.

---

## 6. New tools (the core proposal)

Grouped by the user-flow step they unblock. Each row notes **mutating?** (→ ledger verb) and **provenance treatment** (§7).

### 6.1 `find_tables` — targeted physical-table discovery (read-only) — **highest priority**

- **Signature:** `find_tables(query: string, schema?: string, limit?: int=10) -> {tables: [{schema, table, columns: [{name, type}], score}]}`
- **What:** fuzzy-rank the project's physical catalog (the `SchemaIndex`, already permission-filtered) against a free-text query (a doc-named entity, e.g. "customer orders"). Returns only the top candidates with their columns — not the whole schema.
- **Why:** §5.3. Replaces "dump everything via `get_physical_schema`" for the doc→table mapping step. Keeps token cost bounded on large warehouses; directly serves user-flow step 2.
- **Mutating:** no. **Provenance:** read-only, not a timeline row (like `search_documents`), but the *result* informs which tables get onboarded.

### 6.2 `read_document` — full-document read (read-only)

- **Signature:** `read_document(document_id: string, max_chars?: int) -> {filename, text, truncated}`
- **What:** return the full extracted text (or a bounded prefix) of one uploaded BI doc.
- **Why:** §4 (RAG completeness). `search_documents` returns top-k chunks, which can drop the section that lists the entity/join model. For *extraction* (vs Q&A), the agent should read the whole spec. Mirrors the CR3 decision to ground on the complete scope, not a ranked top-k.
- **Mutating:** no. **Provenance:** read-only; but it strengthens the per-file `source_document_id` grounding signal (R-B6) because the model knows exactly which doc a model was derived from.

### 6.3 `run_coverage` — agent-callable coverage self-audit (read-only)

- **Signature:** `run_coverage(document_id?: string) -> {score, total, covered, partial, missing, findings: [{kind, subject, status, statement}]}`
- **What:** run the existing `run_coverage_audit` / `run_directory_coverage` against the **current working set** (or active MDL) and return the findings to the agent, in-conversation.
- **Why:** user-flow step 3 ("onboarding is reviewed by Copilot"). Today coverage is background-only and post-activation; the agent cannot self-check before handing the changeset to the human. This tool turns the review step into an agent loop: audit → see "missing: revenue metric defined in §4" → author the overlay → re-audit.
- **Mutating:** no. **Provenance:** read-only, but **emit a lightweight `coverage_checked` annotation** into the apply event's detail (not a new top-level event type — stays inside the purge contract) so the timeline can show "self-audited: 8/10 claims covered" alongside the apply. *(See DP3.)*
- **Cost guard:** coverage is LLM-heavy (claim extraction + judging). Cap to N self-audits per turn (R3); reuse the existing coverage **cache** so a re-audit of an unchanged working set is free.

### 6.4 `propose_metric` — typed metric authoring (mutating, verb `write`/new `metric`)

- **Signature:** `propose_metric(model: string, name: string, expression: string, description?: string, grounded_document_id?: string) -> {staged, validation, rejected?}`
- **What:** stage a metric on an onboarded model as a reviewable item, validated like `propose_relationships`.
- **Why:** metrics are the most error-prone hand-written MDL (aggregations, filters). Today they go through raw `write_mdl_file`, so a malformed metric surfaces only at manifest validation. A typed verb gives per-item validation + a clean provenance verb + a place to stamp `source_document_id` when the metric came from a doc ("revenue = sum(amount) per the spec").
- **Mutating:** yes. **Provenance:** `ToolActionKind` — recommend extending the enum with `metric` (DP2), else default to `write`.

### 6.5 `add_project_schema` — schema-add as a first-class verb (mutating, verb `onboard`)

- **Signature:** `add_project_schema(schema: string) -> {added, proven_access, rejected?}`
- **What:** when a cross-schema BI doc names a table in a schema not yet in the project set, add that schema **after re-proving DB access** (the R1 access proof from the multi-schema spec).
- **Why:** the MDL Lab spec (§5.5) says the BI-doc flow may need to *add schemas* before onboarding their tables. Today this is implicit/awkward. A first-class tool makes the access-proof explicit and auditable, and lets `propose_onboard_tables` then target the new schema.
- **Mutating:** yes (changes project membership). **Provenance:** verb `onboard` (or a `schema_add` detail); the access proof is the security-critical step (R2).

### 6.6 Deferred / decision-gated tools

- **`register_dataset`** (DP4) — create a Superset dataset for a physical table the agent wants to onboard. **Deferred:** `skills/onboarding.md` confirms onboarding works off **catalog introspection**, not dataset registration, and dataset creation is a Superset-RBAC-bearing write the manual picker deliberately routes through the Superset UI ("Add Dataset" → `/dataset/add/`). Recommend **not** giving the agent this authority initially; surface "this table isn't registered" as a hint the human resolves.
- **`preview_model` / `sample_rows`** (DP5) — run a bounded `LIMIT` query to sanity-check a join/metric. **Deferred + gated:** violates "never query data before base MDL exists" during onboarding; only meaningful **post-activation** and only behind an explicit flag, because it grants the agent data-read on the database. Recommend deferring to a separate "verification" spec.

### 6.7 Summary table

| Tool | Step | Mutating | Ledger verb | Priority |
|---|---|---|---|---|
| `find_tables` | 2 | no | — | **P0** |
| `read_document` | 2 | no | — | **P0** |
| `run_coverage` | 3 | no | (`coverage_checked` annotation) | P1 |
| `propose_metric` | 3 | yes | `metric` (or `write`) | P1 |
| `add_project_schema` | 2 (cross-schema) | yes | `onboard` | P2 |
| `register_dataset` | 2 | yes | — | **Deferred (DP4)** |
| `preview_model` | post | (read data) | — | **Deferred (DP5)** |

---

## 7. Explainability via the provenance API

The new flow must be **as auditable as the manual one** — the user must see "the agent onboarded *these* tables from *this* document and refined *these* models." This rides entirely on the in-flight `plan_tool_call_provenance_spec.md` mechanism; this spec only **extends the verb set and the grounding signal**, adding no new event type (so the reset purge contract is untouched).

**How each new tool surfaces:**
- **Mutating tools** (`propose_metric`, `add_project_schema`) append a `ToolCallRecord` on dispatch with `action` verb, `paths`, `source_document_ids` (the BI doc that grounded them), and a shape-only `args_summary`. They roll up in `MdlProvenanceDialog` as *"Onboarded 3 tables · Added 1 schema · Wrote 2 metrics — from `sales-spec.pdf`."*
- **`read_document`** strengthens **per-file `source_document_id`** (R-B6): because the agent read exactly one doc to derive a model, the toolset can stamp that model's file with the precise source doc, so the timeline shows the **source chip** per onboarded model.
- **`run_coverage`** is read-only, so it is **not** a timeline row, but its result is folded into the apply event's `detail` as a `coverage_checked` annotation (e.g. `{score: 0.8, missing: 2}`). This is the auditable evidence that the agent **reviewed its own work** (user-flow step 3) before handoff — a governance-relevant signal. It stays inside the already-purged `document_enriched`/`mdl_agent_edit` event (no new `SemanticLayerEventType`, DP3).
- **`find_tables`** is read-only and not recorded (matches "`list_documents` is deliberately not an enrichment signal"); its effect is visible through the tables that *do* get onboarded.

**Net:** the timeline answer to "what did the agent do, and why" becomes a single expandable row per apply: a per-verb rollup, a per-file source-doc chip, and a self-audit score — all from the existing ledger + one annotation field.

---

## 8. Decision points

> Recommendations follow existing in-repo patterns and §4 standards. Confirm the starred ones before build.

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **DP1 ★** | Fate of the manual `OnboardingTablePicker` | (a) demote to secondary "Onboard whole schema"; (b) delete entirely; (c) keep co-equal | **(a)**. The picker is the only no-document and durable-at-scale path (§2). Demote, don't delete. Re-route the *default* to the Copilot flow. |
| **DP2 ★** | Verb for `propose_metric` / metric authoring | (a) extend `ToolActionKind` with `metric`; (b) reuse `write` | **(a)** if you want "Wrote 2 metrics" distinct in the rollup (matches the governance audience); else (b) is zero-migration. Lean (a) — additive enum, no migration (R8 in the provenance spec). |
| **DP3 ★** | How `run_coverage` self-audit is recorded | (a) annotation in the apply event detail (`coverage_checked`); (b) a new top-level provenance event; (c) not recorded | **(a)**. Keeps the reset-purge contract intact (no new event type), gives the governance signal "agent self-reviewed," and avoids timeline-row explosion. (b) escapes the purge frozenset; (c) loses the step-3 evidence. |
| **DP4** | Agent dataset registration (`register_dataset`) | (a) defer — human registers via Superset UI; (b) give the agent the tool | **(a)**. Dataset creation is a Superset-RBAC write the manual picker intentionally routes through `/dataset/add/`; onboarding itself works off catalog introspection. Surface "unregistered" as a hint, not an agent action. |
| **DP5** | Data-preview tool (`preview_model`) | (a) defer to a verification spec, gated; (b) include now | **(a)**. Conflicts with "never query data before base MDL exists"; grants data-read; only useful post-activation. Out of scope here. |
| **DP6** | First-turn seeding | (a) templated, **editable** suggested prompt; (b) auto-send a hidden prompt; (c) no seed (blank chat) | **(a)**. Editable keeps the human in control and teaches the interaction; auto-send (b) removes agency and can mis-fire on a doc the agent can't map; blank (c) wastes the BI-doc context. |
| **DP7** | `find_tables` ranking | (a) reuse the existing embedding/keyword ranker (as coverage/RAG do); (b) substring match only | **(a)**. The codebase already has an embedder + keyword fallback (`judge_coverage`, `keyword_rank_chunks`); reuse it for consistent behavior and degrade-to-keyword resilience. |

---

## 9. Risks & mitigations

| ID | Risk | Sev | Mitigation |
|---|---|---|---|
| **R1** | **Agent onboards the wrong tables** — fuzzy `find_tables` maps "orders" to the wrong physical table. | Med | `find_tables` returns **candidates with scores + columns**; the agent must still `propose_onboard_tables` (a reviewable changeset) and the human accepts. Show the source-doc grounding so the reviewer can check. Never auto-apply. |
| **R2** | **Cross-boundary access via `add_project_schema`** — agent adds a schema the user can't access. | **High (security)** | Route every schema-add through the **R1 access proof** (multi-schema spec): the schema must be in the user's proven Superset DB access, else reject pre-apply. Test: onboard proposal for an unproven schema is rejected. Map to `SECURITY.md`: principal = any role with that DB's access; row = "data access requires proven access to the underlying database." |
| **R3** | **Coverage tool cost** — `run_coverage` is LLM-heavy (extract + judge); an agent could loop it. | Med | Cap self-audits per turn (e.g. 2); reuse the existing coverage **cache** (unchanged working set → free); coverage stays the *self-check*, the authoritative run is still the post-activation background job. |
| **R4** | **Context blow-up from `read_document`** — a huge BI doc floods the window. | Med | `max_chars` bound (default ~50–100KB, matching the 200KB attach slice); return `truncated: true`; the agent can page or fall back to `search_documents` for very large corpora. |
| **R5** | **Loss of durability** if users abandon the job path — a conversational onboard of a 200-table schema hits the 8-step loop ceiling and stalls. | Med | DP1 keeps the background job as the "whole schema" path; `find_tables` keeps the conversational path **targeted** (a doc names a handful of tables, not hundreds). Document the guidance in `skills/onboarding.md`. |
| **R6** | **Provenance regression** — new verbs not captured, timeline says "wrote N files" for an onboard. | Low | F5: each mutating tool appends a `ToolCallRecord`; reuse the provenance spec's guard test (every mutating dispatch ∈ ledger). `run_coverage` annotation rides inside the purged event (DP3). |
| **R7** | **Discoverability** — users don't find the new BI-doc entry and default to the (now secondary) picker. | Low | The empty-project banner makes "Start with a BI document" the **primary** button; the picker is the secondary link. A/B the copy. |
| **R8** | **`find_tables` ranker drift / no embedder** in some deployments. | Low | DP7: degrade to keyword ranking (the existing fallback), same as coverage/RAG. |
| **R9** | **Seeded prompt mis-sets expectations** when the doc has no mappable tables. | Low | DP6 editable seed; the agent reports "I could not map these entities to tables in this database" rather than hallucinating; `find_tables` returning nothing is a clean signal. |

---

## 10. Intent alignment (dev ↔ spec ↔ user/UI)

| Layer | Stated intent | Spec realization | Verification |
|---|---|---|---|
| **User (step 1)** | "Upload BI docs relevant to a database." | Existing Attach → project-scoped RAG; primary empty-project entry (§5.1). | E2E: upload a doc on an empty project → chat opens with the doc attached + a seeded prompt. |
| **User (step 2)** | "Copilot reads the doc and onboards the needed tables." | `read_document` (full text) → `find_tables` (map entities → tables) → `propose_onboard_tables` → `propose_relationships`; one reviewable changeset (§5.2, §6.1–6.2). | E2E: a doc naming 3 tables across 2 schemas → changeset proposes schema-add + 3 models + joins; R1-validated; human accepts. |
| **User (step 3)** | "Onboarding is reviewed by Copilot and refinements are made." | `run_coverage` self-audit in-conversation → agent authors overlays / `propose_metric` → `validate_project` → re-audit (§5.2, §6.3–6.4). | E2E: after onboard, agent reports "missing: revenue metric (§4)," adds it, coverage rises; timeline shows `coverage_checked`. |
| **Dev (tool design)** | "Give the LLM the right tools, not schema dumps." | `find_tables` over `get_physical_schema`; typed `propose_metric`/`add_project_schema`; consolidated verbs (§6, §4 Anthropic guidance). | Unit: `find_tables` returns only top-k candidates; `propose_metric` validates per-item. |
| **Dev (explainability)** | "Maintain explainability primarily via the provenance API." | New mutating tools → `ToolCallRecord` verbs + per-file `source_document_id`; `run_coverage` → `coverage_checked` annotation; no new event type (§7, DP2/DP3). | Unit: a doc-driven onboard apply renders "Onboarded N tables from `<doc>`" + source chips + self-audit score; reset purges it. |
| **Dev (don't rebuild)** | "Reuse the shipped agentic primitives." | §0 — promotion not greenfield; no new endpoints for the core flow; reuse stream/apply/coverage/provenance. | Review: the core flow adds 0 new HTTP routes; tools are added to `MdlToolset`. |
| **Policy** | "Human-in-the-loop; agent doesn't deploy or query data." | Propose→review→apply→*human* activate; no agent data queries; `register_dataset`/`preview` deferred (§5.4, DP4/DP5). | Test: agent cannot activate; cannot run data queries during onboarding. |

---

## 11. Phasing (each ends green per `CLAUDE.md`)

1. **P1 — Discovery + read tools (P0, unblocks the flow).** Add `find_tables` + `read_document` to `MdlToolset` + `specs()`; reuse the embedder/keyword ranker (DP7). Extend `skills/onboarding.md` to prefer `find_tables` over `get_physical_schema` for doc-driven runs. *(Backend; read-only; no migration, no provenance change.)*
2. **P2 — UI re-route (the visible payoff).** Empty-project banner: primary "Start with a BI document" → Attach → seeded editable first turn (DP6); secondary "Onboard whole schema" (the demoted picker, DP1). *(Frontend; reuses stream/apply.)*
3. **P3 — Self-review + typed authoring.** `run_coverage` tool (read-only, cached, capped) + `coverage_checked` annotation (DP3); `propose_metric` typed verb (DP2). *(Backend; provenance-detail add, no new event type.)*
4. **P4 — Cross-schema add.** `add_project_schema` with the R1 access proof (R2); `propose_onboard_tables` can target the newly added schema. *(Backend; security-tested.)*

P1 is the only hard prerequisite (the flow is materially better with it). P2 can ship on P1. P3/P4 are independent enhancements. The deferred tools (DP4/DP5) are explicitly out of these phases.

---

## 12. File touchpoints

> Symbols stable; re-grep lines. Hot files (`tools.py`, `app.py`, `loop.py`, `CopilotPanel.tsx`, `index.tsx`, `api.ts`) take single-writer edits.

### P1 — discovery + read tools
| File:symbol | Change |
|---|---|
| `semantic_layer/copilot/tools.py::MdlToolset.specs, _find_tables (new), _read_document (new), dispatch` | Add two read-only tools + their `ToolSpec`s; rank via the existing embedder/keyword fallback. |
| `skills/onboarding.md` | Prefer `find_tables` for doc-driven mapping; `read_document` for extraction; keep `get_physical_schema` for grounding/validation. |
| `tests/unit_tests/superset_ai_agent/test_copilot_tools.py` | `find_tables` top-k + degrade-to-keyword; `read_document` truncation. |

### P2 — UI re-route
| File:symbol | Change |
|---|---|
| `SemanticLayerEditor/CopilotPanel.tsx` (empty-state banner) | Primary "Start with a BI document" (Attach + seeded turn); secondary "Onboard whole schema". |
| `SemanticLayerEditor/index.tsx` | Wire the secondary path to the existing `OnboardingTablePicker` (demoted, DP1). |
| `*.test.tsx` | Empty-state renders the doc-first primary; picker still reachable. |

### P3 — self-review + metrics
| File:symbol | Change |
|---|---|
| `semantic_layer/copilot/tools.py::_run_coverage (new), _propose_metric (new)` | Read-only coverage (cached, capped); typed metric verb. |
| `semantic_layer/copilot/coverage.py` | Expose a working-set audit entry callable from the tool. |
| `semantic_layer/copilot/schemas.py::ToolActionKind` | (DP2) add `metric`. |
| `semantic_layer/copilot/service.py::apply_provenance_payload` | Fold `coverage_checked` annotation into detail (DP3). |
| `AiAgentPanel/api.ts`, `MdlProvenanceDialog.tsx` | Surface metric verb + self-audit score in the rollup. |

### P4 — cross-schema add
| File:symbol | Change |
|---|---|
| `semantic_layer/copilot/tools.py::_add_project_schema (new)` | Schema-add routed through the R1 access proof. |
| `app.py` (access wiring) | Re-prove DB access for the added schema; reject unproven. |
| `tests/.../test_copilot_onboarding.py` | Cross-schema onboard from a BI doc; unproven-schema rejection (R2). |

---

## 13. Open questions for the user

- **DP1:** confirm demote-not-delete for the manual picker — or do you want it removed once the doc-first flow is proven?
- **DP2:** add a distinct `metric` provenance verb, or fold metrics under `write`?
- **DP3:** is the `coverage_checked` annotation the right "agent self-reviewed" signal, or do you want the self-audit surfaced more prominently (its own UI strip)?
- **DP4/DP5:** confirm deferring agent dataset-registration and data-preview — or are either in scope for this iteration?
- **Seeded prompt (DP6):** approve an editable templated first turn, or prefer the chat opens blank with the doc attached?

---

### Sources
- [WrenAI — agent skills (`wren-onboarding`, `wren-generate-mdl`), human-in-the-loop modeling](https://github.com/Canner/WrenAI)
- [Anthropic — Writing effective tools for agents (few well-named tools, agent-ready context over raw dumps, token-efficient results, consolidate multi-step ops)](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [OpenTelemetry — Semantic Conventions for GenAI agent & framework spans (`execute_tool {name}`, `gen_ai.tool.*`, content-in-events, opt-in capture)](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- [ARMO — Minimum Viable AI-Agent Audit Trail (tool invocation + data access + why; shapes not values)](https://www.armosec.io/blog/minimum-viable-audit-trail/)
- [Cube — semantic-layer governance: lineage, change history, audit](https://cube.dev/articles/dbt-semantic-layer-alternatives-2026)
- [Opening up the Looker semantic layer — platform-governed access](https://cloud.google.com/blog/products/business-intelligence/opening-up-the-looker-semantic-layer)
