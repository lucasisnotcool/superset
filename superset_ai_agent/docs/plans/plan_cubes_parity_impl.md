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

# Implementation Plan — Wren-Parity Cubes (MDL Copilot)

> Spec: `plan_cubes_parity_spec.md`. Sibling: `plan_views_parity_impl.md` (mirror its patterns).
> **Phases are strictly ordered.** Track A (authoring+validation) ships independently of
> Track B (consumption). Do **not** block A on B (spec §6, §11-D1).

## How to use this checklist
- Each step lists `Files`, `Depends on`, and `Test`. Run `pre-commit run --all-files` before
  any push (CLAUDE.md, non-negotiable). After each step: add tests, run them, note residual risk.
- Reuse the verified plumbing (spec §2.1) — do **not** rebuild merge/compile/materialize.

## Guiding patterns to reuse (in-repo)
- `AuthoredView` (`mdl_authoring.py:117-133`) is the structural template for `AuthoredCube`.
- `_validate_cubes` (`mdl_validator.py:942`) already does structural validation — leave it.
- `merge_cube_preserving_structure` (`mdl_merge.py:118`) already does cube patch merges.
- `_friendly_engine_error` (`wren_core_validator.py:109`) translates engine errors — reuse for dry-plan.
- Skill download-and-adapt method: provenance header + `wren_upstream_skills/` → tweaked `skills/`.

---

## PHASE 0 — Spike (DONE; no production change)
### [x] Step 0 — Is a cube query-wired, and how? → **RESOLVED** (memory `cube-query-wiring`)
- **Outcome:** cube **loads** into `SessionContext`; **not** queryable via `transform_sql`
  (`table not found`); **is** queryable via `wren_core.cube_query_to_sql(cube_query_json,
  manifest_json)` (manifest = **raw JSON, not base64**). CubeQuery = `{cube, measures:[str],
  dimensions:[str], timeDimensions:[{dimension,granularity}], filters:[{dimension,operator,value}],
  limit}`. Unknown cube/measure/dimension → `ValueError` (the dry-plan primitive).
- **Consequence:** authoring (Track A) is decoupled from consumption (Track B). Proceed with A.

---

## PHASE 1 — Track A: cube authoring + validation + activation (full *authoring* parity)

### [ ] Step 1 — G2 Layer A: stop dropping cubes from deep validation
- **Files:** `wren_core_validator.py` (`validate_with_wren_core` `:62`, `to_wren_core_manifest` `:145`),
  `mdl_validator.py:509` (the call site).
- **Do:** add `cubes` (and `views`, currently also dropped) params; include them in the engine
  manifest envelope. Degrade closed when wren-core absent (existing guard).
- **Test:** a malformed cube (e.g. bad measure type) that passes structural check is now rejected
  at deep validation; a valid cube passes. Depends on: none.

### [ ] Step 2 — G4 Layer B: per-cube **two-step** dry-plan (REQUIRED — do NOT drop like views D2)
- **Files:** new helper in `wren_core_validator.py` (e.g. `dry_plan_cube(cube, manifest_json,
  manifest_b64)`); wire into the activation validation path.
- **Why two steps (verified spike):** manifest *load* does NOT validate a cube's measure
  expressions, and `cube_query_to_sql` only checks measure/dimension **names** —
  `sum(nonexistent_col)` passes both. The column check needs `transform_sql`.
- **Do, per cube:** (1) build a CubeQuery with **all** measure + dimension names → `cube_query_to_sql`
  (catches bad names / cyclic measures); (2) feed its model-name SQL output through `transform_sql`
  (catches `No field named …` bad columns). Map `ValueError`/`Exception` → `MdlValidationMessage`
  via `_friendly_engine_error`. Skip both when wren-core absent (degrade closed).
- **Test:** (a) cube with bad measure-name → step 1 fails; (b) cube with `sum(nonexistent_col)` →
  step 1 PASSES, step 2 fails (`No field named`); (c) clean cube → both pass; (d) wren-core absent →
  info, valid=True. Depends on: Step 1.

### [ ] Step 3 — G1: authoring contract (`AuthoredCube`)
- **Files:** `mdl_authoring.py` (add `AuthoredCubeField`, `AuthoredCube`; add `cubes` to
  `AuthoredManifest:144`).
- **Do:** typed `{name,type,expression}` fields (not `dict`) so `proposal_response_schema()`
  forces the shape. `baseObject` required. Omit `hierarchies` (D3). Verify camelCase aliases
  (`baseObject`, `timeDimensions`) via `serialize_manifest`.
- **Test:** mirror `test_mdl_authoring_views.py` — round-trip an authored cube to canonical JSON;
  assert it validates structurally and passes Steps 1–2. Depends on: Steps 1,2.

### [ ] Step 4 — G5: `remove_mdl_entity` advertises cubes
- **Files:** `copilot/tools.py:257-292` (spec enum + description).
- **Do:** add `cubes` to the section list + description. Functional already (∈ `MERGE_SECTIONS`).
- **Test:** remove a cube by name; assert gone + manifest still valid. Also add a `patch_mdl_file`
  test for measure-level cube edit (confirms `merge_cube_preserving_structure`). Depends on: Step 3.

### [ ] Step 4.5 — path routing + activation ordering (parity with views G6 + R10)
- **Files:** draft-path logic in `integrations/wren/llm_client.py` (~346, where views route to
  `views/<name>.json`); the changeset assembly.
- **Do:** a cube-only overlay routes to `cubes/<_safe_name>.json` (convention; store is
  path-agnostic). Ensure a newly-proposed cube and a newly-proposed baseObject model/view land in
  the **same changeset** so atomic bulk-status activates them together (§5.8).
