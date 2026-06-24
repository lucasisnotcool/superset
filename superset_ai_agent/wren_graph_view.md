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

# Wren Graph View — Combined Database + MDL Visualization Plan

A graph view that renders the **physical database schema** (tables, columns,
PK/FK relationships) and overlays the **Wren MDL semantic layer** (models,
relationships, calculated fields, metrics, cubes) on top of it — so an operator
can see, at a glance, *what the semantic layer has captured, whether it is
grounded in real tables, and what the agent actually used to answer a question.*

It complements [`wren_full.md`](wren_full.md) (MDL authoring/storage shape) and
[`wren_enrich_and_retrieve.md`](wren_enrich_and_retrieve.md) (the enrichment +
retrieval pipelines, incl. Phase C5 authoring UI). This document is the design
for a **new, read-first visualization surface**; it adds no MDL semantics and
does not change the pipelines.

Status legend: `[TODO]` not started · `[WIP]` in progress · `[DONE]`
source-backed and test-verified · `[BLOCKED]` waiting on a decision/dependency.

Source references are relative to `superset_ai_agent/` (frontend paths via
`../superset-frontend/`). All anchors verified against source on 2026-06-24.

---

## 0. North-star constraints (read first)

These shape every decision below; they come directly from the feature owner.

1. **Large schemas are the norm, not the exception.** Wren onboarding attempts to
   model **all** tables, so a schema (and therefore the MDL) can carry hundreds to
   thousands of tables. **Defaulting to "modeled tables only" is not a valid
   reduction strategy** — the modeled set can itself be the whole warehouse.
2. **Start small, grow on demand.** Open on a small **seed** (default ~10 tables),
   then let the user **search** to jump anywhere and **expand** outward
   (neighborhood-by-neighborhood) as they explore. Never render the whole graph
   eagerly.
3. **Zero cost when the view is not loaded.** Opening SQL Lab / the AI panel must
   be byte-for-byte unaffected: no graph code in the hot bundle, no network calls,
   no timers, no ECharts, until the user explicitly opens the Graph tab.
4. **Prefer the performance-preserving option at every fork** — even at some cost
   to feature richness. Degrade-closed, cap-and-window, lazy-everything.

---

## 1. Concept & value

