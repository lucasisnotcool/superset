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

# Feature Spec — Wren-Parity Cubes for the MDL Copilot

> Companion to `plan_views_parity_spec.md`. Cubes and views are **separate concerns**
> (see §1.4): a view is query-wired through `transform_sql` (plain semantic SQL); a
> cube is **not** — it is queried through a dedicated structural API. That single fact
> drives the entire shape of this spec, including the central scope decision (§6, §11-D1).

## 0. One-paragraph intent

Give the MDL Copilot **Wren-level parity for cubes**: when a BI document describes a
named aggregation pattern ("ARR", "weekly active users", "revenue by month"), the
Copilot should be able to **author a cube** over an existing model — a structured
`{name, baseObject, measures[], dimensions[], timeDimensions[]}` object — and have it
**validated and activated correctly** end-to-end, exactly the way Wren does. The
motivation (per the originating discussion) is that cubes give an LLM a **structured
aggregation API** instead of forcing it to hand-write `GROUP BY` / `DATE_TRUNC`, which
is where small models fail most often. **Reuse Wren as much as possible** — adapt the
upstream cube skill/reference content (download-and-tweak, the same method used for
`onboarding` and `enrich-context`), and reuse the in-repo plumbing already verified to
handle cubes.

---

## 1. What Wren provides (parity target)

### 1.1 The cube object (authoring shape)
A cube is a top-level MDL object. Fields (verified against wren-core 0.7.x — see §1.3):

| Field | Req | Meaning |
|---|---|---|
| `name` | ✓ | Unique cube name; the queryable handle |
| `baseObject` | ✓ | An **existing model or view** the cube aggregates (NOT a raw table) |
| `measures` | ✓ | `[{name, type, expression}]` — aggregations (`sum(amount)`, `count(*)`) |
| `dimensions` | – | `[{name, type, expression}]` — group-by columns |
| `timeDimensions` | – | `[{name, type, expression}]` — time columns supporting granularity |
| `hierarchies` | – | engine map (e.g. `time → [year, quarter, month]`); **not authored** initially |
| `properties` | – | description + governance metadata |

Source for the shape, in-repo: `MdlCube` at `semantic_layer/mdl_schema.py:137-154`.

### 1.2 The decision: when is something a cube? (Wren's routing)
From upstream `enrich-context/references/cube_proposals.md` (already mirrored at
`wren_upstream_skills/enrich-context.references.cube_proposals.md:17-27`):

```
Raw mentions a named metric / aggregation pattern
├── base model has multiple measure-shaped columns + ≥1 group-by dimension → CUBE
├── pure row-level expression (amount*1.1, no grouping)                     → CALCULATED COLUMN
├── needs JOIN across models / window / CTE                                 → VIEW (then cube on the view)
└── old-style MDL metrics: already covers it                                → don't duplicate
```
Cube is Wren's **default** sink for grouped aggregation because "agents pick wrong
joins, double-count, and mis-truncate dates when forced to write aggregation SQL by
hand. Cubes pre-declare those decisions once."

### 1.3 How a cube is QUERIED (the decisive parity fact — empirically verified)
Cubes are **NOT** queried with raw SQL. A spike against the installed wren-core
(recorded in memory `cube-query-wiring`) established:

- A cube **loads** into `SessionContext` (manifest valid) — already pinned by
  `tests/.../test_native_manifest_contract.py:156` (`test_native_cube_loads_into_engine`).
- `SELECT … FROM <cube>` via `transform_sql` **fails**: `table 'wren.public.<cube>' not found`.
  Cubes are not registered as relations for raw-SQL rewrite.
- Cubes **are** queryable via the module function
  **`wren_core.cube_query_to_sql(cube_query_json, manifest_json)`** — both args are JSON
  **strings**, and the manifest is **raw engine JSON (NOT base64)**.