- **Test:** cube + its new baseObject view activate atomically; activating the cube alone (base not
  active) fails validation with a resolvable message. Depends on: Step 3.

### [ ] Step 5 — G3 + G6: skills & prompts (download & adapt — spec §8, six touchpoints)
- **Files:** `wren_upstream_skills/` already has `enrich-context.references.cube_proposals.md`;
  port a tweaked copy to `skills/enrich-context.references.cube_proposals.md` + routing block in
  `skills/enrich-context.md`; flip `skills/generate-mdl.md:265-274`; note in `prompts/mdl_copilot.md`.
- **Do:** keep the decision tree, duplication guard, naming policy, escalation rule; replace
  `wren` CLI / YAML sink / `queries.yml` with our JSON-`cubes[]` + `write_mdl_file`/`patch_mdl_file`
  + `validate_project` + dry-plan. Adopt Wren's metric↔cube routing (D2).
- **Test:** none (prose); review against upstream for parity. Depends on: Steps 3,4.

### [x] Step 6a — cube-over-view spike → **DONE/VERIFIED** (memory `cube-query-wiring`)
- Cube with `baseObject` = a view loads and queries (`FROM <view>`). Confirms the upstream
  "pre-join via VIEW, then cube on the view" path for cross-table aggregation. ⇒ **cross-table
  cubes depend on views** (ordering: views parity lands first; single-model cubes are independent).
### [ ] Step 6b — multi-schema cube spike (remaining; verify §5.6)
- **Do:** cube over a model in schema A with a measure reaching schema B via a relationship;
  confirm Layer-B step 2 (`transform_sql`) rewrites the multi-schema `tableReference`. If it fails,
  the skill must require a pre-joining view as `baseObject`. Depends on: Step 3.

### [ ] Step 7 — Phase 1 gate: suite + lint + **authoring-accuracy eval** + parity sign-off
- **Do:** full unit suite, `pre-commit run --all-files`, confirm cubes author→two-step validate→
  review→activate→materialize end-to-end. **Authoring eval (D6a):** feed docs naming grouped
  metrics; measure whether the Copilot emits cubes that pass the two-step dry-plan (cubes are
  harder to author than views). Record residual risks (esp. R1 inert, R8 bad-column, R11 golden).
- **DoD:** a BI doc naming a grouped metric yields a validated, activatable cube in the changeset.

---

## PHASE 2 — Track B: cube consumption (separate companion spec; do not block Phase 1)

### [ ] Step 8 — engine `cube_query` binding
- **Files:** `semantic_layer/engine/wren_core_engine.py` (+ `passthrough.py` degrade), `base.py`.
- **Do:** `cube_query(cube_query_json, manifest) → PlannedSql` wrapping `cube_query_to_sql`
  (raw-JSON manifest); reuse `extract_referenced_tables`; degrade closed. Output → existing
  `validate_read_only_sql` + executor (no new exec path).
- **Test:** engine contract test (mirror `test_native_cube_loads_into_engine`) pinning the
  CubeQuery→SQL shape against the installed wheel.

### [ ] Step 9 — AI SQL agent cube branch
- **Files:** the LangGraph pipeline (`semantic_layer/pipeline.py` / graph), `prompts/text_to_sql.md`.
- **Do:** on an aggregation question a cube covers, LLM emits a **CubeQuery JSON** (schema-forced)
  → `cube_query` → execute. Adapt upstream `usage.SKILL.md:316-403` aggregation decision tree.
- **Test:** eval on aggregation questions — cube path vs raw-SQL path accuracy.

### [ ] Step 9.5 — DP-C1: cube-backed golden query form (cross-feature, additive)
- **Do:** decide + implement the optional `cube_query` field on the `queries.json` golden entry
  (queries spec §5) so recall can teach the agent to prefer the cube. Runtime memory stays physical
  `native_sql` (RBAC-safe, DB-scoped). Coordinate with the golden-queries spec (§6A flags this).
- **Depends on:** Steps 8,9 + golden-queries feature.

### [ ] Step 10 — Phase 2 **mandatory eval gate** (D6b — do not ship blind): aggregation accuracy
- **Do:** suite + lint + the aggregation eval — confirm cube-backed answers beat hand-written
  GROUP BY / DATE_TRUNC on the **small-model tier** (the stated motivation). If cubes don't help,
  reconsider Track B scope. Note golden-query interaction (Step 9.5). Mirrors views Step 6.5.

---

## Dependency graph (quick reference)
```
Step 0, 6a (DONE)
  └─ Step 1 ─ Step 2(two-step) ─ Step 3 ─ Step 4 ─ Step 4.5 ─ Step 5 ─ Step 7 (Phase 1 ships)
                                    ├─ Step 6b ─────────────────────────┘
                                    └─ (cross-table cubes also need: VIEWS parity shipped)
Phase 1 ─ Step 8 ─ Step 9 ─ Step 9.5 ─ Step 10 (Phase 2 ships)
```
> Cross-feature ordering: **views parity should land before cross-table cubes** (cube-on-view is
> the pre-join path, §6A). Single-model cubes are independent of views.

## Definition of done
- **Phase 1 (Track A):** cubes author→structural+deep+dry-plan validate→review→activate→
  materialize, at both creation points (doc-driven onboarding self-review + enrichment). Skills
  adapted from upstream. **This is full *authoring* parity.**
- **Phase 2 (Track B):** AI SQL agent emits CubeQuery → `cube_query` → executes; eval shows the
  aggregation-accuracy win. **This delivers the stated motivation.**