The semantic layer is **invisible today**: it lives as camelCase JSON in an ACE
editor ([`SemanticLayerEditor/index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)).
A graph makes the three questions operators actually have legible:

- **Coverage** — what is modeled vs. what physically exists (gaps → enrichment).
- **Grounding / correctness** — hallucinated columns, dropped columns, and bad
  relationship references (the failure classes hardened in `wren_full.md` W5/E5)
  become **red nodes/edges** instead of a JSON error list.
- **Agent trust / debugging** — the API already returns, per answer,
  `WrenContextArtifact.matched_models` and
  `WrenRetrievalArtifact.candidate_table_names`
  ([`api.ts:84-122`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L84));
  highlighting *which tables/models the agent used* on the graph is a
  high-value trust surface built on data that already exists.

**Why the overlay is differentiated:** most analytic warehouses (BigQuery,
Snowflake, Redshift) do not enforce/expose physical FKs, so the physical edge set
is sparse and the **MDL relationships are frequently the only join knowledge that
exists**. Drawing MDL joins over an FK-less schema is the high-signal case.

---

## 2. Existing infrastructure (source-backed — what we reuse)

| Need | What exists | Source |
| --- | --- | --- |
| **Graph rendering** | ECharts 5.6 native `graph` series — force/circular layout, `roam` (pan/zoom), categories, edge symbols, tooltips. Already a dependency. | `package.json`; precedent for raw ECharts outside the chart pipeline: [`ZoomConfigsChart.tsx`](../superset-frontend/src/explore/components/controls/ZoomConfigControl/ZoomConfigsChart.tsx) |
| **Code-splitting** | `React.lazy` is the house pattern for deferring heavy routes/panels. | [`views/routes.tsx`](../superset-frontend/src/views/routes.tsx) |
| **Cheap table list** | `GET /api/v1/database/<id>/tables/` → `{count, result:[{value,type,…}]}` (names + table/view kind), `q` rison supports `catalog_name`/`schema_name`/`force`. **One call, no per-table cost.** | [`databases/api.py:835`](../superset/databases/api.py#L835) |
| **Per-table detail (edges)** | `GET /api/v1/database/<id>/table_metadata/?table=&schema=&catalog=` → `{columns[], primaryKey, foreignKeys[{column_names, referred_schema, referred_table, referred_columns}], indexes[]}`. **One call per table; the FK rows are the cross-table edges.** | [`databases/api.py:1025`](../superset/databases/api.py#L1025); shape [`databases/schemas.py:678-704`](../superset/databases/schemas.py#L678); impl [`databases/utils.py:65-110`](../superset/databases/utils.py#L65) |
| **Column key iconography** | `pk`→key, `fk`→link, `index`→bookmark, with tooltips — reuse directly for node/detail rendering. | [`ColumnElement/index.tsx`](../superset-frontend/src/SqlLab/components/ColumnElement/index.tsx) |
| **Table-metadata fetch layer** | SQL Lab already fetches `tables`/`table_metadata` via `SupersetClient`; reuse its hooks rather than re-implementing. | `SqlEditorLeftBar`, `TableExploreTree`, `TablePreview` |
| **MDL data (no new endpoint)** | The editor already resolves a project and `listMdlFiles` returns `MdlFile.content` = camelCase manifest JSON; the whole manifest is small and already client-side. | [`api.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts) `listMdlFiles`; shape [`mdl_schema.py`](semantic_layer/mdl_schema.py) |
| **Agent grounding data** | Per-answer `matched_models` / `candidate_table_names` already on the artifact. | [`api.ts:84-122`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L84) |

**Gaps in existing infra (deliberately not blockers for G1/G2):**
- No ERD / schema-graph feature exists today — this is net-new but built from the
  primitives above.
- No reactflow/cytoscape/dagre present. Adding one is a *governed new dependency*
  (same friction `wren_enrich_and_retrieve.md` §9 hit with `ajv`/`jsonc-parser`).
  **ECharts is the zero-new-dep path and the default; reactflow is an explicit
  later upgrade only if box-style ERD layout becomes a hard requirement.**
- `tables` returns **no pagination** (full name list). That list is cheap
  (strings), but for pathological schemas we filter/window client-side (§4).
- `table_metadata` is **per-table and introspection-costly**; this is the cost we
  engineer around (§4). An optional bulk endpoint is deferred (§6 G4).

### 2.1 Pre-flight: verified anchors & findings (2026-06-24)

Implementation-readiness pass over current source (the codebase moved as C0
progressed). Each plan assumption re-checked:

| Anchor | Verified state | Implication |
| --- | --- | --- |
| Editor tabs | `ContentTabs` with `items=[{key:'models'},{key:'instructions'}]` ([`SemanticLayerEditor/index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)) | Add a third lazy `'graph'` item (G0.1); no structural change. |
| Table fetch/cache | [`src/hooks/apiResources/tables.ts`](../superset-frontend/src/hooks/apiResources/tables.ts) RTK Query: `useLazyTablesQuery`, `useLazyTableMetadataQuery`, `tableEndpoints.tableMetadata.initiate`, `tableApiUtil`; typed `Table`/`Column`/`TableMetaData` | **Reuse — don't hand-roll caching/dedup** (G0.2). Graph adds only throttle/LRU/reverse-FK/seed. |
| Column key icons | `ColumnElement` (pk/fk/index) + `TablePreview` consume the same metadata | Reuse for the detail panel (G1.3). |
| ECharts minimal import | plugin imports types from `echarts/core` + `echarts/charts` (`GraphChart`); `ZoomConfigsChart` uses full `echarts` (lazy-chunked) | Minimal `echarts/core`+`GraphChart` import is precedented (D1). |
| Seed signals | `state.sqlLab.tables` (Redux) + `artifact.wren_context.matched_models` (in panel) | Both zero-cost (D11, §4.8). |
| Instruction scope | `instruction_scope_hash` drops `dataset_ids` ([`store.py:178`](semantic_layer/store.py#L178)); wired in graph/enrich/routes | X8 needs no scope work; §7.3 confirmed. |
| MDL retrieval scope | project-keyed `"{owner}:{project_id}"` ([`schema_retriever.py:573`](semantic_layer/schema_retriever.py#L573)) | dataset-independent already; §7.3 confirmed. |
| X2 plumbing | `AgentQueryRequest` ([`schemas.py:34`](schemas.py#L34)); retrieval refactored to `build_unified_context` (C1.2/C1.3) + `select_relevant_models`/`llm_select_models` ([`runtime.py`](semantic_layer/runtime.py), [`graph.py`](graph.py)) | Add `focus_tables` and thread as a boost into `build_unified_context` (G3.3). |
| **Finding — X3 mapping gap** | `MdlValidationMessage` = `{line,column,severity,message,code}`, **no entity ref** ([`schemas.py:168`](semantic_layer/schemas.py#L168)); entity only in message text | Best-effort name+`code` mapping first, or small additive backend field (G2.2). Not C0-contended. |

**Net:** no blocking drift. The biggest change vs. the original plan is a
*simplification* — G0.2 rides the existing RTK table resource instead of a custom
cache. The only new finding is the X3 validation→node mapping gap (G2.2).

---

## 3. Data model & the overlay mapping

### 3.1 Physical (from `table_metadata`)
- **Table node** — `{db_id, catalog, schema, table, kind: table|view}`.
- **Column** (default: *not* a graph node — see LOD, §4.4) — `{name, type, keys[]}`
  where `keys ∈ {pk, fk, index}`.
- **Physical edge** — one per FK: `source=table`, `target=referred_table`,
  carrying `(column_names → referred_columns)`. Reverse edges ("who references
  me") are built from an incrementally-populated reverse index (§4.5).

### 3.2 Semantic (from the MDL manifest — [`mdl_schema.py`](semantic_layer/mdl_schema.py))
- **Model** → joins to a physical table by `tableReference{catalog,schema,table}`.
- **MDL column** → `{name, type, isCalculated, expression, relationship, notNull,
  properties}`. Three sub-kinds:
  - *physical* — maps 1:1 to a catalog column;
  - *calculated field* (`isCalculated=true`, `expression`) — semantic-only;
  - *relationship column* (`relationship` set) — renders as / annotates an edge.
- **MDL relationship** → `{name, models[2], joinType ∈ {ONE_TO_ONE, ONE_TO_MANY,
  MANY_TO_ONE, MANY_TO_MANY}, condition}` — a **semantic edge** with cardinality.
- **Metric** → `{name, baseObject, expression}` — badge on the `baseObject` model.
- **Cube** → `{name, baseObject, measures, dimensions, timeDimensions}` — badge.
- **View** → `{name, statement}` — listed in a side panel (no physical table).

### 3.3 The mapping (the heart of the combined view)

| Physical element | Semantic overlay |
| --- | --- |
| table node | MDL `model` joined by `tableReference` → table is **modeled** (styled), else **unmodeled** (dimmed) |
| column (detail panel) | MDL column by name (physical 1:1; calculated = semantic-only marker; relationship = edge marker) |
| FK edge | MDL `relationship` edge with `joinType` cardinality — drawn in a **distinct style**; divergence between the two edge sets is surfaced, not hidden |
| — | `metric`/`cube` badges on model nodes; descriptions/synonyms enrich tooltips |
| MDL reference to a missing physical column/table | **error-highlighted** node/edge (reuses validation results) |

**Cheap-vs-expensive asymmetry to exploit:** the *entire MDL is already in
memory*, so the **semantic** graph (model nodes + relationship edges + cardinality
+ badges) can be computed and drawn **instantly with zero network calls**. Only
the **physical hydration** (columns/PK/FK) is lazy and expensive. The combined
view therefore renders the semantic skeleton immediately and *fills in physical
grounding progressively* as `table_metadata` arrives for the seed/expanded set.

---

## 4. Performance-first architecture (the centerpiece)

### 4.1 Zero cost when the view is not loaded `[D1]`
- The Graph tab body is `const SchemaGraph = React.lazy(() => import('./SchemaGraph/SchemaGraph'))`,
  rendered inside `<Suspense>`. The `SchemaGraph` module is the **only** place
  ECharts is imported, and it imports the **minimal** ECharts surface
  (`echarts/core` + `GraphChart` + the few components it needs via `use()`), not
  the full bundle. Result: SQL Lab / AI-panel hot path carries **no** graph code,
  no ECharts, until the tab is first opened.
- **No work on mount of the editor.** The tab pane is not rendered until selected
  (antd `Tabs` lazy-mounts inactive panes), so even the lazy import is not
  triggered until the user clicks "Graph".
- **No always-on side effects.** No polling, no EventSource, no resize observers
  outside the mounted graph. On tab-away the ECharts instance is `dispose()`d;
  in-memory caches are retained (cheap) but inert.
- **Acceptance gate:** a bundle-size assertion / manual `source-map-explorer`
  check that the SQL Lab entry chunk does not grow, and that `echarts` lands in a
  separate async chunk loaded only on tab open.

### 4.2 The seed → search → expand loading model `[D2]`
Three states, each bounded:

1. **Seed (open).** Fetch the **cheap table-name universe once** (`/tables/`,
   names only — even thousands of strings is small) and the **MDL manifest**
   (already loaded). Choose a **seed set** of `SEED_LIMIT` (default **10**) table
   nodes by this priority (first non-empty wins) — all sources are **zero-cost,
   already-in-memory** (see §4.8 on why we avoid the costly path):
   1. **Most recently queried by the agent** — `matched_models` ∪
      `candidate_table_names` from the latest conversation artifact the AI panel
      already holds in memory ([`api.ts:84-122`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L84));
   2. **Most recently used by the user** — the tables the user has open in the SQL
      Lab left bar, `state.sqlLab.tables` (Redux), filtered to the current
      `{dbId,catalog,schema}` scope and ordered most-recent-first;
   3. **Fallback** — the first `SEED_LIMIT` tables from the `/tables/` list
      (deterministic).
   Render the seed nodes + any edges **among them**. Hydrate `table_metadata`
   only for the seed (≤ `SEED_LIMIT` calls, concurrency-capped — §4.6).
2. **Search (jump).** A type-ahead over the in-memory name universe (no network).
   Selecting a table **focuses** it: add the node, center/zoom to it, load its
   neighborhood. This is how a user reaches anything in a huge schema without
   loading it all.
3. **Expand (grow).** Each node exposes an **expand** affordance. Expanding loads
   the node's **1-hop neighborhood**:
   - *semantic neighbors* — from MDL `relationships` where the model
     participates (in memory, instant);
   - *physical neighbors* — from the node's `table_metadata.foreignKeys`
     (`referred_table`) plus reverse-FKs known from the incremental index (§4.5).
   New neighbor nodes are added **collapsed** (no metadata) and only hydrated when
   themselves expanded or focused.

> **On "expand as the user scrolls around":** panning/zooming (`roam`) explores
> the *already-loaded* subgraph and is free. We deliberately make growth
> **explicit** (expand / search), not implicit on every viewport move, because
> (a) force-layout shifts nodes so "what's in view" is unstable, and (b)
> implicit auto-load is the classic way to DoS the metadata API. An **optional**
> "expand all visible" button and an opt-in idle-time prefetch (hard-capped, see
> §4.3) cover the "it grows as I roam" feel without the runaway risk.

### 4.3 Hard caps & windowing `[D3]`
- `MAX_NODES` (default **200**) rendered table nodes. Expansion that would exceed
  it **collapses the least-recently-touched** nodes (LRU) back to off-graph, with
  a non-blocking notice ("focus or filter to see more"). Force layout cost grows
  super-linearly; the cap is the FPS guarantee.
- `MAX_EDGES` companion cap; above it, **bundle** parallel edges and drop
  index-only edges first.
- `SEED_LIMIT` (10), `EXPAND_FANOUT_CAP` (e.g. 25 neighbors per expand; beyond
  that, require search) keep any single action bounded.
- All limits are **frontend constants** (a `graphConfig.ts`) for v1 — **no
  backend `config.py` change** (which keeps us off the C0-contended file, §10).

### 4.4 Level-of-detail (LOD) — columns are not nodes by default `[D4]`
- **Default LOD = table-level.** Nodes are tables/models; edges are FK + MDL
  relationships at the **model level** (trivially derived from
  `relationship.models[2]` / FK `referred_table`). Columns appear in a **detail
  side panel** when a node is selected (reuse `ColumnElement`), **not** as graph
  nodes. Columns-as-nodes is the single biggest blow-up for large schemas; this is
  the most important performance decision.
- **Column LOD on demand.** For a *focused/pinned* table (bounded to ≤ a few),
  optionally expand its columns as child nodes with column-level edges (parsed
  from FK `column_names→referred_columns`; MDL relationship `condition` parsing is
  deferred). Always scoped to the focused neighborhood, never global.

### 4.5 Incremental reverse-FK index `[D5]`
FKs are directional in `table_metadata` (a table knows what *it* references, not
who references it). Maintain a client-side `reverseFk: Map<table, FkEdge[]>` that
is **populated as each table's metadata loads**. Consequence: a physical edge
appears once **either** endpoint has been hydrated; "who references me" gets richer
as the user explores. This avoids a global FK crawl. (A future bulk endpoint, G4,
would supply the full adjacency up front.)

### 4.6 Fetch discipline `[D6]`
- **In-memory cache** `Map<"{db}/{catalog}/{schema}/{table}", TableMetadata>`,
  populated on demand, retained across tab switches **within the session only**
  (no `sessionStorage`/durable persistence — Q2); `force` flag for manual refresh.
- **Concurrency-limited queue** (`MAX_INFLIGHT`, default 5) for `table_metadata`
  so expanding a hub never fires 50 parallel requests.
- **AbortController** cancels in-flight loads for nodes evicted by the LRU cap or
  superseded by a new focus.
- The name universe and the MDL manifest are fetched **once per scope** and
  cached; re-entering the tab re-uses them.

### 4.7 Render discipline `[D7]`
- Single ECharts instance; **incremental `setOption`** (merge) on
  expand/collapse rather than full re-renders; `notMerge:false`.
- Force layout **frozen after stabilization** (`layoutAnimation:false` once
  settled / use `fixed` coordinates after first layout) so adding a neighbor
  doesn't re-jiggle the whole graph; new nodes seed near their parent.
- Debounce search/expand handlers; throttle `roam` listeners (only used for an
  optional minimap / LOD switch, not for loading).

### 4.8 Seed signal cost — why we avoid "parse the last query" `[D11]`
The owner's intent (Q1) is "seed from the most recently queried table, by agent or
user, else first in list." The feasible, **zero-cost** reading of "recently
queried" is the **already-structured** signals:
- the **agent** path is free — `wren_context.matched_models` /
  `candidate_table_names` are on the conversation artifact the panel already holds;
- the **user** path is free — `state.sqlLab.tables` is the set of tables the user
  has opened in the SQL Lab left bar (Redux), which is exactly "tables I'm working
  with," already scoped by `{dbId,catalog,schema}`.

What we **deliberately do not do**: take the user's last *executed SQL*
(`queries[latestQueryId].sql` / `queryEditor.sql`) and **parse it** to extract
referenced tables. That requires a SQL parser (a new dependency / fragility) or a
backend round-trip, and runs on every tab open — the exact performance cost Q1
warned about. The left-bar `tables` array is the parser-free proxy for the same
intent, so the seed never pays a parse or a network call. (If precise last-SQL
table extraction is ever wanted, it belongs behind the deferred parse seam, not
the seed path.)

---

## 5. Decisions (with reasoning)

| # | Decision | Reasoning | Rejected alternative |
| --- | --- | --- | --- |
| D1 | **Lazy-load the whole surface** (React.lazy + minimal ECharts import) | North-star #3 — unloaded cost must be zero; ECharts is heavy. | Static import — taxes the SQL Lab bundle for everyone. |
| D2 | **Seed (≈10) → search → explicit expand**, never eager-all | North-star #1/#2 — the modeled set can be the whole warehouse; bounded growth is the only scalable model. | "Modeled tables only" default (owner ruled out); render-all (DoS). |
| D11 | **Seed from zero-cost structured signals (agent `matched_models` / user left-bar `tables`), never from SQL parsing** | Q1 decision; both signals are already in memory, so the seed pays no parse and no network call (§4.8). | Parse the last executed SQL to extract tables — new dep/fragility, runs every open. |
| D12 | **Stable node-id scheme (S-A) from day one** | Every integration addresses nodes by id; baking it into G0.3 means extensions attach without graph-core changes. | Ad-hoc ids per feature — churn + brittle cross-feature references. |
| D13 | **A `GraphController` command/event API (S-B)** | Highlight/focus/select/action must be drivable by other panels (chat, validation) without reaching into ECharts. | Each feature pokes the chart instance — tight coupling, no reuse. |
| D14 | **Decoration-field node/edge model (S-C), default-absent** | Lets validation/status/provenance/etc. ride the same node objects, rendered only when populated — zero cost until a feature lands. | A bespoke render path per overlay — duplicated layout/hit-testing. |
| D3 | **Hard node/edge caps + LRU windowing** | Force layout degrades super-linearly; caps are the FPS guarantee. | Unbounded canvas — unusable past a few hundred nodes. |
| D4 | **Table-level LOD by default; columns in a side panel** | Columns-as-nodes is the dominant blow-up; tables+model edges stay legible at scale. | Columns-as-nodes globally (the concept's literal form) — collapses on big schemas. |
| D5 | **Incremental reverse-FK index, no global crawl** | A global FK scan = N metadata calls up front; defeats laziness. | Pre-crawl all FKs — exactly the cost we avoid. |
| D6 | **On-demand cache + concurrency cap + abort** | Bounds API fan-out and keeps the UI responsive while exploring. | Fire-and-forget per node — request storms on hubs. |
| D7 | **Compute the semantic graph fully client-side; hydrate physical lazily** | MDL is small & already in memory; only physical detail is costly. | Treat both layers as remote — needless calls for data we hold. |
| D8 | **Frontend constants for all limits in v1 (no `config.py`)** | Avoids the C0-contended backend `config.py`; limits are UX tunables. | Backend flags now — conflicts with in-flight C0 work for no benefit. |
| D9 | **ECharts (zero new dep); reactflow only as a later, governed upgrade** | Matches the repo's dependency-governance posture; ships without approval friction. | Add reactflow/cytoscape now — new-dependency review + bundle cost. |
| D10 | **Read-first; the only write action (G3 "add to MDL") reuses enrichment + `canWrite`** | Keeps the surface safe and scoped; no new mutation paths. | A full graph MDL editor — large surface, out of scope. |

---

## 6. Phased plan (dependency-ordered)

Each item: **goal · files · depends-on · acceptance**. Frontend-only through G3.

### Phase G0 — Lazy scaffolding + perf harness `[TODO]`
- [ ] **G0.1 — Graph tab, lazy-mounted, zero hot-bundle cost** `P1`
  - Goal: add a third tab ("Graph") to the editor that `React.lazy`-loads
    `SchemaGraph`; nothing loads until the tab is opened.
  - Files: [`SemanticLayerEditor/index.tsx`](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/index.tsx)
    (add tab + `Suspense` fallback), new `SemanticLayerEditor/SchemaGraph/` dir.
  - Depends-on: none.
  - Acceptance: SQL Lab entry chunk unchanged (bundle check); ECharts appears only
    in an async chunk fetched on tab open; opening the editor issues **no** graph
    network calls.
- [ ] **G0.2 — `graphConfig.ts` constants + data layer over the existing RTK resource** `P1`
  - Goal: `SEED_LIMIT`, `MAX_NODES`, `MAX_EDGES`, `EXPAND_FANOUT_CAP`,
    `MAX_INFLIGHT`; a `useSchemaGraphData` hook for the name-universe fetch, the
    concurrency throttle, the LRU window, and the reverse-FK index.
  - **Reuse (verified):** fetching + caching + dedup + `force` refresh are already
    provided by [`src/hooks/apiResources/tables.ts`](../superset-frontend/src/hooks/apiResources/tables.ts)
    (RTK Query): `useLazyTablesQuery` (the cheap name universe),
    `useLazyTableMetadataQuery` / `tableEndpoints.tableMetadata.initiate(...)` for
    **dynamic-N** per-table fetches dispatched imperatively, and the exported
    `Table` / `Column` (`ColumnKeyTypeType = 'pk'|'fk'|'index'`) / `TableMetaData`
    types. **Do not hand-roll a metadata cache** — RTK caches and dedups identical
    requests; the graph layer adds only what RTK does not: a **concurrency throttle**
    (`MAX_INFLIGHT`), **LRU windowing** (`MAX_NODES`), the **reverse-FK index**, and
    **seed selection**. Abort-on-evict = unsubscribe the RTK query.
  - Files: `SchemaGraph/graphConfig.ts`, `SchemaGraph/useSchemaGraphData.ts`
    (wraps `tables.ts`); no new `api.ts` wrappers needed for physical data.
  - Depends-on: G0.1.
  - Acceptance: unit tests for the throttle (≤ `MAX_INFLIGHT` concurrent initiates),
    LRU eviction (+ unsubscribe), reverse-FK index population, and seed selection;
    no bespoke metadata cache introduced (RTK is the cache).
- [ ] **G0.3 — Generic integration seams (prerequisite for §7)** `P1`
  - Goal: bake the three seams every extension depends on into graph-core, so
    later features attach **without graph-core changes** (full spec in §7.1):
    - **S-A — stable node IDs:** physical `phys:{catalog}.{schema}.{table}`,
      semantic `mdl:{modelName}`; one resolver maps an MDL entity (model/column/
      relationship by name) ⇄ node id. Edges get stable ids too.
    - **S-B — command/event API:** a `GraphController` handle exposing
      `highlightNodes(ids, style)`, `focusNodes(ids)`, `addNodes(ids)`, plus
      callbacks `onNodeSelect`, `onNodeAction(id, action)`, `onSelectionQuery(ids)`.
    - **S-C — decoration model:** the node/edge type carries optional, integration-
      owned fields `{ validation?, status?, agentUsage?, provenance?, instructions?,
      types? }`, rendered when present and ignored when absent.
  - Files: `SchemaGraph/types.ts` (node/edge + decoration model), `SchemaGraph/ids.ts`
    (resolver), `SchemaGraph/GraphController.ts`.
  - Depends-on: G0.2.
  - Acceptance: id resolver round-trips MDL entity⇄node id (unit-tested);
    `GraphController` highlight/focus drives ECharts without a full re-render;
    decoration fields default-absent and render-safe.
- [ ] **G0.4 — Shared MDL manifest state (prerequisite for live overlay)** `P2`
  - Goal: lift the parsed MDL manifest to a shared source in `SemanticLayerEditor`
    so the **Models editor tab and the Graph tab read one object** — edits reflect
    in the overlay without a refetch (secures the DF2-adjacent "live editing"
    seam, §7.2 item E6).
  - Files: `SemanticLayerEditor/index.tsx` (shared hook/context),
    `SchemaGraph/mdlOverlay.ts` (consume shared state).
  - Depends-on: G0.1.
  - Acceptance: editing MDL in the Models tab updates the Graph overlay with no
    network call; no behavior change when the Graph tab is closed.

### Phase G1 — Physical schema graph `[TODO]`
- [ ] **G1.1 — Seed + render table-level physical graph** `P1`
  - Goal: open on a `SEED_LIMIT` seed (priority per §4.2); render table nodes
    (kind table/view) with PK/FK key badges; FK edges among loaded nodes; pan/zoom.
  - Files: `SchemaGraph/SchemaGraph.tsx`, `SchemaGraph/echartsOptions.ts`.
  - Depends-on: G0.2.
  - Acceptance: with a stubbed 1000-table universe, only seed metadata is fetched;
    FPS stays smooth (cap respected); RTL/interaction tests for seed render.
- [ ] **G1.2 — Search-to-focus + neighborhood expand + LRU windowing** `P1`
  - Goal: type-ahead jump (no network); expand node → 1-hop neighbors (capped);
    exceed `MAX_NODES` → evict LRU with a notice.
  - Files: `SchemaGraph/SchemaGraph.tsx`, `useSchemaGraphData.ts`.
  - Depends-on: G1.1.
  - Acceptance: expanding a hub fetches ≤ `EXPAND_FANOUT_CAP` and never exceeds
    `MAX_INFLIGHT`; node count never exceeds `MAX_NODES`; abort fires on eviction.
- [ ] **G1.3 — Column detail side panel + column LOD on focus** `P2`
  - Goal: selecting a node shows columns (reuse `ColumnElement` iconography);
    optional column-level child nodes for a focused table only.
  - Files: `SchemaGraph/NodeDetailPanel.tsx`.
  - Depends-on: G1.1.
  - Acceptance: columns render from cache (no extra call); column LOD bounded to
    the focused neighborhood.

### Phase G2 — MDL overlay (combined view) `[TODO]`
- [ ] **G2.1 — Parse manifest → semantic graph (instant, client-side)** `P1`
  - Goal: build model nodes + relationship edges (with `joinType` cardinality) +
    metric/cube badges from the in-memory manifest; **layer toggle** Physical /
    MDL / Combined.
  - Files: `SchemaGraph/mdlOverlay.ts`, `SchemaGraph/SchemaGraph.tsx`.
  - Depends-on: G1.1 (shares the canvas), MDL load (editor already has it).
  - Acceptance: semantic graph renders with **zero** network calls; cardinality
    shown via edge end-symbols (crow's-foot-style); combined view aligns model
    nodes onto their `tableReference` table nodes.
- [ ] **G2.2 — Coverage + grounding styling** `P1`
  - Goal: modeled vs. unmodeled dimming; calculated-field markers; **error
    highlight** for MDL references to missing physical columns/tables (reuse the
    validation results the editor already surfaces).
  - Files: `SchemaGraph/mdlOverlay.ts`, `SchemaGraph/echartsOptions.ts`.
  - Depends-on: G2.1, G1.3 (needs hydrated columns to detect missing refs).
  - **Finding (X3 mapping gap):** `MdlValidationMessage` carries only
    `{line, column, severity, message, code}` — **no structured entity ref**; the
    model/column name lives in the message *text* ([`schemas.py:168`](semantic_layer/schemas.py#L168);
    e.g. `code="duplicate_model"`, `message="Duplicate model name: {name}."`). So
    node-level highlighting needs either (a) **best-effort** mapping by `code` +
    name extracted from the message, or (b) a small **additive** backend field
    (`entity`/`model`/`column`) on `MdlValidationMessage` + the emit sites in
    `mdl_validator.py`. Both files are **not C0-contended**. Recommend shipping (a)
    for the common codes first; do (b) if precision is insufficient.
  - Acceptance: a hallucinated column ref shows red on the node (via (a) or (b));
    coverage legend explains the styles.
- [ ] **G2.3 — Seed/expand parity for the combined view** `P2`
  - Goal: the seed/search/expand model (§4.2) applies to model nodes too (since
    onboarding may model everything) — never render all models eagerly.
  - Files: `useSchemaGraphData.ts`, `mdlOverlay.ts`.
  - Depends-on: G2.1, G1.2.
  - Acceptance: a 1000-model manifest opens on the seed; expand grows the
    combined neighborhood within the same caps.

### Phase G3 — Agent grounding & actions `[TODO]`
- [ ] **G3.1 — Highlight what the agent used** `P2`
  - Goal: given the last answer's `matched_models` / `candidate_table_names`,
    highlight those nodes and offer "focus on these".
  - Files: `SchemaGraph/SchemaGraph.tsx`; consume artifact already on
    [`api.ts`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L84).
  - Depends-on: G1.1.
  - Acceptance: after a query, the used tables are visibly marked; clicking
    "focus" seeds the graph from them.
- [ ] **G3.2 — "Add to MDL / enrich" on unmodeled tables (write, gated)** `P3`
  - Goal: from an unmodeled table node, trigger the existing enrichment/onboarding
    path; gated by the project `canWrite`.
  - Files: `SchemaGraph/NodeDetailPanel.tsx`; reuse `onboard`/enrich APIs.
  - Depends-on: G2.2.
  - Acceptance: action hidden when read-only; success refreshes the overlay.
- [ ] **G3.3 — Semantic query tool (graph ⇄ chat, bidirectional)** `P2` *(flagship)*
  - Goal: select nodes on the graph → "Ask about these"; the selection is passed
    to the agent query as a table/model hint (a manual seed for R2's
    `select_relevant_models`), and the resulting answer's
    `matched_models`/`candidate_table_names` highlight back on the graph (reuses
    G3.1). Closes the loop your "highlight tables identified" example described.
  - Files / plumbing (verified anchors):
    - frontend: `SchemaGraph/SchemaGraph.tsx` (S-B `onSelectionQuery`),
      `AiAgentPanel/index.tsx` + `api.ts` `AgentQueryRequest` — add optional
      `focus_tables?: string[]`;
    - backend: `schemas.py` `AgentQueryRequest` ([:34](schemas.py#L34)) — add
      `focus_tables: list[str] = []`; thread into the context node
      ([`graph.py`](graph.py)) where retrieval was refactored to
      `build_unified_context` (C1.2/C1.3) — pass the focus set as a **boost/seed**
      so focused tables lead the merged list and survive table-selection
      (`select_relevant_models` heuristic / `llm_select_models` selector).
  - Depends-on: G0.3 (S-B), G3.1.
  - **Scope rule (§7.3, adopted Option 2):** the selection is a **retrieval focus
    hint** fed to `select_relevant_models` as a boost/seed — it **never** mutates
    `dataset_ids` or the scope identity, so no recall (instructions/MDL/memory) is
    re-partitioned. This makes X2 **independent of the memory realignment** (no
    backend prerequisite).
  - Acceptance: selecting nodes + asking passes the focus hint to the agent and
    biases retrieval toward those tables; the answer re-highlights the used nodes;
    `scope_hash` / `instruction_scope_hash` are unchanged by a graph selection.

### Phase G4 — Optional backend bulk adjacency (deferred) `[TODO]`
- [ ] **G4.1 — Bulk schema-graph endpoint** `P3`
  - Goal: only if telemetry shows per-table fan-out is too slow on huge schemas —
    an agent route returning tables + FK adjacency (and PK flags) in one payload,
    sourced from the introspection layer / `SchemaIndex`
    ([`mdl_validator.py`](semantic_layer/mdl_validator.py), names-only) or a
    dedicated bulk introspection, cached.
  - Files: `app.py` (additive route), a new `semantic_layer/schema_graph.py`;
    **not** `config.py` for limits (keep frontend) unless a server cap is needed.
  - Depends-on: G1 shipped; a measured need.
  - Acceptance: the frontend can switch its data source to the bulk endpoint with
    no UI change; degrades to per-table when the endpoint is absent.
  - **C0 note:** touches `app.py` (shared, additive) and `mdl_validator.py` (not a
    C0 file). Avoid `config.py`/`schema_retriever.py`/`memory_store.py`/
    `instructions.py`/`vector_cache.py` (all C0-contended). See §10.

---

## 7. Integration seams & extension features

This feature is designed to be the visual hub other Wren capabilities plug into.
§7.1 specifies the **generic seams** (built in G0.3) that every extension reuses;
§7.2 lists each **extension feature** with its entrypoint, the seam/API it rides,
its prerequisite, and status; §7.3 calls out the one shared prerequisite that
gates several of them.

### 7.1 Generic seams (the foundation — built in G0.3)

| Seam | Contract | Used by |
| --- | --- | --- |
| **S-A — stable node/edge IDs** | `phys:{catalog}.{schema}.{table}` · `mdl:{modelName}` · edge `e:{src}->{dst}:{kind}`. A resolver maps MDL entity (model/column/relationship by name) ⇄ node id, and physical `{catalog,schema,table}` ⇄ node id. | every feature that addresses a node |
| **S-B — `GraphController` command/event API** | imperative: `highlightNodes(ids, style)`, `focusNodes(ids)`, `addNodes(ids)`, `clearHighlight()`; events: `onNodeSelect(id)`, `onNodeAction(id, action)`, `onSelectionQuery(ids)`. | chat highlight, query tool, validation, jump-to actions |
| **S-C — decoration model** | node/edge type carries optional, integration-owned fields, each rendered only when present: `validation?`, `status?`, `agentUsage?`, `provenance?`, `instructions?`, `types?`. | validation, activation, agent usage, provenance, instructions, types |

Design rule: **extensions never modify graph-core** — they call S-B, populate S-C,
and resolve via S-A. This keeps the unloaded/loaded performance contract (§4)
intact as features accrete.

### 7.2 Extension features (entrypoint · seam/API · prerequisite · status)

Tiers from the seam analysis. "Committed" = folded into the G-phases above;
"Seam-ready" = the hook exists, wiring deferred; "Deferred" = needs more (often
backend) before it can attach.

| # | Feature (source) | Entrypoint (UI) | Seam / API left | Prerequisite | Status |
| --- | --- | --- | --- | --- | --- |
| X1 | **Agent grounding highlight** — R2 `select_relevant_models` + `WrenContextArtifact.matched_models`/`candidate_table_names` (enrich&retrieve) | auto on each answer + "focus on these" button | S-B `highlightNodes`/`focusNodes`; consume latest artifact ([`api.ts:84-122`](../superset-frontend/src/SqlLab/components/AiAgentPanel/api.ts#L84)); S-C `agentUsage` (candidate vs matched, `retrieval_mode`) | S-A, S-B | **Committed — G3.1** |
| X2 | **Semantic query tool** (your example) — graph ⇄ chat | node multi-select → "Ask about these"; answer re-highlights | S-B `onSelectionQuery(ids)` → `focus_tables` hint in `AiAgentPanel/index.tsx` → `select_relevant_models`; return path = X1 | S-B (scope rule §7.3 adopted — focus hint, no `dataset_ids` change) | **Committed — G3.3 (flagship)** |
| X3 | **Validation overlay** — W5 (`column_without_type`, dedup), F4 cube/metric errors, E5; later C2 deep-engine (`wren_full.md`/enrich&retrieve) | red node/edge styling + tooltip; legend | S-A entity→node map; S-C `validation` populated from the editor's existing `MdlValidationResult` | S-A, S-C | **Committed — G2.2**; deep-engine (C2) lands when available |
| X4 | **Node-level enrich / add-to-MDL** — F6 per-entity patch `_patch_target`, E4 (enrich&retrieve) | unmodeled node → "Add to MDL / enrich" | S-B `onNodeAction(id,'enrich')` → existing `enrichProjectDocument`/`onboard`; gated by `canWrite` | S-B; `canWrite` | **Committed — G3.2** (F6 makes single-entity patch safe) |
| X5 | **Activation status + activate-from-graph** — F0.1 engine gate + `MdlFile.status` (`wren_full.md`) | model node badge draft/active; node → "Activate" | S-C `status` from owning `MdlFile`; S-B action → existing `updateMdlFile`; surface 409 gate | S-C; G2.1 (model↔file map) | **Seam-ready** (P2) |
| X6 | **Live MDL editing reflection** — DF2-adjacent (`wren_full.md`) | edits in Models tab update overlay instantly | shared MDL manifest state (G0.4) | **G0.4** | **Prerequisite committed (G0.4)**; full live-edit wiring P2 |
| X7 | **Document provenance** — C4 docs + `MdlFile.source_document_id` + artifact `document_ids` (enrich&retrieve) | node → "Documents that enriched this" | S-C `provenance` from `MdlFile.source_document_id` (already on the API) + `getSemanticDocument` | S-C | **Seam-ready** (P3) |
| X8 | **Instructions cross-link** — R3 / C5.1 (enrich&retrieve) | node → "Add instruction about this" → Instructions tab; optional badge | S-C `instructions` + cross-tab jump (reuse C5.1 `InstructionsPanel`) | S-C; §7.3 (instructions are **scope-level, not model-level** today) | **Seam-ready** (P3, caveat) |
| X9 | **Type-aware detail** — C3 type grounding (enrich&retrieve) | detail panel shows MDL `column.type` vs physical type; mismatch flag | S-C `types` on column rows (physical from `table_metadata`, MDL from `column.type`) | S-C; G1.3 detail panel | **Seam-ready** (P3) |
| X10 | **Indexing status** — E6 eager reindex + `SemanticLayerState.indexing_status` (enrich&retrieve) | graph header badge idle/running/error | read `getProjectSemanticLayerState` (already used by the editor) | none new | **Seam-ready** (P3) |
| X11 | **Examples/memory badges** — R6 confirmed NL→SQL pairs (enrich&retrieve) | node badge "N examples" | S-C count field | **needs backend**: a by-table examples endpoint (memory is keyed by `scope_hash`, not table) | **Deferred** |
| X12 | **Bulk adjacency data source** — perf (this doc G4) | transparent (data-layer swap) | `useSchemaGraphData` source switch behind the same hook | **needs backend G4.1** | **Deferred — G4** |

### 7.3 Scope-normalization rule — **ADOPTED: Option 2 (canonical project scope + selection-as-signal)**

**Decision (2026-06-24):** evaluated against industry practice (vector-DB
multi-tenancy: partition for isolation, metadata-filter + rerank for relevance —
Pinecone/Weaviate; semantic-layer scoping: stable hierarchical levels — LookML
multi-scope resolution). The rule:

> **The canonical scope identity is the project (`database` + `catalog` +
> `schema`). All durable agent knowledge (instructions, MDL, memory) is
> partitioned by it. A `dataset_ids` chat filter or a graph table-selection is a
> *retrieval relevance signal* (a filter/boost in ranking) — never part of the
> partition key.**

This confirms what is already true (instructions schema-level via
`instruction_scope_hash`; MDL retrieval project-keyed) and resolves the one
remaining divergence (memory is the only knowledge still partitioned by
`dataset_ids` — over-partitioning by the industry rule).

**Rejected:** per-resource ad-hoc (incoherent; re-litigated per feature);
hierarchical cascade (Option 3 — over-engineered until dataset-specific authoring
is actually requested; recorded as the future extension if so); tag/label scoping
(Option 4 — duplicates semantic similarity).

**Engineering shape (where each piece lands):**
1. **Instructions / MDL** — already conform. Optionally fold `instruction_scope_hash`
   into a shared `project_scope_hash` name for one concept. *(no behavior change)*
2. **Memory (NL→SQL)** — realign recall partition from full `scope_hash`
   (incl. `dataset_ids`) to the **project scope**, and pass `dataset_ids` /
   selected tables as an **overlap boost** in `memory_store._semantic_rank`.
   Requires a re-key/dual-read migration of stored examples. **Backend owner
   sign-off required** — this is the only behavior change, and it touches
   `memory_store.py` (**C0-contended**), so schedule it with the backend owner,
   *not* under the graph-view work. Tracked as a sub-task in
   [`wren_enrich_and_retrieve.md`](wren_enrich_and_retrieve.md) (see X-ref below).
3. **X2 (graph query tool)** — add `focus_tables`/`focus_models` to the agent query
   request and feed them to `select_relevant_models` as a boost/seed. **Never write
   graph selection into `dataset_ids`** (also avoids the table≠dataset coercion:
   graph nodes are physical `{catalog,schema,table}`, `dataset_ids` are Superset
   dataset IDs that may not exist for a raw table).

**Crucial sequencing consequence:** because X2 passes a *focus hint* and never
mutates `dataset_ids`, **the graph feature does not depend on the memory
realignment**. X2 and X8 (already schema-level) are unblocked under this rule
immediately; the memory migration ships independently. So G0–G3 carry **no backend
prerequisite** — the only C0-contended item (memory realignment) is decoupled.

**Per-feature effect under the adopted rule:**
- **X2** — selection is an advisory re-rank/seed hint to `select_relevant_models`;
  scope identity (and thus all recall) is unchanged. No silent drops.
- **X8** — instructions are already project/schema-level; the node action surfaces
  the schema's instructions (correct by construction, no per-model partition).
- **X11** — unchanged status (still needs a by-table examples endpoint); the rule
  makes "examples for this project" well-defined once that endpoint exists.

## 8. Performance budget & strategy summary

| Lever | Target | Mechanism |
| --- | --- | --- |
| Unloaded cost | **0** added bytes/calls/timers | `React.lazy` + lazy tab pane + minimal ECharts import (D1) |
| Open cost | 1 cheap `/tables/` + MDL (cached) + ≤ `SEED_LIMIT` metadata calls | Seed model (D2), concurrency cap (D6) |
| Per-action cost | ≤ `EXPAND_FANOUT_CAP` metadata calls, ≤ `MAX_INFLIGHT` concurrent | Bounded expand + queue (D3/D6) |
| Steady-state nodes | ≤ `MAX_NODES` | LRU windowing (D3) |
| Node-count blow-up | avoided | table-level LOD; columns in panel (D4) |
| Re-render cost | incremental | merge `setOption`, frozen layout (D7) |
| Repeat visits | near-zero | in-memory cache per scope (D6) |

Degrade-closed defaults: no embedder/engine dependence; no FK data → semantic
layer still renders; metadata error on a node → node stays collapsed with a
warning, never blocks the graph.

---

## 9. Risk register

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Huge schema overwhelms layout | High | Seed+expand (D2), caps+LRU (D3), table-level LOD (D4). |
| Metadata API fan-out / latency | High | Concurrency cap + queue + abort + cache (D6); optional bulk endpoint (G4). |
| Bundle regression for non-users | Med | Lazy load + minimal ECharts import; bundle-size acceptance gate (G0.1). |
| Sparse physical FKs (analytic DBs) | High (by nature) | Lead with the MDL relationship layer; never depend on FK edges for value. |
| Reverse-FK incompleteness until explored | Med | Incremental index (D5); document "edges fill in as you expand"; G4 fixes fully. |
| `condition`/expression parsing for column-level edges | Med | Model-level edges first; defer column-level expression lineage. |
| ECharts force-layout instability on add | Med | Freeze layout post-stabilize, seed new nodes near parent (D7). |
| Permissions | Low | Read-only graph; reuse editor `scope`/`canWrite`; gate only G3.2 write. |
| Catalog/schema scoping mismatch (MDL `tableReference` vs physical) | Med | Join on normalized `{catalog,schema,table}`; surface unmatched models as a coverage warning, don't crash. |

---

## 10. Touchpoints & C0 overlap check

**C0 (in-flight) contended files** (from `wren_enrich_and_retrieve.md` Phase C0 +
working tree): `semantic_layer/memory_store.py`, `semantic_layer/instructions.py`,
`semantic_layer/schema_retriever.py`, `semantic_layer/vector_cache.py`,
`config.py`, `.env.example`.

**This feature's touchpoints:**

**This feature's touchpoints:**

| Phase | Files | Overlap with C0 |
| --- | --- | --- |
| G0–G2 + G3.1 | `../superset-frontend/.../SemanticLayerEditor/index.tsx` (modify: tab + shared MDL state G0.4), new `SemanticLayerEditor/SchemaGraph/**` (incl. `types.ts`, `ids.ts`, `GraphController.ts` for the seams), optional thin wrappers in `AiAgentPanel/api.ts` | **None** — frontend only |
| G3.3 (semantic query tool) | `AiAgentPanel/index.tsx` (modify: accept a selection hint into the query path) | **None** — frontend only |
| G4 (deferred) | `app.py` (additive route), new `semantic_layer/schema_graph.py`, reads `mdl_validator.py` (`SchemaIndex`) | **None of the contended files**; `app.py` is shared but additive. Explicitly **avoid** `config.py` (keep limits in `graphConfig.ts`). |
| Memory realignment (§7.3, decoupled) | backend `memory_store.py` recall + migration | **Overlaps C0** (`memory_store.py`). **Not required by the graph feature** — X2 uses a focus hint, not `dataset_ids`. Schedule with the backend owner independently. |

**Verdict:** G0–G3 are fully parallelizable with C0 (frontend-only, mirrors the
C5.1 isolation). G4 stays clear of every C0-contended backend file by design. The
§7.3 scope rule is **adopted (Option 2)**; its only C0-contended piece (memory
realignment) is **decoupled** from the graph feature, so G0–G3 carry no backend
prerequisite.

`index.tsx` is clean in the working tree; `SemanticLayerImportDialog.tsx` is
already dirty (C5.x) — this feature does not touch it.

---

## 11. Open questions & deferred options

| ID | Item | Note |
| --- | --- | --- |
| Q1 | Seed-priority | **Resolved** — agent last-answer tables → user left-bar `tables` → first-N from `/tables/`. All zero-cost; no SQL parsing (§4.8, D11). |
| Q2 | Persist explored subgraph across sessions | **Resolved — no persistence.** Caches are in-memory for the session only; no `sessionStorage`/durable layout. |
| DG1 | reactflow/cytoscape upgrade for box-style ERD + nicer routing | Governed new dep; only if ECharts layout proves insufficient (D9). |
| DG2 | Column-level expression lineage (parse calculated `expression` / relationship `condition`) | Higher effort; after model-level edges prove valuable. |
| DG3 | Bulk adjacency endpoint (G4) | Build only on measured need. |
| DG4 | Minimap + saved layouts | Polish; after G1/G2. |

## 12. Sequencing & critical path

```
G0 (lazy scaffold + data layer + seams S-A/S-B/S-C + shared MDL state)
        │
        ├─► G1 (physical: seed/search/expand) ─► G2 (MDL overlay: X3 validation)
        │                                              │
        └─► G3.1 (X1 agent highlight) ─► G3.3 (X2 semantic query tool, flagship)
                                          │
                                          └─ §7.3 rule adopted: focus hint, not a
                                             scope change → no backend prerequisite
G4 (bulk endpoint) ── deferred, gated on measured need
Seam-ready extensions X5–X10 attach via S-A/S-B/S-C with no graph-core change.
```

**Recommended first pass: G0 (incl. G0.3 seams) + G1.1** — they prove the two
hardest constraints (zero unloaded cost; bounded seed render on a large schema)
*and* lay the seams every extension needs, before any overlay work. G2 then lands
the differentiated value (incl. X3 validation) on a proven, performant base; the
flagship X2 query tool follows in G3 once §7.3 is settled.