- The **CubeQuery** payload (matches upstream `wren cube query --from -` JSON):
  ```json
  {"cube":"sales","measures":["total_amount"],"dimensions":["status"],
   "timeDimensions":[{"dimension":"created_at","granularity":"month"}],
   "filters":[{"dimension":"status","operator":"eq","value":"completed"}],"limit":100}
  ```
  `measures`/`dimensions` are **plain string lists**. Output is correct aggregated SQL:
  `SELECT DATE_TRUNC('month', created_at) AS created_at__month, status AS status,
   sum(amount) AS total_amount FROM orders WHERE status = 'completed' GROUP BY 1,2 ORDER BY 1`.
- `cube_query_to_sql` raises `ValueError` on **unknown cube / measure / dimension** (e.g.
  `Unknown measure 'nope' in cube 'sales'`) — this is the **cube dry-plan validation
  primitive** (§5.2).

### 1.4 Why cubes ≠ views (the consequence)
| | View | Cube |
|---|---|---|
| Query model | plain semantic SQL `SELECT … FROM view` | structural CubeQuery → `cube_query_to_sql` |
| Fits existing `transform_sql` query path? | ✓ yes (inert: zero new query wiring) | ✗ **no — needs a new consumption path** |
| Authoring difficulty for LLM | low (one SQL statement) | higher (nested measures/dims + types) |

**Implication:** authoring a cube (the Copilot side) is *necessary but not sufficient*
to improve LLM aggregation. Until a query-time path emits a CubeQuery and calls
`cube_query_to_sql`, authored cubes are **inert**. This splits the work into two tracks
(§6) and is the central scope decision (§11-D1).

---

## 2. Current state — already wired vs. missing

### 2.1 Already wired ✓ (reuse as-is — the plumbing is cube-complete)
- **Schema:** `MdlCube` + top-level `cubes[]` — `mdl_schema.py:137,168`.
- **Structural validation:** `_validate_cubes` + `_validate_cube_measures` +
  `_validate_cube_field_entries` — `mdl_validator.py:942-1114`. Enforces name, duplicate,
  `baseObject` present **and resolves** (`unresolved_cube_base`), measures `{name,type,expression}`,
  dimension/timeDimension `{name,type,expression}`. Covered by 9 validator tests + 3 engine
  contract tests (`test_native_manifest_contract.py:156-182`).
- **Merge / patch:** `MERGE_SECTIONS` includes `"cubes"` (`mdl_merge.py:43`); entry-level
  `merge_cube_preserving_structure` preserves `baseObject` and merges measures/dims by name
  (`mdl_merge.py:118-140`).
- **Compile → engine manifest:** `_CUBE_KEYS` + `to_engine_manifest()` emits `cubes`
  (`mdl_compile.py:46,80-81`).
- **Materializer:** merges + dedupes the `cubes` key (`wren_materializer.py:97,166`).
- **Copilot write tools:** `write_mdl_file` / `patch_mdl_file` accept raw JSON incl. cubes
  and route through `validate_mdl` + `merge_manifest_sections` (`copilot/tools.py`).

### 2.2 Gaps ✗ (the actual authoring work — Track A)
- **G1 — Authoring contract excludes cubes.** `AuthoredManifest` exposes only
  `models/relationships/views/metrics` (`mdl_authoring.py:136-144`). The LLM literally
  cannot emit a cube through the structured generation/enrichment path until an
  `AuthoredCube` type + `cubes` field are added (and thus surface in
  `proposal_response_schema()`).
- **G2 — Deep validation drops cubes.** The activation/materializer gate calls
  `validate_with_wren_core(merged_models, merged_relationships)` — cubes are **not passed**
  (`mdl_validator.py:509`). A cube the engine would reject only fails later.
- **G3 — No authoring guidance.** No skill/prompt teaches *when* to author a cube or its
  measure/dimension/type discipline. The upstream `cube_proposals.md` decision tree was
  never ported into the active skills (it lives only in `wren_upstream_skills/`).
- **G4 — No cube dry-plan.** Nothing proves a cube is *queryable* (that each measure/dim
  translates). The `cube_query_to_sql` primitive (§1.3) makes this cheap and definitive.
- **G5 — `remove_mdl_entity` spec omits cubes.** Section validation accepts cubes (they're
  in `MERGE_SECTIONS`) but the tool description lists only `models|relationships|metrics|views`
  (`copilot/tools.py:257-292`) — documentation gap, LLM won't use it for cubes.
