<!--
Implementation plan — fix R1: cross-schema golden/memory recall dropped by a
mis-scoped F2 access set. Derived from evaluation/RESULTS_v3.md (E16) + live root-cause.
Source-backed; file:line cited. Sequential checklist for future agent sessions.
Status: NOT STARTED.
-->

# Fix R1 — Access-aware recall is mis-scoped (cross-schema golden/memory dropped)

## Problem (verified against source)

The F2 access filter (Stage A, fail-closed) drops a recalled pair unless **every**
referenced physical table is in the "accessible" set. That set is built from
`context.datasets` ([graph.py:608](graph.py#L608), [conversation_graph.py:1080](conversation_graph.py#L1080)
— both via `build_recall_access(...)`). Two source-verified defects make that set wrong:

1. **It is single-schema.** `context.datasets` comes from `context_provider.get_context(request)`
   ([graph.py:409](graph.py#L409)), which loads the request's **primary `schema_name`**
   only. A cross-schema project's golden query references tables in a *secondary* schema
   (`seagate_ops`) that are absent → fails Stage A → `recalled=0`. (E16: "Loaded 5 dataset(s)"
   = `seagate_core` only; `seagate_ops` golden refs dropped.)

2. **It is a relevance-ranked, bounded *grounding* subset — not an access set.**
   `get_context` ([context/superset_metadata.py:51](context/superset_metadata.py#L51)) returns
   `retrieve_schema_context(...).context` — the **top-K ranked** datasets for the question,
   not all tables the user can reach. So even **within one schema**, a table that retrieval
   didn't rank in is treated as "inaccessible" and any pair referencing it is dropped.
   F2 conflated *"what we grounded on"* with *"what the user can access."*

**Net:** the fail-closed RBAC filter is correct; its **input set is wrong** (too small,
wrong axis). The fix is a *scope* fix, not a relaxation of the filter.

### Why the manifest is multi-schema but the access set isn't
`materialize_request_semantic_project` ([wren_runtime.py](semantic_layer/wren_runtime.py)) resolves
the project with its full `schema_names` and materializes a multi-schema manifest; golden refs
resolve correctly across schemas (`golden_query_refs` → manifest `tableReference`). But the
recall access set is built from the single-schema, ranked `context.datasets`. The two must match.

### Security note (must preserve)
The access set is access-control-bearing: it gates whether another user's learned SQL **text**
(F1 shared memory) or a project golden's SQL is surfaced to the requester (the leak F2 is
stricter-than-vendors about — see spec §2). The fix must build the set from a **per-user
access-filtered** source, not from the manifest alone (which carries no per-user proof).

---

## Fix: a dedicated, access-filtered, multi-schema accessible-table set for recall

Build the F2 accessible set from the user's **access-filtered datasets across the resolved
project's full `schema_names`**, decoupled from the grounding `context.datasets`.

- Source: `superset_client.list_datasets(database_id, catalog, schema, limit)`
  ([client.py:125](integrations/superset/client.py#L125)) — runs under the requester's Superset
  auth, so results are **per-user access-filtered** (a schema/table the user can't reach never
  appears → Stage A still fails closed for genuinely-inaccessible tables). Available on both
  graphs as `self.superset_client` ([graph.py:266](graph.py#L266), [conversation_graph.py:220](conversation_graph.py#L220)).
- Scope: the resolved `project.schema_names` (multi-schema); fall back to `[request.schema_name]`
  when no project. This matches the manifest the golden refs resolve against.
- Unpruned: pass a high `limit` (a configured cap, not the default 8) so the set is the full
  accessible table list, not a ranked subset.

### Decision points
- **DP-1 — access philosophy (the load-bearing decision).**
  - *(B) Re-prove per user across project schemas (RECOMMENDED).* Access set = union of the
    requester's access-filtered datasets over `project.schema_names`. Keeps F2's deliberately
    stricter-than-vendors posture (a user who can't reach `seagate_ops` still won't see an
    ops-referencing golden's SQL text). Aligns with `SemanticAccessService.require_schema_set_permission`
    ([access.py:129](semantic_layer/access.py#L129)): "a schema that cannot be proven contributes nothing."
  - *(A) Project-trust / manifest-derived.* Access set = the project's onboarded tables
    (manifest `tableReference`). Simpler, no extra loads, matches **Cortex Analyst** (which
    *requires* SELECT on all model tables to use the model) and **Genie** (example text shown
    regardless of per-table grants). But it **relaxes** our leak protection. Choose only if the
    project-trust model is explicitly accepted.
  - **Recommendation: (B).** It fixes the scope without weakening the security invariant the
    report says to keep. (A) is a deliberate policy relaxation; defer unless requested.
- **DP-2 — source of the accessible set.** `superset_client.list_datasets` per project schema
  (RECOMMENDED — access-filtered, already in the graph). Alternative: wire `SemanticAccessService`
  into the graph and call `require_schema_set_permission` (canonical, but new dependency + more
  invasive). Manifest `tableReference` is rejected under DP-1(B) (no per-user proof).
- **DP-3 — `onboarded_tables` for Stage B/C.** Keep `onboarded = accessible` for v1 (preserves
  current behavior; no `semantic_sql` stripping). Enhancement: derive `onboarded` from the
  manifest's modeled tables so Stage B small-down-rank / Stage C native-only fire precisely.
  Defer unless needed.

### Requirements
- R-A: a cross-schema golden whose refs the user **can** access is recalled (Stage A passes).
- R-B: a golden referencing a schema the user **cannot** access is still dropped (fail-closed
  preserved) — DP-1(B).
- R-C: single-schema recall is unchanged in outcome (the positive control still recalls).
- R-D: no extra dataset loads when recall is inert (memory store `none` **and** no project) —
  gate the computation.
- R-E: bounded cost — at most one `list_datasets` per project schema, behind the existing client
  cache; a schema with more tables than the cap degrades closed (under-recall, never over-share).

---

## Sequential checklist

### Task 1 — `build_recall_access` accepts an explicit dataset set  ⟶ *foundation*
- **Entrypoint:** [memory_store.py](semantic_layer/memory_store.py) `build_recall_access(datasets)`.
- **Do:** it already maps `datasets` → `RecallAccess` by `(schema_name, table_name)`. No change to
  its body needed; the fix is *what datasets are passed in*. Optionally add a thin
  `build_recall_access_from_tables(pairs)` if a non-DatasetSummary source is ever used. Keep the
  duck-typed contract (`schema_name`/`table_name`).
- **Test:** existing `test_build_recall_access_from_datasets` still green.

### Task 2 — Graph helper: load the multi-schema accessible set  ⟶ *deps: superset_client*
- **Entrypoint:** [graph.py](graph.py) — add `_load_recall_access(self, request, project) -> RecallAccess`.
- **Do:**
  - Determine the schema set: `project.schema_names` if a project resolved, else
    `request.effective_schema_names or [request.schema_name]`.
  - If recall is inert (`self.config.wren_memory_store == "none"` **and** no project), return an
    empty `RecallAccess()` and skip the loads (R-D). (Empty access ⇒ Stage A drops everything,
    which is correct when there's nothing to recall; but only reach here when recall is off.)
  - For each schema, `self.superset_client.list_datasets(database_id=..., catalog_name=...,
    schema_name=schema, limit=<cap>)`; collect `(schema_name, table_name)`. Use a generous cap
    (e.g. `max(wren_schema_table_candidate_limit, wren_schema_total_candidate_limit, max_context_datasets)`,
    mirroring [context/superset_metadata.py:94](context/superset_metadata.py#L94)).
  - Build `RecallAccess(accessible_tables=union, project_schemas=schemas, onboarded_tables=union)`
    (DP-3 v1). Best-effort: on any `list_datasets` error, degrade to
    `build_recall_access(state["context"].datasets)` (no worse than today).
- **Requirement:** access-filtered per user (list_datasets uses the request auth) — DP-1(B).
- **Test (unit, fake superset_client):** union spans all project schemas; a schema the fake
  client returns empty for (simulating no access) contributes nothing.

### Task 3 — Stash the resolved project (schema_names) on state  ⟶ *deps: Task 2*
- **Entrypoint:** [graph.py:478](graph.py#L478) `_load_wren_context` (where `project` is resolved
  from `materialize_request_semantic_project`).
- **Do:** add the built `RecallAccess` (or the project `schema_names`) to the returned state, e.g.
  `state["recall_access"] = self._load_recall_access(request, project)`. Add `recall_access:
  RecallAccess` to the `AgentState` TypedDict ([graph.py:214](graph.py#L214)).
  - Computing it here reuses the already-resolved `project` (no second resolve) and runs once per
    turn, before `draft_sql`.
- **Test:** state carries a multi-schema `recall_access` when the project spans schemas.

### Task 4 — Draft node uses the stashed access set  ⟶ *deps: Task 3*
- **Entrypoint:** [graph.py:608](graph.py#L608) `_draft_sql`.
- **Do:** replace `access = build_recall_access(context.datasets)` with
  `access = state.get("recall_access") or build_recall_access(context.datasets)`. Both
  `memory.recall_examples(...)` and `recall_golden_queries(...)` already take `access`.
- **Test (integration):** a cross-schema golden whose refs are in the (multi-schema) access set is
  recalled; a golden referencing a schema absent from the access set is dropped.

### Task 5 — Mirror the fix in the conversation graph  ⟶ *deps: Task 2 pattern*
- **Entrypoints:** [conversation_graph.py:907](conversation_graph.py#L907) (project resolution /
  `_load_wren_context` equivalent) and [conversation_graph.py:1080](conversation_graph.py#L1080)
  (`build_recall_access(state["context"].datasets)`).
- **Do:** add the same `_load_recall_access` (or share a module helper), stash on
  `ConversationState` ([conversation_graph.py:160](conversation_graph.py#L160)), and use it at 1080.
  Use `request.scope` for database_id/catalog and the resolved project's `schema_names`.
- **Requirement:** keep the existing `state.get("recalled_examples")` short-circuit ([:1078](conversation_graph.py#L1078)).
- **Test:** conversation-path cross-schema golden recalls.

### Task 6 — Tests + lint
- **Unit:** `_load_recall_access` multi-schema union + access-filtering (fake client); empty/inert gate.
- **Integration:** extend `test_graph.py` / `test_conversation_graph.py` — cross-schema golden recalled;
  inaccessible-schema golden dropped (fail-closed preserved); single-schema unchanged (R-C).
- **Re-run E16** (`run_eval_v3_golden.py`) on `seagate_multi`: expect cross-schema `recalled` ≥ 1 and
  the "verified" signal to fire on Q16/Q17 (the report's acceptance criterion).
- `pre-commit run --all-files`; ruff/mypy on touched modules.

---

## Risks & mitigations
| Risk | Mitigation | Task |
|---|---|---|
| Extra per-schema `list_datasets` loads (request-storm class — see [[mdl-lab-request-storm]]) | Gate on recall-active (R-D); bounded by project schema count; rely on the client/auth cache | 2 |
| A schema with more tables than the cap → some accessible tables omitted | Degrade closed (under-recall, never over-share); cap is generous; log when truncated | 2 |
| Broadening the access set surfaces more **memory** pairs (F1) across the project's schemas | Intended (that *is* DB-scoped sharing); still per-user access-filtered, so no leak | 4/5 |
| DP-1(A) chosen by mistake → relaxes leak protection | Default to (B); require explicit sign-off for (A) | DP-1 |
| Stage B/C inert because `onboarded=accessible` | Acceptable v1 (security stages A unaffected); derive `onboarded` from manifest later | DP-3 |

## Blockers & dependencies
- **B1:** Task 4/5 depend on Task 2+3 (the access set must be built and stashed first).
- **B2:** DP-1 must be decided before Task 2 (it determines the *source* of the access set).
- **Dep:** no migration, no schema change — pure query-path wiring. Independent of the views work
  (R2/R3/R4 in the eval report), which is a separate effort.

## Out of scope (this fix)
- R2 (surface views to retrieval), R3 (non-atomic view activation), R4 (column-grounded view
  authoring) — tracked separately in RESULTS_v3.md.
- Making the *grounding* `context.datasets` multi-schema (the broader cross-schema query-time
  work, [[cross-schema-query-time]]) — this fix deliberately builds a **separate** access set and
  leaves grounding untouched to limit blast radius.
