<!--
Feature spec — Wren-level parity of VIEWS for the MDL Copilot.
Author intent: let the MDL Copilot author Wren views (top-level MDL `views[]`)
during doc-driven onboarding and enrichment, with validation + activation wired
correctly. Parity-first: reuse/adapt Wren's own skill content. Sequential
checklist usable by a future agent session.
-->

# Feature Spec — Wren-Parity Views for the MDL Copilot

Status: **proposed** · Created: 2026-06-30 · Owner: MDL Copilot
Related memories: [[multi-schema-mdl]], [[copilot-onboarding-spec]],
[[coverage-labels-and-progress]], [[mdl-copilot-patch-tools]]

---

## 0. One-paragraph intent

A Wren **view** is a named SQL statement that behaves like a stable virtual
table (`{name, statement, properties}` in the top-level MDL `views[]`). The MDL
Copilot today can *technically* hand-write one via `write_mdl_file` (the
validator accepts it), but it is **not skilled, prompted, structurally
contracted, or deep-validated** to do so — views were dropped as collateral when
cubes were deferred. This spec brings views to **Wren parity**: the Copilot
deliberately authors views where a BI document calls for one, at two points
(doc-driven onboarding self-review, and enrichment), and the validation +
activation path treats a view's SQL as a first-class, *engine-verified* artifact.
Cubes are explicitly **out of scope** (separate, spike-gated track).

---

## 1. What Wren provides (parity target)

Source-backed from the upstream skill copies in
[`wren_upstream_skills/`](wren_upstream_skills/) and the Wren docs.

- **Schema.** A view is `{name (req), statement (req — full SQL SELECT, may
  reference models/other views), properties (opt — incl. `description`)}`. Columns
  are inferred from the statement at query time. (Wren MDL reference.)
- **How it is queried — the parity-critical fact.** A view is queried as **plain
  SQL** (`SELECT … FROM <view>`); wren-core inlines it like a CTE/subquery. This
  is *exactly* the semantic-SQL path our engine already runs
  (`transform_sql`). Views need **no new query mechanism** — unlike cubes, which
  are queried through a structural API.
- **Dual role.** Wren docs: *"views with good descriptions become high-quality
  recall examples."* A well-described view is both a queryable object **and** a
  few-shot/recall artifact for text-to-SQL. The `properties.description` is
  therefore load-bearing, not cosmetic.