- **G6 — Metric↔cube routing undefined.** Today `generate-mdl.md:265-272` routes all
  aggregation intent to **metrics** and explicitly says "Cubes are not authored here."
  Enabling cubes requires a clear routing rule so the agent doesn't author both.

### 2.3 The consumption gap (Track B — see §6)
- **G7 — No cube query path.** The AI SQL agent only calls `transform_sql`. There is no
  binding to `cube_query_to_sql`, no CubeQuery emission, no cube branch in `text_to_sql.md`.
  Without this, Track A cubes are inert.

---

## 3. Design principles (mirror the views work + the repo's invariants)
1. **Reuse Wren; download-and-adapt skills** (non-negotiable per the request) — §8.
2. **Reuse the verified plumbing** (§2.1). Add only the authoring contract, the deep-validation
   pass-through, and the cube dry-plan.
3. **Degrade closed.** Every new seam (dry-plan, consumption) no-ops safely when wren-core
   is absent — same invariant as `WrenCoreEngine`/`validate_with_wren_core`.
4. **Human-review-gated.** Cubes are **high blast radius** (a public, named aggregation
   interface). Per upstream, treat every cube proposal as an escalation: it lands in the
   existing review-gated changeset; the Copilot never auto-activates.
5. **Mirror `AuthoredView`** exactly (it is the in-flight sibling) so the two features stay
   structurally identical and reviewable together.
6. **Document-grounded only (no hallucination).** A cube invented from schema alone is a
   hallucination — base onboarding (`generate_base_model`) sees *tables, not documents* and must
   stay cube-free (parity with views' D1 / Wren). Cubes require a **document-grounded trigger** (a
   described named aggregation: "ARR", "revenue by month") at the two creation points (§4).
7. **Model-name discipline (cross-schema correctness).** A cube's `baseObject` and every
   measure/dimension `expression` reference **model / model-column names**, never physical
   `schema.table.column`. This is what makes a cube cross-schema-correct *by construction* (§5.6)
   — the same model-layer guarantee views rely on. The skills/prompts (§8) must pin it.
8. **No new access authority; project-scoped.** A cube is a project-scoped MDL entity like a
   model/view; it introduces **no per-object RBAC** and rides the existing project access
   boundary + changeset gate (see §RBAC below). Cube *consumption* (Track B) emits SQL that flows
   through the **same** `validate_read_only_sql` + executor + object-level checks as any agent SQL.

---

## 4. The two creation points (dev intent ↔ actual architecture)

The request names two places cubes can be created. Mapped to the real pipeline:

### Place 1 — "auto-onboard" = the doc-driven self-review sub-phase (NOT base onboarding)
Base onboarding (`semantic_layer/onboarding.py:59`) is **structure-only**: it seeds base
**models** from the physical catalog via `generate_base_model`. It has **no aggregation
intent** (a schema alone doesn't say "ARR = MRR×12"), so it **cannot and should not** author
cubes. Cubes arise only where document context exists — which in the onboarding flow is the
**doc-driven self-review** sub-phase (the same place the views spec targets; see
`skills/onboarding.md:163` "Step 5 — Self-review against the documents"). So "create a cube on
auto-onboard" means: *during the document-grounded review pass that runs after base models
exist.* This is correct precisely because **`baseObject` requires an existing model** (§1.1).

### Place 2 — enrichment (later document uploads)
`app.py:3784 enrich_project_document()` → `_enrichment_proposal()` → `propose_mdl_from_document`
(`integrations/wren/llm_client.py:165`). This is the steady-state path: a new BI doc arrives,
the Copilot decides a cube is warranted, and proposes it into the review-gated changeset.

**Both creation points share one LLM path** (`propose_mdl_from_document`, output-gated by
`AuthoredManifest`) and one activation path (§7). So closing G1+G2+G3+G4 enables cubes at
**both** places at once — no per-place work.

---

## 5. Feature spec — component by component

### 5.1 Authoring contract — `AuthoredCube` (closes G1)
Add to `mdl_authoring.py`, mirroring `AuthoredView` (`mdl_authoring.py:117-133`):
```python
class AuthoredCubeField(BaseModel):          # measures / dimensions / timeDimensions entry
    model_config = _AUTHORING_CONFIG
    name: str
    type: str
    expression: str

class AuthoredCube(BaseModel):
    model_config = _AUTHORING_CONFIG
    name: str
    base_object: str                          # MUST resolve to an existing model/view
    measures: list[AuthoredCubeField] = Field(default_factory=list)
    dimensions: list[AuthoredCubeField] = Field(default_factory=list)
    time_dimensions: list[AuthoredCubeField] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    # hierarchies intentionally omitted from the authoring contract (decision D3).
```
Add `cubes: list[AuthoredCube] = Field(default_factory=list)` to `AuthoredManifest`
(`mdl_authoring.py:144`). `proposal_response_schema()` and `serialize_manifest()` pick it up
automatically (camelCase via `by_alias`: `baseObject`, `timeDimensions`). **Typed fields
(not `dict`)** so the JSON schema handed to the LLM forces `{name,type,expression}` — the
single biggest authoring-accuracy lever for small models.

### 5.2 Validation (closes G2 + G4) — two layers
**Layer A (G2) — stop dropping cubes from deep validation.** Extend
`validate_with_wren_core` (`wren_core_validator.py:62`) and its caller
(`mdl_validator.py:509`) to also pass `cubes` (and the existing `views`) into the engine
manifest envelope (`to_wren_core_manifest`, `wren_core_validator.py:145`). Then a cube the
engine rejects on **load** fails at activation, not query time. Cheap, pure pass-through.

**Layer B (G4) — per-cube dry-plan, TWO STEPS (the definitive correctness check).**
⚠️ **Cubes are the opposite of views here.** Views' spike (D2) found manifest *load* is eager
and fully validates a view's SELECT columns, so the views dry-plan was dropped. For cubes,
**load does NOT validate measure expressions** (verified spike): a cube with
`sum(nonexistent_col)` passes structural validation, passes load, *and* passes
`cube_query_to_sql` — which only validates measure/dimension **names**, not the columns inside
their expressions. So the cube dry-plan is required and is **two steps**, per cube:
1. **Name check** — build a CubeQuery selecting **all** the cube's measures + dimensions and call
   `cube_query_to_sql` (§1.3). A `ValueError` (`Unknown measure/dimension`, `Cube not found`,
   cyclic derived measure) → field-anchored error. Output = semantic SQL over **model names**.
2. **Column check** — feed that output SQL back through `transform_sql` (the existing engine path).
   wren-core eagerly resolves columns: a bad ref inside a measure/dimension expression fails with
   `Schema error: No field named …` (verified spike). This is the *only* step that proves the
   cube's expressions actually resolve against the model.

Both map through the existing `_friendly_engine_error` translator (`wren_core_validator.py:109`).
This is the **only** check that guarantees a cube is usable; do **not** drop it by analogy to the
views D2 decision. Degrades closed when wren-core absent.

**Reassurance (no poisoning guard needed).** Unlike native views (which fail engine *load* and
must be excluded from the manifest — views spec R9), a cube **loads cleanly into the engine
manifest** even before its expressions are proven (verified spike). So cubes need **no
manifest-exclusion / isolation machinery**; an unproven cube is caught by Layer B at the
activation gate, never by poisoning the project's load.

### 5.3 Copilot tools (closes G5; patch already wired)
- **`remove_mdl_entity`:** add `cubes` to the spec's section enum + description
  (`copilot/tools.py:257-292`). Functional already (cubes ∈ `MERGE_SECTIONS`); this is the
  doc/enablement fix so the agent will actually use it.
- **`patch_mdl_file`:** no change — `merge_cube_preserving_structure` already supports
  measure/dimension-level edits (`mdl_merge.py:118-140`). Confirm with a test.

### 5.4 Skills & prompts (closes G3) — see §8 for the exact download-and-adapt.

### 5.5 Metric ↔ cube routing (closes G6)
Adopt Wren's decision tree (§1.2) verbatim in the skills. Concretely flip
`generate-mdl.md:269-272` from "Cubes are not authored here" to the routing rule:
**grouped/dimensional aggregation with ≥1 group-by → cube; single reusable scalar aggregation
→ metric; row-level expression → calculated column.** Add the upstream duplication guard
("same expression already exists for the same base_object → don't propose a duplicate") so the
agent never emits both a metric and a cube for the same logic.

### 5.6 Cross-schema cubes (inherit the model-layer guarantee — verify)
A cube aggregates a **model** (its `baseObject`), and the model carries its own
`tableReference.schema`; `cube_query_to_sql` emitted `FROM orders` (the model name) and the
engine rewrites per the manifest. So a cube over a single model is schema-correct by
construction. A cube whose measures need columns from **another schema** must aggregate a
`baseObject` that already reaches them (a view that pre-joins, or a model reachable via a
relationship) — i.e. **cross-schema is the view's/relationship's job, not the cube's**, which
matches the upstream tree ("needs JOIN → VIEW, then cube on the view"). **VERIFIED (spike): a
cube over a VIEW baseObject loads and queries** (`FROM <view>`), so the pre-join path works.
*Remaining: a one-shot multi-schema spike — a cube over a model in schema A with a measure
reaching schema B via a relationship — to confirm `transform_sql` (Layer-B step 2) rewrites the
multi-schema `tableReference`; mirror the views spec §5.6.*

### 5.7 RBAC / authorization (parity with views; no new authority)
A cube is a project-scoped MDL artifact and introduces **no cube-specific RBAC** — it inherits
the project access boundary and the changeset/activation gate exactly as models and views do
(views spec §8). Two consumption-side points keep it safe:
- **Generated SQL references real tables, not the cube.** `cube_query_to_sql` emits SQL over the
  baseObject **model** (`FROM orders`), which `transform_sql` rewrites to physical tables. So the
  executed SQL is gated by the **same** `validate_read_only_sql` + executor + object-level
  `raise_for_access` checks as any agent query — a cube cannot widen data access.
- **Recall safety (interaction with shared memory, F2).** A cube-backed answer stored as a
  runtime NL→SQL pair carries `native_sql` whose `referenced_tables` are the real physical tables;
  the golden-queries **fail-closed access pre-filter** (queries spec §4) therefore gates it
  correctly with no cube-aware logic. See §6A.

### 5.8 Activation atomicity — the baseObject dependency
A cube's `baseObject` must resolve to an **active** model/view. Activating a cube before its
base object would fail — exactly the metric→model ordering hazard already solved by the atomic
bulk-status endpoint that validates the **projected** manifest as one unit ([[mdl-bulk-activate]];
views spec R7). Cubes ride that guarantee; **no new activation logic** — but the skill must keep a
cube and its (new) baseObject model/view in the **same changeset** so they activate together.

### 5.9 Description as a recall artifact (load-bearing, not cosmetic)
Per the views finding, `properties.description` on a cube is load-bearing for text-to-SQL recall,
not decoration. The skills (§8) must require a cube `properties.description` (what it aggregates,
the grain); the optional coverage signal can flag cubes missing one. A well-described cube is both
a query target (Track B) and a few-shot exemplar.

---

## 6A. Cross-feature interactions (cubes ↔ views ↔ golden queries)

### Cubes ↔ Views — a one-way dependency (affects ordering)
- A cube can aggregate a **view** baseObject (VERIFIED, §5.6), and the upstream tree routes any
  **cross-model/JOIN/window** aggregation to *a view first, then a cube on the view*. So
  **cross-table cubes depend on views existing.** Single-model cubes do not.
- **Ordering consequence:** the views parity work (in flight) should land **before** cube Track A
  ships cross-table cubes; single-model cubes can proceed independently. `base_object_names`
  already includes `_names(views)` (`mdl_validator.py:426`), so resolution is wired.
- No change required to the views spec/impl — cubes are strictly downstream of views.

### Cubes ↔ Golden queries / shared memory — store the right SQL form
The queries spec stores two forms: runtime memory = `native_sql` (DB-scoped, RBAC-filtered by
parsed `referenced_tables`); golden = `semantic_sql` over model names (project-scoped). A
**cube-backed answer** is produced from a structured CubeQuery, which raises three points:
- **Runtime memory (DB-scoped):** store the **executed `native_sql`** (physical) — RBAC-safe and
  meaningful across projects, exactly as the queries spec already handles cross-project pairs
  (`native_sql` only, omit `semantic_sql` for foreign models). No cube-aware change needed.
- **Golden (project-scoped):** the cube's generated SQL is *already* model-name SQL (a valid
  `semantic_sql`), so a cube-backed answer **can** be promoted to a golden query in its form
  today — but that loses the "use the cube" signal. **Open decision (DP-C1, §11):** whether to
  extend the golden entry with an optional structured `cube_query` field so recall can *teach the
  agent to prefer the cube*. **Recommend: defer to Track B** (it only matters once the agent can
  consume cubes); the queries spec's `queries.json` entry shape would gain one optional field.