- **Where Wren authors views.** *Not* in base onboarding. Onboarding only
  scaffolds the empty `views/` dir
  ([onboarding.SKILL.md:118](wren_upstream_skills/onboarding.SKILL.md#L118)).
  Views are added during:
  - **generate-mdl Phase 7 "Iterate"** — *"Adding views for common query
    patterns"* ([generate-mdl.SKILL.md:261](wren_upstream_skills/generate-mdl.SKILL.md#L261)).
  - **enrich-context** — a view is one sink in the
    `cube_proposals` decision tree: *"Needs JOIN across multiple models, window
    function, or CTE → propose VIEW"*
    ([cube_proposals.md:23-24](wren_upstream_skills/enrich-context.references.cube_proposals.md#L23)).
    Upstream treats a **new view as high-blast-radius → escalate/grill before
    applying** ([enrich-context.SKILL.md:35,143,187](wren_upstream_skills/enrich-context.SKILL.md#L35)).
- **No standalone view skill exists upstream.** View guidance is *distributed*
  across generate-mdl + enrich-context + the cube_proposals reference. So the
  "download & adapt" requirement (§7) means **extracting the VIEW-specific
  upstream text and weaving it into our ported skills**, mirroring how
  onboarding/enrich were already ported — not copying one file wholesale.

---

## 2. Current state — what is already wired vs. missing

From end-to-end codebase traces. **The plumbing is ~80% there; the gaps are the
authoring contract, deep validation of the SQL, and skill/prompt guidance.**

### 2.1 Already wired ✓ (reuse as-is)

| Capability | Where | Note |
|---|---|---|
| MDL schema | [`MdlView`](semantic_layer/mdl_schema.py#L116); `views[]` [mdl_schema.py:166](semantic_layer/mdl_schema.py#L166) | `{name, statement, properties}` |
| Per-file structural validation | [`_validate_views`](semantic_layer/mdl_validator.py#L802) | requires `name` + `statement` |
| Project manifest validation | [validate_project_manifest](semantic_layer/mdl_validator.py#L475-L491) | views extracted + merged |
| Metric base-object resolution | [mdl_validator.py:426](semantic_layer/mdl_validator.py#L426) | a metric may target a view |
| Merge / patch overlay | `MERGE_SECTIONS` [mdl_merge.py:43](semantic_layer/mdl_merge.py#L43) | name-keyed additive |
| Targeted removal | `remove_mdl_entity` lists `views` [tools.py:256-292](semantic_layer/copilot/tools.py#L256); `remove_manifest_entities` [mdl_merge.py:271](semantic_layer/mdl_merge.py#L271) | verb `remove` |
| Compile → engine manifest | `_VIEW_KEYS` [mdl_compile.py:44](semantic_layer/mdl_compile.py#L44); `to_engine_manifest()` [mdl_compile.py:76](semantic_layer/mdl_compile.py#L76) | views emitted when present |
| Materialize active project | [wren_materializer.py:97](semantic_layer/wren_materializer.py#L97) | views deduped + written to `mdl.json` + `manifest.json` |
| Activation gate | bulk-status [app.py:1900-2046](app.py#L1900) | validates targets as one manifest; views ride along |
| File persistence (path-agnostic) | `MdlFileStore.create` [mdl_files.py:357](semantic_layer/mdl_files.py#L357) | `views/<name>.json` stored like any file |
| UI changeset review | [ChangesetReviewPanel.tsx:28-202](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/ChangesetReviewPanel.tsx#L28) | **file-level** diffs; op = create/update/delete; **no per-entity-type rendering** → views render with zero new UI |

### 2.2 Gaps ✗ (the actual work)

| # | Gap | Where | Severity |
|---|---|---|---|
| **G1** | **Authoring contract excludes views.** The structured generation/enrichment output is gated by [`AuthoredManifest`](semantic_layer/mdl_authoring.py#L117) = `models / relationships / metrics` only. The model *cannot emit a view* through `propose_mdl_from_document` / `generate_base_model`. | `mdl_authoring.py` | **High** — blocks the enrichment creation point |
| **G2** | **View SQL is never engine-validated.** Live deep validation calls `validate_with_wren_core(models, relationships)` — **views are dropped** ([mdl_validator.py:509](semantic_layer/mdl_validator.py#L509); [to_wren_core_manifest](semantic_layer/wren_core_validator.py#L145)). And even a full-manifest `SessionContext` *load* may not plan the view's SELECT. → a view referencing a non-existent column/model passes every gate and only breaks at query time. | `wren_core_validator.py`, `mdl_validator.py` | **High** — correctness |
| **G3** | **No authoring guidance.** No skill/prompt teaches *when/how* to author a view. `enrich-context.md` omits views as a sink; `generate-mdl.md` has only a one-liner; `wren_onboarding.md` never mentions them. | `skills/*`, `prompts/*` | **High** — without this the contract is unused |
| **G4** | **No view dry-plan validator.** There is no function that proves `SELECT … FROM <view>` plans. (Closes G2 definitively.) | new code in engine/validator | **Med** |
| **G5** | **No coverage signal for views.** `run_coverage` never flags "this documented query pattern has no view." | `copilot/coverage.py` | **Low** (nice-to-have) |
| **G6** | **Path routing for view-only files.** Structured proposals default to `models/<name>.json`; a view-only file should land at `views/<name>.json`. | `llm_client.py` | **Low** (convention, not hard constraint) |

---

## 3. Design principles

1. **Parity-first, reuse Wren.** Adapt upstream view text rather than invent
   prose (§7). Keep the `{name, statement, properties}` shape and the
   "high-blast-radius → review-gated" stance verbatim in spirit.
2. **Views are query-wired by construction.** No new query path. The only real
   engineering is *validating the SQL* (G2/G4), which reuses the existing
   `transform_sql` engine seam.
3. **Reuse the file-level changeset + review gate.** A view is JSON inside an MDL
   file; the human-accept diff UI already handles it. The Copilot **never
   auto-activates** — the accept step *is* the review gate (parity with Wren's
   "grill before applying new view").
4. **Degrade closed.** When wren-core is absent, view deep-validation degrades to
   structural-only with a warning (matches every existing engine seam).
5. **Don't author views from raw tables.** A view invented from schema alone with
   no document signal is a hallucination. Views require a **document-grounded**
   trigger (a described query pattern / reusable analysis). This is the rational
   refinement of "two creation points" — see §4.

---

## 4. The two creation points (dev intent ↔ actual architecture)

The request names two places. Mapped onto the real pipeline:

### Place 1 — "auto-onboard" = the **doc-driven self-review** sub-phase
- The base structure-seeding call
  [`generate_base_model`](integrations/wren/llm_client.py#L421) turns *datasets →
  base models*. It sees **tables, not documents** → **it must NOT emit views**
  (parity with Wren onboarding; avoids hallucination). **Decision D1: keep base
  onboarding view-free.**
- When onboarding was triggered **from a BI document**, the Copilot's
  **self-review step** ([skills/onboarding.md:163](skills/onboarding.md#L163),
  `run_coverage` → `write_mdl_file`) is where a view legitimately appears: the
  agent sees a documented reusable query pattern and writes a `views/<n>.json`.
  This is the agent-loop path and needs only **skill guidance (G3)** + **validation
  (G2/G4)** — `write_mdl_file` already accepts views.

### Place 2 — **enrichment** (later doc uploads)
- [`_enrichment_proposal`](app.py#L4648) → `propose_mdl_from_document` produces a
  structured proposal. To emit a view here the **`AuthoredManifest` must carry
  views (G1)** *and* the enrich skill must route to a view sink **(G3)**.

Both points converge on the **same machinery**: contract (G1) + validation
(G2/G4) + skills (G3). Place 1 exercises the agent-loop tools; Place 2 exercises
the structured proposal. One implementation serves both.

---

## 5. Feature spec — component by component

### 5.1 Authoring contract — `AuthoredView` (closes G1)
- Add to [`mdl_authoring.py`](semantic_layer/mdl_authoring.py#L117), mirroring
  `MdlView`:
  ```python
  class AuthoredView(BaseModel):
      model_config = _AUTHORING_CONFIG
      name: str
      statement: str
      dialect: str | None = None  # None ⇒ semantic (WrenSQL over models);
                                   # set ⇒ native SQL in that dialect (§5.7, D6)
      properties: dict[str, Any] = Field(default_factory=dict)
  ```
  (Mirror the same optional `dialect` field on `MdlView` in `mdl_schema.py`.)
- Add `views: list[AuthoredView] = Field(default_factory=list)` to
  `AuthoredManifest`.
- `proposal_response_schema()` ([mdl_authoring.py:146](semantic_layer/mdl_authoring.py#L146))
  auto-includes it (derived from the pydantic model) — **no manual schema edit**.
- **Input the model needs:** a view's `statement` references **model column
  space** (logical model/column names), so the enrichment context must surface
  **existing model names + columns** alongside physical schema + document
  passages. The enrichment path already passes schema/instructions; confirm
  existing-models context is included (same need metrics have). Touchpoint:
  `propose_mdl_from_document` payload in
  [llm_client.py](integrations/wren/llm_client.py).

### 5.2 Deep validation of the view SQL (closes G2; G4 is the definitive form)

Two layers, do both:

- **Layer A — stop dropping views (cheap, do first).** Extend
  [`to_wren_core_manifest`](semantic_layer/wren_core_validator.py#L145) and
  [`validate_with_wren_core`](semantic_layer/wren_core_validator.py#L62) to accept
  `views` (and pass the models a view depends on), and update the call site
  [mdl_validator.py:509](semantic_layer/mdl_validator.py#L509) to forward
  `merged_views`. This makes a *structurally* bad view fail at activation, not
  query time.
- **Layer B — view dry-plan (G4, the real guarantee).** Manifest *load*
  (`SessionContext`) may not resolve a view's SELECT columns. Add a validator that
  **plans the statement**: for each view, call `transform_sql("SELECT * FROM
  <view>")` (or the view's own statement) through the existing
  [`WrenCoreEngine`](semantic_layer/engine/wren_core_engine.py#L80) and surface
  failures via the existing
  [`_friendly_engine_error`](semantic_layer/wren_core_validator.py#L109) path.
  **Decision D2 — RESOLVED by spike (2026-06-30, wren-core 0.7.x): Layer B NOT
  needed.** `SessionContext` load is eager — it rejects an unknown column
  (`No field named …`) *and* a missing model (`table … not found`) at load, while
  valid CTE/WINDOW/multi-model-join views load and plan. So **Layer A alone fully
  validates semantic views at the activation gate**; the separate dry-plan is
  redundant and dropped.
- Degrade closed when wren-core absent (info message, `valid=True`) — reuse the
  existing guard.

**Layers A/B apply to SEMANTIC views only.** A **native view** (`dialect` set,
§5.7) cannot be model-resolved; validate it via `validate_read_only_sql` + an
optional source dry-run (`LIMIT 0`/`EXPLAIN`). The validator must branch on
`dialect`: route native views away from the wren-core model path (and, per the
Step 0.5 spike, likely away from the engine manifest entirely) so they never
poison the manifest load for models.

### 5.3 Skills & prompts (closes G3) — see §7 for exact upstream adaptation.

### 5.4 Coverage signal (G5, optional)
- Extend [`copilot/coverage.py`](semantic_layer/copilot/coverage.py) so a
  documented, multi-model/windowed query pattern with no covering view surfaces as
  a `partial`/`missing` claim — feeding the same self-review loop. Defer if it
  expands scope; record as follow-up.

### 5.5 Path routing (G6, optional)
- In [llm_client.py](integrations/wren/llm_client.py#L346) draft path logic: when an
  overlay contains `views` and no `models`, default the path to
  `views/<safe_name>.json`. Non-blocking — the store is path-agnostic; this is a
  tidiness convention matching Wren's layout.

### 5.6 Cross-schema views (VERIFIED — handled by the model layer)

**Concern:** a view may join real SQL tables that live in *different physical
schemas*. **Verified conclusion: this works automatically, provided the view
statement references model names (semantic SQL), because cross-schema resolution
is inherited from the model layer — no view-specific engine work is needed.**

Mechanism (source-verified):
1. **Manifest is project-scoped, not schema-scoped.** `materialize_wren_project`
   selects all `status=="active"` files with no schema filter
   ([wren_materializer.py:46-52](semantic_layer/wren_materializer.py#L46)). At
   query-time, [wren_runtime.py:117](semantic_layer/wren_runtime.py#L117) uses the
   resolved `schema_name` only to **locate** the project in a schema-filtered list;
   the materialized manifest still contains the project's models across **all** its
   schemas. → a view's join partners in other schemas are present in the loaded
   manifest.
2. **Per-model `tableReference.schema` survives compile.** `compile_manifest`
   passes model bodies through unchanged ([mdl_compile.py:110-127](semantic_layer/mdl_compile.py#L110));
   the top-level `schema` is only the default namespace, not a filter.
   `to_engine_manifest` emits each model with its full `tableReference`.
3. **`transform_sql` expands** view → models → each model's physical schema (the
   shipped wren-core multi-schema rewrite, [[multi-schema-mdl]]).
4. **G2 deep-validation rides this for free:** the
   [mdl_validator.py:509](semantic_layer/mdl_validator.py#L509) call passes
   `merged_models` (built from all files = all schemas) + `merged_views`, so a
   cross-schema view validates against the full model set.

**The condition that must hold — prompt, not engine.** A view statement is
**semantic SQL over MODEL names**, never raw physical tables. If the LLM writes
`SELECT … FROM analytics.orders` (a physical `schema.table`) it bypasses the model
layer and cross-schema correctness becomes a fragile LLM burden. So:
- **Prompt (§7) MUST pin:** "Write the view `statement` against MDL **model
  names**; do not hand-qualify physical schemas — the engine derives each model's
  physical schema from its `tableReference`." This is the single load-bearing
  guidance for cross-schema views.
- **Dry-plan (G4) is the backstop:** a statement referencing a non-model or an
  unresolvable cross-schema table fails the plan and is surfaced before activation.

**Minor validation-fidelity caveat (non-blocking):** `to_wren_core_manifest`
hardcodes top-level `schema="public"`
([wren_core_validator.py:155](semantic_layer/wren_core_validator.py#L155)). Models
that carry an explicit `tableReference.schema` (all multi-schema models do) resolve
regardless; only a model *relying on the default top-level schema* could be
mis-validated. Out of scope for views, but worth a follow-up to thread the
project's real schema into the validation envelope.

### 5.7 Native SQL views vs semantic views — the authoring-accuracy hedge

**Motivating finding (empirical).** When an LLM's context is "poisoned" by a raw
BI/SQL context dump and it is then *forced* to emit **semantic** SQL (over model
names) through Wren, accuracy drops significantly — it must reverse-engineer raw
tables back into model names while its working context is in raw-SQL space. The
MDL Copilot is exactly this case: by the time it authors a view it has already
ingested the BI document (often containing raw SQL). Forcing a semantic-only view
statement inherits the degradation.

**Decision D6: support BOTH semantic and native view statements; let the model
choose.** A view's `statement` may be either:
- **Semantic** (default, parity) — WrenSQL over **model names**; the engine expands
  it (cross-schema-correct via models, §5.6). Governed, reusable, schema-change
  resilient.
- **Native** — dialect SQL over **physical tables** (the form the Copilot already
  has in context). Captured verbatim, no reverse-engineering.

**Why this is elegant, not just a hedge.** A native view encapsulates raw
complexity *behind a semantic name*. Authoring stays in the model's
high-confidence representation (native SQL it already holds) — **and** query-time
stays clean, because the AI SQL agent references the view **by name**
(`SELECT … FROM trusted_view`) and never has to reverse-engineer the internals.
Both ends avoid the poisoned-translation step. For a complex CTE/window pattern
lifted from a BI doc, a native view is *more* accurate than a forced semantic one
at both authoring and query time. (This also makes a native view ≈ a queryable,
project-scoped **golden query** — align with [[golden-queries-shared-memory]] so
we don't build two trusted-SQL stores.)

**Parity note.** Native views are not a clean break from Wren: Wren's MDL view
carries an optional **`dialect`** field (gated behind `schema_version: 3`) for
exactly dialect-specific statements. We adopt the same marker (see schema below)
rather than invent one.

**Schema marker.** Extend `MdlView` / `AuthoredView` with an optional
`dialect: str | None`. Absent ⇒ semantic (status quo, zero behavior change).
Present ⇒ native, written in that dialect.

**⚠️ Execution feasibility — RESOLVED by spike (2026-06-30, wren-core 0.7.x).**
wren-core resolves view statements **only against MODEL names**
(`catalog.schema.<model_name>`). A view referencing a **physical table that is not a
model fails LOAD** (`table 'wren.public.raw_orders_v2' not found`, proven with model
name ≠ table name). The `dialect` field is **accepted by the view schema but inert**
— it does not enable native/physical passthrough, and `schemaVersion: 3` doesn't
change this. **Therefore native (physical-table) views MUST bypass the engine
manifest** (exclude from `to_engine_manifest`/`to_wren_core_manifest`, else they
poison the whole project's load), validate via `validate_read_only_sql` + a source
dry-run, and execute via the `PassthroughEngine` inline path or golden-query
surfacing. Path "engine-native" is unavailable in 0.7.x.

**Value re-scoped by the same spike.** Semantic views already handle complex
CTE/WINDOW/multi-model SQL; the LLM's *only* extra burden vs. a raw-SQL dump is
**substituting physical table names with model names** (a name mapping, not query
synthesis). So before building the native bypass, **an eval gate** measures whether a
"physical→model name-substitution" prompt yields acceptable semantic-view accuracy.
If yes, native views shrink to a narrow feature for *unmodeled/external* tables and
the bypass defers; if no, build it. (Spec §9 Step 6.5.)

**Dual validation (the chokepoint splits).**
- Semantic view → wren-core dry-plan over models (G2/G4, §5.2).
- Native view → **`validate_read_only_sql` + a source dry-run** (`LIMIT 0` /
  `EXPLAIN` against the real DB through the Superset executor) — model resolution
  does not apply. Reuse the existing read-only SQL guard; add the dry-run probe.

**Governance guardrail.** Native views bypass the semantic layer (no
relationships/calculated columns, brittle to physical-schema change, cross-schema
only via explicit `schema.table` qualification). To stop the model defaulting to
native out of laziness (which would erode the semantic layer's value), the prompt
(§7) biases toward **semantic when the pattern maps cleanly to models**, reserving
native for **complex/raw/uncertain** patterns; and the human review gate plus an
optional coverage hint ("native view — consider promoting to semantic") catch
overuse.

---

## 6. Validation & activation flow (target end-state)

```
BI doc ─► (onboard) generate_base_model ─► models only (NO views)  [D1]
          │
          └─(doc-grounded)─► Copilot self-review / enrichment
                 │  agent decides a view helps a documented query pattern
                 ▼
        write_mdl_file / patch_mdl_file  OR  propose_mdl_from_document
                 │   (AuthoredManifest now carries views — G1)
                 ▼
        validate_mdl  ──► _validate_views (name/statement)            [exists]
                 │        + deep: views passed to wren-core (G2 Layer A)
                 │        + view dry-plan (G4 Layer B, if D2 says needed)
                 ▼
        Changeset (file-level)  ──► UI diff + human Accept            [exists]
                 ▼
        bulk-status activate ──► validate as one manifest            [exists]
                 ▼
        materialize ──► mdl.json + manifest.json (views included)    [exists]
                 ▼
        query time: transform_sql expands `SELECT … FROM <view>`     [exists]
```

Only the **bold-new** boxes (G1, G2, G4) are engineering; the rest is reuse.

---

## 7. Skill/prompt adaptation (the "download & adapt" non-negotiable)

There is **no standalone upstream view skill** to copy. Adaptation = extract the
VIEW-specific upstream text and weave it into our ports, exactly as
onboarding/enrich were ported. Concrete actions:

1. **New reference, adapted from upstream cube_proposals (VIEW branch only).**
   Create `skills/references/view_proposals.md` (or inline section) from
   [cube_proposals.md:15-27,90-91](wren_upstream_skills/enrich-context.references.cube_proposals.md#L15),
   keeping the decision rule verbatim in spirit:
   > *Needs JOIN across models / window function / CTE → propose a VIEW.*
   Add our-context tweaks: write to `views/<name>.json`; **statement is semantic
   SQL over MODEL names, never raw physical `schema.table`** — the engine derives
   each model's physical schema from its `tableReference`, so a model-referencing
   view is cross-schema-correct by construction (§5.6), while a physical-table view
   silently breaks multi-schema projects; include a `properties.description` (it
   doubles as a recall example); a view is **high-blast-radius → review-gated** (our
   human-accept step satisfies Wren's "grill before applying").
2. **`skills/enrich-context.md`** — add views as a Step 5 gap-catalog category and a
   Step 7 routing row (sink = `write_mdl_file`, path `views/`), mirroring upstream
   [enrich-context.SKILL.md:143,149](wren_upstream_skills/enrich-context.SKILL.md#L143).
   Update the Parity-notes block ([enrich-context.md:311](skills/enrich-context.md#L311))
   to state views ARE now an authoring sink (removing the implicit exclusion).
3. **`skills/generate-mdl.md`** — promote the L273 one-liner into a short "When to
   author a view" subsection adapted from generate-mdl Phase 7
   ([generate-mdl.SKILL.md:261](wren_upstream_skills/generate-mdl.SKILL.md#L261)) +
   the decision rule; document `views/<name>.json` and the description-as-recall
   point. **Add the semantic-vs-native choice (§5.7, D6):** *"Prefer a **semantic**
   statement (over model names) when the pattern maps cleanly to existing models —
   it's governed and cross-schema-correct. Use a **native** statement (set
   `dialect`, write dialect SQL over physical tables) when the pattern is complex or
   lifted directly from a source document and semantic translation is uncertain —
   don't reverse-engineer a fragile semantic query when you already hold a correct
   native one."*
4. **`skills/onboarding.md`** — update the layout note
   ([onboarding.md:181](skills/onboarding.md#L181)) and extend Step 5 self-review
   to add a missing view when a document describes a reusable query pattern.
5. **`prompts/wren_onboarding.md`** — keep base onboarding view-free (D1) but add
   one line clarifying the agent may *propose* a view during doc-grounded review
   (not from raw structure).
6. **`prompts/mdl_copilot.md`** — already references `views/`; add the
   `{name, statement}` shape + "semantic SQL over model names" + description note.

Each adapted file keeps a provenance comment citing the upstream path + fetch
date, matching the existing port convention.

---

## 8. User intent / flow ↔ actual UI

| User expectation | Actual UI today | Gap / action |
|---|---|---|
| "Copilot suggests a view from my BI doc" | Copilot returns a Changeset; view appears as a **create** item on a `views/<n>.json` file with a JSON diff | **None** — file-level review already renders it ([ChangesetReviewPanel.tsx](../superset-frontend/src/SqlLab/components/AiAgentPanel/SemanticLayerEditor/ChangesetReviewPanel.tsx)) |
| "I can see it's a *view*, not a model" | Op tag shows Create/Update/Delete + the **path** `views/…json`; no entity-type chip | **Minor UX gap.** Path conveys type. *Optional:* add a "view" type chip from the file path. Recommend defer. |
| "I can reject the view but keep the rest" | Per-item Accept/Reject toggles exist | **None** |
| "Validation errors are visible before I accept" | `ChangesetItem.validation` + `manifest_validation` render in the panel | **None** — but G2/G4 must populate *meaningful* view errors (else the panel shows a false green) |
| "Activating the view doesn't break my project" | bulk-status validates targets as one manifest | **None**, once G2 included at activation |

**Net UI verdict:** zero new components required; the file-level changeset is the
right grain. The only *honesty* risk is G2 — without engine validation the panel
would show a green check on a view that cannot run. **Fixing G2 is what makes the
existing UI truthful.**

---

## 9. Implementation checklist (sequential; blockers noted)

> Ordered so each step is shippable and the risky-but-cheap spike comes first.

- [ ] **Step 0 — Spike D2 (blocker for G4 scope).** Take `_CUBE`-style view fixture
      ([test_native_manifest_contract.py](../tests/unit_tests/superset_ai_agent/test_native_manifest_contract.py)):
      build a manifest with a model + a view whose statement references a
      **non-existent column**; load via `SessionContext`; record whether load
      raises. Then try `transform_sql("SELECT * FROM <view>")`. **Outcome decides
      whether G4 Layer B is required or optional.** ~15 lines, no production change.
- [ ] **Step 0.5 — Spike D6 (blocker for native views, §5.7).** Build a manifest
      with a model + a **native** view (`dialect` set, statement = raw `SELECT … FROM
      <physical schema.table>` referencing a real table that is *not* a model). (a)
      Does `SessionContext` **load** it without raising? (b) Does
      `transform_sql("SELECT * FROM <native_view>")` return runnable native SQL?
      **Outcome decides the native-view execution path:** load+plan OK → use the
      engine (full parity); rejects → native views bypass wren-core (separate
      handling + dry-run validation). Settle BEFORE building the native path. ~20
      lines, no production change.
- [ ] **Step 1 — G2 Layer A (validation, no behavior risk).** Extend
      `to_wren_core_manifest` + `validate_with_wren_core` to accept `views`; forward
      `merged_views` at [mdl_validator.py:509](semantic_layer/mdl_validator.py#L509).
      Tests: a view with a bad/empty statement fails deep validation; a clean view
      passes; wren-core-absent degrades closed; **a cross-schema view (statement
      joins two models whose `tableReference.schema` differ) validates clean — the
      multi-schema regression guard from §5.6**. *Depends on: none.*
- [ ] **Step 2 — G4 Layer B (only if Step 0 says needed).** Add per-view dry-plan
      in `WrenCoreEngine`; surface via `_friendly_engine_error`. Tests: view with
      unknown column → readable error; valid cross-model view → clean. *Depends on:
      Step 0.*
- [ ] **Step 3 — G1 contract.** Add `AuthoredView` (incl. optional `dialect`) +
      `AuthoredManifest.views`; mirror `dialect` on `MdlView`. Assert
      `proposal_response_schema()` now contains `views`. Confirm enrichment payload
      surfaces existing-model context. Tests: a proposal containing a view
      round-trips through `MdlProposalResponse`; serialized file validates. *Depends
      on: Step 1 (so emitted views are validated).*
- [ ] **Step 3.5 — D6 native-view path (only after Step 0.5).** Branch validation on
      `dialect`: semantic → wren-core (Steps 1-2); native → `validate_read_only_sql`
      + source dry-run, and (per Step 0.5) route native views around the engine
      manifest so they never break model load. Tests: a native view with valid raw
      SQL validates; one with a write/DDL statement is rejected by the read-only
      guard; a semantic view still takes the wren-core path. *Depends on: Step 0.5,
      Step 1.*
- [ ] **Step 4 — G6 path routing.** View-only overlay → `views/<name>.json`. Test
      path derivation. *Depends on: Step 3.*
- [ ] **Step 5 — G3 skills/prompts (§7).** Adapt upstream view text into the 6
      files, **including the semantic-vs-native choice (§5.7, D6)**. No tests (prose)
      but run an **eval pass**: feed a doc describing a multi-model query pattern →
      confirm the Copilot proposes a valid view; feed a doc with a complex raw SQL
      block → confirm it captures a valid native view rather than a degraded semantic
      one. *Depends on: Steps 1-3.5.*
- [ ] **Step 6 — G5 coverage (optional).** View-gap signal in `coverage.py`.
      Defer if scope grows. *Depends on: Step 5.*
- [ ] **Step 7 — UI chip (optional).** Entity-type chip from path. Defer unless UX
      asks. *Depends on: none.*
- [ ] **Step 8 — Full suite + `pre-commit run --all-files` + eval.** Record
      residual risks + UI-gap notes.

---

## 10. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| **R1 — False-green view (passes validation, fails at query).** | High without G2 | G2 Layer A always; G4 Layer B if D2 says so. This is the single most important fix. |
| **R2 — LLM authors a view over physical tables, not model names.** | Med | For a *semantic* view this is an error: skill text (§7) pins model names; dry-plan (G4) catches non-model refs. But this is now also the **legitimate native-view path (§5.7)** — if the model is more accurate in raw SQL, it sets `dialect` and the native validation path applies. The risk narrows to "physical-table view *without* `dialect`" (caught) vs "intentional native view" (allowed). |
| **R3 — Hallucinated view from raw schema (no doc).** | Med | D1: base onboarding stays view-free; views require a doc-grounded trigger. |
| **R4 — Cross-schema view breaks in multi-schema projects.** | Low (verified §5.6) | Engine handles it: manifest is project-scoped (all schemas' models present), per-model `tableReference.schema` survives compile, `transform_sql` rewrites per-model. **Residual risk is solely the LLM writing raw physical tables instead of model names** → mitigated by the §7 prompt pin + the G4 dry-plan backstop. **Required: a cross-schema-view validation test** (Steps 1–2). |
| **R5 — Engine absent in deployment → no deep validation.** | Low | Degrade closed (structural only) + warning, as every seam does. Note in UI that deep checks were skipped. |
| **R6 — Description omitted → view loses recall value.** | Med | Skill requires `properties.description`; coverage (G5) can flag missing descriptions. |
| **R7 — Activation order (view depends on a not-yet-active model).** | Low | bulk-status already validates the *projected* manifest as one unit ([[mdl-bulk-activate]]); views ride that guarantee. |
| **R8 — Native views erode the semantic layer if overused** (model defaults to native to skip translation effort → ungoverned, schema-brittle sprawl). | Med | §7 prompt biases to semantic when the pattern maps to models; human review gate; optional coverage hint "native view — consider promoting to semantic"; native reserved for complex/raw/uncertain patterns. |
| **R9 — Native-view execution unsupported by wren-core 0.7.1** → native statement poisons manifest load. | Med (unknown until Step 0.5) | Step 0.5 spike settles it; if unsupported, native views bypass the engine manifest entirely (separate handling), never reaching `SessionContext`. |

---

## 11. Decision points (recommendations)

- **D1 — Should base auto-onboard emit views?** → **No (recommended).** Parity
  with Wren + avoids hallucination; views enter via doc-grounded review/enrichment.
- **D2 — Is a separate view dry-plan (G4) required, or does manifest-load suffice?**
  → **Resolve empirically in Step 0.** Recommend building Layer A unconditionally
  and Layer B if the spike shows load doesn't resolve view columns.
- **D3 — New `view_proposals.md` reference file vs inline skill sections?** →
  **Inline into `enrich-context.md` + `generate-mdl.md` (recommended)** for a
  smaller surface, unless the team prefers the upstream's separate-reference
  pattern. Low stakes.
- **D4 — Entity-type chip in the changeset UI?** → **Defer.** Path already conveys
  type; revisit if UX feedback asks.
- **D5 — Coverage signal for views (G5)?** → **Defer to a follow-up.** Not required
  for parity; valuable for "did we miss a view" completeness.
- **D6 — Support native SQL views alongside semantic (the accuracy hedge, §5.7)?**
  → **RESOLVED: DEFER native views (Eval v3, 2026-06-30).** Physical→model
  name-substitution from correct SQL produced 3/3 trials of valid, described semantic
  views with zero physical-schema leak — the translation burden native views were
  meant to remove is small and handled well. Native views reserved for genuinely
  *unmodeled/external* tables only, built on concrete need. The dormant `dialect`
  plumbing + symmetric engine-manifest exclusion (Phase 1) stay. The real query-time
  gap is **view surfacing** (Eval R2), not native execution — see
  `plan_views_surfacing_impl.md`. Original gating analysis below kept for history:
  → **Yes (recommended), gated on the Step 0.5 execution spike.** Addresses the
  context-poisoning finding and is *better* than semantic-only for complex/raw
  patterns (encapsulates native complexity behind a queryable semantic name).
  Carries governance cost (R8) and an execution unknown (R9) — both bounded by the
  spike + prompt bias + review gate. **Phasing:** ship the **semantic** path first
  (Steps 1-5, full parity, zero new execution surface); add the **native** path as a
  fast-follow once Step 0.5 resolves. Do not block semantic views on the native
  spike.

---

## 12. Out of scope (explicit)

- **Cubes.** Separate track, gated on the `transform_sql`-cube-expansion spike.
  Aggregation intent continues to route to metrics / calculated columns.
- **Golden queries / `queries.yml`.** Re-homed to the AI SQL agent's `NlSqlPair`
  runtime store; not part of MDL authoring. **Note:** a *native* view (§5.7, D6) is
  effectively a queryable project-scoped golden query — align with
  [[golden-queries-shared-memory]] so trusted-SQL isn't stored twice.
- **RLAC/CLAC, cubes-on-views.** Future.
- **In scope now (was future):** view **`dialect` / native SQL** — pulled in by D6
  as the accuracy hedge.