- **Recall does not currently signal cube-vs-SQL.** Whether the agent picks a cube is driven by
  the Track-B aggregation decision tree (§6), **not** by memory. Few-shot cube exemplars are a
  Track-B enhancement, not a Track-A dependency.

**Net:** Track A needs **no** change to the queries spec. Track B introduces exactly one optional,
additive field decision (DP-C1) in `queries.json`. Flagged in the queries spec as a pending item.

---

## 6. The consumption gap (Track B) — make cubes actually useful

Authored cubes are **inert** until something queries them (§1.4, G7). Track B delivers the
stated motivation ("improve LLM ability to aggregate"). It lives in the **AI SQL agent**, not
the Copilot, and is a **larger, separable change**:

1. **Engine binding.** Add `cube_query(cube_query_json, manifest) → PlannedSql` to the engine
   (alongside `plan_sql`), wrapping `cube_query_to_sql` (raw-JSON manifest, not base64; degrade
   closed). Output flows through the **existing** `validate_read_only_sql` + Superset executor —
   no new execution path.
2. **Agent branch.** A cube-aware step in the LangGraph pipeline: on an aggregation question
   where a cube covers it, the LLM emits a **CubeQuery JSON** (structured output, schema-forced)
   instead of semantic SQL; the agent calls `cube_query`, then executes. Mirror upstream
   `usage.SKILL.md:316-403` ("Cube Query Workflow" + "Aggregation decision tree").
3. **Prompt.** Adapt the upstream cube-query workflow into `text_to_sql.md` (the "prefer cube
   when it covers the question, else raw SQL" branch + the discover/describe/match steps).

**Recommendation (D1):** ship **Track A first** (this spec's core) — it is full *authoring*
parity, mirrors views, and is low-risk. **Track B is a follow-on feature** with its own spec;
do not block A on B. But the spec must state plainly: *Track A alone produces validated cubes
that no agent yet queries.* If the immediate goal is the aggregation-accuracy win, Track B is
required and should be scheduled right after A.

---

## 7. Validation & activation flow (target end-state, Track A)
1. **Propose** — LLM returns `MdlProposalResponse` whose `AuthoredManifest` now carries `cubes`
   (`llm_client.py:607`, schema from `proposal_response_schema()`).
2. **Structural validate** — `validate_mdl` → `_validate_cubes` (already strong). Errors block.
3. **Deep validate (Layer A)** — merged manifest incl. cubes → `validate_with_wren_core`
   (G2 fix) → cube loads into wren-core or fails with a friendly message.
4. **Cube dry-plan (Layer B)** — each cube's all-measures+dims CubeQuery → `cube_query_to_sql`;
   `ValueError` → field-anchored error. Guarantees queryability.
5. **Stage** — review-gated changeset; human accepts (cube = escalation, §3.4).
6. **Activate** — `set_mdl_files_status` → `_enforce_activation_manifest` →
   `validate_project_manifest` (now cube-complete) → `materialize_wren_project` (already merges
   cubes) → engine `manifest.json` includes the cube.

---

## 8. Skill/prompt adaptation (the "download & adapt" non-negotiable)

Same method as `onboarding`/`enrich-context`: download upstream verbatim into
`wren_upstream_skills/` (provenance header, not committed as the active skill), then port a
lightly-tweaked copy into the active `skills/`. Upstream paths (confirmed via
`gh api repos/Canner/WrenAI/git/trees/main`):

| Upstream file | Adapt into | Tweaks for our context |
|---|---|---|
| `…/enrich-context/references/cube_proposals.md` *(already in `wren_upstream_skills/`)* | new `skills/enrich-context.references.cube_proposals.md` + a routing block in `skills/enrich-context.md` | drop `wren cube`/CLI + `queries.yml`; replace YAML sink (`cubes/<name>/metadata.yml`) with our **JSON `cubes[]` via `write_mdl_file`/`patch_mdl_file`**; replace `wren context validate` / `wren cube query --sql-only` with **`validate_project` + the cube dry-plan**; keep the decision tree, duplication guard, naming policy, and the **escalation rule** verbatim |
| `…/generate-mdl/SKILL.md` (Phase-4 + "When to define cubes", `:155`) | `skills/generate-mdl.md:265-274` | flip "Cubes are not authored here" → the routing rule (§5.5); add the `{name,baseObject,measures,dimensions,timeDimensions}` shape + type discipline; keep metric/calculated-column branches |
| `…/usage/SKILL.md` ("Cube Query Workflow", `:316-403`) | **Track B only** → `prompts/text_to_sql.md` | replace `wren cube query` CLI with our CubeQuery-JSON + `cube_query` engine call; keep the aggregation decision tree + "when NOT to use a cube" fallbacks |
| `skills/onboarding.md` (Step 5 self-review, `:163`) | same file | extend the doc-driven self-review to propose a doc-justified cube (Place 1, §4); keep base onboarding cube-free |
| `prompts/wren_onboarding.md` | same file | one line: base onboarding stays cube-free; cubes are document-grounded (principle 6) |
| `prompts/mdl_copilot.md` | same file | note: cubes live under `cubes/`; author via the routing rule; **expressions over model columns** (principle 7); require `properties.description`; cubes are review-gated escalations kept in the same changeset as their baseObject |

> Mirrors the views six-touchpoint adaptation (views spec §7). The cube-**authoring** content
> (enrich-context references + routing, generate-mdl, onboarding self-review, both prompts) ships
> with **Track A**; the cube-**query** content (`usage.SKILL.md` → `text_to_sql.md`) ships with
> **Track B**. Each ported file carries a provenance header citing the upstream path + fetch date,
> exactly as the existing ports do.

---

## 9. User intent / flow ↔ actual UI
- **What the user sees (Track A):** uploading/enriching from a BI doc that names a grouped
  metric now yields a **cube** in the review-gated diff (alongside models/views/metrics), with
  measures/dimensions and a description. Accept → it activates into the project manifest. **No
  new UI surface required** — cubes ride the existing changeset/diff review and entity list.
- **Honest gap to flag (dev intent ↔ feature):** with Track A only, the cube is **authored and
  validated but not yet queryable by the AI SQL agent** — the "ask an aggregation question, get
  a cube-backed answer" experience needs Track B. If a UI chip/inspector renders the cube, it
  shows a structured object the query agent does not yet consult. Surface this in release notes
  so users don't expect aggregation gains from authoring alone.
- **Optional UI (deferred):** an entity-type chip for cubes in the diff/inspector (mirror the
  deferred views chip, views spec §5/Step 12).

---

## 10. Risks & mitigations
| # | Risk | Mitigation |
|---|---|---|
| R1 | **Inert cubes** — Track A ships, users expect aggregation wins that need Track B | §6 + §9 explicit scoping; release note; schedule Track B next |
| R2 | **LLM authors malformed cubes** (wrong types, bad measure expr) | typed `AuthoredCubeField` forces shape (§5.1); Layer A load check + Layer B dry-plan (§5.2) catch the rest before activation |
| R3 | **Metric/cube duplication** — agent emits both for one metric | upstream duplication guard + routing rule in skills (§5.5) |
| R4 | **High blast radius** — cube is a public named interface | review-gated changeset + escalation rule (§3.4) — never auto-activated |
| R5 | **Cross-schema cube mis-rewrite** | §5.6 verification spike before claiming cross-schema parity; route cross-model aggregation through a view (upstream tree) |
| R6 | **wren-core absent / version drift** | every new seam degrades closed; pin the CubeQuery contract + the two-step dry-plan with an engine contract test next to `test_native_cube_loads_into_engine` |
| R7 | **`hierarchies` complexity** | omit from the authoring contract (D3); engine still deep-validates if present from elsewhere |
| R8 | **Bad column in a measure expression** passes structural + load + name-check, only caught by Layer-B **step 2** (`transform_sql`) | make the two-step dry-plan mandatory (§5.2); do **not** drop it by analogy to views' D2 |
| R9 | **Hallucinated cube** from schema alone | document-grounded principle (6); base onboarding stays cube-free; review gate |
| R10 | **baseObject activation ordering** — cube activated before its base model/view | atomic bulk-status validates the projected manifest as one unit ([[mdl-bulk-activate]]); skill keeps cube + baseObject in one changeset (§5.8) |
| R11 | **Cube-backed golden carries a project-local cube reference** that dangles in another project | store runtime pairs as physical `native_sql` (RBAC-safe, DB-scoped); structured cube form stays project-scoped (§6A, DP-C1) |
| R12 | **Over-cubing / metric duplication** eroding the layer | routing rule + duplication guard in skills (§5.5); review gate |

---

## 11. Decision points (recommendation in **bold**)
- **D1 — Scope: Track A only, or A+B?** **Ship Track A (authoring+validation parity) first as
  this spec; treat Track B (cube consumption in the AI SQL agent) as an immediately-following
  companion spec.** Rationale: A is low-risk, mirrors views, and is the prerequisite; B is where
  the aggregation-accuracy payoff lands. Do not bundle — bundling repeats the original
  cube/view coupling mistake.
- **D2 — Metric vs cube routing.** **Adopt Wren's decision tree verbatim** (§1.2/§5.5):
  grouped/dimensional → cube; single scalar reusable agg → metric; row-level → calculated column.
- **D3 — Author `hierarchies`?** **No (initially).** Author measures/dimensions/timeDimensions
  only; hierarchies are an engine map and add LLM error surface for little near-term value.
- **D4 — Dry-plan depth (Layer B).** **One representative all-measures+all-dimensions CubeQuery
  per cube.** Cheap, covers unknown-measure/dimension and cyclic-derived-measure errors. Avoid
  combinatorial per-measure plans.
- **D5 — Native (non-semantic) cubes?** **N/A** — cubes are always semantic (defined over a
  model/view); there is no native-SQL cube analogue to the views' native hedge. (Also ⇒ no
  poisoning-guard / manifest-exclusion machinery: cubes load cleanly, §5.2.)
- **D6 — Eval gates.** **Two, mirroring views' phasing.** (a) *Track A authoring-accuracy eval*
  before declaring parity — cubes are harder to author than views (nested measures/dims + types),
  so feed docs naming grouped metrics and measure whether the Copilot emits cubes that pass the
  two-step dry-plan. (b) *Track B mandatory gate* — the aggregation-accuracy eval (cube path vs
  hand-written GROUP BY on the small-model tier) is the **whole motivation**; do not ship Track B
  blind. Mirrors views Step 6.5.
- **DP-C1 — Cube-backed golden query form.** **Defer to Track B; recommend an optional additive
  `cube_query` field** on the `queries.json` entry (queries spec §5) so recall can teach the agent
  to prefer the cube. Until Track B, a cube-backed answer promotes as ordinary model-name
  `semantic_sql`. This is the one cross-feature change the **queries spec** should record as
  pending (§6A).

---

## 12. Out of scope (explicit)
- Track B (cube consumption / query-time) — separate companion spec (§6).
- Authoring `hierarchies`, `refreshTime`, RLAC/CLAC on cubes (D3).
- Base-onboarding cube generation from schema alone (architecturally impossible — no
  aggregation intent in a bare catalog; §4).
- A bespoke cube-builder UI (rides existing review/diff; optional chip deferred).
- Golden queries / `queries.yml` (tracked separately in `golden_queries_and_shared_memory_spec.md`).
